import uuid

from django.test import SimpleTestCase
from drf_spectacular.generators import SchemaGenerator
from rest_framework import status

from core.tests.base import BusinessIsolationTestCase
from core.tests.factories import (
    create_product,
    create_variant,
    create_variant_type,
)
from core.tests.helpers import get_public_ids


class VariantParentFilterTests(BusinessIsolationTestCase):
    @classmethod
    def setUpTestData(cls):
        super().setUpTestData()

        cls.product_a = create_product(
            business=cls.business_a,
            status=cls.active_status,
            title="Producto A",
        )
        cls.other_product_a = create_product(
            business=cls.business_a,
            status=cls.active_status,
            title="Otro producto A",
        )
        cls.product_b = create_product(
            business=cls.business_b,
            status=cls.active_status,
            title="Producto B",
        )

        cls.color_type = create_variant_type(
            product=cls.product_a,
            status=cls.active_status,
            name="Color",
        )
        cls.size_type = create_variant_type(
            product=cls.product_a,
            status=cls.active_status,
            name="Talla",
        )
        cls.material_type = create_variant_type(
            product=cls.other_product_a,
            status=cls.active_status,
            name="Material",
        )
        cls.foreign_type = create_variant_type(
            product=cls.product_b,
            status=cls.active_status,
            name="Tipo extranjero",
        )

        cls.red_variant = create_variant(
            variant_type=cls.color_type,
            status=cls.active_status,
            label="Rojo",
        )
        cls.blue_variant = create_variant(
            variant_type=cls.color_type,
            status=cls.active_status,
            label="Azul",
        )
        cls.small_variant = create_variant(
            variant_type=cls.size_type,
            status=cls.active_status,
            label="S",
        )
        cls.cotton_variant = create_variant(
            variant_type=cls.material_type,
            status=cls.active_status,
            label="Algodón",
        )
        cls.foreign_variant = create_variant(
            variant_type=cls.foreign_type,
            status=cls.active_status,
            label="Extranjera",
        )

    def _get_ids(self, endpoint, **parent_filters):
        response = self.client.get(
            endpoint,
            {
                "business_public_id": str(
                    self.business_a.public_id
                ),
                **parent_filters,
            },
        )
        self.assertEqual(
            response.status_code,
            status.HTTP_200_OK,
            msg=response.data,
        )
        return get_public_ids(response)

    def test_variant_types_filter_by_product_within_business(self):
        returned_ids = self._get_ids(
            "/api/variant-types/",
            product_public_id=str(self.product_a.public_id),
        )

        self.assertEqual(
            returned_ids,
            {
                str(self.color_type.public_id),
                str(self.size_type.public_id),
            },
        )
        self.assertNotIn(
            str(self.material_type.public_id),
            returned_ids,
        )

    def test_variant_type_parent_filter_does_not_leak_or_match_unknown(self):
        for product_public_id in (
            self.product_b.public_id,
            uuid.uuid4(),
        ):
            with self.subTest(product_public_id=product_public_id):
                self.assertEqual(
                    self._get_ids(
                        "/api/variant-types/",
                        product_public_id=str(product_public_id),
                    ),
                    set(),
                )

    def test_variant_type_parent_filter_rejects_invalid_uuid(self):
        response = self.client.get(
            "/api/variant-types/",
            {
                "business_public_id": str(
                    self.business_a.public_id
                ),
                "product_public_id": "invalid-uuid",
            },
        )

        self.assertEqual(
            response.status_code,
            status.HTTP_400_BAD_REQUEST,
        )

    def test_variants_filter_by_variant_type(self):
        returned_ids = self._get_ids(
            "/api/variants/",
            variant_type_public_id=str(
                self.color_type.public_id
            ),
        )

        self.assertEqual(
            returned_ids,
            {
                str(self.red_variant.public_id),
                str(self.blue_variant.public_id),
            },
        )

    def test_variants_filter_by_product_across_variant_types(self):
        returned_ids = self._get_ids(
            "/api/variants/",
            product_public_id=str(self.product_a.public_id),
        )

        self.assertEqual(
            returned_ids,
            {
                str(self.red_variant.public_id),
                str(self.blue_variant.public_id),
                str(self.small_variant.public_id),
            },
        )
        self.assertNotIn(
            str(self.cotton_variant.public_id),
            returned_ids,
        )

    def test_variant_parent_filters_combine_with_and_semantics(self):
        compatible_ids = self._get_ids(
            "/api/variants/",
            variant_type_public_id=str(
                self.color_type.public_id
            ),
            product_public_id=str(self.product_a.public_id),
        )
        incompatible_ids = self._get_ids(
            "/api/variants/",
            variant_type_public_id=str(
                self.color_type.public_id
            ),
            product_public_id=str(
                self.other_product_a.public_id
            ),
        )

        self.assertEqual(
            compatible_ids,
            {
                str(self.red_variant.public_id),
                str(self.blue_variant.public_id),
            },
        )
        self.assertEqual(incompatible_ids, set())

    def test_variant_parent_filters_preserve_business_isolation(self):
        for parent_filter in (
            {
                "variant_type_public_id": str(
                    self.foreign_type.public_id
                ),
            },
            {
                "product_public_id": str(
                    self.product_b.public_id
                ),
            },
            {
                "variant_type_public_id": str(uuid.uuid4()),
            },
            {
                "product_public_id": str(uuid.uuid4()),
            },
        ):
            with self.subTest(parent_filter=parent_filter):
                self.assertEqual(
                    self._get_ids(
                        "/api/variants/",
                        **parent_filter,
                    ),
                    set(),
                )

    def test_variant_parent_filters_reject_invalid_uuids(self):
        for parameter in (
            "variant_type_public_id",
            "product_public_id",
        ):
            with self.subTest(parameter=parameter):
                response = self.client.get(
                    "/api/variants/",
                    {
                        "business_public_id": str(
                            self.business_a.public_id
                        ),
                        parameter: "invalid-uuid",
                    },
                )
                self.assertEqual(
                    response.status_code,
                    status.HTTP_400_BAD_REQUEST,
                )


class VariantParentFilterOpenApiTests(SimpleTestCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.schema = SchemaGenerator().get_schema(
            request=None,
            public=True,
        )

    def _parameter_names(self, path, method="get"):
        return {
            parameter["name"]
            for parameter in self.schema["paths"][path][method].get(
                "parameters",
                [],
            )
        }

    def test_variant_type_list_documents_parent_filter(self):
        self.assertTrue(
            {
                "business_public_id",
                "product_public_id",
                "search",
                "status_public_id",
                "ordering",
                "page",
                "page_size",
            }.issubset(
                self._parameter_names("/api/variant-types/")
            )
        )

    def test_variant_list_documents_parent_filters(self):
        self.assertTrue(
            {
                "business_public_id",
                "variant_type_public_id",
                "product_public_id",
                "search",
                "status_public_id",
                "ordering",
                "page",
                "page_size",
            }.issubset(
                self._parameter_names("/api/variants/")
            )
        )

    def test_parent_filters_are_not_documented_on_write_operations(self):
        parent_parameters = {
            "product_public_id",
            "variant_type_public_id",
        }

        for path in (
            "/api/variant-types/",
            "/api/variants/",
        ):
            with self.subTest(path=path, method="post"):
                self.assertTrue(
                    parent_parameters.isdisjoint(
                        self._parameter_names(path, "post")
                    )
                )

            detail_path = f"{path}{{public_id}}/"
            for method in ("put", "patch"):
                with self.subTest(
                    path=detail_path,
                    method=method,
                ):
                    self.assertTrue(
                        parent_parameters.isdisjoint(
                            self._parameter_names(
                                detail_path,
                                method,
                            )
                        )
                    )
