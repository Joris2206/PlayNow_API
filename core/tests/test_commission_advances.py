from datetime import date, datetime, timezone
from decimal import Decimal

from rest_framework import status

from core.models import (
    BusinessMembership,
    CashMovement,
    CommissionSettlement,
    PaymentMethod,
)
from core.tests.base import BusinessIsolationTestCase
from core.tests.factories import (
    create_cash_movement,
    create_cash_register,
    create_commission_plan,
    create_payment_method,
    create_role_user,
    create_transaction,
)


class CommissionAdvancePreviewTests(
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
            full_name="Vendedor principal",
        )

        (
            _,
            cls.other_seller,
            _,
        ) = create_role_user(
            business=cls.business_a,
            role=BusinessMembership.ROLE_SELLER,
            status=cls.active_status,
            full_name="Otro vendedor",
        )

        cls.cash_method = create_payment_method(
            business=cls.business_a,
            status=cls.active_status,
            name="Efectivo",
            method_type=PaymentMethod.TYPE_CASH,
        )

        cls.commission_plan = create_commission_plan(
            employee=cls.seller_employee,
            percentage=Decimal("5.00"),
            valid_from=date(2026, 8, 1),
        )

        # Ventas totales: C$20,000
        # Comisión al 5%: C$1,000
        create_transaction(
            business=cls.business_a,
            created_by=cls.cashier_user,
            employee=cls.seller_employee,
            payment_method=cls.cash_method,
            status=cls.active_status,
            transaction_type="sale",
            total_value=Decimal("12000.00"),
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
            created_by=cls.cashier_user,
            employee=cls.seller_employee,
            payment_method=cls.cash_method,
            status=cls.active_status,
            transaction_type="sale",
            total_value=Decimal("8000.00"),
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
        self.authenticate_as(self.admin_user)

        self.register = create_cash_register(
            business=self.business_a,
            employee=self.cashier_employee,
            opened_by=self.cashier_user,
            opening_balance=Decimal("5000.00"),
            open_time=datetime(
                2026,
                8,
                1,
                8,
                0,
                tzinfo=timezone.utc,
            ),
        )

    def _commission_preview(
        self,
        *,
        date_from="2026-08-01",
        date_to="2026-08-31",
    ):
        return self.client.get(
            "/api/reports/employee-commission/",
            {
                "business_public_id": str(
                    self.business_a.public_id
                ),
                "employee_public_id": str(
                    self.seller_employee.public_id
                ),
                "date_from": date_from,
                "date_to": date_to,
            },
        )

    def _create_employee_movement(
        self,
        *,
        movement_type,
        amount,
        employee=None,
        created_at=None,
    ):
        movement = create_cash_movement(
            cash_register=self.register,
            created_by=self.cashier_user,
            employee=(
                employee
                or self.seller_employee
            ),
            payment_method=self.cash_method,
            movement_type=movement_type,
            amount=Decimal(amount),
        )

        if created_at is not None:
            CashMovement.objects.filter(
                pk=movement.pk
            ).update(
                created_at=created_at
            )

            movement.refresh_from_db()

        return movement

    def test_advance_reduces_net_commission(
        self,
    ):
        # Comisión bruta: 1000
        # Adelanto: 300
        # Neto: 700
        self._create_employee_movement(
            movement_type=(
                CashMovement.TYPE_EMPLOYEE_ADVANCE
            ),
            amount="300.00",
            created_at=datetime(
                2026,
                8,
                15,
                12,
                0,
                tzinfo=timezone.utc,
            ),
        )

        response = self._commission_preview()

        self.assertEqual(
            response.status_code,
            status.HTTP_200_OK,
            msg=response.data,
        )

        self.assertEqual(
            response.data["commission_total"],
            "1000.00",
        )

        self.assertEqual(
            response.data["employee_advances"],
            "300.00",
        )

        self.assertEqual(
            response.data["employee_repayments"],
            "0.00",
        )

        self.assertEqual(
            response.data["advance_balance"],
            "300.00",
        )

        self.assertEqual(
            response.data["net_commission_payable"],
            "700.00",
        )

        self.assertEqual(
            response.data[
                "remaining_advance_balance"
            ],
            "0.00",
        )

    def test_repayment_reduces_advance_balance(
        self,
    ):
        # Adelanto: 600
        # Devolución: 200
        # Saldo de adelanto: 400
        # Comisión bruta: 1000
        # Neto: 600
        self._create_employee_movement(
            movement_type=(
                CashMovement.TYPE_EMPLOYEE_ADVANCE
            ),
            amount="600.00",
            created_at=datetime(
                2026,
                8,
                10,
                12,
                0,
                tzinfo=timezone.utc,
            ),
        )

        self._create_employee_movement(
            movement_type=(
                CashMovement.TYPE_EMPLOYEE_REPAYMENT
            ),
            amount="200.00",
            created_at=datetime(
                2026,
                8,
                20,
                12,
                0,
                tzinfo=timezone.utc,
            ),
        )

        response = self._commission_preview()

        self.assertEqual(
            response.status_code,
            status.HTTP_200_OK,
            msg=response.data,
        )

        self.assertEqual(
            response.data["employee_advances"],
            "600.00",
        )

        self.assertEqual(
            response.data["employee_repayments"],
            "200.00",
        )

        self.assertEqual(
            response.data["advance_balance"],
            "400.00",
        )

        self.assertEqual(
            response.data["net_commission_payable"],
            "600.00",
        )

    def test_advance_above_commission_never_makes_payment_negative(
        self,
    ):
        # Comisión: 1000
        # Adelanto: 1500
        # Neto pagable: 0
        # Saldo todavía pendiente: 500
        self._create_employee_movement(
            movement_type=(
                CashMovement.TYPE_EMPLOYEE_ADVANCE
            ),
            amount="1500.00",
            created_at=datetime(
                2026,
                8,
                12,
                12,
                0,
                tzinfo=timezone.utc,
            ),
        )

        response = self._commission_preview()

        self.assertEqual(
            response.status_code,
            status.HTTP_200_OK,
            msg=response.data,
        )

        self.assertEqual(
            response.data["advance_balance"],
            "1500.00",
        )

        self.assertEqual(
            response.data["net_commission_payable"],
            "0.00",
        )

        self.assertEqual(
            response.data[
                "remaining_advance_balance"
            ],
            "500.00",
        )

    def test_other_employee_movements_do_not_affect_commission(
        self,
    ):
        self._create_employee_movement(
            employee=self.other_seller,
            movement_type=(
                CashMovement.TYPE_EMPLOYEE_ADVANCE
            ),
            amount="900.00",
            created_at=datetime(
                2026,
                8,
                12,
                12,
                0,
                tzinfo=timezone.utc,
            ),
        )

        response = self._commission_preview()

        self.assertEqual(
            response.status_code,
            status.HTTP_200_OK,
            msg=response.data,
        )

        self.assertEqual(
            response.data["employee_advances"],
            "0.00",
        )

        self.assertEqual(
            response.data["advance_balance"],
            "0.00",
        )

        self.assertEqual(
            response.data["net_commission_payable"],
            "1000.00",
        )

    def test_movements_after_period_end_are_not_included(
        self,
    ):
        self._create_employee_movement(
            movement_type=(
                CashMovement.TYPE_EMPLOYEE_ADVANCE
            ),
            amount="400.00",
            created_at=datetime(
                2026,
                9,
                2,
                12,
                0,
                tzinfo=timezone.utc,
            ),
        )

        response = self._commission_preview()

        self.assertEqual(
            response.status_code,
            status.HTTP_200_OK,
            msg=response.data,
        )

        self.assertEqual(
            response.data["employee_advances"],
            "0.00",
        )

        self.assertEqual(
            response.data["net_commission_payable"],
            "1000.00",
        )


class CommissionSettlementAdvanceTests(
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

        create_commission_plan(
            employee=cls.seller_employee,
            percentage=Decimal("5.00"),
            valid_from=date(2026, 8, 1),
        )

        # Comisión bruta: 1000
        create_transaction(
            business=cls.business_a,
            created_by=cls.cashier_user,
            employee=cls.seller_employee,
            payment_method=cls.cash_method,
            status=cls.active_status,
            transaction_type="sale",
            total_value=Decimal("20000.00"),
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
        self.authenticate_as(self.admin_user)

        self.register = create_cash_register(
            business=self.business_a,
            employee=self.cashier_employee,
            opened_by=self.cashier_user,
            opening_balance=Decimal("5000.00"),
            open_time=datetime(
                2026,
                8,
                1,
                8,
                0,
                tzinfo=timezone.utc,
            ),
        )

    def _create_settlement(self):
        return self.client.post(
            "/api/commission-settlements/",
            {
                "employee_public_id": str(
                    self.seller_employee.public_id
                ),
                "period_start": "2026-08-01",
                "period_end": "2026-08-31",
            },
            format="json",
        )

    def test_settlement_freezes_advance_and_net_values(
        self,
    ):
        advance = create_cash_movement(
            cash_register=self.register,
            created_by=self.cashier_user,
            employee=self.seller_employee,
            payment_method=self.cash_method,
            movement_type=(
                CashMovement.TYPE_EMPLOYEE_ADVANCE
            ),
            amount=Decimal("300.00"),
        )

        CashMovement.objects.filter(
            pk=advance.pk
        ).update(
            created_at=datetime(
                2026,
                8,
                15,
                12,
                0,
                tzinfo=timezone.utc,
            )
        )

        response = self._create_settlement()

        self.assertEqual(
            response.status_code,
            status.HTTP_201_CREATED,
            msg=response.data,
        )

        self.assertEqual(
            response.data["commission_total"],
            "1000.00",
        )

        self.assertEqual(
            response.data["employee_advances"],
            "300.00",
        )

        self.assertEqual(
            response.data["advance_balance"],
            "300.00",
        )

        self.assertEqual(
            response.data["net_commission_payable"],
            "700.00",
        )

        settlement = CommissionSettlement.objects.get(
            public_id=response.data["public_id"]
        )

        self.assertEqual(
            settlement.employee_advances,
            Decimal("300.00"),
        )

        self.assertEqual(
            settlement.net_commission_payable,
            Decimal("700.00"),
        )

        # Se crea otro adelanto después de liquidar.
        later_advance = create_cash_movement(
            cash_register=self.register,
            created_by=self.cashier_user,
            employee=self.seller_employee,
            payment_method=self.cash_method,
            movement_type=(
                CashMovement.TYPE_EMPLOYEE_ADVANCE
            ),
            amount=Decimal("200.00"),
        )

        CashMovement.objects.filter(
            pk=later_advance.pk
        ).update(
            created_at=datetime(
                2026,
                9,
                2,
                12,
                0,
                tzinfo=timezone.utc,
            )
        )

        # Volvemos a consultar la liquidación existente.
        detail_response = self.client.get(
            (
                "/api/commission-settlements/"
                f"{settlement.public_id}/"
            )
        )

        self.assertEqual(
            detail_response.status_code,
            status.HTTP_200_OK,
            msg=detail_response.data,
        )

        # Sigue mostrando lo congelado en agosto.
        self.assertEqual(
            detail_response.data[
                "employee_advances"
            ],
            "300.00",
        )

        self.assertEqual(
            detail_response.data[
                "net_commission_payable"
            ],
            "700.00",
        )
