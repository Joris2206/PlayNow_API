from django.db import transaction as db_tx
from rest_framework.exceptions import ValidationError

from core.models import Product, StockMovement


@db_tx.atomic
def record_stock_movement(
    *,
    product,
    quantity,
    movement_type,
    created_by,
    transaction=None,
    transaction_detail=None,
    note="",
    insufficient_stock_message=None,
):
    """Atomically lock stock, apply one delta, and record its audit row."""
    stock_target = (
        Product.objects
        .select_for_update()
        .get(pk=product.pk)
    )

    new_stock = stock_target.stock + quantity

    if new_stock < 0:
        raise ValidationError({
            "details": (
                insufficient_stock_message
                or f"Stock insuficiente en {stock_target.title}."
            )
        })

    stock_target.stock = new_stock
    stock_target.save(update_fields=["stock"])

    return StockMovement.objects.create(
        product=stock_target,
        transaction=transaction,
        transaction_detail=transaction_detail,
        created_by=created_by,
        type=movement_type,
        quantity=quantity,
        note=note,
    )
