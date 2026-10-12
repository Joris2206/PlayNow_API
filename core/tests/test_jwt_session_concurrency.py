from concurrent.futures import ThreadPoolExecutor
from contextlib import ExitStack
from threading import Barrier, Event
from time import monotonic
from unittest import skipUnless
from unittest.mock import patch

from django.conf import settings
from django.contrib.auth.tokens import PasswordResetTokenGenerator
from django.db import (
    DatabaseError,
    close_old_connections,
    connection,
    connections,
    transaction,
)
from django.test import TransactionTestCase, override_settings
from django.urls import reverse
from django.utils.encoding import force_bytes
from django.utils.http import urlsafe_base64_encode
from rest_framework.test import APIClient
from rest_framework_simplejwt.token_blacklist.models import (
    BlacklistedToken,
    OutstandingToken,
)
from rest_framework_simplejwt.tokens import AccessToken

from core.api.serializers import auth as auth_serializers
from core.api.views import auth as auth_views
from core.models import User
from core.services import auth_sessions
from core.api.views.auth import LoginView
from core.tests.factories import create_user


TEST_REST_FRAMEWORK = {
    **settings.REST_FRAMEWORK,
    "DEFAULT_THROTTLE_RATES": {
        **settings.REST_FRAMEWORK["DEFAULT_THROTTLE_RATES"],
        "login": "1000/min",
        "auth_reset_confirm": "1000/min",
    },
}


@skipUnless(connection.vendor == "postgresql", "Requires PostgreSQL row locks")
@override_settings(REST_FRAMEWORK=TEST_REST_FRAMEWORK)
@patch.object(LoginView, "throttle_classes", [])
class JWTSessionConcurrencyTests(TransactionTestCase):
    reset_sequences = True
    password = "ConcurrentJWTPassword-1!"

    def setUp(self):
        self.user = create_user(
            email="jwt-concurrency@playnow.test",
            password=self.password,
        )

    def login(self):
        client = APIClient()
        response = client.post(
            reverse("token_obtain_pair"),
            {"email": self.user.email, "password": self.password},
            format="json",
        )
        self.assertEqual(response.status_code, 200)
        return dict(response.data)

    @staticmethod
    def _request(method, path, payload, access, barrier):
        close_old_connections()
        try:
            client = APIClient()
            if access is not None:
                client.credentials(HTTP_AUTHORIZATION=f"Bearer {access}")
            barrier.wait(timeout=15)
            response = getattr(client, method)(path, payload, format="json")
            data = dict(response.data) if response.data is not None else {}
            return response.status_code, data
        finally:
            close_old_connections()
            connections.close_all()

    def _concurrent(self, first, second):
        barrier = Barrier(2)
        with ThreadPoolExecutor(max_workers=2) as executor:
            futures = [
                executor.submit(self._request, *first, barrier),
                executor.submit(self._request, *second, barrier),
            ]
            return [future.result(timeout=30) for future in futures]

    def _ordered_requests(self, first, second, *, owner, attribute):
        """Hold the second request immediately before its production service."""
        second_waiting = Event()
        release_second = Event()
        original = getattr(owner, attribute)

        def wait_then_call_service(*args, **kwargs):
            second_waiting.set()
            if not release_second.wait(timeout=15):
                raise TimeoutError(f"Timed out before {attribute}")
            return original(*args, **kwargs)

        close_old_connections()
        try:
            with patch.object(owner, attribute, side_effect=wait_then_call_service):
                with ThreadPoolExecutor(max_workers=2) as executor:
                    second_future = executor.submit(
                        self._request_without_barrier,
                        second,
                    )
                    self.assertTrue(
                        second_waiting.wait(timeout=15),
                        f"{attribute} did not reach the production boundary",
                    )
                    first_future = executor.submit(
                        self._request_without_barrier,
                        first,
                    )
                    first_result = first_future.result(timeout=30)
                    self.assertFalse(second_future.done())
                    release_second.set()
                    second_result = second_future.result(timeout=30)
            return first_result, second_result
        finally:
            release_second.set()
            close_old_connections()

    @staticmethod
    def _refresh_call(refresh):
        return (
            "post",
            reverse("token_refresh"),
            {"refresh": refresh},
            None,
        )

    @staticmethod
    def _logout_call(refresh):
        return (
            "post",
            reverse("token_logout"),
            {"refresh": refresh},
            None,
        )

    def assert_access_invalid(self, access):
        client = APIClient()
        client.credentials(HTTP_AUTHORIZATION=f"Bearer {access}")
        self.assertEqual(client.get(reverse("current-user")).status_code, 401)

    def assert_access_valid(self, access):
        client = APIClient()
        client.credentials(HTTP_AUTHORIZATION=f"Bearer {access}")
        self.assertEqual(client.get(reverse("current-user")).status_code, 200)

    def _login_call(self):
        return (
            "post",
            reverse("token_obtain_pair"),
            {"email": self.user.email, "password": self.password},
            None,
        )

    def _change_password_call(self, pair):
        return (
            "post",
            reverse("user-change-password"),
            {
                "current_password": self.password,
                "new_password": "ConcurrentChangedPassword-2!",
            },
            pair["access"],
        )

    def _reset_password_call(self):
        uid = urlsafe_base64_encode(force_bytes(self.user.pk))
        reset_token = PasswordResetTokenGenerator().make_token(self.user)
        return (
            "post",
            reverse("password-reset-confirm"),
            {
                "uid": uid,
                "token": reset_token,
                "new_password": "ConcurrentResetPassword-3!",
            },
            None,
        )

    def _create_old_logout_token(self):
        pair = self.login()
        response = APIClient().post(
            reverse("token_logout"),
            {"refresh": pair["refresh"]},
            format="json",
        )
        self.assertEqual(response.status_code, 204)
        self.user.refresh_from_db()
        return pair["refresh"], self.user.auth_version

    def _assert_request_waits_for_user_lock(
        self,
        call,
        *,
        owner,
        attribute,
        downstream_attribute,
    ):
        """Prove the real HTTP request waits on the production User row lock."""
        lock_acquired = Event()
        release_lock = Event()
        service_entered = Event()
        downstream_reached = Event()
        holder_pid = {}
        request_pid = {}
        original = getattr(owner, attribute)
        downstream_original = getattr(auth_sessions, downstream_attribute)

        def hold_user_lock():
            close_old_connections()
            try:
                with transaction.atomic():
                    with connection.cursor() as cursor:
                        cursor.execute("SELECT pg_backend_pid()")
                        holder_pid["value"] = cursor.fetchone()[0]
                    User.objects.select_for_update().get(pk=self.user.pk)
                    lock_acquired.set()
                    if not release_lock.wait(timeout=15):
                        raise TimeoutError("Timed out while holding the User row lock")
            finally:
                close_old_connections()
                connections.close_all()

        def call_real_service(*args, **kwargs):
            with connection.cursor() as cursor:
                cursor.execute("SELECT pg_backend_pid()")
                request_pid["value"] = cursor.fetchone()[0]
            service_entered.set()
            return original(*args, **kwargs)

        def observe_downstream(*args, **kwargs):
            downstream_reached.set()
            return downstream_original(*args, **kwargs)

        close_old_connections()
        request_future = None
        holder_future = None
        try:
            with ExitStack() as stack:
                executor = stack.enter_context(ThreadPoolExecutor(max_workers=2))
                stack.callback(release_lock.set)
                holder_future = executor.submit(hold_user_lock)
                self.assertTrue(
                    lock_acquired.wait(timeout=15),
                    "Thread A did not acquire the User row lock",
                )
                with (
                    patch.object(owner, attribute, side_effect=call_real_service),
                    patch.object(
                        auth_sessions,
                        downstream_attribute,
                        side_effect=observe_downstream,
                    ),
                ):
                    request_future = executor.submit(
                        self._request_without_barrier,
                        call,
                    )
                    self.assertTrue(
                        service_entered.wait(timeout=15),
                        f"{attribute} did not enter the real production service",
                    )

                    blocking_state = None
                    deadline = monotonic() + 5
                    while monotonic() < deadline:
                        with connection.cursor() as cursor:
                            cursor.execute(
                                """
                                SELECT pg_blocking_pids(%s), wait_event_type,
                                       wait_event
                                FROM pg_stat_activity
                                WHERE pid = %s
                                """,
                                [request_pid["value"], request_pid["value"]],
                            )
                            blocking_state = cursor.fetchone()
                        if (
                            blocking_state
                            and holder_pid["value"] in blocking_state[0]
                            and blocking_state[1] == "Lock"
                            and blocking_state[2] is not None
                        ):
                            break
                        if request_future.done():
                            release_lock.set()
                            self.fail(
                                f"{attribute} completed without waiting for the "
                                "locked User row"
                            )
                        release_lock.wait(timeout=0.05)
                    else:
                        release_lock.set()
                        self.fail(
                            f"PostgreSQL did not report {attribute} as blocked "
                            "by the User row-lock holder"
                        )

                    self.assertFalse(request_future.done())
                    if downstream_reached.is_set():
                        release_lock.set()
                        self.fail(
                            f"{attribute} passed its User lock before PostgreSQL "
                            "released the row"
                        )
                    release_lock.set()
                    holder_future.result(timeout=15)
                    return request_future.result(timeout=30)
        finally:
            release_lock.set()
            if holder_future is not None and holder_future.done():
                holder_future.result()
            close_old_connections()

    def test_login_waits_for_real_user_row_lock(self):
        status_code, data = self._assert_request_waits_for_user_lock(
            self._login_call(),
            owner=auth_serializers,
            attribute="login_and_issue_tokens",
            downstream_attribute="issue_token_pair",
        )
        self.assertEqual(status_code, 200)
        self.assertEqual(set(data), {"access", "refresh"})
        self.assert_access_valid(data["access"])

    def test_logout_waits_for_real_user_row_lock(self):
        pair = self.login()
        status_code, _ = self._assert_request_waits_for_user_lock(
            self._logout_call(pair["refresh"]),
            owner=auth_serializers,
            attribute="logout_user_sessions",
            downstream_attribute="_revoke_locked_user_sessions",
        )
        self.assertEqual(status_code, 204)
        self.assert_access_invalid(pair["access"])
        self.assertEqual(
            APIClient().post(
                reverse("token_refresh"),
                {"refresh": pair["refresh"]},
                format="json",
            ).status_code,
            401,
        )

    def test_password_change_waits_for_real_user_row_lock(self):
        pair = self.login()
        status_code, _ = self._assert_request_waits_for_user_lock(
            self._change_password_call(pair),
            owner=auth_views,
            attribute="change_user_password",
            downstream_attribute="_revoke_locked_user_sessions",
        )
        self.assertEqual(status_code, 204)
        self.assert_access_invalid(pair["access"])
        self.assertEqual(
            APIClient().post(
                reverse("token_obtain_pair"),
                {"email": self.user.email, "password": self.password},
                format="json",
            ).status_code,
            401,
        )
        self.assertEqual(
            APIClient().post(
                reverse("token_obtain_pair"),
                {
                    "email": self.user.email,
                    "password": "ConcurrentChangedPassword-2!",
                },
                format="json",
            ).status_code,
            200,
        )

    def test_password_reset_waits_for_real_user_row_lock(self):
        pair = self.login()
        status_code, _ = self._assert_request_waits_for_user_lock(
            self._reset_password_call(),
            owner=auth_serializers,
            attribute="reset_user_password",
            downstream_attribute="_revoke_locked_user_sessions",
        )
        self.assertEqual(status_code, 204)
        self.assert_access_invalid(pair["access"])
        self.assertEqual(
            APIClient().post(
                reverse("token_obtain_pair"),
                {"email": self.user.email, "password": self.password},
                format="json",
            ).status_code,
            401,
        )
        self.assertEqual(
            APIClient().post(
                reverse("token_obtain_pair"),
                {
                    "email": self.user.email,
                    "password": "ConcurrentResetPassword-3!",
                },
                format="json",
            ).status_code,
            200,
        )

    def test_refresh_waits_for_real_user_row_lock(self):
        pair = self.login()
        status_code, data = self._assert_request_waits_for_user_lock(
            self._refresh_call(pair["refresh"]),
            owner=auth_serializers,
            attribute="rotate_refresh_token",
            downstream_attribute="issue_token_pair",
        )
        self.assertEqual(status_code, 200)
        self.assertEqual(set(data), {"access", "refresh"})
        self.assertEqual(
            APIClient().post(
                reverse("token_refresh"),
                {"refresh": pair["refresh"]},
                format="json",
            ).status_code,
            401,
        )
        self.assert_access_valid(data["access"])
        self.assertEqual(
            APIClient().post(
                reverse("token_refresh"),
                {"refresh": data["refresh"]},
                format="json",
            ).status_code,
            200,
        )

    def test_same_refresh_is_single_use_under_concurrency(self):
        for _ in range(3):
            pair = self.login()
            results = self._concurrent(
                self._refresh_call(pair["refresh"]),
                self._refresh_call(pair["refresh"]),
            )
            self.assertEqual(sorted(status for status, _ in results), [200, 401])
            winner = next(data for status, data in results if status == 200)
            self.assertEqual(set(winner), {"access", "refresh"})
            old_jti = AccessToken(pair["access"])["jti"]
            self.assertTrue(
                BlacklistedToken.objects.filter(
                    token__user=self.user,
                    token__jti=OutstandingToken.objects.get(
                        token=pair["refresh"]
                    ).jti,
                ).exists()
            )
            self.assertNotEqual(old_jti, AccessToken(winner["access"])["jti"])
            self.assertEqual(
                APIClient().post(
                    reverse("token_refresh"),
                    {"refresh": winner["refresh"]},
                    format="json",
                ).status_code,
                200,
            )

    def test_refresh_holds_outstanding_lock_until_commit(self):
        pair = self.login()
        outstanding = OutstandingToken.objects.get(token=pair["refresh"])
        insert_reached = Event()
        release_insert = Event()
        original_create = BlacklistedToken.objects.create

        def create_after_observation(*args, **kwargs):
            insert_reached.set()
            if not release_insert.wait(timeout=15):
                raise TimeoutError("Timed out while observing the row lock")
            return original_create(*args, **kwargs)

        close_old_connections()
        try:
            with patch.object(
                BlacklistedToken.objects,
                "create",
                side_effect=create_after_observation,
            ):
                with ThreadPoolExecutor(max_workers=1) as executor:
                    future = executor.submit(
                        self._request_without_barrier,
                        self._refresh_call(pair["refresh"]),
                    )
                    self.assertTrue(insert_reached.wait(timeout=15))
                    with self.assertRaises(DatabaseError):
                        with transaction.atomic():
                            OutstandingToken.objects.select_for_update(
                                nowait=True
                            ).get(pk=outstanding.pk)
                    release_insert.set()
                    status_code, _ = future.result(timeout=30)
            self.assertEqual(status_code, 200)
        finally:
            release_insert.set()
            close_old_connections()

    @classmethod
    def _request_without_barrier(cls, call):
        method, path, payload, access = call
        close_old_connections()
        try:
            client = APIClient()
            if access is not None:
                client.credentials(HTTP_AUTHORIZATION=f"Bearer {access}")
            response = getattr(client, method)(path, payload, format="json")
            data = dict(response.data) if response.data is not None else {}
            return response.status_code, data
        finally:
            close_old_connections()
            connections.close_all()

    def test_refresh_commits_before_logout(self):
        pair = self.login()
        refresh_result, logout_result = self._ordered_requests(
            self._refresh_call(pair["refresh"]),
            self._logout_call(pair["refresh"]),
            owner=auth_serializers,
            attribute="logout_user_sessions",
        )
        self.assertEqual(refresh_result[0], 200)
        self.assertEqual(logout_result[0], 204)
        self.assert_access_invalid(refresh_result[1]["access"])
        self.assertEqual(
            APIClient().post(
                reverse("token_refresh"),
                {"refresh": refresh_result[1]["refresh"]},
                format="json",
            ).status_code,
            401,
        )

    def test_logout_commits_before_refresh(self):
        pair = self.login()
        logout_result, refresh_result = self._ordered_requests(
            self._logout_call(pair["refresh"]),
            self._refresh_call(pair["refresh"]),
            owner=auth_serializers,
            attribute="rotate_refresh_token",
        )
        self.assertEqual(logout_result[0], 204)
        self.assertEqual(refresh_result[0], 401)
        self.assert_access_invalid(pair["access"])

    def test_login_commits_before_logout(self):
        existing = self.login()
        login_result, logout_result = self._ordered_requests(
            self._login_call(),
            self._logout_call(existing["refresh"]),
            owner=auth_serializers,
            attribute="logout_user_sessions",
        )
        self.assertEqual(login_result[0], 200)
        self.assertEqual(logout_result[0], 204)
        self.assert_access_invalid(login_result[1]["access"])
        self.assertEqual(
            APIClient().post(
                reverse("token_refresh"),
                {"refresh": login_result[1]["refresh"]},
                format="json",
            ).status_code,
            401,
        )

    def test_logout_commits_before_login(self):
        existing = self.login()
        logout_result, login_result = self._ordered_requests(
            self._logout_call(existing["refresh"]),
            self._login_call(),
            owner=auth_serializers,
            attribute="login_and_issue_tokens",
        )
        self.assertEqual(logout_result[0], 204)
        self.assertEqual(login_result[0], 200)
        self.assert_access_valid(login_result[1]["access"])
        self.assertEqual(
            APIClient().post(
                reverse("token_refresh"),
                {"refresh": login_result[1]["refresh"]},
                format="json",
            ).status_code,
            200,
        )

    def test_refresh_commits_before_password_change(self):
        pair = self.login()
        refresh_result, change_result = self._ordered_requests(
            self._refresh_call(pair["refresh"]),
            self._change_password_call(pair),
            owner=auth_views,
            attribute="change_user_password",
        )
        self.assertEqual(refresh_result[0], 200)
        self.assertEqual(change_result[0], 204)
        self.assert_access_invalid(refresh_result[1]["access"])
        self.assertEqual(
            APIClient().post(
                reverse("token_refresh"),
                {"refresh": refresh_result[1]["refresh"]},
                format="json",
            ).status_code,
            401,
        )

    def test_password_change_commits_before_refresh(self):
        pair = self.login()
        change_result, refresh_result = self._ordered_requests(
            self._change_password_call(pair),
            self._refresh_call(pair["refresh"]),
            owner=auth_serializers,
            attribute="rotate_refresh_token",
        )
        self.assertEqual(change_result[0], 204)
        self.assertEqual(refresh_result[0], 401)
        self.assert_access_invalid(pair["access"])

    def test_refresh_commits_before_password_reset(self):
        pair = self.login()
        refresh_result, reset_result = self._ordered_requests(
            self._refresh_call(pair["refresh"]),
            self._reset_password_call(),
            owner=auth_serializers,
            attribute="reset_user_password",
        )
        self.assertEqual(refresh_result[0], 200)
        self.assertEqual(reset_result[0], 204)
        self.assert_access_invalid(refresh_result[1]["access"])
        self.assertEqual(
            APIClient().post(
                reverse("token_refresh"),
                {"refresh": refresh_result[1]["refresh"]},
                format="json",
            ).status_code,
            401,
        )

    def test_password_reset_commits_before_refresh(self):
        pair = self.login()
        reset_result, refresh_result = self._ordered_requests(
            self._reset_password_call(),
            self._refresh_call(pair["refresh"]),
            owner=auth_serializers,
            attribute="rotate_refresh_token",
        )
        self.assertEqual(reset_result[0], 204)
        self.assertEqual(refresh_result[0], 401)
        self.assert_access_invalid(pair["access"])

    def test_old_logout_commits_before_new_login(self):
        old_refresh, current_version = self._create_old_logout_token()
        logout_result, login_result = self._ordered_requests(
            self._logout_call(old_refresh),
            self._login_call(),
            owner=auth_serializers,
            attribute="login_and_issue_tokens",
        )
        self.assertEqual(logout_result[0], 204)
        self.assertEqual(login_result[0], 200)
        self.user.refresh_from_db()
        self.assertEqual(self.user.auth_version, current_version)
        self.assert_access_valid(login_result[1]["access"])
        outstanding = OutstandingToken.objects.get(token=login_result[1]["refresh"])
        self.assertFalse(BlacklistedToken.objects.filter(token=outstanding).exists())

    def test_new_login_commits_before_old_logout(self):
        old_refresh, current_version = self._create_old_logout_token()
        login_result, logout_result = self._ordered_requests(
            self._login_call(),
            self._logout_call(old_refresh),
            owner=auth_serializers,
            attribute="logout_user_sessions",
        )
        self.assertEqual(login_result[0], 200)
        self.assertEqual(logout_result[0], 204)
        self.user.refresh_from_db()
        self.assertEqual(self.user.auth_version, current_version)
        self.assert_access_valid(login_result[1]["access"])
        outstanding = OutstandingToken.objects.get(token=login_result[1]["refresh"])
        self.assertFalse(BlacklistedToken.objects.filter(token=outstanding).exists())

    def test_old_logout_commits_before_current_refresh(self):
        old_refresh, current_version = self._create_old_logout_token()
        current = self.login()
        logout_result, refresh_result = self._ordered_requests(
            self._logout_call(old_refresh),
            self._refresh_call(current["refresh"]),
            owner=auth_serializers,
            attribute="rotate_refresh_token",
        )
        self.assertEqual(logout_result[0], 204)
        self.assertEqual(refresh_result[0], 200)
        self.user.refresh_from_db()
        self.assertEqual(self.user.auth_version, current_version)
        self.assert_access_valid(refresh_result[1]["access"])
        outstanding = OutstandingToken.objects.get(
            token=refresh_result[1]["refresh"]
        )
        self.assertFalse(BlacklistedToken.objects.filter(token=outstanding).exists())

    def test_current_refresh_commits_before_old_logout(self):
        old_refresh, current_version = self._create_old_logout_token()
        current = self.login()
        refresh_result, logout_result = self._ordered_requests(
            self._refresh_call(current["refresh"]),
            self._logout_call(old_refresh),
            owner=auth_serializers,
            attribute="logout_user_sessions",
        )
        self.assertEqual(refresh_result[0], 200)
        self.assertEqual(logout_result[0], 204)
        self.user.refresh_from_db()
        self.assertEqual(self.user.auth_version, current_version)
        self.assert_access_valid(refresh_result[1]["access"])
        outstanding = OutstandingToken.objects.get(
            token=refresh_result[1]["refresh"]
        )
        self.assertFalse(BlacklistedToken.objects.filter(token=outstanding).exists())

    def test_two_concurrent_logouts_increment_version_once(self):
        pair = self.login()
        before = self.user.auth_version
        results = self._concurrent(
            self._logout_call(pair["refresh"]),
            self._logout_call(pair["refresh"]),
        )
        self.assertEqual(sorted(status for status, _ in results), [204, 204])
        self.user.refresh_from_db()
        self.assertEqual(self.user.auth_version, before + 1)
        self.assert_access_invalid(pair["access"])
        outstanding = OutstandingToken.objects.filter(user=self.user).count()
        blacklisted = BlacklistedToken.objects.filter(token__user=self.user).count()
        self.assertEqual(blacklisted, outstanding)
