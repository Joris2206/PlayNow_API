from django.db.models import QuerySet

from core.models import PaymentMethod


FLOW_IN = "inflow"
FLOW_OUT = "outflow"

TERMINAL_TRANSACTION_STATUS_NAMES = (
    "Eliminado",
    "Anulado",
    "Cancelado",
    "Void",
    "Deleted",
)


def transaction_flow_direction(transaction_or_type):
    transaction_type = getattr(
        transaction_or_type,
        "type",
        transaction_or_type,
    )
    if transaction_type == "sale":
        return FLOW_IN
    if transaction_type in {"purchase", "expense"}:
        return FLOW_OUT
    return None


def debt_payment_flow_direction(payment_or_type):
    transaction_type = getattr(
        payment_or_type,
        "transaction_type",
        payment_or_type,
    )
    if hasattr(payment_or_type, "debt"):
        transaction_type = payment_or_type.debt.transaction.type
    if transaction_type == "sale":
        return FLOW_IN
    if transaction_type == "purchase":
        return FLOW_OUT
    return None


def exclude_terminal_transactions(
    queryset: QuerySet,
    *,
    status_lookup="status__name",
):
    return queryset.exclude(**{
        f"{status_lookup}__in": (
            TERMINAL_TRANSACTION_STATUS_NAMES
        ),
    })


def direct_payment_transactions(queryset: QuerySet):
    """Transactions whose value is itself the payment source."""
    return transactions_without_historical_debt(queryset).filter(
        payment_status="paid",
        payment_method__isnull=False,
    )


def transactions_without_historical_debt(queryset: QuerySet):
    return queryset.filter(debts__isnull=True)


def recognized_debt_payments(queryset: QuerySet):
    """Debt payments with a supported V1 financial direction."""
    return (
        exclude_terminal_transactions(
            queryset,
            status_lookup="debt__transaction__status__name",
        )
        .filter(
            debt__transaction__type__in=(
                "sale",
                "purchase",
            ),
        )
    )


def cash_payment_methods(queryset: QuerySet):
    return queryset.filter(
        payment_method__method_type=PaymentMethod.TYPE_CASH,
    )
