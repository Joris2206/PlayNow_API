from dataclasses import dataclass

from django.db import IntegrityError, transaction

from core.models import Business, BusinessMembership, User
from core.services.business_memberships import NON_OWNER_ROLES
from core.utils import log_action


EXISTING_USER_ERROR = {
    "email": [
        "Ya existe una cuenta con este correo. "
        "Utiliza el flujo para agregar un usuario existente."
    ]
}

HIDDEN_RESOURCE_ERROR = "El recurso solicitado no se encuentra disponible."


@dataclass(frozen=True)
class BusinessUserProvisioningError(Exception):
    detail: object
    kind: str = "invalid"

    def __str__(self):
        return str(self.detail)


def _lock_business_by_public_id(public_id):
    try:
        return (
            Business.objects.select_for_update()
            .select_related("status")
            .get(public_id=public_id)
        )
    except (Business.DoesNotExist, ValueError) as exc:
        raise BusinessUserProvisioningError(
            HIDDEN_RESOURCE_ERROR,
            "not_found",
        ) from exc


def _actor_membership_id(*, actor_id, business):
    return (
        BusinessMembership.objects.filter(
            business=business,
            user_id=actor_id,
        )
        .values_list("pk", flat=True)
        .first()
    )


def _lock_actor_membership(*, business, membership_id):
    if membership_id is None:
        return None
    return (
        BusinessMembership.objects.select_for_update()
        .filter(business=business, pk=membership_id)
        .first()
    )


def _lock_actor(actor_id):
    try:
        return User.objects.select_for_update().get(pk=actor_id)
    except User.DoesNotExist as exc:
        raise BusinessUserProvisioningError(
            HIDDEN_RESOURCE_ERROR,
            "not_found",
        ) from exc


def _business_is_visible(business):
    status_name = business.status.name.strip().casefold()
    return status_name not in {
        "eliminado",
        "eliminada",
        "deleted",
        "anulado",
        "anulada",
        "cancelado",
        "cancelada",
    }


def _authorize(*, business, actor, actor_membership):
    if not _business_is_visible(business):
        raise BusinessUserProvisioningError(
            HIDDEN_RESOURCE_ERROR,
            "not_found",
        )
    if not actor.is_active:
        raise BusinessUserProvisioningError(
            HIDDEN_RESOURCE_ERROR,
            "not_found",
        )
    if actor.is_superuser:
        return
    if actor_membership is None or not actor_membership.is_active:
        raise BusinessUserProvisioningError(
            HIDDEN_RESOURCE_ERROR,
            "not_found",
        )
    if actor_membership.role != BusinessMembership.ROLE_OWNER:
        raise BusinessUserProvisioningError(
            "Solo un owner activo o Platform Admin puede crear cuentas.",
            "forbidden",
        )


def _validate_role(role):
    if role not in NON_OWNER_ROLES:
        raise BusinessUserProvisioningError(
            {"role": ["Debe seleccionar un rol permitido distinto de owner."]},
            "invalid",
        )


def _constraint_name(exc):
    cause = getattr(exc, "__cause__", None)
    diagnostic = getattr(cause, "diag", None)
    return getattr(diagnostic, "constraint_name", None)


def _assert_final_state(*, user, business, membership, role):
    valid = (
        user.is_active
        and not user.is_staff
        and not user.is_superuser
        and membership.user_id == user.pk
        and membership.business_id == business.pk
        and membership.employee_id is None
        and membership.role == role
        and membership.is_active
        and BusinessMembership.objects.filter(
            user=user,
            business=business,
        ).count() == 1
    )
    if not valid:
        raise BusinessUserProvisioningError(
            "No fue posible completar la creación de la cuenta.",
            "conflict",
        )


@transaction.atomic
def provision_business_user(
    *,
    actor,
    business_public_id,
    validate_payload,
):
    business = _lock_business_by_public_id(business_public_id)
    actor_membership_id = _actor_membership_id(
        actor_id=actor.pk,
        business=business,
    )
    actor_membership = _lock_actor_membership(
        business=business,
        membership_id=actor_membership_id,
    )
    locked_actor = _lock_actor(actor.pk)
    _authorize(
        business=business,
        actor=locked_actor,
        actor_membership=actor_membership,
    )

    payload = validate_payload()
    role = payload["role"]
    _validate_role(role)
    email = payload["email"]
    if User.objects.filter(email__iexact=email).exists():
        raise BusinessUserProvisioningError(EXISTING_USER_ERROR, "conflict")

    try:
        with transaction.atomic():
            user = User.objects.create_user(
                email=email,
                full_name=payload["full_name"],
                password=payload["password"],
                role=User.Roles.EMPLOYEE,
                is_active=True,
                is_staff=False,
                is_superuser=False,
            )
    except IntegrityError as exc:
        if _constraint_name(exc) == "unique_user_email_ci":
            raise BusinessUserProvisioningError(
                EXISTING_USER_ERROR,
                "conflict",
            ) from exc
        raise

    membership = BusinessMembership.objects.create(
        user=user,
        business=business,
        employee=None,
        role=role,
        is_active=True,
    )
    _assert_final_state(
        user=user,
        business=business,
        membership=membership,
        role=role,
    )
    log_action(
        locked_actor,
        "PROVISION_BUSINESS_USER",
        membership.__class__.__name__,
        membership.pk,
        extra={"user_id": user.pk, "role": role},
    )
    return membership
