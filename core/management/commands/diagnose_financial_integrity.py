from django.core.management.base import BaseCommand, CommandError
from uuid import UUID

from core.models import Business
from core.services.financial_integrity import diagnose_financial_integrity


class Command(BaseCommand):
    help = "Diagnostica la integridad financiera sin modificar datos."

    def add_arguments(self, parser):
        parser.add_argument("--strict", action="store_true")
        parser.add_argument("--business-public-id")

    def handle(self, *args, **options):
        business = None
        business_public_id = options.get("business_public_id")
        if business_public_id:
            try:
                business_public_id = UUID(str(business_public_id))
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

        findings = diagnose_financial_integrity(business=business)
        scope = str(business.public_id) if business else "all"
        self.stdout.write(f"Financial integrity scope={scope}")
        if not findings:
            self.stdout.write("INFO clean count=0")
            return

        for finding in findings:
            samples = ",".join(finding.sample_public_ids) or "-"
            self.stdout.write(
                f"{finding.severity} {finding.code} count={finding.count} "
                f"samples={samples} message={finding.message}"
            )

        error_count = sum(
            finding.count
            for finding in findings
            if finding.severity == "ERROR"
        )
        warning_count = sum(
            finding.count
            for finding in findings
            if finding.severity == "WARNING"
        )
        self.stdout.write(
            f"SUMMARY errors={error_count} warnings={warning_count}"
        )
        if error_count or (options["strict"] and warning_count):
            raise CommandError(
                "El diagnóstico financiero detectó inconsistencias."
            )
