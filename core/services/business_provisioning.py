from dataclasses import dataclass

from django.db import transaction

from core.models import Business, BusinessMembership, User


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


def _lock_users(user_ids):
    ordered_ids = sorted(set(user_ids))
    users = list(
        User.objects.select_for_update()
        .filter(pk__in=ordered_ids)
        .order_by("pk")
    )
    by_id = {user.pk: user for user in users}
    if len(by_id) != len(ordered_ids):
        raise BusinessProvisioningError(
            "La cuenta autenticada ya no se encuentra disponible.",
            "forbidden",
        )
    return by_id


def _has_effective_owner(business):
    return BusinessMembership.objects.filter(
        business=business,
        role=BusinessMembership.ROLE_OWNER,
        is_active=True,
        user__is_active=True,
    ).exists()


@transaction.atomic
def provision_business(
    *,
    actor,
    validated_business_data,
    initial_owner_email=None,
):
    requested_admin_mode = initial_owner_email is not None
    candidate_ids = _candidate_owner_ids(initial_owner_email)
    locked_users = _lock_users([actor.pk, *candidate_ids])
    locked_actor = locked_users[actor.pk]

    if not locked_actor.is_active:
        raise BusinessProvisioningError(
            "La cuenta autenticada no puede crear negocios.",
            "forbidden",
        )
    if requested_admin_mode:
        if not locked_actor.is_superuser:
            raise BusinessProvisioningError(
                "La operación administrativa ya no está autorizada.",
                "forbidden",
            )
        if len(candidate_ids) != 1:
            raise _initial_owner_error()
        locked_owner = locked_users[candidate_ids[0]]
        if (
            not locked_owner.is_active
            or locked_owner.email.strip().casefold()
            != initial_owner_email.strip().casefold()
        ):
            raise _initial_owner_error()
    else:
        if locked_actor.is_superuser:
            raise BusinessProvisioningError(
                {"initial_owner_email": ["Este campo es obligatorio."]},
                "invalid",
            )
        locked_owner = locked_actor

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
