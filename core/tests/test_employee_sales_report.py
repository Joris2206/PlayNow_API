from datetime import (
    datetime,
    timezone,
)
from decimal import Decimal

from rest_framework import status

from core.models import BusinessMembership
from core.tests.base import (
    BusinessIsolationTestCase,
)
from core.tests.factories import (
    create_role_user,
    create_transaction,
)


class EmployeeSalesReportTests(
    BusinessIsolationTestCase
):
    @classmethod
    def setUpTestData(cls):
        super().setUpTestData()

        cls.admin_user, _, _ = (
            create_role_user(
                business=cls.business_a,
                role=(
                    BusinessMembership
                    .ROLE_ADMIN
                ),
                status=cls.active_status,
            )
        )

        _, cls.seller, _ = (
            create_role_user(
                business=cls.business_a,
                role=(
                    BusinessMembership
                    .ROLE_SELLER
                ),
                status=cls.active_status,
            )
        )

        cls.sale_1 = create_transaction(
            business=cls.business_a,
            created_by=cls.admin_user,
            employee=cls.seller,
            status=cls.active_status,
            total_value=Decimal("1000.00"),
            created_at=datetime(
                2026,
                8,
                5,
                12,
                0,
                tzinfo=timezone.utc,
            ),
        )

        cls.sale_2 = create_transaction(
            business=cls.business_a,
            created_by=cls.admin_user,
            employee=cls.seller,
            status=cls.active_status,
            total_value=Decimal("500.00"),
            created_at=datetime(
                2026,
                8,
                10,
                12,
                0,
                tzinfo=timezone.utc,
            ),
        )

    def setUp(self):
        self.authenticate_as(
            self.admin_user
        )

    def test_employee_sales_report(
        self,
    ):
        response = self.client.get(
            "/api/reports/employee-sales/",
            {
                "business_public_id": str(
                    self.business_a.public_id
                ),
                "employee_public_id": str(
                    self.seller.public_id
                ),
                "date_from": "2026-08-01",
                "date_to": "2026-08-31",
            },
        )

        self.assertEqual(
            response.status_code,
            status.HTTP_200_OK,
            msg=response.data,
        )

        self.assertEqual(
            response.data["summary"][
                "sales_count"
            ],
            2,
        )

        self.assertEqual(
            response.data["summary"][
                "sales_total"
            ],
            "1500.00",
        )

        self.assertEqual(
            response.data["summary"][
                "average_sale"
            ],
            "750.00",
        )