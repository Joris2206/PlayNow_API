# core/filters.py
from django_filters import rest_framework as filters
from .models import StockMovement, Transaction

class StockMovementFilter(filters.FilterSet):
    # rango de fechas: ?created_at_after=...&created_at_before=...
    created_at = filters.IsoDateTimeFromToRangeFilter(field_name="created_at")

    # negocio vía PRODUCTO (universal; incluye ajustes sin transacción)
    business_public_id = filters.CharFilter(
        field_name="product__business__public_id", lookup_expr="iexact"
    )

    # negocio vía TRANSACCIÓN (solo cuando hay transacción)
    transaction_business_public_id = filters.CharFilter(
        field_name="transaction__business__public_id", lookup_expr="iexact"
    )

    # ids públicos relacionados
    product_public_id = filters.CharFilter(field_name="product__public_id", lookup_expr="iexact")
    variant_public_id = filters.CharFilter(field_name="variant__public_id", lookup_expr="iexact")
    transaction_public_id = filters.CharFilter(field_name="transaction__public_id", lookup_expr="iexact")

    # tipo de movimiento ('entry', 'sale', 'adjustment')
    type = filters.CharFilter(field_name="type", lookup_expr="iexact")

    class Meta:
        model = StockMovement
        # SOLO nombres de campos que EXISTEN en StockMovement
        fields = ["type", "transaction", "product", "variant", "created_at"]

class TransactionFilter(filters.FilterSet):
    business_public_id = filters.UUIDFilter(
        field_name="business__public_id",
    )

    customer_public_id = filters.UUIDFilter(
        field_name="customer__public_id",
    )

    supplier_public_id = filters.UUIDFilter(
        field_name="supplier__public_id",
    )

    employee_public_id = filters.UUIDFilter(
        field_name="employee__public_id",
    )

    payment_method_public_id = filters.UUIDFilter(
        field_name="payment_method__public_id",
    )

    status_public_id = filters.UUIDFilter(
        field_name="status__public_id",
    )

    date_from = filters.DateFilter(
        field_name="created_at",
        lookup_expr="date__gte",
    )

    date_to = filters.DateFilter(
        field_name="created_at",
        lookup_expr="date__lte",
    )

    class Meta:
        model = Transaction

        fields = (
            "type",
            "payment_status",
            "is_debt",
        )


class StockMovementFilter(filters.FilterSet):
    created_at = filters.IsoDateTimeFromToRangeFilter(
        field_name="created_at"
    )

    business_public_id = filters.CharFilter(
        field_name="product__business__public_id",
        lookup_expr="iexact",
    )

    transaction_business_public_id = filters.CharFilter(
        field_name="transaction__business__public_id",
        lookup_expr="iexact",
    )

    product_public_id = filters.CharFilter(
        field_name="product__public_id",
        lookup_expr="iexact",
    )

    variant_public_id = filters.CharFilter(
        field_name="variant__public_id",
        lookup_expr="iexact",
    )

    transaction_public_id = filters.CharFilter(
        field_name="transaction__public_id",
        lookup_expr="iexact",
    )

    type = filters.CharFilter(
        field_name="type",
        lookup_expr="iexact",
    )

    class Meta:
        model = StockMovement
        fields = (
            "type",
            "transaction",
            "product",
            "variant",
            "created_at",
        )