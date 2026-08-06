from datetime import date, datetime, timezone
from decimal import Decimal

from rest_framework import status

from core.models import (
    BusinessMembership,
    PaymentMethod,
)
from core.tests.base import BusinessIsolationTestCase
from core.tests.factories import (
    create_customer,
    create_debt,
    create_debt_payment,
    create_payment_method,
    create_role_user,
    create_transaction,
)


class PaymentDebtReportTests(
    BusinessIsolationTestCase
):
    @classmethod
    def setUpTestData(cls):
        super().setUpTestData()

        (
            cls.admin_user,
            cls.admin_employee,
            _,
        ) = create_role_user(
            business=cls.business_a,
            role=BusinessMembership.ROLE_ADMIN,
            status=cls.active_status,
        )

        (
            cls.cashier_user,
            cls.cashier_employee,
            _,
        ) = create_role_user(
            business=cls.business_a,
            role=BusinessMembership.ROLE_CASHIER,
            status=cls.active_status,
        )

        cls.customer = create_customer(
            business=cls.business_a,
            status=cls.active_status,
            full_name="Cliente de crédito",
        )

        cls.cash_method = create_payment_method(
            business=cls.business_a,
            status=cls.active_status,
            name="Efectivo",
            method_type=PaymentMethod.TYPE_CASH,
        )

        cls.card_method = create_payment_method(
            business=cls.business_a,
            status=cls.active_status,
            name="Tarjeta",
            method_type=PaymentMethod.TYPE_CARD,
        )

        cls.foreign_method = create_payment_method(
            business=cls.business_b,
            status=cls.active_status,
            name="Efectivo B",
            method_type=PaymentMethod.TYPE_CASH,
        )

    def setUp(self):
        self.authenticate_as(
            self.admin_user
        )

    def _get_payments_summary(
        self,
        *,
        payment_method=None,
        business=None,
        date_from="2026-08-01",
        date_to="2026-08-31",
    ):
        business = business or self.business_a

        params = {
            "business_public_id": str(
                business.public_id
            ),
            "date_from": date_from,
            "date_to": date_to,
        }

        if payment_method is not None:
            params[
                "payment_method_public_id"
            ] = str(
                payment_method.public_id
            )

        return self.client.get(
            "/api/reports/payments-summary/",
            params,
        )

    def _get_debts_summary(
        self,
        *,
        business=None,
        date_from="2026-08-01",
        date_to="2026-08-31",
    ):
        business = business or self.business_a

        return self.client.get(
            "/api/reports/debts-summary/",
            {
                "business_public_id": str(
                    business.public_id
                ),
                "date_from": date_from,
                "date_to": date_to,
            },
        )

    def test_payment_summary_groups_income_and_expenses(
        self,
    ):
        create_transaction(
            business=self.business_a,
            created_by=self.cashier_user,
            customer=self.customer,
            payment_method=self.cash_method,
            status=self.active_status,
            transaction_type="sale",
            total_value=Decimal("1000.00"),
            created_at=datetime(
                2026,
                8,
                5,
                12,
                tzinfo=timezone.utc,
            ),
        )

        create_transaction(
            business=self.business_a,
            created_by=self.admin_user,
            payment_method=self.cash_method,
            status=self.active_status,
            transaction_type="expense",
            total_value=Decimal("200.00"),
            created_at=datetime(
                2026,
                8,
                6,
                12,
                tzinfo=timezone.utc,
            ),
        )

        response = self._get_payments_summary()

        self.assertEqual(
            response.status_code,
            status.HTTP_200_OK,
            msg=response.data,
        )

        totals = response.data["totals"]

        self.assertEqual(
            totals["incoming_total"],
            "1000.00",
        )

        self.assertEqual(
            totals["outgoing_total"],
            "200.00",
        )

        self.assertEqual(
            totals["net_amount"],
            "800.00",
        )

    def test_payment_summary_can_filter_method(
        self,
    ):
        create_transaction(
            business=self.business_a,
            created_by=self.cashier_user,
            customer=self.customer,
            payment_method=self.cash_method,
            status=self.active_status,
            transaction_type="sale",
            total_value=Decimal("800.00"),
            created_at=datetime(
                2026,
                8,
                5,
                12,
                tzinfo=timezone.utc,
            ),
        )

        create_transaction(
            business=self.business_a,
            created_by=self.cashier_user,
            customer=self.customer,
            payment_method=self.card_method,
            status=self.active_status,
            transaction_type="sale",
            total_value=Decimal("500.00"),
            created_at=datetime(
                2026,
                8,
                6,
                12,
                tzinfo=timezone.utc,
            ),
        )

        response = self._get_payments_summary(
            payment_method=self.card_method
        )

        self.assertEqual(
            response.status_code,
            status.HTTP_200_OK,
            msg=response.data,
        )

        self.assertEqual(
            response.data["totals"][
                "incoming_total"
            ],
            "500.00",
        )

        self.assertEqual(
            len(response.data["results"]),
            1,
        )

        self.assertEqual(
            response.data["results"][0][
                "payment_method"
            ]["name"],
            "Tarjeta",
        )

    def test_debt_summary_calculates_generated_paid_and_pending(
        self,
    ):
        transaction = create_transaction(
            business=self.business_a,
            created_by=self.cashier_user,
            customer=self.customer,
            payment_method=self.cash_method,
            status=self.active_status,
            transaction_type="sale",
            total_value=Decimal("1000.00"),
            is_debt=True,
            created_at=datetime(
                2026,
                8,
                5,
                12,
                tzinfo=timezone.utc,
            ),
        )

        debt = create_debt(
            transaction=transaction,
            total_amount=Decimal("1000.00"),
        )

        payment = create_debt_payment(
            debt=debt,
            payment_method=self.cash_method,
            amount=Decimal("300.00"),
        )

        debt.payments.filter(
            pk=payment.pk
        ).update(
            payment_date=date(
                2026,
                8,
                15,
            )
        )

        response = self._get_debts_summary()

        self.assertEqual(
            response.status_code,
            status.HTTP_200_OK,
            msg=response.data,
        )

        self.assertEqual(
            response.data["generated"]["total"],
            "1000.00",
        )

        self.assertEqual(
            response.data[
                "payments_received"
            ]["total"],
            "300.00",
        )

        self.assertEqual(
            response.data[
                "portfolio_at_period_end"
            ]["outstanding"],
            "700.00",
        )

    def test_payment_after_period_is_not_subtracted(
        self,
    ):
        transaction = create_transaction(
            business=self.business_a,
            created_by=self.cashier_user,
            customer=self.customer,
            status=self.active_status,
            transaction_type="sale",
            total_value=Decimal("1000.00"),
            is_debt=True,
            created_at=datetime(
                2026,
                8,
                5,
                12,
                tzinfo=timezone.utc,
            ),
        )

        debt = create_debt(
            transaction=transaction,
            total_amount=Decimal("1000.00"),
        )

        payment = create_debt_payment(
            debt=debt,
            payment_method=self.cash_method,
            amount=Decimal("500.00"),
        )

        debt.payments.filter(
            pk=payment.pk
        ).update(
            payment_date=date(
                2026,
                9,
                2,
            )
        )

        response = self._get_debts_summary()

        self.assertEqual(
            response.status_code,
            status.HTTP_200_OK,
            msg=response.data,
        )

        self.assertEqual(
            response.data[
                "portfolio_at_period_end"
            ]["outstanding"],
            "1000.00",
        )

    def test_foreign_payment_method_returns_not_found(
        self,
    ):
        response = self._get_payments_summary(
            payment_method=self.foreign_method
        )

        self.assertEqual(
            response.status_code,
            status.HTTP_404_NOT_FOUND,
        )

    def test_user_cannot_read_foreign_business_reports(
        self,
    ):
        payments_response = (
            self._get_payments_summary(
                business=self.business_b
            )
        )

        debts_response = (
            self._get_debts_summary(
                business=self.business_b
            )
        )

        self.assertEqual(
            payments_response.status_code,
            status.HTTP_403_FORBIDDEN,
        )

        self.assertEqual(
            debts_response.status_code,
            status.HTTP_403_FORBIDDEN,
        )

    def test_invalid_date_range_is_rejected(
        self,
    ):
        response = self._get_debts_summary(
            date_from="2026-08-31",
            date_to="2026-08-01",
        )

        self.assertEqual(
            response.status_code,
            status.HTTP_400_BAD_REQUEST,
        )