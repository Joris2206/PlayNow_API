from dataclasses import dataclass

from django.db import transaction

from core.models import Business, BusinessMembership, User
from core.services import multi_user_write_lock


INITIAL_OWNER_ERROR = "No fue posible asignar el propietario inicial."


@dataclass(frozen=True)
class BusinessProvisioningError(Exception):
    detail: object
    kind: str = "invalid"

    def __str__(self):
        return str(self.detail)


def _initial_owner_error():
    return BusinessProvisioningError(
        {"initial_owner_email": [INITIAL_OWNER_ERROR]},
        "invalid",
    )


def _candidate_owner_ids(email):
    if email is None:
        return []
    return list(
        User.objects.filter(email__iexact=email)
        .order_by("pk")
        .values_list("pk", flat=True)[:2]
    )


def _lock_actor(actor_id):
    try:
        return User.objects.select_for_update().get(pk=actor_id)
    except User.DoesNotExist as exc:
        raise BusinessProvisioningError(
            "La cuenta autenticada ya no se encuentra disponible.",
            "forbidden",
        ) from exc


def _lock_initial_owner(*, owner_id, locked_actor):
    if owner_id == locked_actor.pk:
        return locked_actor
    try:
        return User.objects.select_for_update().get(pk=owner_id)
    except User.DoesNotExist as exc:
        raise _initial_owner_error() from exc


def _has_effective_owner(business):
    return BusinessMembership.objects.filter(
        business=business,
        role=BusinessMembership.ROLE_OWNER,
        is_active=True,
        user__is_active=True,
    ).exists()


def _preauthorize_administrative_provisioning(actor_id):
    if not User.objects.filter(
        pk=actor_id,
        is_active=True,
        is_superuser=True,
    ).exists():
        raise BusinessProvisioningError(
            "La operación administrativa ya no está autorizada.",
            "forbidden",
        )


@multi_user_write_lock.translate_multi_user_write_contention
@transaction.atomic
def provision_business(
    *,
    actor,
    validated_business_data,
    initial_owner_email=None,
):
    _preauthorize_administrative_provisioning(actor.pk)
    multi_user_write_lock.acquire_multi_user_write_lock()
    locked_actor = _lock_actor(actor.pk)

    if not locked_actor.is_active or not locked_actor.is_superuser:
        raise BusinessProvisioningError(
            "La operación administrativa ya no está autorizada.",
            "forbidden",
        )

    if initial_owner_email is None:
        raise BusinessProvisioningError(
            {"initial_owner_email": ["Este campo es obligatorio."]},
            "invalid",
        )
    candidate_ids = _candidate_owner_ids(initial_owner_email)
    if len(candidate_ids) != 1:
        raise _initial_owner_error()
    locked_owner = _lock_initial_owner(
        owner_id=candidate_ids[0],
        locked_actor=locked_actor,
    )
    if (
        not locked_owner.is_active
        or locked_owner.email.strip().casefold()
        != initial_owner_email.strip().casefold()
    ):
        raise _initial_owner_error()

    business = Business.objects.create(
        user=locked_actor,
        **validated_business_data,
    )
    owner_membership = BusinessMembership.objects.create(
        business=business,
        user=locked_owner,
        employee=None,
        role=BusinessMembership.ROLE_OWNER,
        is_active=True,
    )
    if not _has_effective_owner(business):
        raise BusinessProvisioningError(
            "No fue posible completar el propietario inicial.",
            "conflict",
        )
    return business, owner_membership
