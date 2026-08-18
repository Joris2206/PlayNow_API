from collections import defaultdict
from decimal import Decimal

from django.db import transaction as db_tx

from core.models import Debt, DebtPayment, Transaction
from core.services.debt_payments import DebtPaymentConflict
from core.services.financial_flows import is_terminal_transaction_status
from core.services.inventory import (
    lock_products_for_inventory,
    record_locked_stock_movement,
)


PAYMENT_ACTIVITY_CONFLICT_MESSAGE = (
    "No se puede anular una transacción que tiene pagos registrados."
)


@db_tx.atomic
def cancel_transaction(
    *,
    transaction_id,
    business_id,
    terminal_status,
    actor,
):
    """Cancel a transaction using the global Debt -> Transaction lock order."""
    debt = (
        Debt.objects
        .select_for_update(of=("self",))
        .filter(
            transaction_id=transaction_id,
            transaction__business_id=business_id,
        )
        .first()
    )

    transaction = (
        Transaction.objects
        .select_for_update(of=("self",))
        .select_related("status")
        .get(
            pk=transaction_id,
            business_id=business_id,
        )
    )

    if is_terminal_transaction_status(transaction.status):
        raise DebtPaymentConflict({
            "detail": "La transacción ya se encuentra anulada o eliminada.",
        })

    if debt is not None:
        has_payment_history = DebtPayment.objects.filter(debt=debt).exists()
        if (
            has_payment_history
            or debt.paid_amount > Decimal("0.00")
            or debt.is_settled
        ):
            raise DebtPaymentConflict({
                "non_field_errors": PAYMENT_ACTIVITY_CONFLICT_MESSAGE,
            })

    totals = defaultdict(int)
    for product_id, quantity in transaction.stock_movements.values_list(
        "product_id",
        "quantity",
    ):
        totals[product_id] += quantity

    locked_products = lock_products_for_inventory(
        product_ids=totals,
        business_id=transaction.business_id,
    )

    for product_id, total_quantity in sorted(totals.items()):
        if total_quantity:
            record_locked_stock_movement(
                product=locked_products[product_id],
                transaction=transaction,
                created_by=actor,
                movement_type="adjustment",
                quantity=-total_quantity,
                note=f"Auto neutralize {transaction.public_id}",
                insufficient_stock_message=(
                    "No se puede eliminar la transacción porque "
                    "dejaría stock negativo en un producto."
                ),
            )

    transaction.status = terminal_status
    transaction.save(update_fields=["status", "updated_at"])
    return transaction
