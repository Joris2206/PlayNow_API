from decimal import Decimal
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier

from django.db import close_old_connections
from django.test import TransactionTestCase
from rest_framework import status
from rest_framework.test import APIClient

from core.models import (
    BusinessMembership,
    StockMovement,
    Transaction,
    TransactionDetail,
)
from core.tests.base import BusinessIsolationTestCase
from core.tests.factories import (
    create_business, create_customer, create_payment_method, create_product,
    create_role_user, create_status, create_stock_movement, create_supplier,
    create_user,
)


class StockMovementMethodTests(BusinessIsolationTestCase):
    def test_stock_movement_write_methods_are_not_allowed(self):
        endpoint = "/api/stock-movements/00000000-0000-0000-0000-000000000000/"
        self.assert_method_not_allowed(method="post", endpoint="/api/stock-movements/", payload={})
        self.assert_method_not_allowed(method="put", endpoint=endpoint)
        self.assert_method_not_allowed(method="patch", endpoint=endpoint)
        self.assert_method_not_allowed(method="delete", endpoint=endpoint)

    def test_stock_movement_reads_are_business_isolated(self):
        owned_product = create_product(
            business=self.business_a,
            status=self.active_status,
        )
        foreign_product = create_product(
            business=self.business_b,
            status=self.active_status,
        )
        owned_movement = create_stock_movement(
            product=owned_product,
            created_by=self.user_a,
            movement_type="adjustment",
            quantity=1,
        )
        foreign_movement = create_stock_movement(
            product=foreign_product,
            created_by=self.user_b,
            movement_type="adjustment",
            quantity=1,
        )

        self.assert_list_contains_only_owned_object(
            endpoint="/api/stock-movements/",
            owned_object=owned_movement,
            foreign_object=foreign_movement,
        )
        self.assert_cannot_retrieve_foreign_object(
            endpoint=(
                "/api/stock-movements/"
                f"{foreign_movement.public_id}/"
            )
        )


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
        self.assertFalse(Transaction.objects.exists())
        self.assertFalse(TransactionDetail.objects.exists())
        self.assertFalse(StockMovement.objects.exists())


class ConcurrentTransactionStockTests(TransactionTestCase):
    reset_sequences = True

    def setUp(self):
        active_status = create_status("Activo")
        create_status("Anulado")
        owner = create_user(email="concurrent-owner@playnow.test")
        self.business = create_business(
            user=owner,
            status=active_status,
            business_name="Negocio concurrente",
        )
        self.cashier, _, _ = create_role_user(
            business=self.business,
            role=BusinessMembership.ROLE_CASHIER,
            status=active_status,
        )
        _, self.seller, _ = create_role_user(
            business=self.business,
            role=BusinessMembership.ROLE_SELLER,
            status=active_status,
        )
        self.customer = create_customer(
            business=self.business,
            status=active_status,
        )
        self.payment_method = create_payment_method(
            business=self.business,
            status=active_status,
        )
        self.product = create_product(
            business=self.business,
            status=active_status,
            stock=5,
        )

    def _create_sale(self, barrier):
        close_old_connections()
        client = APIClient()
        client.force_authenticate(
            user=type(self.cashier).objects.get(pk=self.cashier.pk)
        )
        barrier.wait(timeout=10)
        response = client.post(
            "/api/transactions/",
            {
                "business_public_id": str(self.business.public_id),
                "customer_public_id": str(self.customer.public_id),
                "employee_public_id": str(self.seller.public_id),
                "payment_method_public_id": str(self.payment_method.public_id),
                "type": "sale",
                "details": [{
                    "product_public_id": str(self.product.public_id),
                    "quantity": 4,
                }],
            },
            format="json",
        )
        close_old_connections()
        return response.status_code

    def test_concurrent_sales_cannot_oversell_product(self):
        barrier = Barrier(2)
        with ThreadPoolExecutor(max_workers=2) as executor:
            statuses = sorted(
                executor.map(self._create_sale, [barrier, barrier])
            )

        self.assertEqual(
            statuses,
            [status.HTTP_201_CREATED, status.HTTP_400_BAD_REQUEST],
        )
        self.product.refresh_from_db()
        self.assertEqual(self.product.stock, 1)
        self.assertEqual(StockMovement.objects.count(), 1)
        self.assertEqual(Transaction.objects.filter(type="sale").count(), 1)
