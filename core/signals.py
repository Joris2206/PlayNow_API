# core/signals.py
from django.db.models.signals import post_save
from django.dispatch import receiver
from django.db.models import F, Sum
from .models import StockMovement, ProductVariant, Product

@receiver(post_save, sender=StockMovement)
def update_stock_counters(sender, instance: StockMovement, created, **kwargs):
    """
    Movimientos son append-only (tu lógica crea ajustes en updates/anulaciones),
    así que solo actuamos al crear. 'quantity' ya viene con signo:
      - sale: negativo
      - entry: positivo
      - adjustment: +/- según corresponda
    """
    if not created:
        return

    delta = instance.quantity
    product = instance.product
    variant = instance.variant

    if not product:
        return

    if variant:
        ProductVariant.objects.filter(pk=variant.pk).update(stock=F('stock') + delta)
        total = (ProductVariant.objects
                 .filter(variant_type__product_id=product.pk)
                 .aggregate(total=Sum('stock'))['total'] or 0)
        Product.objects.filter(pk=product.pk).update(stock=total)
    else:
        Product.objects.filter(pk=product.pk).update(stock=F('stock') + delta)
