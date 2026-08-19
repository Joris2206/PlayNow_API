from concurrent.futures import (
    ThreadPoolExecutor,
    TimeoutError as FutureTimeoutError,
)
from datetime import timedelta
from decimal import Decimal
from threading import Event
from types import SimpleNamespace
from unittest.mock import patch
from uuid import uuid4

from django.db import (
    close_old_connections,
    connection,
    transaction as db_tx,
)
from django.test import TransactionTestCase
from django.test.utils import CaptureQueriesContext
from django.utils import timezone
from rest_framework import status
from rest_framework.exceptions import ValidationError
from rest_framework.test import APIClient

from core.models import (
    BusinessMembership,
    CashMovement,
    DebtPayment,
    PaymentMethod,
    Transaction,
)
from core.services.debt_payments import (
    register_debt_payment,
)
from core.serializers import DebtPaymentSerializer
from core.tests.base import BusinessIsolationTestCase
from core.tests.factories import (
    create_business,
    create_debt,
    create_payment_method,
    create_role_user,
    create_status,
    create_transaction,
    create_user,
)


class DebtPaymentRegistrationTests(
    BusinessIsolationTestCase
):
    @classmethod
    def setUpTestData(cls):
        super().setUpTestData()

        cls.inactive_status = create_status(
            "Inactivo"
        )
        cls.deleted_status = create_status(
            "Eliminado"
        )

        cls.method = create_payment_method(
            business=cls.business_a,
            status=cls.active_status,
            method_type=PaymentMethod.TYPE_CASH,
        )
        cls.foreign_method = create_payment_method(
            business=cls.business_b,
            status=cls.active_status,
        )
        cls.inactive_method = create_payment_method(
            business=cls.business_a,
            status=cls.inactive_status,
        )
        cls.deleted_method = create_payment_method(
            business=cls.business_a,
            status=cls.deleted_status,
        )

    def setUp(self):
        super().setUp()
        self.transaction = create_transaction(
            business=self.business_a,
            created_by=self.user_a,
            status=self.active_status,
            is_debt=True,
            total_value=Decimal("100.00"),
        )
        self.debt = create_debt(
            transaction=self.transaction,
            total_amount=Decimal("100.00"),
        )

    def _payload(self, **overrides):
        payload = {
            "debt_public_id": str(
                self.debt.public_id
            ),
            "amount": "25.00",
            "payment_date": str(
                timezone.localdate()
            ),
            "payment_method_public_id": str(
                self.method.public_id
            ),
        }
        payload.update(overrides)
        return payload

    def _post(self, **overrides):
        return self.client.post(
            "/api/debt-payments/",
            self._payload(**overrides),
            format="json",
        )

    def _assert_unchanged(self):
        self.debt.refresh_from_db()
        self.transaction.refresh_from_db()
        self.assertEqual(
            self.debt.paid_amount,
            Decimal("0.00"),
        )
        self.assertFalse(self.debt.is_settled)
        self.assertEqual(
            self.transaction.payment_status,
            "pending",
        )
        self.assertTrue(self.transaction.is_debt)
        self.assertFalse(
            DebtPayment.objects.filter(
                debt=self.debt,
            ).exists()
        )

    def test_invalid_amounts_are_rejected_without_effects(self):
        for amount in (
            "0.00",
            "-1.00",
            "100.01",
        ):
            with self.subTest(amount=amount):
                response = self._post(
                    amount=amount
                )
                self.assertEqual(
                    response.status_code,
                    status.HTTP_400_BAD_REQUEST,
                    msg=response.data,
                )
                self.assertIn(
                    "amount",
                    response.data,
                )
                self._assert_unchanged()

    def test_partial_payments_accumulate_and_synchronize(self):
        first_response = self._post(
            amount="30.10"
        )
        second_response = self._post(
            amount="20.20"
        )

        self.assertEqual(
            first_response.status_code,
            status.HTTP_201_CREATED,
        )
        self.assertEqual(
            second_response.status_code,
            status.HTTP_201_CREATED,
        )

        self.debt.refresh_from_db()
        self.transaction.refresh_from_db()

        self.assertEqual(
            self.debt.paid_amount,
            Decimal("50.30"),
        )
        self.assertFalse(self.debt.is_settled)
        self.assertEqual(
            self.transaction.payment_status,
            "partial",
        )
        self.assertTrue(self.transaction.is_debt)

    def test_final_payment_from_pending_synchronizes_all_records(self):
        response = self._post(amount="100.00")

        self.assertEqual(
            response.status_code,
            status.HTTP_201_CREATED,
            msg=response.data,
        )

        payment = DebtPayment.objects.get(
            debt=self.debt
        )
        self.debt.refresh_from_db()
        self.transaction.refresh_from_db()

        self.assertEqual(
            payment.transaction_id,
            self.transaction.pk,
        )
        self.assertEqual(
            self.debt.paid_amount,
            Decimal("100.00"),
        )
        self.assertTrue(self.debt.is_settled)
        self.assertEqual(
            self.transaction.payment_status,
            "paid",
        )
        self.assertFalse(self.transaction.is_debt)

    def test_final_payment_from_partial_synchronizes_all_records(self):
        first_response = self._post(
            amount="40.00"
        )
        final_response = self._post(
            amount="60.00"
        )

        self.assertEqual(
            first_response.status_code,
            status.HTTP_201_CREATED,
        )
        self.assertEqual(
            final_response.status_code,
            status.HTTP_201_CREATED,
        )

        self.debt.refresh_from_db()
        self.transaction.refresh_from_db()

        self.assertEqual(
            self.debt.paid_amount,
            Decimal("100.00"),
        )
        self.assertTrue(self.debt.is_settled)
        self.assertEqual(
            self.transaction.payment_status,
            "paid",
        )
        self.assertFalse(self.transaction.is_debt)

    def test_settled_debt_is_rejected_without_effects(self):
        self.debt.paid_amount = Decimal("100.00")
        self.debt.is_settled = True
        self.debt.save(
            update_fields=[
                "paid_amount",
                "is_settled",
            ],
        )
        self.transaction.payment_status = "paid"
        self.transaction.is_debt = False
        self.transaction.save(
            update_fields=[
                "payment_status",
                "is_debt",
            ],
        )

        response = self._post(amount="1.00")

        self.assertEqual(
            response.status_code,
            status.HTTP_400_BAD_REQUEST,
        )
        self.assertFalse(
            DebtPayment.objects.filter(
                debt=self.debt,
            ).exists()
        )
        self.debt.refresh_from_db()
        self.transaction.refresh_from_db()
        self.assertEqual(
            self.debt.paid_amount,
            Decimal("100.00"),
        )
        self.assertEqual(
            self.transaction.payment_status,
            "paid",
        )

    def test_payment_method_must_be_active_and_same_business(self):
        cases = (
            (self.inactive_method, status.HTTP_400_BAD_REQUEST),
            (self.deleted_method, status.HTTP_400_BAD_REQUEST),
            (self.foreign_method, status.HTTP_400_BAD_REQUEST),
        )

        for payment_method, expected_status in cases:
            with self.subTest(
                payment_method=payment_method.name
            ):
                response = self._post(
                    payment_method_public_id=str(
                        payment_method.public_id
                    )
                )
                self.assertEqual(
                    response.status_code,
                    expected_status,
                    msg=response.data,
                )
                self.assertIn(
                    "payment_method_public_id",
                    response.data,
                )
                self._assert_unchanged()

    def test_service_reloads_and_locks_payment_method(self):
        serializer = DebtPaymentSerializer(
            data=self._payload(),
            context={
                "request": SimpleNamespace(
                    user=self.user_a
                ),
            },
        )
        self.assertTrue(
            serializer.is_valid(),
            msg=serializer.errors,
        )

        PaymentMethod.objects.filter(
            pk=self.method.pk,
        ).update(
            status=self.inactive_status,
        )

        with self.assertRaises(
            ValidationError
        ) as raised:
            serializer.save()

        self.assertIn(
            "payment_method_public_id",
            raised.exception.detail,
        )
        self._assert_unchanged()

    def test_authoritative_queries_lock_in_fixed_order(self):
        with CaptureQueriesContext(
            connection
        ) as captured:
            register_debt_payment(
                debt_id=self.debt.pk,
                amount=Decimal("10.00"),
                payment_date=timezone.localdate(),
                payment_method_id=self.method.pk,
                actor=self.user_a,
                observed_remaining_amount=(
                    Decimal("100.00")
                ),
            )

        lock_queries = [
            query["sql"].casefold()
            for query in captured.captured_queries
            if "for update" in query["sql"].casefold()
        ]

        locked_tables = (
            "core_debt",
            "core_transaction",
            "core_paymentmethod",
        )
        positions = []

        for table_name in locked_tables:
            matching_positions = [
                index
                for index, sql in enumerate(
                    lock_queries
                )
                if table_name in sql
            ]
            self.assertTrue(
                matching_positions,
                msg=(
                    f"No se capturó FOR UPDATE "
                    f"para {table_name}: {lock_queries}"
                ),
            )
            positions.append(
                matching_positions[0]
            )

        self.assertEqual(
            positions,
            sorted(positions),
        )

    def test_all_active_method_types_remain_usable_without_cash_effects(self):
        initial_movements = CashMovement.objects.count()

        for index, (method_type, _) in enumerate(
            PaymentMethod.METHOD_TYPES,
            start=1,
        ):
            payment_method = create_payment_method(
                business=self.business_a,
                status=self.active_status,
                name=f"Tipo activo {index}",
                method_type=method_type,
            )
            response = self._post(
                amount="10.00",
                payment_method_public_id=str(
                    payment_method.public_id
                ),
            )
            self.assertEqual(
                response.status_code,
                status.HTTP_201_CREATED,
                msg=response.data,
            )

        self.assertEqual(
            CashMovement.objects.count(),
            initial_movements,
        )

    def test_terminal_transactions_are_rejected_atomically(self):
        terminal_names = (
            "Anulado",
            "Cancelado",
            "Eliminado",
            "Void",
            "Deleted",
        )

        for name in terminal_names:
            with self.subTest(status=name):
                terminal_status = create_status(name)
                self.transaction.status = terminal_status
                self.transaction.save(
                    update_fields=["status"],
                )

                response = self._post()

                self.assertEqual(
                    response.status_code,
                    status.HTTP_409_CONFLICT,
                    msg=response.data,
                )
                self._assert_unchanged()

                self.transaction.status = (
                    self.active_status
                )
                self.transaction.save(
                    update_fields=["status"],
                )

    def test_payment_date_accepts_today_and_past_but_rejects_future(self):
        today = timezone.localdate()

        today_response = self._post(
            amount="10.00",
            payment_date=str(today),
        )
        past_response = self._post(
            amount="10.00",
            payment_date=str(
                today - timedelta(days=1)
            ),
        )
        future_response = self._post(
            amount="10.00",
            payment_date=str(
                today + timedelta(days=1)
            ),
        )

        self.assertEqual(
            today_response.status_code,
            status.HTTP_201_CREATED,
        )
        self.assertEqual(
            past_response.status_code,
            status.HTTP_201_CREATED,
        )
        self.assertEqual(
            future_response.status_code,
            status.HTTP_400_BAD_REQUEST,
        )
        self.assertIn(
            "payment_date",
            future_response.data,
        )
        self.assertEqual(
            DebtPayment.objects.filter(
                debt=self.debt,
            ).count(),
            2,
        )

    def test_transaction_is_derived_or_must_match_debt(self):
        omitted_response = self._post(
            amount="10.00"
        )
        matching_response = self._post(
            amount="10.00",
            transaction_public_id=str(
                self.transaction.public_id
            ),
        )

        other_transaction = create_transaction(
            business=self.business_a,
            created_by=self.user_a,
            status=self.active_status,
            is_debt=True,
        )
        mismatch_response = self._post(
            amount="10.00",
            transaction_public_id=str(
                other_transaction.public_id
            ),
        )

        foreign_transaction = create_transaction(
            business=self.business_b,
            created_by=self.user_b,
            status=self.active_status,
            is_debt=True,
        )
        foreign_response = self._post(
            amount="10.00",
            transaction_public_id=str(
                foreign_transaction.public_id
            ),
        )

        self.assertEqual(
            omitted_response.status_code,
            status.HTTP_201_CREATED,
        )
        self.assertEqual(
            matching_response.status_code,
            status.HTTP_201_CREATED,
        )
        self.assertEqual(
            mismatch_response.status_code,
            status.HTTP_400_BAD_REQUEST,
        )
        self.assertEqual(
            foreign_response.status_code,
            status.HTTP_400_BAD_REQUEST,
        )
        self.assertIn(
            "transaction_public_id",
            mismatch_response.data,
        )
        self.assertIn(
            "transaction_public_id",
            foreign_response.data,
        )

        expected_transaction_id = str(
            self.transaction.public_id
        )
        self.assertEqual(
            str(omitted_response.data[
                "transaction_public_id"
            ]),
            expected_transaction_id,
        )
        self.assertEqual(
            str(matching_response.data[
                "transaction_public_id"
            ]),
            expected_transaction_id,
        )

    def test_relation_errors_do_not_reveal_foreign_objects(self):
        foreign_transaction = create_transaction(
            business=self.business_b,
            created_by=self.user_b,
            status=self.active_status,
            is_debt=True,
        )
        foreign_debt = create_debt(
            transaction=foreign_transaction
        )

        cases = (
            (
                "debt_public_id",
                str(foreign_debt.public_id),
            ),
            (
                "payment_method_public_id",
                str(self.foreign_method.public_id),
            ),
            (
                "transaction_public_id",
                str(foreign_transaction.public_id),
            ),
        )

        for field_name, foreign_public_id in cases:
            with self.subTest(field=field_name):
                foreign_response = self._post(**{
                    field_name: foreign_public_id,
                })
                missing_response = self._post(**{
                    field_name: str(uuid4()),
                })

                self.assertEqual(
                    foreign_response.status_code,
                    status.HTTP_400_BAD_REQUEST,
                )
                self.assertEqual(
                    missing_response.status_code,
                    status.HTTP_400_BAD_REQUEST,
                )
                self.assertEqual(
                    foreign_response.data,
                    missing_response.data,
                )
                self.assertEqual(
                    set(foreign_response.data),
                    {field_name},
                )

        self._assert_unchanged()

    def test_unauthorized_role_is_rejected_with_403(self):
        seller, _, _ = create_role_user(
            business=self.business_a,
            role=BusinessMembership.ROLE_SELLER,
            status=self.active_status,
        )
        self.authenticate_as(seller)
        denied_response = self._post()

        self.assertEqual(
            denied_response.status_code,
            status.HTTP_403_FORBIDDEN,
        )
        self._assert_unchanged()

    def test_superuser_keeps_global_relation_access(self):
        foreign_transaction = create_transaction(
            business=self.business_b,
            created_by=self.user_b,
            status=self.active_status,
            is_debt=True,
        )
        foreign_debt = create_debt(
            transaction=foreign_transaction
        )
        superuser = create_user(
            email="debt-payment-superuser@playnow.test",
            is_superuser=True,
        )
        self.authenticate_as(superuser)

        response = self._post(
            debt_public_id=str(
                foreign_debt.public_id
            ),
            payment_method_public_id=str(
                self.foreign_method.public_id
            ),
        )

        self.assertEqual(
            response.status_code,
            status.HTTP_201_CREATED,
            msg=response.data,
        )
        self.assertEqual(
            str(response.data[
                "transaction_public_id"
            ]),
            str(foreign_transaction.public_id),
        )

    def test_owner_admin_and_cashier_can_register_payments(self):
        admin, _, _ = create_role_user(
            business=self.business_a,
            role=BusinessMembership.ROLE_ADMIN,
            status=self.active_status,
        )
        cashier, _, _ = create_role_user(
            business=self.business_a,
            role=BusinessMembership.ROLE_CASHIER,
            status=self.active_status,
        )

        for user in (
            self.user_a,
            admin,
            cashier,
        ):
            with self.subTest(user=user.email):
                self.authenticate_as(user)
                response = self._post(
                    amount="10.00"
                )
                self.assertEqual(
                    response.status_code,
                    status.HTTP_201_CREATED,
                    msg=response.data,
                )

    def test_failure_while_synchronizing_transaction_rolls_back_all(self):
        with patch.object(
            Transaction,
            "save",
            side_effect=RuntimeError(
                "forced transaction failure"
            ),
        ):
            with self.assertRaises(RuntimeError):
                register_debt_payment(
                    debt_id=self.debt.pk,
                    amount=Decimal("25.00"),
                    payment_date=timezone.localdate(),
                    payment_method_id=self.method.pk,
                    actor=self.user_a,
                    observed_remaining_amount=(
                        Decimal("100.00")
                    ),
                )

        self._assert_unchanged()


class ConcurrentDebtPaymentTests(
    TransactionTestCase
):
    reset_sequences = True

    def setUp(self):
        self.active_status = create_status(
            "Activo"
        )
        self.owner = create_user(
            email="debt-concurrency@playnow.test"
        )
        self.business = create_business(
            user=self.owner,
            status=self.active_status,
            business_name="Negocio deuda concurrente",
        )
        self.method = create_payment_method(
            business=self.business,
            status=self.active_status,
            method_type=PaymentMethod.TYPE_CASH,
        )

    def _build_debt(self):
        transaction = create_transaction(
            business=self.business,
            created_by=self.owner,
            status=self.active_status,
            is_debt=True,
            total_value=Decimal("100.00"),
        )
        debt = create_debt(
            transaction=transaction,
            total_amount=Decimal("100.00"),
        )
        return transaction, debt

    def _post_payment(
        self,
        *,
        debt_public_id,
        amount,
        operation_started=None,
        lock_held=None,
        release_lock=None,
    ):
        close_old_connections()
        try:
            client = APIClient()
            client.force_authenticate(
                user=type(self.owner).objects.get(
                    pk=self.owner.pk
                )
            )
            payload = {
                "debt_public_id": str(
                    debt_public_id
                ),
                "amount": str(amount),
                "payment_date": str(
                    timezone.localdate()
                ),
                "payment_method_public_id": str(
                    self.method.public_id
                ),
            }

            if operation_started is not None:
                operation_started.set()

            if lock_held is None:
                response = client.post(
                    "/api/debt-payments/",
                    payload,
                    format="json",
                )
            else:
                with db_tx.atomic():
                    response = client.post(
                        "/api/debt-payments/",
                        payload,
                        format="json",
                    )
                    if (
                        response.status_code
                        != status.HTTP_201_CREATED
                    ):
                        raise AssertionError(
                            response.data
                        )
                    lock_held.set()
                    if not release_lock.wait(
                        timeout=10
                    ):
                        raise AssertionError(
                            "No se liberó el lock a tiempo."
                        )

            return response.status_code
        finally:
            close_old_connections()

    def _assert_concurrent_result(
        self,
        *,
        amount,
    ):
        transaction, debt = self._build_debt()
        lock_held = Event()
        release_lock = Event()
        second_started = Event()
        executor = ThreadPoolExecutor(
            max_workers=2
        )

        try:
            first = executor.submit(
                self._post_payment,
                debt_public_id=debt.public_id,
                amount=amount,
                lock_held=lock_held,
                release_lock=release_lock,
            )
            self.assertTrue(
                lock_held.wait(timeout=10),
                msg=(
                    "El primer worker no retuvo "
                    "el lock a tiempo."
                ),
            )

            second = executor.submit(
                self._post_payment,
                debt_public_id=debt.public_id,
                amount=amount,
                operation_started=second_started,
            )
            self.assertTrue(
                second_started.wait(timeout=10),
                msg=(
                    "El segundo worker no inició "
                    "la operación a tiempo."
                ),
            )

            with self.assertRaises(
                FutureTimeoutError
            ):
                second.result(timeout=0.25)

            release_lock.set()
            statuses = sorted([
                first.result(timeout=10),
                second.result(timeout=10),
            ])
        finally:
            release_lock.set()
            executor.shutdown(
                wait=True,
                cancel_futures=True,
            )

        debt.refresh_from_db()
        transaction.refresh_from_db()

        self.assertEqual(
            statuses,
            [
                status.HTTP_201_CREATED,
                status.HTTP_409_CONFLICT,
            ],
        )
        self.assertEqual(
            DebtPayment.objects.filter(
                debt=debt,
            ).count(),
            1,
        )
        self.assertEqual(
            debt.paid_amount,
            amount,
        )
        self.assertEqual(
            debt.is_settled,
            amount == Decimal("100.00"),
        )
        self.assertEqual(
            transaction.payment_status,
            (
                "paid"
                if amount == Decimal("100.00")
                else "partial"
            ),
        )
        self.assertEqual(
            transaction.is_debt,
            amount != Decimal("100.00"),
        )

    def test_concurrent_final_payments_apply_exactly_once(self):
        self._assert_concurrent_result(
            amount=Decimal("100.00")
        )

    def test_concurrent_sixty_percent_payments_never_overpay(self):
        self._assert_concurrent_result(
            amount=Decimal("60.00")
        )
