from decimal import Decimal

from rest_framework import status

from core.models import BusinessMembership, Debt, StockMovement, Transaction
from core.tests.base import BusinessIsolationTestCase
from core.tests.factories import (
    create_customer, create_payment_method, create_product, create_role_user,
    create_status,
    create_supplier,
)


class TransactionTests(BusinessIsolationTestCase):
    @classmethod
    def setUpTestData(cls):
        super().setUpTestData()
        cls.cashier_user, cls.cashier_employee, _ = create_role_user(
            business=cls.business_a,
            role=BusinessMembership.ROLE_CASHIER,
            status=cls.active_status,
        )
        cls.seller_user, cls.seller_employee, _ = create_role_user(
            business=cls.business_a,
            role=BusinessMembership.ROLE_SELLER,
            status=cls.active_status,
        )
        cls.customer = create_customer(
            business=cls.business_a,
            status=cls.active_status,
            full_name="José Pérez",
        )
        cls.supplier = create_supplier(
            business=cls.business_a,
            status=cls.active_status,
        )
        cls.method = create_payment_method(
            business=cls.business_a,
            status=cls.active_status,
        )
        cls.simple_product = create_product(
            business=cls.business_a,
            status=cls.active_status,
            title="Producto simple",
            base_price=Decimal("100.00"),
            base_cost=Decimal("60.00"),
            stock=20,
        )
        cls.second_product = create_product(
            business=cls.business_a,
            status=cls.active_status,
            title="PS5 Pro",
            base_price=Decimal("1050.00"),
            base_cost=Decimal("560.00"),
            stock=10,
        )

    def setUp(self):
        self.authenticate_as(self.cashier_user)

    def sale_payload(self, *, product=None, quantity=1):
        detail = {
            "product_public_id": str((product or self.simple_product).public_id),
            "quantity": quantity,
        }
        return {
            "business_public_id": str(self.business_a.public_id),
            "customer_public_id": str(self.customer.public_id),
            "employee_public_id": str(self.seller_employee.public_id),
            "payment_method_public_id": str(self.method.public_id),
            "type": "sale",
            "details": [detail],
        }

    def test_cashier_registers_sale_for_another_employee(self):
        response = self.client.post(
            "/api/transactions/", self.sale_payload(quantity=2), format="json"
        )
        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        tx = Transaction.objects.get(public_id=response.data["public_id"])
        self.assertEqual(tx.created_by, self.cashier_user)
        self.assertEqual(tx.employee, self.seller_employee)

    def test_sale_requires_employee(self):
        payload = self.sale_payload()
        payload.pop("employee_public_id")
        response = self.client.post("/api/transactions/", payload, format="json")
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn("employee_public_id", response.data)

    def test_sale_rejects_employee_from_other_business(self):
        _, foreign_employee, _ = create_role_user(
            business=self.business_b,
            role=BusinessMembership.ROLE_SELLER,
            status=self.active_status,
        )
        payload = self.sale_payload()
        payload["employee_public_id"] = str(foreign_employee.public_id)
        response = self.client.post("/api/transactions/", payload, format="json")
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn("employee_public_id", response.data)

    def test_sale_without_unit_price_uses_base_price(self):
        response = self.client.post(
            "/api/transactions/", self.sale_payload(quantity=3), format="json"
        )
        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        self.assertEqual(Decimal(response.data["total_value"]), Decimal("300.00"))

    def test_individual_product_uses_its_own_price(self):
        response = self.client.post(
            "/api/transactions/",
            self.sale_payload(
                product=self.second_product,
                quantity=2,
            ),
            format="json",
        )
        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        self.assertEqual(Decimal(response.data["total_value"]), Decimal("2100.00"))

    def test_new_sale_rejects_inactive_product(self):
        inactive_product = create_product(
            business=self.business_a,
            status=create_status("Inactivo"),
            title="Producto inactivo",
            stock=10,
        )

        response = self.client.post(
            "/api/transactions/",
            self.sale_payload(
                product=inactive_product,
            ),
            format="json",
        )

        self.assertEqual(
            response.status_code,
            status.HTTP_400_BAD_REQUEST,
        )
        self.assertIn("details", response.data)

    def test_sale_can_include_multiple_products(self):
        payload = self.sale_payload(quantity=2)
        payload["details"].append({
            "product_public_id": str(self.second_product.public_id),
            "quantity": 1,
        })
        response = self.client.post("/api/transactions/", payload, format="json")
        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        self.assertEqual(len(response.data["details"]), 2)
        self.assertEqual(Decimal(response.data["total_value"]), Decimal("1250.00"))

    def test_pending_sale_creates_debt(self):
        payload = self.sale_payload()
        payload["payment_status"] = "pending"
        response = self.client.post("/api/transactions/", payload, format="json")
        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        tx = Transaction.objects.get(public_id=response.data["public_id"])
        self.assertTrue(tx.is_debt)
        self.assertTrue(Debt.objects.filter(transaction=tx).exists())

    def test_paid_sale_does_not_create_debt(self):
        response = self.client.post(
            "/api/transactions/", self.sale_payload(), format="json"
        )
        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        tx = Transaction.objects.get(public_id=response.data["public_id"])
        self.assertFalse(Debt.objects.filter(transaction=tx).exists())

    def test_expense_does_not_create_details_or_stock_movements(self):
        self.authenticate_as(self.user_a)
        payload = {
            "business_public_id": str(self.business_a.public_id),
            "payment_method_public_id": str(self.method.public_id),
            "type": "expense",
            "concept": "Pago de energía",
            "expense_amount": "500.00",
        }
        response = self.client.post("/api/transactions/", payload, format="json")
        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        tx = Transaction.objects.get(public_id=response.data["public_id"])
        self.assertFalse(tx.details.exists())
        self.assertFalse(StockMovement.objects.filter(transaction=tx).exists())
        self.assertEqual(tx.total_value, Decimal("500.00"))
