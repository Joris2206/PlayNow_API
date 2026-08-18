from datetime import date
from decimal import Decimal

from django.db.models import Count, DecimalField, F, Q, Sum, Value
from django.db.models.functions import Coalesce

from core.models import (
    CashRegister,
    CommissionSettlement,
    Debt,
    DebtPayment,
    Product,
    Transaction,
)
from core.services.customer_supplier_reports import (
    decimal_or_zero,
    get_report_datetime_range,
)
from core.services.financial_flows import (
    direct_payment_transactions,
    exclude_terminal_transactions,
    recognized_debt_payments,
)


MONEY_FIELD = DecimalField(max_digits=12, decimal_places=2)


def build_dashboard_overview(
    *,
    business,
    date_from: date,
    date_to: date,
    low_stock_threshold: int = 5,
) -> dict:
    start_datetime, end_datetime = (
        get_report_datetime_range(
            date_from=date_from,
            date_to=date_to,
        )
    )

    transactions = exclude_terminal_transactions(
        Transaction.objects.filter(
            business=business,
            created_at__gte=start_datetime,
            created_at__lt=end_datetime,
        )
    )

    transaction_totals = transactions.aggregate(
        sales_count=Count(
            "id",
            filter=Q(type="sale"),
        ),
        sales_total=Sum(
            "total_value",
            filter=Q(type="sale"),
        ),
        purchases_count=Count(
            "id",
            filter=Q(type="purchase"),
        ),
        purchases_total=Sum(
            "total_value",
            filter=Q(type="purchase"),
        ),
        expenses_count=Count(
            "id",
            filter=Q(type="expense"),
        ),
        expenses_total=Sum(
            "total_value",
            filter=Q(type="expense"),
        ),
    )

    direct_payments = direct_payment_transactions(transactions)
    direct_payment_totals = direct_payments.aggregate(
        sales=Sum("total_value", filter=Q(type="sale")),
        purchases=Sum("total_value", filter=Q(type="purchase")),
        expenses=Sum("total_value", filter=Q(type="expense")),
    )

    debt_payments = recognized_debt_payments(
        DebtPayment.objects.filter(
            debt__transaction__business=business,
            payment_date__gte=date_from,
            payment_date__lte=date_to,
        )
    )
    debt_payment_totals = debt_payments.aggregate(
        count=Count("id"),
        received=Sum("amount", filter=Q(debt__transaction__type="sale")),
        made=Sum("amount", filter=Q(debt__transaction__type="purchase")),
    )

    valid_debts = exclude_terminal_transactions(
        Debt.objects.filter(
            transaction__business=business,
            transaction__created_at__lt=end_datetime,
        ),
        status_lookup="transaction__status__name",
    )
    debts_at_end = valid_debts.annotate(
        paid_until_end=Coalesce(
            Sum("payments__amount", filter=Q(payments__payment_date__lte=date_to)),
            Value(Decimal("0.00")),
            output_field=MONEY_FIELD,
        )
    )

    def debt_position(transaction_type):
        directional = (
            valid_debts.filter(transaction__type=transaction_type)
            if transaction_type is not None
            else valid_debts.exclude(transaction__type__in=("sale", "purchase"))
        )
        queryset = debts_at_end.filter(pk__in=directional.values("pk"))
        original = directional.aggregate(
            total=Sum("total_amount")
        )["total"]
        paid = DebtPayment.objects.filter(
            debt__in=directional,
            payment_date__lte=date_to,
        ).aggregate(total=Sum("amount"))["total"]
        outstanding = max(
            decimal_or_zero(original) - decimal_or_zero(paid),
            Decimal("0.00"),
        ).quantize(Decimal("0.01"))
        return {
            "outstanding": outstanding,
            "pending_count": queryset.filter(paid_until_end__lt=F("total_amount")).count(),
        }

    receivables = debt_position("sale")
    payables = debt_position("purchase")
    unclassified_debts = debt_position(None)
    outstanding_debt = (
        receivables["outstanding"]
        + payables["outstanding"]
        + unclassified_debts["outstanding"]
    ).quantize(Decimal("0.01"))
    pending_debts_count = (
        receivables["pending_count"]
        + payables["pending_count"]
        + unclassified_debts["pending_count"]
    )

    direct_received = decimal_or_zero(direct_payment_totals["sales"])
    direct_made = (
        decimal_or_zero(direct_payment_totals["purchases"])
        + decimal_or_zero(direct_payment_totals["expenses"])
    ).quantize(Decimal("0.01"))
    debt_received = decimal_or_zero(debt_payment_totals["received"])
    debt_made = decimal_or_zero(debt_payment_totals["made"])
    payments_received = (direct_received + debt_received).quantize(Decimal("0.01"))
    payments_made = (direct_made + debt_made).quantize(Decimal("0.01"))

    closed_cash_registers = (
        CashRegister.objects
        .filter(
            business=business,
            status=CashRegister.STATUS_CLOSED,
            close_time__gte=start_datetime,
            close_time__lt=end_datetime,
        )
    )

    cash_totals = (
        closed_cash_registers.aggregate(
            closed_count=Count("id"),
            expected_total=Sum(
                "expected_closing_balance"
            ),
            counted_total=Sum(
                "closing_balance"
            ),
            difference_total=Sum(
                "difference"
            ),
        )
    )

    open_cash_register = (
        CashRegister.objects
        .filter(
            business=business,
            status=CashRegister.STATUS_OPEN,
        )
        .exists()
    )

    commissions = (
        CommissionSettlement.objects
        .filter(
            employee__business=business,
            period_start__gte=date_from,
            period_end__lte=date_to,
        )
    )

    commission_totals = commissions.aggregate(
        gross_total=Sum(
            "commission_total"
        ),
        net_total=Sum(
            "net_commission_payable"
        ),
        pending_total=Sum(
            "net_commission_payable",
            filter=Q(
                status=(
                    CommissionSettlement
                    .STATUS_PENDING
                )
            ),
        ),
        paid_total=Sum(
            "net_commission_payable",
            filter=Q(
                status=(
                    CommissionSettlement
                    .STATUS_PAID
                )
            ),
        ),
    )

    inventory = (
        Product.objects
        .filter(business=business)
        .aggregate(
            current_units=Sum("stock"),
            low_stock_count=Count(
                "id",
                filter=Q(
                    stock__lte=low_stock_threshold
                ),
            ),
            out_of_stock_count=Count(
                "id",
                filter=Q(stock=0),
            ),
        )
    )

    current_inventory_units = (
        int(inventory["current_units"] or 0)
    )

    low_stock_items_count = (
        inventory["low_stock_count"]
    )

    out_of_stock_items_count = (
        inventory["out_of_stock_count"]
    )

    sales_total = decimal_or_zero(
        transaction_totals["sales_total"]
    )

    purchases_total = decimal_or_zero(
        transaction_totals[
            "purchases_total"
        ]
    )

    expenses_total = decimal_or_zero(
        transaction_totals[
            "expenses_total"
        ]
    )

    gross_margin_before_costs = (
        sales_total
        - purchases_total
        - expenses_total
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
        "cards": {
            "sales_total": str(
                sales_total
            ),
            "purchases_total": str(
                purchases_total
            ),
            "expenses_total": str(
                expenses_total
            ),
            "gross_margin_before_costs": str(
                gross_margin_before_costs
            ),
            "outstanding_debt": str(
                outstanding_debt
            ),
            "outstanding_receivables": str(receivables["outstanding"]),
            "outstanding_payables": str(payables["outstanding"]),
            "debt_payments_received": str(
                debt_received
            ),
            "debt_payments_made": str(debt_made),
            "payments_received": str(payments_received),
            "payments_made": str(payments_made),
            "pending_commissions": str(
                decimal_or_zero(
                    commission_totals[
                        "pending_total"
                    ]
                )
            ),
            "cash_difference": str(
                decimal_or_zero(
                    cash_totals[
                        "difference_total"
                    ]
                )
            ),
            "current_inventory_units": (
                current_inventory_units
            ),
        },
        "activity": {
            "sales_count": (
                transaction_totals[
                    "sales_count"
                ]
            ),
            "purchases_count": (
                transaction_totals[
                    "purchases_count"
                ]
            ),
            "expenses_count": (
                transaction_totals[
                    "expenses_count"
                ]
            ),
            "debt_payments_count": (
                debt_payment_totals["count"]
            ),
            "pending_debts_count": (
                pending_debts_count
            ),
            "closed_cash_registers_count": (
                cash_totals[
                    "closed_count"
                ]
            ),
            "open_cash_register": (
                open_cash_register
            ),
            "low_stock_items_count": (
                low_stock_items_count
            ),
            "out_of_stock_items_count": (
                out_of_stock_items_count
            ),
        },
        "commissions": {
            "gross_total": str(
                decimal_or_zero(
                    commission_totals[
                        "gross_total"
                    ]
                )
            ),
            "net_total": str(
                decimal_or_zero(
                    commission_totals[
                        "net_total"
                    ]
                )
            ),
            "pending_total": str(
                decimal_or_zero(
                    commission_totals[
                        "pending_total"
                    ]
                )
            ),
            "paid_total": str(
                decimal_or_zero(
                    commission_totals[
                        "paid_total"
                    ]
                )
            ),
        },
        "cash": {
            "closed_count": (
                cash_totals[
                    "closed_count"
                ]
            ),
            "expected_total": str(
                decimal_or_zero(
                    cash_totals[
                        "expected_total"
                    ]
                )
            ),
            "counted_total": str(
                decimal_or_zero(
                    cash_totals[
                        "counted_total"
                    ]
                )
            ),
            "difference_total": str(
                decimal_or_zero(
                    cash_totals[
                        "difference_total"
                    ]
                )
            ),
        },
    }
