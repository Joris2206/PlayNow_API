from django.db import transaction as db_tx
from rest_framework.exceptions import ValidationError

from core.models import Product, StockMovement


def lock_products_for_inventory(
    *,
    product_ids,
    business_id,
    require_active=False,
):
    """Lock a unique product set in the global deterministic PK order."""
    ordered_ids = sorted(set(product_ids))
    queryset = Product.objects.select_for_update(of=("self",)).filter(
        pk__in=ordered_ids,
        business_id=business_id,
    )
    if require_active:
        queryset = queryset.filter(status__name__iexact="Activo")

    locked_products = list(queryset.order_by("pk"))
    if len(locked_products) != len(ordered_ids):
        raise ValidationError({
            "details": (
                "Uno o más productos no son válidos, no pertenecen "
                "al negocio o no se encuentran Activos."
            ),
        })

    return {
        product.pk: product
        for product in locked_products
    }


def record_locked_stock_movement(
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
    """Apply a stock delta to a Product already locked by the caller."""
    new_stock = product.stock + quantity

    if new_stock < 0:
        raise ValidationError({
            "details": (
                insufficient_stock_message
                or f"Stock insuficiente en {product.title}."
            )
        })

    product.stock = new_stock
    product.save(update_fields=["stock"])

    return StockMovement.objects.create(
        product=product,
        transaction=transaction,
        transaction_detail=transaction_detail,
        created_by=created_by,
        type=movement_type,
        quantity=quantity,
        note=note,
    )


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
        .select_for_update(of=("self",))
        .get(pk=product.pk)
    )

    return record_locked_stock_movement(
        product=stock_target,
        quantity=quantity,
        movement_type=movement_type,
        created_by=created_by,
        transaction=transaction,
        transaction_detail=transaction_detail,
        note=note,
        insufficient_stock_message=insufficient_stock_message,
    )


@db_tx.atomic
def adjust_product_stock(
    *,
    product_id,
    business_id,
    quantity,
    note,
    created_by,
):
    """Apply one authorized manual adjustment and return its stock bounds."""
    product = (
        Product.objects
        .select_for_update(of=("self",))
        .get(
            pk=product_id,
            business_id=business_id,
        )
    )

    if product.status.name.casefold() != "activo".casefold():
        raise ValidationError({
            "product": [
                "El producto debe estar Activo para ajustar su stock."
            ],
        })

    if quantity == 0:
        raise ValidationError({
            "quantity": ["La cantidad debe ser distinta de cero."],
        })

    previous_stock = product.stock
    new_stock = previous_stock + quantity

    if new_stock < 0:
        raise ValidationError({
            "quantity": [
                "El ajuste dejaría el stock en un valor negativo."
            ],
        })

    movement = record_locked_stock_movement(
        product=product,
        quantity=quantity,
        movement_type="adjustment",
        created_by=created_by,
        note=note,
    )

    return {
        "previous_stock": previous_stock,
        "new_stock": new_stock,
        "movement": movement,
    }
