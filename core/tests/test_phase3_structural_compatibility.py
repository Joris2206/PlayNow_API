import os
import subprocess
import sys
import textwrap
from pathlib import Path

from django.test import SimpleTestCase
from django.urls import URLPattern, get_resolver, resolve, reverse

from core import urls as core_urls
from core import views as legacy_views
from core.api.schemas import examples as canonical_examples
from core.api.views.base import BusinessScopedViewSet
from core.api.views.report_access import validate_report_business_access


DOCUMENTATION_NAMES = (
    "BUDGET_CREATE_EXAMPLE",
    "BUSINESS_CREATE_EXAMPLE",
    "BUSINESS_PUBLIC_ID",
    "CATEGORY_CREATE_EXAMPLE",
    "CATEGORY_PUBLIC_ID",
    "CUSTOMER_CREATE_EXAMPLE",
    "CUSTOMER_PUBLIC_ID",
    "DEBT_CREATE_EXAMPLE",
    "DEBT_PAYMENT_CREATE_EXAMPLE",
    "DEBT_PUBLIC_ID",
    "EMPLOYEE_ACCESS_EXAMPLE",
    "EMPLOYEE_CREATE_EXAMPLE",
    "EMPLOYEE_PERIOD_QUERY_PARAMETERS",
    "EMPLOYEE_PUBLIC_ID",
    "GOAL_CREATE_EXAMPLE",
    "GOAL_PROGRESS_CREATE_EXAMPLE",
    "GOAL_PUBLIC_ID",
    "MEMBERSHIP_UPDATE_EXAMPLE",
    "NOTIFICATION_CREATE_EXAMPLE",
    "PAYMENT_METHOD_CREATE_EXAMPLE",
    "PAYMENT_METHOD_PUBLIC_ID",
    "PRODUCT_CREATE_EXAMPLE",
    "PRODUCT_PUBLIC_ID",
    "PUBLIC_CATALOG_BUSINESS_PARAMETER",
    "REMINDER_CREATE_EXAMPLE",
    "STATUS_PUBLIC_ID",
    "SUPPLIER_CREATE_EXAMPLE",
    "SUPPLIER_PUBLIC_ID",
    "TRANSACTION_EXPENSE_EXAMPLE",
    "TRANSACTION_PUBLIC_ID",
    "TRANSACTION_PURCHASE_EXAMPLE",
    "TRANSACTION_PURCHASE_PARTIAL_EXAMPLE",
    "TRANSACTION_PURCHASE_PENDING_EXAMPLE",
    "TRANSACTION_SALE_EXAMPLE",
    "TRANSACTION_SALE_PARTIAL_EXAMPLE",
    "TRANSACTION_SALE_PENDING_EXAMPLE",
    "TRANSACTION_UPDATE_EXAMPLE",
)

BUSINESS_SCOPED_VIEWSETS = (
    "ProductCategoryViewSet",
    "ProductViewSet",
    "EmployeeViewSet",
    "CustomerViewSet",
    "SupplierViewSet",
    "PaymentMethodViewSet",
    "StockMovementViewSet",
    "TransactionViewSet",
    "DebtViewSet",
    "DebtPaymentViewSet",
    "NotificationViewSet",
    "ReminderViewSet",
    "BudgetViewSet",
    "GoalViewSet",
    "GoalProgressViewSet",
)

ROUTER_REGISTRY = (
    ("auth/register", "RegisterViewSet", "register"),
    ("statuses", "EntityStatusViewSet", "entity-status"),
    ("payment-methods", "PaymentMethodViewSet", "payment-method"),
    ("businesses", "BusinessViewSet", "business"),
    ("categories", "ProductCategoryViewSet", "product-category"),
    ("products", "ProductViewSet", "product"),
    ("employees", "EmployeeViewSet", "employee"),
    ("customers", "CustomerViewSet", "customer"),
    ("suppliers", "SupplierViewSet", "supplier"),
    ("transactions", "TransactionViewSet", "transaction"),
    ("debts", "DebtViewSet", "debt"),
    ("debt-payments", "DebtPaymentViewSet", "debt-payment"),
    ("notifications", "NotificationViewSet", "notification"),
    ("reminders", "ReminderViewSet", "reminder"),
    ("budgets", "BudgetViewSet", "budget"),
    ("goals", "GoalViewSet", "goal"),
    ("goal-progress", "GoalProgressViewSet", "goal-progress"),
    ("stock-movements", "StockMovementViewSet", "stock-movement"),
    ("users", "UserViewSet", "user"),
    ("commission-plans", "EmployeeCommissionPlanViewSet", "commission-plan"),
    (
        "commission-settlements",
        "CommissionSettlementViewSet",
        "commission-settlement",
    ),
    ("cash-movements", "CashMovementViewSet", "cash-movement"),
    ("cash-registers", "CashRegisterViewSet", "cash-register"),
    ("monthly-closures", "MonthlyClosureViewSet", "monthly-closure"),
)

PUBLIC_ROUTER_REGISTRY = (
    ("categories", "PublicProductCategoryViewSet", "public-product-category"),
    ("products", "PublicProductViewSet", "public-product"),
)

EXAMPLE_PUBLIC_ID = "00000000-0000-0000-0000-000000000001"

ROUTER_ROUTE_CASES = (
    ("business-list", None, "/api/businesses/", "BusinessViewSet"),
    (
        "business-detail",
        {"public_id": EXAMPLE_PUBLIC_ID},
        f"/api/businesses/{EXAMPLE_PUBLIC_ID}/",
        "BusinessViewSet",
    ),
    ("product-list", None, "/api/products/", "ProductViewSet"),
    (
        "product-detail",
        {"public_id": EXAMPLE_PUBLIC_ID},
        f"/api/products/{EXAMPLE_PUBLIC_ID}/",
        "ProductViewSet",
    ),
    ("employee-list", None, "/api/employees/", "EmployeeViewSet"),
    (
        "employee-detail",
        {"public_id": EXAMPLE_PUBLIC_ID},
        f"/api/employees/{EXAMPLE_PUBLIC_ID}/",
        "EmployeeViewSet",
    ),
    (
        "payment-method-list",
        None,
        "/api/payment-methods/",
        "PaymentMethodViewSet",
    ),
    (
        "payment-method-detail",
        {"public_id": EXAMPLE_PUBLIC_ID},
        f"/api/payment-methods/{EXAMPLE_PUBLIC_ID}/",
        "PaymentMethodViewSet",
    ),
    ("transaction-list", None, "/api/transactions/", "TransactionViewSet"),
    (
        "transaction-detail",
        {"public_id": EXAMPLE_PUBLIC_ID},
        f"/api/transactions/{EXAMPLE_PUBLIC_ID}/",
        "TransactionViewSet",
    ),
    ("debt-list", None, "/api/debts/", "DebtViewSet"),
    (
        "debt-detail",
        {"public_id": EXAMPLE_PUBLIC_ID},
        f"/api/debts/{EXAMPLE_PUBLIC_ID}/",
        "DebtViewSet",
    ),
    ("debt-payment-list", None, "/api/debt-payments/", "DebtPaymentViewSet"),
    (
        "debt-payment-detail",
        {"public_id": EXAMPLE_PUBLIC_ID},
        f"/api/debt-payments/{EXAMPLE_PUBLIC_ID}/",
        "DebtPaymentViewSet",
    ),
    ("cash-register-list", None, "/api/cash-registers/", "CashRegisterViewSet"),
    (
        "cash-register-detail",
        {"public_id": EXAMPLE_PUBLIC_ID},
        f"/api/cash-registers/{EXAMPLE_PUBLIC_ID}/",
        "CashRegisterViewSet",
    ),
    (
        "cash-register-close",
        {"public_id": EXAMPLE_PUBLIC_ID},
        f"/api/cash-registers/{EXAMPLE_PUBLIC_ID}/close/",
        "CashRegisterViewSet",
    ),
    (
        "monthly-closure-list",
        None,
        "/api/monthly-closures/",
        "MonthlyClosureViewSet",
    ),
    (
        "monthly-closure-detail",
        {"public_id": EXAMPLE_PUBLIC_ID},
        f"/api/monthly-closures/{EXAMPLE_PUBLIC_ID}/",
        "MonthlyClosureViewSet",
    ),
    (
        "monthly-closure-reopen",
        {"public_id": EXAMPLE_PUBLIC_ID},
        f"/api/monthly-closures/{EXAMPLE_PUBLIC_ID}/reopen/",
        "MonthlyClosureViewSet",
    ),
    ("register-list", None, "/api/auth/register/", "RegisterViewSet"),
)

NON_ROUTER_ROUTE_CASES = (
    ("current-user", "/api/me/", "CurrentUserView"),
    (
        "password-reset-request",
        "/api/auth/password/reset/",
        "PasswordResetRequestView",
    ),
    (
        "password-reset-confirm",
        "/api/auth/password/reset/confirm/",
        "PasswordResetConfirmView",
    ),
)


class PhaseThreeStructuralCompatibilityTests(SimpleTestCase):
    def test_legacy_exports_are_canonical_objects(self):
        self.assertIs(legacy_views.BusinessScopedViewSet, BusinessScopedViewSet)
        self.assertIs(
            legacy_views.validate_report_business_access,
            validate_report_business_access,
        )

        for name in DOCUMENTATION_NAMES:
            with self.subTest(name=name):
                self.assertIs(
                    getattr(legacy_views, name),
                    getattr(canonical_examples, name),
                )

    def test_domain_viewsets_inherit_from_canonical_base(self):
        for name in BUSINESS_SCOPED_VIEWSETS:
            with self.subTest(viewset=name):
                self.assertIn(
                    BusinessScopedViewSet,
                    getattr(legacy_views, name).__mro__,
                )

    def test_router_registries_match_baseline(self):
        router_registry = tuple(
            (prefix, viewset.__name__, basename)
            for prefix, viewset, basename in core_urls.router.registry
        )
        public_router_registry = tuple(
            (prefix, viewset.__name__, basename)
            for prefix, viewset, basename in core_urls.public_router.registry
        )

        self.assertEqual(
            router_registry,
            ROUTER_REGISTRY,
            msg="The private router registry changed from the Phase 3 baseline.",
        )
        self.assertEqual(
            public_router_registry,
            PUBLIC_ROUTER_REGISTRY,
            msg="The public router registry changed from the Phase 3 baseline.",
        )

    def test_representative_router_urls_reverse_and_resolve(self):
        for url_name, kwargs, expected_path, expected_viewset_name in ROUTER_ROUTE_CASES:
            with self.subTest(url_name=url_name):
                path = reverse(url_name, kwargs=kwargs)
                self.assertEqual(
                    path,
                    expected_path,
                    msg=f"Unexpected path for {url_name}.",
                )

                match = resolve(path)
                self.assertEqual(
                    match.url_name,
                    url_name,
                    msg=f"Unexpected resolved URL name for {path}.",
                )

                expected_viewset = getattr(legacy_views, expected_viewset_name)
                self.assertIs(
                    match.func.cls,
                    expected_viewset,
                    msg=f"Unexpected ViewSet resolved for {url_name}.",
                )

                if expected_viewset_name in BUSINESS_SCOPED_VIEWSETS:
                    self.assertIn(
                        BusinessScopedViewSet,
                        expected_viewset.__mro__,
                        msg=f"{expected_viewset_name} lost its canonical base.",
                    )

    def test_non_router_urls_reverse_and_resolve(self):
        health_path = reverse("healthcheck")
        self.assertEqual(health_path, "/api/health/")
        health_match = resolve(health_path)
        self.assertEqual(health_match.url_name, "healthcheck")
        self.assertIs(health_match.func, legacy_views.healthcheck)

        for url_name, expected_path, expected_view_name in NON_ROUTER_ROUTE_CASES:
            with self.subTest(url_name=url_name):
                path = reverse(url_name)
                self.assertEqual(
                    path,
                    expected_path,
                    msg=f"Unexpected path for {url_name}.",
                )

                match = resolve(path)
                self.assertEqual(
                    match.url_name,
                    url_name,
                    msg=f"Unexpected resolved URL name for {path}.",
                )
                self.assertIs(
                    match.func.view_class,
                    getattr(legacy_views, expected_view_name),
                    msg=f"Unexpected view resolved for {url_name}.",
                )

    def test_total_url_pattern_count_matches_baseline(self):
        patterns = self._flatten_url_patterns(get_resolver().url_patterns)
        self.assertEqual(
            len(patterns),
            296,
            msg="The total URL pattern count changed from the Phase 3 baseline.",
        )

    def _flatten_url_patterns(self, patterns):
        flattened = []
        for pattern in patterns:
            if isinstance(pattern, URLPattern):
                flattened.append(pattern)
            else:
                flattened.extend(self._flatten_url_patterns(pattern.url_patterns))
        return flattened

    def test_import_orders_in_isolated_processes(self):
        import_orders = (
            (
                "canonical modules before legacy facade and URLs",
                (
                    "core.api.views.base",
                    "core.api.views.report_access",
                    "core.api.schemas.examples",
                    "core.views",
                    "core.urls",
                ),
            ),
            (
                "legacy facade before canonical modules and URLs",
                (
                    "core.views",
                    "core.api.views.base",
                    "core.api.views.report_access",
                    "core.api.schemas.examples",
                    "core.urls",
                ),
            ),
            (
                "URLs before canonical modules",
                (
                    "core.urls",
                    "core.api.schemas.examples",
                    "core.api.views.report_access",
                    "core.api.views.base",
                    "core.views",
                ),
            ),
        )

        for description, modules in import_orders:
            with self.subTest(order=description):
                self._assert_import_order_in_isolated_process(description, modules)

    def _assert_import_order_in_isolated_process(self, description, modules):
        script = textwrap.dedent(
            f"""
            import importlib
            import os

            os.environ["DJANGO_SETTINGS_MODULE"] = "playnow.settings"

            import django
            django.setup()

            imported = []
            for module_name in {modules!r}:
                try:
                    importlib.import_module(module_name)
                except Exception as exc:
                    raise AssertionError(
                        "Failed importing %s after %r: %s"
                        % (module_name, imported, exc)
                    ) from exc
                imported.append(module_name)

            from core import views as legacy
            from core.api.schemas import examples
            from core.api.views import base, report_access

            assert legacy.BusinessScopedViewSet is base.BusinessScopedViewSet, (
                "BusinessScopedViewSet identity mismatch"
            )
            assert (
                legacy.validate_report_business_access
                is report_access.validate_report_business_access
            ), "validate_report_business_access identity mismatch"

            for name in {DOCUMENTATION_NAMES!r}:
                assert getattr(legacy, name) is getattr(examples, name), (
                    "Documentation identity mismatch for %s" % name
                )

            for name in {BUSINESS_SCOPED_VIEWSETS!r}:
                assert base.BusinessScopedViewSet in getattr(legacy, name).__mro__, (
                    "Canonical base missing from MRO for %s" % name
                )
            """
        )
        environment = os.environ.copy()
        environment["DJANGO_SETTINGS_MODULE"] = "playnow.settings"
        result = subprocess.run(
            [sys.executable, "-c", script],
            cwd=Path(__file__).resolve().parents[2],
            env=environment,
            capture_output=True,
            text=True,
            timeout=30,
            check=False,
        )

        self.assertEqual(
            result.returncode,
            0,
            msg=(
                f"Import order failed: {description}\n"
                f"stdout:\n{result.stdout}\n"
                f"stderr:\n{result.stderr}"
            ),
        )
