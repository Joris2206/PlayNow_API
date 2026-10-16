from django.db.models import Q
from rest_framework.exceptions import NotFound


ACTIVE_BUSINESS_STATUS = "Activo"
INACTIVE_BUSINESS_STATUS = "Inactivo"
DELETED_BUSINESS_STATUS = "Eliminado"


def normalized_status_name(status_or_business):
    status = getattr(status_or_business, "status", status_or_business)
    return str(getattr(status, "name", "")).strip().casefold()


def is_business_operational(business):
    return normalized_status_name(business) == ACTIVE_BUSINESS_STATUS.casefold()


def operational_business_q(lookup="business"):
    prefix = f"{lookup}__" if lookup else ""
    return Q(**{f"{prefix}status__name__iexact": ACTIVE_BUSINESS_STATUS})


def filter_operational_businesses(queryset, lookup="business"):
    return queryset.filter(operational_business_q(lookup))


def require_operational_business(business):
    if not is_business_operational(business):
        raise NotFound("El recurso solicitado no se encuentra disponible.")
    return business
