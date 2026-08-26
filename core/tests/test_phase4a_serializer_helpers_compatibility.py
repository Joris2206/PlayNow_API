import ast
import os
from pathlib import Path
import subprocess
import sys

from django.test import SimpleTestCase
from rest_framework import serializers

from core import serializers as legacy_serializers
from core.api.serializers import fields as canonical_fields
from core.api.serializers import status as canonical_status
from core.models import Business


MIXIN_CONSUMERS = (
    "PaymentMethodSerializer",
    "BusinessSerializer",
    "ProductCategorySerializer",
    "ProductSerializer",
    "EmployeeSerializer",
    "CustomerSerializer",
    "SupplierSerializer",
)

IMPORT_ORDERS = (
    (
        "core.api.serializers.fields",
        "core.api.serializers.status",
        "core.serializers",
    ),
    (
        "core.serializers",
        "core.api.serializers.status",
        "core.api.serializers.fields",
    ),
    (
        "core.urls",
        "core.views",
        "core.serializers",
        "core.api.serializers.fields",
        "core.api.serializers.status",
    ),
)


class PhaseFourASerializerHelperCompatibilityTests(SimpleTestCase):
    def test_legacy_exports_are_canonical_objects(self):
        self.assertIs(
            legacy_serializers.public_id_field,
            canonical_fields.public_id_field,
        )
        self.assertIs(
            legacy_serializers.related_name_field,
            canonical_fields.related_name_field,
        )
        self.assertIs(
            legacy_serializers.get_active_status,
            canonical_status.get_active_status,
        )
        self.assertIs(
            legacy_serializers.DefaultActiveStatusMixin,
            canonical_status.DefaultActiveStatusMixin,
        )

    def test_helpers_have_one_canonical_definition(self):
        project_root = Path(__file__).resolve().parents[2]
        expected_locations = {
            "public_id_field": "core/api/serializers/fields.py",
            "related_name_field": "core/api/serializers/fields.py",
            "get_active_status": "core/api/serializers/status.py",
            "DefaultActiveStatusMixin": "core/api/serializers/status.py",
        }
        definitions = {name: [] for name in expected_locations}

        production_modules = (
            path
            for path in (project_root / "core").rglob("*.py")
            if "tests" not in path.relative_to(project_root / "core").parts
        )
        for module_path in production_modules:
            relative_path = module_path.relative_to(project_root).as_posix()
            module = ast.parse(
                module_path.read_text(encoding="utf-8")
            )
            for node in module.body:
                if (
                    isinstance(node, (ast.FunctionDef, ast.ClassDef))
                    and node.name in definitions
                ):
                    definitions[node.name].append(relative_path)

        self.assertEqual(
            definitions,
            {name: [path] for name, path in expected_locations.items()},
        )

    def test_consumers_inherit_from_canonical_mixin(self):
        for name in MIXIN_CONSUMERS:
            with self.subTest(serializer=name):
                serializer_class = getattr(legacy_serializers, name)
                self.assertIs(
                    serializer_class.__bases__[0],
                    canonical_status.DefaultActiveStatusMixin,
                )
                self.assertIs(
                    serializer_class.__bases__[1],
                    serializers.ModelSerializer,
                )

    def test_public_id_field_contract_is_preserved(self):
        default_field = canonical_fields.public_id_field(Business)
        configured_field = canonical_fields.public_id_field(
            Business,
            source="business",
            required=False,
            allow_null=True,
        )

        self.assertIsInstance(default_field, serializers.SlugRelatedField)
        self.assertEqual(default_field.slug_field, "public_id")
        self.assertIs(default_field.queryset.model, Business)
        self.assertIsNone(default_field.source)
        self.assertTrue(default_field.required)
        self.assertFalse(default_field.allow_null)
        self.assertFalse(default_field.read_only)

        self.assertIsInstance(configured_field, serializers.SlugRelatedField)
        self.assertEqual(configured_field.slug_field, "public_id")
        self.assertIs(configured_field.queryset.model, Business)
        self.assertEqual(configured_field.source, "business")
        self.assertFalse(configured_field.required)
        self.assertTrue(configured_field.allow_null)
        self.assertFalse(configured_field.read_only)
        self.assertEqual(
            configured_field.error_messages,
            default_field.error_messages,
        )

    def test_related_name_field_contract_is_preserved(self):
        default_field = canonical_fields.related_name_field("status.name")
        nullable_field = canonical_fields.related_name_field(
            "status.name",
            allow_null=True,
        )

        self.assertIsInstance(default_field, serializers.CharField)
        self.assertEqual(default_field.source, "status.name")
        self.assertTrue(default_field.read_only)
        self.assertFalse(default_field.required)
        self.assertFalse(default_field.allow_null)

        self.assertIsInstance(nullable_field, serializers.CharField)
        self.assertEqual(nullable_field.source, "status.name")
        self.assertTrue(nullable_field.read_only)
        self.assertFalse(nullable_field.required)
        self.assertTrue(nullable_field.allow_null)
        self.assertEqual(nullable_field.error_messages, default_field.error_messages)

    def test_import_orders_in_isolated_processes(self):
        project_root = Path(__file__).resolve().parents[2]
        environment = os.environ.copy()
        environment["DJANGO_SETTINGS_MODULE"] = "playnow.settings"

        for modules in IMPORT_ORDERS:
            with self.subTest(modules=modules):
                script = f"""
import importlib
import django

django.setup()
for module_name in {modules!r}:
    importlib.import_module(module_name)

import core.serializers as legacy
from core.api.serializers import fields, status

assert legacy.public_id_field is fields.public_id_field
assert legacy.related_name_field is fields.related_name_field
assert legacy.get_active_status is status.get_active_status
assert legacy.DefaultActiveStatusMixin is status.DefaultActiveStatusMixin
for serializer_name in {MIXIN_CONSUMERS!r}:
    serializer_class = getattr(legacy, serializer_name)
    assert status.DefaultActiveStatusMixin in serializer_class.__mro__, serializer_name
"""
                result = subprocess.run(
                    [sys.executable, "-c", script],
                    cwd=project_root,
                    env=environment,
                    capture_output=True,
                    text=True,
                    check=False,
                )
                self.assertEqual(
                    result.returncode,
                    0,
                    msg=(
                        f"Import order {modules!r} failed.\n"
                        f"stdout:\n{result.stdout}\n"
                        f"stderr:\n{result.stderr}"
                    ),
                )
