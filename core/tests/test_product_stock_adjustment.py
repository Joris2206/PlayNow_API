from concurrent.futures import (
    ThreadPoolExecutor,
    TimeoutError as FutureTimeoutError,
)
from threading import Barrier, Event
from unittest.mock import patch

from django.db import close_old_connections
from django.test import TransactionTestCase
from django.urls import resolve, reverse
from drf_spectacular.generators import SchemaGenerator
from rest_framework import status
from rest_framework.test import APIClient

from core.models import BusinessMembership, Product, StockMovement
from core.services.inventory import (
    adjust_product_stock,
    record_locked_stock_movement,
)
from core.tests.base import BusinessIsolationTestCase
from core.tests.factories import (
    create_business,
    create_customer,
    create_payment_method,
    create_product,
    create_role_user,
    create_status,
    create_supplier,
    create_user,
)


class ProductStockAdjustmentTests(BusinessIsolationTestCase):
    @classmethod
    def setUpTestData(cls):
        super().setUpTestData()
        cls.inactive_status = create_status("Inactivo")
        cls.deleted_status = create_status("Eliminado")
        cls.product = create_product(
            business=cls.business_a,
            status=cls.active_status,
            title="Producto ajustable",
            stock=10,
        )
        cls.inactive_product = create_product(
            business=cls.business_a,
            status=cls.inactive_status,
            stock=10,
        )
        cls.deleted_product = create_product(
            business=cls.business_a,
            status=cls.deleted_status,
            stock=10,
        )
        cls.foreign_product = create_product(
            business=cls.business_b,
            status=cls.active_status,
            stock=10,
        )
        cls.admin, _, _ = create_role_user(
            business=cls.business_a,
            role=BusinessMembership.ROLE_ADMIN,
            status=cls.active_status,
        )
        cls.inventory, _, _ = create_role_user(
            business=cls.business_a,
            role=BusinessMembership.ROLE_INVENTORY,
            status=cls.active_status,
        )
        cls.cashier, _, _ = create_role_user(
            business=cls.business_a,
            role=BusinessMembership.ROLE_CASHIER,
            status=cls.active_status,
        )
        cls.seller, _, _ = create_role_user(
            business=cls.business_a,
            role=BusinessMembership.ROLE_SELLER,
            status=cls.active_status,
        )
        cls.viewer, _, _ = create_role_user(
            business=cls.business_a,
            role=BusinessMembership.ROLE_VIEWER,
            status=cls.active_status,
        )
        cls.no_membership = create_user(
            email="no-membership-adjustment@playnow.test",
        )
        cls.inactive_member, _, inactive_membership = create_role_user(
            business=cls.business_a,
            role=BusinessMembership.ROLE_INVENTORY,
            status=cls.active_status,
        )
        inactive_membership.is_active = False
        inactive_membership.save(update_fields=["is_active", "updated_at"])
        cls.superuser = create_user(
            email="superuser-adjustment@playnow.test",
            is_superuser=True,
        )

    def endpoint(self, product=None):
        return reverse(
            "product-adjust-stock",
            kwargs={"public_id": (product or self.product).public_id},
        )

    def post_adjustment(self, *, quantity, note="Conteo físico de cierre"):
        return self.client.post(
            self.endpoint(),
            {"quantity": quantity, "note": note},
            format="json",
        )

    def test_positive_adjustment_returns_stock_bounds_and_one_movement(self):
        response = self.post_adjustment(quantity=3)

        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        self.assertEqual(response.data["previous_stock"], 10)
        self.assertEqual(response.data["new_stock"], 13)
        self.product.refresh_from_db()
        self.assertEqual(self.product.stock, 13)

        movement = StockMovement.objects.get(product=self.product)
        self.assertEqual(movement.quantity, 3)
        self.assertEqual(movement.type, "adjustment")
        self.assertEqual(movement.created_by, self.user_a)
        self.assertIsNone(movement.transaction_id)
        self.assertIsNone(movement.transaction_detail_id)

        movement_data = response.data["movement"]
        self.assertEqual(movement_data["quantity"], 3)
        self.assertEqual(movement_data["type"], "adjustment")
        self.assertEqual(movement_data["origin_type"], "adjustment")
        self.assertIsNone(movement_data["is_reversal"])
        self.assertIsNone(movement_data["transaction_public_id"])
        self.assertIsNone(movement_data["transaction_detail_public_id"])

    def test_negative_adjustment_preserves_exact_valid_note(self):
        note = "Conteo físico de cierre"
        response = self.post_adjustment(quantity=-3, note=note)

        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        self.assertEqual(response.data["previous_stock"], 10)
        self.assertEqual(response.data["new_stock"], 7)
        self.assertEqual(response.data["movement"]["note"], note)
        self.product.refresh_from_db()
        self.assertEqual(self.product.stock, 7)

    def test_zero_and_insufficient_adjustments_are_controlled_and_atomic(self):
        for quantity in (0, -11):
            with self.subTest(quantity=quantity):
                response = self.post_adjustment(quantity=quantity)
                self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
                self.assertIn("quantity", response.data)
                self.product.refresh_from_db()
                self.assertEqual(self.product.stock, 10)
                self.assertFalse(StockMovement.objects.exists())

    def test_quantity_and_note_validation(self):
        cases = (
            ({"note": "Motivo"}, "quantity"),
            ({"quantity": None, "note": "Motivo"}, "quantity"),
            ({"quantity": "invalid", "note": "Motivo"}, "quantity"),
            ({"quantity": "1.5", "note": "Motivo"}, "quantity"),
            ({"quantity": 1}, "note"),
            ({"quantity": 1, "note": None}, "note"),
            ({"quantity": 1, "note": ""}, "note"),
            ({"quantity": 1, "note": "   "}, "note"),
            ({"quantity": 1, "note": "x" * 256}, "note"),
        )
        for payload, error_key in cases:
            with self.subTest(payload=payload):
                response = self.client.post(
                    self.endpoint(), payload, format="json"
                )
                self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
                self.assertIn(error_key, response.data)
                self.product.refresh_from_db()
                self.assertEqual(self.product.stock, 10)
                self.assertFalse(StockMovement.objects.exists())

    def test_boolean_true_quantity_is_rejected_without_effects(self):
        response = self.client.post(
            self.endpoint(),
            {"quantity": True, "note": "Motivo"},
            format="json",
        )

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn("quantity", response.data)
        self.product.refresh_from_db()
        self.assertEqual(self.product.stock, 10)
        self.assertFalse(StockMovement.objects.exists())

    def test_boolean_false_quantity_is_rejected_without_effects(self):
        response = self.client.post(
            self.endpoint(),
            {"quantity": False, "note": "Motivo"},
            format="json",
        )

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn("quantity", response.data)
        self.product.refresh_from_db()
        self.assertEqual(self.product.stock, 10)
        self.assertFalse(StockMovement.objects.exists())

    def test_note_is_trimmed_in_response_and_persisted_movement(self):
        response = self.post_adjustment(
            quantity=1,
            note="  Conteo físico  ",
        )

        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        self.assertEqual(response.data["previous_stock"], 10)
        self.assertEqual(response.data["new_stock"], 11)
        self.assertEqual(response.data["movement"]["note"], "Conteo físico")
        movement = StockMovement.objects.get(product=self.product)
        self.assertEqual(movement.note, "Conteo físico")
        self.product.refresh_from_db()
        self.assertEqual(self.product.stock, 11)
        self.assertEqual(
            StockMovement.objects.filter(product=self.product).count(),
            1,
        )

    def test_note_with_exactly_255_characters_is_accepted(self):
        note = "x" * 255
        response = self.post_adjustment(quantity=1, note=note)

        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        self.assertEqual(response.data["movement"]["note"], note)
        movement = StockMovement.objects.get(product=self.product)
        self.assertEqual(movement.note, note)

    def test_note_with_256_characters_is_rejected_without_effects(self):
        response = self.post_adjustment(quantity=1, note="x" * 256)

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn("note", response.data)
        self.product.refresh_from_db()
        self.assertEqual(self.product.stock, 10)
        self.assertFalse(StockMovement.objects.exists())

    def test_product_and_movement_unknown_fields_are_rejected_exactly(self):
        for field, value in (("product", "valor"), ("movement", {})):
            with self.subTest(field=field):
                response = self.client.post(
                    self.endpoint(),
                    {"quantity": 1, "note": "Motivo", field: value},
                    format="json",
                )

                self.assertEqual(
                    response.status_code,
                    status.HTTP_400_BAD_REQUEST,
                )
                self.assertEqual(
                    response.data[field],
                    "Este campo no está permitido.",
                )
                self.product.refresh_from_db()
                self.assertEqual(self.product.stock, 10)
                self.assertFalse(StockMovement.objects.exists())

    def test_unknown_and_server_controlled_fields_are_rejected(self):
        forbidden_fields = (
            "business_public_id",
            "product_public_id",
            "stock",
            "new_stock",
            "previous_stock",
            "type",
            "origin_type",
            "is_reversal",
            "transaction_public_id",
            "transaction_detail_public_id",
            "created_by",
        )
        for field in forbidden_fields:
            with self.subTest(field=field):
                response = self.client.post(
                    self.endpoint(),
                    {"quantity": 1, "note": "Motivo", field: "ignored"},
                    format="json",
                )
                self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
                self.assertIn(field, response.data)
                self.product.refresh_from_db()
                self.assertEqual(self.product.stock, 10)
                self.assertFalse(StockMovement.objects.exists())

    def test_inactive_and_deleted_products_are_rejected(self):
        for product in (self.inactive_product, self.deleted_product):
            with self.subTest(status=product.status.name):
                response = self.client.post(
                    self.endpoint(product),
                    {"quantity": 1, "note": "Motivo"},
                    format="json",
                )
                self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
                self.assertIn("product", response.data)
                product.refresh_from_db()
                self.assertEqual(product.stock, 10)
                self.assertFalse(
                    StockMovement.objects.filter(product=product).exists()
                )

    def test_allowed_roles_and_superuser_can_adjust(self):
        for user in (self.user_a, self.admin, self.inventory, self.superuser):
            with self.subTest(user=user.email):
                self.authenticate_as(user)
                response = self.post_adjustment(quantity=1)
                self.assertEqual(response.status_code, status.HTTP_201_CREATED)

    def test_read_only_roles_are_forbidden(self):
        for user in (self.cashier, self.seller, self.viewer):
            with self.subTest(user=user.email):
                self.authenticate_as(user)
                response = self.post_adjustment(quantity=1)
                self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)

        self.product.refresh_from_db()
        self.assertEqual(self.product.stock, 10)
        self.assertFalse(StockMovement.objects.exists())

    def test_invisible_memberships_and_foreign_or_missing_products_are_404(self):
        for user in (self.no_membership, self.inactive_member):
            with self.subTest(user=user.email):
                self.authenticate_as(user)
                response = self.post_adjustment(quantity=1)
                self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND)

        self.authenticate_as(self.user_a)
        foreign = self.client.post(
            self.endpoint(self.foreign_product),
            {"quantity": 1, "note": "Motivo"},
            format="json",
        )
        missing = self.client.post(
            reverse(
                "product-adjust-stock",
                kwargs={"public_id": "00000000-0000-0000-0000-000000000000"},
            ),
            {"quantity": 1, "note": "Motivo"},
            format="json",
        )
        self.assertEqual(foreign.status_code, status.HTTP_404_NOT_FOUND)
        self.assertEqual(missing.status_code, status.HTTP_404_NOT_FOUND)
        self.foreign_product.refresh_from_db()
        self.assertEqual(self.foreign_product.stock, 10)

    def test_unauthenticated_request_is_401(self):
        self.client.force_authenticate(user=None)
        response = self.post_adjustment(quantity=1)
        self.assertEqual(response.status_code, status.HTTP_401_UNAUTHORIZED)

    def test_movement_is_visible_in_business_history(self):
        response = self.post_adjustment(quantity=1)
        movement_id = response.data["movement"]["public_id"]

        history = self.client.get(
            "/api/stock-movements/",
            {"business_public_id": str(self.business_a.public_id)},
        )
        self.assertEqual(history.status_code, status.HTTP_200_OK)
        self.assertEqual(
            [str(row["public_id"]) for row in history.data["results"]],
            [str(movement_id)],
        )

    def test_stock_movement_direct_writes_remain_disallowed(self):
        response = self.post_adjustment(quantity=1)
        movement_id = response.data["movement"]["public_id"]
        detail = f"/api/stock-movements/{movement_id}/"

        self.assertEqual(self.client.post("/api/stock-movements/", {}).status_code, 405)
        self.assertEqual(self.client.put(detail, {}).status_code, 405)
        self.assertEqual(self.client.patch(detail, {}).status_code, 405)
        self.assertEqual(self.client.delete(detail).status_code, 405)

    def test_movement_failure_rolls_back_stock(self):
        with patch(
            "core.services.inventory.StockMovement.objects.create",
            side_effect=RuntimeError("movement insert failed"),
        ):
            with self.assertRaisesMessage(RuntimeError, "movement insert failed"):
                self.post_adjustment(quantity=2)

        self.product.refresh_from_db()
        self.assertEqual(self.product.stock, 10)
        self.assertFalse(StockMovement.objects.exists())

    def test_reverse_resolve_and_non_post_methods(self):
        endpoint = self.endpoint()
        match = resolve(endpoint)
        self.assertEqual(match.url_name, "product-adjust-stock")
        self.assertEqual(match.func.cls.__name__, "ProductViewSet")
        self.assertEqual(match.func.actions, {"post": "adjust_stock"})

        for method in ("get", "put", "patch", "delete"):
            with self.subTest(method=method):
                response = getattr(self.client, method)(
                    endpoint,
                    {"quantity": 1, "note": "Motivo"},
                    format="json",
                )
                self.assertEqual(response.status_code, status.HTTP_405_METHOD_NOT_ALLOWED)

    def test_openapi_documents_the_closed_adjustment_contract(self):
        schema = SchemaGenerator().get_schema(request=None, public=True)
        path = schema["paths"][
            "/api/products/{public_id}/adjust-stock/"
        ]
        self.assertEqual(set(path), {"post"})
        operation = path["post"]
        self.assertEqual(
            operation["operationId"],
            "api_products_adjust_stock_create",
        )
        self.assertEqual(
            operation["security"],
            [{"BearerAuth": []}, {"BearerAuth": []}],
        )
        self.assertEqual(
            operation["parameters"],
            [{
                "in": "path",
                "name": "public_id",
                "schema": {"type": "string", "format": "uuid"},
                "required": True,
            }],
        )
        self.assertEqual(set(operation["responses"]), {"201", "400", "401", "403", "404"})
        request_ref = operation["requestBody"]["content"]["application/json"]["schema"]["$ref"]
        request_schema = schema["components"]["schemas"][request_ref.rsplit("/", 1)[-1]]
        self.assertEqual(set(request_schema["properties"]), {"quantity", "note"})
        self.assertEqual(set(request_schema["required"]), {"quantity", "note"})

        response_ref = operation["responses"]["201"]["content"]["application/json"]["schema"]["$ref"]
        response_schema = schema["components"]["schemas"][response_ref.rsplit("/", 1)[-1]]
        self.assertEqual(
            set(response_schema["properties"]),
            {"previous_stock", "new_stock", "movement"},
        )
        movement_ref = response_schema["properties"]["movement"]["allOf"][0]["$ref"]
        self.assertEqual(movement_ref, "#/components/schemas/StockMovement")

        error_ref = operation["responses"]["400"]["content"][
            "application/json"
        ]["schema"]["$ref"]
        error_schema = schema["components"]["schemas"][
            error_ref.rsplit("/", 1)[-1]
        ]
        self.assertEqual(
            set(error_schema["properties"]),
            {"quantity", "note", "product", "non_field_errors"},
        )
        for field in ("quantity", "note", "product", "non_field_errors"):
            self.assertEqual(
                error_schema["properties"][field],
                {
                    "type": "array",
                    "items": {"type": "string"},
                },
            )
        self.assertEqual(
            error_schema["additionalProperties"],
            {"type": "string"},
        )


class ProductStockAdjustmentConcurrencyTests(TransactionTestCase):
    reset_sequences = True

    def setUp(self):
        if Product.objects.db_manager("default").db != "default":
            self.skipTest("Database routing inesperado.")

        self.active_status = create_status("Activo")
        self.inactive_status = create_status("Inactivo")
        create_status("Anulado")
        self.owner = create_user(email="adjust-concurrency@playnow.test")
        self.business = create_business(
            user=self.owner,
            status=self.active_status,
        )
        self.product = create_product(
            business=self.business,
            status=self.active_status,
            stock=10,
        )
        self.cashier, _, _ = create_role_user(
            business=self.business,
            role=BusinessMembership.ROLE_CASHIER,
            status=self.active_status,
        )
        _, self.seller_employee, _ = create_role_user(
            business=self.business,
            role=BusinessMembership.ROLE_SELLER,
            status=self.active_status,
        )
        self.customer = create_customer(
            business=self.business,
            status=self.active_status,
        )
        self.supplier = create_supplier(
            business=self.business,
            status=self.active_status,
        )

    def endpoint(self):
        return reverse(
            "product-adjust-stock",
            kwargs={"public_id": self.product.public_id},
        )

    def _request_adjustment(self, quantity, started):
        close_old_connections()
        try:
            client = APIClient()
            client.force_authenticate(
                user=type(self.owner).objects.get(pk=self.owner.pk)
            )
            started.set()
            response = client.post(
                self.endpoint(),
                {"quantity": quantity, "note": "Ajuste concurrente"},
                format="json",
            )
            return response.status_code, getattr(response, "data", None)
        finally:
            close_old_connections()

    def _request_concurrent_adjustment(self, quantity):
        close_old_connections()
        try:
            client = APIClient()
            client.force_authenticate(
                user=type(self.owner).objects.get(pk=self.owner.pk)
            )
            response = client.post(
                self.endpoint(),
                {"quantity": quantity, "note": "Ajuste concurrente"},
                format="json",
            )
            return response.status_code, getattr(response, "data", None)
        finally:
            close_old_connections()

    def _run_concurrent_adjustments(self, *quantities):
        service_barrier = Barrier(len(quantities))

        def synchronized_adjust_product_stock(**kwargs):
            service_barrier.wait(timeout=10)
            return adjust_product_stock(**kwargs)

        with patch(
            "core.views.adjust_product_stock",
            side_effect=synchronized_adjust_product_stock,
        ):
            with ThreadPoolExecutor(max_workers=len(quantities)) as executor:
                futures = [
                    executor.submit(
                        self._request_concurrent_adjustment,
                        quantity,
                    )
                    for quantity in quantities
                ]
                return [future.result(timeout=15) for future in futures]

    def _hold_product_lock_and_adjust(self, quantity, lock_held, release_lock):
        close_old_connections()
        try:
            from django.db import transaction as db_tx

            with db_tx.atomic():
                product = (
                    Product.objects
                    .select_for_update(of=("self",))
                    .get(pk=self.product.pk)
                )
                lock_held.set()
                if not release_lock.wait(timeout=10):
                    raise AssertionError("Product lock was not released")
                record_locked_stock_movement(
                    product=product,
                    quantity=quantity,
                    movement_type="adjustment",
                    created_by=type(self.owner).objects.get(pk=self.owner.pk),
                    note="Ajuste que mantiene el lock",
                )
            return status.HTTP_201_CREATED
        finally:
            close_old_connections()

    def _request_transaction(self, payload, user_id, started):
        close_old_connections()
        try:
            client = APIClient()
            client.force_authenticate(
                user=type(self.owner).objects.get(pk=user_id)
            )
            started.set()
            response = client.post(
                "/api/transactions/",
                payload,
                format="json",
            )
            return response.status_code, getattr(response, "data", None)
        finally:
            close_old_connections()

    def _request_cancellation(self, transaction_public_id, started):
        close_old_connections()
        try:
            client = APIClient()
            client.force_authenticate(
                user=type(self.owner).objects.get(pk=self.owner.pk)
            )
            started.set()
            response = client.delete(
                f"/api/transactions/{transaction_public_id}/"
            )
            return response.status_code, getattr(response, "data", None)
        finally:
            close_old_connections()

    def _assert_request_waits_for_held_product_lock(
        self,
        *,
        held_quantity,
        request_worker,
        request_args,
    ):
        lock_held = Event()
        release_lock = Event()
        request_started = Event()
        with ThreadPoolExecutor(max_workers=2) as executor:
            holder = executor.submit(
                self._hold_product_lock_and_adjust,
                held_quantity,
                lock_held,
                release_lock,
            )
            self.assertTrue(lock_held.wait(timeout=10))
            request = executor.submit(
                request_worker,
                *request_args,
                request_started,
            )
            self.assertTrue(request_started.wait(timeout=10))
            try:
                early_result = request.result(timeout=0.25)
            except FutureTimeoutError:
                early_result = None
            if early_result is not None:
                release_lock.set()
                holder.result(timeout=10)
                self.fail(
                    "La petición no esperó el Product lock: "
                    f"{early_result!r}"
                )
            release_lock.set()
            self.assertEqual(
                holder.result(timeout=10),
                status.HTTP_201_CREATED,
            )
            return request.result(timeout=10)

    def test_adjustment_vs_adjustment_uses_the_product_lock(self):
        lock_held = Event()
        release_lock = Event()
        request_started = Event()
        with ThreadPoolExecutor(max_workers=2) as executor:
            first = executor.submit(
                self._hold_product_lock_and_adjust,
                2,
                lock_held,
                release_lock,
            )
            self.assertTrue(lock_held.wait(timeout=10))
            second = executor.submit(
                self._request_adjustment,
                3,
                request_started,
            )
            self.assertTrue(request_started.wait(timeout=10))
            with self.assertRaises(FutureTimeoutError):
                second.result(timeout=0.25)
            release_lock.set()
            self.assertEqual(first.result(timeout=10), status.HTTP_201_CREATED)
            self.assertEqual(second.result(timeout=10)[0], status.HTTP_201_CREATED)

        self.product.refresh_from_db()
        self.assertEqual(self.product.stock, 15)
        self.assertEqual(StockMovement.objects.filter(product=self.product).count(), 2)

    def test_two_valid_concurrent_manual_decreases_are_serializable(self):
        results = self._run_concurrent_adjustments(-3, -2)

        self.assertEqual(
            [response_status for response_status, _ in results],
            [status.HTTP_201_CREATED, status.HTTP_201_CREATED],
        )
        transitions = {
            data["movement"]["quantity"]: (
                data["previous_stock"],
                data["new_stock"],
            )
            for _, data in results
        }
        self.assertIn(
            transitions,
            (
                {-3: (10, 7), -2: (7, 5)},
                {-2: (10, 8), -3: (8, 5)},
            ),
        )
        self.product.refresh_from_db()
        self.assertEqual(self.product.stock, 5)
        self.assertEqual(
            sorted(
                StockMovement.objects.filter(product=self.product)
                .values_list("quantity", flat=True)
            ),
            [-3, -2],
        )

    def test_concurrent_manual_decreases_allow_only_one_oversell(self):
        Product.objects.filter(pk=self.product.pk).update(stock=5)

        results = self._run_concurrent_adjustments(-4, -4)

        self.assertEqual(
            sorted(response_status for response_status, _ in results),
            [status.HTTP_201_CREATED, status.HTTP_400_BAD_REQUEST],
        )
        rejected_data = next(
            data
            for response_status, data in results
            if response_status == status.HTTP_400_BAD_REQUEST
        )
        self.assertIn("quantity", rejected_data)
        self.product.refresh_from_db()
        self.assertEqual(self.product.stock, 1)
        self.assertEqual(
            list(
                StockMovement.objects.filter(product=self.product)
                .values_list("quantity", flat=True)
            ),
            [-4],
        )

    def test_concurrent_manual_increase_and_decrease_are_serializable(self):
        results = self._run_concurrent_adjustments(5, -3)

        self.assertEqual(
            [response_status for response_status, _ in results],
            [status.HTTP_201_CREATED, status.HTTP_201_CREATED],
        )
        transitions = {
            data["movement"]["quantity"]: (
                data["previous_stock"],
                data["new_stock"],
            )
            for _, data in results
        }
        self.assertIn(
            transitions,
            (
                {5: (10, 15), -3: (15, 12)},
                {-3: (10, 7), 5: (7, 12)},
            ),
        )
        self.product.refresh_from_db()
        self.assertEqual(self.product.stock, 12)
        self.assertEqual(
            sorted(
                StockMovement.objects.filter(product=self.product)
                .values_list("quantity", flat=True)
            ),
            [-3, 5],
        )

    def test_negative_adjustment_vs_sale_cannot_oversell(self):
        Product.objects.filter(pk=self.product.pk).update(stock=5)
        sale_payload = {
            "business_public_id": str(self.business.public_id),
            "customer_public_id": str(self.customer.public_id),
            "employee_public_id": str(self.seller_employee.public_id),
            "type": "sale",
            "payment_status": "pending",
            "details": [{
                "product_public_id": str(self.product.public_id),
                "quantity": 3,
            }],
        }

        response_status, response_data = (
            self._assert_request_waits_for_held_product_lock(
                held_quantity=-4,
                request_worker=self._request_transaction,
                request_args=(sale_payload, self.cashier.pk),
            )
        )

        self.assertEqual(response_status, status.HTTP_400_BAD_REQUEST)
        self.assertIn("details", response_data)
        self.product.refresh_from_db()
        self.assertEqual(self.product.stock, 1)
        self.assertEqual(StockMovement.objects.filter(product=self.product).count(), 1)

    def test_adjustment_vs_purchase_preserves_both_deltas(self):
        purchase_payload = {
            "business_public_id": str(self.business.public_id),
            "supplier_public_id": str(self.supplier.public_id),
            "type": "purchase",
            "payment_status": "pending",
            "details": [{
                "product_public_id": str(self.product.public_id),
                "quantity": 3,
            }],
        }

        response_status, response_data = (
            self._assert_request_waits_for_held_product_lock(
                held_quantity=2,
                request_worker=self._request_transaction,
                request_args=(purchase_payload, self.owner.pk),
            )
        )

        self.assertEqual(
            response_status,
            status.HTTP_201_CREATED,
            response_data,
        )
        self.product.refresh_from_db()
        self.assertEqual(self.product.stock, 15)
        self.assertEqual(StockMovement.objects.filter(product=self.product).count(), 2)

    def _create_transaction_for_cancellation(self, transaction_type):
        payload = {
            "business_public_id": str(self.business.public_id),
            "type": transaction_type,
            "payment_status": "pending",
            "details": [{
                "product_public_id": str(self.product.public_id),
                "quantity": 3,
            }],
        }
        if transaction_type == "sale":
            payload.update({
                "customer_public_id": str(self.customer.public_id),
                "employee_public_id": str(self.seller_employee.public_id),
            })
            user = self.cashier
        else:
            payload["supplier_public_id"] = str(self.supplier.public_id)
            user = self.owner

        client = APIClient()
        client.force_authenticate(user=user)
        response = client.post("/api/transactions/", payload, format="json")
        self.assertEqual(response.status_code, status.HTTP_201_CREATED, response.data)
        return response.data["public_id"]

    def test_adjustment_vs_sale_cancellation_preserves_both_deltas(self):
        transaction_public_id = self._create_transaction_for_cancellation("sale")

        response_status, response_data = (
            self._assert_request_waits_for_held_product_lock(
                held_quantity=1,
                request_worker=self._request_cancellation,
                request_args=(transaction_public_id,),
            )
        )

        self.assertEqual(
            response_status,
            status.HTTP_204_NO_CONTENT,
            response_data,
        )
        self.product.refresh_from_db()
        self.assertEqual(self.product.stock, 11)
        self.assertEqual(StockMovement.objects.filter(product=self.product).count(), 3)

    def test_adjustment_vs_purchase_cancellation_preserves_both_deltas(self):
        transaction_public_id = self._create_transaction_for_cancellation("purchase")

        response_status, response_data = (
            self._assert_request_waits_for_held_product_lock(
                held_quantity=-1,
                request_worker=self._request_cancellation,
                request_args=(transaction_public_id,),
            )
        )

        self.assertEqual(
            response_status,
            status.HTTP_204_NO_CONTENT,
            response_data,
        )
        self.product.refresh_from_db()
        self.assertEqual(self.product.stock, 9)
        self.assertEqual(StockMovement.objects.filter(product=self.product).count(), 3)

    def test_status_committed_while_waiting_is_rechecked_after_lock(self):
        lock_held = Event()
        release_lock = Event()
        request_started = Event()

        def deactivate_product():
            close_old_connections()
            try:
                from django.db import transaction as db_tx

                with db_tx.atomic():
                    product = Product.objects.select_for_update(of=("self",)).get(
                        pk=self.product.pk
                    )
                    product.status = type(self.inactive_status).objects.get(
                        pk=self.inactive_status.pk
                    )
                    product.save(update_fields=["status", "updated_at"])
                    lock_held.set()
                    if not release_lock.wait(timeout=10):
                        raise AssertionError("Product lock was not released")
            finally:
                close_old_connections()

        with ThreadPoolExecutor(max_workers=2) as executor:
            first = executor.submit(deactivate_product)
            self.assertTrue(lock_held.wait(timeout=10))
            second = executor.submit(
                self._request_adjustment,
                1,
                request_started,
            )
            self.assertTrue(request_started.wait(timeout=10))
            with self.assertRaises(FutureTimeoutError):
                second.result(timeout=0.25)
            release_lock.set()
            first.result(timeout=10)
            response_status, response_data = second.result(timeout=10)

        self.assertEqual(response_status, status.HTTP_400_BAD_REQUEST)
        self.assertIn("product", response_data)
        self.product.refresh_from_db()
        self.assertEqual(self.product.stock, 10)
        self.assertFalse(StockMovement.objects.exists())
