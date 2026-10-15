from dataclasses import dataclass
from contextlib import contextmanager

from django.contrib.auth.password_validation import validate_password
from django.core.exceptions import ValidationError as DjangoValidationError
from django.db import IntegrityError, transaction

from core.models import User
from core.services import multi_user_write_lock
from core.services.auth_sessions import _revoke_locked_user_sessions
from core.utils import log_action


EMAIL_CONFLICT = {"email": ["Ya existe una cuenta con este correo."]}
HIDDEN_RESOURCE = "El recurso solicitado no se encuentra disponible."
EMAIL_UNIQUE_CONSTRAINTS = {
    "core_user_email_key",
    "unique_user_email_ci",
}


@dataclass(frozen=True)
class PlatformUserIdentityError(Exception):
    detail: object
    kind: str

    def __str__(self):
        return str(self.detail)


def _constraint_name(exc):
    cause = getattr(exc, "__cause__", None)
    diagnostic = getattr(cause, "diag", None)
    return getattr(diagnostic, "constraint_name", None)


def _candidate_target_id(public_id):
    try:
        return (
            User.objects.filter(public_id=public_id)
            .values_list("pk", flat=True)
            .first()
        )
    except (TypeError, ValueError):
        return None


def _lock_actor_and_target(*, actor_id, target_id):
    user_ids = sorted({
        user_id
        for user_id in (actor_id, target_id)
        if user_id is not None
    })
    users = list(
        User.objects.select_for_update()
        .filter(pk__in=user_ids)
        .order_by("pk")
    )
    return {user.pk: user for user in users}


def _authorize(locked_actor):
    if (
        locked_actor is None
        or not locked_actor.is_active
        or not locked_actor.is_superuser
    ):
        raise PlatformUserIdentityError(
            "La operación administrativa ya no está autorizada.",
            "forbidden",
        )


@contextmanager
def locked_platform_admin_read(*, actor_id):
    """Keep the definitive actor authorization locked through materialization."""

    with transaction.atomic():
        locked_actor = (
            User.objects.select_for_update()
            .filter(pk=actor_id)
            .first()
        )
        _authorize(locked_actor)
        yield locked_actor


def _assert_protected_identity_unchanged(target, protected):
    current = {
        field: getattr(target, field)
        for field in protected
    }
    if current != protected:
        raise PlatformUserIdentityError(
            "No fue posible completar la actualización de identidad.",
            "conflict",
        )


@multi_user_write_lock.translate_multi_user_write_contention
@transaction.atomic
def update_platform_user_identity(
    *,
    actor,
    target_public_id,
    validate_payload,
):
    if str(target_public_id) != str(actor.public_id):
        multi_user_write_lock.acquire_multi_user_write_lock()
    target_id = _candidate_target_id(target_public_id)
    locked_users = _lock_actor_and_target(
        actor_id=actor.pk,
        target_id=target_id,
    )
    locked_actor = locked_users.get(actor.pk)
    _authorize(locked_actor)

    target = locked_users.get(target_id)
    if target is None or target.public_id != target_public_id:
        raise PlatformUserIdentityError(HIDDEN_RESOURCE, "not_found")

    protected_fields = (
        "password",
        "is_active",
        "is_superuser",
        "is_staff",
        "role",
        "auth_version",
    )
    protected = {
        field: getattr(target, field)
        for field in protected_fields
    }
    payload = validate_payload(target)
    changed_fields = []

    if "email" in payload:
        email = payload["email"]
        if (
            User.objects.filter(email__iexact=email)
            .exclude(pk=target.pk)
            .exists()
        ):
            raise PlatformUserIdentityError(EMAIL_CONFLICT, "conflict")
        if target.email != email:
            target.email = email
            changed_fields.append("email")

    if "full_name" in payload and target.full_name != payload["full_name"]:
        target.full_name = payload["full_name"]
        changed_fields.append("full_name")

    if changed_fields:
        try:
            with transaction.atomic():
                target.save(update_fields=[*changed_fields, "updated_at"])
        except IntegrityError as exc:
            if _constraint_name(exc) in EMAIL_UNIQUE_CONSTRAINTS:
                raise PlatformUserIdentityError(
                    EMAIL_CONFLICT,
                    "conflict",
                ) from exc
            raise

    _assert_protected_identity_unchanged(target, protected)
    log_action(
        locked_actor,
        "UPDATE_PLATFORM_USER_IDENTITY",
        target.__class__.__name__,
        target.public_id,
        extra={"fields": sorted(changed_fields)},
    )
    return target


SELF_MUTATION = "Un Platform Admin no puede realizar esta operación sobre sí mismo."


def _lock_platform_admin(actor_id):
    locked_actor = (
        User.objects.select_for_update()
        .filter(pk=actor_id)
        .first()
    )
    _authorize(locked_actor)
    return locked_actor


def _lock_platform_user_target(target_public_id):
    target = (
        User.objects.select_for_update()
        .filter(public_id=target_public_id)
        .first()
    )
    if target is None:
        raise PlatformUserIdentityError(HIDDEN_RESOURCE, "not_found")
    return target


def _audit_platform_user_action(actor, target, action):
    log_action(
        actor,
        action,
        target.__class__.__name__,
        target.public_id,
        extra=None,
        actor_public_id=actor.public_id,
    )


def _assert_admin_mutation_result(
    target,
    *,
    expected_is_active,
    expected_password,
    expected_auth_version,
    protected,
):
    target.refresh_from_db()
    current_protected = {
        field: getattr(target, field)
        for field in protected
    }
    if (
        target.is_active is not expected_is_active
        or target.password != expected_password
        or target.auth_version != expected_auth_version
        or current_protected != protected
    ):
        raise PlatformUserIdentityError(
            "No fue posible verificar la operación administrativa.",
            "conflict",
        )


def _protected_admin_fields(target):
    return {
        field: getattr(target, field)
        for field in (
            "email",
            "full_name",
            "is_superuser",
            "is_staff",
            "role",
        )
    }


@multi_user_write_lock.translate_multi_user_write_contention
@transaction.atomic
def activate_platform_user(*, actor_id, target_public_id, validate_payload):
    multi_user_write_lock.acquire_multi_user_write_lock()
    actor = _lock_platform_admin(actor_id)
    target = _lock_platform_user_target(target_public_id)
    validate_payload(target)

    if target.is_active:
        return target

    password = target.password
    auth_version = target.auth_version
    protected = _protected_admin_fields(target)
    target.is_active = True
    target.save(update_fields=["is_active", "updated_at"])
    _assert_admin_mutation_result(
        target,
        expected_is_active=True,
        expected_password=password,
        expected_auth_version=auth_version,
        protected=protected,
    )
    _audit_platform_user_action(actor, target, "activate")
    return target


@multi_user_write_lock.translate_multi_user_write_contention
@transaction.atomic
def deactivate_platform_user(*, actor_id, target_public_id, validate_payload):
    multi_user_write_lock.acquire_multi_user_write_lock()
    actor = _lock_platform_admin(actor_id)
    target = _lock_platform_user_target(target_public_id)
    if target.pk == actor.pk:
        raise PlatformUserIdentityError(SELF_MUTATION, "conflict")
    validate_payload(target)

    if not target.is_active:
        return target

    password = target.password
    auth_version = target.auth_version + 1
    protected = _protected_admin_fields(target)
    target.is_active = False
    _revoke_locked_user_sessions(
        target,
        update_fields=("is_active", "updated_at"),
    )
    _assert_admin_mutation_result(
        target,
        expected_is_active=False,
        expected_password=password,
        expected_auth_version=auth_version,
        protected=protected,
    )
    _audit_platform_user_action(actor, target, "deactivate")
    return target


@multi_user_write_lock.translate_multi_user_write_contention
@transaction.atomic
def reset_platform_user_password(
    *,
    actor_id,
    target_public_id,
    validate_payload,
):
    multi_user_write_lock.acquire_multi_user_write_lock()
    actor = _lock_platform_admin(actor_id)
    target = _lock_platform_user_target(target_public_id)
    if target.pk == actor.pk:
        raise PlatformUserIdentityError(SELF_MUTATION, "conflict")
    payload = validate_payload(target)
    password = payload["password"]
    try:
        validate_password(password, user=target)
    except DjangoValidationError as exc:
        raise PlatformUserIdentityError(
            {"password": list(exc.messages)},
            "invalid",
        ) from exc

    auth_version = target.auth_version + 1
    protected = _protected_admin_fields(target)
    target.set_password(password)
    expected_password = target.password
    _revoke_locked_user_sessions(
        target,
        update_fields=("password", "updated_at"),
    )
    _assert_admin_mutation_result(
        target,
        expected_is_active=target.is_active,
        expected_password=expected_password,
        expected_auth_version=auth_version,
        protected=protected,
    )
    _audit_platform_user_action(actor, target, "reset-password")
    return target


@multi_user_write_lock.translate_multi_user_write_contention
@transaction.atomic
def revoke_platform_user_sessions(
    *,
    actor_id,
    target_public_id,
    validate_payload,
):
    multi_user_write_lock.acquire_multi_user_write_lock()
    actor = _lock_platform_admin(actor_id)
    target = _lock_platform_user_target(target_public_id)
    if target.pk == actor.pk:
        raise PlatformUserIdentityError(SELF_MUTATION, "conflict")
    validate_payload(target)

    password = target.password
    is_active = target.is_active
    auth_version = target.auth_version + 1
    protected = _protected_admin_fields(target)
    _revoke_locked_user_sessions(target)
    _assert_admin_mutation_result(
        target,
        expected_is_active=is_active,
        expected_password=password,
        expected_auth_version=auth_version,
        protected=protected,
    )
    _audit_platform_user_action(actor, target, "revoke-sessions")
    return target
