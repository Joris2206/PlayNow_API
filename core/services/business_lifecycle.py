from dataclasses import dataclass

from django.db import transaction
from django.db.models import Exists, IntegerField, OuterRef, Q, Value

from core.business_policy import (
    ACTIVE_BUSINESS_STATUS,
    DELETED_BUSINESS_STATUS,
    INACTIVE_BUSINESS_STATUS,
    normalized_status_name,
)
from core.models import Business, BusinessMembership, EntityStatus, User
from core.utils import log_action


HIDDEN_RESOURCE = "El recurso solicitado no se encuentra disponible."


@dataclass(frozen=True)
class BusinessActorAuthority:
    actor_is_active: bool
    actor_is_platform_admin: bool
    actor_has_active_membership: bool
    actor_is_effective_owner: bool
    actor_is_effective_admin: bool


def annotate_business_actor_authority(queryset, *, actor_id):
    active_actor = User.objects.filter(pk=actor_id, is_active=True)
    active_memberships = BusinessMembership.objects.filter(
        business_id=OuterRef("pk"),
        user_id=actor_id,
        user__is_active=True,
        is_active=True,
    )
    return queryset.annotate(
        capability_actor_id=Value(actor_id, output_field=IntegerField()),
        actor_is_active=Exists(active_actor),
        actor_is_active_platform_admin=Exists(
            active_actor.filter(is_superuser=True)
        ),
        actor_has_active_membership=Exists(active_memberships),
        actor_is_effective_owner=Exists(
            active_memberships.filter(role=BusinessMembership.ROLE_OWNER)
        ),
        actor_is_effective_admin=Exists(
            active_memberships.filter(role=BusinessMembership.ROLE_ADMIN)
        ),
    )


def _annotated_authority(*, actor_id, business):
    if getattr(business, "capability_actor_id", None) != actor_id:
        raise RuntimeError("Business actor authority was not annotated.")
    return BusinessActorAuthority(
        actor_is_active=business.actor_is_active,
        actor_is_platform_admin=business.actor_is_active_platform_admin,
        actor_has_active_membership=business.actor_has_active_membership,
        actor_is_effective_owner=business.actor_is_effective_owner,
        actor_is_effective_admin=business.actor_is_effective_admin,
    )


def _locked_authority(*, actor, membership):
    actor_is_active = bool(actor.is_active)
    membership_is_effective = bool(
        actor_is_active
        and membership is not None
        and membership.is_active
    )
    return BusinessActorAuthority(
        actor_is_active=actor_is_active,
        actor_is_platform_admin=bool(
            actor_is_active and actor.is_superuser
        ),
        actor_has_active_membership=membership_is_effective,
        actor_is_effective_owner=bool(
            membership_is_effective
            and membership.role == BusinessMembership.ROLE_OWNER
        ),
        actor_is_effective_admin=bool(
            membership_is_effective
            and membership.role == BusinessMembership.ROLE_ADMIN
        ),
    )


def _can_change_lifecycle(authority):
    return (
        authority.actor_is_platform_admin
        or authority.actor_is_effective_owner
    )


def _can_edit_business(*, business, authority):
    current = normalized_status_name(business)
    if current == DELETED_BUSINESS_STATUS.casefold():
        return False
    if authority.actor_is_platform_admin:
        return True
    if current == ACTIVE_BUSINESS_STATUS.casefold():
        return (
            authority.actor_is_effective_owner
            or authority.actor_is_effective_admin
        )
    if current == INACTIVE_BUSINESS_STATUS.casefold():
        return authority.actor_is_effective_owner
    return False


def business_administration_capabilities(*, actor, business):
    authority = _annotated_authority(actor_id=actor.pk, business=business)
    current = normalized_status_name(business)
    can_change_lifecycle = _can_change_lifecycle(authority)
    is_deleted = current == DELETED_BUSINESS_STATUS.casefold()
    return {
        "can_edit": _can_edit_business(
            business=business,
            authority=authority,
        ),
        "can_suspend": bool(
            can_change_lifecycle
            and current == ACTIVE_BUSINESS_STATUS.casefold()
        ),
        "can_reactivate": bool(
            can_change_lifecycle
            and current == INACTIVE_BUSINESS_STATUS.casefold()
        ),
        "can_archive": bool(
            authority.actor_is_platform_admin and not is_deleted
        ),
    }


def can_access_business_administration(*, actor_id):
    qualifying_memberships = BusinessMembership.objects.filter(
        user_id=OuterRef("pk"),
        user__is_active=True,
        is_active=True,
    ).filter(
        Q(
            role=BusinessMembership.ROLE_OWNER,
            business__status__name__iexact=ACTIVE_BUSINESS_STATUS,
        )
        | Q(
            role=BusinessMembership.ROLE_OWNER,
            business__status__name__iexact=INACTIVE_BUSINESS_STATUS,
        )
        | Q(
            role=BusinessMembership.ROLE_ADMIN,
            business__status__name__iexact=ACTIVE_BUSINESS_STATUS,
        )
    )
    actor = (
        User.objects.filter(pk=actor_id, is_active=True)
        .annotate(
            has_business_administration_membership=Exists(
                qualifying_memberships
            )
        )
        .values(
            "is_superuser",
            "has_business_administration_membership",
        )
        .first()
    )
    return bool(
        actor
        and (
            actor["is_superuser"]
            or actor["has_business_administration_membership"]
        )
    )


@dataclass(frozen=True)
class BusinessLifecycleError(Exception):
    detail: object
    kind: str

    def __str__(self):
        return str(self.detail)


def _preauthorize(*, actor_id, business_public_id, platform_only=False):
    candidates = annotate_business_actor_authority(
        Business.objects.filter(public_id=business_public_id),
        actor_id=actor_id,
    ).filter(
        Q(actor_is_active_platform_admin=True)
        | Q(actor_has_active_membership=True)
    )
    candidates = (
        candidates
        .select_related("status")
        .only("pk", "status__name")
    )
    try:
        candidate = candidates.get()
    except (Business.DoesNotExist, ValueError) as exc:
        raise BusinessLifecycleError(HIDDEN_RESOURCE, "not_found") from exc

    authority = _annotated_authority(
        actor_id=actor_id,
        business=candidate,
    )
    if authority.actor_is_platform_admin:
        return candidate.pk
    if normalized_status_name(candidate) == DELETED_BUSINESS_STATUS.casefold():
        raise BusinessLifecycleError(HIDDEN_RESOURCE, "not_found")
    if platform_only:
        raise BusinessLifecycleError(
            "Solo un Platform Admin puede archivar un negocio.",
            "forbidden",
        )
    if not authority.actor_is_effective_owner:
        raise BusinessLifecycleError(
            "Solo un owner efectivo o Platform Admin puede cambiar el estado del negocio.",
            "forbidden",
        )
    return candidate.pk


def _lock_context(*, actor_id, business_id):
    try:
        business = (
            Business.objects.select_for_update()
            .select_related("status")
            .get(pk=business_id)
        )
    except Business.DoesNotExist as exc:
        raise BusinessLifecycleError(HIDDEN_RESOURCE, "not_found") from exc

    membership_id = (
        BusinessMembership.objects.filter(
            business=business,
            user_id=actor_id,
        )
        .values_list("pk", flat=True)
        .first()
    )
    membership = None
    if membership_id is not None:
        membership = (
            BusinessMembership.objects.select_for_update()
            .filter(pk=membership_id, business=business)
            .first()
        )
    try:
        actor = User.objects.select_for_update().get(pk=actor_id)
    except User.DoesNotExist as exc:
        raise BusinessLifecycleError(HIDDEN_RESOURCE, "not_found") from exc
    return business, membership, actor


def _reauthorize(*, actor, membership, platform_only=False):
    authority = _locked_authority(actor=actor, membership=membership)
    if not authority.actor_is_active:
        raise BusinessLifecycleError(HIDDEN_RESOURCE, "not_found")
    if authority.actor_is_platform_admin:
        return
    if not authority.actor_has_active_membership:
        raise BusinessLifecycleError(HIDDEN_RESOURCE, "not_found")
    if platform_only:
        raise BusinessLifecycleError(
            "Solo un Platform Admin puede archivar un negocio.",
            "forbidden",
        )
    if not _can_change_lifecycle(authority):
        raise BusinessLifecycleError(
            "Solo un owner efectivo o Platform Admin puede cambiar el estado del negocio.",
            "forbidden",
        )


def _configured_status(name):
    matches = list(
        EntityStatus.objects.filter(name__iexact=name).order_by("pk")[:2]
    )
    if len(matches) != 1:
        raise BusinessLifecycleError(
            f"No existe una configuracion unica para el estado '{name}'.",
            "conflict",
        )
    return matches[0]


def _transition(*, actor_id, business_id, action):
    business, membership, actor = _lock_context(
        actor_id=actor_id,
        business_id=business_id,
    )
    _reauthorize(actor=actor, membership=membership)
    current = normalized_status_name(business)
    active = ACTIVE_BUSINESS_STATUS.casefold()
    inactive = INACTIVE_BUSINESS_STATUS.casefold()
    deleted = DELETED_BUSINESS_STATUS.casefold()

    if current == deleted:
        raise BusinessLifecycleError(
            "Un negocio archivado no puede cambiar de estado.",
            "conflict",
        )

    if action == "suspend":
        if current == inactive:
            return business
        if current != active:
            raise BusinessLifecycleError("Transicion de estado invalida.", "conflict")
        target = _configured_status(INACTIVE_BUSINESS_STATUS)
        audit_action = "SUSPEND_BUSINESS"
    else:
        if current == active:
            return business
        if current != inactive:
            raise BusinessLifecycleError("Transicion de estado invalida.", "conflict")
        target = _configured_status(ACTIVE_BUSINESS_STATUS)
        audit_action = "REACTIVATE_BUSINESS"

    business.status = target
    business.save(update_fields=["status", "updated_at"])
    log_action(actor, audit_action, business.__class__.__name__, business.pk)
    return business


@transaction.atomic
def suspend_business(*, actor_id, business_public_id):
    business_id = _preauthorize(
        actor_id=actor_id,
        business_public_id=business_public_id,
    )
    return _transition(actor_id=actor_id, business_id=business_id, action="suspend")


@transaction.atomic
def reactivate_business(*, actor_id, business_public_id):
    business_id = _preauthorize(
        actor_id=actor_id,
        business_public_id=business_public_id,
    )
    return _transition(actor_id=actor_id, business_id=business_id, action="reactivate")


@transaction.atomic
def archive_business(*, actor_id, business_public_id):
    business_id = _preauthorize(
        actor_id=actor_id,
        business_public_id=business_public_id,
        platform_only=True,
    )
    business, membership, actor = _lock_context(
        actor_id=actor_id,
        business_id=business_id,
    )
    _reauthorize(actor=actor, membership=membership, platform_only=True)
    if normalized_status_name(business) == DELETED_BUSINESS_STATUS.casefold():
        raise BusinessLifecycleError("El negocio ya esta archivado.", "conflict")
    deleted = _configured_status(DELETED_BUSINESS_STATUS)
    business.status = deleted
    business.save(update_fields=["status", "updated_at"])
    log_action(actor, "ARCHIVE_BUSINESS", business.__class__.__name__, business.pk)
    return business
