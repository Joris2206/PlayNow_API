from decimal import Decimal

from rest_framework import status

from core.models import (
    BusinessMembership,
    CashMovement,
    CashRegister,
    PaymentMethod,
)
from core.tests.base import (
    BusinessIsolationTestCase,
)
from core.tests.factories import (
    create_cash_movement,
    create_cash_register,
    create_payment_method,
    create_role_user,
)


class CashMovementTests(
    BusinessIsolationTestCase
):
    @classmethod
    def setUpTestData(cls):
        super().setUpTestData()

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
            cls.seller_user,
            cls.seller_employee,
            _,
        ) = create_role_user(
            business=cls.business_a,
            role=BusinessMembership.ROLE_SELLER,
            status=cls.active_status,
        )

        (
            cls.foreign_cashier,
            cls.foreign_employee,
            _,
        ) = create_role_user(
            business=cls.business_b,
            role=BusinessMembership.ROLE_CASHIER,
            status=cls.active_status,
        )

        cls.cash_method = create_payment_method(
            business=cls.business_a,
            status=cls.active_status,
            name="Efectivo",
            method_type=PaymentMethod.TYPE_CASH,
        )

        cls.foreign_method = (
            create_payment_method(
                business=cls.business_b,
                status=cls.active_status,
                name="Efectivo B",
                method_type=(
                    PaymentMethod.TYPE_CASH
                ),
            )
        )

    def setUp(self):
        self.authenticate_as(
            self.cashier_user
        )

        self.register = create_cash_register(
            business=self.business_a,
            employee=self.cashier_employee,
            opened_by=self.cashier_user,
            opening_balance=Decimal(
                "1000.00"
            ),
        )

    def _post_movement(
        self,
        *,
        movement_type,
        amount="100.00",
        employee=None,
        payment_method=None,
    ):
        payload = {
            "cash_register": str(
                self.register.public_id
            ),
            "payment_method": str(
                (
                    payment_method
                    or self.cash_method
                ).public_id
            ),
            "movement_type": movement_type,
            "amount": amount,
            "note": "Movimiento de prueba",
        }

        if employee is not None:
            payload["employee"] = str(
                employee.public_id
            )

        return self.client.post(
            "/api/cash-movements/",
            payload,
            format="json",
        )

    def test_movement_types_have_correct_signed_amount(
        self,
    ):
        cases = [
            (
                CashMovement.TYPE_DEPOSIT,
                None,
                "100.00",
            ),
            (
                CashMovement.TYPE_WITHDRAWAL,
                None,
                "-100.00",
            ),
            (
                CashMovement.TYPE_EMPLOYEE_ADVANCE,
                self.seller_employee,
                "-100.00",
            ),
            (
                CashMovement.TYPE_EMPLOYEE_REPAYMENT,
                self.seller_employee,
                "100.00",
            ),
            (
                CashMovement.TYPE_OTHER_INCOME,
                None,
                "100.00",
            ),
            (
                CashMovement.TYPE_OTHER_EXPENSE,
                None,
                "-100.00",
            ),
        ]

        for (
            movement_type,
            employee,
            expected_signed_amount,
        ) in cases:
            with self.subTest(
                movement_type=movement_type
            ):
                response = self._post_movement(
                    movement_type=movement_type,
                    employee=employee,
                )

                self.assertEqual(
                    response.status_code,
                    status.HTTP_201_CREATED,
                    msg=response.data,
                )

                self.assertEqual(
                    response.data[
                        "signed_amount"
                    ],
                    expected_signed_amount,
                )

    def test_amount_must_be_positive(
        self,
    ):
        response = self._post_movement(
            movement_type=(
                CashMovement.TYPE_WITHDRAWAL
            ),
            amount="-100.00",
        )

        self.assertEqual(
            response.status_code,
            status.HTTP_400_BAD_REQUEST,
            msg=response.data,
        )

    def test_employee_advance_requires_employee(
        self,
    ):
        response = self._post_movement(
            movement_type=(
                CashMovement
                .TYPE_EMPLOYEE_ADVANCE
            ),
        )

        self.assertEqual(
            response.status_code,
            status.HTTP_400_BAD_REQUEST,
            msg=response.data,
        )

    def test_employee_repayment_requires_employee(
        self,
    ):
        response = self._post_movement(
            movement_type=(
                CashMovement
                .TYPE_EMPLOYEE_REPAYMENT
            ),
        )

        self.assertEqual(
            response.status_code,
            status.HTTP_400_BAD_REQUEST,
            msg=response.data,
        )

    def test_foreign_employee_is_rejected(
        self,
    ):
        response = self._post_movement(
            movement_type=(
                CashMovement
                .TYPE_EMPLOYEE_ADVANCE
            ),
            employee=self.foreign_employee,
        )

        self.assertEqual(
            response.status_code,
            status.HTTP_400_BAD_REQUEST,
            msg=response.data,
        )

    def test_foreign_payment_method_is_rejected(
        self,
    ):
        response = self._post_movement(
            movement_type=(
                CashMovement.TYPE_DEPOSIT
            ),
            payment_method=(
                self.foreign_method
            ),
        )

        self.assertEqual(
            response.status_code,
            status.HTTP_400_BAD_REQUEST,
            msg=response.data,
        )

    def test_cannot_create_movement_in_closed_register(
        self,
    ):
        self.register.status = (
            CashRegister.STATUS_CLOSED
        )

        self.register.closed_by = (
            self.cashier_user
        )

        self.register.closing_balance = (
            Decimal("1000.00")
        )

        self.register.expected_closing_balance = (
            Decimal("1000.00")
        )

        self.register.difference = (
            Decimal("0.00")
        )

        from django.utils import timezone

        self.register.close_time = (
            timezone.now()
        )

        self.register.save()

        response = self._post_movement(
            movement_type=(
                CashMovement.TYPE_DEPOSIT
            ),
        )

        self.assertEqual(
            response.status_code,
            status.HTTP_400_BAD_REQUEST,
            msg=response.data,
        )

    def test_created_by_is_authenticated_user(
        self,
    ):
        response = self._post_movement(
            movement_type=(
                CashMovement.TYPE_DEPOSIT
            ),
        )

        self.assertEqual(
            response.status_code,
            status.HTTP_201_CREATED,
            msg=response.data,
        )

        movement = CashMovement.objects.get(
            public_id=response.data[
                "public_id"
            ]
        )

        self.assertEqual(
            movement.created_by,
            self.cashier_user,
        )

    def test_cash_movements_are_immutable(
        self,
    ):
        movement = create_cash_movement(
            cash_register=self.register,
            created_by=self.cashier_user,
            payment_method=self.cash_method,
        )

        endpoint = (
            "/api/cash-movements/"
            f"{movement.public_id}/"
        )

        patch_response = self.client.patch(
            endpoint,
            {
                "amount": "9999.00",
            },
            format="json",
        )

        delete_response = self.client.delete(
            endpoint
        )

        self.assertEqual(
            patch_response.status_code,
            status.HTTP_405_METHOD_NOT_ALLOWED,
        )

        self.assertEqual(
            delete_response.status_code,
            status.HTTP_405_METHOD_NOT_ALLOWED,
        )

    def test_preview_includes_all_cash_movements(
        self,
    ):
        movements = [
            (
                CashMovement.TYPE_DEPOSIT,
                None,
                "500.00",
            ),
            (
                CashMovement.TYPE_WITHDRAWAL,
                None,
                "100.00",
            ),
            (
                CashMovement.TYPE_EMPLOYEE_ADVANCE,
                self.seller_employee,
                "200.00",
            ),
            (
                CashMovement.TYPE_EMPLOYEE_REPAYMENT,
                self.seller_employee,
                "50.00",
            ),
            (
                CashMovement.TYPE_OTHER_INCOME,
                None,
                "30.00",
            ),
            (
                CashMovement.TYPE_OTHER_EXPENSE,
                None,
                "20.00",
            ),
        ]

        for (
            movement_type,
            employee,
            amount,
        ) in movements:
            response = self._post_movement(
                movement_type=movement_type,
                employee=employee,
                amount=amount,
            )

            self.assertEqual(
                response.status_code,
                status.HTTP_201_CREATED,
                msg=response.data,
            )

        response = self.client.get(
            (
                "/api/cash-registers/"
                f"{self.register.public_id}/"
                "closing-preview/"
            )
        )

        self.assertEqual(
            response.status_code,
            status.HTTP_200_OK,
            msg=response.data,
        )

        # 1000 + 500 - 100 - 200
        # + 50 + 30 - 20 = 1260
        self.assertEqual(
            response.data[
                "expected_closing_balance"
            ],
            Decimal("1260.00"),
        )