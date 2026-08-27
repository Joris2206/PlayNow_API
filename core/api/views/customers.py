from drf_spectacular.utils import extend_schema, extend_schema_view

from core.api.schemas.examples import CUSTOMER_CREATE_EXAMPLE
from core.api.serializers.customers import CustomerSerializer
from core.api.views.base import BusinessScopedViewSet
from core.mixins import SoftDeleteByStatusMixin
from core.models import BusinessMembership, Customer
from core.pagination import StandardResultsSetPagination


@extend_schema_view(
    list=extend_schema(tags=["Customers"]),
    retrieve=extend_schema(tags=["Customers"]),
    create=extend_schema(
        tags=["Customers"],
        description="Registra un cliente en el negocio indicado.",
        examples=[CUSTOMER_CREATE_EXAMPLE],
    ),
    update=extend_schema(tags=["Customers"]),
    partial_update=extend_schema(tags=["Customers"]),
    destroy=extend_schema(tags=["Customers"]),
)
class CustomerViewSet(SoftDeleteByStatusMixin, BusinessScopedViewSet):
    queryset = Customer.objects.select_related("business", "status").all()
    serializer_class = CustomerSerializer
    lookup_field = "public_id"
    lookup_url_kwarg = "public_id"

    business_lookup = "business"

    read_allowed_roles = [
        BusinessMembership.ROLE_OWNER,
        BusinessMembership.ROLE_ADMIN,
        BusinessMembership.ROLE_CASHIER,
        BusinessMembership.ROLE_SELLER,
        BusinessMembership.ROLE_VIEWER,
    ]

    create_allowed_roles = [
        BusinessMembership.ROLE_OWNER,
        BusinessMembership.ROLE_ADMIN,
        BusinessMembership.ROLE_CASHIER,
        BusinessMembership.ROLE_SELLER,
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
    
    search_fields = ["full_name", "email", "phone"]
    ordering_fields = ["full_name", "created_at", "updated_at"]
    ordering = ["-created_at"]

    pagination_class = StandardResultsSetPagination
