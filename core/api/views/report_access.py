from rest_framework.exceptions import PermissionDenied

from core.models import BusinessMembership
from core.business_policy import require_operational_business

def validate_report_business_access(
    *,
    user,
    business,
    allowed_roles=None,
):
    require_operational_business(business)

    if user.is_superuser:
        return

    if allowed_roles is None:
        allowed_roles = [
            BusinessMembership.ROLE_OWNER,
            BusinessMembership.ROLE_ADMIN,
        ]

    has_access = (
        BusinessMembership.objects
        .filter(
            user=user,
            business=business,
            is_active=True,
            role__in=allowed_roles,
        )
        .exists()
    )

    if not has_access:
        raise PermissionDenied(
            "No tienes permisos para consultar este reporte."
        )
