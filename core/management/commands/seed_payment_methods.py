from django.core.management.base import BaseCommand
from django.db import transaction
from core.models import PaymentMethod

DEFAULTS = [
    "Efectivo",
    "Tarjeta",
    "Transferencia",
    "Cheque",
    "POS",
    "Crédito",
    "PayPal",
]

class Command(BaseCommand):
    help = "Crea métodos de pago por defecto si no existen"

    @transaction.atomic
    def handle(self, *args, **options):
        created = 0
        for name in DEFAULTS:
            _, was_created = PaymentMethod.objects.get_or_create(name=name)
            if was_created:
                created += 1
        self.stdout.write(self.style.SUCCESS(f"PaymentMethods creados: {created}, existentes: {len(DEFAULTS) - created}"))