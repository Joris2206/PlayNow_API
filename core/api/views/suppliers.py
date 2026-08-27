from drf_spectacular.utils import extend_schema, extend_schema_view

from core.api.schemas.examples import SUPPLIER_CREATE_EXAMPLE
from core.api.serializers.suppliers import SupplierSerializer
from core.api.views.base import BusinessScopedViewSet
from core.mixins import SoftDeleteByStatusMixin
from core.models import BusinessMembership, Supplier
from core.pagination import StandardResultsSetPagination


@extend_schema_view(
    list=extend_schema(tags=["Suppliers"]),
    retrieve=extend_schema(tags=["Suppliers"]),
    create=extend_schema(
        tags=["Suppliers"],
        description="Registra un proveedor en el negocio indicado.",
        examples=[SUPPLIER_CREATE_EXAMPLE],
    ),
    update=extend_schema(tags=["Suppliers"]),
    partial_update=extend_schema(tags=["Suppliers"]),
    destroy=extend_schema(tags=["Suppliers"]),
)
class SupplierViewSet(SoftDeleteByStatusMixin, BusinessScopedViewSet):
    queryset = Supplier.objects.select_related("business", "status").all()
    serializer_class = SupplierSerializer
    lookup_field = "public_id"
    lookup_url_kwarg = "public_id"

    business_lookup = "business"

    read_allowed_roles = [
        BusinessMembership.ROLE_OWNER,
        BusinessMembership.ROLE_ADMIN,
        BusinessMembership.ROLE_INVENTORY,
        BusinessMembership.ROLE_VIEWER,
    ]

    create_allowed_roles = [
        BusinessMembership.ROLE_OWNER,
        BusinessMembership.ROLE_ADMIN,
        BusinessMembership.ROLE_INVENTORY,
    ]

    update_allowed_roles = create_allowed_roles

    destroy_allowed_roles = [
        BusinessMembership.ROLE_OWNER,
        BusinessMembership.ROLE_ADMIN,
    ]

    public_id_filter_fields = {
        "status_public_id": (
            "status__public_id"
        ),
    }
    
    search_fields = ["name", "email", "phone"]
    ordering_fields = ["name", "created_at", "updated_at"]
    ordering = ["-created_at"]

    pagination_class = StandardResultsSetPagination
