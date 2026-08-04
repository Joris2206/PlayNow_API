from datetime import datetime, timezone

from rest_framework import status

from core.models import BusinessMembership
from core.tests.base import BusinessIsolationTestCase
from core.tests.factories import create_role_user, create_transaction
from core.tests.helpers import get_public_ids


class TransactionFilterTests(BusinessIsolationTestCase):
    @classmethod
    def setUpTestData(cls):
        super().setUpTestData()
        cls.cashier, _, _ = create_role_user(
            business=cls.business_a,
            role=BusinessMembership.ROLE_CASHIER,
            status=cls.active_status,
        )
        _, cls.seller_a, _ = create_role_user(
            business=cls.business_a,
            role=BusinessMembership.ROLE_SELLER,
            status=cls.active_status,
        )
        _, cls.seller_b, _ = create_role_user(
            business=cls.business_a,
            role=BusinessMembership.ROLE_SELLER,
            status=cls.active_status,
        )
        cls.july_sale = create_transaction(
            business=cls.business_a,
            created_by=cls.cashier,
            employee=cls.seller_a,
            status=cls.active_status,
            created_at=datetime(2026, 7, 15, 12, 0, tzinfo=timezone.utc),
        )
        cls.august_sale = create_transaction(
            business=cls.business_a,
            created_by=cls.cashier,
            employee=cls.seller_a,
            status=cls.active_status,
            created_at=datetime(2026, 8, 10, 12, 0, tzinfo=timezone.utc),
        )
        cls.other_seller_sale = create_transaction(
            business=cls.business_a,
            created_by=cls.cashier,
            employee=cls.seller_b,
            status=cls.active_status,
            created_at=datetime(2026, 8, 11, 12, 0, tzinfo=timezone.utc),
        )

    def setUp(self):
        self.authenticate_as(self.cashier)

    def test_filter_by_employee_public_id(self):
        response = self.client.get(
            "/api/transactions/",
            {"employee_public_id": str(self.seller_a.public_id)},
        )
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        ids = get_public_ids(response)
        self.assertIn(str(self.july_sale.public_id), ids)
        self.assertIn(str(self.august_sale.public_id), ids)
        self.assertNotIn(str(self.other_seller_sale.public_id), ids)

    def test_filter_by_date_range(self):
        response = self.client.get(
            "/api/transactions/",
            {"date_from": "2026-08-01", "date_to": "2026-08-31"},
        )
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        ids = get_public_ids(response)
        self.assertNotIn(str(self.july_sale.public_id), ids)
        self.assertIn(str(self.august_sale.public_id), ids)
        self.assertIn(str(self.other_seller_sale.public_id), ids)

    def test_filter_by_employee_and_date_range(self):
        response = self.client.get(
            "/api/transactions/",
            {
                "employee_public_id": str(self.seller_a.public_id),
                "date_from": "2026-08-01",
                "date_to": "2026-08-31",
                "type": "sale",
            },
        )
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(get_public_ids(response), {str(self.august_sale.public_id)})
