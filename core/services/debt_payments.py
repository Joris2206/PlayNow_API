from decimal import Decimal

from django.db import transaction as db_tx
from django.utils import timezone
from rest_framework.exceptions import APIException, ValidationError

from core.models import (
    Debt,
    DebtPayment,
    PaymentMethod,
    Transaction,
)
from core.services.financial_flows import (
    is_terminal_transaction_status,
)


class DebtPaymentConflict(APIException):
    status_code = 409
    default_detail = (
        "El pago entra en conflicto con el estado actual de la deuda."
    )
    default_code = "debt_payment_conflict"


def _raise_amount_error(message):
    raise ValidationError({
        "amount": message,
    })


def get_locked_active_payment_method(
    *,
    payment_method_id,
    business_id,
):
    """Lock and authoritatively validate a payment method.

    Debt payments call this after locking Debt and Transaction, preserving
    the global Debt -> Transaction -> PaymentMethod order. A new Transaction
    has no pre-existing financial rows to lock, so creation locks only the
    PaymentMethod.
    """
    payment_method = (
        PaymentMethod.objects
        .select_for_update(of=("self",))
        .select_related("business", "status")
        .filter(
            pk=payment_method_id,
            business_id=business_id,
        )
        .first()
    )

    if payment_method is None:
        raise ValidationError({
            "payment_method_public_id": (
                "El método de pago no es válido para este negocio."
            ),
        })

    if payment_method.status.name.casefold() != "activo":
        raise ValidationError({
            "payment_method_public_id": (
                "El método de pago debe estar Activo."
            ),
        })

    return payment_method


@db_tx.atomic
def register_debt_payment(
    *,
    debt_id,
    amount,
    payment_date,
    payment_method_id,
    actor,
    submitted_transaction_id=None,
    observed_remaining_amount=None,
):
    """Register one payment and synchronize its Debt and Transaction."""
    debt = (
        Debt.objects
        .select_for_update(of=("self",))
        .get(pk=debt_id)
    )

    transaction = (
        Transaction.objects
        .select_for_update(of=("self",))
        .select_related(
            "business",
            "status",
        )
        .get(pk=debt.transaction_id)
    )

    if is_terminal_transaction_status(transaction.status):
        raise DebtPaymentConflict({
            "non_field_errors": (
                "No se puede registrar un pago contra "
                "una transacción terminal."
            ),
        })

    if payment_date > timezone.localdate():
        raise ValidationError({
            "payment_date": (
                "La fecha del pago no puede ser futura."
            ),
        })

    if amount <= Decimal("0.00"):
        _raise_amount_error(
            "El importe del pago debe ser mayor que cero."
        )

    payment_method = get_locked_active_payment_method(
        payment_method_id=payment_method_id,
        business_id=transaction.business_id,
    )

    if (
        submitted_transaction_id is not None
        and submitted_transaction_id
        != transaction.pk
    ):
        raise ValidationError({
            "transaction_public_id": (
                "La transacción debe coincidir con "
                "la transacción de la deuda."
            ),
        })

    remaining_amount = (
        debt.total_amount
        - debt.paid_amount
    )

    balance_changed = (
        observed_remaining_amount is not None
        and observed_remaining_amount
        != remaining_amount
    )

    if debt.is_settled or remaining_amount <= Decimal("0.00"):
        if balance_changed:
            raise DebtPaymentConflict({
                "amount": (
                    "El saldo fue consumido por otro pago concurrente."
                ),
            })

        raise ValidationError({
            "non_field_errors": (
                "La deuda ya se encuentra liquidada."
            ),
        })

    if amount > remaining_amount:
        if (
            balance_changed
            and amount <= observed_remaining_amount
        ):
            raise DebtPaymentConflict({
                "amount": (
                    "El saldo cambió mientras se registraba el pago."
                ),
            })

        _raise_amount_error(
            "El pago no puede superar el saldo pendiente."
        )

    new_paid_amount = debt.paid_amount + amount
    is_final_payment = (
        new_paid_amount == debt.total_amount
    )

    payment = DebtPayment.objects.create(
        debt=debt,
        amount=amount,
        payment_date=payment_date,
        payment_method=payment_method,
        transaction=transaction,
        created_by=actor,
    )

    debt.paid_amount = new_paid_amount
    debt.is_settled = is_final_payment
    debt.save(
        update_fields=[
            "paid_amount",
            "is_settled",
            "updated_at",
        ],
    )

    transaction.payment_status = (
        "paid"
        if is_final_payment
        else "partial"
    )
    transaction.is_debt = not is_final_payment
    transaction.save(
        update_fields=[
            "payment_status",
            "is_debt",
            "updated_at",
        ],
    )

    return payment
