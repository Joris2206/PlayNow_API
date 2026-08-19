from datetime import datetime, timezone
from decimal import Decimal
from io import StringIO

from django.core.management import call_command
from django.utils import timezone as django_timezone
from rest_framework import status

from core.models import (
    BusinessMembership,
    CashRegister,
    MonthlyClosure,
    PaymentMethod,
)
from core.services.financial_integrity import (
    diagnose_financial_integrity,
    is_legacy_monthly_closure_snapshot,
)
from core.tests.base import BusinessIsolationTestCase
from core.tests.factories import (
    create_cash_register,
    create_payment_method,
    create_role_user,
    create_transaction,
)


class MonthlyClosureTests(
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
            full_name="Administrador A",
        )

        (
            cls.cashier_user,
            cls.cashier_employee,
            _,
        ) = create_role_user(
            business=cls.business_a,
            role=BusinessMembership.ROLE_CASHIER,
            status=cls.active_status,
            full_name="Cajero A",
        )

        (
            cls.seller_user,
            cls.seller_employee,
            _,
        ) = create_role_user(
            business=cls.business_a,
            role=BusinessMembership.ROLE_SELLER,
            status=cls.active_status,
            full_name="Vendedor A",
        )

        (
            cls.inventory_user,
            cls.inventory_employee,
            _,
        ) = create_role_user(
            business=cls.business_a,
            role=BusinessMembership.ROLE_INVENTORY,
            status=cls.active_status,
            full_name="Inventario A",
        )

        (
            cls.viewer_user,
            cls.viewer_employee,
            _,
        ) = create_role_user(
            business=cls.business_a,
            role=BusinessMembership.ROLE_VIEWER,
            status=cls.active_status,
            full_name="Consulta A",
        )

        (
            cls.foreign_admin_user,
            cls.foreign_admin_employee,
            _,
        ) = create_role_user(
            business=cls.business_b,
            role=BusinessMembership.ROLE_ADMIN,
            status=cls.active_status,
            full_name="Administrador B",
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

    def _create_closure(
        self,
        *,
        business=None,
        year=2026,
        month=7,
    ):
        business = (
            business
            or self.business_a
        )

        return self.client.post(
            "/api/monthly-closures/",
            {
                "business_public_id": str(
                    business.public_id
                ),
                "year": year,
                "month": month,
            },
            format="json",
        )

    def _reopen_closure(
        self,
        closure,
        *,
        reason=(
            "Se encontró una transacción "
            "pendiente de registrar."
        ),
    ):
        return self.client.post(
            (
                "/api/monthly-closures/"
                f"{closure.public_id}/reopen/"
            ),
            {
                "reason": reason,
            },
            format="json",
        )

    def test_owner_and_admin_can_create_monthly_closure(
        self,
    ):
        cases = [
            self.user_a,
            self.admin_user,
        ]

        for user in cases:
            with self.subTest(
                user=user.email
            ):
                MonthlyClosure.objects.all().delete()

                self.authenticate_as(user)

                response = self._create_closure()

                self.assertEqual(
                    response.status_code,
                    status.HTTP_201_CREATED,
                    msg=response.data,
                )

                closure = (
                    MonthlyClosure.objects.get(
                        public_id=(
                            response.data[
                                "public_id"
                            ]
                        )
                    )
                )

                self.assertEqual(
                    closure.closed_by,
                    user,
                )

                self.assertEqual(
                    closure.status,
                    MonthlyClosure.STATUS_CLOSED,
                )

                self.assertEqual(
                    closure.version,
                    1,
                )

                self.assertEqual(
                    closure.year,
                    2026,
                )

                self.assertEqual(
                    closure.month,
                    7,
                )

                self.assertIsInstance(
                    closure.summary,
                    dict,
                )

                self.assertIn(
                    "payments",
                    closure.summary,
                )

                self.assertIn(
                    "outstanding_receivables",
                    closure.summary["debts"],
                )

    def test_new_closure_is_current_and_passes_strict_diagnostic(self):
        response = self._create_closure()
        self.assertEqual(
            response.status_code,
            status.HTTP_201_CREATED,
            msg=response.data,
        )
        closure = MonthlyClosure.objects.get(
            public_id=response.data["public_id"]
        )
        self.assertFalse(
            is_legacy_monthly_closure_snapshot(closure.summary)
        )
        codes = {
            finding.code
            for finding in diagnose_financial_integrity(
                business=self.business_a
            )
        }
        self.assertNotIn("historical_monthly_closure_review", codes)
        output = StringIO()
        call_command(
            "diagnose_financial_integrity",
            strict=True,
            business_public_id=str(self.business_a.public_id),
            stdout=output,
        )
        self.assertIn("INFO clean count=0", output.getvalue())

    def test_operational_roles_cannot_create_monthly_closure(
        self,
    ):
        cases = [
            self.cashier_user,
            self.seller_user,
            self.inventory_user,
            self.viewer_user,
        ]

        for user in cases:
            with self.subTest(
                user=user.email
            ):
                self.authenticate_as(user)

                response = self._create_closure()

                self.assertEqual(
                    response.status_code,
                    status.HTTP_403_FORBIDDEN,
                    msg=response.data,
                )

                self.assertFalse(
                    MonthlyClosure.objects.filter(
                        closed_by=user
                    ).exists()
                )

    def test_cannot_close_month_that_has_not_finished(
        self,
    ):
        today = django_timezone.localdate()

        if today.month == 12:
            future_year = today.year + 1
            future_month = 1
        else:
            future_year = today.year
            future_month = today.month + 1

        response = self._create_closure(
            year=future_year,
            month=future_month,
        )

        self.assertEqual(
            response.status_code,
            status.HTTP_400_BAD_REQUEST,
            msg=response.data,
        )

        self.assertIn(
            "period",
            response.data,
        )

    def test_cannot_close_period_with_open_cash_register(
        self,
    ):
        create_cash_register(
            business=self.business_a,
            employee=self.cashier_employee,
            opened_by=self.cashier_user,
            opening_balance=Decimal("1000.00"),
            register_status=(
                CashRegister.STATUS_OPEN
            ),
            open_time=datetime(
                2026,
                7,
                31,
                8,
                0,
                tzinfo=timezone.utc,
            ),
        )

        response = self._create_closure(
            year=2026,
            month=7,
        )

        self.assertEqual(
            response.status_code,
            status.HTTP_400_BAD_REQUEST,
            msg=response.data,
        )

        self.assertIn(
            "cash_register",
            response.data,
        )

    def test_cannot_create_two_active_closures_for_same_period(
        self,
    ):
        first_response = self._create_closure()

        self.assertEqual(
            first_response.status_code,
            status.HTTP_201_CREATED,
            msg=first_response.data,
        )

        second_response = self._create_closure()

        self.assertEqual(
            second_response.status_code,
            status.HTTP_400_BAD_REQUEST,
            msg=second_response.data,
        )

        self.assertEqual(
            MonthlyClosure.objects.filter(
                business=self.business_a,
                year=2026,
                month=7,
                status=(
                    MonthlyClosure.STATUS_CLOSED
                ),
            ).count(),
            1,
        )

    def test_reopen_requires_valid_reason(
        self,
    ):
        create_response = self._create_closure()

        closure = MonthlyClosure.objects.get(
            public_id=create_response.data[
                "public_id"
            ]
        )

        response = self._reopen_closure(
            closure,
            reason="No",
        )

        self.assertEqual(
            response.status_code,
            status.HTTP_400_BAD_REQUEST,
            msg=response.data,
        )

        closure.refresh_from_db()

        self.assertEqual(
            closure.status,
            MonthlyClosure.STATUS_CLOSED,
        )

    def test_closed_period_can_be_reopened(
        self,
    ):
        create_response = self._create_closure()

        closure = MonthlyClosure.objects.get(
            public_id=create_response.data[
                "public_id"
            ]
        )

        reason = (
            "Se encontró una factura de julio "
            "que no había sido registrada."
        )

        response = self._reopen_closure(
            closure,
            reason=reason,
        )

        self.assertEqual(
            response.status_code,
            status.HTTP_200_OK,
            msg=response.data,
        )

        closure.refresh_from_db()

        self.assertEqual(
            closure.status,
            MonthlyClosure.STATUS_REOPENED,
        )

        self.assertEqual(
            closure.reopened_by,
            self.admin_user,
        )

        self.assertIsNotNone(
            closure.reopened_at,
        )

        self.assertEqual(
            closure.reopen_reason,
            reason,
        )

    def test_reopened_closure_cannot_be_reopened_twice(
        self,
    ):
        create_response = self._create_closure()

        closure = MonthlyClosure.objects.get(
            public_id=create_response.data[
                "public_id"
            ]
        )

        first_response = self._reopen_closure(
            closure
        )

        self.assertEqual(
            first_response.status_code,
            status.HTTP_200_OK,
            msg=first_response.data,
        )

        second_response = self._reopen_closure(
            closure
        )

        self.assertEqual(
            second_response.status_code,
            status.HTTP_400_BAD_REQUEST,
            msg=second_response.data,
        )

    def test_new_closure_after_reopen_creates_next_version(
        self,
    ):
        first_response = self._create_closure()

        first_closure = (
            MonthlyClosure.objects.get(
                public_id=(
                    first_response.data[
                        "public_id"
                    ]
                )
            )
        )

        reopen_response = (
            self._reopen_closure(
                first_closure
            )
        )

        self.assertEqual(
            reopen_response.status_code,
            status.HTTP_200_OK,
            msg=reopen_response.data,
        )

        second_response = self._create_closure()

        self.assertEqual(
            second_response.status_code,
            status.HTTP_201_CREATED,
            msg=second_response.data,
        )

        second_closure = (
            MonthlyClosure.objects.get(
                public_id=(
                    second_response.data[
                        "public_id"
                    ]
                )
            )
        )

        self.assertEqual(
            first_closure.version,
            1,
        )

        self.assertEqual(
            second_closure.version,
            2,
        )

        self.assertEqual(
            second_closure.status,
            MonthlyClosure.STATUS_CLOSED,
        )

        self.assertEqual(
            MonthlyClosure.objects.filter(
                business=self.business_a,
                year=2026,
                month=7,
            ).count(),
            2,
        )

    def test_saved_summary_remains_frozen(
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
                7,
                10,
                12,
                0,
                tzinfo=timezone.utc,
            ),
        )

        create_response = self._create_closure()

        self.assertEqual(
            create_response.status_code,
            status.HTTP_201_CREATED,
            msg=create_response.data,
        )

        closure = MonthlyClosure.objects.get(
            public_id=create_response.data[
                "public_id"
            ]
        )

        self.assertEqual(
            closure.summary[
                "transactions"
            ]["sales"]["total"],
            "1000.00",
        )

        # Se registra después una venta atrasada
        # correspondiente al mismo mes.
        create_transaction(
            business=self.business_a,
            created_by=self.cashier_user,
            employee=self.seller_employee,
            payment_method=self.cash_method,
            status=self.active_status,
            transaction_type="sale",
            total_value=Decimal("500.00"),
            created_at=datetime(
                2026,
                7,
                20,
                12,
                0,
                tzinfo=timezone.utc,
            ),
        )

        detail_response = self.client.get(
            (
                "/api/monthly-closures/"
                f"{closure.public_id}/"
            )
        )

        self.assertEqual(
            detail_response.status_code,
            status.HTTP_200_OK,
            msg=detail_response.data,
        )

        # El cierre original sigue conservando 1000.
        self.assertEqual(
            detail_response.data[
                "summary"
            ]["transactions"]["sales"]["total"],
            "1000.00",
        )

    def test_reclosed_version_uses_updated_summary(
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
                7,
                10,
                12,
                0,
                tzinfo=timezone.utc,
            ),
        )

        first_response = self._create_closure()

        first_closure = MonthlyClosure.objects.get(
            public_id=first_response.data[
                "public_id"
            ]
        )

        self._reopen_closure(
            first_closure
        )

        create_transaction(
            business=self.business_a,
            created_by=self.cashier_user,
            employee=self.seller_employee,
            payment_method=self.cash_method,
            status=self.active_status,
            transaction_type="sale",
            total_value=Decimal("500.00"),
            created_at=datetime(
                2026,
                7,
                20,
                12,
                0,
                tzinfo=timezone.utc,
            ),
        )

        second_response = self._create_closure()

        self.assertEqual(
            second_response.status_code,
            status.HTTP_201_CREATED,
            msg=second_response.data,
        )

        self.assertEqual(
            first_closure.summary[
                "transactions"
            ]["sales"]["total"],
            "1000.00",
        )

        self.assertEqual(
            second_response.data[
                "summary"
            ]["transactions"]["sales"]["total"],
            "1500.00",
        )

        self.assertEqual(
            second_response.data["version"],
            2,
        )

    def test_foreign_user_cannot_retrieve_closure(
        self,
    ):
        create_response = self._create_closure()

        self.authenticate_as(
            self.foreign_admin_user
        )

        response = self.client.get(
            (
                "/api/monthly-closures/"
                f"{create_response.data['public_id']}/"
            )
        )

        self.assertEqual(
            response.status_code,
            status.HTTP_404_NOT_FOUND,
        )

    def test_monthly_closure_cannot_be_updated_or_deleted(
        self,
    ):
        create_response = self._create_closure()

        endpoint = (
            "/api/monthly-closures/"
            f"{create_response.data['public_id']}/"
        )

        patch_response = self.client.patch(
            endpoint,
            {
                "version": 99,
            },
            format="json",
        )

        put_response = self.client.put(
            endpoint,
            {
                "version": 99,
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
            put_response.status_code,
            status.HTTP_405_METHOD_NOT_ALLOWED,
        )

        self.assertEqual(
            delete_response.status_code,
            status.HTTP_405_METHOD_NOT_ALLOWED,
        )
