from django.db import transaction as db_tx
from core.models import StockMovement, Transaction

def _movement_type_for_transaction(tx_type: str) -> str | None:
    if tx_type == "sale":
        return "sale"
    if tx_type == "purchase":
        return "entry"
    return None

@db_tx.atomic
def recreate_movements_for_transaction(tx: Transaction):
    """
    Idempotente: borra y vuelve a crear los movimientos de stock para una transacción.
    Úsalo en create/update.
    """
    StockMovement.objects.filter(transaction=tx).delete()

    mov_type = _movement_type_for_transaction(tx.type)
    if not mov_type:
        return 
    details = tx.details.select_related("product", "variant").all()
    for d in details:
        sign = -1 if mov_type == "sale" else 1
        StockMovement.objects.create(
            product=d.product,
            variant=d.variant,
            transaction=tx,
            type=mov_type,
            quantity=sign * d.quantity, 
            note=f"Auto from {tx.type} {tx.public_id}",
        )