from datetime import date
from decimal import Decimal
from uuid import uuid4

from django.db import IntegrityError, transaction as db_tx
from django.test import TransactionTestCase
from drf_spectacular.generators import SchemaGenerator
from rest_framework import status

from core.models import Debt, DebtPayment, Transaction, User
from core.serializers import DebtPaymentSerializer
from core.tests.base import BusinessIsolationTestCase
from core.tests.factories import (
    create_customer,
    create_debt,
    create_payment_method,
    create_product,
    create_role_user,
    create_status,
    create_transaction,
    create_user,
)
from core.models import BusinessMembership


class DebtPaymentActorTests(BusinessIsolationTestCase):
    @classmethod
    def setUpTestData(cls):
        super().setUpTestData()
        cls.method = create_payment_method(
            business=cls.business_a,
            status=cls.active_status,
        )
        _, cls.seller, _ = create_role_user(
            business=cls.business_a,
            role=BusinessMembership.ROLE_SELLER,
            status=cls.active_status,
        )
        cls.customer = create_customer(
            business=cls.business_a,
            status=cls.active_status,
        )
        cls.product = create_product(
            business=cls.business_a,
            status=cls.active_status,
            stock=20,
        )

    def setUp(self):
        super().setUp()
        self.transaction = create_transaction(
            business=self.business_a,
            created_by=self.user_a,
            status=self.active_status,
            is_debt=True,
        )
        self.debt = create_debt(transaction=self.transaction)

    def payment_payload(self, **overrides):
        payload = {
            "debt_public_id": str(self.debt.public_id),
            "amount": "25.00",
            "payment_date": date.today().isoformat(),
            "payment_method_public_id": str(self.method.public_id),
        }
        payload.update(overrides)
        return payload

    def test_api_actor_is_authoritative_and_exposed_everywhere(self):
        spoofed = create_user(email="spoofed-payment-actor@playnow.test")
        rejected = self.client.post(
            "/api/debt-payments/",
            self.payment_payload(
                created_by_public_id=str(spoofed.public_id),
                created_by_name="Suplantado",
            ),
            format="json",
        )
        self.assertEqual(rejected.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn("created_by_public_id", rejected.data)
        self.assertFalse(DebtPayment.objects.filter(debt=self.debt).exists())

        response = self.client.post(
            "/api/debt-payments/",
            self.payment_payload(),
            format="json",
        )
        self.assertEqual(response.status_code, status.HTTP_201_CREATED, response.data)
        payment = DebtPayment.objects.get(public_id=response.data["public_id"])
        self.assertEqual(payment.created_by, self.user_a)
        self.assertEqual(
            str(response.data["created_by_public_id"]),
            str(self.user_a.public_id),
        )
        self.assertEqual(response.data["created_by_name"], self.user_a.full_name)

        listed = self.client.get(
            "/api/debt-payments/",
            {"business_public_id": str(self.business_a.public_id)},
        )
        self.assertEqual(listed.status_code, status.HTTP_200_OK)
        listed_item = next(
            item for item in listed.data["results"]
            if item["public_id"] == str(payment.public_id)
        )
        self.assertEqual(
            str(listed_item["created_by_public_id"]),
            str(self.user_a.public_id),
        )

        retrieved = self.client.get(f"/api/debt-payments/{payment.public_id}/")
        self.assertEqual(retrieved.status_code, status.HTTP_200_OK)
        self.assertEqual(
            str(retrieved.data["created_by_public_id"]),
            str(self.user_a.public_id),
        )

    def test_initial_and_later_payments_keep_distinct_actors(self):
        response = self.client.post(
            "/api/transactions/",
            {
                "business_public_id": str(self.business_a.public_id),
                "type": "sale",
                "payment_status": "partial",
                "customer_public_id": str(self.customer.public_id),
                "employee_public_id": str(self.seller.public_id),
                "payment_method_public_id": str(self.method.public_id),
                "initial_paid_amount": "25.00",
                "details": [{
                    "product_public_id": str(self.product.public_id),
                    "quantity": 1,
                }],
            },
            format="json",
        )
        self.assertEqual(response.status_code, status.HTTP_201_CREATED, response.data)
        transaction = Transaction.objects.get(public_id=response.data["public_id"])
        debt = Debt.objects.get(transaction=transaction)
        initial = debt.payments.get()
        self.assertEqual(initial.created_by, self.user_a)

        cashier, _, _ = create_role_user(
            business=self.business_a,
            role=BusinessMembership.ROLE_CASHIER,
            status=self.active_status,
        )
        self.authenticate_as(cashier)
        later = self.client.post(
            "/api/debt-payments/",
            {
                "debt_public_id": str(debt.public_id),
                "amount": "10.00",
                "payment_date": date.today().isoformat(),
                "payment_method_public_id": str(self.method.public_id),
            },
            format="json",
        )
        self.assertEqual(later.status_code, status.HTTP_201_CREATED, later.data)
        self.assertEqual(
            DebtPayment.objects.get(public_id=later.data["public_id"]).created_by,
            cashier,
        )

    def test_historical_null_and_user_deletion_preserve_payment(self):
        historical = DebtPayment.objects.create(
            debt=self.debt,
            amount=Decimal("1.00"),
            payment_date=date.today(),
            payment_method=self.method,
            transaction=self.transaction,
        )
        self.assertIsNone(DebtPaymentSerializer(historical).data["created_by_public_id"])
        self.assertIsNone(DebtPaymentSerializer(historical).data["created_by_name"])

        actor = create_user(email="deletable-payment-actor@playnow.test")
        historical.created_by = actor
        historical.save(update_fields=["created_by"])
        actor.delete()
        historical.refresh_from_db()
        self.assertIsNone(historical.created_by)

    def test_openapi_actor_is_nullable_read_only_and_not_in_request(self):
        schema = SchemaGenerator().get_schema(request=None, public=True)
        response_component = schema["components"]["schemas"]["DebtPayment"]
        actor = response_component["properties"]["created_by_public_id"]
        self.assertTrue(actor["readOnly"])
        self.assertTrue(actor["nullable"])
        self.assertEqual(actor["format"], "uuid")

        request_component = schema["components"]["schemas"]["DebtPaymentRequest"]
        self.assertNotIn("created_by_public_id", request_component["properties"])
        self.assertNotIn("created_by_name", request_component["properties"])


class DebtConstraintTests(TransactionTestCase):
    reset_sequences = True

    def setUp(self):
        self.active = create_status("Activo")
        self.user = create_user(email="debt-constraints@playnow.test")
        from core.tests.factories import create_business
        self.business = create_business(user=self.user, status=self.active)

    def make_transaction(self):
        return create_transaction(
            business=self.business,
            created_by=self.user,
            status=self.active,
            total_value=Decimal("100.00"),
            is_debt=True,
        )

    def assert_debt_rejected(self, *, total, paid, settled):
        with self.assertRaises(IntegrityError):
            with db_tx.atomic():
                Debt.objects.create(
                    transaction=self.make_transaction(),
                    total_amount=total,
                    paid_amount=paid,
                    interest_rate=Decimal("0.00"),
                    term_months=0,
                    due_date=date.today(),
                    is_settled=settled,
                )

    def test_database_rejects_invalid_debt_states(self):
        for total, paid, settled in (
            (Decimal("0.00"), Decimal("0.00"), True),
            (Decimal("-1.00"), Decimal("0.00"), False),
            (Decimal("100.00"), Decimal("-1.00"), False),
            (Decimal("100.00"), Decimal("101.00"), True),
            (Decimal("100.00"), Decimal("50.00"), True),
            (Decimal("100.00"), Decimal("100.00"), False),
        ):
            with self.subTest(total=total, paid=paid, settled=settled):
                self.assert_debt_rejected(total=total, paid=paid, settled=settled)

    def test_database_accepts_pending_partial_and_settled(self):
        valid_states = (
            (Decimal("100.00"), Decimal("0.00"), False),
            (Decimal("100.00"), Decimal("25.00"), False),
            (Decimal("100.00"), Decimal("100.00"), True),
        )
        for total, paid, settled in valid_states:
            with self.subTest(paid=paid, settled=settled):
                debt = Debt.objects.create(
                    transaction=self.make_transaction(),
                    total_amount=total,
                    paid_amount=paid,
                    interest_rate=Decimal("0.00"),
                    term_months=0,
                    due_date=date.today(),
                    is_settled=settled,
                )
                self.assertIsNotNone(debt.pk)
