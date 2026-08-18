from datetime import date, datetime, timezone
from decimal import Decimal

from django.test import TestCase
from drf_spectacular.generators import SchemaGenerator
from rest_framework import status

from core.models import (
    BusinessMembership,
    DebtPayment,
    MonthlyClosure,
    PaymentMethod,
    Transaction,
)
from core.services.dashboard import build_dashboard_overview
from core.services.monthly_summary import build_monthly_summary
from core.services.payment_debt_reports import build_payments_summary
from core.services.payment_debt_reports import build_debts_summary
from core.tests.factories import (
    create_business,
    create_cash_register,
    create_customer,
    create_debt,
    create_debt_payment,
    create_employee,
    create_payment_method,
    create_role_user,
    create_status,
    create_supplier,
    create_transaction,
    create_user,
)
from core.views import calculate_cash_register_summary
from core.tests.base import BusinessIsolationTestCase


class FinancialFlowRecognitionTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.status = create_status()
        cls.user = create_user()
        cls.business = create_business(user=cls.user, status=cls.status)
        cls.employee = create_employee(business=cls.business, status=cls.status)
        cls.customer = create_customer(business=cls.business, status=cls.status)
        cls.supplier = create_supplier(business=cls.business, status=cls.status)
        cls.cash = create_payment_method(
            business=cls.business,
            status=cls.status,
            name="Caja principal",
            method_type=PaymentMethod.TYPE_CASH,
        )
        cls.named_cash = create_payment_method(
            business=cls.business,
            status=cls.status,
            name="Efectivo",
            method_type=PaymentMethod.TYPE_OTHER,
        )

    def create_transaction(self, transaction_type, amount, *, method=None, debt=False):
        return create_transaction(
            business=self.business,
            created_by=self.user,
            payment_method=method,
            status=self.status,
            transaction_type=transaction_type,
            total_value=amount,
            is_debt=debt,
            created_at=datetime(2026, 8, 10, 12, tzinfo=timezone.utc),
        )

    def add_payment(self, transaction, amount, *, method=None, settle=False):
        debt = create_debt(transaction=transaction, total_amount=transaction.total_value)
        payment = create_debt_payment(
            debt=debt,
            payment_method=method or self.cash,
            amount=amount,
        )
        DebtPayment.objects.filter(pk=payment.pk).update(
            payment_date=date(2026, 8, 15),
            created_at=datetime(2026, 8, 15, 12, tzinfo=timezone.utc),
        )
        if settle:
            debt.paid_amount = transaction.total_value
            debt.is_settled = True
            debt.save(update_fields=("paid_amount", "is_settled"))
            Transaction.objects.filter(pk=transaction.pk).update(
                payment_status="paid",
                is_debt=False,
            )
        return debt

    def build_flow_matrix(self):
        self.create_transaction("sale", Decimal("100.00"), method=self.cash)
        self.create_transaction("purchase", Decimal("40.00"), method=self.cash)
        self.create_transaction("expense", Decimal("10.00"), method=self.cash)

        settled_sale = self.create_transaction(
            "sale", Decimal("200.00"), method=self.cash, debt=True,
        )
        self.add_payment(settled_sale, Decimal("200.00"), settle=True)

        purchase_debt = self.create_transaction(
            "purchase", Decimal("90.00"), debt=True,
        )
        self.add_payment(purchase_debt, Decimal("30.00"))

        unsupported_expense = self.create_transaction(
            "expense", Decimal("25.00"), debt=True,
        )
        self.add_payment(unsupported_expense, Decimal("5.00"))

    def test_reports_recognize_each_payment_once_with_correct_direction(self):
        self.build_flow_matrix()

        summary = build_payments_summary(
            business=self.business,
            date_from=date(2026, 8, 1),
            date_to=date(2026, 8, 31),
        )

        self.assertEqual(summary["totals"]["incoming_total"], "300.00")
        self.assertEqual(summary["totals"]["outgoing_total"], "80.00")
        self.assertEqual(summary["totals"]["debt_payments_received"]["total"], "200.00")
        self.assertEqual(summary["totals"]["debt_payments_made"]["total"], "30.00")

    def test_dashboard_and_monthly_summary_share_payment_semantics(self):
        self.build_flow_matrix()

        with self.assertNumQueries(16):
            dashboard = build_dashboard_overview(
                business=self.business,
                date_from=date(2026, 8, 1),
                date_to=date(2026, 8, 31),
            )
        with self.assertNumQueries(20):
            monthly = build_monthly_summary(business=self.business, year=2026, month=8)

        self.assertEqual(dashboard["cards"]["payments_received"], "300.00")
        self.assertEqual(dashboard["cards"]["payments_made"], "80.00")
        self.assertEqual(dashboard["cards"]["outstanding_receivables"], "0.00")
        self.assertEqual(dashboard["cards"]["outstanding_payables"], "60.00")
        self.assertEqual(monthly["payments"]["received"], "300.00")
        self.assertEqual(monthly["payments"]["made"], "80.00")
        self.assertEqual(monthly["debts"]["outstanding_receivables"], "0.00")
        self.assertEqual(monthly["debts"]["outstanding_payables"], "60.00")
        self.assertEqual(monthly["transactions"]["sales"]["total"], "300.00")

    def test_cash_summary_uses_method_type_and_debt_payment_direction(self):
        register = create_cash_register(
            business=self.business,
            employee=self.employee,
            opened_by=self.user,
            opening_balance=Decimal("1000.00"),
            open_time=datetime(2026, 8, 1, 8, tzinfo=timezone.utc),
        )
        self.create_transaction("sale", Decimal("100.00"), method=self.cash)
        self.create_transaction("sale", Decimal("50.00"), method=self.named_cash)
        sale_debt = self.create_transaction("sale", Decimal("20.00"), debt=True)
        self.add_payment(sale_debt, Decimal("20.00"))
        purchase_debt = self.create_transaction("purchase", Decimal("10.00"), debt=True)
        self.add_payment(purchase_debt, Decimal("10.00"))

        summary = calculate_cash_register_summary(
            register,
            until=datetime(2026, 8, 31, 20, tzinfo=timezone.utc),
        )

        self.assertEqual(summary["sales"]["cash"], Decimal("100.00"))
        self.assertEqual(summary["sales"]["other"], Decimal("50.00"))
        self.assertEqual(summary["cash_debt_payments_received"], Decimal("20.00"))
        self.assertEqual(summary["cash_debt_payments_made"], Decimal("10.00"))
        self.assertEqual(summary["cash_debt_payments"], Decimal("30.00"))
        self.assertEqual(summary["expected_closing_balance"], Decimal("1110.00"))

    def test_all_method_types_are_reported_but_only_cash_affects_register(self):
        methods = [self.cash]
        for method_type in (
            PaymentMethod.TYPE_CARD,
            PaymentMethod.TYPE_TRANSFER,
            PaymentMethod.TYPE_OTHER,
        ):
            methods.append(create_payment_method(
                business=self.business,
                status=self.status,
                name=f"Metodo {method_type}",
                method_type=method_type,
            ))

        register = create_cash_register(
            business=self.business,
            employee=self.employee,
            opened_by=self.user,
            opening_balance=Decimal("0.00"),
            open_time=datetime(2026, 8, 1, 8, tzinfo=timezone.utc),
        )
        for method in methods:
            self.create_transaction("sale", Decimal("10.00"), method=method)
            debt_sale = self.create_transaction("sale", Decimal("5.00"), debt=True)
            self.add_payment(debt_sale, Decimal("5.00"), method=method)

        report = build_payments_summary(
            business=self.business,
            date_from=date(2026, 8, 1),
            date_to=date(2026, 8, 31),
        )
        by_type = {
            row["payment_method"]["method_type"]: row
            for row in report["results"]
        }
        for method_type in (
            PaymentMethod.TYPE_CASH,
            PaymentMethod.TYPE_CARD,
            PaymentMethod.TYPE_TRANSFER,
            PaymentMethod.TYPE_OTHER,
        ):
            self.assertEqual(by_type[method_type]["total_incoming"], "15.00")

        cash = calculate_cash_register_summary(
            register,
            until=datetime(2026, 8, 31, 20, tzinfo=timezone.utc),
        )
        self.assertEqual(cash["expected_closing_balance"], Decimal("15.00"))
        self.assertEqual(cash["cash_debt_payments"], Decimal("5.00"))

    def test_payment_date_controls_reports_and_created_at_controls_cash_session(self):
        register = create_cash_register(
            business=self.business,
            employee=self.employee,
            opened_by=self.user,
            opening_balance=Decimal("0.00"),
            open_time=datetime(2026, 8, 1, 8, tzinfo=timezone.utc),
        )
        transaction = self.create_transaction("sale", Decimal("25.00"), debt=True)
        debt = self.add_payment(transaction, Decimal("25.00"))
        debt.payments.update(payment_date=date(2026, 9, 2))

        august = build_payments_summary(
            business=self.business,
            date_from=date(2026, 8, 1),
            date_to=date(2026, 8, 31),
        )
        september = build_payments_summary(
            business=self.business,
            date_from=date(2026, 9, 1),
            date_to=date(2026, 9, 30),
        )
        cash = calculate_cash_register_summary(
            register,
            until=datetime(2026, 8, 31, 20, tzinfo=timezone.utc),
        )

        self.assertEqual(august["totals"]["payments_received"], "0.00")
        self.assertEqual(september["totals"]["payments_received"], "25.00")
        self.assertEqual(cash["cash_debt_payments_received"], Decimal("25.00"))

    def test_terminal_transaction_and_its_payments_are_excluded_together(self):
        terminal = create_status("Anulado")
        direct = self.create_transaction("sale", Decimal("100.00"), method=self.cash)
        debt_transaction = self.create_transaction("sale", Decimal("40.00"), debt=True)
        self.add_payment(debt_transaction, Decimal("20.00"))
        Transaction.objects.filter(pk__in=(direct.pk, debt_transaction.pk)).update(status=terminal)

        report = build_payments_summary(
            business=self.business,
            date_from=date(2026, 8, 1),
            date_to=date(2026, 8, 31),
        )

        self.assertEqual(report["totals"]["payments_received"], "0.00")
        self.assertEqual(report["totals"]["debt_payments"]["count"], 0)

    def test_building_current_summary_does_not_rewrite_historical_closure(self):
        historical = MonthlyClosure.objects.create(
            business=self.business,
            year=2026,
            month=7,
            summary={"legacy": "unchanged"},
            closed_by=self.user,
        )

        build_monthly_summary(business=self.business, year=2026, month=8)
        historical.refresh_from_db()

        self.assertEqual(historical.summary, {"legacy": "unchanged"})

    def test_debt_report_separates_receivable_payable_and_unclassified(self):
        sale = create_transaction(
            business=self.business,
            created_by=self.user,
            customer=self.customer,
            status=self.status,
            transaction_type="sale",
            total_value=Decimal("100.00"),
            is_debt=True,
            created_at=datetime(2026, 8, 10, 12, tzinfo=timezone.utc),
        )
        purchase = create_transaction(
            business=self.business,
            created_by=self.user,
            supplier=self.supplier,
            status=self.status,
            transaction_type="purchase",
            total_value=Decimal("80.00"),
            is_debt=True,
            created_at=datetime(2026, 8, 10, 12, tzinfo=timezone.utc),
        )
        expense = self.create_transaction("expense", Decimal("20.00"), debt=True)
        self.add_payment(sale, Decimal("40.00"))
        self.add_payment(purchase, Decimal("30.00"))
        self.add_payment(expense, Decimal("5.00"))

        with self.assertNumQueries(16):
            summary = build_debts_summary(
                business=self.business,
                date_from=date(2026, 8, 1),
                date_to=date(2026, 8, 31),
            )
        rows = {row["direction"]: row for row in summary["results"]}

        self.assertEqual(summary["accounts_receivable"]["outstanding"], "60.00")
        self.assertEqual(summary["accounts_payable"]["outstanding"], "50.00")
        self.assertEqual(summary["unclassified"]["outstanding"], "15.00")
        self.assertEqual(summary["payments_received"]["total"], "40.00")
        self.assertEqual(summary["payments_made"]["total"], "30.00")
        self.assertEqual(rows["receivable"]["customer"]["name"], self.customer.full_name)
        self.assertEqual(rows["payable"]["supplier"]["name"], self.supplier.name)
        self.assertEqual(rows["unclassified"]["outstanding"], "15.00")

    def test_cash_debt_payments_legacy_alias_is_gross_not_net(self):
        cases = (
            (Decimal("40.00"), Decimal("25.00"), Decimal("65.00"), Decimal("15.00")),
            (Decimal("40.00"), Decimal("0.00"), Decimal("40.00"), Decimal("40.00")),
            (Decimal("0.00"), Decimal("25.00"), Decimal("25.00"), Decimal("-25.00")),
            (Decimal("0.00"), Decimal("0.00"), Decimal("0.00"), Decimal("0.00")),
        )
        for received, made, gross, expected in cases:
            with self.subTest(received=received, made=made):
                user = create_user()
                business = create_business(user=user, status=self.status)
                employee = create_employee(business=business, status=self.status)
                method = create_payment_method(
                    business=business,
                    status=self.status,
                    method_type=PaymentMethod.TYPE_CASH,
                )
                register = create_cash_register(
                    business=business,
                    employee=employee,
                    opened_by=user,
                    opening_balance=Decimal("0.00"),
                    open_time=datetime(2026, 8, 1, 8, tzinfo=timezone.utc),
                )

                for transaction_type, amount in (("sale", received), ("purchase", made)):
                    if amount == 0:
                        continue
                    transaction = create_transaction(
                        business=business,
                        created_by=user,
                        status=self.status,
                        transaction_type=transaction_type,
                        total_value=amount,
                        is_debt=True,
                        created_at=datetime(2026, 8, 10, 12, tzinfo=timezone.utc),
                    )
                    debt = create_debt(transaction=transaction, total_amount=amount)
                    payment = create_debt_payment(
                        debt=debt,
                        payment_method=method,
                        amount=amount,
                    )
                    DebtPayment.objects.filter(pk=payment.pk).update(
                        created_at=datetime(2026, 8, 15, 12, tzinfo=timezone.utc),
                    )

                summary = calculate_cash_register_summary(
                    register,
                    until=datetime(2026, 8, 31, 20, tzinfo=timezone.utc),
                )
                self.assertEqual(summary["cash_debt_payments_received"], received)
                self.assertEqual(summary["cash_debt_payments_made"], made)
                self.assertEqual(summary["cash_debt_payments"], gross)
                self.assertEqual(summary["expected_closing_balance"], expected)


class FinancialResponseSchemaContractTests(BusinessIsolationTestCase):
    @classmethod
    def setUpTestData(cls):
        super().setUpTestData()
        cls.admin_user, cls.admin_employee, _ = create_role_user(
            business=cls.business_a,
            role=BusinessMembership.ROLE_ADMIN,
            status=cls.active_status,
        )
        cls.register = create_cash_register(
            business=cls.business_a,
            employee=cls.admin_employee,
            opened_by=cls.admin_user,
            opening_balance=Decimal("0.00"),
            open_time=datetime(2026, 8, 1, 8, tzinfo=timezone.utc),
        )

    def setUp(self):
        self.authenticate_as(self.admin_user)
        self.schema = SchemaGenerator().get_schema(request=None, public=True)

    def dereference(self, value):
        if "$ref" not in value:
            return value
        name = value["$ref"].rsplit("/", 1)[-1]
        return self.schema["components"]["schemas"][name]

    def operation_response_schema(self, path):
        response = self.schema["paths"][path]["get"]["responses"]["200"]
        documented = response["content"]["application/json"]["schema"]
        self.assertIn("$ref", documented)
        return self.dereference(documented)

    def test_financial_endpoints_match_structured_openapi_contracts(self):
        business_id = str(self.business_a.public_id)
        cases = (
            (
                f"/api/cash-registers/{self.register.public_id}/closing-preview/",
                "/api/cash-registers/{public_id}/closing-preview/",
                {},
                ("cash_debt_payments", "cash_debt_payments_received", "cash_debt_payments_made"),
            ),
            (
                "/api/dashboard/overview/",
                "/api/dashboard/overview/",
                {"business_public_id": business_id, "date_from": "2026-08-01", "date_to": "2026-08-31"},
                ("cards", "activity", "commissions", "cash"),
            ),
            (
                "/api/reports/payments-summary/",
                "/api/reports/payments-summary/",
                {"business_public_id": business_id, "date_from": "2026-08-01", "date_to": "2026-08-31"},
                ("totals", "results"),
            ),
            (
                "/api/reports/debts-summary/",
                "/api/reports/debts-summary/",
                {"business_public_id": business_id, "date_from": "2026-08-01", "date_to": "2026-08-31"},
                ("accounts_receivable", "accounts_payable", "unclassified", "results"),
            ),
            (
                "/api/reports/monthly-summary/",
                "/api/reports/monthly-summary/",
                {"business_public_id": business_id, "year": 2026, "month": 8},
                ("transactions", "debts", "payments", "cash_registers", "commissions"),
            ),
        )
        for runtime_path, schema_path, params, fields in cases:
            with self.subTest(path=runtime_path):
                response = self.client.get(runtime_path, params)
                self.assertEqual(response.status_code, status.HTTP_200_OK, response.data)
                response_schema = self.operation_response_schema(schema_path)
                for field in fields:
                    self.assertIn(field, response.data)
                    self.assertIn(field, response_schema["properties"])

    def test_new_and_legacy_money_fields_are_decimal_in_schema(self):
        cash = self.operation_response_schema(
            "/api/cash-registers/{public_id}/closing-preview/"
        )
        legacy = cash["properties"]["cash_debt_payments"]
        self.assertEqual(legacy["format"], "decimal")
        self.assertIn("legado", legacy["description"].lower())

        dashboard = self.operation_response_schema("/api/dashboard/overview/")
        cards = self.dereference(dashboard["properties"]["cards"])
        for field in ("outstanding_debt", "outstanding_receivables", "payments_made"):
            self.assertEqual(cards["properties"][field]["format"], "decimal")

        debts = self.operation_response_schema("/api/reports/debts-summary/")
        results = debts["properties"]["results"]["items"]
        detail = self.dereference(results)
        self.assertTrue(detail["properties"]["customer"]["nullable"])
        direction = detail["properties"]["direction"]
        self.assertEqual(
            set(self.dereference(direction)["enum"]),
            {"receivable", "payable", "unclassified"},
        )
