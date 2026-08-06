from datetime import datetime, timezone
from decimal import Decimal

from rest_framework import status

from core.models import BusinessMembership
from core.tests.base import (
    BusinessIsolationTestCase,
)
from core.tests.factories import (
    create_customer,
    create_role_user,
    create_supplier,
    create_transaction,
)


class CustomerSupplierReportTests(
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

        cls.customer_a1 = create_customer(
            business=cls.business_a,
            status=cls.active_status,
            full_name="Cliente Uno",
        )

        cls.customer_a2 = create_customer(
            business=cls.business_a,
            status=cls.active_status,
            full_name="Cliente Dos",
        )

        cls.customer_b = create_customer(
            business=cls.business_b,
            status=cls.active_status,
            full_name="Cliente Extranjero",
        )

        cls.supplier_a1 = create_supplier(
            business=cls.business_a,
            status=cls.active_status,
            name="Proveedor Uno",
        )

        cls.supplier_a2 = create_supplier(
            business=cls.business_a,
            status=cls.active_status,
            name="Proveedor Dos",
        )

        cls.supplier_b = create_supplier(
            business=cls.business_b,
            status=cls.active_status,
            name="Proveedor Extranjero",
        )

    def setUp(self):
        self.authenticate_as(
            self.admin_user
        )

    def _get_customers_summary(
        self,
        *,
        customer=None,
        business=None,
        date_from="2026-08-01",
        date_to="2026-08-31",
    ):
        business = (
            business
            or self.business_a
        )

        payload = {
            "business_public_id": str(
                business.public_id
            ),
            "date_from": date_from,
            "date_to": date_to,
        }

        if customer is not None:
            payload["customer_public_id"] = str(
                customer.public_id
            )

        return self.client.get(
            "/api/reports/customers-summary/",
            payload,
        )

    def _get_suppliers_summary(
        self,
        *,
        supplier=None,
        business=None,
        date_from="2026-08-01",
        date_to="2026-08-31",
    ):
        business = (
            business
            or self.business_a
        )

        payload = {
            "business_public_id": str(
                business.public_id
            ),
            "date_from": date_from,
            "date_to": date_to,
        }

        if supplier is not None:
            payload["supplier_public_id"] = str(
                supplier.public_id
            )

        return self.client.get(
            "/api/reports/suppliers-summary/",
            payload,
        )

    def test_customer_summary_groups_sales_by_customer(
        self,
    ):
        create_transaction(
            business=self.business_a,
            created_by=self.cashier_user,
            customer=self.customer_a1,
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
            created_by=self.cashier_user,
            customer=self.customer_a1,
            status=self.active_status,
            transaction_type="sale",
            total_value=Decimal("500.00"),
            created_at=datetime(
                2026,
                8,
                10,
                12,
                tzinfo=timezone.utc,
            ),
        )

        create_transaction(
            business=self.business_a,
            created_by=self.cashier_user,
            customer=self.customer_a2,
            status=self.active_status,
            transaction_type="sale",
            total_value=Decimal("300.00"),
            created_at=datetime(
                2026,
                8,
                15,
                12,
                tzinfo=timezone.utc,
            ),
        )

        response = (
            self._get_customers_summary()
        )

        self.assertEqual(
            response.status_code,
            status.HTTP_200_OK,
            msg=response.data,
        )

        self.assertEqual(
            response.data["totals"][
                "customers_count"
            ],
            2,
        )

        self.assertEqual(
            response.data["totals"][
                "transactions_count"
            ],
            3,
        )

        self.assertEqual(
            response.data["totals"][
                "total_amount"
            ],
            "1800.00",
        )

        first_customer = (
            response.data["results"][0]
        )

        self.assertEqual(
            first_customer["customer"][
                "full_name"
            ],
            "Cliente Uno",
        )

        self.assertEqual(
            first_customer[
                "transactions_count"
            ],
            2,
        )

        self.assertEqual(
            first_customer["total_amount"],
            "1500.00",
        )

        self.assertEqual(
            first_customer["average_ticket"],
            "750.00",
        )

    def test_customer_summary_can_filter_specific_customer(
        self,
    ):
        create_transaction(
            business=self.business_a,
            created_by=self.cashier_user,
            customer=self.customer_a1,
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
            customer=self.customer_a2,
            status=self.active_status,
            transaction_type="sale",
            total_value=Decimal("400.00"),
            created_at=datetime(
                2026,
                8,
                6,
                12,
                tzinfo=timezone.utc,
            ),
        )

        response = self._get_customers_summary(
            customer=self.customer_a2
        )

        self.assertEqual(
            response.status_code,
            status.HTTP_200_OK,
            msg=response.data,
        )

        self.assertEqual(
            len(response.data["results"]),
            1,
        )

        self.assertEqual(
            response.data["results"][0][
                "customer"
            ]["public_id"],
            str(self.customer_a2.public_id),
        )

        self.assertEqual(
            response.data["totals"][
                "total_amount"
            ],
            "400.00",
        )

    def test_supplier_summary_groups_purchases_by_supplier(
        self,
    ):
        create_transaction(
            business=self.business_a,
            created_by=self.admin_user,
            supplier=self.supplier_a1,
            status=self.active_status,
            transaction_type="purchase",
            total_value=Decimal("2000.00"),
            created_at=datetime(
                2026,
                8,
                4,
                12,
                tzinfo=timezone.utc,
            ),
        )

        create_transaction(
            business=self.business_a,
            created_by=self.admin_user,
            supplier=self.supplier_a1,
            status=self.active_status,
            transaction_type="purchase",
            total_value=Decimal("1000.00"),
            created_at=datetime(
                2026,
                8,
                8,
                12,
                tzinfo=timezone.utc,
            ),
        )

        create_transaction(
            business=self.business_a,
            created_by=self.admin_user,
            supplier=self.supplier_a2,
            status=self.active_status,
            transaction_type="purchase",
            total_value=Decimal("500.00"),
            created_at=datetime(
                2026,
                8,
                12,
                12,
                tzinfo=timezone.utc,
            ),
        )

        response = (
            self._get_suppliers_summary()
        )

        self.assertEqual(
            response.status_code,
            status.HTTP_200_OK,
            msg=response.data,
        )

        self.assertEqual(
            response.data["totals"][
                "suppliers_count"
            ],
            2,
        )

        self.assertEqual(
            response.data["totals"][
                "transactions_count"
            ],
            3,
        )

        self.assertEqual(
            response.data["totals"][
                "total_amount"
            ],
            "3500.00",
        )

        first_supplier = (
            response.data["results"][0]
        )

        self.assertEqual(
            first_supplier["supplier"][
                "name"
            ],
            "Proveedor Uno",
        )

        self.assertEqual(
            first_supplier["total_amount"],
            "3000.00",
        )

        self.assertEqual(
            first_supplier[
                "average_purchase"
            ],
            "1500.00",
        )

    def test_transactions_outside_date_range_are_excluded(
        self,
    ):
        create_transaction(
            business=self.business_a,
            created_by=self.cashier_user,
            customer=self.customer_a1,
            status=self.active_status,
            transaction_type="sale",
            total_value=Decimal("900.00"),
            created_at=datetime(
                2026,
                9,
                1,
                12,
                tzinfo=timezone.utc,
            ),
        )

        response = (
            self._get_customers_summary()
        )

        self.assertEqual(
            response.status_code,
            status.HTTP_200_OK,
            msg=response.data,
        )

        self.assertEqual(
            response.data["totals"][
                "transactions_count"
            ],
            0,
        )

        self.assertEqual(
            response.data["totals"][
                "total_amount"
            ],
            "0.00",
        )

    def test_foreign_business_data_is_not_included(
        self,
    ):
        create_transaction(
            business=self.business_b,
            created_by=self.user_b,
            customer=self.customer_b,
            status=self.active_status,
            transaction_type="sale",
            total_value=Decimal("5000.00"),
            created_at=datetime(
                2026,
                8,
                10,
                12,
                tzinfo=timezone.utc,
            ),
        )

        response = (
            self._get_customers_summary()
        )

        self.assertEqual(
            response.status_code,
            status.HTTP_200_OK,
            msg=response.data,
        )

        self.assertEqual(
            response.data["totals"][
                "total_amount"
            ],
            "0.00",
        )

    def test_user_cannot_read_foreign_business_report(
        self,
    ):
        response = (
            self._get_customers_summary(
                business=self.business_b
            )
        )

        self.assertEqual(
            response.status_code,
            status.HTTP_403_FORBIDDEN,
        )

    def test_customer_from_other_business_returns_not_found(
        self,
    ):
        response = (
            self._get_customers_summary(
                customer=self.customer_b
            )
        )

        self.assertEqual(
            response.status_code,
            status.HTTP_404_NOT_FOUND,
        )

    def test_invalid_date_range_is_rejected(
        self,
    ):
        response = (
            self._get_customers_summary(
                date_from="2026-08-31",
                date_to="2026-08-01",
            )
        )

        self.assertEqual(
            response.status_code,
            status.HTTP_400_BAD_REQUEST,
        )