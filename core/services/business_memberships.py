from dataclasses import dataclass
from uuid import UUID

from django.db import transaction
from django.db.models import Exists, OuterRef, Q

from core.models import Business, BusinessMembership, User


NON_OWNER_ROLES = frozenset({
    BusinessMembership.ROLE_ADMIN,
    BusinessMembership.ROLE_CASHIER,
    BusinessMembership.ROLE_SELLER,
    BusinessMembership.ROLE_INVENTORY,
    BusinessMembership.ROLE_VIEWER,
})

LOWER_ROLES = frozenset({
    BusinessMembership.ROLE_CASHIER,
    BusinessMembership.ROLE_SELLER,
    BusinessMembership.ROLE_INVENTORY,
    BusinessMembership.ROLE_VIEWER,
})


@dataclass(frozen=True)
class MembershipDomainError(Exception):
    detail: object
    kind: str = "invalid"

    def __str__(self):
        return str(self.detail)


def _lock_business(business_id):
    try:
        return Business.objects.select_for_update().get(pk=business_id)
    except Business.DoesNotExist as exc:
        raise MembershipDomainError(
            "El negocio ya no se encuentra disponible.",
            "conflict",
        ) from exc


def _lock_business_by_public_id(public_id):
    try:
        return Business.objects.select_for_update().get(public_id=public_id)
    except (Business.DoesNotExist, ValueError) as exc:
        raise MembershipDomainError(
            "El recurso solicitado no se encuentra disponible.",
            "not_found",
        ) from exc


def _lock_memberships(*, business, membership_ids):
    unique_ids = sorted(set(membership_ids))
    memberships = list(
        BusinessMembership.objects.select_for_update()
        .filter(business=business, pk__in=unique_ids)
        .order_by("pk")
    )
    by_id = {membership.pk: membership for membership in memberships}
    if len(by_id) != len(unique_ids):
        raise MembershipDomainError(
            "La membresía indicada no existe en este negocio.",
            "not_found",
        )
    return by_id


def _lock_users(*, memberships, additional_user_ids=()):
    user_ids = sorted({
        *(membership.user_id for membership in memberships),
        *additional_user_ids,
    })
    users = list(
        User.objects.select_for_update()
        .filter(pk__in=user_ids)
        .order_by("pk")
    )
    by_id = {user.pk: user for user in users}
    if len(by_id) != len(user_ids):
        raise MembershipDomainError(
            "Una cuenta relacionada ya no se encuentra disponible.",
            "conflict",
        )
    return by_id


def _actor_membership_id(*, actor_id, business):
    return (
        BusinessMembership.objects.filter(
            business=business,
            user_id=actor_id,
        )
        .values_list("pk", flat=True)
        .first()
    )


def _authorize_management_actor(*, locked_actor, actor_membership):
    if not locked_actor.is_active:
        raise MembershipDomainError(
            "No tienes permiso para administrar membresías.",
            "forbidden",
        )
    if locked_actor.is_superuser:
        return
    if actor_membership is None or not actor_membership.is_active:
        raise MembershipDomainError(
            "No tienes permiso para administrar membresías.",
            "forbidden",
        )
    if actor_membership.role not in (
        BusinessMembership.ROLE_OWNER,
        BusinessMembership.ROLE_ADMIN,
    ):
        raise MembershipDomainError(
            "No tienes permiso para administrar membresías.",
            "forbidden",
        )


def _validate_management_target(
    *,
    locked_actor,
    actor_membership,
    target,
    requested_role=None,
):
    if target.user_id == locked_actor.pk:
        raise MembershipDomainError(
            "No puedes modificar tu propia membresía.",
            "forbidden",
        )
    if target.role == BusinessMembership.ROLE_OWNER:
        raise MembershipDomainError(
            "Los owners solo pueden modificarse mediante las acciones de ownership.",
            "forbidden",
        )
    if requested_role == BusinessMembership.ROLE_OWNER:
        raise MembershipDomainError(
            "El rol owner solo puede asignarse mediante la acción de promoción.",
            "forbidden",
        )
    if (
        locked_actor.is_superuser
        or actor_membership.role == BusinessMembership.ROLE_OWNER
    ):
        return
    if target.role == BusinessMembership.ROLE_ADMIN:
        raise MembershipDomainError(
            "Un administrador no puede modificar a otro administrador.",
            "forbidden",
        )
    if requested_role not in (None, *LOWER_ROLES):
        raise MembershipDomainError(
            "Un administrador no puede escalar privilegios.",
            "forbidden",
        )


def _authorize_management(
    *,
    locked_actor,
    actor_membership,
    target,
    requested_role=None,
):
    _authorize_management_actor(
        locked_actor=locked_actor,
        actor_membership=actor_membership,
    )
    _validate_management_target(
        locked_actor=locked_actor,
        actor_membership=actor_membership,
        target=target,
        requested_role=requested_role,
    )
def _authorize_ownership(*, locked_actor, actor_membership):
    if not locked_actor.is_active:
        raise MembershipDomainError(
            "Solo un owner efectivo puede administrar ownership.",
            "forbidden",
        )
    if locked_actor.is_superuser:
        return
    if (
        actor_membership is None
        or not actor_membership.is_active
        or actor_membership.role != BusinessMembership.ROLE_OWNER
    ):
        raise MembershipDomainError(
            "Solo un owner efectivo puede administrar ownership.",
            "forbidden",
        )


def _hide_missing_actor_access(authorize, *, locked_actor, actor_membership):
    try:
        authorize(
            locked_actor=locked_actor,
            actor_membership=actor_membership,
        )
    except MembershipDomainError as exc:
        has_visibility = (
            locked_actor.is_active
            and actor_membership is not None
            and actor_membership.is_active
        )
        if exc.kind == "forbidden" and not has_visibility:
            raise MembershipDomainError(
                "El recurso solicitado no se encuentra disponible.",
                "not_found",
            ) from exc
        raise


def _validate_non_owner_role(*, field_name, value):
    if value not in NON_OWNER_ROLES:
        raise MembershipDomainError(
            {field_name: ["Debe seleccionar un rol distinto de owner."]},
            "invalid",
        )


TARGET_EMAIL_ERROR = {
    "email": ["No se pudo agregar una cuenta elegible con este correo."]
}


def _preauthorize_add_existing_member(*, actor_id, business_public_id):
    """Reject unauthorized actors before resolving any target identity."""
    visible_businesses = (
        Business.objects.filter(public_id=business_public_id)
        .annotate(
            actor_is_platform_admin=Exists(
                User.objects.filter(
                    pk=actor_id,
                    is_active=True,
                    is_superuser=True,
                )
            ),
            actor_can_manage=Exists(
                BusinessMembership.objects.filter(
                    business_id=OuterRef("pk"),
                    user_id=actor_id,
                    user__is_active=True,
                    is_active=True,
                    role__in=[
                        BusinessMembership.ROLE_OWNER,
                        BusinessMembership.ROLE_ADMIN,
                    ],
                )
            ),
            actor_has_active_membership=Exists(
                BusinessMembership.objects.filter(
                    business_id=OuterRef("pk"),
                    user_id=actor_id,
                    user__is_active=True,
                    is_active=True,
                )
            ),
        )
        .filter(
            Q(actor_is_platform_admin=True)
            | Q(actor_has_active_membership=True)
        )
        .only("pk")
    )
    try:
        visible_business = visible_businesses.get()
    except (Business.DoesNotExist, ValueError) as exc:
        raise MembershipDomainError(
            "El recurso solicitado no se encuentra disponible.",
            "not_found",
        ) from exc
    if not (
        visible_business.actor_is_platform_admin
        or visible_business.actor_can_manage
    ):
        raise MembershipDomainError(
            "No tienes permiso para administrar membresÃ­as.",
            "forbidden",
        )


@transaction.atomic
def add_existing_member(*, actor, business_public_id, email, role):
    """Create or reactivate a non-owner membership for an existing User."""
    _validate_non_owner_role(field_name="role", value=role)
    _preauthorize_add_existing_member(
        actor_id=actor.pk,
        business_public_id=business_public_id,
    )

    # The target is resolved only after the actor passes the DB-backed
    # preflight. Eligibility is still authoritative only after row locks.
    target_user_ids = list(
        User.objects.filter(email__iexact=email).values_list("pk", flat=True)
    )

    business = _lock_business_by_public_id(business_public_id)
    actor_membership_id = _actor_membership_id(
        actor_id=actor.pk,
        business=business,
    )
    target_membership_id = None
    if len(target_user_ids) == 1:
        target_membership_ids = list(
            BusinessMembership.objects.filter(
                business=business,
                user_id=target_user_ids[0],
            )
            .values_list("pk", flat=True)
        )
        if target_membership_ids:
            target_membership_id = target_membership_ids[0]

    membership_ids = sorted({
        membership_id
        for membership_id in (actor_membership_id, target_membership_id)
        if membership_id is not None
    })
    locked_memberships = list(
        BusinessMembership.objects.select_for_update()
        .filter(business=business, pk__in=membership_ids)
        .order_by("pk")
    )
    memberships_by_id = {
        membership.pk: membership for membership in locked_memberships
    }

    user_ids = sorted({actor.pk, *target_user_ids})
    locked_users = list(
        User.objects.select_for_update()
        .filter(pk__in=user_ids)
        .order_by("pk")
    )
    users_by_id = {user.pk: user for user in locked_users}
    locked_actor = users_by_id.get(actor.pk)
    actor_membership = memberships_by_id.get(actor_membership_id)
    if locked_actor is None:
        raise MembershipDomainError(
            "El recurso solicitado no se encuentra disponible.",
            "not_found",
        )
    _hide_missing_actor_access(
        _authorize_management_actor,
        locked_actor=locked_actor,
        actor_membership=actor_membership,
    )

    if (
        not locked_actor.is_superuser
        and actor_membership.role == BusinessMembership.ROLE_ADMIN
        and role not in LOWER_ROLES
    ):
        raise MembershipDomainError(
            "Un administrador no puede escalar privilegios.",
            "forbidden",
        )

    if len(target_user_ids) != 1:
        raise MembershipDomainError(TARGET_EMAIL_ERROR, "invalid")
    target_user = users_by_id.get(target_user_ids[0])
    if (
        target_user is None
        or not target_user.is_active
        or target_user.email.casefold() != email.casefold()
    ):
        raise MembershipDomainError(TARGET_EMAIL_ERROR, "invalid")

    existing = memberships_by_id.get(target_membership_id)
    if existing is not None:
        if existing.role == BusinessMembership.ROLE_OWNER:
            raise MembershipDomainError(
                "Los owners solo pueden modificarse mediante las acciones de ownership.",
                "conflict",
            )
        if existing.is_active:
            raise MembershipDomainError(
                "El User ya tiene acceso activo a este negocio.",
                "conflict",
            )
        existing.role = role
        existing.is_active = True
        existing.save(update_fields=["role", "is_active", "updated_at"])
        return existing, False

    membership = BusinessMembership.objects.create(
        business=business,
        user=target_user,
        employee=None,
        role=role,
        is_active=True,
    )
    return membership, True


def effective_owner_count(*, business):
    return BusinessMembership.objects.filter(
        business=business,
        role=BusinessMembership.ROLE_OWNER,
        is_active=True,
        user__is_active=True,
    ).count()


def _assert_effective_owner(*, business):
    if effective_owner_count(business=business) < 1:
        raise MembershipDomainError(
            "El negocio debe conservar al menos un owner efectivo.",
            "conflict",
        )


def _locked_context(*, actor, business_id, target_ids):
    business = _lock_business(business_id)
    actor_membership_id = _actor_membership_id(
        actor_id=actor.pk,
        business=business,
    )
    membership_ids = list(target_ids)
    if actor_membership_id is not None:
        membership_ids.append(actor_membership_id)
    memberships = _lock_memberships(
        business=business,
        membership_ids=membership_ids,
    )
    users = _lock_users(
        memberships=memberships.values(),
        additional_user_ids=[actor.pk],
    )
    locked_actor = users[actor.pk]
    actor_membership = memberships.get(actor_membership_id)
    return business, memberships, users, locked_actor, actor_membership


@transaction.atomic
def update_membership(*, actor, membership, changes):
    business, memberships, _, locked_actor, actor_membership = _locked_context(
        actor=actor,
        business_id=membership.business_id,
        target_ids=[membership.pk],
    )
    target = memberships[membership.pk]
    requested_role = changes.get("role")
    _authorize_management(
        locked_actor=locked_actor,
        actor_membership=actor_membership,
        target=target,
        requested_role=requested_role,
    )
    update_fields = []
    for field_name in ("role", "is_active"):
        if field_name in changes and getattr(target, field_name) != changes[field_name]:
            setattr(target, field_name, changes[field_name])
            update_fields.append(field_name)
    if update_fields:
        target.save(update_fields=[*update_fields, "updated_at"])
    _assert_effective_owner(business=business)
    return target


@transaction.atomic
def deactivate_membership(*, actor, membership):
    business, memberships, _, locked_actor, actor_membership = _locked_context(
        actor=actor,
        business_id=membership.business_id,
        target_ids=[membership.pk],
    )
    target = memberships[membership.pk]
    _authorize_management(
        locked_actor=locked_actor,
        actor_membership=actor_membership,
        target=target,
    )
    if target.is_active:
        target.is_active = False
        target.save(update_fields=["is_active", "updated_at"])
    _assert_effective_owner(business=business)
    return target


@transaction.atomic
def promote_to_owner(*, actor, business, membership):
    (
        locked_business,
        memberships,
        users,
        locked_actor,
        actor_membership,
    ) = _locked_context(
        actor=actor,
        business_id=business.pk,
        target_ids=[membership.pk],
    )
    target = memberships[membership.pk]
    _authorize_ownership(
        locked_actor=locked_actor,
        actor_membership=actor_membership,
    )
    target_user = users[target.user_id]
    if not target.is_active or not target_user.is_active:
        raise MembershipDomainError(
            "La membership y su User deben estar activos.",
            "invalid",
        )
    if (
        target.user_id == locked_actor.pk
        and target.role != BusinessMembership.ROLE_OWNER
    ):
        raise MembershipDomainError(
            "No puedes promover tu propia membresía.",
            "forbidden",
        )
    if target.role != BusinessMembership.ROLE_OWNER:
        target.role = BusinessMembership.ROLE_OWNER
        target.save(update_fields=["role", "updated_at"])
    _assert_effective_owner(business=locked_business)
    return target


@transaction.atomic
def transfer_ownership(*, actor, business, from_membership, to_membership, from_role):
    if from_membership.pk == to_membership.pk:
        raise MembershipDomainError(
            "Las memberships de origen y destino deben ser diferentes.",
            "invalid",
        )
    (
        locked_business,
        memberships,
        users,
        locked_actor,
        actor_membership,
    ) = _locked_context(
        actor=actor,
        business_id=business.pk,
        target_ids=[from_membership.pk, to_membership.pk],
    )
    source = memberships[from_membership.pk]
    target = memberships[to_membership.pk]
    _authorize_ownership(
        locked_actor=locked_actor,
        actor_membership=actor_membership,
    )
    _validate_non_owner_role(field_name="from_role", value=from_role)
    if (
        source.role != BusinessMembership.ROLE_OWNER
        or not source.is_active
        or not users[source.user_id].is_active
    ):
        raise MembershipDomainError(
            "La membership de origen ya no es un owner efectivo.",
            "conflict",
        )
    if not target.is_active or not users[target.user_id].is_active:
        raise MembershipDomainError(
            "La membership y el User de destino deben estar activos.",
            "invalid",
        )
    target.role = BusinessMembership.ROLE_OWNER
    target.save(update_fields=["role", "updated_at"])
    source.role = from_role
    source.save(update_fields=["role", "updated_at"])
    _assert_effective_owner(business=locked_business)
    return source, target


@transaction.atomic
def remove_owner(*, actor, business, membership, replacement_role):
    (
        locked_business,
        memberships,
        _,
        locked_actor,
        actor_membership,
    ) = _locked_context(
        actor=actor,
        business_id=business.pk,
        target_ids=[membership.pk],
    )
    target = memberships[membership.pk]
    _authorize_ownership(
        locked_actor=locked_actor,
        actor_membership=actor_membership,
    )
    _validate_non_owner_role(
        field_name="replacement_role",
        value=replacement_role,
    )
    if target.user_id == locked_actor.pk:
        raise MembershipDomainError(
            "No puedes retirar tu propio ownership.",
            "forbidden",
        )
    if target.role != BusinessMembership.ROLE_OWNER:
        raise MembershipDomainError(
            "La membership indicada no es owner.",
            "invalid",
        )
    target.role = replacement_role
    target.save(update_fields=["role", "updated_at"])
    _assert_effective_owner(business=locked_business)
    return target


def _membership_identity(public_id):
    try:
        return BusinessMembership.objects.only("pk", "business_id").get(
            public_id=public_id,
        )
    except (BusinessMembership.DoesNotExist, ValueError) as exc:
        raise MembershipDomainError(
            "El recurso solicitado no se encuentra disponible.",
            "not_found",
        ) from exc


def _valid_candidate_public_ids(values):
    try:
        return [str(UUID(str(value))) for value in values]
    except (TypeError, ValueError, AttributeError):
        return []


def _locked_actor_for_business(*, actor, business, target_public_ids=()):
    normalized_public_ids = _valid_candidate_public_ids(target_public_ids)
    target_identities = list(
        BusinessMembership.objects.filter(
            business=business,
            public_id__in=normalized_public_ids,
        ).values_list("public_id", "pk")
    )
    targets_missing = len(target_identities) != len(set(normalized_public_ids))
    target_ids = {
        str(public_id): membership_id
        for public_id, membership_id in target_identities
    }
    actor_membership_id = _actor_membership_id(
        actor_id=actor.pk,
        business=business,
    )
    memberships = _lock_memberships(
        business=business,
        membership_ids=[
            *target_ids.values(),
            *(
                [actor_membership_id]
                if actor_membership_id is not None
                else []
            ),
        ],
    )
    users = _lock_users(
        memberships=memberships.values(),
        additional_user_ids=[actor.pk],
    )
    targets = {
        public_id: memberships[membership_id]
        for public_id, membership_id in target_ids.items()
    }
    return (
        users[actor.pk],
        memberships.get(actor_membership_id),
        targets,
        users,
        targets_missing,
    )


def _raise_if_targets_missing(targets_missing):
    if targets_missing:
        raise MembershipDomainError(
            "El recurso solicitado no se encuentra disponible.",
            "not_found",
        )


@transaction.atomic
def update_membership_by_public_id(
    *,
    actor,
    membership_public_id,
    validate_changes,
):
    identity = _membership_identity(membership_public_id)
    business, memberships, _, locked_actor, actor_membership = _locked_context(
        actor=actor,
        business_id=identity.business_id,
        target_ids=[identity.pk],
    )
    target = memberships[identity.pk]
    _hide_missing_actor_access(
        _authorize_management_actor,
        locked_actor=locked_actor,
        actor_membership=actor_membership,
    )
    changes = validate_changes(target)
    _validate_management_target(
        locked_actor=locked_actor,
        actor_membership=actor_membership,
        target=target,
        requested_role=changes.get("role"),
    )
    update_fields = []
    for field_name in ("role", "is_active"):
        if field_name in changes and getattr(target, field_name) != changes[field_name]:
            setattr(target, field_name, changes[field_name])
            update_fields.append(field_name)
    if update_fields:
        target.save(update_fields=[*update_fields, "updated_at"])
    _assert_effective_owner(business=business)
    return target


@transaction.atomic
def deactivate_membership_by_public_id(*, actor, membership_public_id):
    identity = _membership_identity(membership_public_id)
    business, memberships, _, locked_actor, actor_membership = _locked_context(
        actor=actor,
        business_id=identity.business_id,
        target_ids=[identity.pk],
    )
    target = memberships[identity.pk]
    _hide_missing_actor_access(
        _authorize_management_actor,
        locked_actor=locked_actor,
        actor_membership=actor_membership,
    )
    _validate_management_target(
        locked_actor=locked_actor,
        actor_membership=actor_membership,
        target=target,
    )
    if target.is_active:
        target.is_active = False
        target.save(update_fields=["is_active", "updated_at"])
    _assert_effective_owner(business=business)
    return target


@transaction.atomic
def promote_to_owner_by_public_id(
    *,
    actor,
    business_public_id,
    candidate_membership_public_id,
    validate_payload,
):
    business = _lock_business_by_public_id(business_public_id)
    (
        locked_actor,
        actor_membership,
        memberships,
        users,
        targets_missing,
    ) = _locked_actor_for_business(
        actor=actor,
        business=business,
        target_public_ids=[candidate_membership_public_id],
    )
    _hide_missing_actor_access(
        _authorize_ownership,
        locked_actor=locked_actor,
        actor_membership=actor_membership,
    )
    _raise_if_targets_missing(targets_missing)
    payload = validate_payload()
    target = memberships[str(payload["membership_public_id"])]
    target_user = users[target.user_id]
    if not target.is_active or not target_user.is_active:
        raise MembershipDomainError(
            "La membership y su User deben estar activos.",
            "invalid",
        )
    if target.user_id == locked_actor.pk and target.role != BusinessMembership.ROLE_OWNER:
        raise MembershipDomainError(
            "No puedes promover tu propia membresía.",
            "forbidden",
        )
    if target.role != BusinessMembership.ROLE_OWNER:
        target.role = BusinessMembership.ROLE_OWNER
        target.save(update_fields=["role", "updated_at"])
    _assert_effective_owner(business=business)
    return target


@transaction.atomic
def transfer_ownership_by_public_id(
    *,
    actor,
    business_public_id,
    candidate_membership_public_ids,
    validate_payload,
):
    business = _lock_business_by_public_id(business_public_id)
    (
        locked_actor,
        actor_membership,
        memberships,
        users,
        targets_missing,
    ) = _locked_actor_for_business(
        actor=actor,
        business=business,
        target_public_ids=candidate_membership_public_ids,
    )
    _hide_missing_actor_access(
        _authorize_ownership,
        locked_actor=locked_actor,
        actor_membership=actor_membership,
    )
    _raise_if_targets_missing(targets_missing)
    payload = validate_payload()
    from_public_id = payload["from_membership_public_id"]
    to_public_id = payload["to_membership_public_id"]
    _validate_non_owner_role(field_name="from_role", value=payload["from_role"])
    if from_public_id == to_public_id:
        raise MembershipDomainError(
            "Las memberships de origen y destino deben ser diferentes.",
            "invalid",
        )
    source = memberships[str(from_public_id)]
    target = memberships[str(to_public_id)]
    if (
        source.role != BusinessMembership.ROLE_OWNER
        or not source.is_active
        or not users[source.user_id].is_active
    ):
        raise MembershipDomainError(
            "La membership de origen ya no es un owner efectivo.",
            "conflict",
        )
    if not target.is_active or not users[target.user_id].is_active:
        raise MembershipDomainError(
            "La membership y el User de destino deben estar activos.",
            "invalid",
        )
    target.role = BusinessMembership.ROLE_OWNER
    target.save(update_fields=["role", "updated_at"])
    source.role = payload["from_role"]
    source.save(update_fields=["role", "updated_at"])
    _assert_effective_owner(business=business)
    return source, target


@transaction.atomic
def remove_owner_by_public_id(
    *,
    actor,
    business_public_id,
    membership_public_id,
    validate_payload,
):
    business = _lock_business_by_public_id(business_public_id)
    (
        locked_actor,
        actor_membership,
        memberships,
        _,
        targets_missing,
    ) = _locked_actor_for_business(
        actor=actor,
        business=business,
        target_public_ids=[membership_public_id],
    )
    _hide_missing_actor_access(
        _authorize_ownership,
        locked_actor=locked_actor,
        actor_membership=actor_membership,
    )
    _raise_if_targets_missing(targets_missing)
    payload = validate_payload()
    _validate_non_owner_role(
        field_name="replacement_role",
        value=payload["replacement_role"],
    )
    target = memberships[str(membership_public_id)]
    if target.user_id == locked_actor.pk:
        raise MembershipDomainError(
            "No puedes retirar tu propio ownership.",
            "forbidden",
        )
    if target.role != BusinessMembership.ROLE_OWNER:
        raise MembershipDomainError(
            "La membership indicada no es owner.",
            "invalid",
        )
    target.role = payload["replacement_role"]
    target.save(update_fields=["role", "updated_at"])
    _assert_effective_owner(business=business)
    return target


@transaction.atomic
def deactivate_membership_in_business_by_public_id(
    *,
    actor,
    business_public_id,
    membership_public_id,
):
    business = _lock_business_by_public_id(business_public_id)
    (
        locked_actor,
        actor_membership,
        memberships,
        _,
        targets_missing,
    ) = _locked_actor_for_business(
        actor=actor,
        business=business,
        target_public_ids=[membership_public_id],
    )
    _hide_missing_actor_access(
        _authorize_management_actor,
        locked_actor=locked_actor,
        actor_membership=actor_membership,
    )
    _raise_if_targets_missing(targets_missing)
    target = memberships[str(membership_public_id)]
    _validate_management_target(
        locked_actor=locked_actor,
        actor_membership=actor_membership,
        target=target,
    )
    if target.is_active:
        target.is_active = False
        target.save(update_fields=["is_active", "updated_at"])
    _assert_effective_owner(business=business)
    return target
