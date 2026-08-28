import ast
import hashlib
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
from core.api.serializers.employees import (
    EmployeeSelectionSerializer,
    EmployeeSerializer,
)
from core.api.serializers.status import DefaultActiveStatusMixin
from core.api.views.base import BusinessScopedViewSet
from core.api.views.employees import EmployeeViewSet
from core.filters import ConfiguredSearchFilter, PublicIdFilterBackend
from core.mixins import SoftDeleteByStatusMixin
from core.models import Business, BusinessMembership, Employee, EntityStatus
from core.pagination import StandardResultsSetPagination
from core.permissions import IsOwnerOrBusinessOwner


EXAMPLE_PUBLIC_ID = "00000000-0000-0000-0000-000000000001"

CANONICAL_LOCATIONS = {
    "EmployeeSerializer": "core/api/serializers/employees.py",
    "EmployeeSelectionSerializer": "core/api/serializers/employees.py",
    "EmployeeViewSet": "core/api/views/employees.py",
}

AST_HASHES = {
    "EmployeeSerializer": (
        "c39a9167d4b45008f8f8f38f753dd2ad665ef79a40a232f31a713c475c400822"
    ),
    "EmployeeSelectionSerializer": (
        "17db4308f381f26c7d674655646d123b1e79538322e30073370f7037149c6db4"
    ),
    "EmployeeViewSet": (
        "5a6177268d13dde6f41274dbe3892b3ff84258c5e134a9dda7ffd820906a7587"
    ),
}

IMPORT_SCENARIOS = (
    (
        "canonical-first",
        """
import core.api.serializers.employees
import core.api.views.employees
import core.serializers
import core.views
import core.urls
""",
    ),
    (
        "legacy-first",
        """
import core.serializers
import core.views
import core.api.serializers.employees
import core.api.views.employees
import core.urls
""",
    ),
    (
        "URL-first",
        """
import core.urls
import core.views
import core.serializers
import core.api.views.employees
import core.api.serializers.employees
""",
    ),
    (
        "Business-first",
        """
from core.serializers import EmployeeAccessCreateSerializer, BusinessSerializer
from core.views import BusinessViewSet
import core.api.serializers.employees
import core.api.views.employees
import core.urls
""",
    ),
    (
        "Transaction-first",
        """
from core.serializers import TransactionSerializer
from core.views import TransactionViewSet
import core.api.serializers.employees
import core.api.views.employees
import core.urls
""",
    ),
    (
        "OpenAPI-first",
        """
from drf_spectacular.generators import SchemaGenerator
schema = SchemaGenerator().get_schema(request=None, public=True)
assert "EmployeeRead" in schema["components"]["schemas"]
import core.api.serializers.employees
import core.api.views.employees
import core.serializers
import core.views
import core.urls
""",
    ),
)


class PhaseSixEmployeeStructuralCompatibilityTests(SimpleTestCase):
    def test_legacy_exports_are_canonical_objects(self):
        self.assertIs(
            legacy_serializers.EmployeeSerializer,
            EmployeeSerializer,
        )
        self.assertIs(
            legacy_serializers.EmployeeSelectionSerializer,
            EmployeeSelectionSerializer,
        )
        self.assertIs(legacy_views.EmployeeViewSet, EmployeeViewSet)

    def test_classes_are_unique_and_ast_equivalent_to_baseline(self):
        project_root = Path(__file__).resolve().parents[2]
        definitions = {name: [] for name in CANONICAL_LOCATIONS}
        hashes = {}

        for module_path in (project_root / "core").rglob("*.py"):
            if "tests" in module_path.relative_to(project_root / "core").parts:
                continue

            relative_path = module_path.relative_to(project_root).as_posix()
            module = ast.parse(module_path.read_text(encoding="utf-8"))
            for node in ast.walk(module):
                if not isinstance(node, ast.ClassDef):
                    continue
                if node.name not in definitions:
                    continue

                definitions[node.name].append(relative_path)
                class_dump = ast.dump(node, include_attributes=False)
                hashes[node.name] = hashlib.sha256(
                    class_dump.encode("utf-8")
                ).hexdigest()

        self.assertEqual(
            definitions,
            {name: [path] for name, path in CANONICAL_LOCATIONS.items()},
        )
        self.assertEqual(hashes, AST_HASHES)

    def test_canonical_modules_have_only_forward_imports(self):
        project_root = Path(__file__).resolve().parents[2]
        violations = []

        for relative_path in set(CANONICAL_LOCATIONS.values()):
            module = ast.parse(
                (project_root / relative_path).read_text(encoding="utf-8")
            )
            for node in ast.walk(module):
                if isinstance(node, ast.ImportFrom):
                    if node.module in {"core.serializers", "core.views"}:
                        violations.append((relative_path, node.module))
                    if node.module == "core":
                        for alias in node.names:
                            if alias.name in {"serializers", "views"}:
                                violations.append(
                                    (relative_path, f"core.{alias.name}")
                                )
                    if any(alias.name == "*" for alias in node.names):
                        violations.append((relative_path, "wildcard"))
                elif isinstance(node, ast.Import):
                    for alias in node.names:
                        if alias.name in {"core.serializers", "core.views"}:
                            violations.append((relative_path, alias.name))

        self.assertEqual(violations, [])

    def test_serializer_contracts_are_preserved(self):
        self.assertEqual(
            EmployeeSerializer.__bases__,
            (DefaultActiveStatusMixin, serializers.ModelSerializer),
        )
        self.assertEqual(
            EmployeeSelectionSerializer.__bases__,
            (serializers.ModelSerializer,),
        )

        field_names = (
            "public_id",
            "business_public_id",
            "full_name",
            "phone",
            "email",
            "position",
            "status_public_id",
            "status_name",
            "created_at",
            "updated_at",
        )
        read_only_fields = ("public_id", "created_at", "updated_at")
        self.assertIs(EmployeeSerializer.Meta.model, Employee)
        self.assertEqual(EmployeeSerializer.Meta.fields, field_names)
        self.assertEqual(
            EmployeeSerializer.Meta.read_only_fields,
            read_only_fields,
        )

        fields = EmployeeSerializer().fields
        self.assertEqual(tuple(fields), field_names)
        expected_types = {
            "public_id": serializers.UUIDField,
            "business_public_id": serializers.SlugRelatedField,
            "full_name": serializers.CharField,
            "phone": serializers.CharField,
            "email": serializers.EmailField,
            "position": serializers.CharField,
            "status_public_id": serializers.SlugRelatedField,
            "status_name": serializers.CharField,
            "created_at": serializers.DateTimeField,
            "updated_at": serializers.DateTimeField,
        }
        required = {"business_public_id", "full_name", "position"}
        read_only = {"public_id", "status_name", "created_at", "updated_at"}
        allow_blank = {"phone", "email"}
        labels = {
            "public_id": "Public id",
            "business_public_id": "Business public id",
            "full_name": "Full name",
            "phone": "Phone",
            "email": "Email",
            "position": "Position",
            "status_public_id": "Status public id",
            "status_name": "Status name",
            "created_at": "Created at",
            "updated_at": "Updated at",
        }

        for field_name, field in fields.items():
            with self.subTest(field=field_name):
                self.assertIs(type(field), expected_types[field_name])
                self.assertEqual(field.required, field_name in required)
                self.assertEqual(field.read_only, field_name in read_only)
                self.assertFalse(field.write_only)
                self.assertFalse(field.allow_null)
                self.assertIs(field.default, empty)
                self.assertEqual(field.label, labels[field_name])
                self.assertIsNone(field.help_text)
                if hasattr(field, "allow_blank"):
                    self.assertEqual(
                        field.allow_blank,
                        field_name in allow_blank,
                    )

        business = fields["business_public_id"]
        self.assertEqual(business.source, "business")
        self.assertEqual(business.slug_field, "public_id")
        self.assertIs(business.queryset.model, Business)

        status = fields["status_public_id"]
        self.assertEqual(status.source, "status")
        self.assertEqual(status.slug_field, "public_id")
        self.assertIs(status.queryset.model, EntityStatus)
        self.assertEqual(fields["status_name"].source, "status.name")

        lengths = {
            "full_name": 200,
            "phone": 30,
            "email": 254,
            "position": 100,
        }
        for field_name, max_length in lengths.items():
            self.assertEqual(fields[field_name].max_length, max_length)
            self.assertTrue(fields[field_name].trim_whitespace)

        selection_names = ("public_id", "full_name", "position")
        self.assertIs(EmployeeSelectionSerializer.Meta.model, Employee)
        self.assertEqual(
            EmployeeSelectionSerializer.Meta.fields,
            selection_names,
        )
        self.assertEqual(
            EmployeeSelectionSerializer.Meta.read_only_fields,
            selection_names,
        )
        selection = EmployeeSelectionSerializer().fields
        self.assertEqual(tuple(selection), selection_names)
        self.assertIs(type(selection["public_id"]), serializers.UUIDField)
        for field_name, field in selection.items():
            with self.subTest(selection_field=field_name):
                self.assertTrue(field.read_only)
                self.assertFalse(field.required)
                self.assertFalse(field.write_only)
                self.assertFalse(field.allow_null)
                self.assertIs(field.default, empty)

    def test_default_status_and_model_serializer_update_are_preserved(self):
        active_status = object()
        supplied_status = object()

        for validated_data, expected_status in (
            ({}, active_status),
            ({"status": supplied_status}, supplied_status),
        ):
            with self.subTest(status_supplied=bool(validated_data)):
                data = dict(validated_data)
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
                    result = EmployeeSerializer().create(data)

                self.assertEqual(result, "created")
                self.assertIs(data["status"], expected_status)
                model_create.assert_called_once_with(data)

        self.assertIs(
            EmployeeSerializer.update,
            serializers.ModelSerializer.update,
        )

    def test_viewset_contract_and_dynamic_selection_are_preserved(self):
        self.assertEqual(
            EmployeeViewSet.__bases__,
            (SoftDeleteByStatusMixin, BusinessScopedViewSet),
        )
        self.assertIs(EmployeeViewSet.serializer_class, EmployeeSerializer)
        self.assertIs(EmployeeViewSet.queryset.model, Employee)
        self.assertEqual(
            EmployeeViewSet.queryset.query.select_related,
            {"business": {}, "status": {}},
        )
        self.assertEqual(
            EmployeeViewSet.permission_classes,
            [IsAuthenticated, IsOwnerOrBusinessOwner],
        )
        self.assertEqual(
            EmployeeViewSet.filter_backends,
            [PublicIdFilterBackend, ConfiguredSearchFilter, OrderingFilter],
        )
        self.assertEqual(
            EmployeeViewSet.read_allowed_roles,
            [
                BusinessMembership.ROLE_OWNER,
                BusinessMembership.ROLE_ADMIN,
                BusinessMembership.ROLE_CASHIER,
                BusinessMembership.ROLE_SELLER,
            ],
        )
        write_roles = [
            BusinessMembership.ROLE_OWNER,
            BusinessMembership.ROLE_ADMIN,
        ]
        self.assertEqual(EmployeeViewSet.create_allowed_roles, write_roles)
        self.assertEqual(EmployeeViewSet.update_allowed_roles, write_roles)
        self.assertEqual(EmployeeViewSet.destroy_allowed_roles, write_roles)
        self.assertEqual(
            EmployeeViewSet.public_id_filter_fields,
            {"status_public_id": "status__public_id"},
        )
        self.assertEqual(
            EmployeeViewSet.search_fields,
            ["full_name", "email", "phone"],
        )
        self.assertEqual(
            EmployeeViewSet.ordering_fields,
            ["full_name", "created_at", "updated_at"],
        )
        self.assertEqual(EmployeeViewSet.ordering, ["-created_at"])
        self.assertIs(
            EmployeeViewSet.pagination_class,
            StandardResultsSetPagination,
        )
        self.assertEqual(EmployeeViewSet.lookup_field, "public_id")
        self.assertEqual(EmployeeViewSet.lookup_url_kwarg, "public_id")
        self.assertEqual(EmployeeViewSet.business_lookup, "business")

        view = EmployeeViewSet()
        for action in ("list", "retrieve"):
            view.action = action
            for role, expected in (
                (BusinessMembership.ROLE_OWNER, EmployeeSerializer),
                (BusinessMembership.ROLE_ADMIN, EmployeeSerializer),
                (BusinessMembership.ROLE_CASHIER, EmployeeSelectionSerializer),
                (BusinessMembership.ROLE_SELLER, EmployeeSelectionSerializer),
                (BusinessMembership.ROLE_INVENTORY, EmployeeSerializer),
                (BusinessMembership.ROLE_VIEWER, EmployeeSerializer),
                (None, EmployeeSerializer),
            ):
                with self.subTest(action=action, role=role):
                    with patch.object(
                        view,
                        "_request_membership_role",
                        return_value=role,
                    ):
                        self.assertIs(view.get_serializer_class(), expected)

        for action in ("create", "update", "partial_update", "destroy"):
            view.action = action
            with patch.object(view, "_request_membership_role") as role_lookup:
                self.assertIs(view.get_serializer_class(), EmployeeSerializer)
                role_lookup.assert_not_called()

        for role, expected in (
            (BusinessMembership.ROLE_CASHIER, ["full_name", "position"]),
            (BusinessMembership.ROLE_SELLER, ["full_name", "position"]),
            (BusinessMembership.ROLE_OWNER, EmployeeViewSet.search_fields),
            (BusinessMembership.ROLE_ADMIN, EmployeeViewSet.search_fields),
            (None, EmployeeViewSet.search_fields),
        ):
            with self.subTest(search_role=role):
                with patch.object(
                    view,
                    "_request_membership_role",
                    return_value=role,
                ):
                    self.assertEqual(view.get_search_fields(), expected)

    def test_router_urls_and_business_access_route_are_preserved(self):
        registration = tuple(
            (prefix, viewset, basename)
            for prefix, viewset, basename in core_urls.router.registry
            if prefix == "employees"
        )
        self.assertEqual(
            registration,
            (("employees", EmployeeViewSet, "employee"),),
        )

        cases = (
            (
                "employee-list",
                None,
                "/api/employees/",
                {"get": "list", "post": "create"},
            ),
            (
                "employee-detail",
                {"public_id": EXAMPLE_PUBLIC_ID},
                f"/api/employees/{EXAMPLE_PUBLIC_ID}/",
                {
                    "get": "retrieve",
                    "put": "update",
                    "patch": "partial_update",
                    "delete": "destroy",
                },
            ),
        )
        for name, kwargs, expected_path, actions in cases:
            with self.subTest(name=name):
                path = reverse(name, kwargs=kwargs)
                self.assertEqual(path, expected_path)
                match = resolve(path)
                self.assertEqual(match.url_name, name)
                self.assertIs(match.func.cls, EmployeeViewSet)
                self.assertEqual(
                    {
                        method: action
                        for method, action in match.func.actions.items()
                        if method != "head"
                    },
                    actions,
                )
                if "head" in match.func.actions:
                    self.assertEqual(match.func.actions["head"], actions["get"])

        business_path = reverse(
            "business-create-employee-access",
            kwargs={"public_id": EXAMPLE_PUBLIC_ID},
        )
        self.assertEqual(
            business_path,
            f"/api/businesses/{EXAMPLE_PUBLIC_ID}/employees/create-access/",
        )
        business_match = resolve(business_path)
        self.assertEqual(
            business_match.url_name,
            "business-create-employee-access",
        )
        self.assertIs(business_match.func.cls, legacy_views.BusinessViewSet)
        self.assertEqual(
            {
                method: action
                for method, action in business_match.func.actions.items()
                if method != "head"
            },
            {"post": "create_employee_access"},
        )

    def test_employee_openapi_contract_is_preserved(self):
        schema = SchemaGenerator().get_schema(request=None, public=True)
        collection = schema["paths"]["/api/employees/"]
        detail = schema["paths"]["/api/employees/{public_id}/"]
        self.assertEqual(set(collection), {"get", "post"})
        self.assertEqual(set(detail), {"get", "put", "patch", "delete"})

        operation_ids = {
            ("collection", "get"): "api_employees_list",
            ("collection", "post"): "api_employees_create",
            ("detail", "get"): "api_employees_retrieve",
            ("detail", "put"): "api_employees_update",
            ("detail", "patch"): "api_employees_partial_update",
            ("detail", "delete"): "api_employees_destroy",
        }
        locations = {"collection": collection, "detail": detail}
        for (location, method), operation_id in operation_ids.items():
            operation = locations[location][method]
            self.assertEqual(operation["operationId"], operation_id)
            self.assertEqual(
                operation["security"],
                [{"BearerAuth": []}, {"BearerAuth": []}],
            )

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
        self.assertTrue(list_parameters["business_public_id"]["required"])
        for name in ("business_public_id", "status_public_id"):
            self.assertEqual(
                list_parameters[name]["schema"],
                {"type": "string", "format": "uuid"},
            )

        components = schema["components"]["schemas"]
        for name in (
            "Employee",
            "EmployeeRequest",
            "PatchedEmployeeRequest",
            "EmployeeSelection",
            "EmployeeRead",
            "PaginatedEmployeeReadList",
        ):
            self.assertIn(name, components)

        self.assertEqual(
            components["EmployeeRead"],
            {
                "oneOf": [
                    {"$ref": "#/components/schemas/Employee"},
                    {"$ref": "#/components/schemas/EmployeeSelection"},
                ]
            },
        )
        self.assertNotIn("discriminator", components["EmployeeRead"])
        self.assertEqual(
            components["PaginatedEmployeeReadList"]["properties"]
            ["results"]["items"],
            {"$ref": "#/components/schemas/EmployeeRead"},
        )

        employee_fields = {
            "public_id",
            "business_public_id",
            "full_name",
            "phone",
            "email",
            "position",
            "status_public_id",
            "status_name",
            "created_at",
            "updated_at",
        }
        response = components["Employee"]
        request = components["EmployeeRequest"]
        patched = components["PatchedEmployeeRequest"]
        selection = components["EmployeeSelection"]
        self.assertEqual(set(response["properties"]), employee_fields)
        self.assertEqual(
            request["required"],
            ["business_public_id", "full_name", "position"],
        )
        self.assertNotIn("required", patched)
        self.assertEqual(
            set(selection["properties"]),
            {"public_id", "full_name", "position"},
        )
        for field_name in ("public_id", "full_name", "position"):
            self.assertTrue(selection["properties"][field_name]["readOnly"])
        for field_name in ("public_id", "status_name", "created_at", "updated_at"):
            self.assertTrue(response["properties"][field_name]["readOnly"])
        for field_name in ("public_id", "business_public_id", "status_public_id"):
            self.assertEqual(
                response["properties"][field_name].get("format"),
                "uuid",
            )
        for properties in (
            response["properties"],
            request["properties"],
            patched["properties"],
            selection["properties"],
        ):
            for field in properties.values():
                self.assertNotIn("nullable", field)

        self.assertEqual(
            collection["get"]["responses"]["200"]["content"]
            ["application/json"]["schema"],
            {"$ref": "#/components/schemas/PaginatedEmployeeReadList"},
        )
        self.assertEqual(
            detail["get"]["responses"]["200"]["content"]
            ["application/json"]["schema"],
            {"$ref": "#/components/schemas/EmployeeRead"},
        )
        self.assertIn(
            "RegistrarEmpleadoSinAccesoAlSistema",
            collection["post"]["requestBody"]["content"]
            ["application/json"]["examples"],
        )
        self.assertIn("201", collection["post"]["responses"])
        self.assertIn("200", detail["put"]["responses"])
        self.assertIn("200", detail["patch"]["responses"])
        self.assertEqual(set(detail["delete"]["responses"]), {"204"})

        access = schema["paths"][
            "/api/businesses/{public_id}/employees/create-access/"
        ]["post"]
        self.assertEqual(
            access["operationId"],
            "api_businesses_employees_create_access_create",
        )
        self.assertEqual(
            access["requestBody"]["content"]["application/json"]["schema"],
            {"$ref": "#/components/schemas/EmployeeAccessCreateRequest"},
        )
        self.assertEqual(
            access["responses"]["201"]["content"]["application/json"]
            ["schema"],
            {"$ref": "#/components/schemas/BusinessMembership"},
        )

    def test_import_orders_in_isolated_processes(self):
        project_root = Path(__file__).resolve().parents[2]
        environment = os.environ.copy()
        environment["DJANGO_SETTINGS_MODULE"] = "playnow.settings"

        assertions = """
import core.serializers as legacy_serializers
import core.urls as core_urls
import core.views as legacy_views
from core.api.serializers.employees import EmployeeSelectionSerializer, EmployeeSerializer
from core.api.serializers.status import DefaultActiveStatusMixin
from core.api.views.base import BusinessScopedViewSet
from core.api.views.employees import EmployeeViewSet
from core.mixins import SoftDeleteByStatusMixin
from rest_framework import serializers

assert legacy_serializers.EmployeeSerializer is EmployeeSerializer
assert legacy_serializers.EmployeeSelectionSerializer is EmployeeSelectionSerializer
assert legacy_views.EmployeeViewSet is EmployeeViewSet
assert EmployeeSerializer.__bases__ == (DefaultActiveStatusMixin, serializers.ModelSerializer)
assert EmployeeSelectionSerializer.__bases__ == (serializers.ModelSerializer,)
assert EmployeeViewSet.__bases__ == (SoftDeleteByStatusMixin, BusinessScopedViewSet)
assert [
    (prefix, viewset, basename)
    for prefix, viewset, basename in core_urls.router.registry
    if prefix == "employees"
] == [("employees", EmployeeViewSet, "employee")]
"""

        for label, imports in IMPORT_SCENARIOS:
            with self.subTest(order=label):
                script = f"""
import django
django.setup()
{imports}
{assertions}
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
                        f"Import order {label!r} failed.\n"
                        f"stdout:\n{result.stdout}\n"
                        f"stderr:\n{result.stderr}"
                    ),
                )
