import ast
import os
from pathlib import Path
import subprocess
import sys

from django.test import SimpleTestCase
from django.urls import resolve, reverse
from rest_framework import serializers, viewsets
from rest_framework.fields import empty

from core import serializers as legacy_serializers
from core import urls as core_urls
from core import views as legacy_views
from core.api.serializers.payment_methods import PaymentMethodSerializer
from core.api.serializers.status import EntityStatusSerializer
from core.api.views.base import BusinessScopedViewSet
from core.api.views.payment_methods import PaymentMethodViewSet
from core.api.views.statuses import EntityStatusViewSet
from core.mixins import SoftDeleteByStatusMixin
from core.models import Business, EntityStatus, PaymentMethod


EXAMPLE_PUBLIC_ID = "00000000-0000-0000-0000-000000000001"

CANONICAL_LOCATIONS = {
    "EntityStatusSerializer": "core/api/serializers/status.py",
    "PaymentMethodSerializer": "core/api/serializers/payment_methods.py",
    "EntityStatusViewSet": "core/api/views/statuses.py",
    "PaymentMethodViewSet": "core/api/views/payment_methods.py",
}

IMPORT_ORDERS = (
    (
        "core.api.serializers.fields",
        "core.api.serializers.status",
        "core.api.serializers.payment_methods",
        "core.api.views.statuses",
        "core.api.views.payment_methods",
        "core.serializers",
        "core.views",
        "core.urls",
    ),
    (
        "core.serializers",
        "core.views",
        "core.api.serializers.fields",
        "core.api.serializers.status",
        "core.api.serializers.payment_methods",
        "core.api.views.statuses",
        "core.api.views.payment_methods",
        "core.urls",
    ),
    (
        "core.urls",
        "core.views",
        "core.serializers",
        "core.api.views.statuses",
        "core.api.views.payment_methods",
        "core.api.serializers.fields",
        "core.api.serializers.status",
        "core.api.serializers.payment_methods",
    ),
)


class PhaseFourBStructuralCompatibilityTests(SimpleTestCase):
    def test_legacy_exports_are_canonical_objects(self):
        self.assertIs(
            legacy_serializers.EntityStatusSerializer,
            EntityStatusSerializer,
        )
        self.assertIs(
            legacy_serializers.PaymentMethodSerializer,
            PaymentMethodSerializer,
        )
        self.assertIs(
            legacy_views.EntityStatusViewSet,
            EntityStatusViewSet,
        )
        self.assertIs(
            legacy_views.PaymentMethodViewSet,
            PaymentMethodViewSet,
        )

    def test_classes_have_one_canonical_definition(self):
        project_root = Path(__file__).resolve().parents[2]
        definitions = {name: [] for name in CANONICAL_LOCATIONS}

        production_modules = (
            path
            for path in (project_root / "core").rglob("*.py")
            if "tests" not in path.relative_to(project_root / "core").parts
        )
        for module_path in production_modules:
            relative_path = module_path.relative_to(project_root).as_posix()
            module = ast.parse(module_path.read_text(encoding="utf-8"))
            for node in module.body:
                if isinstance(node, ast.ClassDef) and node.name in definitions:
                    definitions[node.name].append(relative_path)

        self.assertEqual(
            definitions,
            {name: [path] for name, path in CANONICAL_LOCATIONS.items()},
        )

    def test_mro_and_serializers_are_preserved(self):
        self.assertEqual(
            EntityStatusSerializer.__bases__,
            (serializers.ModelSerializer,),
        )
        self.assertEqual(
            PaymentMethodSerializer.__bases__,
            (
                legacy_serializers.DefaultActiveStatusMixin,
                serializers.ModelSerializer,
            ),
        )
        self.assertEqual(
            EntityStatusViewSet.__bases__,
            (viewsets.ReadOnlyModelViewSet,),
        )
        self.assertEqual(
            PaymentMethodViewSet.__bases__,
            (SoftDeleteByStatusMixin, BusinessScopedViewSet),
        )
        self.assertIs(
            EntityStatusViewSet.serializer_class,
            EntityStatusSerializer,
        )
        self.assertIs(
            PaymentMethodViewSet.serializer_class,
            PaymentMethodSerializer,
        )

        for viewset, actions, serializer_class in (
            (
                EntityStatusViewSet,
                ("list", "retrieve"),
                EntityStatusSerializer,
            ),
            (
                PaymentMethodViewSet,
                (
                    "list",
                    "retrieve",
                    "create",
                    "update",
                    "partial_update",
                    "destroy",
                ),
                PaymentMethodSerializer,
            ),
        ):
            for action in actions:
                with self.subTest(viewset=viewset.__name__, action=action):
                    view = viewset()
                    view.action = action
                    self.assertIs(
                        view.get_serializer_class(),
                        serializer_class,
                    )

    def test_entity_status_serializer_field_contract(self):
        self.assertIs(EntityStatusSerializer.Meta.model, EntityStatus)
        self.assertEqual(
            EntityStatusSerializer.Meta.fields,
            ("public_id", "name"),
        )
        self.assertEqual(
            EntityStatusSerializer.Meta.read_only_fields,
            ("public_id",),
        )

        fields = EntityStatusSerializer().fields
        self.assertEqual(tuple(fields), ("public_id", "name"))
        self.assertNotIn("description", fields)

        public_id = fields["public_id"]
        self.assertIs(type(public_id), serializers.UUIDField)
        self.assertEqual(public_id.source, "public_id")
        self.assertFalse(public_id.required)
        self.assertTrue(public_id.read_only)
        self.assertFalse(public_id.write_only)
        self.assertFalse(public_id.allow_null)
        self.assertIs(public_id.default, empty)
        self.assertEqual(public_id.label, "Public id")
        self.assertIsNone(public_id.help_text)

        name = fields["name"]
        self.assertIs(type(name), serializers.CharField)
        self.assertEqual(name.source, "name")
        self.assertTrue(name.required)
        self.assertFalse(name.read_only)
        self.assertFalse(name.write_only)
        self.assertFalse(name.allow_null)
        self.assertIs(name.default, empty)
        self.assertEqual(name.label, "Name")
        self.assertIsNone(name.help_text)
        self.assertEqual(name.max_length, 50)
        self.assertTrue(name.trim_whitespace)

    def test_payment_method_serializer_field_contract(self):
        self.assertIs(PaymentMethodSerializer.Meta.model, PaymentMethod)
        self.assertEqual(
            PaymentMethodSerializer.Meta.fields,
            (
                "public_id",
                "business_public_id",
                "name",
                "method_type",
                "status_public_id",
                "status_name",
            ),
        )
        self.assertEqual(
            PaymentMethodSerializer.Meta.read_only_fields,
            ("public_id",),
        )

        fields = PaymentMethodSerializer().fields
        self.assertEqual(
            tuple(fields),
            PaymentMethodSerializer.Meta.fields,
        )

        expected_fields = {
            "public_id": (
                serializers.UUIDField,
                "public_id",
                False,
                True,
                False,
                False,
                "Public id",
            ),
            "business_public_id": (
                serializers.SlugRelatedField,
                "business",
                True,
                False,
                False,
                False,
                "Business public id",
            ),
            "name": (
                serializers.CharField,
                "name",
                True,
                False,
                False,
                False,
                "Name",
            ),
            "method_type": (
                serializers.ChoiceField,
                "method_type",
                True,
                False,
                False,
                False,
                "Method type",
            ),
            "status_public_id": (
                serializers.SlugRelatedField,
                "status",
                False,
                False,
                False,
                False,
                "Status public id",
            ),
            "status_name": (
                serializers.CharField,
                "status.name",
                False,
                True,
                False,
                False,
                "Status name",
            ),
        }
        for field_name, expected in expected_fields.items():
            with self.subTest(field=field_name):
                field = fields[field_name]
                (
                    field_type,
                    source,
                    required,
                    read_only,
                    write_only,
                    allow_null,
                    label,
                ) = expected
                self.assertIs(type(field), field_type)
                self.assertEqual(field.source, source)
                self.assertEqual(field.required, required)
                self.assertEqual(field.read_only, read_only)
                self.assertEqual(field.write_only, write_only)
                self.assertEqual(field.allow_null, allow_null)
                self.assertIs(field.default, empty)
                self.assertEqual(field.label, label)
                self.assertIsNone(field.help_text)

        business_public_id = fields["business_public_id"]
        self.assertEqual(business_public_id.slug_field, "public_id")
        self.assertIs(business_public_id.queryset.model, Business)

        status_public_id = fields["status_public_id"]
        self.assertEqual(status_public_id.slug_field, "public_id")
        self.assertIs(status_public_id.queryset.model, EntityStatus)

        self.assertEqual(fields["name"].max_length, 100)
        self.assertTrue(fields["name"].trim_whitespace)
        self.assertTrue(fields["status_name"].trim_whitespace)

    def test_payment_method_type_validation_contract(self):
        method_type = PaymentMethodSerializer().fields["method_type"]
        self.assertTrue(method_type.required)
        self.assertFalse(method_type.read_only)
        self.assertFalse(method_type.write_only)
        self.assertFalse(method_type.allow_null)
        self.assertEqual(
            tuple(method_type.choices.items()),
            tuple(PaymentMethod.METHOD_TYPES),
        )
        self.assertIn(PaymentMethod.TYPE_OTHER, method_type.choices)

        instance = PaymentMethod(
            name="Otro",
            method_type=PaymentMethod.TYPE_OTHER,
        )
        creation = PaymentMethodSerializer(data={})
        full_update = PaymentMethodSerializer(instance, data={})
        partial_update = PaymentMethodSerializer(
            instance,
            data={},
            partial=True,
        )
        invalid_partial_update = PaymentMethodSerializer(
            instance,
            data={"method_type": "invalid"},
            partial=True,
        )

        self.assertFalse(creation.is_valid())
        self.assertEqual(
            creation.errors["method_type"][0].code,
            "required",
        )
        self.assertFalse(full_update.is_valid())
        self.assertEqual(
            full_update.errors["method_type"][0].code,
            "required",
        )
        self.assertTrue(partial_update.is_valid())
        self.assertNotIn("method_type", partial_update.validated_data)
        self.assertEqual(instance.method_type, PaymentMethod.TYPE_OTHER)
        self.assertFalse(invalid_partial_update.is_valid())
        self.assertEqual(
            invalid_partial_update.errors["method_type"][0].code,
            "invalid_choice",
        )

    def test_router_and_urls_are_preserved(self):
        registrations = tuple(
            (prefix, viewset, basename)
            for prefix, viewset, basename in core_urls.router.registry
            if prefix in ("statuses", "payment-methods")
        )
        self.assertEqual(
            registrations,
            (
                ("statuses", EntityStatusViewSet, "entity-status"),
                (
                    "payment-methods",
                    PaymentMethodViewSet,
                    "payment-method",
                ),
            ),
        )

        cases = (
            (
                "entity-status-list",
                None,
                "/api/statuses/",
                EntityStatusViewSet,
            ),
            (
                "entity-status-detail",
                {"public_id": EXAMPLE_PUBLIC_ID},
                f"/api/statuses/{EXAMPLE_PUBLIC_ID}/",
                EntityStatusViewSet,
            ),
            (
                "payment-method-list",
                None,
                "/api/payment-methods/",
                PaymentMethodViewSet,
            ),
            (
                "payment-method-detail",
                {"public_id": EXAMPLE_PUBLIC_ID},
                f"/api/payment-methods/{EXAMPLE_PUBLIC_ID}/",
                PaymentMethodViewSet,
            ),
        )
        for url_name, kwargs, expected_path, expected_viewset in cases:
            with self.subTest(url_name=url_name):
                path = reverse(url_name, kwargs=kwargs)
                self.assertEqual(path, expected_path)
                match = resolve(path)
                self.assertEqual(match.url_name, url_name)
                self.assertIs(match.func.cls, expected_viewset)

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

import core.serializers as legacy_serializers
import core.urls as core_urls
import core.views as legacy_views
from core.api.serializers.payment_methods import PaymentMethodSerializer
from core.api.serializers.status import EntityStatusSerializer
from core.api.views.base import BusinessScopedViewSet
from core.api.views.payment_methods import PaymentMethodViewSet
from core.api.views.statuses import EntityStatusViewSet
from core.mixins import SoftDeleteByStatusMixin
from rest_framework import viewsets

assert legacy_serializers.EntityStatusSerializer is EntityStatusSerializer
assert legacy_serializers.PaymentMethodSerializer is PaymentMethodSerializer
assert legacy_views.EntityStatusViewSet is EntityStatusViewSet
assert legacy_views.PaymentMethodViewSet is PaymentMethodViewSet
assert EntityStatusViewSet.__bases__ == (viewsets.ReadOnlyModelViewSet,)
assert PaymentMethodViewSet.__bases__ == (
    SoftDeleteByStatusMixin,
    BusinessScopedViewSet,
)
registrations = [
    (prefix, viewset, basename)
    for prefix, viewset, basename in core_urls.router.registry
    if prefix in ("statuses", "payment-methods")
]
assert registrations == [
    ("statuses", EntityStatusViewSet, "entity-status"),
    ("payment-methods", PaymentMethodViewSet, "payment-method"),
]
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
