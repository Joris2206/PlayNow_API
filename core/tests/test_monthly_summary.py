from datetime import (
    date,
    datetime,
    timezone,
)
from decimal import Decimal

from rest_framework import status

from core.models import (
    BusinessMembership,
    CashRegister,
    CommissionSettlement,
    PaymentMethod,
)
from core.tests.base import (
    BusinessIsolationTestCase,
)
from core.tests.factories import (
    create_cash_register,
    create_commission_settlement,
    create_debt,
    create_debt_payment,
    create_payment_method,
    create_role_user,
    create_transaction,
)


class MonthlySummaryTests(
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

        (
            _,
            cls.seller_employee,
            _,
        ) = create_role_user(
            business=cls.business_a,
            role=BusinessMembership.ROLE_SELLER,
            status=cls.active_status,
        )

        cls.cash_method = create_payment_method(
            business=cls.business_a,
            status=cls.active_status,
            name="Efectivo",
            method_type=PaymentMethod.TYPE_CASH,
        )

    def setUp(self):
        self.authenticate_as(
            self.admin_user
        )

    def _get_summary(
        self,
        *,
        year=2026,
        month=8,
    ):
        return self.client.get(
            "/api/reports/monthly-summary/",
            {
                "business_public_id": str(
                    self.business_a.public_id
                ),
                "year": year,
                "month": month,
            },
        )

    def test_monthly_summary_calculates_transactions(
        self,
    ):
        create_transaction(
            business=self.business_a,
            created_by=self.cashier_user,
            employee=self.seller_employee,
            payment_method=self.cash_method,
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
            created_by=self.admin_user,
            payment_method=self.cash_method,
            status=self.active_status,
            transaction_type="purchase",
            total_value=Decimal("400.00"),
            created_at=datetime(
                2026,
                8,
                6,
                12,
                tzinfo=timezone.utc,
            ),
        )

        create_transaction(
            business=self.business_a,
            created_by=self.admin_user,
            payment_method=self.cash_method,
            status=self.active_status,
            transaction_type="expense",
            total_value=Decimal("100.00"),
            created_at=datetime(
                2026,
                8,
                7,
                12,
                tzinfo=timezone.utc,
            ),
        )

        response = self._get_summary()

        self.assertEqual(
            response.status_code,
            status.HTTP_200_OK,
            msg=response.data,
        )

        self.assertEqual(
            response.data[
                "transactions"
            ]["sales"]["total"],
            "1000.00",
        )

        self.assertEqual(
            response.data[
                "transactions"
            ]["purchases"]["total"],
            "400.00",
        )

        self.assertEqual(
            response.data[
                "transactions"
            ]["expenses"]["total"],
            "100.00",
        )

    def test_transactions_outside_month_are_excluded(
        self,
    ):
        create_transaction(
            business=self.business_a,
            created_by=self.cashier_user,
            employee=self.seller_employee,
            payment_method=self.cash_method,
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

        response = self._get_summary()

        self.assertEqual(
            response.status_code,
            status.HTTP_200_OK,
            msg=response.data,
        )

        self.assertEqual(
            response.data[
                "transactions"
            ]["sales"]["total"],
            "0.00",
        )

    def test_monthly_summary_includes_closed_cash_registers(
        self,
    ):
        register = create_cash_register(
            business=self.business_a,
            employee=self.cashier_employee,
            opened_by=self.cashier_user,
            opening_balance=Decimal("1000.00"),
            register_status=(
                CashRegister.STATUS_CLOSED
            ),
            expected_closing_balance=(
                Decimal("1500.00")
            ),
            closing_balance=Decimal("1490.00"),
            difference=Decimal("-10.00"),
            closed_by=self.cashier_user,
            open_time=datetime(
                2026,
                8,
                5,
                8,
                tzinfo=timezone.utc,
            ),
            close_time=datetime(
                2026,
                8,
                5,
                18,
                tzinfo=timezone.utc,
            ),
        )

        response = self._get_summary()

        self.assertEqual(
            response.status_code,
            status.HTTP_200_OK,
            msg=response.data,
        )

        cash = response.data[
            "cash_registers"
        ]

        self.assertEqual(
            cash["closed_count"],
            1,
        )

        self.assertEqual(
            cash["difference_total"],
            "-10.00",
        )

        self.assertEqual(
            cash["shortages_total"],
            "-10.00",
        )

    def test_monthly_summary_includes_commissions(
        self,
    ):
        create_commission_settlement(
            employee=self.seller_employee,
            created_by=self.admin_user,
            period_start=date(
                2026,
                8,
                1,
            ),
            period_end=date(
                2026,
                8,
                31,
            ),
            sales_total=Decimal("20000.00"),
            commission_total=Decimal("1000.00"),
            employee_advances=Decimal("300.00"),
            advance_balance=Decimal("300.00"),
            net_commission_payable=(
                Decimal("700.00")
            ),
            settlement_status=(
                CommissionSettlement
                .STATUS_PENDING
            ),
        )

        response = self._get_summary()

        self.assertEqual(
            response.status_code,
            status.HTTP_200_OK,
            msg=response.data,
        )

        commissions = response.data[
            "commissions"
        ]

        self.assertEqual(
            commissions[
                "gross_commission_total"
            ],
            "1000.00",
        )

        self.assertEqual(
            commissions[
                "employee_advances"
            ],
            "300.00",
        )

        self.assertEqual(
            commissions[
                "net_commission_payable"
            ],
            "700.00",
        )

        self.assertEqual(
            commissions["pending"]["total"],
            "700.00",
        )

    def test_user_cannot_read_foreign_business_summary(
        self,
    ):
        response = self.client.get(
            "/api/reports/monthly-summary/",
            {
                "business_public_id": str(
                    self.business_b.public_id
                ),
                "year": 2026,
                "month": 8,
            },
        )

        self.assertEqual(
            response.status_code,
            status.HTTP_403_FORBIDDEN,
        )

    def test_invalid_month_is_rejected(
        self,
    ):
        response = self._get_summary(
            month=13
        )

        self.assertEqual(
            response.status_code,
            status.HTTP_400_BAD_REQUEST,
        )