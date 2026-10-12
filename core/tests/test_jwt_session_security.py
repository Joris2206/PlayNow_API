from datetime import timedelta
from unittest.mock import patch

from django.conf import settings
from django.contrib.auth.tokens import PasswordResetTokenGenerator
from django.test import override_settings
from django.urls import reverse
from django.utils.encoding import force_bytes
from django.utils.http import urlsafe_base64_encode
from drf_spectacular.generators import SchemaGenerator
from rest_framework import status
from rest_framework.test import APIClient, APITestCase
from rest_framework_simplejwt.token_blacklist.models import (
    BlacklistedToken,
    OutstandingToken,
)
from rest_framework_simplejwt.tokens import AccessToken

from core.models import BusinessMembership, Employee, User
from core.api.views.auth import LoginView
from core.tests.factories import create_business, create_status, create_user
from core.tokens import AUTH_VERSION_CLAIM, VersionedRefreshToken


_MISSING_CLAIM = object()


TEST_REST_FRAMEWORK = {
    **settings.REST_FRAMEWORK,
    "DEFAULT_THROTTLE_RATES": {
        **settings.REST_FRAMEWORK["DEFAULT_THROTTLE_RATES"],
        "login": "1000/min",
        "auth_reset_request": "1000/min",
        "auth_reset_confirm": "1000/min",
    },
}


@override_settings(REST_FRAMEWORK=TEST_REST_FRAMEWORK)
@patch.object(LoginView, "throttle_classes", [])
class JWTSessionSecurityTests(APITestCase):
    password = "InitialSecurePassword-1!"

    @classmethod
    def setUpTestData(cls):
        cls.user = create_user(
            email="jwt-security@playnow.test",
            password=cls.password,
        )
        cls.active = create_status("Activo")
        cls.business = create_business(user=cls.user, status=cls.active)

    def login(self, password=None):
        return self.client.post(
            reverse("token_obtain_pair"),
            {
                "email": self.user.email,
                "password": password or self.password,
            },
            format="json",
        )

    @staticmethod
    def authenticated_client(access):
        client = APIClient()
        client.credentials(HTTP_AUTHORIZATION=f"Bearer {access}")
        return client

    def assert_access_status(self, access, expected):
        response = self.authenticated_client(access).get(reverse("current-user"))
        self.assertEqual(response.status_code, expected)

    @classmethod
    def contains_auth_version(cls, value):
        if isinstance(value, dict):
            return any(
                key == AUTH_VERSION_CLAIM
                or cls.contains_auth_version(key)
                or cls.contains_auth_version(nested)
                for key, nested in value.items()
            )
        if isinstance(value, (list, tuple)):
            return any(cls.contains_auth_version(item) for item in value)
        return value == AUTH_VERSION_CLAIM

    @staticmethod
    def _set_auth_version_claim(token, value):
        if value is _MISSING_CLAIM:
            token.payload.pop(AUTH_VERSION_CLAIM, None)
        else:
            token[AUTH_VERSION_CLAIM] = value
        return str(token)

    def test_claims_access_validation_and_current_privileges(self):
        response = self.login()
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        refresh = VersionedRefreshToken(response.data["refresh"])
        access = AccessToken(response.data["access"])
        for token in (refresh, access):
            self.assertEqual(token[AUTH_VERSION_CLAIM], self.user.auth_version)
            self.assertIn("user_id", token)
            self.assertIn("jti", token)
            self.assertNotIn("email", token)
            self.assertNotIn("role", token)
            self.assertNotIn("is_superuser", token)
        self.assert_access_status(response.data["access"], status.HTTP_200_OK)

        without_version = AccessToken.for_user(self.user)
        self.assertNotIn(AUTH_VERSION_CLAIM, without_version)
        self.assert_access_status(str(without_version), status.HTTP_401_UNAUTHORIZED)

        self.user.auth_version += 1
        self.user.save(update_fields=["auth_version"])
        self.assert_access_status(response.data["access"], status.HTTP_401_UNAUTHORIZED)

        self.user.is_superuser = True
        self.user.save(update_fields=["is_superuser"])
        current = self.login()
        self.assertEqual(current.status_code, status.HTTP_200_OK)
        self.user.is_superuser = False
        self.user.save(update_fields=["is_superuser"])
        result = self.authenticated_client(current.data["access"]).get(
            reverse("current-user")
        )
        self.assertEqual(result.status_code, status.HTTP_200_OK)
        self.assertFalse(result.data["is_superuser"])

        version = self.user.auth_version
        membership = BusinessMembership.objects.get(
            user=self.user,
            business=self.business,
        )
        membership.role = BusinessMembership.ROLE_ADMIN
        membership.save(update_fields=["role", "updated_at"])
        self.user.refresh_from_db()
        self.assertEqual(self.user.auth_version, version)

    def test_inactive_tampered_and_invalid_login_are_generic(self):
        valid = self.login()
        self.assertEqual(valid.status_code, status.HTTP_200_OK)
        header, payload, signature = valid.data["access"].split(".")
        signature = ("a" if signature[0] != "a" else "b") + signature[1:]
        tampered = ".".join((header, payload, signature))
        self.assert_access_status(tampered, status.HTTP_401_UNAUTHORIZED)

        self.user.is_active = False
        self.user.save(update_fields=["is_active"])
        self.assert_access_status(valid.data["access"], status.HTTP_401_UNAUTHORIZED)
        inactive = self.login()
        missing = self.client.post(
            reverse("token_obtain_pair"),
            {"email": "missing@playnow.test", "password": self.password},
            format="json",
        )
        wrong = self.login(password="WrongSecurePassword-1!")
        self.assertEqual(inactive.status_code, status.HTTP_401_UNAUTHORIZED)
        self.assertEqual(missing.status_code, status.HTTP_401_UNAUTHORIZED)
        self.assertEqual(wrong.status_code, status.HTTP_401_UNAUTHORIZED)
        self.assertEqual(inactive.data, missing.data)
        self.assertEqual(missing.data, wrong.data)

    def test_multiple_login_rotation_and_global_logout(self):
        login_a = self.login()
        login_b = self.login()
        self.assertEqual(login_a.status_code, status.HTTP_200_OK)
        self.assertEqual(login_b.status_code, status.HTTP_200_OK)
        self.assertNotEqual(login_a.data["refresh"], login_b.data["refresh"])
        self.assert_access_status(login_a.data["access"], status.HTTP_200_OK)
        self.assert_access_status(login_b.data["access"], status.HTTP_200_OK)

        rotated_a = self.client.post(
            reverse("token_refresh"),
            {"refresh": login_a.data["refresh"]},
            format="json",
        )
        self.assertEqual(rotated_a.status_code, status.HTTP_200_OK)
        self.assertEqual(set(rotated_a.data), {"access", "refresh"})
        reused = self.client.post(
            reverse("token_refresh"),
            {"refresh": login_a.data["refresh"]},
            format="json",
        )
        self.assertEqual(reused.status_code, status.HTTP_401_UNAUTHORIZED)
        rotated_b = self.client.post(
            reverse("token_refresh"),
            {"refresh": login_b.data["refresh"]},
            format="json",
        )
        self.assertEqual(rotated_b.status_code, status.HTTP_200_OK)

        logout = self.client.post(
            reverse("token_logout"),
            {"refresh": rotated_a.data["refresh"]},
            format="json",
        )
        self.assertEqual(logout.status_code, status.HTTP_204_NO_CONTENT)
        self.assertEqual(logout.content, b"")
        for access in (
            login_a.data["access"],
            login_b.data["access"],
            rotated_a.data["access"],
            rotated_b.data["access"],
        ):
            self.assert_access_status(access, status.HTTP_401_UNAUTHORIZED)
        for refresh in (rotated_a.data["refresh"], rotated_b.data["refresh"]):
            response = self.client.post(
                reverse("token_refresh"),
                {"refresh": refresh},
                format="json",
            )
            self.assertEqual(response.status_code, status.HTTP_401_UNAUTHORIZED)

        repeated = self.client.post(
            reverse("token_logout"),
            {"refresh": rotated_a.data["refresh"]},
            format="json",
        )
        self.assertEqual(repeated.status_code, status.HTTP_204_NO_CONTENT)
        self.assertEqual(self.login().status_code, status.HTTP_200_OK)

    def test_old_logout_cannot_revoke_a_later_session(self):
        login_a = self.login()
        self.assertEqual(login_a.status_code, status.HTTP_200_OK)
        original_user = User.objects.values(
            "password", "is_active", "is_superuser", "auth_version"
        ).get(pk=self.user.pk)
        memberships = list(
            BusinessMembership.objects.filter(user=self.user)
            .order_by("pk")
            .values()
        )
        employees = list(
            Employee.objects.filter(business=self.business).order_by("pk").values()
        )
        original_totals = {
            "users": User.objects.count(),
            "memberships": BusinessMembership.objects.count(),
            "employees": Employee.objects.count(),
        }
        outstanding_a = OutstandingToken.objects.get(token=login_a.data["refresh"])
        self.assertEqual(
            OutstandingToken.objects.filter(user=self.user).count(),
            1,
        )
        self.assertEqual(
            BlacklistedToken.objects.filter(token__user=self.user).count(),
            0,
        )
        self.assertFalse(
            BlacklistedToken.objects.filter(token=outstanding_a).exists()
        )

        logout_a = self.client.post(
            reverse("token_logout"),
            {"refresh": login_a.data["refresh"]},
            format="json",
        )
        self.assertEqual(logout_a.status_code, status.HTTP_204_NO_CONTENT)
        self.user.refresh_from_db()
        current_version = original_user["auth_version"] + 1
        self.assertEqual(self.user.auth_version, current_version)
        self.assertEqual(
            OutstandingToken.objects.filter(user=self.user).count(),
            1,
        )
        self.assertEqual(
            BlacklistedToken.objects.filter(token__user=self.user).count(),
            1,
        )
        self.assertTrue(
            BlacklistedToken.objects.filter(token=outstanding_a).exists()
        )
        self.assert_access_status(login_a.data["access"], status.HTTP_401_UNAUTHORIZED)
        self.assertEqual(
            self.client.post(
                reverse("token_refresh"),
                {"refresh": login_a.data["refresh"]},
                format="json",
            ).status_code,
            status.HTTP_401_UNAUTHORIZED,
        )

        login_b = self.login()
        self.assertEqual(login_b.status_code, status.HTTP_200_OK)
        outstanding_b = OutstandingToken.objects.get(token=login_b.data["refresh"])
        self.assertNotEqual(outstanding_a.jti, outstanding_b.jti)
        self.assertEqual(
            OutstandingToken.objects.filter(user=self.user).count(),
            2,
        )
        self.assertEqual(
            BlacklistedToken.objects.filter(token__user=self.user).count(),
            1,
        )
        self.assertFalse(BlacklistedToken.objects.filter(token=outstanding_b).exists())

        state_before_replays = {
            "user": User.objects.values(
                "password", "is_active", "is_superuser", "auth_version"
            ).get(pk=self.user.pk),
            "memberships": list(
                BusinessMembership.objects.filter(user=self.user)
                .order_by("pk")
                .values()
            ),
            "employees": list(
                Employee.objects.filter(business=self.business)
                .order_by("pk")
                .values()
            ),
            "outstanding": OutstandingToken.objects.filter(user=self.user).count(),
            "blacklisted": BlacklistedToken.objects.filter(
                token__user=self.user
            ).count(),
        }
        for _ in range(3):
            repeated = self.client.post(
                reverse("token_logout"),
                {"refresh": login_a.data["refresh"]},
                format="json",
            )
            self.assertEqual(repeated.status_code, status.HTTP_204_NO_CONTENT)
            reused_refresh = self.client.post(
                reverse("token_refresh"),
                {"refresh": login_a.data["refresh"]},
                format="json",
            )
            self.assertEqual(
                reused_refresh.status_code,
                status.HTTP_401_UNAUTHORIZED,
            )
            self.assertEqual(
                {
                    "user": User.objects.values(
                        "password", "is_active", "is_superuser", "auth_version"
                    ).get(pk=self.user.pk),
                    "memberships": list(
                        BusinessMembership.objects.filter(user=self.user)
                        .order_by("pk")
                        .values()
                    ),
                    "employees": list(
                        Employee.objects.filter(business=self.business)
                        .order_by("pk")
                        .values()
                    ),
                    "outstanding": OutstandingToken.objects.filter(
                        user=self.user
                    ).count(),
                    "blacklisted": BlacklistedToken.objects.filter(
                        token__user=self.user
                    ).count(),
                },
                state_before_replays,
            )

        self.user.refresh_from_db()
        self.assertEqual(self.user.auth_version, current_version)
        self.assertFalse(BlacklistedToken.objects.filter(token=outstanding_b).exists())
        self.assert_access_status(login_b.data["access"], status.HTTP_200_OK)

        login_b2 = self.client.post(
            reverse("token_refresh"),
            {"refresh": login_b.data["refresh"]},
            format="json",
        )
        self.assertEqual(login_b2.status_code, status.HTTP_200_OK)
        self.assertEqual(set(login_b2.data), {"access", "refresh"})
        self.assertEqual(
            AccessToken(login_b2.data["access"])[AUTH_VERSION_CLAIM],
            current_version,
        )
        self.assertEqual(
            VersionedRefreshToken(login_b2.data["refresh"])[AUTH_VERSION_CLAIM],
            current_version,
        )
        outstanding_b2 = OutstandingToken.objects.get(
            token=login_b2.data["refresh"]
        )
        self.assertNotIn(outstanding_b2.jti, {outstanding_a.jti, outstanding_b.jti})
        self.assertTrue(BlacklistedToken.objects.filter(token=outstanding_b).exists())
        self.assertFalse(
            BlacklistedToken.objects.filter(token=outstanding_b2).exists()
        )
        self.assertEqual(
            OutstandingToken.objects.filter(user=self.user).count(),
            3,
        )
        self.assertEqual(
            BlacklistedToken.objects.filter(token__user=self.user).count(),
            2,
        )

        login_b3 = self.client.post(
            reverse("token_refresh"),
            {"refresh": login_b2.data["refresh"]},
            format="json",
        )
        self.assertEqual(login_b3.status_code, status.HTTP_200_OK)
        outstanding_b3 = OutstandingToken.objects.get(
            token=login_b3.data["refresh"]
        )
        self.assertNotIn(
            outstanding_b3.jti,
            {outstanding_a.jti, outstanding_b.jti, outstanding_b2.jti},
        )
        self.assertEqual(
            AccessToken(login_b3.data["access"])[AUTH_VERSION_CLAIM],
            current_version,
        )
        self.assertEqual(
            VersionedRefreshToken(login_b3.data["refresh"])[AUTH_VERSION_CLAIM],
            current_version,
        )
        self.assertTrue(BlacklistedToken.objects.filter(token=outstanding_b2).exists())
        self.assertFalse(BlacklistedToken.objects.filter(token=outstanding_b3).exists())
        self.assertEqual(
            OutstandingToken.objects.filter(user=self.user).count(),
            4,
        )
        self.assertEqual(
            BlacklistedToken.objects.filter(token__user=self.user).count(),
            3,
        )

        for access in (
            login_b.data["access"],
            login_b2.data["access"],
            login_b3.data["access"],
        ):
            self.assert_access_status(access, status.HTTP_200_OK)
        for consumed_refresh in (
            login_a.data["refresh"],
            login_b.data["refresh"],
            login_b2.data["refresh"],
        ):
            self.assertEqual(
                self.client.post(
                    reverse("token_refresh"),
                    {"refresh": consumed_refresh},
                    format="json",
                ).status_code,
                status.HTTP_401_UNAUTHORIZED,
            )

        login_b4 = self.client.post(
            reverse("token_refresh"),
            {"refresh": login_b3.data["refresh"]},
            format="json",
        )
        self.assertEqual(login_b4.status_code, status.HTTP_200_OK)
        outstanding_b4 = OutstandingToken.objects.get(
            token=login_b4.data["refresh"]
        )
        self.assertTrue(BlacklistedToken.objects.filter(token=outstanding_b3).exists())
        self.assertFalse(BlacklistedToken.objects.filter(token=outstanding_b4).exists())
        self.assertEqual(
            OutstandingToken.objects.filter(user=self.user).count(),
            5,
        )
        self.assertEqual(
            BlacklistedToken.objects.filter(token__user=self.user).count(),
            4,
        )
        self.assert_access_status(login_b4.data["access"], status.HTTP_200_OK)
        self.assertEqual(
            VersionedRefreshToken(login_b4.data["refresh"])[AUTH_VERSION_CLAIM],
            current_version,
        )

        current_user = User.objects.values(
            "password", "is_active", "is_superuser", "auth_version"
        ).get(pk=self.user.pk)
        self.assertEqual(current_user["password"], original_user["password"])
        self.assertEqual(current_user["is_active"], original_user["is_active"])
        self.assertEqual(
            current_user["is_superuser"], original_user["is_superuser"]
        )
        self.assertEqual(current_user["auth_version"], current_version)
        self.assertEqual(
            list(
                BusinessMembership.objects.filter(user=self.user)
                .order_by("pk")
                .values()
            ),
            memberships,
        )
        self.assertEqual(
            list(
                Employee.objects.filter(business=self.business)
                .order_by("pk")
                .values()
            ),
            employees,
        )
        self.assertEqual(
            {
                "users": User.objects.count(),
                "memberships": BusinessMembership.objects.count(),
                "employees": Employee.objects.count(),
            },
            original_totals,
        )

    def test_invalid_auth_version_claim_matrix(self):
        invalid_values = (
            ("missing", _MISSING_CLAIM),
            ("null", None),
            ("numeric-string", "1"),
            ("arbitrary-string", "invalid"),
            ("true", True),
            ("false", False),
            ("negative", -1),
            ("future", self.user.auth_version + 1),
            ("object", {"version": self.user.auth_version}),
            ("list", [self.user.auth_version]),
        )
        membership_snapshot = list(
            BusinessMembership.objects.filter(user=self.user)
            .order_by("pk")
            .values()
        )
        employee_snapshot = list(
            Employee.objects.filter(business=self.business).order_by("pk").values()
        )

        for label, invalid_value in invalid_values:
            with self.subTest(value=label):
                access = AccessToken.for_user(self.user)
                refresh = VersionedRefreshToken.for_user(self.user)
                raw_access = self._set_auth_version_claim(access, invalid_value)
                raw_refresh = self._set_auth_version_claim(refresh, invalid_value)
                user_snapshot = User.objects.values(
                    "password", "is_active", "auth_version"
                ).get(pk=self.user.pk)
                blacklist_count = BlacklistedToken.objects.filter(
                    token__user=self.user
                ).count()
                outstanding = OutstandingToken.objects.get(jti=refresh["jti"])

                self.assert_access_status(raw_access, status.HTTP_401_UNAUTHORIZED)
                self.assertEqual(
                    self.client.post(
                        reverse("token_refresh"),
                        {"refresh": raw_refresh},
                        format="json",
                    ).status_code,
                    status.HTTP_401_UNAUTHORIZED,
                )
                self.assertEqual(
                    self.client.post(
                        reverse("token_logout"),
                        {"refresh": raw_refresh},
                        format="json",
                    ).status_code,
                    status.HTTP_401_UNAUTHORIZED,
                )
                self.assertEqual(
                    User.objects.values(
                        "password", "is_active", "auth_version"
                    ).get(pk=self.user.pk),
                    user_snapshot,
                )
                self.assertFalse(
                    BlacklistedToken.objects.filter(token=outstanding).exists()
                )
                self.assertEqual(
                    BlacklistedToken.objects.filter(token__user=self.user).count(),
                    blacklist_count,
                )
                self.assertEqual(
                    list(
                        BusinessMembership.objects.filter(user=self.user)
                        .order_by("pk")
                        .values()
                    ),
                    membership_snapshot,
                )
                self.assertEqual(
                    list(
                        Employee.objects.filter(business=self.business)
                        .order_by("pk")
                        .values()
                    ),
                    employee_snapshot,
                )

    def test_refresh_rejects_malformed_expired_inactive_and_old_version(self):
        pair = self.login()
        malformed = self.client.post(
            reverse("token_refresh"),
            {"refresh": "not-a-token"},
            format="json",
        )
        self.assertEqual(malformed.status_code, status.HTTP_401_UNAUTHORIZED)

        expired = VersionedRefreshToken.for_user(self.user)
        expired.set_exp(lifetime=timedelta(minutes=-5))
        expired_response = self.client.post(
            reverse("token_refresh"),
            {"refresh": str(expired)},
            format="json",
        )
        self.assertEqual(expired_response.status_code, status.HTTP_401_UNAUTHORIZED)

        self.user.is_active = False
        self.user.save(update_fields=["is_active"])
        inactive = self.client.post(
            reverse("token_refresh"),
            {"refresh": pair.data["refresh"]},
            format="json",
        )
        self.assertEqual(inactive.status_code, status.HTTP_401_UNAUTHORIZED)

        self.user.is_active = True
        self.user.auth_version += 1
        self.user.save(update_fields=["is_active", "auth_version"])
        old_version = self.client.post(
            reverse("token_refresh"),
            {"refresh": pair.data["refresh"]},
            format="json",
        )
        self.assertEqual(old_version.status_code, status.HTTP_401_UNAUTHORIZED)

    def test_logout_rejects_invalid_tokens_without_access(self):
        for refresh in ("not-a-token", str(AccessToken.for_user(self.user))):
            with self.subTest(refresh=refresh[:12]):
                response = self.client.post(
                    reverse("token_logout"),
                    {"refresh": refresh},
                    format="json",
                )
                self.assertEqual(response.status_code, status.HTTP_401_UNAUTHORIZED)

    def test_password_change_is_atomic_and_revokes_sessions(self):
        login = self.login()
        membership_snapshot = BusinessMembership.objects.values().get(
            user=self.user,
            business=self.business,
        )
        client = self.authenticated_client(login.data["access"])
        changed = client.post(
            reverse("user-change-password"),
            {
                "current_password": self.password,
                "new_password": "ChangedSecurePassword-2!",
            },
            format="json",
        )
        self.assertEqual(changed.status_code, status.HTTP_204_NO_CONTENT)
        self.assert_access_status(login.data["access"], status.HTTP_401_UNAUTHORIZED)
        old_refresh = self.client.post(
            reverse("token_refresh"),
            {"refresh": login.data["refresh"]},
            format="json",
        )
        self.assertEqual(old_refresh.status_code, status.HTTP_401_UNAUTHORIZED)
        self.assertEqual(self.login().status_code, status.HTTP_401_UNAUTHORIZED)
        self.assertEqual(
            self.login(password="ChangedSecurePassword-2!").status_code,
            status.HTTP_200_OK,
        )
        self.assertEqual(
            BusinessMembership.objects.values().get(
                user=self.user,
                business=self.business,
            ),
            membership_snapshot,
        )

    def test_password_change_validation_has_no_partial_effects(self):
        login = self.login()
        client = self.authenticated_client(login.data["access"])
        before = User.objects.values("password", "auth_version").get(pk=self.user.pk)
        for payload in (
            {"current_password": "wrong", "new_password": "ValidPassword-9!"},
            {"current_password": self.password, "new_password": "12345678"},
        ):
            with self.subTest(payload=payload):
                response = client.post(
                    reverse("user-change-password"),
                    payload,
                    format="json",
                )
                self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
                self.assertEqual(
                    User.objects.values("password", "auth_version").get(pk=self.user.pk),
                    before,
                )

    def test_password_change_rolls_back_when_revocation_fails(self):
        login = self.login()
        client = self.authenticated_client(login.data["access"])
        before = User.objects.values("password", "auth_version").get(pk=self.user.pk)
        self.client.raise_request_exception = False
        with patch(
            "core.services.auth_sessions._blacklist_locked_tokens",
            side_effect=RuntimeError("forced revocation failure"),
        ):
            client.raise_request_exception = False
            failed = client.post(
                reverse("user-change-password"),
                {
                    "current_password": self.password,
                    "new_password": "ChangedSecurePassword-8!",
                },
                format="json",
            )
        self.assertEqual(failed.status_code, status.HTTP_500_INTERNAL_SERVER_ERROR)
        self.assertEqual(
            User.objects.values("password", "auth_version").get(pk=self.user.pk),
            before,
        )

    def test_password_reset_revalidates_and_rolls_back_revocation_failure(self):
        login = self.login()
        uid = urlsafe_base64_encode(force_bytes(self.user.pk))
        reset_token = PasswordResetTokenGenerator().make_token(self.user)
        payload = {
            "uid": uid,
            "token": reset_token,
            "new_password": "ResetSecurePassword-3!",
        }
        before = User.objects.values("password", "auth_version").get(pk=self.user.pk)
        self.client.raise_request_exception = False
        with patch(
            "core.services.auth_sessions._blacklist_locked_tokens",
            side_effect=RuntimeError("forced revocation failure"),
        ):
            failed = self.client.post(
                reverse("password-reset-confirm"),
                payload,
                format="json",
            )
        self.assertEqual(failed.status_code, status.HTTP_500_INTERNAL_SERVER_ERROR)
        self.assertEqual(
            User.objects.values("password", "auth_version").get(pk=self.user.pk),
            before,
        )

        succeeded = self.client.post(
            reverse("password-reset-confirm"),
            payload,
            format="json",
        )
        self.assertEqual(succeeded.status_code, status.HTTP_204_NO_CONTENT)
        self.assert_access_status(login.data["access"], status.HTTP_401_UNAUTHORIZED)
        old_refresh = self.client.post(
            reverse("token_refresh"),
            {"refresh": login.data["refresh"]},
            format="json",
        )
        self.assertEqual(old_refresh.status_code, status.HTTP_401_UNAUTHORIZED)
        self.assertEqual(
            self.login(password="ResetSecurePassword-3!").status_code,
            status.HTTP_200_OK,
        )

    def test_reset_request_and_invalid_confirmation_contract(self):
        request_url = reverse("password-reset-request")
        existing = self.client.post(
            request_url,
            {"email": self.user.email},
            format="json",
        )
        missing = self.client.post(
            request_url,
            {"email": "missing@playnow.test"},
            format="json",
        )
        self.assertEqual(existing.status_code, status.HTTP_200_OK)
        self.assertEqual(missing.status_code, status.HTTP_200_OK)
        invalid = self.client.post(
            reverse("password-reset-confirm"),
            {"uid": "bad", "token": "bad", "new_password": "ValidPassword-4!"},
            format="json",
        )
        self.assertEqual(invalid.status_code, status.HTTP_400_BAD_REQUEST)

    def test_openapi_documents_logout_without_internal_claims(self):
        schema = SchemaGenerator().get_schema(request=None, public=True)
        operation = schema["paths"]["/api/logout/"]["post"]
        self.assertEqual(set(operation["responses"]), {"204", "401"})
        self.assertEqual(operation.get("security"), [{}])
        request_ref = operation["requestBody"]["content"]["application/json"][
            "schema"
        ]["$ref"]
        request_schema = schema["components"]["schemas"][request_ref.rsplit("/", 1)[1]]
        self.assertEqual(set(request_schema["properties"]), {"refresh"})
        self.assertTrue(request_schema["properties"]["refresh"]["writeOnly"])
        self.assertFalse(self.contains_auth_version(schema))

    def test_auth_version_schema_detector(self):
        self.assertFalse(self.contains_auth_version({"nested": ["safe"]}))
        self.assertTrue(self.contains_auth_version({AUTH_VERSION_CLAIM: "hidden"}))
        self.assertTrue(
            self.contains_auth_version({"nested": [{AUTH_VERSION_CLAIM: 1}]})
        )
        self.assertTrue(
            self.contains_auth_version({"nested": [AUTH_VERSION_CLAIM]})
        )
