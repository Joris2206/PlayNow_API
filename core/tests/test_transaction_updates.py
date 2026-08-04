from decimal import Decimal

from rest_framework import status

from core.models import (
    BusinessMembership,
    Transaction,
)
from core.tests.base import BusinessIsolationTestCase
from core.tests.factories import (
    create_customer,
    create_payment_method,
    create_product,
    create_role_user,
)


class TransactionUpdateTests(
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

        _, cls.seller_a, _ = create_role_user(
            business=cls.business_a,
            role=BusinessMembership.ROLE_SELLER,
            status=cls.active_status,
            full_name="Vendedor A",
        )

        _, cls.seller_b, _ = create_role_user(
            business=cls.business_a,
            role=BusinessMembership.ROLE_SELLER,
            status=cls.active_status,
            full_name="Vendedor B",
        )

        _, cls.foreign_seller, _ = (
            create_role_user(
                business=cls.business_b,
                role=(
                    BusinessMembership
                    .ROLE_SELLER
                ),
                status=cls.active_status,
                full_name="Vendedor extranjero",
            )
        )

        cls.customer = create_customer(
            business=cls.business_a,
            status=cls.active_status,
            full_name="José Pérez",
        )

        cls.payment_method = (
            create_payment_method(
                business=cls.business_a,
                status=cls.active_status,
                name="Efectivo",
            )
        )

        cls.product = create_product(
            business=cls.business_a,
            status=cls.active_status,
            title="Control inalámbrico",
            base_price=Decimal("100.00"),
            base_cost=Decimal("60.00"),
            stock=20,
        )

    def setUp(self):
        self.authenticate_as(
            self.cashier_user
        )

        response = self.client.post(
            "/api/transactions/",
            {
                "business": str(
                    self.business_a.public_id
                ),
                "customer": str(
                    self.customer.public_id
                ),
                "employee": str(
                    self.seller_a.public_id
                ),
                "payment_method": str(
                    self.payment_method.public_id
                ),
                "type": "sale",
                "concept": "Venta inicial",
                "invoice_number": "0001",
                "invoice_series": "A",
                "details": [
                    {
                        "product": str(
                            self.product.public_id
                        ),
                        "quantity": 2,
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

        self.transaction = (
            Transaction.objects.get(
                public_id=response.data[
                    "public_id"
                ]
            )
        )

        self.endpoint = (
            "/api/transactions/"
            f"{self.transaction.public_id}/"
        )

    def test_cannot_change_transaction_business(
        self,
    ):
        response = self.client.patch(
            self.endpoint,
            {
                "business": str(
                    self.business_b.public_id
                ),
            },
            format="json",
        )

        self.assertIn(
            response.status_code,
            {
                status.HTTP_400_BAD_REQUEST,
                status.HTTP_403_FORBIDDEN,
            },
        )

        self.transaction.refresh_from_db()

        self.assertEqual(
            self.transaction.business,
            self.business_a,
        )

    def test_cannot_change_transaction_type(
        self,
    ):
        response = self.client.patch(
            self.endpoint,
            {
                "type": "purchase",
            },
            format="json",
        )

        self.assertEqual(
            response.status_code,
            status.HTTP_400_BAD_REQUEST,
            msg=response.data,
        )

        self.transaction.refresh_from_db()

        self.assertEqual(
            self.transaction.type,
            "sale",
        )

    def test_cannot_replace_transaction_details(
        self,
    ):
        original_detail_ids = set(
            self.transaction.details.values_list(
                "pk",
                flat=True,
            )
        )

        response = self.client.patch(
            self.endpoint,
            {
                "details": [
                    {
                        "product": str(
                            self.product.public_id
                        ),
                        "quantity": 10,
                    }
                ]
            },
            format="json",
        )

        self.assertEqual(
            response.status_code,
            status.HTTP_400_BAD_REQUEST,
            msg=response.data,
        )

        current_detail_ids = set(
            self.transaction.details.values_list(
                "pk",
                flat=True,
            )
        )

        self.assertEqual(
            current_detail_ids,
            original_detail_ids,
        )

    def test_cannot_change_payment_status_directly(
        self,
    ):
        response = self.client.patch(
            self.endpoint,
            {
                "payment_status": "pending",
            },
            format="json",
        )

        self.assertEqual(
            response.status_code,
            status.HTTP_400_BAD_REQUEST,
            msg=response.data,
        )

        self.transaction.refresh_from_db()

        self.assertEqual(
            self.transaction.payment_status,
            "paid",
        )

        self.assertFalse(
            self.transaction.is_debt,
        )

    def test_can_update_administrative_fields(
        self,
    ):
        response = self.client.patch(
            self.endpoint,
            {
                "concept": (
                    "Venta corregida desde caja"
                ),
                "invoice_number": "0002",
                "invoice_series": "B",
                "invoice_file_url": (
                    "https://example.com/"
                    "facturas/0002.pdf"
                ),
            },
            format="json",
        )

        self.assertEqual(
            response.status_code,
            status.HTTP_200_OK,
            msg=response.data,
        )

        self.transaction.refresh_from_db()

        self.assertEqual(
            self.transaction.concept,
            "Venta corregida desde caja",
        )

        self.assertEqual(
            self.transaction.invoice_number,
            "0002",
        )

        self.assertEqual(
            self.transaction.invoice_series,
            "B",
        )

        self.assertEqual(
            self.transaction.updated_by,
            self.cashier_user,
        )

    def test_can_change_employee_to_another_employee_from_same_business(
        self,
    ):
        response = self.client.patch(
            self.endpoint,
            {
                "employee": str(
                    self.seller_b.public_id
                ),
            },
            format="json",
        )

        self.assertEqual(
            response.status_code,
            status.HTTP_200_OK,
            msg=response.data,
        )

        self.transaction.refresh_from_db()

        self.assertEqual(
            self.transaction.employee,
            self.seller_b,
        )

        self.assertEqual(
            self.transaction.updated_by,
            self.cashier_user,
        )

    def test_cannot_change_employee_to_foreign_business(
        self,
    ):
        response = self.client.patch(
            self.endpoint,
            {
                "employee": str(
                    self.foreign_seller.public_id
                ),
            },
            format="json",
        )

        self.assertEqual(
            response.status_code,
            status.HTTP_400_BAD_REQUEST,
            msg=response.data,
        )

        self.transaction.refresh_from_db()

        self.assertEqual(
            self.transaction.employee,
            self.seller_a,
        )

    def test_cannot_send_expense_amount_to_existing_sale(
        self,
    ):
        response = self.client.patch(
            self.endpoint,
            {
                "expense_amount": "500.00",
            },
            format="json",
        )

        self.assertEqual(
            response.status_code,
            status.HTTP_400_BAD_REQUEST,
            msg=response.data,
        )

        self.transaction.refresh_from_db()

        self.assertEqual(
            self.transaction.total_value,
            Decimal("200.00"),
        )