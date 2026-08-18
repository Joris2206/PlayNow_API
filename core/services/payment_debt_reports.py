from datetime import date
from decimal import Decimal

from django.db.models import Count, DecimalField, F, Q, Sum, Value
from django.db.models.functions import Coalesce

from core.models import Debt, DebtPayment, PaymentMethod, Transaction
from core.services.customer_supplier_reports import decimal_or_zero, get_report_datetime_range
from core.services.financial_flows import (
    direct_payment_transactions,
    exclude_terminal_transactions,
    recognized_debt_payments,
)


MONEY_FIELD = DecimalField(max_digits=12, decimal_places=2)


def _group_by_method(queryset, prefix, amount_field):
    return {
        row["payment_method_id"]: row
        for row in queryset.values("payment_method_id").annotate(**{
            f"{prefix}_count": Count("id"),
            f"{prefix}_total": Sum(amount_field),
        })
    }


def _summary(queryset, amount_field):
    result = queryset.aggregate(count=Count("id"), total=Sum(amount_field))
    return {"count": result["count"], "total": decimal_or_zero(result["total"])}


def _render_summary(summary):
    return {"count": summary["count"], "total": str(summary["total"])}


def build_payments_summary(*, business, date_from: date, date_to: date, payment_method=None) -> dict:
    """Recognize direct payments and historical-debt payments exactly once."""
    start_datetime, end_datetime = get_report_datetime_range(
        date_from=date_from, date_to=date_to,
    )
    transactions = exclude_terminal_transactions(Transaction.objects.filter(
        business=business,
        created_at__gte=start_datetime,
        created_at__lt=end_datetime,
    ))
    direct = direct_payment_transactions(transactions)
    debt_payments = recognized_debt_payments(DebtPayment.objects.filter(
        debt__transaction__business=business,
        payment_date__gte=date_from,
        payment_date__lte=date_to,
    ))
    if payment_method is not None:
        direct = direct.filter(payment_method=payment_method)
        debt_payments = debt_payments.filter(payment_method=payment_method)

    sources = {
        "sales": (direct.filter(type="sale"), "total_value"),
        "purchases": (direct.filter(type="purchase"), "total_value"),
        "expenses": (direct.filter(type="expense"), "total_value"),
        "received": (debt_payments.filter(debt__transaction__type="sale"), "amount"),
        "made": (debt_payments.filter(debt__transaction__type="purchase"), "amount"),
    }
    grouped = {
        name: _group_by_method(queryset, name, amount_field)
        for name, (queryset, amount_field) in sources.items()
    }
    method_ids = set().union(*(set(rows) for rows in grouped.values()))
    methods = PaymentMethod.objects.filter(
        business=business, id__in=method_ids,
    ).order_by("name")

    results = []
    for method in methods:
        rows = {name: data.get(method.id, {}) for name, data in grouped.items()}
        amounts = {
            name: decimal_or_zero(row.get(f"{name}_total"))
            for name, row in rows.items()
        }
        incoming = (amounts["sales"] + amounts["received"]).quantize(Decimal("0.01"))
        outgoing = (
            amounts["purchases"] + amounts["expenses"] + amounts["made"]
        ).quantize(Decimal("0.01"))
        debt_total = (amounts["received"] + amounts["made"]).quantize(Decimal("0.01"))

        def item(name):
            return {
                "count": rows[name].get(f"{name}_count", 0),
                "total": str(amounts[name]),
            }

        results.append({
            "payment_method": {
                "public_id": str(method.public_id),
                "name": method.name,
                "method_type": method.method_type,
            },
            "sales": item("sales"),
            "purchases": item("purchases"),
            "expenses": item("expenses"),
            "debt_payments": {
                "count": rows["received"].get("received_count", 0) + rows["made"].get("made_count", 0),
                "total": str(debt_total),
            },
            "debt_payments_received": item("received"),
            "debt_payments_made": item("made"),
            "total_incoming": str(incoming),
            "total_outgoing": str(outgoing),
            "net_amount": str(incoming - outgoing),
        })

    summaries = {
        name: _summary(queryset, amount_field)
        for name, (queryset, amount_field) in sources.items()
    }
    incoming = (summaries["sales"]["total"] + summaries["received"]["total"]).quantize(Decimal("0.01"))
    outgoing = (
        summaries["purchases"]["total"]
        + summaries["expenses"]["total"]
        + summaries["made"]["total"]
    ).quantize(Decimal("0.01"))

    return {
        "business": {"public_id": str(business.public_id), "name": business.business_name, "currency": business.currency},
        "period": {"date_from": date_from.isoformat(), "date_to": date_to.isoformat()},
        "totals": {
            "sales": _render_summary(summaries["sales"]),
            "purchases": _render_summary(summaries["purchases"]),
            "expenses": _render_summary(summaries["expenses"]),
            "debt_payments": {
                "count": summaries["received"]["count"] + summaries["made"]["count"],
                "total": str((summaries["received"]["total"] + summaries["made"]["total"]).quantize(Decimal("0.01"))),
            },
            "debt_payments_received": _render_summary(summaries["received"]),
            "debt_payments_made": _render_summary(summaries["made"]),
            "payments_received": str(incoming),
            "payments_made": str(outgoing),
            "incoming_total": str(incoming),
            "outgoing_total": str(outgoing),
            "net_amount": str(incoming - outgoing),
        },
        "results": results,
    }


def _annotate_paid_until(queryset, date_to):
    return queryset.annotate(paid_until_end=Coalesce(
        Sum("payments__amount", filter=Q(payments__payment_date__lte=date_to)),
        Value(Decimal("0.00")),
        output_field=MONEY_FIELD,
    ))


def _debt_direction_summary(queryset, date_to):
    original = queryset.aggregate(count=Count("id"), total=Sum("total_amount"))
    paid = DebtPayment.objects.filter(debt__in=queryset, payment_date__lte=date_to).aggregate(total=Sum("amount"))
    original_total = decimal_or_zero(original["total"])
    paid_total = decimal_or_zero(paid["total"])
    settled_count = _annotate_paid_until(queryset, date_to).filter(
        paid_until_end__gte=F("total_amount")
    ).count()
    return {
        "count": original["count"],
        "settled_count": settled_count,
        "pending_count": original["count"] - settled_count,
        "original_total": original_total,
        "paid_total": paid_total,
        "outstanding": max(original_total - paid_total, Decimal("0.00")).quantize(Decimal("0.01")),
    }


def build_debts_summary(*, business, date_from: date, date_to: date) -> dict:
    start_datetime, end_datetime = get_report_datetime_range(
        date_from=date_from, date_to=date_to,
    )
    valid = exclude_terminal_transactions(
        Debt.objects.filter(
            transaction__business=business,
            transaction__created_at__lt=end_datetime,
        ),
        status_lookup="transaction__status__name",
    )
    generated = valid.filter(transaction__created_at__gte=start_datetime)
    direction_querysets = {
        "receivable": valid.filter(transaction__type="sale"),
        "payable": valid.filter(transaction__type="purchase"),
        "unclassified": valid.exclude(transaction__type__in=("sale", "purchase")),
    }
    direction_summaries = {
        name: _debt_direction_summary(queryset, date_to)
        for name, queryset in direction_querysets.items()
    }
    period_payments = recognized_debt_payments(DebtPayment.objects.filter(
        debt__transaction__business=business,
        payment_date__gte=date_from,
        payment_date__lte=date_to,
    ))
    received = _summary(period_payments.filter(debt__transaction__type="sale"), "amount")
    made = _summary(period_payments.filter(debt__transaction__type="purchase"), "amount")

    original_total = sum(
        (item["original_total"] for item in direction_summaries.values()),
        Decimal("0.00"),
    ).quantize(Decimal("0.01"))
    paid_total = sum(
        (item["paid_total"] for item in direction_summaries.values()),
        Decimal("0.00"),
    ).quantize(Decimal("0.01"))
    outstanding = max(original_total - paid_total, Decimal("0.00")).quantize(Decimal("0.01"))
    overdue = _debt_direction_summary(valid.filter(due_date__lt=date_to), date_to)
    generated_summary = generated.aggregate(count=Count("id"), total=Sum("total_amount"))

    results = []
    generated_rows = _annotate_paid_until(generated, date_to).select_related(
        "transaction", "transaction__customer", "transaction__supplier", "transaction__employee",
    ).order_by("due_date", "created_at")
    for debt in generated_rows:
        transaction = debt.transaction
        paid = decimal_or_zero(debt.paid_until_end)
        pending = max(debt.total_amount - paid, Decimal("0.00")).quantize(Decimal("0.01"))
        direction = {"sale": "receivable", "purchase": "payable"}.get(transaction.type, "unclassified")

        def party(obj, name_field):
            if obj is None:
                return None
            name = getattr(obj, name_field)
            return {"public_id": str(obj.public_id), "name": name, name_field: name}

        results.append({
            "debt": {"public_id": str(debt.public_id), "transaction_public_id": str(transaction.public_id)},
            "transaction": {
                "public_id": str(transaction.public_id),
                "type": transaction.type,
            },
            "direction": direction,
            "customer": party(transaction.customer, "full_name"),
            "supplier": party(transaction.supplier, "name"),
            "employee": party(transaction.employee, "full_name"),
            "total_amount": str(decimal_or_zero(debt.total_amount)),
            "total": str(decimal_or_zero(debt.total_amount)),
            "paid_until_period_end": str(paid),
            "paid": str(paid),
            "pending_at_period_end": str(pending),
            "outstanding": str(pending),
            "is_settled_at_period_end": pending == 0,
            "is_settled": pending == 0,
            "due_date": debt.due_date.isoformat(),
            "was_overdue_at_period_end": debt.due_date < date_to and pending > 0,
        })

    def render_direction(summary):
        return {
            "count": summary["count"],
            "settled_count": summary["settled_count"],
            "pending_count": summary["pending_count"],
            "original_total": str(summary["original_total"]),
            "paid_total": str(summary["paid_total"]),
            "outstanding": str(summary["outstanding"]),
        }

    return {
        "business": {"public_id": str(business.public_id), "name": business.business_name, "currency": business.currency},
        "period": {"date_from": date_from.isoformat(), "date_to": date_to.isoformat()},
        "generated": {"count": generated_summary["count"], "total": str(decimal_or_zero(generated_summary["total"]))},
        "payments_received": _render_summary(received),
        "payments_made": _render_summary(made),
        "accounts_receivable": render_direction(direction_summaries["receivable"]),
        "accounts_payable": render_direction(direction_summaries["payable"]),
        "unclassified": render_direction(direction_summaries["unclassified"]),
        "portfolio_at_period_end": {
            "original_debt_total": str(original_total),
            "paid_until_period_end": str(paid_total),
            "outstanding": str(outstanding),
            "overdue_outstanding": str(overdue["outstanding"]),
        },
        "results": results,
    }
