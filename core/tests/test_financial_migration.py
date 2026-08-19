from datetime import date
from decimal import Decimal

from django.db import connection
from django.db.migrations.executor import MigrationExecutor
from django.db.migrations.recorder import MigrationRecorder
from django.test import TransactionTestCase


class FinancialHardeningMigrationTests(TransactionTestCase):
    reset_sequences = True
    migrate_from = [("core", "0002_remove_productvariant_status_and_more")]
    migrate_to = [("core", "0003_harden_debt_audit_constraints")]

    def _migrate(self, targets):
        executor = MigrationExecutor(connection)
        executor.migrate(targets)
        return executor

    def _apps_at(self, executor, targets):
        return executor.loader.project_state(targets).apps

    def _historical_models(self, apps):
        return {
            name: apps.get_model("core", name)
            for name in (
                "User",
                "EntityStatus",
                "Business",
                "PaymentMethod",
                "Transaction",
                "Debt",
                "DebtPayment",
            )
        }

    def _create_base_graph(self, apps, *, suffix):
        models = self._historical_models(apps)
        user = models["User"].objects.create(
            password="!",
            full_name=f"Migration User {suffix}",
            phone="",
            email=f"migration-{suffix}@playnow.test",
            role="business_owner",
            is_active=True,
            is_staff=False,
            is_superuser=False,
        )
        status = models["EntityStatus"].objects.create(name="Activo")
        business = models["Business"].objects.create(
            business_name=f"Migration Business {suffix}",
            description="",
            currency="NIO",
            user=user,
            status=status,
        )
        method = models["PaymentMethod"].objects.create(
            name="Efectivo",
            method_type="cash",
            business=business,
            status=status,
        )
        return models, user, status, business, method

    def _create_transaction(
        self,
        models,
        *,
        user,
        status,
        business,
        total_value,
        payment_status,
    ):
        return models["Transaction"].objects.create(
            type="sale",
            is_debt=True,
            discount_percent=None,
            concept="Historical migration transaction",
            total_value=total_value,
            invoice_number=None,
            payment_status=payment_status,
            invoice_series=None,
            invoice_file_url="",
            business=business,
            created_by=user,
            customer=None,
            employee=None,
            payment_method=None,
            status=status,
            supplier=None,
            updated_by=None,
        )

    def _schema_state(self):
        with connection.cursor() as cursor:
            columns = {
                column.name
                for column in connection.introspection.get_table_description(
                    cursor,
                    "core_debtpayment",
                )
            }
            constraints = connection.introspection.get_constraints(
                cursor,
                "core_debt",
            )
        return columns, constraints

    def test_valid_historical_data_migrates_reverses_and_reapplies(self):
        try:
            executor = self._migrate(self.migrate_from)
            old_apps = self._apps_at(executor, self.migrate_from)
            models, user, status, business, method = (
                self._create_base_graph(old_apps, suffix="valid")
            )

            pending_transaction = self._create_transaction(
                models,
                user=user,
                status=status,
                business=business,
                total_value=Decimal("100.00"),
                payment_status="pending",
            )
            models["Debt"].objects.create(
                transaction=pending_transaction,
                total_amount=Decimal("100.00"),
                paid_amount=Decimal("0.00"),
                interest_rate=Decimal("0.00"),
                term_months=0,
                due_date=date(2026, 8, 18),
                is_settled=False,
            )

            partial_transaction = self._create_transaction(
                models,
                user=user,
                status=status,
                business=business,
                total_value=Decimal("100.00"),
                payment_status="partial",
            )
            partial_debt = models["Debt"].objects.create(
                transaction=partial_transaction,
                total_amount=Decimal("100.00"),
                paid_amount=Decimal("10.00"),
                interest_rate=Decimal("0.00"),
                term_months=0,
                due_date=date(2026, 8, 18),
                is_settled=False,
            )
            payment = models["DebtPayment"].objects.create(
                debt=partial_debt,
                amount=Decimal("10.00"),
                payment_date=date(2026, 8, 18),
                transaction=partial_transaction,
                payment_method=method,
            )

            executor = self._migrate(self.migrate_to)
            new_apps = self._apps_at(executor, self.migrate_to)
            HistoricalDebtPayment = new_apps.get_model(
                "core",
                "DebtPayment",
            )
            self.assertIsNone(
                HistoricalDebtPayment.objects.get(pk=payment.pk).created_by_id
            )
            columns, constraints = self._schema_state()
            self.assertIn("created_by_id", columns)
            self.assertIn("debt_total_amount_gt_0", constraints)
            self.assertIn(
                "debt_settlement_matches_paid_amount",
                constraints,
            )

            self._migrate(self.migrate_from)
            columns, constraints = self._schema_state()
            self.assertNotIn("created_by_id", columns)
            self.assertIn("debt_total_amount_gte_0", constraints)
            self.assertNotIn("debt_total_amount_gt_0", constraints)
            self.assertNotIn(
                "debt_settlement_matches_paid_amount",
                constraints,
            )

            self._migrate(self.migrate_to)
            columns, constraints = self._schema_state()
            self.assertIn("created_by_id", columns)
            self.assertIn("debt_total_amount_gt_0", constraints)
        finally:
            self._migrate(self.migrate_to)

    def test_invalid_historical_data_blocks_without_partial_schema(self):
        HistoricalDebt = None
        invalid_debt_pk = None
        try:
            executor = self._migrate(self.migrate_from)
            old_apps = self._apps_at(executor, self.migrate_from)
            models, user, status, business, _ = self._create_base_graph(
                old_apps,
                suffix="invalid",
            )
            invalid_transaction = self._create_transaction(
                models,
                user=user,
                status=status,
                business=business,
                total_value=Decimal("0.00"),
                payment_status="pending",
            )
            invalid_debt = models["Debt"].objects.create(
                transaction=invalid_transaction,
                total_amount=Decimal("0.00"),
                paid_amount=Decimal("0.00"),
                interest_rate=Decimal("0.00"),
                term_months=0,
                due_date=date(2026, 8, 18),
                is_settled=False,
            )
            HistoricalDebt = models["Debt"]
            invalid_debt_pk = invalid_debt.pk

            with self.assertRaisesRegex(
                RuntimeError,
                "total_amount_nonpositive=1.*diagnose_financial_integrity",
            ):
                self._migrate(self.migrate_to)

            columns, constraints = self._schema_state()
            self.assertNotIn("created_by_id", columns)
            self.assertIn("debt_total_amount_gte_0", constraints)
            self.assertNotIn("debt_total_amount_gt_0", constraints)
            self.assertNotIn(
                "debt_settlement_matches_paid_amount",
                constraints,
            )
            self.assertNotIn(
                self.migrate_to[0],
                MigrationRecorder(connection).applied_migrations(),
            )

            models["Debt"].objects.filter(pk=invalid_debt.pk).delete()
            self._migrate(self.migrate_to)
            columns, constraints = self._schema_state()
            self.assertIn("created_by_id", columns)
            self.assertIn("debt_total_amount_gt_0", constraints)
        finally:
            if HistoricalDebt is not None and invalid_debt_pk is not None:
                HistoricalDebt.objects.filter(pk=invalid_debt_pk).delete()
            self._migrate(self.migrate_to)
