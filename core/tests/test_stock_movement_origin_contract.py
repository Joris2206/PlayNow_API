from django.db import connection
from django.test.utils import CaptureQueriesContext
from drf_spectacular.generators import SchemaGenerator
from rest_framework import status

from core.models import StockMovement
from core.serializers import StockMovementSerializer
from core.tests.base import BusinessIsolationTestCase
from core.tests.factories import (
    create_product,
    create_stock_movement,
    create_transaction,
    create_transaction_detail,
)
from core.tests.helpers import get_response_results
from core.views import StockMovementViewSet


class StockMovementOriginContractTests(BusinessIsolationTestCase):
    @classmethod
    def setUpTestData(cls):
        super().setUpTestData()
        cls.product = create_product(
            business=cls.business_a,
            status=cls.active_status,
            stock=100,
        )
        cls.other_product = create_product(
            business=cls.business_a,
            status=cls.active_status,
            stock=20,
        )
        cls.sale = create_transaction(
            business=cls.business_a,
            created_by=cls.user_a,
            status=cls.active_status,
            transaction_type="sale",
        )
        cls.purchase = create_transaction(
            business=cls.business_a,
            created_by=cls.user_a,
            status=cls.active_status,
            transaction_type="purchase",
        )
        cls.sale_detail = create_transaction_detail(
            transaction=cls.sale,
            product=cls.product,
        )
        cls.purchase_detail = create_transaction_detail(
            transaction=cls.purchase,
            product=cls.product,
        )

        cls.sale_base = create_stock_movement(
            product=cls.product,
            transaction=cls.sale,
            transaction_detail=cls.sale_detail,
            created_by=cls.user_a,
            movement_type="sale",
            quantity=-1,
            note="arbitrary text with purchase 00000000-0000-0000-0000-000000000000",
        )
        cls.purchase_base = create_stock_movement(
            product=cls.product,
            transaction=cls.purchase,
            transaction_detail=cls.purchase_detail,
            created_by=cls.user_a,
            movement_type="entry",
            quantity=1,
            note="",
        )
        cls.sale_reversal = create_stock_movement(
            product=cls.product,
            transaction=cls.sale,
            created_by=cls.user_a,
            movement_type="adjustment",
            quantity=1,
            note="not a neutralization note",
        )
        cls.purchase_reversal = create_stock_movement(
            product=cls.product,
            transaction=cls.purchase,
            created_by=cls.user_a,
            movement_type="adjustment",
            quantity=-1,
            note="Auto neutralize unrelated-value",
        )
        cls.positive_adjustment = create_stock_movement(
            product=cls.product,
            created_by=cls.user_a,
            movement_type="adjustment",
            quantity=2,
            note="Auto base from sale ignored",
        )
        cls.negative_adjustment = create_stock_movement(
            product=cls.product,
            created_by=None,
            movement_type="adjustment",
            quantity=-2,
            note="",
        )
        cls.incoherent = create_stock_movement(
            product=cls.product,
            transaction=cls.sale,
            transaction_detail=cls.sale_detail,
            created_by=cls.user_a,
            movement_type="entry",
            quantity=1,
        )
        cls.detail_without_transaction = create_stock_movement(
            product=cls.product,
            transaction_detail=cls.sale_detail,
            created_by=cls.user_a,
            movement_type="adjustment",
            quantity=1,
            note="Auto neutralize 11111111-1111-1111-1111-111111111111",
        )
        cls.other_product_movement = create_stock_movement(
            product=cls.other_product,
            created_by=cls.user_a,
            movement_type="adjustment",
            quantity=1,
        )

    def _list(self, **params):
        query = {
            "business_public_id": str(self.business_a.public_id),
            "page_size": 100,
        }
        query.update(params)
        return self.client.get("/api/stock-movements/", query)

    def test_structured_origin_classification_ignores_note(self):
        response = self._list()
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        rows = {
            str(row["public_id"]): row
            for row in get_response_results(response)
        }

        expected = {
            self.sale_base: ("sale", False),
            self.purchase_base: ("purchase", False),
            self.sale_reversal: ("sale", True),
            self.purchase_reversal: ("purchase", True),
            self.positive_adjustment: ("adjustment", None),
            self.negative_adjustment: ("adjustment", None),
            self.incoherent: ("unknown", None),
        }
        for movement, origin in expected.items():
            with self.subTest(movement=movement.public_id):
                row = rows[str(movement.public_id)]
                self.assertEqual(
                    (row["origin_type"], row["is_reversal"]),
                    origin,
                )
                self.assertEqual(row["note"], movement.note)

    def test_detail_without_transaction_is_unknown(self):
        response = self.client.get(
            "/api/stock-movements/"
            f"{self.detail_without_transaction.public_id}/"
        )
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertIsNone(response.data["transaction_public_id"])
        self.assertEqual(
            str(response.data["transaction_detail_public_id"]),
            str(self.sale_detail.public_id),
        )
        self.assertEqual(response.data["origin_type"], "unknown")
        self.assertIsNone(response.data["is_reversal"])

    def test_physical_transaction_deletion_is_conservative(self):
        transaction = create_transaction(
            business=self.business_a,
            created_by=self.user_a,
            status=self.active_status,
            transaction_type="sale",
        )
        detail = create_transaction_detail(
            transaction=transaction,
            product=self.product,
        )
        create_stock_movement(
            product=self.product,
            transaction=transaction,
            transaction_detail=detail,
            created_by=self.user_a,
            movement_type="sale",
            quantity=-1,
        )
        reversal = create_stock_movement(
            product=self.product,
            transaction=transaction,
            created_by=self.user_a,
            movement_type="adjustment",
            quantity=1,
            note="preserved arbitrary note",
        )

        transaction.delete()
        reversal.refresh_from_db()
        self.assertIsNone(reversal.transaction_id)
        self.assertIsNone(reversal.transaction_detail_id)

        response = self.client.get(
            f"/api/stock-movements/{reversal.public_id}/"
        )
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertIsNone(response.data["transaction_public_id"])
        self.assertIsNone(response.data["transaction_detail_public_id"])
        self.assertEqual(response.data["origin_type"], "adjustment")
        self.assertIsNone(response.data["is_reversal"])
        self.assertEqual(response.data["note"], "preserved arbitrary note")

    def test_list_and_retrieve_preserve_existing_contract(self):
        existing_fields = [
            "public_id",
            "product_public_id",
            "product_name",
            "transaction_public_id",
            "transaction_detail_public_id",
            "note",
            "type",
            "quantity",
            "created_by_email",
            "created_at",
            "updated_at",
        ]
        self.assertEqual(
            list(StockMovementSerializer().fields),
            existing_fields + ["origin_type", "is_reversal"],
        )

        list_response = self._list()
        self.assertEqual(list_response.status_code, status.HTTP_200_OK)
        self.assertEqual(list_response.data["page_size"], 100)
        row = get_response_results(list_response)[0]
        self.assertEqual(set(existing_fields), set(row) - {"origin_type", "is_reversal"})

        detail_response = self.client.get(
            f"/api/stock-movements/{self.sale_base.public_id}/"
        )
        self.assertEqual(detail_response.status_code, status.HTTP_200_OK)
        self.assertEqual(detail_response.data["origin_type"], "sale")
        self.assertIs(detail_response.data["is_reversal"], False)
        self.assertEqual(
            str(detail_response.data["transaction_public_id"]),
            str(self.sale.public_id),
        )
        self.assertEqual(
            str(detail_response.data["transaction_detail_public_id"]),
            str(self.sale_detail.public_id),
        )

    def test_product_and_type_filters_return_exact_movements(self):
        product_response = self._list(
            product_public_id=str(self.product.public_id),
            ordering="id",
        )
        self.assertEqual(product_response.status_code, status.HTTP_200_OK)
        expected_product_ids = {
            str(movement.public_id)
            for movement in (
                self.sale_base,
                self.purchase_base,
                self.sale_reversal,
                self.purchase_reversal,
                self.positive_adjustment,
                self.negative_adjustment,
                self.incoherent,
                self.detail_without_transaction,
            )
        }
        returned_product_ids = {
            str(row["public_id"])
            for row in get_response_results(product_response)
        }
        self.assertEqual(returned_product_ids, expected_product_ids)
        self.assertNotIn(
            str(self.other_product_movement.public_id),
            returned_product_ids,
        )

        type_response = self._list(type="sale", ordering="id")
        self.assertEqual(type_response.status_code, status.HTTP_200_OK)
        type_rows = get_response_results(type_response)
        self.assertEqual(
            [str(row["public_id"]) for row in type_rows],
            [str(self.sale_base.public_id)],
        )
        self.assertTrue(all(row["type"] == "sale" for row in type_rows))

    def test_ordering_and_pagination_return_stable_exact_pages(self):
        movements = [
            self.sale_base,
            self.purchase_base,
            self.sale_reversal,
            self.purchase_reversal,
            self.positive_adjustment,
            self.negative_adjustment,
            self.incoherent,
            self.detail_without_transaction,
            self.other_product_movement,
        ]
        expected_ids = [
            str(movement.public_id)
            for movement in sorted(movements, key=lambda movement: movement.pk)
        ]

        ordered_response = self._list(ordering="id")
        self.assertEqual(ordered_response.status_code, status.HTTP_200_OK)
        self.assertEqual(
            [
                str(row["public_id"])
                for row in get_response_results(ordered_response)
            ],
            expected_ids,
        )

        page_one = self.client.get(
            "/api/stock-movements/",
            {
                "business_public_id": str(self.business_a.public_id),
                "ordering": "id",
                "page_size": 3,
                "page": 1,
            },
        )
        page_two = self.client.get(
            "/api/stock-movements/",
            {
                "business_public_id": str(self.business_a.public_id),
                "ordering": "id",
                "page_size": 3,
                "page": 2,
            },
        )
        for page, number in ((page_one, 1), (page_two, 2)):
            self.assertEqual(page.status_code, status.HTTP_200_OK)
            self.assertEqual(page.data["count"], len(expected_ids))
            self.assertEqual(page.data["total_pages"], 3)
            self.assertEqual(page.data["current_page"], number)
            self.assertEqual(page.data["page_size"], 3)

        page_one_ids = [
            str(row["public_id"])
            for row in get_response_results(page_one)
        ]
        page_two_ids = [
            str(row["public_id"])
            for row in get_response_results(page_two)
        ]
        self.assertEqual(page_one_ids, expected_ids[:3])
        self.assertEqual(page_two_ids, expected_ids[3:6])
        self.assertFalse(set(page_one_ids) & set(page_two_ids))

    def test_relation_query_count_is_constant(self):
        query = {
            "business_public_id": str(self.business_a.public_id),
            "page_size": 1,
        }
        with CaptureQueriesContext(connection) as single_queries:
            single_response = self.client.get("/api/stock-movements/", query)
        self.assertEqual(single_response.status_code, status.HTTP_200_OK)

        query["page_size"] = 100
        with CaptureQueriesContext(connection) as multiple_queries:
            multiple_response = self.client.get("/api/stock-movements/", query)
        self.assertEqual(multiple_response.status_code, status.HTTP_200_OK)
        self.assertGreater(len(get_response_results(multiple_response)), 1)
        self.assertEqual(len(multiple_queries), len(single_queries))

    def test_existing_read_only_and_business_isolation_contract_remains(self):
        foreign_product = create_product(
            business=self.business_b,
            status=self.active_status,
        )
        foreign_movement = create_stock_movement(
            product=foreign_product,
            created_by=self.user_b,
            movement_type="adjustment",
            quantity=1,
        )
        returned_ids = {
            str(row["public_id"])
            for row in get_response_results(self._list())
        }
        self.assertNotIn(str(foreign_movement.public_id), returned_ids)

        endpoint = f"/api/stock-movements/{self.sale_base.public_id}/"
        self.assertEqual(self.client.post("/api/stock-movements/", {}).status_code, 405)
        self.assertEqual(self.client.put(endpoint, {}).status_code, 405)
        self.assertEqual(self.client.patch(endpoint, {}).status_code, 405)
        self.assertEqual(self.client.delete(endpoint).status_code, 405)


class StockMovementOriginOpenApiTests(BusinessIsolationTestCase):
    def test_openapi_documents_only_response_origin_fields(self):
        schema = SchemaGenerator().get_schema(request=None, public=True)
        component = schema["components"]["schemas"]["StockMovement"]
        properties = component["properties"]

        origin = properties["origin_type"]
        origin_component_name = origin["allOf"][0]["$ref"].rsplit("/", 1)[-1]
        origin_component = schema["components"]["schemas"][origin_component_name]
        self.assertEqual(origin_component["type"], "string")
        self.assertEqual(
            set(origin_component["enum"]),
            {"sale", "purchase", "adjustment", "unknown"},
        )
        self.assertIs(origin["readOnly"], True)

        reversal = properties["is_reversal"]
        self.assertEqual(reversal["type"], "boolean")
        self.assertIs(reversal["nullable"], True)
        self.assertIs(reversal["readOnly"], True)
        self.assertTrue({"origin_type", "is_reversal"}.issubset(component["required"]))

        paths = schema["paths"]
        self.assertEqual(
            paths["/api/stock-movements/"]["get"]["operationId"],
            "api_stock_movements_list",
        )
        self.assertEqual(
            paths["/api/stock-movements/{public_id}/"]["get"]["operationId"],
            "api_stock_movements_retrieve",
        )
        self.assertNotIn("post", paths["/api/stock-movements/"])
        self.assertFalse(
            {"put", "patch", "delete"}
            & set(paths["/api/stock-movements/{public_id}/"])
        )

    def test_viewset_keeps_expected_eager_loaded_relations(self):
        self.assertEqual(
            set(StockMovementViewSet.queryset.query.select_related),
            {
                "product",
                "transaction",
                "transaction_detail",
                "created_by",
            },
        )
        self.assertEqual(
            StockMovementViewSet.queryset.query.select_related["product"],
            {"business": {}},
        )
