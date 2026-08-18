from datetime import (
    date,
    datetime,
    time,
    timedelta,
)
from decimal import Decimal

from django.db.models import (
    Count,
    Q,
    Sum,
)
from django.utils import timezone as django_timezone

from core.models import (
    CashRegister,
    CommissionSettlement,
    Debt,
    DebtPayment,
    Transaction,
)
from core.services.financial_flows import (
    direct_payment_transactions,
    exclude_terminal_transactions,
    recognized_debt_payments,
)

def decimal_or_zero(
    value,
) -> Decimal:
    return (
        value
        if value is not None
        else Decimal("0.00")
    ).quantize(
        Decimal("0.01")
    )


def get_month_period(
    *,
    year: int,
    month: int,
) -> dict:
    start_date = date(
        year,
        month,
        1,
    )

    if month == 12:
        next_month_date = date(
            year + 1,
            1,
            1,
        )
    else:
        next_month_date = date(
            year,
            month + 1,
            1,
        )

    current_timezone = (
        django_timezone.get_current_timezone()
    )

    start_datetime = (
        django_timezone.make_aware(
            datetime.combine(
                start_date,
                time.min,
            ),
            current_timezone,
        )
    )

    end_datetime = (
        django_timezone.make_aware(
            datetime.combine(
                next_month_date,
                time.min,
            ),
            current_timezone,
        )
    )

    end_date = (
        next_month_date
        - timedelta(days=1)
    )

    return {
        "start_date": start_date,
        "end_date": end_date,
        "start_datetime": start_datetime,
        "end_datetime": end_datetime,
    }


def build_monthly_summary(
    *,
    business,
    year: int,
    month: int,
) -> dict:
    """
    Construye el resumen mensual dinámico de un negocio.

    Todos los valores monetarios se devuelven como strings
    para que el resultado pueda:

    - enviarse directamente como JSON;
    - guardarse en MonthlyClosure.summary;
    - evitar pérdida de precisión decimal.
    """
    period = get_month_period(
        year=year,
        month=month,
    )

    start_date = period["start_date"]
    end_date = period["end_date"]

    period_start = period[
        "start_datetime"
    ]

    period_end = period[
        "end_datetime"
    ]

    base_transactions = exclude_terminal_transactions(
        Transaction.objects.filter(
            business=business,
            created_at__gte=period_start,
            created_at__lt=period_end,
        )
    )

    def transaction_summary(
        transaction_type: str,
    ) -> dict:
        result = (
            base_transactions
            .filter(
                type=transaction_type,
            )
            .aggregate(
                count=Count("id"),
                total=Sum("total_value"),
            )
        )

        return {
            "count": result["count"],
            "total": str(
                decimal_or_zero(
                    result["total"]
                )
            ),
        }

    sales = transaction_summary(
        "sale"
    )

    purchases = transaction_summary(
        "purchase"
    )

    expenses = transaction_summary(
        "expense"
    )

    direct_payments = direct_payment_transactions(base_transactions)
    paid_sales = (
        direct_payments.filter(type="sale")
        .aggregate(
            count=Count("id"),
            total=Sum("total_value"),
        )
    )

    debt_sales = (
        base_transactions.filter(type="sale", debts__isnull=False)
        .aggregate(
            count=Count("id"),
            total=Sum("total_value"),
        )
    )

    debt_generated = exclude_terminal_transactions(
        Debt.objects.filter(
            transaction__business=business,
            transaction__created_at__gte=(
                period_start
            ),
            transaction__created_at__lt=(
                period_end
            ),
        ),
        status_lookup="transaction__status__name",
    ).aggregate(
            count=Count("id"),
            total=Sum("total_amount"),
    )

    debt_payment_queryset = recognized_debt_payments(
        DebtPayment.objects.filter(
            debt__transaction__business=business,
            payment_date__gte=start_date,
            payment_date__lte=end_date,
        )
    )
    debt_payments = debt_payment_queryset.aggregate(
        count=Count("id"),
        total=Sum("amount"),
        received=Sum("amount", filter=Q(debt__transaction__type="sale")),
        made=Sum("amount", filter=Q(debt__transaction__type="purchase")),
    )
    direct_payment_totals = direct_payments.aggregate(
        sales=Sum("total_value", filter=Q(type="sale")),
        purchases=Sum("total_value", filter=Q(type="purchase")),
        expenses=Sum("total_value", filter=Q(type="expense")),
    )

    valid_debts = exclude_terminal_transactions(
        Debt.objects.filter(
            transaction__business=business,
            transaction__created_at__lt=period_end,
        ),
        status_lookup="transaction__status__name",
    )

    def outstanding_for(transaction_type):
        directional = valid_debts.filter(transaction__type=transaction_type)
        original = directional.aggregate(total=Sum("total_amount"))["total"]
        paid = DebtPayment.objects.filter(
            debt__in=directional,
            payment_date__lte=end_date,
        ).aggregate(total=Sum("amount"))["total"]
        return max(
            decimal_or_zero(original) - decimal_or_zero(paid),
            Decimal("0.00"),
        ).quantize(Decimal("0.01"))

    outstanding_receivables = outstanding_for("sale")
    outstanding_payables = outstanding_for("purchase")
    unclassified_debts = valid_debts.exclude(
        transaction__type__in=("sale", "purchase")
    )
    unclassified_original = unclassified_debts.aggregate(total=Sum("total_amount"))["total"]
    unclassified_paid = DebtPayment.objects.filter(
        debt__in=unclassified_debts,
        payment_date__lte=end_date,
    ).aggregate(total=Sum("amount"))["total"]
    outstanding_unclassified = max(
        decimal_or_zero(unclassified_original) - decimal_or_zero(unclassified_paid),
        Decimal("0.00"),
    ).quantize(Decimal("0.01"))
    outstanding_total = (
        outstanding_receivables
        + outstanding_payables
        + outstanding_unclassified
    ).quantize(Decimal("0.01"))
    debt_received = decimal_or_zero(debt_payments["received"])
    debt_made = decimal_or_zero(debt_payments["made"])
    direct_received = decimal_or_zero(direct_payment_totals["sales"])
    direct_made = (
        decimal_or_zero(direct_payment_totals["purchases"])
        + decimal_or_zero(direct_payment_totals["expenses"])
    ).quantize(Decimal("0.01"))
    payments_received = (direct_received + debt_received).quantize(Decimal("0.01"))
    payments_made = (direct_made + debt_made).quantize(Decimal("0.01"))

    closed_registers = (
        CashRegister.objects
        .filter(
            business=business,
            status=CashRegister.STATUS_CLOSED,
            close_time__gte=period_start,
            close_time__lt=period_end,
        )
    )

    cash_summary = (
        closed_registers.aggregate(
            registers_count=Count("id"),
            opening_total=Sum(
                "opening_balance"
            ),
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

    shortages = (
        closed_registers
        .filter(
            difference__lt=0,
        )
        .aggregate(
            total=Sum("difference")
        )
    )

    surpluses = (
        closed_registers
        .filter(
            difference__gt=0,
        )
        .aggregate(
            total=Sum("difference")
        )
    )

    commission_settlements = (
        CommissionSettlement.objects
        .filter(
            employee__business=business,
            period_start__gte=start_date,
            period_end__lte=end_date,
        )
    )

    commission_summary = (
        commission_settlements
        .aggregate(
            settlements_count=Count("id"),
            sales_total=Sum(
                "sales_total"
            ),
            commission_total=Sum(
                "commission_total"
            ),
            employee_advances=Sum(
                "employee_advances"
            ),
            employee_repayments=Sum(
                "employee_repayments"
            ),
            advance_balance=Sum(
                "advance_balance"
            ),
            net_commission_payable=Sum(
                "net_commission_payable"
            ),
            remaining_advance_balance=Sum(
                "remaining_advance_balance"
            ),
        )
    )

    paid_commissions = (
        commission_settlements
        .filter(
            status=(
                CommissionSettlement
                .STATUS_PAID
            )
        )
        .aggregate(
            count=Count("id"),
            total=Sum(
                "net_commission_payable"
            ),
        )
    )

    pending_commissions = (
        commission_settlements
        .filter(
            status=(
                CommissionSettlement
                .STATUS_PENDING
            )
        )
        .aggregate(
            count=Count("id"),
            total=Sum(
                "net_commission_payable"
            ),
        )
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
            "year": year,
            "month": month,
            "date_from": (
                start_date.isoformat()
            ),
            "date_to": (
                end_date.isoformat()
            ),
        },
        "transactions": {
            "sales": {
                "count": sales["count"],
                "total": sales["total"],
                "paid_count": (
                    paid_sales["count"]
                ),
                "paid_total": str(
                    decimal_or_zero(
                        paid_sales["total"]
                    )
                ),
                "debt_count": (
                    debt_sales["count"]
                ),
                "debt_total": str(
                    decimal_or_zero(
                        debt_sales["total"]
                    )
                ),
            },
            "purchases": {
                "count": purchases["count"],
                "total": purchases["total"],
            },
            "expenses": {
                "count": expenses["count"],
                "total": expenses["total"],
            },
        },
        "debts": {
            "generated_count": (
                debt_generated["count"]
            ),
            "generated_total": str(
                decimal_or_zero(
                    debt_generated["total"]
                )
            ),
            "payments_count": (
                debt_payments["count"]
            ),
            "payments_total": str(
                decimal_or_zero(
                    debt_payments["total"]
                )
            ),
            "payments_received": str(debt_received),
            "payments_made": str(debt_made),
            "outstanding_receivables": str(outstanding_receivables),
            "outstanding_payables": str(outstanding_payables),
            "outstanding_unclassified": str(outstanding_unclassified),
            "outstanding_at_period_end": str(
                outstanding_total
            ),
        },
        "payments": {
            "received": str(payments_received),
            "made": str(payments_made),
            "net": str((payments_received - payments_made).quantize(Decimal("0.01"))),
            "direct_sales": str(direct_received),
            "direct_purchases_and_expenses": str(direct_made),
            "debt_payments_received": str(debt_received),
            "debt_payments_made": str(debt_made),
        },
        "cash_registers": {
            "closed_count": (
                cash_summary[
                    "registers_count"
                ]
            ),
            "opening_total": str(
                decimal_or_zero(
                    cash_summary[
                        "opening_total"
                    ]
                )
            ),
            "expected_total": str(
                decimal_or_zero(
                    cash_summary[
                        "expected_total"
                    ]
                )
            ),
            "counted_total": str(
                decimal_or_zero(
                    cash_summary[
                        "counted_total"
                    ]
                )
            ),
            "difference_total": str(
                decimal_or_zero(
                    cash_summary[
                        "difference_total"
                    ]
                )
            ),
            "shortages_total": str(
                decimal_or_zero(
                    shortages["total"]
                )
            ),
            "surpluses_total": str(
                decimal_or_zero(
                    surpluses["total"]
                )
            ),
        },
        "commissions": {
            "settlements_count": (
                commission_summary[
                    "settlements_count"
                ]
            ),
            "settled_sales_total": str(
                decimal_or_zero(
                    commission_summary[
                        "sales_total"
                    ]
                )
            ),
            "gross_commission_total": str(
                decimal_or_zero(
                    commission_summary[
                        "commission_total"
                    ]
                )
            ),
            "employee_advances": str(
                decimal_or_zero(
                    commission_summary[
                        "employee_advances"
                    ]
                )
            ),
            "employee_repayments": str(
                decimal_or_zero(
                    commission_summary[
                        "employee_repayments"
                    ]
                )
            ),
            "advance_balance": str(
                decimal_or_zero(
                    commission_summary[
                        "advance_balance"
                    ]
                )
            ),
            "net_commission_payable": str(
                decimal_or_zero(
                    commission_summary[
                        "net_commission_payable"
                    ]
                )
            ),
            "remaining_advance_balance": str(
                decimal_or_zero(
                    commission_summary[
                        "remaining_advance_balance"
                    ]
                )
            ),
            "paid": {
                "count": (
                    paid_commissions["count"]
                ),
                "total": str(
                    decimal_or_zero(
                        paid_commissions[
                            "total"
                        ]
                    )
                ),
            },
            "pending": {
                "count": (
                    pending_commissions[
                        "count"
                    ]
                ),
                "total": str(
                    decimal_or_zero(
                        pending_commissions[
                            "total"
                        ]
                    )
                ),
            },
        },
    }
