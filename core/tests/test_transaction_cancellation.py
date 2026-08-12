from decimal import Decimal

from rest_framework import status

from core.models import (
    BusinessMembership,
    StockMovement,
    Transaction,
)
from core.tests.base import BusinessIsolationTestCase
from core.tests.factories import (
    create_customer,
    create_payment_method,
    create_product,
    create_role_user,
    create_supplier,
)


class TransactionCancellationTests(
    BusinessIsolationTestCase
):
    @classmethod
    def setUpTestData(cls):
        super().setUpTestData()

        cls.cashier_user, _, _ = create_role_user(
            business=cls.business_a,
            role=BusinessMembership.ROLE_CASHIER,
            status=cls.active_status,
        )

        cls.inventory_user, _, _ = create_role_user(
            business=cls.business_a,
            role=BusinessMembership.ROLE_INVENTORY,
            status=cls.active_status,
        )

        _, cls.seller_employee, _ = create_role_user(
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
            name="Distribuidora PlayNow",
        )

        cls.payment_method = create_payment_method(
            business=cls.business_a,
            status=cls.active_status,
            name="Efectivo",
        )

    def setUp(self):
        self.product = create_product(
            business=self.business_a,
            status=self.active_status,
            title="Control inalámbrico",
            base_price=Decimal("100.00"),
            base_cost=Decimal("60.00"),
            stock=10,
        )

    def _create_sale(
        self,
        *,
        quantity=3,
    ):
        self.authenticate_as(
            self.cashier_user
        )

        response = self.client.post(
            "/api/transactions/",
            {
                "business_public_id": str(
                    self.business_a.public_id
                ),
                "customer_public_id": str(
                    self.customer.public_id
                ),
                "employee_public_id": str(
                    self.seller_employee.public_id
                ),
                "payment_method_public_id": str(
                    self.payment_method.public_id
                ),
                "type": "sale",
                "details": [
                    {
                        "product_public_id": str(
                            self.product.public_id
                        ),
                        "quantity": quantity,
                    }
                ],
            },
            format="json",
        )

        self.assertEqual(
            response.status_code,
            status.HTTP_201_CREATED,
            msg=response.data,
        )

        return Transaction.objects.get(
            public_id=response.data[
                "public_id"
            ]
        )

    def _create_purchase(
        self,
        *,
        quantity=5,
    ):
        self.authenticate_as(
            self.inventory_user
        )

        response = self.client.post(
            "/api/transactions/",
            {
                "business_public_id": str(
                    self.business_a.public_id
                ),
                "supplier_public_id": str(
                    self.supplier.public_id
                ),
                "payment_method_public_id": str(
                    self.payment_method.public_id
                ),
                "type": "purchase",
                "details": [
                    {
                        "product_public_id": str(
                            self.product.public_id
                        ),
                        "quantity": quantity,
                    }
                ],
            },
            format="json",
        )

        self.assertEqual(
            response.status_code,
            status.HTTP_201_CREATED,
            msg=response.data,
        )

        return Transaction.objects.get(
            public_id=response.data[
                "public_id"
            ]
        )

    def test_cancel_sale_restores_product_stock(
        self,
    ):
        transaction = self._create_sale(
            quantity=3,
        )

        self.product.refresh_from_db()

        self.assertEqual(
            self.product.stock,
            7,
        )

        self.authenticate_as(self.user_a)

        response = self.client.delete(
            (
                "/api/transactions/"
                f"{transaction.public_id}/"
            )
        )

        self.assertEqual(
            response.status_code,
            status.HTTP_204_NO_CONTENT,
            msg=getattr(
                response,
                "data",
                None,
            ),
        )

        self.product.refresh_from_db()
        transaction.refresh_from_db()

        self.assertEqual(
            self.product.stock,
            10,
        )

        self.assertIn(
            transaction.status.name,
            {
                "Anulado",
                "Cancelado",
                "Eliminado",
                "Void",
                "Deleted",
            },
        )

    def test_cancel_sale_creates_adjustment_movement(
        self,
    ):
        transaction = self._create_sale(
            quantity=2,
        )

        self.authenticate_as(self.user_a)

        response = self.client.delete(
            (
                "/api/transactions/"
                f"{transaction.public_id}/"
            )
        )

        self.assertEqual(
            response.status_code,
            status.HTTP_204_NO_CONTENT,
        )

        adjustment = (
            StockMovement.objects
            .filter(
                transaction=transaction,
                type="adjustment",
            )
            .latest("created_at")
        )

        self.assertEqual(
            adjustment.quantity,
            2,
        )

        self.assertEqual(
            adjustment.product,
            self.product,
        )

    def test_cancel_purchase_removes_added_stock(
        self,
    ):
        transaction = self._create_purchase(
            quantity=5,
        )

        self.product.refresh_from_db()

        self.assertEqual(
            self.product.stock,
            15,
        )

        self.authenticate_as(self.user_a)

        response = self.client.delete(
            (
                "/api/transactions/"
                f"{transaction.public_id}/"
            )
        )

        self.assertEqual(
            response.status_code,
            status.HTTP_204_NO_CONTENT,
            msg=getattr(
                response,
                "data",
                None,
            ),
        )

        self.product.refresh_from_db()

        self.assertEqual(
            self.product.stock,
            10,
        )

        adjustment = (
            StockMovement.objects
            .filter(
                transaction=transaction,
                type="adjustment",
            )
            .latest("created_at")
        )

        self.assertEqual(
            adjustment.quantity,
            -5,
        )

    def test_cannot_cancel_purchase_when_stock_would_be_negative(
        self,
    ):
        transaction = self._create_purchase(
            quantity=5,
        )

        self.product.refresh_from_db()

        self.assertEqual(
            self.product.stock,
            15,
        )

        # Simula que parte del inventario comprado
        # ya fue utilizado o vendido.
        self.product.stock = 2
        self.product.save(
            update_fields=["stock"]
        )

        self.authenticate_as(self.user_a)

        response = self.client.delete(
            (
                "/api/transactions/"
                f"{transaction.public_id}/"
            )
        )

        self.assertEqual(
            response.status_code,
            status.HTTP_400_BAD_REQUEST,
            msg=getattr(
                response,
                "data",
                None,
            ),
        )

        self.product.refresh_from_db()
        transaction.refresh_from_db()

        self.assertEqual(
            self.product.stock,
            2,
        )

        # La operación atómica debe impedir que
        # el cambio de estado quede guardado.
        self.assertEqual(
            transaction.status,
            self.active_status,
        )

    def test_cancelled_transaction_cannot_be_cancelled_twice(
        self,
    ):
        transaction = self._create_sale(
            quantity=2,
        )

        self.authenticate_as(self.user_a)

        first_response = self.client.delete(
            (
                "/api/transactions/"
                f"{transaction.public_id}/"
            )
        )

        self.assertEqual(
            first_response.status_code,
            status.HTTP_204_NO_CONTENT,
        )

        second_response = self.client.delete(
            (
                "/api/transactions/"
                f"{transaction.public_id}/"
            )
        )

        # Como el queryset excluye registros
        # inactivos/anulados, ya no debe encontrarlo.
        self.assertEqual(
            second_response.status_code,
            status.HTTP_404_NOT_FOUND,
        )

        adjustments = (
            StockMovement.objects
            .filter(
                transaction=transaction,
                type="adjustment",
            )
        )

        self.assertEqual(
            adjustments.count(),
            1,
        )

        self.product.refresh_from_db()

        self.assertEqual(
            self.product.stock,
            10,
        )
