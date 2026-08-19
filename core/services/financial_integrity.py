from dataclasses import dataclass
from decimal import Decimal

from django.db import connection
from django.db.models import DecimalField, F, Q, Sum, Value
from django.db.models.functions import Coalesce

from core.models import (
    Debt,
    DebtPayment,
    MonthlyClosure,
    PaymentMethod,
    Transaction,
)
from core.services.financial_flows import (
    build_terminal_transaction_status_q,
)


# Minimum stable structural markers introduced by the corrected Phase 4
# contract. Other summary fields remain optional for classification.
_CURRENT_MONTHLY_CLOSURE_MARKERS = {
    "debts": frozenset({
        "outstanding_receivables",
        "outstanding_payables",
        "outstanding_unclassified",
    }),
    "payments": frozenset({
        "received",
        "made",
        "debt_payments_received",
        "debt_payments_made",
    }),
}


def is_legacy_monthly_closure_snapshot(snapshot):
    """Classify old, ambiguous, or malformed persisted summaries."""
    if not isinstance(snapshot, dict):
        return True

    for block_name, required_fields in (
        _CURRENT_MONTHLY_CLOSURE_MARKERS.items()
    ):
        block = snapshot.get(block_name)
        if not isinstance(block, dict):
            return True
        if not required_fields.issubset(block):
            return True

    return False


@dataclass(frozen=True)
class FinancialIntegrityFinding:
    severity: str
    code: str
    message: str
    count: int
    sample_public_ids: tuple[str, ...]


def diagnose_financial_integrity(*, business=None, sample_limit=10):
    """Return deterministic, read-only financial integrity findings."""
    findings = []

    debt_scope = Debt.objects.all()
    payment_scope = DebtPayment.objects.all()
    transaction_scope = Transaction.objects.all()
    method_scope = PaymentMethod.objects.all()
    closure_scope = MonthlyClosure.objects.all()
    if business is not None:
        debt_scope = debt_scope.filter(transaction__business=business)
        payment_scope = payment_scope.filter(
            debt__transaction__business=business,
        )
        transaction_scope = transaction_scope.filter(business=business)
        method_scope = method_scope.filter(business=business)
        closure_scope = closure_scope.filter(business=business)

    def add(severity, code, message, queryset):
        queryset = queryset.order_by("public_id")
        count = queryset.count()
        if not count:
            return
        samples = tuple(
            str(public_id)
            for public_id in queryset.values_list(
                "public_id",
                flat=True,
            )[:sample_limit]
        )
        findings.append(FinancialIntegrityFinding(
            severity=severity,
            code=code,
            message=message,
            count=count,
            sample_public_ids=samples,
        ))

    add("ERROR", "debt_total_nonpositive", "Debt.total_amount debe ser positivo.", debt_scope.filter(total_amount__lte=0))
    add("ERROR", "debt_paid_negative", "Debt.paid_amount no puede ser negativo.", debt_scope.filter(paid_amount__lt=0))
    add("ERROR", "debt_paid_above_total", "Debt.paid_amount supera total_amount.", debt_scope.filter(paid_amount__gt=F("total_amount")))
    add("ERROR", "debt_settled_with_balance", "Debt liquidada conserva saldo pendiente.", debt_scope.filter(is_settled=True).exclude(paid_amount=F("total_amount")))
    add("ERROR", "debt_unsettled_without_balance", "Debt no liquidada carece de saldo pendiente.", debt_scope.filter(is_settled=False, paid_amount=F("total_amount")))

    add("ERROR", "debt_payment_nonpositive", "DebtPayment.amount debe ser positivo.", payment_scope.filter(amount__lte=0))
    add(
        "ERROR",
        "debt_payment_transaction_mismatch",
        "DebtPayment.transaction no coincide con Debt.transaction.",
        payment_scope.filter(transaction__isnull=False).exclude(
            transaction_id=F("debt__transaction_id"),
        ),
    )
    add(
        "ERROR",
        "debt_payment_method_business_mismatch",
        "PaymentMethod pertenece a otro Business.",
        payment_scope.exclude(
            payment_method__business_id=F("debt__transaction__business_id"),
        ),
    )
    add(
        "ERROR",
        "debt_payment_terminal_transaction",
        "DebtPayment pertenece a una Transaction terminal.",
        payment_scope.filter(
            build_terminal_transaction_status_q(
                "debt__transaction__status__name",
            ),
        ),
    )
    add("ERROR", "debt_payment_expense", "Expense no puede tener DebtPayment.", payment_scope.filter(debt__transaction__type="expense"))
    with connection.cursor() as cursor:
        debt_payment_columns = {
            column.name
            for column in connection.introspection.get_table_description(
                cursor,
                DebtPayment._meta.db_table,
            )
        }
    if "created_by_id" in debt_payment_columns:
        add("WARNING", "debt_payment_actor_missing", "DebtPayment histórico no tiene actor auditable.", payment_scope.filter(created_by__isnull=True))
    else:
        findings.append(FinancialIntegrityFinding(
            severity="INFO",
            code="debt_payment_actor_column_pending",
            message="La migración del actor auditable aún no está aplicada.",
            count=1,
            sample_public_ids=(),
        ))

    payment_total = Coalesce(
        Sum("payments__amount"),
        Value(Decimal("0.00")),
        output_field=DecimalField(max_digits=12, decimal_places=2),
    )
    add(
        "ERROR",
        "debt_payment_sum_mismatch",
        "La suma de DebtPayments no coincide con Debt.paid_amount.",
        debt_scope.annotate(payment_total=payment_total).exclude(
            payment_total=F("paid_amount"),
        ),
    )

    add(
        "ERROR",
        "transaction_debt_missing",
        "Transaction pending/partial no tiene Debt.",
        transaction_scope.filter(
            payment_status__in=("pending", "partial"),
            debts__isnull=True,
        ),
    )
    add(
        "ERROR",
        "paid_transaction_unsettled_debt",
        "Transaction paid conserva Debt no liquidada.",
        transaction_scope.filter(
            payment_status="paid",
            debts__is_settled=False,
        ),
    )
    add("ERROR", "pending_transaction_with_payment", "Transaction pending tiene paid_amount positivo.", transaction_scope.filter(payment_status="pending", debts__paid_amount__gt=0))
    add("ERROR", "partial_transaction_without_payment", "Transaction partial no tiene pago positivo.", transaction_scope.filter(payment_status="partial", debts__paid_amount__lte=0))
    add("ERROR", "partial_transaction_fully_paid", "Transaction partial no conserva saldo pendiente.", transaction_scope.filter(payment_status="partial", debts__paid_amount__gte=F("debts__total_amount")))
    add(
        "ERROR",
        "transaction_is_debt_mismatch",
        "Transaction.is_debt no coincide con el saldo de Debt.",
        transaction_scope.filter(
            Q(is_debt=True, debts__isnull=True)
            | Q(is_debt=False, debts__paid_amount__lt=F("debts__total_amount"))
            | Q(is_debt=True, debts__paid_amount__gte=F("debts__total_amount"))
        ).distinct(),
    )
    add("ERROR", "expense_with_debt", "Expense no puede tener Debt.", debt_scope.filter(transaction__type="expense"))
    add("ERROR", "debt_unsupported_transaction_type", "Debt pertenece a un tipo de Transaction no soportado.", debt_scope.exclude(transaction__type__in=("sale", "purchase")))

    add("ERROR", "payment_method_catalog_missing_relation", "PaymentMethod carece de Business o status.", method_scope.filter(Q(business__isnull=True) | Q(status__isnull=True)))
    add("ERROR", "payment_method_type_invalid", "PaymentMethod.method_type no pertenece al catálogo.", method_scope.exclude(method_type__in=dict(PaymentMethod.METHOD_TYPES)))
    add("WARNING", "payment_method_other_review", "PaymentMethod de tipo other requiere revisión manual.", method_scope.filter(method_type=PaymentMethod.TYPE_OTHER))

    add(
        "WARNING",
        "terminal_transaction_pending_debt",
        "Transaction terminal conserva Debt pendiente para trazabilidad.",
        debt_scope.filter(
            build_terminal_transaction_status_q(
                "transaction__status__name",
            ),
            paid_amount__lt=F("total_amount"),
        ),
    )
    legacy_closure_count = 0
    legacy_closure_samples = []
    for public_id, summary in (
        closure_scope
        .order_by("public_id")
        .values_list("public_id", "summary")
        .iterator(chunk_size=200)
    ):
        if not is_legacy_monthly_closure_snapshot(summary):
            continue
        legacy_closure_count += 1
        if len(legacy_closure_samples) < sample_limit:
            legacy_closure_samples.append(str(public_id))

    if legacy_closure_count:
        findings.append(FinancialIntegrityFinding(
            severity="WARNING",
            code="historical_monthly_closure_review",
            message=(
                "MonthlyClosure con snapshot anterior, ambiguo o "
                "malformado requiere revisión."
            ),
            count=legacy_closure_count,
            sample_public_ids=tuple(legacy_closure_samples),
        ))

    severity_order = {"ERROR": 0, "WARNING": 1, "INFO": 2}
    return sorted(
        findings,
        key=lambda finding: (
            severity_order[finding.severity],
            finding.code,
        ),
    )
