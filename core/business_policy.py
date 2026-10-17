from enum import Enum

from django.db.models import Q
from rest_framework.exceptions import NotFound


ACTIVE_BUSINESS_STATUS = "Activo"
INACTIVE_BUSINESS_STATUS = "Inactivo"
DELETED_BUSINESS_STATUS = "Eliminado"


class BusinessAccessMode(str, Enum):
    OPERATIONAL = "operational"
    ADMINISTRATIVE_READ_ONLY = "administrative_read_only"
    DENIED = "denied"


def normalized_status_name(status_or_business):
    status = getattr(status_or_business, "status", status_or_business)
    return str(getattr(status, "name", "")).strip().casefold()


def is_business_operational(business):
    return normalized_status_name(business) == ACTIVE_BUSINESS_STATUS.casefold()


def is_current_active_platform_admin(user):
    if not user or not user.is_authenticated or user.pk is None:
        return False
    return user.__class__._default_manager.filter(
        pk=user.pk,
        is_active=True,
        is_superuser=True,
    ).exists()


def approved_business_read_access_mode(*, user, business):
    current = normalized_status_name(business)
    if current == ACTIVE_BUSINESS_STATUS.casefold():
        return BusinessAccessMode.OPERATIONAL
    if (
        current == INACTIVE_BUSINESS_STATUS.casefold()
        and is_current_active_platform_admin(user)
    ):
        return BusinessAccessMode.ADMINISTRATIVE_READ_ONLY
    return BusinessAccessMode.DENIED


def require_approved_business_read_access(*, user, business):
    mode = approved_business_read_access_mode(
        user=user,
        business=business,
    )
    if mode is BusinessAccessMode.DENIED:
        raise NotFound("El recurso solicitado no se encuentra disponible.")
    return mode


def operational_business_q(lookup="business"):
    prefix = f"{lookup}__" if lookup else ""
    return Q(**{f"{prefix}status__name__iexact": ACTIVE_BUSINESS_STATUS})


def approved_business_read_q(*, user, lookup="business"):
    query = operational_business_q(lookup)
    if is_current_active_platform_admin(user):
        prefix = f"{lookup}__" if lookup else ""
        query |= Q(
            **{
                f"{prefix}status__name__iexact": INACTIVE_BUSINESS_STATUS,
            }
        )
    return query


def filter_operational_businesses(queryset, lookup="business"):
    return queryset.filter(operational_business_q(lookup))


def filter_approved_business_reads(queryset, *, user, lookup="business"):
    return queryset.filter(
        approved_business_read_q(user=user, lookup=lookup)
    )


def require_operational_business(business):
    if not is_business_operational(business):
        raise NotFound("El recurso solicitado no se encuentra disponible.")
    return business
