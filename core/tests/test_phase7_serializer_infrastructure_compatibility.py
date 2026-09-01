import ast
import hashlib
import inspect
import os
from pathlib import Path
import subprocess
import sys
from types import SimpleNamespace
from uuid import uuid4

from django.contrib.auth.models import AnonymousUser
from django.test import SimpleTestCase, TestCase
from drf_spectacular.generators import SchemaGenerator
from drf_spectacular.renderers import OpenApiYamlRenderer
from rest_framework import serializers
from rest_framework.exceptions import ValidationError

from core import serializers as legacy_serializers
from core.api.serializers.access import active_membership_business_ids
from core.api.serializers.fields import (
    SecurePublicIdRelatedField,
    public_id_read_only,
    secure_public_id_field,
)
from core.models import Business, BusinessMembership
from core.tests.factories import (
    create_business,
    create_customer,
    create_membership,
    create_payment_method,
    create_user,
)


EXPECTED_OPENAPI_SHA256 = (
    "d4257e952d03d45170eceb8e4a9912919aaa1e4c0332f9a96dc4df1645b677a9"
)

CANONICAL_LOCATIONS = {
    "public_id_read_only": "core/api/serializers/fields.py",
    "SecurePublicIdRelatedField": "core/api/serializers/fields.py",
    "secure_public_id_field": "core/api/serializers/fields.py",
    "active_membership_business_ids": "core/api/serializers/access.py",
}

AST_HASHES = {
    "public_id_read_only": (
        "fcba1425bcadcb3f96abec16c5afacf33209a273c42a675d33951191f943ae29"
    ),
    "SecurePublicIdRelatedField": (
        "20a11474572306134db23f62a8deab471b3d55b68844b9d7233e0fe0b554f2be"
    ),
    "secure_public_id_field": (
        "a9f86eb0e81a972d203573c2a9c2ee3735ae3fe3701409b45010a07ee0737042"
    ),
    "active_membership_business_ids": (
        "1e104cea36527710d0a0ad9005d2bae08352a57a66f86304a3c5ecbf0923d60f"
    ),
}

CONSUMER_AST_HASHES = {
    "TransactionDetailSerializer": (
        "a1ad6b82c78940b36e39f5ae48bc35725197bd59573f15e8da198de644ce10dd"
    ),
    "TransactionSerializer": (
        "62218d3359d2854ac6f4e2db9b2913c6fd3493ae9272ece8d02c1650696c3762"
    ),
    "DebtPaymentSerializer": (
        "8bf87df9bf8e7d898675a7664b8318f557131cf7bb4c1db91b59768c372d0a83"
    ),
}

IMPORT_SCENARIOS = (
    (
        "canonical-first",
        """
import core.api.serializers.fields
import core.api.serializers.access
import core.serializers
""",
    ),
    (
        "legacy-first",
        """
import core.serializers
import core.api.serializers.fields
import core.api.serializers.access
""",
    ),
    (
        "consumer-first",
        """
from core.serializers import BusinessMembershipSerializer, StockMovementSerializer
import core.api.serializers.fields
import core.api.serializers.access
""",
    ),
    (
        "Transaction-first",
        """
from core.serializers import TransactionDetailSerializer, TransactionSerializer
import core.api.serializers.fields
import core.api.serializers.access
""",
    ),
    (
        "DebtPayment-first",
        """
from core.serializers import DebtPaymentSerializer
import core.api.serializers.fields
import core.api.serializers.access
""",
    ),
    (
        "reports-first",
        """
from core.serializers import CustomerSummaryQuerySerializer
from core.views import CustomerSummaryView
import core.api.serializers.fields
import core.api.serializers.access
""",
    ),
    (
        "URL-first",
        """
import core.urls
import core.serializers
import core.api.serializers.fields
import core.api.serializers.access
""",
    ),
    (
        "OpenAPI-first",
        """
from drf_spectacular.generators import SchemaGenerator
schema = SchemaGenerator().get_schema(request=None, public=True)
assert "/api/transactions/" in schema["paths"]
import core.serializers
import core.api.serializers.fields
import core.api.serializers.access
""",
    ),
)


class PhaseSevenSerializerInfrastructureStructuralTests(SimpleTestCase):
    def test_legacy_exports_are_canonical_objects(self):
        self.assertIs(
            legacy_serializers.public_id_read_only,
            public_id_read_only,
        )
        self.assertIs(
            legacy_serializers.SecurePublicIdRelatedField,
            SecurePublicIdRelatedField,
        )
        self.assertIs(
            legacy_serializers.secure_public_id_field,
            secure_public_id_field,
        )
        self.assertIs(
            legacy_serializers.active_membership_business_ids,
            active_membership_business_ids,
        )

    def test_helpers_are_unique_and_ast_equivalent_to_baseline(self):
        project_root = Path(__file__).resolve().parents[2]
        definitions = {name: [] for name in CANONICAL_LOCATIONS}
        hashes = {}

        for module_path in (project_root / "core").rglob("*.py"):
            if "tests" in module_path.relative_to(project_root / "core").parts:
                continue

            relative_path = module_path.relative_to(project_root).as_posix()
            module = ast.parse(module_path.read_text(encoding="utf-8"))
            for node in ast.walk(module):
                name = getattr(node, "name", None)
                if name not in definitions:
                    continue
                if not isinstance(node, (ast.FunctionDef, ast.ClassDef)):
                    continue

                definitions[name].append(relative_path)
                hashes[name] = hashlib.sha256(
                    ast.dump(node, include_attributes=False).encode("utf-8")
                ).hexdigest()

        self.assertEqual(
            definitions,
            {name: [path] for name, path in CANONICAL_LOCATIONS.items()},
        )
        self.assertEqual(hashes, AST_HASHES)

    def test_canonical_modules_have_forward_only_dependencies(self):
        project_root = Path(__file__).resolve().parents[2]
        fields_module = ast.parse(
            (project_root / "core/api/serializers/fields.py").read_text(
                encoding="utf-8"
            )
        )
        access_module = ast.parse(
            (project_root / "core/api/serializers/access.py").read_text(
                encoding="utf-8"
            )
        )
        facade_module = ast.parse(
            (project_root / "core/serializers.py").read_text(
                encoding="utf-8"
            )
        )

        def imports_in(module):
            imports = []
            for node in ast.walk(module):
                if isinstance(node, ast.Import):
                    imports.extend(
                        (
                            "import",
                            alias.name,
                            0,
                            None,
                            alias.asname,
                        )
                        for alias in node.names
                    )
                elif isinstance(node, ast.ImportFrom):
                    imports.extend(
                        (
                            "from",
                            node.module,
                            node.level,
                            alias.name,
                            alias.asname,
                        )
                        for alias in node.names
                    )
            return imports

        def assert_exact_imports(label, actual, expected):
            self.assertEqual(
                sorted(actual, key=repr),
                sorted(expected, key=repr),
                msg=(
                    f"{label} imports changed.\n"
                    f"Expected: {expected!r}\n"
                    f"Actual: {actual!r}"
                ),
            )

        fields_imports = imports_in(fields_module)
        assert_exact_imports(
            "core.api.serializers.fields",
            fields_imports,
            [
                (
                    "from",
                    "rest_framework",
                    0,
                    "serializers",
                    None,
                ),
            ],
        )

        access_imports = imports_in(access_module)
        assert_exact_imports(
            "core.api.serializers.access",
            access_imports,
            [
                ("from", "core.models", 0, "Business", None),
                (
                    "from",
                    "core.models",
                    0,
                    "BusinessMembership",
                    None,
                ),
            ],
        )

        facade_imports = imports_in(facade_module)
        wildcard_imports = [
            imported
            for imported in facade_imports
            if imported[3] == "*"
        ]
        self.assertEqual(
            wildcard_imports,
            [],
            msg=f"core.serializers uses wildcard imports: {wildcard_imports!r}",
        )

        expected_reexports = [
            (
                "from",
                "core.api.serializers.fields",
                0,
                "public_id_read_only",
                None,
            ),
            (
                "from",
                "core.api.serializers.fields",
                0,
                "SecurePublicIdRelatedField",
                None,
            ),
            (
                "from",
                "core.api.serializers.fields",
                0,
                "secure_public_id_field",
                None,
            ),
            (
                "from",
                "core.api.serializers.access",
                0,
                "active_membership_business_ids",
                None,
            ),
        ]
        reexport_names = {
            imported[3]
            for imported in expected_reexports
        }

        def bound_name(imported):
            kind, module, _level, name, alias = imported
            if alias is not None:
                return alias
            if kind == "from":
                return name
            return module.split(".", 1)[0]

        actual_reexports = [
            imported
            for imported in facade_imports
            if bound_name(imported) in reexport_names
        ]
        assert_exact_imports(
            "core.serializers Phase 7 reexports",
            actual_reexports,
            expected_reexports,
        )

        assigned_reexports = []
        for node in facade_module.body:
            targets = []
            if isinstance(node, ast.Assign):
                targets = node.targets
            elif isinstance(node, (ast.AnnAssign, ast.AugAssign)):
                targets = [node.target]

            for target in targets:
                assigned_reexports.extend(
                    child.id
                    for child in ast.walk(target)
                    if isinstance(child, ast.Name)
                    and child.id in reexport_names
                )

        self.assertEqual(
            assigned_reexports,
            [],
            msg=(
                "core.serializers assigns Phase 7 reexports manually: "
                f"{assigned_reexports!r}"
            ),
        )

    def test_signatures_and_field_factories_are_preserved(self):
        self.assertEqual(
            str(inspect.signature(public_id_read_only)),
            "(*, source=None, allow_null=False)",
        )
        self.assertEqual(
            str(inspect.signature(secure_public_id_field)),
            "(model, *, source=None, required=True, allow_null=False)",
        )
        self.assertEqual(
            str(inspect.signature(active_membership_business_ids)),
            "(context)",
        )

        first_read = public_id_read_only()
        second_read = public_id_read_only(
            source="business",
            allow_null=True,
        )
        self.assertIs(type(first_read), serializers.SlugRelatedField)
        self.assertEqual(first_read.slug_field, "public_id")
        self.assertTrue(first_read.read_only)
        self.assertFalse(first_read.allow_null)
        self.assertIsNone(first_read.source)
        self.assertIsNone(first_read.label)
        self.assertIsNone(first_read.queryset)
        self.assertIsNot(first_read, second_read)
        self.assertEqual(second_read.source, "business")
        self.assertTrue(second_read.allow_null)

        first_secure = secure_public_id_field(Business)
        second_secure = secure_public_id_field(
            Business,
            source="business",
            required=False,
            allow_null=True,
        )
        self.assertIs(type(first_secure), SecurePublicIdRelatedField)
        self.assertEqual(first_secure.slug_field, "public_id")
        self.assertTrue(first_secure.required)
        self.assertFalse(first_secure.allow_null)
        self.assertIsNone(first_secure.source)
        self.assertIs(first_secure.queryset.model, Business)
        self.assertIsNone(first_secure.queryset._result_cache)
        self.assertIsNot(first_secure, second_secure)
        self.assertIsNot(first_secure.queryset, second_secure.queryset)
        self.assertIsNot(first_secure.queryset.query, second_secure.queryset.query)
        self.assertIsNone(second_secure.queryset._result_cache)
        self.assertEqual(second_secure.source, "business")
        self.assertFalse(second_secure.required)
        self.assertTrue(second_secure.allow_null)

    def test_secure_field_inheritance_and_errors_are_preserved(self):
        self.assertEqual(
            SecurePublicIdRelatedField.__bases__,
            (serializers.SlugRelatedField,),
        )
        self.assertIs(
            SecurePublicIdRelatedField.to_internal_value,
            serializers.SlugRelatedField.to_internal_value,
        )
        self.assertEqual(
            SecurePublicIdRelatedField.default_error_messages[
                "does_not_exist"
            ],
            "La relación indicada no es válida.",
        )
        secure_field = secure_public_id_field(Business)
        slug_field = serializers.SlugRelatedField(
            slug_field="public_id",
            queryset=Business.objects.all(),
        )
        self.assertEqual(
            {
                key: value
                for key, value in secure_field.error_messages.items()
                if key != "does_not_exist"
            },
            {
                key: value
                for key, value in slug_field.error_messages.items()
                if key != "does_not_exist"
            },
        )

    def test_transaction_and_debt_payment_consumers_are_unchanged(self):
        project_root = Path(__file__).resolve().parents[2]
        module = ast.parse(
            (project_root / "core/serializers.py").read_text(encoding="utf-8")
        )
        hashes = {}
        for node in module.body:
            if not isinstance(node, ast.ClassDef):
                continue
            if node.name not in CONSUMER_AST_HASHES:
                continue
            hashes[node.name] = hashlib.sha256(
                ast.dump(node, include_attributes=False).encode("utf-8")
            ).hexdigest()

        self.assertEqual(hashes, CONSUMER_AST_HASHES)
        self.assertIsInstance(
            legacy_serializers.TransactionSerializer().fields[
                "business_public_id"
            ],
            SecurePublicIdRelatedField,
        )
        self.assertIsInstance(
            legacy_serializers.DebtPaymentSerializer().fields[
                "debt_public_id"
            ],
            SecurePublicIdRelatedField,
        )

    def test_openapi_is_byte_identical_to_baseline(self):
        schema = SchemaGenerator().get_schema(request=None, public=True)
        rendered = OpenApiYamlRenderer().render(schema)
        self.assertEqual(
            hashlib.sha256(rendered).hexdigest(),
            EXPECTED_OPENAPI_SHA256,
        )

    def test_import_orders_in_isolated_processes(self):
        project_root = Path(__file__).resolve().parents[2]
        environment = os.environ.copy()
        environment["DJANGO_SETTINGS_MODULE"] = "playnow.settings"
        assertions = """
import core.api.serializers.access as canonical_access
import core.api.serializers.fields as canonical_fields
import core.serializers as legacy
from core.models import Business

assert legacy.public_id_read_only is canonical_fields.public_id_read_only
assert legacy.SecurePublicIdRelatedField is canonical_fields.SecurePublicIdRelatedField
assert legacy.secure_public_id_field is canonical_fields.secure_public_id_field
assert legacy.active_membership_business_ids is canonical_access.active_membership_business_ids
assert type(legacy.public_id_read_only()) is canonical_fields.serializers.SlugRelatedField
assert type(legacy.secure_public_id_field(Business)) is canonical_fields.SecurePublicIdRelatedField
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


class PhaseSevenSerializerInfrastructureAccessTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.owner = create_user(email="phase7-owner@playnow.test")
        cls.member = create_user(email="phase7-member@playnow.test")
        cls.superuser = create_user(
            email="phase7-superuser@playnow.test",
            is_superuser=True,
        )
        cls.business_a = create_business(user=cls.owner)
        cls.business_b = create_business(user=cls.owner)
        cls.business_c = create_business(user=cls.owner)
        create_membership(
            user=cls.member,
            business=cls.business_a,
            role=BusinessMembership.ROLE_CASHIER,
        )
        create_membership(
            user=cls.member,
            business=cls.business_b,
            role=BusinessMembership.ROLE_CASHIER,
            is_active=False,
        )
        create_membership(
            user=cls.member,
            business=cls.business_c,
            role=BusinessMembership.ROLE_SELLER,
        )
        cls.customer_a = create_customer(business=cls.business_a)
        cls.customer_b = create_customer(business=cls.business_b)
        cls.method_a = create_payment_method(business=cls.business_a)
        cls.method_b = create_payment_method(business=cls.business_b)

    def test_membership_business_ids_preserve_all_actor_cases(self):
        for label, context in (
            ("missing-request", {}),
            ("missing-user", {"request": SimpleNamespace()}),
            (
                "anonymous",
                {"request": SimpleNamespace(user=AnonymousUser())},
            ),
        ):
            with self.subTest(actor=label):
                queryset = active_membership_business_ids(context)
                self.assertIs(queryset.model, Business)
                self.assertIsNone(queryset._result_cache)
                self.assertEqual(list(queryset), [])

        queryset = active_membership_business_ids(
            {"request": SimpleNamespace(user=self.member)}
        )
        self.assertIsNone(queryset._result_cache)
        self.assertEqual(
            set(queryset),
            {self.business_a.pk, self.business_c.pk},
        )
        self.assertIsNone(
            active_membership_business_ids(
                {"request": SimpleNamespace(user=self.superuser)}
            )
        )

    def test_real_consumers_keep_scoped_and_global_querysets(self):
        member_request = SimpleNamespace(user=self.member)
        transaction = legacy_serializers.TransactionSerializer(
            context={"request": member_request}
        )
        customer_queryset = transaction.fields[
            "customer_public_id"
        ].queryset
        self.assertEqual(
            set(customer_queryset.values_list("pk", flat=True)),
            {self.customer_a.pk},
        )

        payment = legacy_serializers.DebtPaymentSerializer(
            context={"request": member_request}
        )
        method_queryset = payment.fields[
            "payment_method_public_id"
        ].queryset
        self.assertEqual(
            set(method_queryset.values_list("pk", flat=True)),
            {self.method_a.pk},
        )

        anonymous = legacy_serializers.TransactionSerializer()
        self.assertEqual(
            list(anonymous.fields["customer_public_id"].queryset),
            [],
        )

        global_serializer = legacy_serializers.TransactionSerializer(
            context={
                "request": SimpleNamespace(user=self.superuser),
            }
        )
        self.assertEqual(
            set(
                global_serializer.fields["customer_public_id"]
                .queryset.values_list("pk", flat=True)
            ),
            {self.customer_a.pk, self.customer_b.pk},
        )

    def test_foreign_and_missing_relations_have_identical_errors(self):
        serializer = legacy_serializers.TransactionSerializer(
            context={
                "request": SimpleNamespace(user=self.member),
            }
        )
        field = serializer.fields["customer_public_id"]
        errors = []
        for value in (self.customer_b.public_id, uuid4()):
            with self.assertRaises(ValidationError) as caught:
                field.run_validation(str(value))
            errors.append(
                (
                    str(caught.exception.detail[0]),
                    caught.exception.detail[0].code,
                )
            )

        self.assertEqual(errors[0], errors[1])
        self.assertEqual(
            errors[0],
            ("La relación indicada no es válida.", "does_not_exist"),
        )
