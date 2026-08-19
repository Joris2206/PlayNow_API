from django.core.management.base import BaseCommand, CommandError
from django.db import transaction
from uuid import UUID

from core.models import Business, EntityStatus, PaymentMethod


CANONICAL_PAYMENT_METHODS = (
    ("Efectivo", PaymentMethod.TYPE_CASH),
    ("Tarjeta", PaymentMethod.TYPE_CARD),
    ("Transferencia", PaymentMethod.TYPE_TRANSFER),
    ("Cheque", PaymentMethod.TYPE_OTHER),
    ("POS", PaymentMethod.TYPE_CARD),
    ("Crédito", PaymentMethod.TYPE_OTHER),
    ("PayPal", PaymentMethod.TYPE_OTHER),
)


class Command(BaseCommand):
    help = "Crea métodos de pago canónicos para un Business específico."

    def add_arguments(self, parser):
        parser.add_argument("--business-public-id", required=True)
        parser.add_argument("--dry-run", action="store_true")

    @transaction.atomic
    def handle(self, *args, **options):
        try:
            business_public_id = UUID(str(options["business_public_id"]))
        except (TypeError, ValueError):
            raise CommandError(
                "El business-public-id debe ser un UUID válido."
            )
        business = Business.objects.filter(
            public_id=business_public_id,
        ).first()
        if business is None:
            raise CommandError(
                "No existe un Business con el public_id indicado."
            )

        active_status = EntityStatus.objects.filter(
            name__iexact="Activo",
        ).first()
        if active_status is None:
            raise CommandError(
                "No existe el estado Activo. Ejecute seed_statuses primero."
            )

        dry_run = options["dry_run"]
        counts = {
            "created": 0,
            "updated": 0,
            "unchanged": 0,
            "skipped": 0,
        }

        for name, method_type in CANONICAL_PAYMENT_METHODS:
            existing = PaymentMethod.objects.filter(
                business=business,
                name__iexact=name,
            ).first()
            if existing is None:
                counts["created"] += 1
                if not dry_run:
                    PaymentMethod.objects.create(
                        business=business,
                        name=name,
                        method_type=method_type,
                        status=active_status,
                    )
                continue

            if existing.method_type == method_type:
                counts["unchanged"] += 1
                continue

            if (
                existing.method_type == PaymentMethod.TYPE_OTHER
                and method_type != PaymentMethod.TYPE_OTHER
            ):
                counts["updated"] += 1
                if not dry_run:
                    existing.method_type = method_type
                    existing.save(
                        update_fields=["method_type", "updated_at"],
                    )
                continue

            counts["skipped"] += 1
            self.stdout.write(
                self.style.WARNING(
                    f"conflict name={name} existing_type="
                    f"{existing.method_type} expected_type={method_type}"
                )
            )

        self.stdout.write(
            f"Business={business.public_id} dry_run={str(dry_run).lower()} "
            f"created={counts['created']} updated={counts['updated']} "
            f"unchanged={counts['unchanged']} skipped={counts['skipped']}"
        )
