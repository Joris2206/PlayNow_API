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
    create_customer,
    create_debt,
    create_debt_payment,
    create_payment_method,
    create_product,
    create_role_user,
    create_transaction,
    create_variant,
    create_variant_type,
)


class DashboardOverviewTests(
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
            role=(
                BusinessMembership
                .ROLE_ADMIN
            ),
            status=cls.active_status,
        )

        (
            cls.cashier_user,
            cls.cashier_employee,
            _,
        ) = create_role_user(
            business=cls.business_a,
            role=(
                BusinessMembership
                .ROLE_CASHIER
            ),
            status=cls.active_status,
        )

        (
            cls.viewer_user,
            cls.viewer_employee,
            _,
        ) = create_role_user(
            business=cls.business_a,
            role=(
                BusinessMembership
                .ROLE_VIEWER
            ),
            status=cls.active_status,
        )

        (
            cls.seller_user,
            cls.seller_employee,
            _,
        ) = create_role_user(
            business=cls.business_a,
            role=(
                BusinessMembership
                .ROLE_SELLER
            ),
            status=cls.active_status,
        )

        cls.customer = create_customer(
            business=cls.business_a,
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

    def _get_dashboard(
        self,
        *,
        business=None,
        date_from="2026-08-01",
        date_to="2026-08-31",
        threshold=5,
    ):
        business = (
            business
            or self.business_a
        )

        return self.client.get(
            "/api/dashboard/overview/",
            {
                "business_public_id": str(
                    business.public_id
                ),
                "date_from": date_from,
                "date_to": date_to,
                "low_stock_threshold": (
                    threshold
                ),
            },
        )

    def test_dashboard_calculates_transaction_cards(
        self,
    ):
        create_transaction(
            business=self.business_a,
            created_by=self.cashier_user,
            customer=self.customer,
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
            total_value=Decimal("300.00"),
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

        response = self._get_dashboard()

        self.assertEqual(
            response.status_code,
            status.HTTP_200_OK,
            msg=response.data,
        )

        cards = response.data["cards"]

        self.assertEqual(
            cards["sales_total"],
            "1000.00",
        )

        self.assertEqual(
            cards["purchases_total"],
            "300.00",
        )

        self.assertEqual(
            cards["expenses_total"],
            "100.00",
        )

        self.assertEqual(
            cards[
                "gross_margin_before_costs"
            ],
            "600.00",
        )

        activity = response.data[
            "activity"
        ]

        self.assertEqual(
            activity["sales_count"],
            1,
        )

        self.assertEqual(
            activity["purchases_count"],
            1,
        )

        self.assertEqual(
            activity["expenses_count"],
            1,
        )

    def test_dashboard_includes_debt_information(
        self,
    ):
        tx = create_transaction(
            business=self.business_a,
            created_by=self.cashier_user,
            customer=self.customer,
            employee=self.seller_employee,
            payment_method=self.cash_method,
            status=self.active_status,
            transaction_type="sale",
            total_value=Decimal("1000.00"),
            is_debt=True,
            created_at=datetime(
                2026,
                8,
                5,
                12,
                tzinfo=timezone.utc,
            ),
        )

        debt = create_debt(
            transaction=tx,
            total_amount=Decimal("1000.00"),
        )

        payment = create_debt_payment(
            debt=debt,
            payment_method=self.cash_method,
            amount=Decimal("300.00"),
        )

        debt.payments.filter(
            pk=payment.pk
        ).update(
            payment_date=date(
                2026,
                8,
                15,
            )
        )

        response = self._get_dashboard()

        self.assertEqual(
            response.status_code,
            status.HTTP_200_OK,
            msg=response.data,
        )

        self.assertEqual(
            response.data["cards"][
                "outstanding_debt"
            ],
            "700.00",
        )

        self.assertEqual(
            response.data["cards"][
                "debt_payments_received"
            ],
            "300.00",
        )

        self.assertEqual(
            response.data["activity"][
                "pending_debts_count"
            ],
            1,
        )

    def test_dashboard_includes_cash_and_commissions(
        self,
    ):
        create_cash_register(
            business=self.business_a,
            employee=self.cashier_employee,
            opened_by=self.cashier_user,
            opening_balance=Decimal(
                "1000.00"
            ),
            register_status=(
                CashRegister.STATUS_CLOSED
            ),
            expected_closing_balance=(
                Decimal("1500.00")
            ),
            closing_balance=Decimal(
                "1490.00"
            ),
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
            commission_total=Decimal(
                "500.00"
            ),
            net_commission_payable=(
                Decimal("400.00")
            ),
            settlement_status=(
                CommissionSettlement
                .STATUS_PENDING
            ),
        )

        response = self._get_dashboard()

        self.assertEqual(
            response.status_code,
            status.HTTP_200_OK,
            msg=response.data,
        )

        self.assertEqual(
            response.data["cards"][
                "cash_difference"
            ],
            "-10.00",
        )

        self.assertEqual(
            response.data["cards"][
                "pending_commissions"
            ],
            "400.00",
        )

        self.assertEqual(
            response.data["cash"][
                "closed_count"
            ],
            1,
        )

    def test_dashboard_counts_inventory_without_duplication(
        self,
    ):
        create_product(
            business=self.business_a,
            status=self.active_status,
            title="Producto simple",
            stock=4,
        )

        product_with_variants = (
            create_product(
                business=self.business_a,
                status=self.active_status,
                title="Producto variante",
                stock=999,
            )
        )

        variant_type = create_variant_type(
            product=product_with_variants,
            status=self.active_status,
            name="Talla",
        )

        create_variant(
            variant_type=variant_type,
            status=self.active_status,
            label="S",
            stock=3,
        )

        create_variant(
            variant_type=variant_type,
            status=self.active_status,
            label="L",
            stock=8,
        )

        response = self._get_dashboard(
            threshold=5
        )

        self.assertEqual(
            response.status_code,
            status.HTTP_200_OK,
            msg=response.data,
        )

        self.assertEqual(
            response.data["cards"][
                "current_inventory_units"
            ],
            15,
        )

        self.assertEqual(
            response.data["activity"][
                "low_stock_items_count"
            ],
            2,
        )

    def test_viewer_can_read_dashboard(
        self,
    ):
        self.authenticate_as(
            self.viewer_user
        )

        response = self._get_dashboard()

        self.assertEqual(
            response.status_code,
            status.HTTP_200_OK,
            msg=response.data,
        )

    def test_seller_cannot_read_dashboard(
        self,
    ):
        self.authenticate_as(
            self.seller_user
        )

        response = self._get_dashboard()

        self.assertEqual(
            response.status_code,
            status.HTTP_403_FORBIDDEN,
        )

    def test_foreign_business_is_forbidden(
        self,
    ):
        response = self._get_dashboard(
            business=self.business_b
        )

        self.assertEqual(
            response.status_code,
            status.HTTP_403_FORBIDDEN,
        )

    def test_invalid_date_range_is_rejected(
        self,
    ):
        response = self._get_dashboard(
            date_from="2026-08-31",
            date_to="2026-08-01",
        )

        self.assertEqual(
            response.status_code,
            status.HTTP_400_BAD_REQUEST,
        )