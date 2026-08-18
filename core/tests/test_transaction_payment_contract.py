from decimal import Decimal
from unittest.mock import patch
from uuid import uuid4

from django.db import connection
from django.test.utils import CaptureQueriesContext
from django.utils import timezone
from drf_spectacular.generators import SchemaGenerator
from rest_framework import status

from core.models import (
    BusinessMembership,
    Debt,
    DebtPayment,
    StockMovement,
    Transaction,
    TransactionDetail,
)
from core.serializers import TransactionSerializer
from core.services.debt_payments import get_locked_active_payment_method
from core.tests.base import BusinessIsolationTestCase
from core.tests.factories import (
    create_business,
    create_customer,
    create_membership,
    create_payment_method,
    create_product,
    create_role_user,
    create_status,
    create_supplier,
    create_user,
)


class TransactionPaymentContractTests(BusinessIsolationTestCase):
    @classmethod
    def setUpTestData(cls):
        super().setUpTestData()
        cls.cashier, _, _ = create_role_user(
            business=cls.business_a,
            role=BusinessMembership.ROLE_CASHIER,
            status=cls.active_status,
        )
        cls.inventory, _, _ = create_role_user(
            business=cls.business_a,
            role=BusinessMembership.ROLE_INVENTORY,
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
        cls.supplier = create_supplier(
            business=cls.business_a,
            status=cls.active_status,
        )
        cls.method = create_payment_method(
            business=cls.business_a,
            status=cls.active_status,
        )
        cls.foreign_method = create_payment_method(
            business=cls.business_b,
            status=cls.active_status,
        )
        cls.inactive_method = create_payment_method(
            business=cls.business_a,
            status=create_status("Inactivo"),
        )
        cls.deleted_method = create_payment_method(
            business=cls.business_a,
            status=create_status("Eliminado"),
        )
        cls.foreign_customer = create_customer(
            business=cls.business_b,
            status=cls.active_status,
        )
        cls.foreign_supplier = create_supplier(
            business=cls.business_b,
            status=cls.active_status,
        )
        _, cls.foreign_employee, _ = create_role_user(
            business=cls.business_b,
            role=BusinessMembership.ROLE_SELLER,
            status=cls.active_status,
        )
        cls.foreign_product = create_product(
            business=cls.business_b,
            status=cls.active_status,
        )
        cls.inactive_business = create_business(
            user=cls.user_b,
            status=cls.active_status,
            create_owner_membership=False,
        )
        create_membership(
            user=cls.user_a,
            business=cls.inactive_business,
            role=BusinessMembership.ROLE_OWNER,
            is_active=False,
        )
        cls.viewer, _, _ = create_role_user(
            business=cls.business_a,
            role=BusinessMembership.ROLE_VIEWER,
            status=cls.active_status,
        )
        cls.superuser = create_user(
            email="platform.admin.transactions@playnow.test",
            is_superuser=True,
        )
        cls.product = create_product(
            business=cls.business_a,
            status=cls.active_status,
            base_price=Decimal("100.00"),
            base_cost=Decimal("60.00"),
            stock=20,
        )

    def sale_payload(self, payment_status="paid"):
        payload = {
            "business_public_id": str(self.business_a.public_id),
            "customer_public_id": str(self.customer.public_id),
            "employee_public_id": str(self.seller.public_id),
            "type": "sale",
            "payment_status": payment_status,
            "details": [{
                "product_public_id": str(self.product.public_id),
                "quantity": 1,
            }],
        }
        if payment_status in {"paid", "partial"}:
            payload["payment_method_public_id"] = str(
                self.method.public_id
            )
        return payload

    def purchase_payload(self, payment_status="paid"):
        payload = {
            "business_public_id": str(self.business_a.public_id),
            "supplier_public_id": str(self.supplier.public_id),
            "type": "purchase",
            "payment_status": payment_status,
            "details": [{
                "product_public_id": str(self.product.public_id),
                "quantity": 2,
            }],
        }
        if payment_status in {"paid", "partial"}:
            payload["payment_method_public_id"] = str(
                self.method.public_id
            )
        return payload

    def test_paid_positive_contract(self):
        self.authenticate_as(self.cashier)
        response = self.client.post(
            "/api/transactions/", self.sale_payload(), format="json"
        )
        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        self.assertNotIn("initial_paid_amount", response.data)
        tx = Transaction.objects.get(public_id=response.data["public_id"])
        self.assertEqual(tx.payment_status, "paid")
        self.assertFalse(tx.is_debt)
        self.assertEqual(tx.payment_method, self.method)
        self.assertFalse(Debt.objects.filter(transaction=tx).exists())

        for field, value in (
            ("payment_method_public_id", None),
            ("initial_paid_amount", "1.00"),
        ):
            payload = self.sale_payload()
            if value is None:
                payload.pop(field)
            else:
                payload[field] = value
            rejected = self.client.post(
                "/api/transactions/", payload, format="json"
            )
            self.assertEqual(rejected.status_code, status.HTTP_400_BAD_REQUEST)
            self.assertIn(field, rejected.data)

    def test_pending_creates_zero_paid_debt_only(self):
        self.authenticate_as(self.cashier)
        payload = self.sale_payload("pending")
        payload["initial_paid_amount"] = "0.00"
        response = self.client.post(
            "/api/transactions/", payload, format="json"
        )
        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        tx = Transaction.objects.get(public_id=response.data["public_id"])
        debt = Debt.objects.get(transaction=tx)
        self.assertEqual(debt.total_amount, Decimal("100.00"))
        self.assertEqual(debt.paid_amount, Decimal("0.00"))
        self.assertFalse(debt.is_settled)
        self.assertFalse(DebtPayment.objects.filter(debt=debt).exists())

        for field, value in (
            ("payment_method_public_id", str(self.method.public_id)),
            ("initial_paid_amount", "1.00"),
        ):
            invalid = self.sale_payload("pending")
            invalid[field] = value
            rejected = self.client.post(
                "/api/transactions/", invalid, format="json"
            )
            self.assertEqual(rejected.status_code, status.HTTP_400_BAD_REQUEST)
            self.assertIn(field, rejected.data)

    def test_partial_creates_initial_payment_and_can_be_settled(self):
        self.authenticate_as(self.cashier)
        payload = self.sale_payload("partial")
        payload["initial_paid_amount"] = "25.00"
        response = self.client.post(
            "/api/transactions/", payload, format="json"
        )
        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        self.assertNotIn("initial_paid_amount", response.data)
        tx = Transaction.objects.get(public_id=response.data["public_id"])
        debt = Debt.objects.get(transaction=tx)
        payment = DebtPayment.objects.get(debt=debt)
        self.assertEqual(debt.paid_amount, Decimal("25.00"))
        self.assertEqual(payment.amount, Decimal("25.00"))
        self.assertEqual(payment.payment_date, timezone.localdate())
        self.assertEqual(payment.payment_method, self.method)
        self.assertEqual(payment.transaction, tx)
        self.assertEqual(tx.payment_status, "partial")
        self.assertTrue(tx.is_debt)

        settled = self.client.post(
            "/api/debt-payments/",
            {
                "debt_public_id": str(debt.public_id),
                "amount": "75.00",
                "payment_date": str(timezone.localdate()),
                "payment_method_public_id": str(self.method.public_id),
                "transaction_public_id": str(tx.public_id),
            },
            format="json",
        )
        self.assertEqual(settled.status_code, status.HTTP_201_CREATED)
        debt.refresh_from_db()
        tx.refresh_from_db()
        self.assertTrue(debt.is_settled)
        self.assertEqual(debt.paid_amount, Decimal("100.00"))
        self.assertEqual(tx.payment_status, "paid")
        self.assertFalse(tx.is_debt)

    def test_partial_amount_boundaries_and_required_fields(self):
        self.authenticate_as(self.cashier)
        cases = (
            (None, "initial_paid_amount"),
            ("0.00", "initial_paid_amount"),
            ("-1.00", "initial_paid_amount"),
            ("100.00", "initial_paid_amount"),
            ("101.00", "initial_paid_amount"),
        )
        for amount, error_field in cases:
            payload = self.sale_payload("partial")
            if amount is not None:
                payload["initial_paid_amount"] = amount
            response = self.client.post(
                "/api/transactions/", payload, format="json"
            )
            self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
            self.assertIn(error_field, response.data)

        payload = self.sale_payload("partial")
        payload["initial_paid_amount"] = "25.00"
        payload.pop("payment_method_public_id")
        response = self.client.post(
            "/api/transactions/", payload, format="json"
        )
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn("payment_method_public_id", response.data)

    def test_zero_total_contract_uses_final_server_total(self):
        self.authenticate_as(self.cashier)
        self.product.base_price = Decimal("0.00")
        self.product.save(update_fields=["base_price"])
        valid = self.sale_payload()
        valid.pop("payment_method_public_id")
        valid["initial_paid_amount"] = "0.00"
        response = self.client.post(
            "/api/transactions/", valid, format="json"
        )
        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        tx = Transaction.objects.get(public_id=response.data["public_id"])
        self.assertEqual(tx.total_value, Decimal("0.00"))
        self.assertFalse(tx.is_debt)
        self.assertFalse(Debt.objects.filter(transaction=tx).exists())

        for payment_status, with_method, initial in (
            ("pending", False, None),
            ("partial", True, "1.00"),
            ("paid", True, None),
            ("paid", False, "1.00"),
        ):
            payload = self.sale_payload(payment_status)
            if not with_method:
                payload.pop("payment_method_public_id", None)
            if initial is not None:
                payload["initial_paid_amount"] = initial
            rejected = self.client.post(
                "/api/transactions/", payload, format="json"
            )
            self.assertEqual(rejected.status_code, status.HTTP_400_BAD_REQUEST)

    def test_expense_only_allows_paid_with_active_method(self):
        self.authenticate_as(self.user_a)
        base = {
            "business_public_id": str(self.business_a.public_id),
            "payment_method_public_id": str(self.method.public_id),
            "type": "expense",
            "expense_amount": "50.00",
            "payment_status": "paid",
        }
        response = self.client.post(
            "/api/transactions/", base, format="json"
        )
        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        tx = Transaction.objects.get(public_id=response.data["public_id"])
        self.assertFalse(Debt.objects.filter(transaction=tx).exists())
        self.assertFalse(DebtPayment.objects.filter(transaction=tx).exists())

        variants = (
            ({**base, "payment_status": "pending"}, "payment_status"),
            ({**base, "payment_status": "partial", "initial_paid_amount": "1.00"}, "payment_status"),
            ({key: value for key, value in base.items() if key != "payment_method_public_id"}, "payment_method_public_id"),
            ({**base, "initial_paid_amount": "1.00"}, "initial_paid_amount"),
        )
        for payload, error_field in variants:
            rejected = self.client.post(
                "/api/transactions/", payload, format="json"
            )
            self.assertEqual(rejected.status_code, status.HTTP_400_BAD_REQUEST)
            self.assertIn(error_field, rejected.data)

    def test_purchase_partial_updates_stock_once(self):
        self.authenticate_as(self.inventory)
        original_stock = self.product.stock
        payload = self.purchase_payload("partial")
        payload["initial_paid_amount"] = "30.00"
        response = self.client.post(
            "/api/transactions/", payload, format="json"
        )
        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        self.product.refresh_from_db()
        self.assertEqual(self.product.stock, original_stock + 2)
        tx = Transaction.objects.get(public_id=response.data["public_id"])
        self.assertEqual(StockMovement.objects.filter(transaction=tx).count(), 1)
        self.assertEqual(DebtPayment.objects.filter(transaction=tx).count(), 1)

    def test_inactive_deleted_and_foreign_methods_are_safe(self):
        self.authenticate_as(self.cashier)
        for method in (self.inactive_method, self.deleted_method):
            payload = self.sale_payload()
            payload["payment_method_public_id"] = str(method.public_id)
            response = self.client.post(
                "/api/transactions/", payload, format="json"
            )
            self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
            self.assertIn("payment_method_public_id", response.data)

        payload = self.sale_payload()
        payload["payment_method_public_id"] = str(self.foreign_method.public_id)
        foreign = self.client.post(
            "/api/transactions/", payload, format="json"
        )
        payload["payment_method_public_id"] = str(uuid4())
        missing = self.client.post(
            "/api/transactions/", payload, format="json"
        )
        self.assertEqual(foreign.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertEqual(foreign.json(), missing.json())

    def test_foreign_business_and_method_do_not_reveal_existence(self):
        self.authenticate_as(self.user_a)
        payload = {
            "business_public_id": str(self.business_b.public_id),
            "payment_method_public_id": str(self.foreign_method.public_id),
            "type": "expense",
            "expense_amount": "50.00",
            "payment_status": "paid",
        }
        foreign = self.client.post(
            "/api/transactions/", payload, format="json"
        )
        payload["payment_method_public_id"] = str(uuid4())
        missing_method = self.client.post(
            "/api/transactions/", payload, format="json"
        )

        self.assertEqual(foreign.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertEqual(foreign.json(), missing_method.json())
        self.assertEqual(
            foreign.json(),
            {
                "business_public_id": [
                    "La relación indicada no es válida."
                ],
                "payment_method_public_id": [
                    "La relación indicada no es válida."
                ],
            },
        )
        self.assertFalse(Transaction.objects.exists())
        self.assertFalse(Debt.objects.exists())
        self.assertFalse(DebtPayment.objects.exists())
        self.assertFalse(StockMovement.objects.exists())

    def test_business_scope_hides_foreign_missing_and_inactive_membership(self):
        self.authenticate_as(self.user_a)

        def response_for(public_id):
            return self.client.post(
                "/api/transactions/",
                {
                    "business_public_id": str(public_id),
                    "type": "expense",
                    "expense_amount": "50.00",
                },
                format="json",
            )

        foreign = response_for(self.business_b.public_id)
        missing = response_for(uuid4())
        inactive = response_for(self.inactive_business.public_id)
        self.assertEqual(foreign.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertEqual(foreign.json(), missing.json())
        self.assertEqual(foreign.json(), inactive.json())
        self.assertEqual(
            foreign.json(),
            {
                "business_public_id": [
                    "La relación indicada no es válida."
                ],
            },
        )

    def test_other_transaction_relations_are_membership_scoped(self):
        self.authenticate_as(self.cashier)
        sale_cases = (
            ("customer_public_id", self.foreign_customer.public_id),
            ("employee_public_id", self.foreign_employee.public_id),
        )
        for field, foreign_id in sale_cases:
            payload = self.sale_payload()
            payload[field] = str(foreign_id)
            foreign = self.client.post(
                "/api/transactions/", payload, format="json"
            )
            payload[field] = str(uuid4())
            missing = self.client.post(
                "/api/transactions/", payload, format="json"
            )
            self.assertEqual(foreign.json(), missing.json())

        payload = self.sale_payload()
        payload["details"][0]["product_public_id"] = str(
            self.foreign_product.public_id
        )
        foreign = self.client.post(
            "/api/transactions/", payload, format="json"
        )
        payload["details"][0]["product_public_id"] = str(uuid4())
        missing = self.client.post(
            "/api/transactions/", payload, format="json"
        )
        self.assertEqual(foreign.json(), missing.json())

        self.authenticate_as(self.inventory)
        payload = self.purchase_payload()
        payload["supplier_public_id"] = str(
            self.foreign_supplier.public_id
        )
        foreign = self.client.post(
            "/api/transactions/", payload, format="json"
        )
        payload["supplier_public_id"] = str(uuid4())
        missing = self.client.post(
            "/api/transactions/", payload, format="json"
        )
        self.assertEqual(foreign.json(), missing.json())

    def test_role_denial_remains_403_and_superuser_remains_global(self):
        self.authenticate_as(self.viewer)
        denied = self.client.post(
            "/api/transactions/", self.sale_payload(), format="json"
        )
        self.assertEqual(denied.status_code, status.HTTP_403_FORBIDDEN)

        self.authenticate_as(self.superuser)
        accepted = self.client.post(
            "/api/transactions/",
            {
                "business_public_id": str(self.business_b.public_id),
                "payment_method_public_id": str(
                    self.foreign_method.public_id
                ),
                "type": "expense",
                "expense_amount": "50.00",
            },
            format="json",
        )
        self.assertEqual(accepted.status_code, status.HTTP_201_CREATED)

    def test_method_is_reloaded_and_locked_before_recording_money(self):
        self.authenticate_as(self.cashier)

        def deactivate_then_lock(**kwargs):
            self.method.status = self.inactive_method.status
            self.method.save(update_fields=["status"])
            return get_locked_active_payment_method(**kwargs)

        with patch(
            "core.serializers.get_locked_active_payment_method",
            side_effect=deactivate_then_lock,
        ):
            rejected = self.client.post(
                "/api/transactions/", self.sale_payload(), format="json"
            )
        self.assertEqual(rejected.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn("payment_method_public_id", rejected.data)
        self.assertEqual(Transaction.objects.count(), 0)

        with CaptureQueriesContext(connection) as queries:
            accepted = self.client.post(
                "/api/transactions/", self.sale_payload(), format="json"
            )
        self.assertEqual(accepted.status_code, status.HTTP_201_CREATED)
        self.assertTrue(any(
            "FOR UPDATE" in query["sql"].upper()
            and "core_paymentmethod" in query["sql"]
            for query in queries.captured_queries
        ))

    def test_financial_and_inventory_failures_roll_back_everything(self):
        self.authenticate_as(self.cashier)
        initial_stock = self.product.stock
        payload = self.sale_payload("partial")
        payload["initial_paid_amount"] = "25.00"

        with patch(
            "core.serializers.DebtPayment.objects.create",
            side_effect=RuntimeError("forced payment failure"),
        ):
            with self.assertRaises(RuntimeError):
                self.client.post(
                    "/api/transactions/", payload, format="json"
                )
        self.assertEqual(Transaction.objects.count(), 0)
        self.assertEqual(TransactionDetail.objects.count(), 0)
        self.assertEqual(Debt.objects.count(), 0)
        self.assertEqual(DebtPayment.objects.count(), 0)

        with patch(
            "core.views.record_stock_movement",
            side_effect=RuntimeError("forced inventory failure"),
        ):
            with self.assertRaises(RuntimeError):
                self.client.post(
                    "/api/transactions/", payload, format="json"
                )
        self.product.refresh_from_db()
        self.assertEqual(self.product.stock, initial_stock)
        self.assertEqual(Transaction.objects.count(), 0)
        self.assertEqual(TransactionDetail.objects.count(), 0)
        self.assertEqual(Debt.objects.count(), 0)
        self.assertEqual(DebtPayment.objects.count(), 0)
        self.assertEqual(StockMovement.objects.count(), 0)

    def test_initial_amount_is_write_only_and_rejected_on_patch(self):
        field = TransactionSerializer().fields["initial_paid_amount"]
        self.assertTrue(field.write_only)
        schema = SchemaGenerator().get_schema(
            request=None,
            public=True,
        )
        create_properties = schema["components"]["schemas"][
            "TransactionRequest"
        ]["properties"]
        update_properties = schema["components"]["schemas"][
            "TransactionUpdateSchemaRequest"
        ]["properties"]
        update_required = schema["components"]["schemas"][
            "TransactionUpdateSchemaRequest"
        ]["required"]
        patch_schema = schema["components"]["schemas"][
            "PatchedTransactionUpdateSchemaRequest"
        ]
        self.assertTrue(
            create_properties["initial_paid_amount"]["writeOnly"]
        )
        self.assertNotIn("initial_paid_amount", update_properties)
        self.assertNotIn("payment_status", update_properties)
        self.assertIn("business_public_id", update_properties)
        self.assertIn("type", update_properties)
        self.assertIn("business_public_id", update_required)
        self.assertIn("type", update_required)
        self.assertNotIn(
            "business_public_id",
            patch_schema.get("required", []),
        )
        self.assertNotIn("type", patch_schema.get("required", []))
        self.assertNotIn(
            "initial_paid_amount",
            patch_schema["properties"],
        )
        self.assertNotIn(
            "payment_status",
            patch_schema["properties"],
        )
        self.authenticate_as(self.cashier)
        created = self.client.post(
            "/api/transactions/", self.sale_payload(), format="json"
        )
        endpoint = f"/api/transactions/{created.data['public_id']}/"
        put_response = self.client.put(
            endpoint,
            {
                "business_public_id": str(self.business_a.public_id),
                "type": "sale",
                "concept": "PUT conforme al contrato OpenAPI",
            },
            format="json",
        )
        self.assertEqual(
            put_response.status_code,
            status.HTTP_200_OK,
            msg=put_response.data,
        )
        response = self.client.patch(
            endpoint, {"initial_paid_amount": "1.00"}, format="json"
        )
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn("initial_paid_amount", response.data)
