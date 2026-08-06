from datetime import date, datetime, time, timedelta
from decimal import Decimal

from django.db.models import (
    Avg,
    Count,
    Max,
    Sum,
)
from django.utils import timezone as django_timezone

from core.models import Transaction


EXCLUDED_STATUS_NAMES = [
    "Eliminado",
    "Anulado",
    "Cancelado",
    "Void",
    "Deleted",
]


def decimal_or_zero(value) -> Decimal:
    return (
        value
        if value is not None
        else Decimal("0.00")
    ).quantize(
        Decimal("0.01")
    )


def get_report_datetime_range(
    *,
    date_from: date,
    date_to: date,
) -> tuple[datetime, datetime]:
    """
    Convierte un rango de fechas inclusivo en un rango
    de datetimes:

    date_from 00:00:00 <= created_at < día posterior a date_to
    """
    current_timezone = (
        django_timezone.get_current_timezone()
    )

    start_datetime = django_timezone.make_aware(
        datetime.combine(
            date_from,
            time.min,
        ),
        current_timezone,
    )

    end_datetime = django_timezone.make_aware(
        datetime.combine(
            date_to + timedelta(days=1),
            time.min,
        ),
        current_timezone,
    )

    return start_datetime, end_datetime


def build_customers_summary(
    *,
    business,
    date_from: date,
    date_to: date,
    customer=None,
) -> dict:
    start_datetime, end_datetime = (
        get_report_datetime_range(
            date_from=date_from,
            date_to=date_to,
        )
    )

    transactions = (
        Transaction.objects
        .filter(
            business=business,
            type="sale",
            customer__isnull=False,
            created_at__gte=start_datetime,
            created_at__lt=end_datetime,
        )
        .exclude(
            status__name__in=EXCLUDED_STATUS_NAMES
        )
    )

    if customer is not None:
        transactions = transactions.filter(
            customer=customer
        )

    grouped_customers = (
        transactions
        .values(
            "customer__public_id",
            "customer__full_name",
            "customer__phone",
            "customer__email",
        )
        .annotate(
            transactions_count=Count("id"),
            total_amount=Sum("total_value"),
            average_ticket=Avg("total_value"),
            last_transaction_at=Max("created_at"),
        )
        .order_by(
            "-total_amount",
            "customer__full_name",
        )
    )

    results = []

    for row in grouped_customers:
        results.append({
            "customer": {
                "public_id": str(
                    row["customer__public_id"]
                ),
                "full_name": (
                    row["customer__full_name"]
                ),
                "phone": (
                    row["customer__phone"]
                ),
                "email": (
                    row["customer__email"]
                ),
            },
            "transactions_count": (
                row["transactions_count"]
            ),
            "total_amount": str(
                decimal_or_zero(
                    row["total_amount"]
                )
            ),
            "average_ticket": str(
                decimal_or_zero(
                    row["average_ticket"]
                )
            ),
            "last_transaction_at": (
                row["last_transaction_at"]
            ),
        })

    totals = transactions.aggregate(
        customers_count=Count(
            "customer",
            distinct=True,
        ),
        transactions_count=Count("id"),
        total_amount=Sum("total_value"),
        average_ticket=Avg("total_value"),
    )

    return {
        "business": {
            "public_id": str(
                business.public_id
            ),
            "name": business.business_name,
            "currency": business.currency,
        },
        "period": {
            "date_from": date_from.isoformat(),
            "date_to": date_to.isoformat(),
        },
        "totals": {
            "customers_count": (
                totals["customers_count"]
            ),
            "transactions_count": (
                totals["transactions_count"]
            ),
            "total_amount": str(
                decimal_or_zero(
                    totals["total_amount"]
                )
            ),
            "average_ticket": str(
                decimal_or_zero(
                    totals["average_ticket"]
                )
            ),
        },
        "results": results,
    }


def build_suppliers_summary(
    *,
    business,
    date_from: date,
    date_to: date,
    supplier=None,
) -> dict:
    start_datetime, end_datetime = (
        get_report_datetime_range(
            date_from=date_from,
            date_to=date_to,
        )
    )

    transactions = (
        Transaction.objects
        .filter(
            business=business,
            type="purchase",
            supplier__isnull=False,
            created_at__gte=start_datetime,
            created_at__lt=end_datetime,
        )
        .exclude(
            status__name__in=EXCLUDED_STATUS_NAMES
        )
    )

    if supplier is not None:
        transactions = transactions.filter(
            supplier=supplier
        )

    grouped_suppliers = (
        transactions
        .values(
            "supplier__public_id",
            "supplier__name",
            "supplier__phone",
            "supplier__email",
        )
        .annotate(
            transactions_count=Count("id"),
            total_amount=Sum("total_value"),
            average_purchase=Avg("total_value"),
            last_transaction_at=Max("created_at"),
        )
        .order_by(
            "-total_amount",
            "supplier__name",
        )
    )

    results = []

    for row in grouped_suppliers:
        results.append({
            "supplier": {
                "public_id": str(
                    row["supplier__public_id"]
                ),
                "name": (
                    row["supplier__name"]
                ),
                "phone": (
                    row["supplier__phone"]
                ),
                "email": (
                    row["supplier__email"]
                ),
            },
            "transactions_count": (
                row["transactions_count"]
            ),
            "total_amount": str(
                decimal_or_zero(
                    row["total_amount"]
                )
            ),
            "average_purchase": str(
                decimal_or_zero(
                    row["average_purchase"]
                )
            ),
            "last_transaction_at": (
                row["last_transaction_at"]
            ),
        })

    totals = transactions.aggregate(
        suppliers_count=Count(
            "supplier",
            distinct=True,
        ),
        transactions_count=Count("id"),
        total_amount=Sum("total_value"),
        average_purchase=Avg("total_value"),
    )

    return {
        "business": {
            "public_id": str(
                business.public_id
            ),
            "name": business.business_name,
            "currency": business.currency,
        },
        "period": {
            "date_from": date_from.isoformat(),
            "date_to": date_to.isoformat(),
        },
        "totals": {
            "suppliers_count": (
                totals["suppliers_count"]
            ),
            "transactions_count": (
                totals["transactions_count"]
            ),
            "total_amount": str(
                decimal_or_zero(
                    totals["total_amount"]
                )
            ),
            "average_purchase": str(
                decimal_or_zero(
                    totals["average_purchase"]
                )
            ),
        },
        "results": results,
    }