from contextvars import ContextVar
from functools import wraps

from django.conf import settings
from django.core.exceptions import ImproperlyConfigured
from django.db import connection
from django.db.utils import OperationalError


# Shared protocol for transactional writers that can retain one User row while
# waiting for another. Every participant must acquire this lock before any row
# lock in its flow (Business, BusinessMembership, User, or token rows).
# "PNMUSR" names the PlayNow multi-user-write namespace. A new key deliberately
# replaces the former phase-specific namespace.
_MULTI_USER_WRITE_LOCK_KEY = 0x504E4D555352
_LOCK_NOT_AVAILABLE_SQLSTATE = "55P03"
_MAX_POSTGRES_TIMEOUT_MS = 2_147_483_647
_protocol_activated = ContextVar("multi_user_write_protocol_activated", default=False)


class MultiUserWriteContentionError(Exception):
    """A bounded multi-User lock wait expired."""


def _configured_timeout_ms():
    value = settings.MULTI_USER_WRITE_LOCK_TIMEOUT_MS
    if type(value) is not int or not 1 <= value <= _MAX_POSTGRES_TIMEOUT_MS:
        raise ImproperlyConfigured(
            "MULTI_USER_WRITE_LOCK_TIMEOUT_MS must be an integer between "
            "1 and 2147483647."
        )
    return value


def _database_sqlstate(exc):
    seen = set()
    current = exc
    while current is not None:
        if id(current) in seen:
            break
        seen.add(id(current))
        for attribute in ("sqlstate", "pgcode"):
            value = getattr(current, attribute, None)
            if value:
                return value
        explicit_cause = getattr(current, "__cause__", None)
        current = (
            explicit_cause
            if explicit_cause is not None
            else getattr(current, "__context__", None)
        )
    return None


def translate_multi_user_write_contention(operation):
    """Translate only PostgreSQL lock timeout after atomic rollback completes."""

    @wraps(operation)
    def wrapped(*args, **kwargs):
        token = _protocol_activated.set(False)
        try:
            return operation(*args, **kwargs)
        except OperationalError as exc:
            if (
                not _protocol_activated.get()
                or _database_sqlstate(exc) != _LOCK_NOT_AVAILABLE_SQLSTATE
            ):
                raise
            raise MultiUserWriteContentionError() from exc
        finally:
            _protocol_activated.reset(token)

    return wrapped


def acquire_multi_user_write_lock():
    """Serialize multi-User writers for the current database transaction."""

    if not connection.in_atomic_block:
        raise RuntimeError(
            "The multi-User write lock requires an active transaction."
        )
    _protocol_activated.set(True)
    with connection.cursor() as cursor:
        cursor.execute(
            "SELECT set_config('lock_timeout', %s, true)",
            [f"{_configured_timeout_ms()}ms"],
        )
        cursor.execute(
            "SELECT pg_advisory_xact_lock(%s)",
            [_MULTI_USER_WRITE_LOCK_KEY],
        )
