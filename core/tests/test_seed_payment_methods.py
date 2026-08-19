from io import StringIO
from unittest.mock import patch
from uuid import uuid4

from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import TestCase

from core.management.commands.seed_payment_methods import (
    CANONICAL_PAYMENT_METHODS,
)
from core.models import PaymentMethod
from core.tests.factories import (
    create_business,
    create_payment_method,
    create_status,
    create_user,
)


class SeedPaymentMethodsTests(TestCase):
    def setUp(self):
        self.active = create_status("Activo")
        self.inactive = create_status("Inactivo")
        self.owner = create_user(email="seed-owner@playnow.test")
        self.business = create_business(user=self.owner, status=self.active)

    def run_seed(self, business=None, **options):
        stdout = StringIO()
        call_command(
            "seed_payment_methods",
            business_public_id=str((business or self.business).public_id),
            stdout=stdout,
            **options,
        )
        return stdout.getvalue()

    def test_business_is_required_and_must_exist(self):
        with self.assertRaises(CommandError):
            call_command("seed_payment_methods")
        with self.assertRaises(CommandError):
            call_command(
                "seed_payment_methods",
                business_public_id=str(uuid4()),
            )

    def test_active_status_is_required_without_partial_writes(self):
        self.active.name = "Disponible"
        self.active.save(update_fields=["name"])
        with self.assertRaises(CommandError):
            self.run_seed()
        self.assertFalse(
            PaymentMethod.objects.filter(business=self.business).exists()
        )

    def test_first_and_second_run_are_typed_and_idempotent(self):
        first = self.run_seed()
        self.assertIn(f"created={len(CANONICAL_PAYMENT_METHODS)}", first)
        methods = PaymentMethod.objects.filter(business=self.business)
        self.assertEqual(methods.count(), len(CANONICAL_PAYMENT_METHODS))
        self.assertEqual(
            {method.name: method.method_type for method in methods},
            dict(CANONICAL_PAYMENT_METHODS),
        )
        self.assertTrue(all(method.status == self.active for method in methods))

        second = self.run_seed()
        self.assertIn(f"unchanged={len(CANONICAL_PAYMENT_METHODS)}", second)
        self.assertEqual(methods.count(), len(CANONICAL_PAYMENT_METHODS))

    def test_dry_run_reports_without_writing(self):
        output = self.run_seed(dry_run=True)
        self.assertIn("dry_run=true", output)
        self.assertIn(f"created={len(CANONICAL_PAYMENT_METHODS)}", output)
        self.assertFalse(
            PaymentMethod.objects.filter(business=self.business).exists()
        )

    def test_only_exact_canonical_other_is_corrected(self):
        canonical = create_payment_method(
            business=self.business,
            status=self.inactive,
            name="Tarjeta",
            method_type=PaymentMethod.TYPE_OTHER,
        )
        custom = create_payment_method(
            business=self.business,
            status=self.active,
            name="Tarjeta corporativa",
            method_type=PaymentMethod.TYPE_OTHER,
        )
        output = self.run_seed()
        canonical.refresh_from_db()
        custom.refresh_from_db()
        self.assertIn("updated=1", output)
        self.assertEqual(canonical.method_type, PaymentMethod.TYPE_CARD)
        self.assertEqual(canonical.status, self.inactive)
        self.assertEqual(custom.method_type, PaymentMethod.TYPE_OTHER)

    def test_non_other_conflict_is_skipped(self):
        method = create_payment_method(
            business=self.business,
            status=self.active,
            name="Efectivo",
            method_type=PaymentMethod.TYPE_CARD,
        )
        output = self.run_seed()
        method.refresh_from_db()
        self.assertIn("skipped=1", output)
        self.assertIn("conflict name=Efectivo", output)
        self.assertEqual(method.method_type, PaymentMethod.TYPE_CARD)

    def test_failure_rolls_back_all_creations(self):
        original_create = PaymentMethod.objects.create

        def fail_on_transfer(**kwargs):
            if kwargs.get("name") == "Transferencia":
                raise RuntimeError("forced seed failure")
            return original_create(**kwargs)

        with patch.object(
            PaymentMethod.objects,
            "create",
            side_effect=fail_on_transfer,
        ):
            with self.assertRaises(RuntimeError):
                self.run_seed()
        self.assertFalse(
            PaymentMethod.objects.filter(business=self.business).exists()
        )

    def test_business_a_does_not_affect_business_b(self):
        other_owner = create_user(email="seed-other-owner@playnow.test")
        other_business = create_business(
            user=other_owner,
            status=self.active,
        )
        custom = create_payment_method(
            business=other_business,
            status=self.inactive,
            name="Tarjeta",
            method_type=PaymentMethod.TYPE_OTHER,
        )
        self.run_seed(self.business)
        custom.refresh_from_db()
        self.assertEqual(custom.method_type, PaymentMethod.TYPE_OTHER)
        self.assertEqual(custom.status, self.inactive)
