import os
import subprocess
import sys
import textwrap
from pathlib import Path

from django.contrib.auth import get_user_model as django_get_user_model
from django.test import SimpleTestCase

from core.api.serializers import auth as canonical
from core.services import serializer as legacy


class AuthSerializerCompatibilityTests(SimpleTestCase):
    def test_legacy_exports_are_canonical_objects(self):
        names = (
            "BLACKLIST_ENABLED",
            "User",
            "token_generator",
            "BlacklistedToken",
            "OutstandingToken",
            "PasswordResetTokenGenerator",
            "force_bytes",
            "force_str",
            "urlsafe_base64_encode",
            "urlsafe_base64_decode",
            "serializers",
            "PasswordResetRequestSerializer",
            "PasswordResetConfirmSerializer",
            "ChangePasswordSerializer",
        )

        for name in names:
            with self.subTest(name=name):
                self.assertIs(getattr(legacy, name), getattr(canonical, name))

    def test_legacy_get_user_model_is_the_django_helper(self):
        self.assertIs(legacy.get_user_model, django_get_user_model)

    def test_import_orders_in_isolated_processes(self):
        import_orders = (
            (
                "canonical serializers before legacy service",
                (
                    "core.api.serializers.auth",
                    "core.services.serializer",
                ),
            ),
            (
                "legacy service before canonical serializers",
                (
                    "core.services.serializer",
                    "core.api.serializers.auth",
                ),
            ),
            (
                "legacy facades before canonical modules",
                (
                    "core.views",
                    "core.serializers",
                    "core.services.serializer",
                    "core.api.views.auth",
                    "core.api.views.health",
                    "core.api.serializers.auth",
                ),
            ),
            (
                "canonical modules before legacy facades and URLs",
                (
                    "core.api.serializers.auth",
                    "core.api.views.auth",
                    "core.api.views.health",
                    "core.services.serializer",
                    "core.serializers",
                    "core.views",
                    "core.urls",
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

            from django.contrib.auth import get_user_model
            from core.api.serializers import auth as canonical_serializers
            from core.api.views import auth as canonical_auth_views
            from core.api.views import health as canonical_health_views
            from core import serializers as legacy_serializers
            from core import views as legacy_views
            from core.services import serializer as legacy_service

            service_names = (
                "BLACKLIST_ENABLED",
                "User",
                "token_generator",
                "BlacklistedToken",
                "OutstandingToken",
                "PasswordResetTokenGenerator",
                "force_bytes",
                "force_str",
                "urlsafe_base64_encode",
                "urlsafe_base64_decode",
                "serializers",
                "PasswordResetRequestSerializer",
                "PasswordResetConfirmSerializer",
                "ChangePasswordSerializer",
            )
            for name in service_names:
                assert getattr(legacy_service, name) is getattr(canonical_serializers, name), (
                    "Legacy service identity mismatch for %s" % name
                )

            assert legacy_service.get_user_model is get_user_model, (
                "Legacy get_user_model identity mismatch"
            )

            for name in ("HealthSerializer", "RegisterSerializer"):
                assert getattr(legacy_serializers, name) is getattr(canonical_serializers, name), (
                    "Legacy serializer identity mismatch for %s" % name
                )

            for name in (
                "RegisterViewSet",
                "UserViewSet",
                "PasswordResetRequestView",
                "PasswordResetConfirmView",
            ):
                assert getattr(legacy_views, name) is getattr(canonical_auth_views, name), (
                    "Legacy auth view identity mismatch for %s" % name
                )

            assert legacy_views.healthcheck is canonical_health_views.healthcheck, (
                "Legacy healthcheck identity mismatch"
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
