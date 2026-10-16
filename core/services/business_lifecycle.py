from dataclasses import dataclass

from django.db import transaction
from django.db.models import Exists, OuterRef

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
class BusinessLifecycleError(Exception):
    detail: object
    kind: str

    def __str__(self):
        return str(self.detail)


def _preauthorize(*, actor_id, business_public_id, platform_only=False):
    actor_is_platform_admin = User.objects.filter(
        pk=actor_id,
        is_active=True,
        is_superuser=True,
    ).exists()
    candidates = (
        Business.objects.filter(public_id=business_public_id)
        .annotate(
            actor_is_effective_owner=Exists(
                BusinessMembership.objects.filter(
                    business_id=OuterRef("pk"),
                    user_id=actor_id,
                    user__is_active=True,
                    is_active=True,
                    role=BusinessMembership.ROLE_OWNER,
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
        .select_related("status")
        .only("pk", "status__name")
    )
    if not actor_is_platform_admin:
        candidates = candidates.filter(actor_has_active_membership=True)
    try:
        candidate = candidates.get()
    except (Business.DoesNotExist, ValueError) as exc:
        raise BusinessLifecycleError(HIDDEN_RESOURCE, "not_found") from exc

    if actor_is_platform_admin:
        return candidate.pk
    if normalized_status_name(candidate) == DELETED_BUSINESS_STATUS.casefold():
        raise BusinessLifecycleError(HIDDEN_RESOURCE, "not_found")
    if platform_only:
        raise BusinessLifecycleError(
            "Solo un Platform Admin puede archivar un negocio.",
            "forbidden",
        )
    if not candidate.actor_is_effective_owner:
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
    if not actor.is_active:
        raise BusinessLifecycleError(HIDDEN_RESOURCE, "not_found")
    if actor.is_superuser:
        return
    if membership is None or not membership.is_active:
        raise BusinessLifecycleError(HIDDEN_RESOURCE, "not_found")
    if platform_only:
        raise BusinessLifecycleError(
            "Solo un Platform Admin puede archivar un negocio.",
            "forbidden",
        )
    if membership.role != BusinessMembership.ROLE_OWNER:
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
