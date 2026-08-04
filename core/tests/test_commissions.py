from datetime import (
    date,
    datetime,
    timezone,
)
from decimal import Decimal

from rest_framework import status

from core.models import (
    BusinessMembership,
    EmployeeCommissionPlan,
)
from core.tests.base import (
    BusinessIsolationTestCase,
)
from core.tests.factories import (
    create_role_user,
    create_transaction,
)


class EmployeeCommissionTests(
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

        cls.plan = (
            EmployeeCommissionPlan.objects
            .create(
                employee=cls.seller,
                percentage=Decimal("5.00"),
                valid_from=date(
                    2026,
                    8,
                    1,
                ),
                valid_until=None,
                is_active=True,
            )
        )

        create_transaction(
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

        create_transaction(
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

    def test_commission_preview(
        self,
    ):
        response = self.client.get(
            "/api/reports/employee-commission/",
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
            response.data["sales_count"],
            2,
        )

        self.assertEqual(
            response.data["sales_total"],
            "1500.00",
        )

        self.assertEqual(
            response.data[
                "commission_percentage"
            ],
            "5.00",
        )

        self.assertEqual(
            response.data[
                "commission_total"
            ],
            "75.00",
        )