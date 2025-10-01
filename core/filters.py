# core/filters.py
from django_filters import rest_framework as filters
from .models import StockMovement

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
