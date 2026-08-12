from decimal import Decimal

from rest_framework import status

from core.models import BusinessMembership, StockMovement
from core.tests.base import BusinessIsolationTestCase
from core.tests.factories import (
    create_customer, create_payment_method, create_product, create_role_user,
    create_supplier, create_variant, create_variant_type,
)


class StockMovementMethodTests(BusinessIsolationTestCase):
    def test_stock_movement_write_methods_are_not_allowed(self):
        endpoint = "/api/stock-movements/00000000-0000-0000-0000-000000000000/"
        self.assert_method_not_allowed(method="post", endpoint="/api/stock-movements/", payload={})
        self.assert_method_not_allowed(method="put", endpoint=endpoint)
        self.assert_method_not_allowed(method="patch", endpoint=endpoint)
        self.assert_method_not_allowed(method="delete", endpoint=endpoint)


class TransactionStockTests(BusinessIsolationTestCase):
    @classmethod
    def setUpTestData(cls):
        super().setUpTestData()
        cls.cashier, _, _ = create_role_user(
            business=cls.business_a,
            role=BusinessMembership.ROLE_CASHIER,
            status=cls.active_status,
        )
        _, cls.seller_employee, _ = create_role_user(
            business=cls.business_a,
            role=BusinessMembership.ROLE_SELLER,
            status=cls.active_status,
        )
        cls.inventory_user, _, _ = create_role_user(
            business=cls.business_a,
            role=BusinessMembership.ROLE_INVENTORY,
            status=cls.active_status,
        )
        cls.customer = create_customer(business=cls.business_a, status=cls.active_status)
        cls.supplier = create_supplier(business=cls.business_a, status=cls.active_status)
        cls.method = create_payment_method(business=cls.business_a, status=cls.active_status)

    def setUp(self):
        self.product = create_product(
            business=self.business_a,
            status=self.active_status,
            stock=10,
            base_price=Decimal("100.00"),
            base_cost=Decimal("60.00"),
        )

    def test_sale_decreases_product_stock_and_creates_movement(self):
        self.authenticate_as(self.cashier)
        response = self.client.post(
            "/api/transactions/",
            {
                "business_public_id": str(self.business_a.public_id),
                "customer_public_id": str(self.customer.public_id),
                "employee_public_id": str(self.seller_employee.public_id),
                "payment_method_public_id": str(self.method.public_id),
                "type": "sale",
                "details": [{"product_public_id": str(self.product.public_id), "quantity": 3}],
            },
            format="json",
        )
        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        self.product.refresh_from_db()
        self.assertEqual(self.product.stock, 7)
        movement = StockMovement.objects.get(transaction__public_id=response.data["public_id"])
        self.assertEqual(movement.quantity, -3)
        self.assertEqual(movement.type, "sale")
        self.assertIsNotNone(movement.transaction_detail_id)

    def test_purchase_increases_product_stock(self):
        self.authenticate_as(self.inventory_user)
        response = self.client.post(
            "/api/transactions/",
            {
                "business_public_id": str(self.business_a.public_id),
                "supplier_public_id": str(self.supplier.public_id),
                "payment_method_public_id": str(self.method.public_id),
                "type": "purchase",
                "details": [{"product_public_id": str(self.product.public_id), "quantity": 5}],
            },
            format="json",
        )
        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        self.product.refresh_from_db()
        self.assertEqual(self.product.stock, 15)

    def test_sale_above_stock_is_rejected_atomically(self):
        self.authenticate_as(self.cashier)
        response = self.client.post(
            "/api/transactions/",
            {
                "business_public_id": str(self.business_a.public_id),
                "customer_public_id": str(self.customer.public_id),
                "employee_public_id": str(self.seller_employee.public_id),
                "payment_method_public_id": str(self.method.public_id),
                "type": "sale",
                "details": [{"product_public_id": str(self.product.public_id), "quantity": 11}],
            },
            format="json",
        )
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.product.refresh_from_db()
        self.assertEqual(self.product.stock, 10)

    def test_variant_sale_changes_only_variant_stock(self):
        variant_type = create_variant_type(
            product=self.product,
            status=self.active_status,
            name="Modelo",
        )
        variant = create_variant(
            variant_type=variant_type,
            status=self.active_status,
            stock=8,
        )
        self.authenticate_as(self.cashier)
        response = self.client.post(
            "/api/transactions/",
            {
                "business_public_id": str(self.business_a.public_id),
                "customer_public_id": str(self.customer.public_id),
                "employee_public_id": str(self.seller_employee.public_id),
                "payment_method_public_id": str(self.method.public_id),
                "type": "sale",
                "details": [{
                    "product_public_id": str(self.product.public_id),
                    "variant_public_id": str(variant.public_id),
                    "quantity": 3,
                }],
            },
            format="json",
        )
        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        variant.refresh_from_db()
        self.product.refresh_from_db()
        self.assertEqual(variant.stock, 5)
        self.assertEqual(self.product.stock, 10)
