from decimal import Decimal

from rest_framework import status

from core.models import (
    BusinessMembership,
    CashRegister,
    PaymentMethod,
)
from core.tests.base import (
    BusinessIsolationTestCase,
)
from core.tests.factories import (
    create_payment_method,
    create_role_user,
    create_transaction,
)


class CashRegisterTests(
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
            cls.seller_user,
            cls.seller_employee,
            _,
        ) = create_role_user(
            business=cls.business_a,
            role=BusinessMembership.ROLE_SELLER,
            status=cls.active_status,
        )

        (
            cls.inventory_user,
            cls.inventory_employee,
            _,
        ) = create_role_user(
            business=cls.business_a,
            role=BusinessMembership.ROLE_INVENTORY,
            status=cls.active_status,
        )

        (
            cls.viewer_user,
            cls.viewer_employee,
            _,
        ) = create_role_user(
            business=cls.business_a,
            role=BusinessMembership.ROLE_VIEWER,
            status=cls.active_status,
        )

        (
            cls.foreign_cashier_user,
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

        cls.card_method = create_payment_method(
            business=cls.business_a,
            status=cls.active_status,
            name="Tarjeta",
            method_type=PaymentMethod.TYPE_CARD,
        )

    def setUp(self):
        self.authenticate_as(
            self.cashier_user
        )

    def _open_register(
        self,
        *,
        employee=None,
        opening_balance="1000.00",
    ):
        return self.client.post(
            "/api/cash-registers/",
            {
                "business_public_id": str(
                    self.business_a.public_id
                ),
                "employee_public_id": str(
                    (
                        employee
                        or self.cashier_employee
                    ).public_id
                ),
                "opening_balance": (
                    opening_balance
                ),
                "opening_notes": (
                    "Inicio de turno"
                ),
            },
            format="json",
        )

    def test_owner_admin_and_cashier_can_open_register(
        self,
    ):
        cases = [
            (
                self.user_a,
                self.cashier_employee,
            ),
            (
                self.admin_user,
                self.admin_employee,
            ),
            (
                self.cashier_user,
                self.cashier_employee,
            ),
        ]

        for user, employee in cases:
            with self.subTest(
                user=user.email
            ):
                CashRegister.objects.all().delete()

                self.authenticate_as(user)

                response = self._open_register(
                    employee=employee
                )

                self.assertEqual(
                    response.status_code,
                    status.HTTP_201_CREATED,
                    msg=response.data,
                )

                register = (
                    CashRegister.objects.get(
                        public_id=(
                            response.data[
                                "public_id"
                            ]
                        )
                    )
                )

                self.assertEqual(
                    register.opened_by,
                    user,
                )

                self.assertEqual(
                    register.status,
                    CashRegister.STATUS_OPEN,
                )

                self.assertIsNotNone(
                    register.open_time
                )

    def test_seller_inventory_and_viewer_cannot_open_register(
        self,
    ):
        cases = [
            (
                self.seller_user,
                self.seller_employee,
            ),
            (
                self.inventory_user,
                self.inventory_employee,
            ),
            (
                self.viewer_user,
                self.viewer_employee,
            ),
        ]

        for user, employee in cases:
            with self.subTest(
                user=user.email
            ):
                self.authenticate_as(user)

                response = self._open_register(
                    employee=employee
                )

                self.assertEqual(
                    response.status_code,
                    status.HTTP_403_FORBIDDEN,
                    msg=response.data,
                )

                self.assertFalse(
                    CashRegister.objects.filter(
                        opened_by=user
                    ).exists()
                )

    def test_employee_must_belong_to_register_business(
        self,
    ):
        response = self.client.post(
            "/api/cash-registers/",
            {
                "business_public_id": str(
                    self.business_a.public_id
                ),
                "employee_public_id": str(
                    self.foreign_employee.public_id
                ),
                "opening_balance": "1000.00",
            },
            format="json",
        )

        self.assertEqual(
            response.status_code,
            status.HTTP_400_BAD_REQUEST,
            msg=response.data,
        )

    def test_business_cannot_have_two_open_registers(
        self,
    ):
        first_response = (
            self._open_register()
        )

        self.assertEqual(
            first_response.status_code,
            status.HTTP_201_CREATED,
        )

        second_response = (
            self._open_register()
        )

        self.assertEqual(
            second_response.status_code,
            status.HTTP_400_BAD_REQUEST,
            msg=second_response.data,
        )

        self.assertEqual(
            CashRegister.objects.filter(
                business=self.business_a,
                status=CashRegister.STATUS_OPEN,
            ).count(),
            1,
        )

    def test_closing_preview_calculates_expected_cash(
        self,
    ):
        open_response = (
            self._open_register(
                opening_balance="1000.00"
            )
        )

        register_id = open_response.data[
            "public_id"
        ]

        create_transaction(
            business=self.business_a,
            created_by=self.cashier_user,
            employee=self.seller_employee,
            payment_method=self.cash_method,
            status=self.active_status,
            transaction_type="sale",
            total_value=Decimal("500.00"),
        )

        create_transaction(
            business=self.business_a,
            created_by=self.cashier_user,
            employee=self.seller_employee,
            payment_method=self.card_method,
            status=self.active_status,
            transaction_type="sale",
            total_value=Decimal("300.00"),
        )

        create_transaction(
            business=self.business_a,
            created_by=self.user_a,
            payment_method=self.cash_method,
            status=self.active_status,
            transaction_type="expense",
            total_value=Decimal("100.00"),
        )

        response = self.client.get(
            (
                "/api/cash-registers/"
                f"{register_id}/"
                "closing-preview/"
            )
        )

        self.assertEqual(
            response.status_code,
            status.HTTP_200_OK,
            msg=response.data,
        )

        self.assertEqual(
            response.data["sales"]["cash"],
            Decimal("500.00"),
        )

        self.assertEqual(
            response.data["sales"]["card"],
            Decimal("300.00"),
        )

        self.assertEqual(
            response.data[
                "cash_expenses"
            ],
            Decimal("100.00"),
        )

        self.assertEqual(
            response.data[
                "expected_closing_balance"
            ],
            Decimal("1400.00"),
        )

    def test_register_can_close_with_shortage(
        self,
    ):
        open_response = (
            self._open_register(
                opening_balance="1000.00"
            )
        )

        register_id = open_response.data[
            "public_id"
        ]

        create_transaction(
            business=self.business_a,
            created_by=self.cashier_user,
            employee=self.seller_employee,
            payment_method=self.cash_method,
            status=self.active_status,
            transaction_type="sale",
            total_value=Decimal("500.00"),
        )

        response = self.client.post(
            (
                "/api/cash-registers/"
                f"{register_id}/close/"
            ),
            {
                "closing_balance": "1490.00",
                "closing_notes": (
                    "Faltante de C$10"
                ),
            },
            format="json",
        )

        self.assertEqual(
            response.status_code,
            status.HTTP_200_OK,
            msg=response.data,
        )

        self.assertEqual(
            response.data["status"],
            CashRegister.STATUS_CLOSED,
        )

        self.assertEqual(
            response.data[
                "expected_closing_balance"
            ],
            "1500.00",
        )

        self.assertEqual(
            response.data[
                "closing_balance"
            ],
            "1490.00",
        )

        self.assertEqual(
            response.data["difference"],
            "-10.00",
        )

        register = (
            CashRegister.objects.get(
                public_id=register_id
            )
        )

        self.assertEqual(
            register.closed_by,
            self.cashier_user,
        )

        self.assertIsNotNone(
            register.close_time
        )

    def test_register_cannot_be_closed_twice(
        self,
    ):
        open_response = (
            self._open_register()
        )

        endpoint = (
            "/api/cash-registers/"
            f"{open_response.data['public_id']}/"
            "close/"
        )

        first_response = self.client.post(
            endpoint,
            {
                "closing_balance": "1000.00",
            },
            format="json",
        )

        self.assertEqual(
            first_response.status_code,
            status.HTTP_200_OK,
        )

        second_response = self.client.post(
            endpoint,
            {
                "closing_balance": "1000.00",
            },
            format="json",
        )

        self.assertEqual(
            second_response.status_code,
            status.HTTP_400_BAD_REQUEST,
            msg=second_response.data,
        )

    def test_foreign_user_cannot_retrieve_register(
        self,
    ):
        open_response = (
            self._open_register()
        )

        self.authenticate_as(
            self.foreign_cashier_user
        )

        response = self.client.get(
            (
                "/api/cash-registers/"
                f"{open_response.data['public_id']}/"
            )
        )

        self.assertEqual(
            response.status_code,
            status.HTTP_404_NOT_FOUND,
        )
