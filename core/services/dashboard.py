from datetime import date
from decimal import Decimal

from django.db.models import Count, Q, Sum

from core.models import (
    CashRegister,
    CommissionSettlement,
    Debt,
    DebtPayment,
    Product,
    ProductVariant,
    Transaction,
)
from core.services.customer_supplier_reports import (
    EXCLUDED_STATUS_NAMES,
    decimal_or_zero,
    get_report_datetime_range,
)


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
        .aggregate(
            count=Count("id"),
            total=Sum("amount"),
        )
    )

    valid_debts = (
        Debt.objects
        .filter(
            transaction__business=business,
            transaction__created_at__lt=end_datetime,
        )
        .exclude(
            transaction__status__name__in=(
                EXCLUDED_STATUS_NAMES
            )
        )
    )

    debt_totals = valid_debts.aggregate(
        original_total=Sum("total_amount"),
    )

    payments_until_period_end = (
        DebtPayment.objects
        .filter(
            debt__in=valid_debts,
            payment_date__lte=date_to,
        )
        .aggregate(
            total=Sum("amount"),
        )
    )

    outstanding_debt = max(
        decimal_or_zero(
            debt_totals["original_total"]
        )
        - decimal_or_zero(
            payments_until_period_end["total"]
        ),
        Decimal("0.00"),
    ).quantize(
        Decimal("0.01")
    )

    pending_debts_count = 0

    for debt in valid_debts:
        paid_until_end = (
            debt.payments
            .filter(
                payment_date__lte=date_to,
            )
            .aggregate(
                total=Sum("amount"),
            )["total"]
        )

        if (
            debt.total_amount
            - decimal_or_zero(
                paid_until_end
            )
            > 0
        ):
            pending_debts_count += 1

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

    products_without_variants = (
        Product.objects
        .filter(
            business=business,
        )
        .annotate(
            variants_count=Count(
                "variant_types__variants",
                distinct=True,
            )
        )
        .filter(
            variants_count=0,
        )
    )

    simple_inventory = (
        products_without_variants
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

    variants = (
        ProductVariant.objects
        .filter(
            variant_type__product__business=(
                business
            ),
        )
    )

    variant_inventory = (
        variants.aggregate(
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
        int(
            simple_inventory[
                "current_units"
            ]
            or 0
        )
        + int(
            variant_inventory[
                "current_units"
            ]
            or 0
        )
    )

    low_stock_items_count = (
        simple_inventory[
            "low_stock_count"
        ]
        + variant_inventory[
            "low_stock_count"
        ]
    )

    out_of_stock_items_count = (
        simple_inventory[
            "out_of_stock_count"
        ]
        + variant_inventory[
            "out_of_stock_count"
        ]
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
            "debt_payments_received": str(
                decimal_or_zero(
                    debt_payments["total"]
                )
            ),
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
                debt_payments["count"]
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