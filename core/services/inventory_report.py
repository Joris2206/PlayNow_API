from datetime import date
from django.db.models import (
    Count,
    Q,
    Sum,
)
from django.db.models.functions import Coalesce

from core.models import Product, StockMovement
from core.services.customer_supplier_reports import (
    get_report_datetime_range,
)


def integer_or_zero(value) -> int:
    return int(value or 0)


def build_inventory_summary(
    *,
    business,
    date_from: date,
    date_to: date,
    product=None,
) -> dict:
    """
    Construye el reporte histórico de inventario.

    Reglas:

    - Cada fila representa un Product individual.
    - El stock histórico se reconstruye usando StockMovement.
    """
    start_datetime, end_datetime = (
        get_report_datetime_range(
            date_from=date_from,
            date_to=date_to,
        )
    )

    product_queryset = (
        Product.objects
        .filter(
            business=business,
        )
        .select_related(
            "status",
        )
        .order_by(
            "title",
        )
    )

    if product is not None:
        product_queryset = (
            product_queryset.filter(
                pk=product.pk,
            )
        )

    movements = (
        StockMovement.objects
        .filter(
            product__business=business,
        )
    )

    if product is not None:
        movements = movements.filter(
            product=product,
        )

    period_movements = movements.filter(
        created_at__gte=start_datetime,
        created_at__lt=end_datetime,
    )

    movements_after_period = movements.filter(
        created_at__gte=end_datetime,
    )

    period_grouped = (
        period_movements
        .values(
            "product_id",
        )
        .annotate(
            movements_count=Count("id"),
            entries=Coalesce(
                Sum(
                    "quantity",
                    filter=Q(
                        type="entry",
                        quantity__gt=0,
                    ),
                ),
                0,
            ),
            sales_signed=Coalesce(
                Sum(
                    "quantity",
                    filter=Q(
                        type="sale",
                    ),
                ),
                0,
            ),
            positive_adjustments=Coalesce(
                Sum(
                    "quantity",
                    filter=Q(
                        type="adjustment",
                        quantity__gt=0,
                    ),
                ),
                0,
            ),
            negative_adjustments_signed=Coalesce(
                Sum(
                    "quantity",
                    filter=Q(
                        type="adjustment",
                        quantity__lt=0,
                    ),
                ),
                0,
            ),
            net_movement=Coalesce(
                Sum("quantity"),
                0,
            ),
        )
    )

    after_grouped = (
        movements_after_period
        .values(
            "product_id",
        )
        .annotate(
            net_after_period=Coalesce(
                Sum("quantity"),
                0,
            )
        )
    )

    period_map = {
        (
            row["product_id"],
        ): row
        for row in period_grouped
    }

    after_map = {
        (
            row["product_id"],
        ): integer_or_zero(
            row["net_after_period"]
        )
        for row in after_grouped
    }

    results = []

    total_opening_stock = 0
    total_entries = 0
    total_sales = 0
    total_positive_adjustments = 0
    total_negative_adjustments = 0
    total_net_movement = 0
    total_closing_stock = 0
    total_current_stock = 0
    total_movements_count = 0

    def append_result(
        *,
        current_product,
        current_stock,
    ):
        nonlocal total_opening_stock
        nonlocal total_entries
        nonlocal total_sales
        nonlocal total_positive_adjustments
        nonlocal total_negative_adjustments
        nonlocal total_net_movement
        nonlocal total_closing_stock
        nonlocal total_current_stock
        nonlocal total_movements_count

        key = (current_product.pk,)

        period_row = period_map.get(
            key,
            {},
        )

        entries = integer_or_zero(
            period_row.get("entries")
        )

        sales = abs(
            integer_or_zero(
                period_row.get(
                    "sales_signed"
                )
            )
        )

        positive_adjustments = (
            integer_or_zero(
                period_row.get(
                    "positive_adjustments"
                )
            )
        )

        negative_adjustments = abs(
            integer_or_zero(
                period_row.get(
                    "negative_adjustments_signed"
                )
            )
        )

        net_movement = integer_or_zero(
            period_row.get(
                "net_movement"
            )
        )

        movements_count = integer_or_zero(
            period_row.get(
                "movements_count"
            )
        )

        net_after_period = (
            after_map.get(
                key,
                0,
            )
        )

        closing_stock = (
            current_stock
            - net_after_period
        )

        opening_stock = (
            closing_stock
            - net_movement
        )

        result = {
            "product": {
                "public_id": str(
                    current_product.public_id
                ),
                "title": (
                    current_product.title
                ),
            },
            "opening_stock": opening_stock,
            "entries": entries,
            "sales": sales,
            "positive_adjustments": (
                positive_adjustments
            ),
            "negative_adjustments": (
                negative_adjustments
            ),
            "net_movement": net_movement,
            "closing_stock": closing_stock,
            "current_stock": current_stock,
            "movements_count": (
                movements_count
            ),
        }

        results.append(result)

        total_opening_stock += opening_stock
        total_entries += entries
        total_sales += sales
        total_positive_adjustments += (
            positive_adjustments
        )
        total_negative_adjustments += (
            negative_adjustments
        )
        total_net_movement += net_movement
        total_closing_stock += closing_stock
        total_current_stock += current_stock
        total_movements_count += (
            movements_count
        )

    for current_product in product_queryset:
        append_result(
            current_product=current_product,
            current_stock=(
                current_product.stock
            ),
        )

    return {
        "business": {
            "public_id": str(
                business.public_id
            ),
            "name": (
                business.business_name
            ),
            "currency": business.currency,
        },
        "period": {
            "date_from": (
                date_from.isoformat()
            ),
            "date_to": (
                date_to.isoformat()
            ),
        },
        "totals": {
            "items_count": len(results),
            "opening_stock": (
                total_opening_stock
            ),
            "entries": total_entries,
            "sales": total_sales,
            "positive_adjustments": (
                total_positive_adjustments
            ),
            "negative_adjustments": (
                total_negative_adjustments
            ),
            "net_movement": (
                total_net_movement
            ),
            "closing_stock": (
                total_closing_stock
            ),
            "current_stock": (
                total_current_stock
            ),
            "movements_count": (
                total_movements_count
            ),
        },
        "results": results,
    }
