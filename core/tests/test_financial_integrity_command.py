from datetime import date
from decimal import Decimal
from io import StringIO
from unittest.mock import patch
from uuid import uuid4

from django.core.management import call_command
from django.core.management.base import CommandError
from django.db import connection
from django.test import TestCase
from django.test.utils import CaptureQueriesContext
from rest_framework import status
from rest_framework.test import APIClient

from core.models import DebtPayment, MonthlyClosure, PaymentMethod, Transaction
from core.services.financial_flows import (
    exclude_terminal_transactions,
    is_terminal_transaction_status,
    recognized_debt_payments,
)
from core.services.financial_integrity import (
    diagnose_financial_integrity,
    is_legacy_monthly_closure_snapshot,
)
from core.tests.factories import (
    create_business,
    create_debt,
    create_debt_payment,
    create_payment_method,
    create_status,
    create_transaction,
    create_user,
)


class FinancialIntegrityCommandTests(TestCase):
    def setUp(self):
        self.active = create_status("Activo")
        self.terminal = create_status("Anulado")
        self.owner = create_user(email="integrity-owner@playnow.test")
        self.business = create_business(user=self.owner, status=self.active)
        self.method = create_payment_method(
            business=self.business,
            status=self.active,
            method_type=PaymentMethod.TYPE_CASH,
        )
        self.client = APIClient()
        self.client.force_authenticate(self.owner)

    def current_monthly_summary(self):
        return {
            "debts": {
                "outstanding_receivables": "0.00",
                "outstanding_payables": "0.00",
                "outstanding_unclassified": "0.00",
            },
            "payments": {
                "received": "0.00",
                "made": "0.00",
                "debt_payments_received": "0.00",
                "debt_payments_made": "0.00",
            },
        }

    def build_debt(self, *, business=None, paid=Decimal("0.00")):
        business = business or self.business
        owner = business.user
        transaction = create_transaction(
            business=business,
            created_by=owner,
            status=self.active,
            total_value=Decimal("100.00"),
            is_debt=True,
        )
        debt = create_debt(
            transaction=transaction,
            total_amount=Decimal("100.00"),
            paid_amount=paid,
        )
        if paid > 0:
            transaction.payment_status = "partial"
            transaction.save(update_fields=["payment_status"])
        return transaction, debt

    def run_command(self, **options):
        stdout = StringIO()
        call_command("diagnose_financial_integrity", stdout=stdout, **options)
        return stdout.getvalue()

    def test_clean_database_returns_success_without_writes(self):
        self.build_debt()
        before = (
            Transaction.objects.count(),
            DebtPayment.objects.count(),
            PaymentMethod.objects.count(),
        )
        output = self.run_command()
        after = (
            Transaction.objects.count(),
            DebtPayment.objects.count(),
            PaymentMethod.objects.count(),
        )
        self.assertIn("INFO clean count=0", output)
        self.assertEqual(after, before)

    def test_errors_produce_nonzero_and_report_relational_categories(self):
        transaction, debt = self.build_debt()
        other_transaction = create_transaction(
            business=self.business,
            created_by=self.owner,
            status=self.active,
        )
        create_debt_payment(
            debt=debt,
            payment_method=self.method,
            amount=Decimal("10.00"),
            created_by=self.owner,
            transaction=other_transaction,
        )
        transaction.status = self.terminal
        transaction.save(update_fields=["status"])

        stdout = StringIO()
        with self.assertRaises(CommandError):
            call_command("diagnose_financial_integrity", stdout=stdout)
        output = stdout.getvalue()
        self.assertIn("ERROR debt_payment_transaction_mismatch", output)
        self.assertIn("ERROR debt_payment_terminal_transaction", output)
        self.assertIn("ERROR debt_payment_sum_mismatch", output)
        self.assertIn("WARNING terminal_transaction_pending_debt", output)

    def test_warnings_only_pass_unless_strict(self):
        transaction, debt = self.build_debt(paid=Decimal("10.00"))
        method = create_payment_method(
            business=self.business,
            status=self.active,
            method_type=PaymentMethod.TYPE_OTHER,
        )
        create_debt_payment(
            debt=debt,
            payment_method=method,
            amount=Decimal("10.00"),
            transaction=transaction,
        )
        output = self.run_command()
        self.assertIn("WARNING debt_payment_actor_missing", output)
        self.assertIn("WARNING payment_method_other_review", output)

        with self.assertRaises(CommandError):
            self.run_command(strict=True)

    def test_business_filter_and_unknown_business(self):
        foreign_owner = create_user(email="integrity-foreign@playnow.test")
        foreign_business = create_business(
            user=foreign_owner,
            status=self.active,
        )
        foreign_method = create_payment_method(
            business=foreign_business,
            status=self.active,
            method_type=PaymentMethod.TYPE_CASH,
        )
        foreign_transaction, foreign_debt = self.build_debt(
            business=foreign_business,
        )
        create_debt_payment(
            debt=foreign_debt,
            payment_method=foreign_method,
            amount=Decimal("1.00"),
            created_by=foreign_owner,
            transaction=foreign_transaction,
        )

        output = self.run_command(
            business_public_id=str(self.business.public_id),
        )
        self.assertIn("INFO clean count=0", output)
        with self.assertRaises(CommandError):
            self.run_command(business_public_id=str(uuid4()))

    def test_samples_are_limited_and_query_count_is_bounded(self):
        for index in range(12):
            transaction, debt = self.build_debt(paid=Decimal("1.00"))
            create_debt_payment(
                debt=debt,
                payment_method=self.method,
                amount=Decimal("1.00"),
                transaction=transaction,
            )

        with CaptureQueriesContext(connection) as queries:
            findings = diagnose_financial_integrity(
                business=self.business,
                sample_limit=3,
            )
        actor_warning = next(
            finding for finding in findings
            if finding.code == "debt_payment_actor_missing"
        )
        self.assertEqual(actor_warning.count, 12)
        self.assertEqual(len(actor_warning.sample_public_ids), 3)
        self.assertLessEqual(len(queries), 40)

    def test_diagnostic_remains_compatible_before_actor_column(self):
        self.build_debt()
        with connection.cursor() as cursor:
            legacy_columns = [
                column
                for column in connection.introspection.get_table_description(
                    cursor,
                    DebtPayment._meta.db_table,
                )
                if column.name != "created_by_id"
            ]

        with patch.object(
            connection.introspection,
            "get_table_description",
            return_value=legacy_columns,
        ):
            findings = diagnose_financial_integrity(
                business=self.business
            )

        actor_finding = next(
            finding
            for finding in findings
            if finding.code == "debt_payment_actor_column_pending"
        )
        self.assertEqual(actor_finding.severity, "INFO")

    def test_catalog_monthly_closure_and_invalid_type_are_classified(self):
        custom = create_payment_method(
            business=self.business,
            status=self.active,
            method_type=PaymentMethod.TYPE_OTHER,
        )
        PaymentMethod.objects.filter(pk=custom.pk).update(method_type="legacy")
        MonthlyClosure.objects.create(
            business=self.business,
            year=2026,
            month=7,
            version=1,
            status=MonthlyClosure.STATUS_CLOSED,
            summary={},
            closed_by=self.owner,
        )
        findings = diagnose_financial_integrity(business=self.business)
        codes = {finding.code: finding.severity for finding in findings}
        self.assertEqual(codes["payment_method_type_invalid"], "ERROR")
        self.assertEqual(codes["historical_monthly_closure_review"], "WARNING")

    def test_terminal_status_variants_are_consistent_across_consumers(self):
        terminal_names = (
            "Anulado",
            "anulado",
            "ANULADO",
            "aNuLaDo",
            "Cancelado",
            "CANCELADO",
            "Eliminado",
            "VOID",
            "deleted",
        )

        for index, name in enumerate(terminal_names, start=1):
            with self.subTest(name=name):
                terminal_status = create_status(name)
                transaction, debt = self.build_debt()
                transaction.status = terminal_status
                transaction.save(update_fields=["status"])

                self.assertTrue(
                    is_terminal_transaction_status(transaction.status)
                )
                self.assertFalse(
                    exclude_terminal_transactions(
                        Transaction.objects.filter(pk=transaction.pk)
                    ).exists()
                )

                payment_response = self.client.post(
                    "/api/debt-payments/",
                    {
                        "debt_public_id": str(debt.public_id),
                        "amount": "1.00",
                        "payment_date": date.today().isoformat(),
                        "payment_method_public_id": str(
                            self.method.public_id
                        ),
                    },
                    format="json",
                )
                self.assertEqual(
                    payment_response.status_code,
                    status.HTTP_409_CONFLICT,
                )
                delete_response = self.client.delete(
                    f"/api/transactions/{transaction.public_id}/"
                )
                self.assertEqual(
                    delete_response.status_code,
                    status.HTTP_409_CONFLICT,
                )

                payment = create_debt_payment(
                    debt=debt,
                    payment_method=self.method,
                    amount=Decimal("1.00"),
                    created_by=self.owner,
                    transaction=transaction,
                )
                self.assertFalse(
                    recognized_debt_payments(
                        DebtPayment.objects.filter(pk=payment.pk)
                    ).exists()
                )
                finding = next(
                    item
                    for item in diagnose_financial_integrity(
                        business=self.business
                    )
                    if item.code == "debt_payment_terminal_transaction"
                )
                self.assertEqual(finding.count, index)
                pending_finding = next(
                    item
                    for item in diagnose_financial_integrity(
                        business=self.business
                    )
                    if item.code == "terminal_transaction_pending_debt"
                )
                self.assertEqual(pending_finding.count, index)

        self.assertFalse(
            is_terminal_transaction_status("Anulado temporal")
        )

    def test_terminal_diagnostic_remains_business_scoped(self):
        foreign_owner = create_user(
            email="terminal-foreign@playnow.test"
        )
        foreign_business = create_business(
            user=foreign_owner,
            status=self.active,
        )
        foreign_method = create_payment_method(
            business=foreign_business,
            status=self.active,
            method_type=PaymentMethod.TYPE_CASH,
        )
        transaction, debt = self.build_debt(business=foreign_business)
        transaction.status = create_status("aNuLaDo")
        transaction.save(update_fields=["status"])
        create_debt_payment(
            debt=debt,
            payment_method=foreign_method,
            amount=Decimal("1.00"),
            created_by=foreign_owner,
            transaction=transaction,
        )

        codes = {
            finding.code
            for finding in diagnose_financial_integrity(
                business=self.business
            )
        }
        self.assertNotIn("debt_payment_terminal_transaction", codes)
        self.assertNotIn("terminal_transaction_pending_debt", codes)

    def test_monthly_snapshot_classifier_handles_current_and_bad_json(self):
        self.assertFalse(
            is_legacy_monthly_closure_snapshot(
                self.current_monthly_summary()
            )
        )
        for snapshot in (
            {},
            [],
            "damaged",
            {
                "debts": {"outstanding_at_period_end": "0.00"},
                "payments": {"total": "0.00"},
            },
            {"payments": {}},
            {"debts": {}, "payments": {}},
        ):
            with self.subTest(snapshot=snapshot):
                self.assertTrue(
                    is_legacy_monthly_closure_snapshot(snapshot)
                )

    def test_monthly_closure_warning_counts_only_legacy_snapshots(self):
        for version, summary in enumerate(
            ({}, "damaged", self.current_monthly_summary()),
            start=1,
        ):
            MonthlyClosure.objects.create(
                business=self.business,
                year=2026,
                month=6,
                version=version,
                status=MonthlyClosure.STATUS_REOPENED,
                summary=summary,
                closed_by=self.owner,
            )

        finding = next(
            item
            for item in diagnose_financial_integrity(
                business=self.business
            )
            if item.code == "historical_monthly_closure_review"
        )
        self.assertEqual(finding.count, 2)

    def test_legacy_monthly_closure_warning_fails_only_in_strict_mode(self):
        MonthlyClosure.objects.create(
            business=self.business,
            year=2026,
            month=6,
            version=1,
            status=MonthlyClosure.STATUS_CLOSED,
            summary={
                "debts": {"outstanding_at_period_end": "0.00"},
                "payments": {"total": "0.00"},
            },
            closed_by=self.owner,
        )
        output = self.run_command(
            business_public_id=str(self.business.public_id)
        )
        self.assertIn("WARNING historical_monthly_closure_review", output)
        with self.assertRaises(CommandError):
            self.run_command(
                strict=True,
                business_public_id=str(self.business.public_id),
            )

    def test_monthly_closure_classification_is_business_scoped(self):
        foreign_owner = create_user(
            email="closure-foreign@playnow.test"
        )
        foreign_business = create_business(
            user=foreign_owner,
            status=self.active,
        )
        MonthlyClosure.objects.create(
            business=foreign_business,
            year=2026,
            month=6,
            version=1,
            status=MonthlyClosure.STATUS_CLOSED,
            summary={},
            closed_by=foreign_owner,
        )
        MonthlyClosure.objects.create(
            business=self.business,
            year=2026,
            month=6,
            version=1,
            status=MonthlyClosure.STATUS_CLOSED,
            summary=self.current_monthly_summary(),
            closed_by=self.owner,
        )

        codes = {
            finding.code
            for finding in diagnose_financial_integrity(
                business=self.business
            )
        }
        self.assertNotIn("historical_monthly_closure_review", codes)
