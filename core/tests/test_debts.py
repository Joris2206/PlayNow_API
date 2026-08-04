from decimal import Decimal

from rest_framework import status

from core.tests.base import BusinessIsolationTestCase
from core.tests.factories import (
    create_debt, create_debt_payment, create_payment_method, create_transaction,
)


class DebtBusinessIsolationTests(BusinessIsolationTestCase):
    @classmethod
    def setUpTestData(cls):
        super().setUpTestData()
        cls.tx_a = create_transaction(
            business=cls.business_a,
            created_by=cls.user_a,
            status=cls.active_status,
            is_debt=True,
            total_value=Decimal("100.00"),
        )
        cls.tx_b = create_transaction(
            business=cls.business_b,
            created_by=cls.user_b,
            status=cls.active_status,
            is_debt=True,
            total_value=Decimal("200.00"),
        )
        cls.debt_a = create_debt(transaction=cls.tx_a)
        cls.debt_b = create_debt(transaction=cls.tx_b)

    def test_user_only_lists_own_debts(self):
        self.assert_list_contains_only_owned_object(
            endpoint="/api/debts/",
            owned_object=self.debt_a,
            foreign_object=self.debt_b,
        )

    def test_user_cannot_retrieve_foreign_debt(self):
        self.assert_cannot_retrieve_foreign_object(
            endpoint=f"/api/debts/{self.debt_b.public_id}/"
        )

    def test_debt_is_read_only(self):
        self.assert_method_not_allowed(method="post", endpoint="/api/debts/", payload={})
        self.assert_method_not_allowed(
            method="patch",
            endpoint=f"/api/debts/{self.debt_a.public_id}/",
            payload={"paid_amount": "10.00"},
        )
        self.assert_method_not_allowed(
            method="delete",
            endpoint=f"/api/debts/{self.debt_a.public_id}/",
        )


class DebtPaymentBusinessIsolationTests(BusinessIsolationTestCase):
    @classmethod
    def setUpTestData(cls):
        super().setUpTestData()
        cls.method_a = create_payment_method(
            business=cls.business_a, status=cls.active_status
        )
        cls.method_b = create_payment_method(
            business=cls.business_b, status=cls.active_status
        )
        cls.tx_a = create_transaction(
            business=cls.business_a,
            created_by=cls.user_a,
            status=cls.active_status,
            is_debt=True,
            total_value=Decimal("100.00"),
        )
        cls.tx_b = create_transaction(
            business=cls.business_b,
            created_by=cls.user_b,
            status=cls.active_status,
            is_debt=True,
            total_value=Decimal("100.00"),
        )
        cls.debt_a = create_debt(transaction=cls.tx_a)
        cls.debt_b = create_debt(transaction=cls.tx_b)
        cls.payment_a = create_debt_payment(
            debt=cls.debt_a, payment_method=cls.method_a, amount=Decimal("20.00")
        )
        cls.payment_b = create_debt_payment(
            debt=cls.debt_b, payment_method=cls.method_b, amount=Decimal("20.00")
        )

    def test_user_only_lists_own_debt_payments(self):
        self.assert_list_contains_only_owned_object(
            endpoint="/api/debt-payments/",
            owned_object=self.payment_a,
            foreign_object=self.payment_b,
        )

    def test_user_cannot_retrieve_foreign_debt_payment(self):
        self.assert_cannot_retrieve_foreign_object(
            endpoint=f"/api/debt-payments/{self.payment_b.public_id}/"
        )

    def test_create_payment_updates_debt(self):
        response = self.client.post(
            "/api/debt-payments/",
            {
                "debt": str(self.debt_a.public_id),
                "amount": "30.00",
                "payment_date": "2026-08-04",
                "payment_method": str(self.method_a.public_id),
            },
            format="json",
        )
        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        self.debt_a.refresh_from_db()
        self.assertEqual(self.debt_a.paid_amount, Decimal("30.00"))

    def test_payment_above_remaining_balance_is_rejected(self):
        response = self.client.post(
            "/api/debt-payments/",
            {
                "debt": str(self.debt_a.public_id),
                "amount": "101.00",
                "payment_date": "2026-08-04",
                "payment_method": str(self.method_a.public_id),
            },
            format="json",
        )
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

    def test_debt_payment_cannot_be_updated_or_deleted(self):
        self.assert_method_not_allowed(
            method="patch",
            endpoint=f"/api/debt-payments/{self.payment_a.public_id}/",
            payload={"amount": "5.00"},
        )
        self.assert_method_not_allowed(
            method="delete",
            endpoint=f"/api/debt-payments/{self.payment_a.public_id}/",
        )
