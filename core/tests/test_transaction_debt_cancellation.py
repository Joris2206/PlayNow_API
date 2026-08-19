from datetime import date
from decimal import Decimal
from concurrent.futures import ThreadPoolExecutor, TimeoutError as FutureTimeoutError
import re
from threading import Barrier, Event
from unittest.mock import patch

from django.db import close_old_connections, connection, transaction as db_tx
from django.test import TransactionTestCase
from django.test.utils import CaptureQueriesContext
from django.utils import timezone
from drf_spectacular.generators import SchemaGenerator
from rest_framework import status
from rest_framework.test import APIClient

from core.models import Debt, DebtPayment, StockMovement, Transaction
from core.tests.base import BusinessIsolationTestCase
from core.tests.factories import (
    create_business,
    create_customer,
    create_debt,
    create_payment_method,
    create_product,
    create_role_user,
    create_status,
    create_supplier,
    create_transaction,
    create_user,
)
from core.models import BusinessMembership
from core.services.debt_payments import DebtPaymentConflict, register_debt_payment
from core.services.inventory import record_stock_movement
from core.services.transaction_cancellation import cancel_transaction


class TransactionDebtCancellationTests(BusinessIsolationTestCase):
    @classmethod
    def setUpTestData(cls):
        super().setUpTestData()
        _, cls.seller, _ = create_role_user(
            business=cls.business_a,
            role=BusinessMembership.ROLE_SELLER,
            status=cls.active_status,
        )
        cls.customer = create_customer(
            business=cls.business_a,
            status=cls.active_status,
        )
        cls.supplier = create_supplier(
            business=cls.business_a,
            status=cls.active_status,
        )
        cls.method = create_payment_method(
            business=cls.business_a,
            status=cls.active_status,
        )

    def setUp(self):
        super().setUp()
        self.product = create_product(
            business=self.business_a,
            status=self.active_status,
            base_price=Decimal("100.00"),
            base_cost=Decimal("60.00"),
            stock=20,
        )

    def create_debt_transaction(self, transaction_type, payment_status="pending"):
        payload = {
            "business_public_id": str(self.business_a.public_id),
            "type": transaction_type,
            "payment_status": payment_status,
            "details": [{
                "product_public_id": str(self.product.public_id),
                "quantity": 2,
            }],
        }
        if transaction_type == "sale":
            payload.update({
                "customer_public_id": str(self.customer.public_id),
                "employee_public_id": str(self.seller.public_id),
            })
        else:
            payload["supplier_public_id"] = str(self.supplier.public_id)
        if payment_status == "partial":
            payload.update({
                "payment_method_public_id": str(self.method.public_id),
                "initial_paid_amount": "50.00",
            })

        response = self.client.post("/api/transactions/", payload, format="json")
        self.assertEqual(response.status_code, status.HTTP_201_CREATED, response.data)
        transaction = Transaction.objects.get(public_id=response.data["public_id"])
        return transaction, Debt.objects.get(transaction=transaction)

    def cancel(self, transaction):
        return self.client.delete(f"/api/transactions/{transaction.public_id}/")

    def post_payment(self, debt, amount="10.00"):
        return self.client.post(
            "/api/debt-payments/",
            {
                "debt_public_id": str(debt.public_id),
                "amount": amount,
                "payment_date": date.today().isoformat(),
                "payment_method_public_id": str(self.method.public_id),
            },
            format="json",
        )

    def test_pending_sale_and_purchase_can_be_cancelled_preserving_debt(self):
        for transaction_type, expected_stock in (("sale", 20), ("purchase", 20)):
            with self.subTest(transaction_type=transaction_type):
                transaction, debt = self.create_debt_transaction(transaction_type)
                initial_adjustments = StockMovement.objects.filter(
                    transaction=transaction,
                    type="adjustment",
                ).count()

                response = self.cancel(transaction)
                self.assertEqual(response.status_code, status.HTTP_204_NO_CONTENT)
                transaction.refresh_from_db()
                debt.refresh_from_db()
                self.product.refresh_from_db()

                self.assertEqual(transaction.status.name, "Anulado")
                self.assertTrue(Debt.objects.filter(pk=debt.pk).exists())
                self.assertEqual(debt.paid_amount, Decimal("0.00"))
                self.assertFalse(debt.is_settled)
                self.assertFalse(DebtPayment.objects.filter(debt=debt).exists())
                self.assertEqual(self.product.stock, expected_stock)
                self.assertEqual(
                    StockMovement.objects.filter(
                        transaction=transaction,
                        type="adjustment",
                    ).count(),
                    initial_adjustments + 1,
                )
                self.assertEqual(
                    self.post_payment(debt).status_code,
                    status.HTTP_409_CONFLICT,
                )
                self.assertEqual(
                    self.cancel(transaction).status_code,
                    status.HTTP_409_CONFLICT,
                )

                # Cada subcaso usa un producto nuevo para no compartir stock.
                self.product = create_product(
                    business=self.business_a,
                    status=self.active_status,
                    stock=20,
                )

    def test_partial_and_settled_debts_block_sale_and_purchase_cancellation(self):
        for transaction_type in ("sale", "purchase"):
            for settle in (False, True):
                with self.subTest(transaction_type=transaction_type, settle=settle):
                    transaction, debt = self.create_debt_transaction(
                        transaction_type,
                        payment_status="partial",
                    )
                    if settle:
                        remaining = debt.total_amount - debt.paid_amount
                        paid = self.post_payment(debt, str(remaining))
                        self.assertEqual(paid.status_code, status.HTTP_201_CREATED, paid.data)

                    self.product.refresh_from_db()
                    stock_before = self.product.stock
                    movement_count = StockMovement.objects.filter(
                        transaction=transaction,
                    ).count()
                    debt.refresh_from_db()
                    payment_count = debt.payments.count()

                    response = self.cancel(transaction)
                    self.assertEqual(response.status_code, status.HTTP_409_CONFLICT)
                    transaction.refresh_from_db()
                    debt.refresh_from_db()
                    self.product.refresh_from_db()
                    self.assertEqual(transaction.status, self.active_status)
                    self.assertEqual(debt.payments.count(), payment_count)
                    self.assertEqual(self.product.stock, stock_before)
                    self.assertEqual(
                        StockMovement.objects.filter(transaction=transaction).count(),
                        movement_count,
                    )

                    self.product = create_product(
                        business=self.business_a,
                        status=self.active_status,
                        stock=20,
                    )

    def test_any_inconsistent_financial_evidence_blocks_cancellation(self):
        cases = ("paid_amount", "is_settled", "payment_history")
        for evidence in cases:
            with self.subTest(evidence=evidence):
                transaction, debt = self.create_debt_transaction("sale")
                if evidence == "paid_amount":
                    Debt.objects.filter(pk=debt.pk).update(paid_amount=Decimal("1.00"))
                elif evidence == "is_settled":
                    Debt.objects.filter(pk=debt.pk).update(
                        paid_amount=debt.total_amount,
                        is_settled=True,
                    )
                else:
                    DebtPayment.objects.create(
                        debt=debt,
                        amount=Decimal("1.00"),
                        payment_date=date.today(),
                        payment_method=self.method,
                        transaction=transaction,
                    )

                movement_count = StockMovement.objects.filter(transaction=transaction).count()
                response = self.cancel(transaction)
                self.assertEqual(response.status_code, status.HTTP_409_CONFLICT)
                transaction.refresh_from_db()
                self.assertEqual(transaction.status, self.active_status)
                self.assertEqual(
                    StockMovement.objects.filter(transaction=transaction).count(),
                    movement_count,
                )
                self.product = create_product(
                    business=self.business_a,
                    status=self.active_status,
                    stock=20,
                )

    def test_inventory_and_status_failures_roll_back_debt_cancellation(self):
        failure_targets = (
            "core.services.transaction_cancellation.record_locked_stock_movement",
            "core.models.StockMovement.objects.create",
            "core.models.Transaction.save",
        )
        for target in failure_targets:
            with self.subTest(target=target):
                transaction, debt = self.create_debt_transaction("sale")
                self.product.refresh_from_db()
                stock_before = self.product.stock
                movement_count = StockMovement.objects.filter(transaction=transaction).count()

                with patch(target, side_effect=RuntimeError("forced cancellation failure")):
                    with self.assertRaises(RuntimeError):
                        self.cancel(transaction)

                transaction.refresh_from_db()
                debt.refresh_from_db()
                self.product.refresh_from_db()
                self.assertEqual(transaction.status, self.active_status)
                self.assertEqual(self.product.stock, stock_before)
                self.assertEqual(debt.paid_amount, Decimal("0.00"))
                self.assertEqual(
                    StockMovement.objects.filter(transaction=transaction).count(),
                    movement_count,
                )
                self.product = create_product(
                    business=self.business_a,
                    status=self.active_status,
                    stock=20,
                )

    def test_paid_expense_without_debt_keeps_existing_delete_contract(self):
        response = self.client.post(
            "/api/transactions/",
            {
                "business_public_id": str(self.business_a.public_id),
                "type": "expense",
                "expense_amount": "25.00",
                "payment_status": "paid",
                "payment_method_public_id": str(self.method.public_id),
            },
            format="json",
        )
        self.assertEqual(response.status_code, status.HTTP_201_CREATED, response.data)
        transaction = Transaction.objects.get(public_id=response.data["public_id"])
        self.assertFalse(Debt.objects.filter(transaction=transaction).exists())
        self.assertEqual(self.cancel(transaction).status_code, status.HTTP_204_NO_CONTENT)
        self.assertEqual(self.cancel(transaction).status_code, status.HTTP_409_CONFLICT)

    def test_delete_locks_debt_before_transaction_with_self_only_for_update(self):
        transaction, _ = self.create_debt_transaction("sale")
        second_product = create_product(
            business=self.business_a,
            status=self.active_status,
            stock=10,
        )
        record_stock_movement(
            product=second_product,
            transaction=transaction,
            created_by=self.user_a,
            movement_type="sale",
            quantity=-1,
        )
        with CaptureQueriesContext(connection) as queries:
            cancel_transaction(
                transaction_id=transaction.pk,
                business_id=self.business_a.pk,
                terminal_status=self.void_status,
                actor=self.user_a,
            )

        lock_sql = [
            query["sql"]
            for query in queries.captured_queries
            if "FOR UPDATE" in query["sql"]
        ]
        debt_lock = next(i for i, sql in enumerate(lock_sql) if 'OF "core_debt"' in sql)
        transaction_lock = next(
            i for i, sql in enumerate(lock_sql) if 'OF "core_transaction"' in sql
        )
        self.assertLess(debt_lock, transaction_lock)
        product_locks = [
            sql for sql in lock_sql
            if 'OF "core_product"' in sql
        ]
        self.assertEqual(len(product_locks), 1)
        self.assertIn('ORDER BY "core_product"."id" ASC', product_locks[0])

    def test_openapi_documents_transaction_delete_conflicts(self):
        schema = SchemaGenerator().get_schema(request=None, public=True)
        operation = schema["paths"]["/api/transactions/{public_id}/"]["delete"]
        self.assertTrue(
            {"204", "400", "403", "404", "409"}.issubset(operation["responses"])
        )
        self.assertNotIn("content", operation["responses"]["204"])

        inventory_error = operation["responses"]["400"]["content"][
            "application/json"
        ]["schema"]
        self.assertIn("$ref", inventory_error)
        inventory_component = schema["components"]["schemas"][
            inventory_error["$ref"].rsplit("/", 1)[-1]
        ]
        self.assertIn("details", inventory_component["properties"])

        conflict = operation["responses"]["409"]["content"]["application/json"]["schema"]
        self.assertIn("$ref", conflict)
        component = schema["components"]["schemas"][conflict["$ref"].rsplit("/", 1)[-1]]
        self.assertIn("detail", component["properties"])
        self.assertIn("non_field_errors", component["properties"])

        transaction, _ = self.create_debt_transaction("purchase")
        self.product.stock = 0
        self.product.save(update_fields=["stock"])
        response = self.cancel(transaction)
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertEqual(
            set(response.data),
            set(inventory_component["properties"]),
        )

    def test_transaction_creation_locks_unique_products_in_pk_order(self):
        for transaction_type, reverse, repeated in (
            ("sale", True, False),
            ("sale", False, False),
            ("sale", True, True),
            ("purchase", True, False),
        ):
            with self.subTest(
                transaction_type=transaction_type,
                reverse=reverse,
                repeated=repeated,
            ):
                product_a = create_product(
                    business=self.business_a,
                    status=self.active_status,
                    stock=20,
                )
                product_b = create_product(
                    business=self.business_a,
                    status=self.active_status,
                    stock=20,
                )
                products = [product_b, product_a] if reverse else [product_a, product_b]
                if repeated:
                    products.append(product_b)
                payload = {
                    "business_public_id": str(self.business_a.public_id),
                    "type": transaction_type,
                    "payment_status": "pending",
                    "details": [
                        {
                            "product_public_id": str(product.public_id),
                            "quantity": 1,
                        }
                        for product in products
                    ],
                }
                if transaction_type == "sale":
                    payload.update({
                        "customer_public_id": str(self.customer.public_id),
                        "employee_public_id": str(self.seller.public_id),
                    })
                else:
                    payload["supplier_public_id"] = str(self.supplier.public_id)

                with CaptureQueriesContext(connection) as queries:
                    response = self.client.post(
                        "/api/transactions/",
                        payload,
                        format="json",
                    )
                self.assertEqual(response.status_code, status.HTTP_201_CREATED, response.data)

                lock_sql = [
                    query["sql"]
                    for query in queries.captured_queries
                    if 'FOR UPDATE OF "core_product"' in query["sql"]
                ]
                self.assertEqual(len(lock_sql), 1)
                self.assertIn('ORDER BY "core_product"."id" ASC', lock_sql[0])
                match = re.search(
                    r'"core_product"\."id" IN \(([^)]+)\)',
                    lock_sql[0],
                )
                self.assertIsNotNone(match)
                locked_ids = [int(value.strip()) for value in match.group(1).split(",")]
                self.assertEqual(locked_ids, sorted({product_a.pk, product_b.pk}))

                product_a.refresh_from_db()
                product_b.refresh_from_db()
                direction = -1 if transaction_type == "sale" else 1
                self.assertEqual(product_a.stock, 20 + direction)
                expected_b_delta = direction * (2 if repeated else 1)
                self.assertEqual(product_b.stock, 20 + expected_b_delta)

    def test_creation_product_lock_remains_business_scoped(self):
        foreign_product = create_product(
            business=self.business_b,
            status=self.active_status,
            stock=20,
        )
        response = self.client.post(
            "/api/transactions/",
            {
                "business_public_id": str(self.business_a.public_id),
                "type": "sale",
                "payment_status": "pending",
                "customer_public_id": str(self.customer.public_id),
                "employee_public_id": str(self.seller.public_id),
                "details": [{
                    "product_public_id": str(foreign_product.public_id),
                    "quantity": 1,
                }],
            },
            format="json",
        )
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertFalse(Transaction.objects.filter(business=self.business_a).exists())


class ConcurrentTransactionDebtCancellationTests(TransactionTestCase):
    reset_sequences = True

    def setUp(self):
        self.active_status = create_status("Activo")
        self.void_status = create_status("Anulado")
        self.owner = create_user(email="cancel-payment-race@playnow.test")
        self.business = create_business(
            user=self.owner,
            status=self.active_status,
        )
        self.method = create_payment_method(
            business=self.business,
            status=self.active_status,
        )

    def build_debt_sale(self):
        product = create_product(
            business=self.business,
            status=self.active_status,
            stock=10,
        )
        transaction = create_transaction(
            business=self.business,
            created_by=self.owner,
            status=self.active_status,
            transaction_type="sale",
            total_value=Decimal("100.00"),
            is_debt=True,
        )
        debt = create_debt(transaction=transaction, total_amount=Decimal("100.00"))
        record_stock_movement(
            product=product,
            transaction=transaction,
            created_by=self.owner,
            movement_type="sale",
            quantity=-2,
        )
        return product, transaction, debt

    def cancellation_worker(self, transaction_id, lock_held=None, release_lock=None, started=None):
        close_old_connections()
        try:
            if started is not None:
                started.set()
            try:
                with db_tx.atomic():
                    cancel_transaction(
                        transaction_id=transaction_id,
                        business_id=self.business.pk,
                        terminal_status=type(self.void_status).objects.get(pk=self.void_status.pk),
                        actor=type(self.owner).objects.get(pk=self.owner.pk),
                    )
                    if lock_held is not None:
                        lock_held.set()
                        if not release_lock.wait(timeout=10):
                            raise AssertionError("Cancellation lock was not released")
                return status.HTTP_204_NO_CONTENT
            except DebtPaymentConflict:
                return status.HTTP_409_CONFLICT
        finally:
            close_old_connections()

    def payment_worker(self, debt_id, lock_held=None, release_lock=None, started=None):
        close_old_connections()
        try:
            if started is not None:
                started.set()
            try:
                with db_tx.atomic():
                    register_debt_payment(
                        debt_id=debt_id,
                        amount=Decimal("25.00"),
                        payment_date=timezone.localdate(),
                        payment_method_id=self.method.pk,
                        actor=type(self.owner).objects.get(pk=self.owner.pk),
                        observed_remaining_amount=Decimal("100.00"),
                    )
                    if lock_held is not None:
                        lock_held.set()
                        if not release_lock.wait(timeout=10):
                            raise AssertionError("Payment lock was not released")
                return status.HTTP_201_CREATED
            except DebtPaymentConflict:
                return status.HTTP_409_CONFLICT
        finally:
            close_old_connections()

    def assert_worker_waits(self, future, started):
        self.assertTrue(started.wait(timeout=10))
        with self.assertRaises(FutureTimeoutError):
            future.result(timeout=0.25)

    def test_delete_wins_and_waiting_payment_returns_conflict(self):
        product, transaction, debt = self.build_debt_sale()
        lock_held = Event()
        release_lock = Event()
        payment_started = Event()
        with ThreadPoolExecutor(max_workers=2) as executor:
            cancellation = executor.submit(
                self.cancellation_worker,
                transaction.pk,
                lock_held,
                release_lock,
            )
            self.assertTrue(lock_held.wait(timeout=10))
            payment = executor.submit(
                self.payment_worker,
                debt.pk,
                started=payment_started,
            )
            self.assert_worker_waits(payment, payment_started)
            release_lock.set()
            self.assertEqual(cancellation.result(timeout=10), status.HTTP_204_NO_CONTENT)
            self.assertEqual(payment.result(timeout=10), status.HTTP_409_CONFLICT)

        transaction.refresh_from_db()
        product.refresh_from_db()
        self.assertEqual(transaction.status.name, "Anulado")
        self.assertEqual(product.stock, 10)
        self.assertFalse(DebtPayment.objects.filter(debt=debt).exists())
        self.assertEqual(
            StockMovement.objects.filter(transaction=transaction, type="adjustment").count(),
            1,
        )

    def test_payment_wins_and_waiting_delete_returns_conflict(self):
        product, transaction, debt = self.build_debt_sale()
        lock_held = Event()
        release_lock = Event()
        cancellation_started = Event()
        with ThreadPoolExecutor(max_workers=2) as executor:
            payment = executor.submit(
                self.payment_worker,
                debt.pk,
                lock_held,
                release_lock,
            )
            self.assertTrue(lock_held.wait(timeout=10))
            cancellation = executor.submit(
                self.cancellation_worker,
                transaction.pk,
                started=cancellation_started,
            )
            self.assert_worker_waits(cancellation, cancellation_started)
            release_lock.set()
            self.assertEqual(payment.result(timeout=10), status.HTTP_201_CREATED)
            self.assertEqual(cancellation.result(timeout=10), status.HTTP_409_CONFLICT)

        transaction.refresh_from_db()
        debt.refresh_from_db()
        product.refresh_from_db()
        self.assertEqual(transaction.status.name, "Activo")
        self.assertEqual(debt.paid_amount, Decimal("25.00"))
        self.assertEqual(DebtPayment.objects.filter(debt=debt).count(), 1)
        self.assertEqual(product.stock, 8)
        self.assertFalse(
            StockMovement.objects.filter(transaction=transaction, type="adjustment").exists()
        )


class ConcurrentInverseProductOrderTests(TransactionTestCase):
    reset_sequences = True

    def setUp(self):
        self.active_status = create_status("Activo")
        self.owner = create_user(email="inverse-product-locks@playnow.test")
        self.business = create_business(user=self.owner, status=self.active_status)
        self.supplier = create_supplier(
            business=self.business,
            status=self.active_status,
        )
        self.product_a = create_product(
            business=self.business,
            status=self.active_status,
            stock=10,
        )
        self.product_b = create_product(
            business=self.business,
            status=self.active_status,
            stock=10,
        )

    def creation_worker(self, product_public_ids, start_barrier):
        close_old_connections()
        try:
            client = APIClient()
            owner = type(self.owner).objects.get(pk=self.owner.pk)
            client.force_authenticate(user=owner)
            start_barrier.wait(timeout=10)
            response = client.post(
                "/api/transactions/",
                {
                    "business_public_id": str(self.business.public_id),
                    "type": "purchase",
                    "payment_status": "pending",
                    "supplier_public_id": str(self.supplier.public_id),
                    "details": [
                        {
                            "product_public_id": product_public_id,
                            "quantity": 1,
                        }
                        for product_public_id in product_public_ids
                    ],
                },
                format="json",
            )
            return response.status_code, getattr(response, "data", None)
        finally:
            close_old_connections()

    def test_inverse_payload_orders_complete_without_deadlock(self):
        product_a = str(self.product_a.public_id)
        product_b = str(self.product_b.public_id)
        start_barrier = Barrier(2)
        with ThreadPoolExecutor(max_workers=2) as executor:
            first = executor.submit(
                self.creation_worker,
                [product_a, product_b],
                start_barrier,
            )
            second = executor.submit(
                self.creation_worker,
                [product_b, product_a],
                start_barrier,
            )
            first_result = first.result(timeout=15)
            second_result = second.result(timeout=15)

        self.assertEqual(first_result[0], status.HTTP_201_CREATED, first_result[1])
        self.assertEqual(second_result[0], status.HTTP_201_CREATED, second_result[1])
        self.product_a.refresh_from_db()
        self.product_b.refresh_from_db()
        self.assertEqual(self.product_a.stock, 12)
        self.assertEqual(self.product_b.stock, 12)
        transactions = Transaction.objects.filter(business=self.business)
        self.assertEqual(transactions.count(), 2)
        self.assertEqual(
            StockMovement.objects.filter(transaction__in=transactions).count(),
            4,
        )
        self.assertTrue(all(transaction.details.count() == 2 for transaction in transactions))
