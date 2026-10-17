from rest_framework.exceptions import PermissionDenied

from core.models import BusinessMembership
from core.business_policy import (
    BusinessAccessMode,
    is_current_active_platform_admin,
    require_approved_business_read_access,
)

def validate_report_business_access(
    *,
    user,
    business,
    allowed_roles=None,
):
    access_mode = require_approved_business_read_access(
        user=user,
        business=business,
    )

    if access_mode is BusinessAccessMode.ADMINISTRATIVE_READ_ONLY:
        return

    if is_current_active_platform_admin(user):
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
