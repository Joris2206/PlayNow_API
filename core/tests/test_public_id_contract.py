import inspect

from django.test import SimpleTestCase
from drf_spectacular.generators import SchemaGenerator
from rest_framework import serializers as drf_serializers

import core.serializers as core_serializers
from core.serializers import (
    CashRegisterSerializer,
    CommissionSettlementSerializer,
    MonthlyClosureSerializer,
    ProductCategorySerializer,
    ProductSerializer,
)


class PublicIdSerializerContractTests(SimpleTestCase):
    @staticmethod
    def _iter_fields(serializer, prefix=""):
        for field_name, field in serializer.fields.items():
            field_path = (
                f"{prefix}.{field_name}"
                if prefix
                else field_name
            )

            yield field_path, field

            nested = (
                field.child
                if isinstance(field, drf_serializers.ListSerializer)
                else field
            )

            if isinstance(nested, drf_serializers.BaseSerializer):
                yield from (
                    PublicIdSerializerContractTests
                    ._iter_fields(nested, field_path)
                )

    def test_public_id_relations_use_explicit_public_names(self):
        for serializer_name, serializer_class in inspect.getmembers(
            core_serializers,
            inspect.isclass,
        ):
            if not issubclass(
                serializer_class,
                drf_serializers.BaseSerializer,
            ):
                continue

            if serializer_class.__module__ != core_serializers.__name__:
                continue

            serializer = serializer_class()

            for field_name, field in self._iter_fields(serializer):
                with self.subTest(
                    serializer=serializer_name,
                    field=field_name,
                ):
                    self.assertNotIsInstance(
                        field,
                        drf_serializers.PrimaryKeyRelatedField,
                    )

                    if not (
                        isinstance(field, drf_serializers.SlugRelatedField)
                        and field.slug_field == "public_id"
                    ):
                        continue

                    self.assertTrue(
                        field_name.endswith("_public_id"),
                        msg=(
                            f"{serializer_name}.{field_name} expone "
                            "un public_id sin nombre explícito."
                        ),
                    )

                    relation_name = field.source.split(".")[-1]

                    self.assertEqual(
                        field_name.split(".")[-1],
                        f"{relation_name}_public_id",
                        msg=(
                            f"{serializer_name}.{field_name} apunta "
                            f"a source={field.source!r}, que no "
                            "coincide con su nombre público."
                        ),
                    )

    def test_model_public_ids_are_read_only(self):
        for serializer_name, serializer_class in inspect.getmembers(
            core_serializers,
            inspect.isclass,
        ):
            if not issubclass(
                serializer_class,
                drf_serializers.ModelSerializer,
            ):
                continue

            if serializer_class.__module__ != core_serializers.__name__:
                continue

            public_id = serializer_class().fields.get("public_id")

            if public_id is not None:
                with self.subTest(serializer=serializer_name):
                    self.assertTrue(public_id.read_only)

    def test_legacy_product_relation_names_are_not_exposed(self):
        fields = ProductSerializer().fields

        self.assertNotIn("business", fields)
        self.assertNotIn("category", fields)
        self.assertNotIn("status", fields)
        self.assertIn("business_public_id", fields)
        self.assertIn("category_public_id", fields)
        self.assertIn("status_public_id", fields)

    def test_entity_status_and_choice_status_remain_distinct(self):
        self.assertIn(
            "status_public_id",
            ProductCategorySerializer().fields,
        )

        for serializer_class in (
            CashRegisterSerializer,
            CommissionSettlementSerializer,
            MonthlyClosureSerializer,
        ):
            fields = serializer_class().fields

            with self.subTest(serializer=serializer_class.__name__):
                self.assertIn("status", fields)
                self.assertNotIn("status_public_id", fields)


class PublicIdOpenApiContractTests(SimpleTestCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.schema = SchemaGenerator().get_schema(
            request=None,
            public=True,
        )

    def test_uuid_properties_use_public_id_names(self):
        schemas = self.schema["components"]["schemas"]

        for schema_name, schema in schemas.items():
            for field_name, field_schema in schema.get(
                "properties",
                {},
            ).items():
                if field_schema.get("format") != "uuid":
                    continue

                with self.subTest(
                    schema=schema_name,
                    field=field_name,
                ):
                    self.assertTrue(
                        field_name == "public_id"
                        or field_name.endswith("_public_id"),
                    )

    def test_product_request_and_response_use_normalized_relations(self):
        schemas = self.schema["components"]["schemas"]

        for schema_name in ("Product", "ProductRequest"):
            fields = schemas[schema_name]["properties"]

            with self.subTest(schema=schema_name):
                self.assertIn("business_public_id", fields)
                self.assertIn("category_public_id", fields)
                self.assertIn("status_public_id", fields)
                self.assertNotIn("business", fields)
                self.assertNotIn("category", fields)
                self.assertNotIn("status", fields)

    def test_business_public_id_is_required_only_on_scoped_lists(self):
        for path, path_item in self.schema["paths"].items():
            for method, operation in path_item.items():
                if method not in {
                    "get",
                    "post",
                    "put",
                    "patch",
                    "delete",
                }:
                    continue

                parameters = [
                    parameter
                    for parameter in operation.get("parameters", [])
                    if parameter["name"] == "business_public_id"
                ]

                for parameter in parameters:
                    with self.subTest(path=path, method=method):
                        self.assertEqual(method, "get")
                        self.assertNotIn("{public_id}", path)
                        self.assertTrue(parameter.get("required"))
