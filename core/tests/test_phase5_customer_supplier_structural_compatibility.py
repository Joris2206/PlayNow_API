import ast
import os
from pathlib import Path
import subprocess
import sys
from unittest.mock import patch

from django.test import SimpleTestCase
from django.urls import resolve, reverse
from drf_spectacular.generators import SchemaGenerator
from rest_framework import serializers
from rest_framework.fields import empty
from rest_framework.filters import OrderingFilter
from rest_framework.permissions import IsAuthenticated

from core import serializers as legacy_serializers
from core import urls as core_urls
from core import views as legacy_views
from core.api.serializers.customers import CustomerSerializer
from core.api.serializers.status import DefaultActiveStatusMixin
from core.api.serializers.suppliers import SupplierSerializer
from core.api.views.base import BusinessScopedViewSet
from core.api.views.customers import CustomerViewSet
from core.api.views.suppliers import SupplierViewSet
from core.filters import ConfiguredSearchFilter, PublicIdFilterBackend
from core.mixins import SoftDeleteByStatusMixin
from core.models import (
    Business,
    BusinessMembership,
    Customer,
    EntityStatus,
    Supplier,
)
from core.pagination import StandardResultsSetPagination
from core.permissions import IsOwnerOrBusinessOwner


EXAMPLE_PUBLIC_ID = "00000000-0000-0000-0000-000000000001"

CANONICAL_LOCATIONS = {
    "CustomerSerializer": "core/api/serializers/customers.py",
    "SupplierSerializer": "core/api/serializers/suppliers.py",
    "CustomerViewSet": "core/api/views/customers.py",
    "SupplierViewSet": "core/api/views/suppliers.py",
}

IMPORT_ORDERS = (
    (
        "core.api.serializers.customers",
        "core.api.serializers.suppliers",
        "core.api.views.customers",
        "core.api.views.suppliers",
        "core.serializers",
        "core.views",
        "core.urls",
    ),
    (
        "core.serializers",
        "core.views",
        "core.api.serializers.customers",
        "core.api.serializers.suppliers",
        "core.api.views.customers",
        "core.api.views.suppliers",
        "core.urls",
    ),
    (
        "core.urls",
        "core.views",
        "core.serializers",
        "core.api.views.customers",
        "core.api.views.suppliers",
        "core.api.serializers.customers",
        "core.api.serializers.suppliers",
    ),
)


class PhaseFiveCustomerSupplierStructuralCompatibilityTests(SimpleTestCase):
    def test_legacy_exports_are_canonical_objects(self):
        self.assertIs(
            legacy_serializers.CustomerSerializer,
            CustomerSerializer,
        )
        self.assertIs(
            legacy_serializers.SupplierSerializer,
            SupplierSerializer,
        )
        self.assertIs(legacy_views.CustomerViewSet, CustomerViewSet)
        self.assertIs(legacy_views.SupplierViewSet, SupplierViewSet)

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

    def test_canonical_modules_do_not_import_legacy_facades(self):
        project_root = Path(__file__).resolve().parents[2]
        forbidden_imports = []

        for relative_path in CANONICAL_LOCATIONS.values():
            module = ast.parse(
                (project_root / relative_path).read_text(encoding="utf-8")
            )
            for node in ast.walk(module):
                if (
                    isinstance(node, ast.ImportFrom)
                    and node.module in {"core.serializers", "core.views"}
                ):
                    forbidden_imports.append((relative_path, node.module))
                elif isinstance(node, ast.Import):
                    for alias in node.names:
                        if alias.name in {"core.serializers", "core.views"}:
                            forbidden_imports.append(
                                (relative_path, alias.name)
                            )

        self.assertEqual(forbidden_imports, [])

    def test_serializer_mro_and_default_status_are_preserved(self):
        for serializer_class in (CustomerSerializer, SupplierSerializer):
            with self.subTest(serializer=serializer_class.__name__):
                self.assertEqual(
                    serializer_class.__bases__,
                    (DefaultActiveStatusMixin, serializers.ModelSerializer),
                )

                active_status = object()
                validated_data = {}
                with (
                    patch(
                        "core.api.serializers.status.get_active_status",
                        return_value=active_status,
                    ),
                    patch.object(
                        serializers.ModelSerializer,
                        "create",
                        return_value="created",
                    ) as model_create,
                ):
                    result = serializer_class().create(validated_data)

                self.assertEqual(result, "created")
                self.assertIs(validated_data["status"], active_status)
                model_create.assert_called_once_with(validated_data)

    def test_serializer_field_contracts_are_preserved(self):
        contracts = (
            (
                CustomerSerializer,
                Customer,
                (
                    "public_id",
                    "business_public_id",
                    "full_name",
                    "phone",
                    "email",
                    "status_public_id",
                    "status_name",
                    "created_at",
                    "updated_at",
                ),
                "full_name",
            ),
            (
                SupplierSerializer,
                Supplier,
                (
                    "public_id",
                    "business_public_id",
                    "name",
                    "phone",
                    "email",
                    "status_public_id",
                    "status_name",
                    "created_at",
                    "updated_at",
                ),
                "name",
            ),
        )
        read_only_fields = ("public_id", "created_at", "updated_at")

        for serializer_class, model, field_names, identity_name in contracts:
            with self.subTest(serializer=serializer_class.__name__):
                self.assertIs(serializer_class.Meta.model, model)
                self.assertEqual(serializer_class.Meta.fields, field_names)
                self.assertEqual(
                    serializer_class.Meta.read_only_fields,
                    read_only_fields,
                )

                fields = serializer_class().fields
                self.assertEqual(tuple(fields), field_names)

                expected_types = {
                    "public_id": serializers.UUIDField,
                    "business_public_id": serializers.SlugRelatedField,
                    identity_name: serializers.CharField,
                    "phone": serializers.CharField,
                    "email": serializers.EmailField,
                    "status_public_id": serializers.SlugRelatedField,
                    "status_name": serializers.CharField,
                    "created_at": serializers.DateTimeField,
                    "updated_at": serializers.DateTimeField,
                }
                expected_required = {
                    "public_id": False,
                    "business_public_id": True,
                    identity_name: True,
                    "phone": True,
                    "email": False,
                    "status_public_id": False,
                    "status_name": False,
                    "created_at": False,
                    "updated_at": False,
                }
                expected_labels = {
                    "public_id": "Public id",
                    "business_public_id": "Business public id",
                    identity_name: (
                        "Full name" if identity_name == "full_name" else "Name"
                    ),
                    "phone": "Phone",
                    "email": "Email",
                    "status_public_id": "Status public id",
                    "status_name": "Status name",
                    "created_at": "Created at",
                    "updated_at": "Updated at",
                }

                for field_name, field in fields.items():
                    with self.subTest(field=field_name):
                        self.assertIs(type(field), expected_types[field_name])
                        self.assertEqual(
                            field.required,
                            expected_required[field_name],
                        )
                        self.assertEqual(
                            field.read_only,
                            field_name in {
                                "public_id",
                                "status_name",
                                "created_at",
                                "updated_at",
                            },
                        )
                        self.assertFalse(field.write_only)
                        self.assertFalse(field.allow_null)
                        self.assertIs(field.default, empty)
                        self.assertEqual(field.label, expected_labels[field_name])
                        self.assertIsNone(field.help_text)

                business = fields["business_public_id"]
                self.assertEqual(business.source, "business")
                self.assertEqual(business.slug_field, "public_id")
                self.assertIs(business.queryset.model, Business)

                status = fields["status_public_id"]
                self.assertEqual(status.source, "status")
                self.assertEqual(status.slug_field, "public_id")
                self.assertIs(status.queryset.model, EntityStatus)

                self.assertEqual(fields["status_name"].source, "status.name")
                self.assertEqual(fields[identity_name].source, identity_name)
                self.assertEqual(fields[identity_name].max_length, 255)
                self.assertTrue(fields[identity_name].trim_whitespace)
                self.assertEqual(fields["phone"].max_length, 50)
                self.assertTrue(fields["phone"].trim_whitespace)
                self.assertEqual(fields["email"].max_length, 254)
                self.assertTrue(fields["email"].trim_whitespace)

    def test_viewset_contracts_are_preserved(self):
        contracts = (
            (
                CustomerViewSet,
                CustomerSerializer,
                Customer,
                [
                    BusinessMembership.ROLE_OWNER,
                    BusinessMembership.ROLE_ADMIN,
                    BusinessMembership.ROLE_CASHIER,
                    BusinessMembership.ROLE_SELLER,
                    BusinessMembership.ROLE_VIEWER,
                ],
                [
                    BusinessMembership.ROLE_OWNER,
                    BusinessMembership.ROLE_ADMIN,
                    BusinessMembership.ROLE_CASHIER,
                    BusinessMembership.ROLE_SELLER,
                ],
                ["full_name", "email", "phone"],
                ["full_name", "created_at", "updated_at"],
            ),
            (
                SupplierViewSet,
                SupplierSerializer,
                Supplier,
                [
                    BusinessMembership.ROLE_OWNER,
                    BusinessMembership.ROLE_ADMIN,
                    BusinessMembership.ROLE_INVENTORY,
                    BusinessMembership.ROLE_VIEWER,
                ],
                [
                    BusinessMembership.ROLE_OWNER,
                    BusinessMembership.ROLE_ADMIN,
                    BusinessMembership.ROLE_INVENTORY,
                ],
                ["name", "email", "phone"],
                ["name", "created_at", "updated_at"],
            ),
        )

        for (
            viewset,
            serializer_class,
            model,
            read_roles,
            write_roles,
            search_fields,
            ordering_fields,
        ) in contracts:
            with self.subTest(viewset=viewset.__name__):
                self.assertEqual(
                    viewset.__bases__,
                    (SoftDeleteByStatusMixin, BusinessScopedViewSet),
                )
                self.assertIs(viewset.serializer_class, serializer_class)
                self.assertIs(viewset.queryset.model, model)
                self.assertEqual(
                    viewset.queryset.query.select_related,
                    {"business": {}, "status": {}},
                )
                self.assertEqual(
                    viewset.permission_classes,
                    [IsAuthenticated, IsOwnerOrBusinessOwner],
                )
                self.assertEqual(
                    viewset.filter_backends,
                    [
                        PublicIdFilterBackend,
                        ConfiguredSearchFilter,
                        OrderingFilter,
                    ],
                )
                self.assertEqual(viewset.read_allowed_roles, read_roles)
                self.assertEqual(viewset.create_allowed_roles, write_roles)
                self.assertEqual(viewset.update_allowed_roles, write_roles)
                self.assertEqual(
                    viewset.destroy_allowed_roles,
                    [
                        BusinessMembership.ROLE_OWNER,
                        BusinessMembership.ROLE_ADMIN,
                    ],
                )
                self.assertEqual(
                    viewset.public_id_filter_fields,
                    {"status_public_id": "status__public_id"},
                )
                self.assertEqual(viewset.search_fields, search_fields)
                self.assertEqual(viewset.ordering_fields, ordering_fields)
                self.assertEqual(viewset.ordering, ["-created_at"])
                self.assertIs(
                    viewset.pagination_class,
                    StandardResultsSetPagination,
                )
                self.assertEqual(viewset.lookup_field, "public_id")
                self.assertEqual(viewset.lookup_url_kwarg, "public_id")
                self.assertEqual(viewset.business_lookup, "business")

    def test_router_and_urls_are_preserved(self):
        registrations = tuple(
            (prefix, viewset, basename)
            for prefix, viewset, basename in core_urls.router.registry
            if prefix in {"customers", "suppliers"}
        )
        self.assertEqual(
            registrations,
            (
                ("customers", CustomerViewSet, "customer"),
                ("suppliers", SupplierViewSet, "supplier"),
            ),
        )

        cases = (
            (
                "customer-list",
                None,
                "/api/customers/",
                CustomerViewSet,
                {"get": "list", "post": "create"},
            ),
            (
                "customer-detail",
                {"public_id": EXAMPLE_PUBLIC_ID},
                f"/api/customers/{EXAMPLE_PUBLIC_ID}/",
                CustomerViewSet,
                {
                    "get": "retrieve",
                    "put": "update",
                    "patch": "partial_update",
                    "delete": "destroy",
                },
            ),
            (
                "supplier-list",
                None,
                "/api/suppliers/",
                SupplierViewSet,
                {"get": "list", "post": "create"},
            ),
            (
                "supplier-detail",
                {"public_id": EXAMPLE_PUBLIC_ID},
                f"/api/suppliers/{EXAMPLE_PUBLIC_ID}/",
                SupplierViewSet,
                {
                    "get": "retrieve",
                    "put": "update",
                    "patch": "partial_update",
                    "delete": "destroy",
                },
            ),
        )
        for url_name, kwargs, expected_path, viewset, actions in cases:
            with self.subTest(url_name=url_name):
                path = reverse(url_name, kwargs=kwargs)
                self.assertEqual(path, expected_path)
                match = resolve(path)
                self.assertEqual(match.url_name, url_name)
                self.assertIs(match.func.cls, viewset)
                self.assertEqual(
                    {
                        method: action
                        for method, action in match.func.actions.items()
                        if method != "head"
                    },
                    actions,
                )
                if "head" in match.func.actions:
                    self.assertEqual(
                        match.func.actions["head"],
                        actions["get"],
                    )

    def test_openapi_contract_is_preserved(self):
        schema = SchemaGenerator().get_schema(request=None, public=True)
        security = [{"BearerAuth": []}, {"BearerAuth": []}]

        for resource, component, identity_field in (
            ("customers", "Customer", "full_name"),
            ("suppliers", "Supplier", "name"),
        ):
            with self.subTest(resource=resource):
                collection = schema["paths"][f"/api/{resource}/"]
                detail = schema["paths"][
                    f"/api/{resource}/{{public_id}}/"
                ]
                self.assertEqual(set(collection), {"get", "post"})
                self.assertEqual(
                    set(detail),
                    {"get", "put", "patch", "delete"},
                )

                singular = resource.removesuffix("s")
                expected_operation_ids = {
                    "get": f"api_{resource}_list",
                    "post": f"api_{resource}_create",
                }
                for method, operation_id in expected_operation_ids.items():
                    self.assertEqual(
                        collection[method]["operationId"],
                        operation_id,
                    )
                    self.assertEqual(collection[method]["security"], security)
                for method, suffix in (
                    ("get", "retrieve"),
                    ("put", "update"),
                    ("patch", "partial_update"),
                    ("delete", "destroy"),
                ):
                    self.assertEqual(
                        detail[method]["operationId"],
                        f"api_{resource}_{suffix}",
                    )
                    self.assertEqual(detail[method]["security"], security)

                list_parameters = {
                    parameter["name"]: parameter
                    for parameter in collection["get"]["parameters"]
                }
                self.assertEqual(
                    set(list_parameters),
                    {
                        "business_public_id",
                        "ordering",
                        "page",
                        "page_size",
                        "search",
                        "status_public_id",
                    },
                )
                self.assertTrue(
                    list_parameters["business_public_id"]["required"]
                )
                self.assertEqual(
                    list_parameters["business_public_id"]["schema"],
                    {"type": "string", "format": "uuid"},
                )
                self.assertEqual(
                    list_parameters["status_public_id"]["schema"],
                    {"type": "string", "format": "uuid"},
                )

                response = schema["components"]["schemas"][component]
                request = schema["components"]["schemas"][
                    f"{component}Request"
                ]
                patched = schema["components"]["schemas"][
                    f"Patched{component}Request"
                ]
                self.assertEqual(
                    set(response["properties"]),
                    {
                        "public_id",
                        "business_public_id",
                        identity_field,
                        "phone",
                        "email",
                        "status_public_id",
                        "status_name",
                        "created_at",
                        "updated_at",
                    },
                )
                self.assertEqual(
                    request["required"],
                    ["business_public_id", identity_field, "phone"],
                )
                self.assertNotIn("required", patched)
                for field_name in (
                    "public_id",
                    "business_public_id",
                    "status_public_id",
                ):
                    self.assertEqual(
                        response["properties"][field_name].get("format"),
                        "uuid",
                    )
                for field_name in (
                    "public_id",
                    "status_name",
                    "created_at",
                    "updated_at",
                ):
                    self.assertTrue(
                        response["properties"][field_name]["readOnly"]
                    )
                for properties in (
                    response["properties"],
                    request["properties"],
                    patched["properties"],
                ):
                    for field in properties.values():
                        self.assertNotIn("nullable", field)

                example_name = (
                    "RegistrarCliente"
                    if singular == "customer"
                    else "RegistrarProveedor"
                )
                self.assertIn(
                    example_name,
                    collection["post"]["requestBody"]["content"]
                    ["application/json"]["examples"],
                )

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
from core.api.serializers.customers import CustomerSerializer
from core.api.serializers.status import DefaultActiveStatusMixin
from core.api.serializers.suppliers import SupplierSerializer
from core.api.views.base import BusinessScopedViewSet
from core.api.views.customers import CustomerViewSet
from core.api.views.suppliers import SupplierViewSet
from core.mixins import SoftDeleteByStatusMixin
from rest_framework import serializers

assert legacy_serializers.CustomerSerializer is CustomerSerializer
assert legacy_serializers.SupplierSerializer is SupplierSerializer
assert legacy_views.CustomerViewSet is CustomerViewSet
assert legacy_views.SupplierViewSet is SupplierViewSet
assert CustomerSerializer.__bases__ == (
    DefaultActiveStatusMixin,
    serializers.ModelSerializer,
)
assert SupplierSerializer.__bases__ == (
    DefaultActiveStatusMixin,
    serializers.ModelSerializer,
)
assert CustomerViewSet.__bases__ == (
    SoftDeleteByStatusMixin,
    BusinessScopedViewSet,
)
assert SupplierViewSet.__bases__ == (
    SoftDeleteByStatusMixin,
    BusinessScopedViewSet,
)
registrations = [
    (prefix, viewset, basename)
    for prefix, viewset, basename in core_urls.router.registry
    if prefix in {{"customers", "suppliers"}}
]
assert registrations == [
    ("customers", CustomerViewSet, "customer"),
    ("suppliers", SupplierViewSet, "supplier"),
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
