from dataclasses import dataclass
from contextlib import contextmanager

from django.db import IntegrityError, transaction

from core.models import User
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


@transaction.atomic
def update_platform_user_identity(
    *,
    actor,
    target_public_id,
    validate_payload,
):
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
