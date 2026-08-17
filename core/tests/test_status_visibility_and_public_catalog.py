from decimal import Decimal

from drf_spectacular.generators import SchemaGenerator
from rest_framework import status

from core.tests.base import BusinessIsolationTestCase
from core.tests.factories import (
    create_category,
    create_product,
    create_status,
)
from core.tests.helpers import (
    get_public_ids,
    get_response_results,
)


class AdministrativeStatusVisibilityTests(
    BusinessIsolationTestCase,
):
    @classmethod
    def setUpTestData(cls):
        super().setUpTestData()
        cls.inactive_status = create_status("Inactivo")
        cls.deleted_status = create_status("Eliminado")
        cls.cancelled_status = create_status("Cancelado")

        cls.products = [
            create_product(
                business=cls.business_a,
                status=current_status,
                title=f"Producto {current_status.name}",
            )
            for current_status in (
                cls.active_status,
                cls.inactive_status,
                cls.deleted_status,
                cls.void_status,
                cls.cancelled_status,
            )
        ]
        cls.foreign_deleted_product = create_product(
            business=cls.business_b,
            status=cls.deleted_status,
            title="Producto eliminado ajeno",
        )

    def test_admin_list_returns_every_status_from_its_business(self):
        response = self.client.get(
            "/api/products/",
            {
                "business_public_id": str(
                    self.business_a.public_id
                ),
            },
        )

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        returned_ids = get_public_ids(response)

        self.assertEqual(
            returned_ids,
            {
                str(product.public_id)
                for product in self.products
            },
        )
        self.assertNotIn(
            str(self.foreign_deleted_product.public_id),
            returned_ids,
        )

    def test_admin_status_filter_operates_on_complete_queryset(self):
        response = self.client.get(
            "/api/products/",
            {
                "business_public_id": str(
                    self.business_a.public_id
                ),
                "status_public_id": str(
                    self.deleted_status.public_id
                ),
            },
        )

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(
            get_public_ids(response),
            {str(self.products[2].public_id)},
        )

    def test_admin_can_retrieve_and_reactivate_deleted_product(self):
        product = self.products[2]
        endpoint = f"/api/products/{product.public_id}/"

        retrieve_response = self.client.get(endpoint)
        self.assertEqual(
            retrieve_response.status_code,
            status.HTTP_200_OK,
        )

        patch_response = self.client.patch(
            endpoint,
            {
                "status_public_id": str(
                    self.active_status.public_id
                ),
            },
            format="json",
        )
        self.assertEqual(
            patch_response.status_code,
            status.HTTP_200_OK,
        )
        product.refresh_from_db()
        self.assertEqual(product.status, self.active_status)

    def test_product_delete_is_deterministic_and_visible_afterward(self):
        product = self.products[0]
        endpoint = f"/api/products/{product.public_id}/"

        first_response = self.client.delete(endpoint)
        self.assertEqual(
            first_response.status_code,
            status.HTTP_204_NO_CONTENT,
        )
        product.refresh_from_db()
        self.assertEqual(product.status, self.deleted_status)

        list_response = self.client.get(
            "/api/products/",
            {
                "business_public_id": str(
                    self.business_a.public_id
                ),
            },
        )
        self.assertIn(
            str(product.public_id),
            get_public_ids(list_response),
        )

        second_response = self.client.delete(endpoint)
        self.assertEqual(
            second_response.status_code,
            status.HTTP_409_CONFLICT,
        )
        product.refresh_from_db()
        self.assertEqual(product.status, self.deleted_status)

    def test_deleted_category_can_be_reactivated_with_patch(self):
        category = create_category(
            business=self.business_a,
            status=self.active_status,
            name="Categoría reactivable",
        )
        endpoint = f"/api/categories/{category.public_id}/"

        self.assertEqual(
            self.client.delete(endpoint).status_code,
            status.HTTP_204_NO_CONTENT,
        )
        category.refresh_from_db()
        self.assertEqual(category.status, self.deleted_status)

        response = self.client.patch(
            endpoint,
            {
                "status_public_id": str(
                    self.active_status.public_id
                ),
            },
            format="json",
        )

        self.assertEqual(
            response.status_code,
            status.HTTP_200_OK,
        )
        category.refresh_from_db()
        self.assertEqual(category.status, self.active_status)

    def test_admin_does_not_hide_product_when_is_visible_is_false(self):
        product = self.products[0]
        product.is_visible = False
        product.save(update_fields=["is_visible"])

        response = self.client.get(
            "/api/products/",
            {
                "business_public_id": str(
                    self.business_a.public_id
                ),
            },
        )

        self.assertIn(
            str(product.public_id),
            get_public_ids(response),
        )

    def test_foreign_deleted_product_cannot_be_read_or_modified(self):
        endpoint = (
            "/api/products/"
            f"{self.foreign_deleted_product.public_id}/"
        )

        self.assertEqual(
            self.client.get(endpoint).status_code,
            status.HTTP_404_NOT_FOUND,
        )
        self.assertEqual(
            self.client.patch(
                endpoint,
                {"title": "Manipulado"},
                format="json",
            ).status_code,
            status.HTTP_404_NOT_FOUND,
        )
        self.assertEqual(
            self.client.delete(endpoint).status_code,
            status.HTTP_404_NOT_FOUND,
        )


class PublicCatalogTests(BusinessIsolationTestCase):
    @classmethod
    def setUpTestData(cls):
        super().setUpTestData()
        cls.inactive_status = create_status("Inactivo")
        cls.deleted_status = create_status("Eliminado")

        cls.category = create_category(
            business=cls.business_a,
            status=cls.active_status,
            name="Calzado",
        )
        cls.public_product = create_product(
            business=cls.business_a,
            category=cls.category,
            status=cls.active_status,
            title="Producto público",
            base_cost=Decimal("55.00"),
        )
        cls.inactive_product = create_product(
            business=cls.business_a,
            status=cls.inactive_status,
            title="Producto inactivo",
        )
        cls.deleted_product = create_product(
            business=cls.business_a,
            status=cls.deleted_status,
            title="Producto eliminado",
        )
        cls.hidden_product = create_product(
            business=cls.business_a,
            status=cls.active_status,
            title="Producto oculto",
        )
        cls.hidden_product.is_visible = False
        cls.hidden_product.save(update_fields=["is_visible"])
        cls.foreign_product = create_product(
            business=cls.business_b,
            status=cls.active_status,
            title="Producto público ajeno",
        )

    def setUp(self):
        self.client.force_authenticate(user=None)

    def business_params(self, **extra):
        return {
            "business_public_id": str(
                self.business_a.public_id
            ),
            **extra,
        }

    def test_public_product_list_is_anonymous_and_restrictive(self):
        response = self.client.get(
            "/api/public/products/",
            self.business_params(
                status_public_id=str(
                    self.deleted_status.public_id
                ),
                include_inactive="true",
                is_visible="false",
            ),
        )

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(
            get_public_ids(response),
            {str(self.public_product.public_id)},
        )
        item = get_response_results(response)[0]
        self.assertNotIn("base_cost", item)
        self.assertNotIn("status_public_id", item)
        self.assertNotIn("status_name", item)
        self.assertNotIn("created_at", item)

    def test_public_product_detail_requires_matching_business(self):
        endpoint = (
            "/api/public/products/"
            f"{self.public_product.public_id}/"
        )

        self.assertEqual(
            self.client.get(
                endpoint,
                self.business_params(),
            ).status_code,
            status.HTTP_200_OK,
        )
        self.assertEqual(
            self.client.get(
                endpoint,
                {
                    "business_public_id": str(
                        self.business_b.public_id
                    ),
                },
            ).status_code,
            status.HTTP_404_NOT_FOUND,
        )

        for product in (
            self.inactive_product,
            self.deleted_product,
            self.hidden_product,
        ):
            with self.subTest(product=product.title):
                response = self.client.get(
                    f"/api/public/products/{product.public_id}/",
                    self.business_params(),
                )
                self.assertEqual(
                    response.status_code,
                    status.HTTP_404_NOT_FOUND,
                )

    def test_public_catalog_is_read_only(self):
        collection_endpoints = (
            "/api/public/products/",
            "/api/public/categories/",
        )

        for endpoint in collection_endpoints:
            with self.subTest(endpoint=endpoint, method="post"):
                response = self.client.post(
                    endpoint,
                    self.business_params(),
                    format="json",
                )
                self.assertEqual(
                    response.status_code,
                    status.HTTP_405_METHOD_NOT_ALLOWED,
                )

        detail_endpoint = (
            "/api/public/products/"
            f"{self.public_product.public_id}/"
            f"?business_public_id={self.business_a.public_id}"
        )
        for method in ("put", "patch", "delete"):
            with self.subTest(method=method):
                response = getattr(self.client, method)(
                    detail_endpoint,
                    {},
                    format="json",
                )
                self.assertEqual(
                    response.status_code,
                    status.HTTP_405_METHOD_NOT_ALLOWED,
                )

    def test_public_categories_are_scoped_to_business(self):
        category_response = self.client.get(
            "/api/public/categories/",
            self.business_params(),
        )
        self.assertEqual(
            get_public_ids(category_response),
            {str(self.category.public_id)},
        )

    def test_variant_endpoints_are_removed(self):
        for endpoint in (
            "/api/variant-types/",
            "/api/variants/",
            "/api/public/variant-types/",
            "/api/public/variants/",
        ):
            with self.subTest(endpoint=endpoint):
                self.assertEqual(
                    self.client.get(endpoint).status_code,
                    status.HTTP_404_NOT_FOUND,
                )


class StatusAndPublicOpenApiTests(BusinessIsolationTestCase):
    def test_schema_exposes_admin_filters_and_read_only_public_paths(self):
        schema = SchemaGenerator().get_schema(
            request=None,
            public=True,
        )

        admin_paths = (
            "/api/businesses/",
            "/api/categories/",
            "/api/products/",
            "/api/employees/",
            "/api/customers/",
            "/api/suppliers/",
            "/api/payment-methods/",
            "/api/transactions/",
            "/api/notifications/",
            "/api/reminders/",
            "/api/budgets/",
            "/api/goals/",
            "/api/goal-progress/",
        )

        for path in admin_paths:
            with self.subTest(path=path):
                parameter_names = {
                    parameter["name"]
                    for parameter in schema["paths"][path]["get"]
                    .get("parameters", [])
                }
                self.assertIn("status_public_id", parameter_names)

        for resource in (
            "products",
            "categories",
        ):
            collection = schema["paths"][
                f"/api/public/{resource}/"
            ]
            detail = schema["paths"][
                f"/api/public/{resource}/{{public_id}}/"
            ]
            self.assertEqual(set(collection), {"get"})
            self.assertEqual(set(detail), {"get"})

        for path in (
            "/api/variant-types/",
            "/api/variants/",
            "/api/public/variant-types/",
            "/api/public/variants/",
        ):
            self.assertNotIn(path, schema["paths"])

        public_product_fields = schema["components"]["schemas"][
            "PublicProduct"
        ]["properties"]
        self.assertNotIn("base_cost", public_product_fields)
        self.assertNotIn("status_public_id", public_product_fields)
