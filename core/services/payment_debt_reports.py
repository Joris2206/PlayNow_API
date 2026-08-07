from datetime import date
from decimal import Decimal

from django.db.models import Count, Sum

from core.models import (
    Debt,
    DebtPayment,
    PaymentMethod,
    Transaction,
)
from core.services.customer_supplier_reports import (
    EXCLUDED_STATUS_NAMES,
    decimal_or_zero,
    get_report_datetime_range,
)


def build_payments_summary(
    *,
    business,
    date_from: date,
    date_to: date,
    payment_method=None,
) -> dict:
    """
    Resume entradas y salidas por métodos de pago.

    Entradas:
    - ventas pagadas directamente;
    - abonos de deudas.

    Salidas:
    - compras;
    - gastos.

    No mezcla ventas pendientes con dinero realmente recibido.
    """
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
            created_at__gte=start_datetime,
            created_at__lt=end_datetime,
        )
        .exclude(
            status__name__in=EXCLUDED_STATUS_NAMES
        )
    )

    if payment_method is not None:
        transactions = transactions.filter(
            payment_method=payment_method
        )

    paid_sales = transactions.filter(
        type="sale",
        payment_status="paid",
        payment_method__isnull=False,
    )

    purchases = transactions.filter(
        type="purchase",
        payment_method__isnull=False,
    )

    expenses = transactions.filter(
        type="expense",
        payment_method__isnull=False,
    )

    debt_payments = (
        DebtPayment.objects
        .filter(
            debt__transaction__business=business,
            payment_date__gte=date_from,
            payment_date__lte=date_to,
        )
        .exclude(
            debt__transaction__status__name__in=(
                EXCLUDED_STATUS_NAMES
            )
        )
    )

    if payment_method is not None:
        debt_payments = debt_payments.filter(
            payment_method=payment_method
        )

    sales_by_method = {
        row["payment_method_id"]: row
        for row in (
            paid_sales
            .values(
                "payment_method_id",
                "payment_method__public_id",
                "payment_method__name",
                "payment_method__method_type",
            )
            .annotate(
                sales_count=Count("id"),
                sales_total=Sum("total_value"),
            )
        )
    }

    purchases_by_method = {
        row["payment_method_id"]: row
        for row in (
            purchases
            .values("payment_method_id")
            .annotate(
                purchases_count=Count("id"),
                purchases_total=Sum(
                    "total_value"
                ),
            )
        )
    }

    expenses_by_method = {
        row["payment_method_id"]: row
        for row in (
            expenses
            .values("payment_method_id")
            .annotate(
                expenses_count=Count("id"),
                expenses_total=Sum(
                    "total_value"
                ),
            )
        )
    }

    debt_by_method = {
        row["payment_method_id"]: row
        for row in (
            debt_payments
            .values("payment_method_id")
            .annotate(
                debt_payments_count=Count("id"),
                debt_payments_total=Sum("amount"),
            )
        )
    }

    method_ids = (
        set(sales_by_method)
        | set(purchases_by_method)
        | set(expenses_by_method)
        | set(debt_by_method)
    )

    methods = (
        PaymentMethod.objects
        .filter(
            business=business,
            id__in=method_ids,
        )
        .order_by("name")
    )

    results = []

    for method in methods:
        sales_row = sales_by_method.get(
            method.id,
            {},
        )

        purchases_row = purchases_by_method.get(
            method.id,
            {},
        )

        expenses_row = expenses_by_method.get(
            method.id,
            {},
        )

        debt_row = debt_by_method.get(
            method.id,
            {},
        )

        sales_total = decimal_or_zero(
            sales_row.get("sales_total")
        )

        debt_total = decimal_or_zero(
            debt_row.get(
                "debt_payments_total"
            )
        )

        purchases_total = decimal_or_zero(
            purchases_row.get(
                "purchases_total"
            )
        )

        expenses_total = decimal_or_zero(
            expenses_row.get(
                "expenses_total"
            )
        )

        total_incoming = (
            sales_total
            + debt_total
        ).quantize(
            Decimal("0.01")
        )

        total_outgoing = (
            purchases_total
            + expenses_total
        ).quantize(
            Decimal("0.01")
        )

        net_amount = (
            total_incoming
            - total_outgoing
        ).quantize(
            Decimal("0.01")
        )

        results.append({
            "payment_method": {
                "public_id": str(
                    method.public_id
                ),
                "name": method.name,
                "method_type": (
                    method.method_type
                ),
            },
            "sales": {
                "count": sales_row.get(
                    "sales_count",
                    0,
                ),
                "total": str(sales_total),
            },
            "debt_payments": {
                "count": debt_row.get(
                    "debt_payments_count",
                    0,
                ),
                "total": str(debt_total),
            },
            "purchases": {
                "count": purchases_row.get(
                    "purchases_count",
                    0,
                ),
                "total": str(
                    purchases_total
                ),
            },
            "expenses": {
                "count": expenses_row.get(
                    "expenses_count",
                    0,
                ),
                "total": str(
                    expenses_total
                ),
            },
            "total_incoming": str(
                total_incoming
            ),
            "total_outgoing": str(
                total_outgoing
            ),
            "net_amount": str(net_amount),
        })

    paid_sales_summary = paid_sales.aggregate(
        count=Count("id"),
        total=Sum("total_value"),
    )

    debt_payments_summary = (
        debt_payments.aggregate(
            count=Count("id"),
            total=Sum("amount"),
        )
    )

    purchases_summary = purchases.aggregate(
        count=Count("id"),
        total=Sum("total_value"),
    )

    expenses_summary = expenses.aggregate(
        count=Count("id"),
        total=Sum("total_value"),
    )

    sales_total = decimal_or_zero(
        paid_sales_summary["total"]
    )

    debt_total = decimal_or_zero(
        debt_payments_summary["total"]
    )

    purchases_total = decimal_or_zero(
        purchases_summary["total"]
    )

    expenses_total = decimal_or_zero(
        expenses_summary["total"]
    )

    incoming_total = (
        sales_total
        + debt_total
    ).quantize(
        Decimal("0.01")
    )

    outgoing_total = (
        purchases_total
        + expenses_total
    ).quantize(
        Decimal("0.01")
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
            "sales": {
                "count": (
                    paid_sales_summary["count"]
                ),
                "total": str(sales_total),
            },
            "debt_payments": {
                "count": (
                    debt_payments_summary[
                        "count"
                    ]
                ),
                "total": str(debt_total),
            },
            "purchases": {
                "count": (
                    purchases_summary["count"]
                ),
                "total": str(
                    purchases_total
                ),
            },
            "expenses": {
                "count": (
                    expenses_summary["count"]
                ),
                "total": str(
                    expenses_total
                ),
            },
            "incoming_total": str(
                incoming_total
            ),
            "outgoing_total": str(
                outgoing_total
            ),
            "net_amount": str(
                incoming_total
                - outgoing_total
            ),
        },
        "results": results,
    }

def build_debts_summary(
    *,
    business,
    date_from: date,
    date_to: date,
) -> dict:
    """
    Calcula:

    - deudas generadas dentro del período;
    - pagos recibidos dentro del período;
    - saldo pendiente al final del período;
    - saldo vencido al final del período.

    El pendiente histórico se calcula usando solamente pagos
    cuya fecha sea menor o igual a date_to.
    """
    start_datetime, end_datetime = (
        get_report_datetime_range(
            date_from=date_from,
            date_to=date_to,
        )
    )

    valid_debts = (
        Debt.objects
        .filter(
            transaction__business=business,
            transaction__created_at__lt=(
                end_datetime
            ),
        )
        .exclude(
            transaction__status__name__in=(
                EXCLUDED_STATUS_NAMES
            )
        )
    )

    generated_debts = valid_debts.filter(
        transaction__created_at__gte=(
            start_datetime
        )
    )

    generated_summary = (
        generated_debts.aggregate(
            count=Count("id"),
            total=Sum("total_amount"),
        )
    )

    period_payments = (
        DebtPayment.objects
        .filter(
            debt__transaction__business=business,
            payment_date__gte=date_from,
            payment_date__lte=date_to,
        )
        .exclude(
            debt__transaction__status__name__in=(
                EXCLUDED_STATUS_NAMES
            )
        )
    )

    payments_summary = (
        period_payments.aggregate(
            count=Count("id"),
            total=Sum("amount"),
        )
    )

    all_debt_total = decimal_or_zero(
        valid_debts.aggregate(
            total=Sum("total_amount")
        )["total"]
    )

    payments_until_period_end = (
        DebtPayment.objects
        .filter(
            debt__in=valid_debts,
            payment_date__lte=date_to,
        )
        .aggregate(
            total=Sum("amount")
        )
    )

    paid_until_end = decimal_or_zero(
        payments_until_period_end["total"]
    )

    outstanding_at_end = max(
        all_debt_total - paid_until_end,
        Decimal("0.00"),
    ).quantize(
        Decimal("0.01")
    )

    overdue_debts = valid_debts.filter(
        due_date__lt=date_to,
    )

    overdue_total = decimal_or_zero(
        overdue_debts.aggregate(
            total=Sum("total_amount")
        )["total"]
    )

    overdue_payments = decimal_or_zero(
        DebtPayment.objects
        .filter(
            debt__in=overdue_debts,
            payment_date__lte=date_to,
        )
        .aggregate(
            total=Sum("amount")
        )["total"]
    )

    overdue_outstanding = max(
        overdue_total - overdue_payments,
        Decimal("0.00"),
    ).quantize(
        Decimal("0.01")
    )

    generated_results = (
        generated_debts
        .select_related(
            "transaction",
            "transaction__customer",
            "transaction__employee",
        )
        .order_by(
            "due_date",
            "created_at",
        )
    )

    results = []

    for debt in generated_results:
        paid_to_period_end = (
            debt.payments
            .filter(
                payment_date__lte=date_to
            )
            .aggregate(
                total=Sum("amount")
            )["total"]
        )

        paid_amount = decimal_or_zero(
            paid_to_period_end
        )

        pending_amount = max(
            debt.total_amount - paid_amount,
            Decimal("0.00"),
        ).quantize(
            Decimal("0.01")
        )

        customer = (
            debt.transaction.customer
        )

        employee = (
            debt.transaction.employee
        )

        results.append({
            "debt": {
                "public_id": str(
                    debt.public_id
                ),
                "transaction": str(
                    debt.transaction.public_id
                ),
            },
            "customer": (
                {
                    "public_id": str(
                        customer.public_id
                    ),
                    "full_name": (
                        customer.full_name
                    ),
                }
                if customer is not None
                else None
            ),
            "employee": (
                {
                    "public_id": str(
                        employee.public_id
                    ),
                    "full_name": (
                        employee.full_name
                    ),
                }
                if employee is not None
                else None
            ),
            "total_amount": str(
                decimal_or_zero(
                    debt.total_amount
                )
            ),
            "paid_until_period_end": str(
                paid_amount
            ),
            "pending_at_period_end": str(
                pending_amount
            ),
            "due_date": (
                debt.due_date.isoformat()
            ),
            "was_overdue_at_period_end": (
                debt.due_date < date_to
                and pending_amount > 0
            ),
        })

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
        "generated": {
            "count": (
                generated_summary["count"]
            ),
            "total": str(
                decimal_or_zero(
                    generated_summary["total"]
                )
            ),
        },
        "payments_received": {
            "count": payments_summary["count"],
            "total": str(
                decimal_or_zero(
                    payments_summary["total"]
                )
            ),
        },
        "portfolio_at_period_end": {
            "original_debt_total": str(
                all_debt_total
            ),
            "paid_until_period_end": str(
                paid_until_end
            ),
            "outstanding": str(
                outstanding_at_end
            ),
            "overdue_outstanding": str(
                overdue_outstanding
            ),
        },
        "results": results,
    }
