from decimal import Decimal

from rest_framework import status

from core.models import BusinessMembership, Debt, StockMovement, Transaction
from core.tests.base import BusinessIsolationTestCase
from core.tests.factories import (
    create_customer, create_payment_method, create_product, create_role_user,
    create_supplier, create_variant, create_variant_type,
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
        cls.variant_product = create_product(
            business=cls.business_a,
            status=cls.active_status,
            title="PS5",
            base_price=Decimal("850.00"),
            base_cost=Decimal("560.00"),
            stock=0,
        )
        cls.variant_type = create_variant_type(
            product=cls.variant_product,
            status=cls.active_status,
            name="Modelo",
        )
        cls.variant = create_variant(
            variant_type=cls.variant_type,
            status=cls.active_status,
            label="Pro",
            additional_price=Decimal("200.00"),
            stock=10,
        )

    def setUp(self):
        self.authenticate_as(self.cashier_user)

    def sale_payload(self, *, product=None, variant=None, quantity=1):
        detail = {
            "product": str((product or self.simple_product).public_id),
            "quantity": quantity,
        }
        if variant is not None:
            detail["variant"] = str(variant.public_id)
        return {
            "business": str(self.business_a.public_id),
            "customer": str(self.customer.public_id),
            "employee": str(self.seller_employee.public_id),
            "payment_method": str(self.method.public_id),
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
        payload.pop("employee")
        response = self.client.post("/api/transactions/", payload, format="json")
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn("employee", response.data)

    def test_sale_rejects_employee_from_other_business(self):
        _, foreign_employee, _ = create_role_user(
            business=self.business_b,
            role=BusinessMembership.ROLE_SELLER,
            status=self.active_status,
        )
        payload = self.sale_payload()
        payload["employee"] = str(foreign_employee.public_id)
        response = self.client.post("/api/transactions/", payload, format="json")
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn("employee", response.data)

    def test_sale_without_unit_price_uses_base_price(self):
        response = self.client.post(
            "/api/transactions/", self.sale_payload(quantity=3), format="json"
        )
        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        self.assertEqual(Decimal(response.data["total_value"]), Decimal("300.00"))

    def test_variant_sale_uses_base_plus_additional(self):
        response = self.client.post(
            "/api/transactions/",
            self.sale_payload(
                product=self.variant_product,
                variant=self.variant,
                quantity=2,
            ),
            format="json",
        )
        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        self.assertEqual(Decimal(response.data["total_value"]), Decimal("2100.00"))

    def test_product_with_variants_requires_variant(self):
        response = self.client.post(
            "/api/transactions/",
            self.sale_payload(product=self.variant_product, quantity=1),
            format="json",
        )
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn("details", response.data)

    def test_sale_can_include_multiple_products(self):
        payload = self.sale_payload(quantity=2)
        payload["details"].append({
            "product": str(self.variant_product.public_id),
            "variant": str(self.variant.public_id),
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
            "business": str(self.business_a.public_id),
            "payment_method": str(self.method.public_id),
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
