from uuid import UUID

from drf_spectacular.utils import (
    PolymorphicProxySerializer,
    extend_schema,
    extend_schema_view,
)

from core.api.schemas.examples import EMPLOYEE_CREATE_EXAMPLE
from core.api.serializers.employees import (
    EmployeeSelectionSerializer,
    EmployeeSerializer,
)
from core.api.views.base import BusinessScopedViewSet
from core.mixins import SoftDeleteByStatusMixin
from core.models import Business, BusinessMembership, Employee
from core.pagination import StandardResultsSetPagination


@extend_schema_view(
    list=extend_schema(
        tags=["Employees"],
        description=(
            "Owner y admin reciben el contrato administrativo completo. "
            "Cashier y seller reciben exclusivamente public_id, full_name "
            "y position para seleccionar al responsable de una venta."
        ),
        responses=PolymorphicProxySerializer(
            component_name="EmployeeRead",
            serializers=[
                EmployeeSerializer,
                EmployeeSelectionSerializer,
            ],
            resource_type_field_name=None,
            many=True,
        ),
    ),
    retrieve=extend_schema(
        tags=["Employees"],
        description=(
            "Owner y admin reciben el contrato administrativo completo. "
            "Cashier y seller reciben exclusivamente public_id, full_name "
            "y position."
        ),
        responses=PolymorphicProxySerializer(
            component_name="EmployeeRead",
            serializers=[
                EmployeeSerializer,
                EmployeeSelectionSerializer,
            ],
            resource_type_field_name=None,
        ),
    ),
    create=extend_schema(
        tags=["Employees"],
        description="Registra un empleado. Este endpoint no crea automáticamente acceso al sistema.",
        examples=[EMPLOYEE_CREATE_EXAMPLE],
    ),
    update=extend_schema(tags=["Employees"]),
    partial_update=extend_schema(tags=["Employees"]),
    destroy=extend_schema(tags=["Employees"]),
)
class EmployeeViewSet(SoftDeleteByStatusMixin, BusinessScopedViewSet):
    queryset = Employee.objects.select_related("business", "status").all()
    serializer_class = EmployeeSerializer
    lookup_field = "public_id"
    lookup_url_kwarg = "public_id"

    business_lookup = "business"

    read_allowed_roles = [
        BusinessMembership.ROLE_OWNER,
        BusinessMembership.ROLE_ADMIN,
        BusinessMembership.ROLE_CASHIER,
        BusinessMembership.ROLE_SELLER,
    ]

    create_allowed_roles = [
        BusinessMembership.ROLE_OWNER,
        BusinessMembership.ROLE_ADMIN,
    ]
    update_allowed_roles = create_allowed_roles
    destroy_allowed_roles = create_allowed_roles

    public_id_filter_fields = {
        "status_public_id": (
            "status__public_id"
        ),
    }
    
    search_fields = ["full_name", "email", "phone"]
    ordering_fields = ["full_name", "created_at", "updated_at"]
    ordering = ["-created_at"]

    pagination_class = StandardResultsSetPagination

    def _request_membership_role(self):
        user = self.request.user

        if self._is_platform_admin(user):
            return BusinessMembership.ROLE_OWNER

        business_id = None

        if self.action == "list":
            business_public_id = self.request.query_params.get(
                "business_public_id"
            )
            try:
                business_public_id = UUID(str(business_public_id))
            except (TypeError, ValueError):
                return None

            business_id = (
                Business.objects
                .filter(public_id=business_public_id)
                .values_list("pk", flat=True)
                .first()
            )

        elif self.action == "retrieve":
            employee_public_id = self.kwargs.get("public_id")
            try:
                employee_public_id = UUID(str(employee_public_id))
            except (TypeError, ValueError):
                return None

            business_id = (
                Employee.objects
                .filter(public_id=employee_public_id)
                .values_list("business_id", flat=True)
                .first()
            )

        if business_id is None:
            return None

        return (
            BusinessMembership.objects
            .filter(
                user=user,
                business_id=business_id,
                is_active=True,
            )
            .values_list("role", flat=True)
            .first()
        )

    def get_serializer_class(self):
        if self.action in {"list", "retrieve"}:
            role = self._request_membership_role()
            if role in {
                BusinessMembership.ROLE_CASHIER,
                BusinessMembership.ROLE_SELLER,
            }:
                return EmployeeSelectionSerializer

        return EmployeeSerializer

    def get_search_fields(self):
        role = self._request_membership_role()

        if role in {
            BusinessMembership.ROLE_CASHIER,
            BusinessMembership.ROLE_SELLER,
        }:
            return ["full_name", "position"]

        return self.search_fields
