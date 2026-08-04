from datetime import (
    date,
    datetime,
    timezone,
)
from decimal import Decimal

from rest_framework import status

from core.models import (
    BusinessMembership,
    CommissionSettlement,
)
from core.tests.base import (
    BusinessIsolationTestCase,
)
from core.tests.factories import (
    create_commission_plan,
    create_role_user,
    create_transaction,
)


class CommissionSettlementTests(
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
                full_name="Vendedor de prueba",
            )
        )

        cls.plan = create_commission_plan(
            employee=cls.seller,
            percentage=Decimal("5.00"),
            valid_from=date(
                2026,
                8,
                1,
            ),
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

    def _create_settlement(self):
        return self.client.post(
            "/api/commission-settlements/",
            {
                "employee": str(
                    self.seller.public_id
                ),
                "period_start": (
                    "2026-08-01"
                ),
                "period_end": (
                    "2026-08-31"
                ),
            },
            format="json",
        )

    def test_create_commission_settlement(
        self,
    ):
        response = self._create_settlement()

        self.assertEqual(
            response.status_code,
            status.HTTP_201_CREATED,
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

        self.assertEqual(
            response.data["status"],
            CommissionSettlement
            .STATUS_PENDING,
        )

        settlement = (
            CommissionSettlement.objects
            .get(
                public_id=response.data[
                    "public_id"
                ]
            )
        )

        self.assertEqual(
            settlement.created_by,
            self.admin_user,
        )

    def test_cannot_create_duplicate_settlement(
        self,
    ):
        first_response = (
            self._create_settlement()
        )

        self.assertEqual(
            first_response.status_code,
            status.HTTP_201_CREATED,
        )

        second_response = (
            self._create_settlement()
        )

        self.assertEqual(
            second_response.status_code,
            status.HTTP_400_BAD_REQUEST,
        )

    def test_mark_settlement_as_paid(
        self,
    ):
        create_response = (
            self._create_settlement()
        )

        settlement_public_id = (
            create_response.data[
                "public_id"
            ]
        )

        response = self.client.post(
            (
                "/api/commission-settlements/"
                f"{settlement_public_id}/"
                "mark-paid/"
            ),
            {},
            format="json",
        )

        self.assertEqual(
            response.status_code,
            status.HTTP_200_OK,
            msg=response.data,
        )

        self.assertEqual(
            response.data["status"],
            CommissionSettlement
            .STATUS_PAID,
        )

        self.assertIsNotNone(
            response.data["paid_at"]
        )

    def test_cannot_mark_paid_twice(
        self,
    ):
        create_response = (
            self._create_settlement()
        )

        endpoint = (
            "/api/commission-settlements/"
            f"{create_response.data['public_id']}/"
            "mark-paid/"
        )

        first_response = self.client.post(
            endpoint,
            {},
            format="json",
        )

        self.assertEqual(
            first_response.status_code,
            status.HTTP_200_OK,
        )

        second_response = self.client.post(
            endpoint,
            {},
            format="json",
        )

        self.assertEqual(
            second_response.status_code,
            status.HTTP_400_BAD_REQUEST,
        )

    def test_settlement_cannot_be_updated(
        self,
    ):
        create_response = (
            self._create_settlement()
        )

        endpoint = (
            "/api/commission-settlements/"
            f"{create_response.data['public_id']}/"
        )

        patch_response = self.client.patch(
            endpoint,
            {
                "commission_total": (
                    "999999.00"
                )
            },
            format="json",
        )

        self.assertEqual(
            patch_response.status_code,
            status.HTTP_405_METHOD_NOT_ALLOWED,
        )

    def test_settlement_cannot_be_deleted(
        self,
    ):
        create_response = (
            self._create_settlement()
        )

        endpoint = (
            "/api/commission-settlements/"
            f"{create_response.data['public_id']}/"
        )

        response = self.client.delete(
            endpoint
        )

        self.assertEqual(
            response.status_code,
            status.HTTP_405_METHOD_NOT_ALLOWED,
        )

class CommissionSettlementPermissionTests(
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

        cls.cashier_user, _, _ = (
            create_role_user(
                business=cls.business_a,
                role=(
                    BusinessMembership
                    .ROLE_CASHIER
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

        create_commission_plan(
            employee=cls.seller,
            percentage=Decimal("5.00"),
            valid_from=date(
                2026,
                8,
                1,
            ),
        )

    def test_cashier_cannot_create_settlement(
        self,
    ):
        self.authenticate_as(
            self.cashier_user
        )

        response = self.client.post(
            "/api/commission-settlements/",
            {
                "employee": str(
                    self.seller.public_id
                ),
                "period_start": (
                    "2026-08-01"
                ),
                "period_end": (
                    "2026-08-31"
                ),
            },
            format="json",
        )

        self.assertEqual(
            response.status_code,
            status.HTTP_403_FORBIDDEN,
        )

    def test_cashier_cannot_list_settlements(
        self,
    ):
        self.authenticate_as(
            self.cashier_user
        )

        response = self.client.get(
            "/api/commission-settlements/"
        )

        self.assertEqual(
            response.status_code,
            status.HTTP_200_OK,
        )

        results = (
            response.data.get(
                "results",
                response.data,
            )
        )

        self.assertEqual(
            len(results),
            0,
        )