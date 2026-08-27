from django.db.models import Q
from drf_spectacular.utils import extend_schema, extend_schema_view

from core.api.schemas.examples import PAYMENT_METHOD_CREATE_EXAMPLE
from core.api.serializers.payment_methods import PaymentMethodSerializer
from core.api.views.base import BusinessScopedViewSet
from core.mixins import SoftDeleteByStatusMixin
from core.models import BusinessMembership, PaymentMethod
from core.pagination import StandardResultsSetPagination


@extend_schema_view(
    list=extend_schema(tags=["Payment Methods"]),
    retrieve=extend_schema(tags=["Payment Methods"]),
    create=extend_schema(
        tags=["Payment Methods"],
        description="Crea un método de pago disponible para el negocio.",
        examples=[PAYMENT_METHOD_CREATE_EXAMPLE],
    ),
    update=extend_schema(tags=["Payment Methods"]),
    partial_update=extend_schema(tags=["Payment Methods"]),
    destroy=extend_schema(tags=["Payment Methods"]),
)
class PaymentMethodViewSet(SoftDeleteByStatusMixin, BusinessScopedViewSet):
    queryset = PaymentMethod.objects.select_related(
        "business",
        "status",
    ).all()
    serializer_class = PaymentMethodSerializer

    business_lookup = "business"

    read_allowed_roles = [
        BusinessMembership.ROLE_OWNER,
        BusinessMembership.ROLE_ADMIN,
        BusinessMembership.ROLE_CASHIER,
        BusinessMembership.ROLE_SELLER,
        BusinessMembership.ROLE_INVENTORY,
        BusinessMembership.ROLE_VIEWER,
    ]

    create_allowed_roles = [
        BusinessMembership.ROLE_OWNER,
        BusinessMembership.ROLE_ADMIN,
    ]

    update_allowed_roles = create_allowed_roles
    destroy_allowed_roles = create_allowed_roles

    lookup_field = "public_id"
    lookup_url_kwarg = "public_id"
    pagination_class = StandardResultsSetPagination

    public_id_filter_fields = {
        "status_public_id": (
            "status__public_id"
        ),
    }

    search_fields = ["name"]

    administrative_read_roles = [
        BusinessMembership.ROLE_OWNER,
        BusinessMembership.ROLE_ADMIN,
        BusinessMembership.ROLE_VIEWER,
    ]

    operational_read_roles = [
        BusinessMembership.ROLE_CASHIER,
        BusinessMembership.ROLE_SELLER,
        BusinessMembership.ROLE_INVENTORY,
    ]

    def get_queryset(self):
        queryset = super().get_queryset()
        user = self.request.user

        if user.is_superuser:
            return queryset

        administrative_businesses = (
            BusinessMembership.objects
            .filter(
                user=user,
                is_active=True,
                role__in=(
                    self.administrative_read_roles
                ),
            )
            .values("business_id")
        )

        operational_businesses = (
            BusinessMembership.objects
            .filter(
                user=user,
                is_active=True,
                role__in=(
                    self.operational_read_roles
                ),
            )
            .values("business_id")
        )

        return queryset.filter(
            Q(
                business_id__in=(
                    administrative_businesses
                ),
            )
            | Q(
                business_id__in=(
                    operational_businesses
                ),
                status__name__iexact="Activo",
            )
        ).distinct()
