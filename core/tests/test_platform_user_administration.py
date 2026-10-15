from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
from threading import Barrier, Event, Lock
from time import monotonic
from unittest.mock import patch

from django.db import (
    IntegrityError,
    close_old_connections,
    connection,
    connections,
    transaction,
)
from django.test import TransactionTestCase
from django.urls import resolve, reverse
from drf_spectacular.generators import SchemaGenerator
from rest_framework import status
from rest_framework.test import APIClient, APITestCase
from rest_framework_simplejwt.token_blacklist.models import (
    BlacklistedToken,
    OutstandingToken,
)

from core.api.serializers.platform_users import (
    EmptyPlatformUserActionSerializer,
    PlatformUserPasswordResetSerializer,
)
from core.api.views.auth import LoginView
from core.api.views.platform_users import PlatformUserActionView
from core.models import BusinessMembership, Employee, User
from core.permissions import IsActivePlatformAdmin
from core.services.multi_user_write_lock import acquire_multi_user_write_lock
from core.services.platform_users import (
    _lock_platform_admin,
    activate_platform_user,
    deactivate_platform_user,
    reset_platform_user_password,
    revoke_platform_user_sessions,
)
from core.tests.factories import (
    create_business,
    create_membership,
    create_role_user,
    create_status,
    create_user,
)
from core.tokens import VersionedRefreshToken


RESPONSE_FIELDS = {
    "public_id",
    "email",
    "full_name",
    "is_active",
    "is_superuser",
    "created_at",
    "updated_at",
}

PROHIBITED_PLATFORM_USER_FIELDS = (
    "public_id", "email", "full_name", "is_active", "is_superuser",
    "is_staff", "groups", "user_permissions", "role", "auth_version",
    "memberships", "membership", "employee", "employee_public_id",
    "business", "business_public_id", "created_at", "updated_at", "id",
    "pk", "access", "refresh", "token", "password",
    "password_confirmation", "unknown",
)


@patch.object(LoginView, "throttle_classes", [])
@patch.object(PlatformUserActionView, "throttle_classes", [])
class PlatformUserAdministrationAPITests(APITestCase):
    password = "PlatformAdministration-1!"
    new_password = "Cobalt-River-927!Quartz"

    @classmethod
    def setUpTestData(cls):
        cls.active_status = create_status("Activo")
        cls.platform = create_user(
            email="administration-platform@playnow.test",
            full_name="Administration Platform",
            password=cls.password,
            is_superuser=True,
        )
        cls.owner = create_user(email="administration-owner@playnow.test")
        cls.business = create_business(
            user=cls.owner,
            status=cls.active_status,
        )
        cls.roles = {BusinessMembership.ROLE_OWNER: cls.owner}
        for role, _ in BusinessMembership.ROLES:
            if role != BusinessMembership.ROLE_OWNER:
                cls.roles[role] = create_role_user(
                    business=cls.business,
                    role=role,
                    status=cls.active_status,
                    email=f"administration-{role}@playnow.test",
                )[0]
        cls.target = create_user(
            email="administration-target@playnow.test",
            full_name="Administration Target",
            password=cls.password,
        )
        User.objects.filter(pk=cls.target.pk).update(is_staff=True)
        cls.target.refresh_from_db()
        cls.employee = Employee.objects.create(
            business=cls.business,
            full_name="Preserved Employee",
            position="Tester",
            status=cls.active_status,
        )
        cls.membership = create_membership(
            user=cls.target,
            business=cls.business,
            role=BusinessMembership.ROLE_VIEWER,
            employee=cls.employee,
        )

    def setUp(self):
        self.client.force_authenticate(self.platform)

    def url(self, action, user=None):
        return reverse(
            f"platform-user-{action}",
            kwargs={"public_id": (user or self.target).public_id},
        )

    @staticmethod
    def snapshot(user):
        return User.objects.values(
            "email",
            "full_name",
            "password",
            "is_active",
            "is_superuser",
            "is_staff",
            "role",
            "auth_version",
            "created_at",
            "updated_at",
        ).get(pk=user.pk)

    @staticmethod
    def _rows(model, fields=None):
        selected = fields or tuple(
            field.attname for field in model._meta.concrete_fields
        )
        return tuple(model.objects.order_by("pk").values_list(*selected))

    def complete_snapshot(self):
        models = (
            User,
            BusinessMembership,
            Employee,
            OutstandingToken,
            BlacklistedToken,
        )
        return {
            "users": self._rows(User, (
                "pk", "public_id", "email", "full_name", "password",
                "is_active", "is_staff", "is_superuser", "role",
                "auth_version", "created_at", "updated_at",
            )),
            "memberships": self._rows(BusinessMembership),
            "employees": self._rows(Employee),
            "outstanding_tokens": self._rows(OutstandingToken, (
                "pk", "user_id", "jti", "created_at", "expires_at",
            )),
            "blacklisted_tokens": self._rows(BlacklistedToken, (
                "pk", "token_id", "blacklisted_at",
            )),
            "counts": tuple((model._meta.label, model.objects.count()) for model in models),
        }

    def assert_complete_snapshot(self, expected):
        self.assertEqual(self.complete_snapshot(), expected)

    def login(self, password=None):
        return APIClient().post(
            reverse("token_obtain_pair"),
            {"email": self.target.email, "password": password or self.password},
            format="json",
        )

    def test_routes_are_uuid_post_only_and_named(self):
        names = {
            "activate": "platform-user-activate",
            "deactivate": "platform-user-deactivate",
            "reset-password": "platform-user-reset-password",
            "revoke-sessions": "platform-user-revoke-sessions",
        }
        for action, name in names.items():
            url = self.url(action)
            with self.subTest(action=action):
                self.assertEqual(resolve(url).url_name, name)
                self.assertEqual(self.client.get(url).status_code, 405)
                self.assertEqual(self.client.put(url, {}, format="json").status_code, 405)
                self.assertEqual(self.client.patch(url, {}, format="json").status_code, 405)
                self.assertEqual(self.client.delete(url).status_code, 405)
                self.assertEqual(
                    self.client.post(
                        url.replace(str(self.target.public_id), "not-a-uuid"),
                        {},
                        format="json",
                    ).status_code,
                    404,
                )

    def test_complete_authorization_and_anti_enumeration_matrix(self):
        missing_public_id = "00000000-0000-0000-0000-000000000001"
        valid_payloads = {
            "activate": {},
            "deactivate": {},
            "reset-password": {
                "password": self.new_password,
                "password_confirmation": self.new_password,
            },
            "revoke-sessions": {},
        }
        invalid_payload = {"email": "forbidden"}
        outsider = create_user(email="administration-outsider@playnow.test")

        for action, valid_payload in valid_payloads.items():
            missing = reverse(
                f"platform-user-{action}",
                kwargs={"public_id": missing_public_id},
            )
            existing = self.url(action)
            with self.subTest(action=action, case="authorized-missing"):
                before = self.complete_snapshot()
                with patch("core.services.platform_users.log_action") as audit:
                    response = self.client.post(missing, valid_payload, format="json")
                self.assertEqual(response.status_code, 404)
                self.assert_complete_snapshot(before)
                audit.assert_not_called()

            self.client.force_authenticate(outsider)
            before = self.complete_snapshot()
            with (
                patch("core.services.multi_user_write_lock.acquire_multi_user_write_lock") as advisory,
                patch("core.services.platform_users._lock_platform_user_target") as target_lock,
                patch("core.services.platform_users._revoke_locked_user_sessions") as revoke,
                patch("core.services.platform_users.log_action") as audit,
            ):
                responses = [
                    self.client.post(existing, valid_payload, format="json"),
                    self.client.post(missing, valid_payload, format="json"),
                    self.client.post(existing, invalid_payload, format="json"),
                    self.client.post(missing, invalid_payload, format="json"),
                ]
            self.assertTrue(all(response.status_code == 403 for response in responses))
            self.assertTrue(all(response.data == responses[0].data for response in responses[1:]))
            self.assertEqual(
                str(responses[0].data["detail"]),
                IsActivePlatformAdmin.message,
            )
            self.assert_complete_snapshot(before)
            advisory.assert_not_called()
            target_lock.assert_not_called()
            revoke.assert_not_called()
            audit.assert_not_called()

            for field in ("is_superuser", "is_active"):
                stale_actor = create_user(
                    email=f"stale-{action}-{field}@playnow.test",
                    is_superuser=True,
                )
                self.client.force_authenticate(stale_actor)
                User.objects.filter(pk=stale_actor.pk).update(**{field: False})
                before = self.complete_snapshot()
                with (
                    patch("core.services.multi_user_write_lock.acquire_multi_user_write_lock") as advisory,
                    patch("core.services.platform_users._lock_platform_user_target") as target_lock,
                ):
                    response = self.client.post(existing, valid_payload, format="json")
                self.assertEqual(response.status_code, 403)
                self.assert_complete_snapshot(before)
                advisory.assert_not_called()
                target_lock.assert_not_called()

            self.client.force_authenticate(None)
            before = self.complete_snapshot()
            response = self.client.post(existing, valid_payload, format="json")
            self.assertEqual(response.status_code, 401)
            self.assert_complete_snapshot(before)
            self.client.force_authenticate(self.platform)

        success_statuses = {
            "activate": 200,
            "deactivate": 200,
            "reset-password": 204,
            "revoke-sessions": 204,
        }
        for action, payload in valid_payloads.items():
            target = create_user(
                email=f"authorized-{action}@playnow.test",
                password=self.password,
            )
            if action == "activate":
                User.objects.filter(pk=target.pk).update(is_active=False)
                target.refresh_from_db()
            response = self.client.post(self.url(action, target), payload, format="json")
            self.assertEqual(response.status_code, success_statuses[action])

    def test_authorized_missing_target_is_404(self):
        missing = reverse(
            "platform-user-revoke-sessions",
            kwargs={"public_id": "00000000-0000-0000-0000-000000000001"},
        )
        before = self.complete_snapshot()
        with patch("core.services.platform_users.log_action") as audit:
            response = self.client.post(missing, {}, format="json")
        self.assertEqual(response.status_code, 404)
        self.assert_complete_snapshot(before)
        audit.assert_not_called()

    def test_empty_actions_reject_every_field_before_mutation(self):
        before = self.complete_snapshot()
        for action in ("activate", "deactivate", "revoke-sessions"):
            for field in PROHIBITED_PLATFORM_USER_FIELDS:
                with self.subTest(action=action, field=field):
                    with patch("core.services.platform_users.log_action") as audit:
                        response = self.client.post(
                            self.url(action), {field: "forbidden"}, format="json"
                        )
                    self.assertEqual(response.status_code, 400)
                    self.assertIn(field, response.data)
                    self.assert_complete_snapshot(before)
                    audit.assert_not_called()

    def test_reset_password_rejects_every_forbidden_field_without_effects(self):
        before = self.complete_snapshot()
        payload = {
            "password": self.new_password,
            "password_confirmation": self.new_password,
        }
        forbidden = tuple(
            field for field in PROHIBITED_PLATFORM_USER_FIELDS
            if field not in payload
        )
        for field in forbidden:
            with self.subTest(field=field):
                with patch("core.services.platform_users.log_action") as audit:
                    response = self.client.post(
                        self.url("reset-password"),
                        {**payload, field: "forbidden"},
                        format="json",
                    )
                self.assertEqual(response.status_code, 400)
                self.assertIn(field, response.data)
                self.assert_complete_snapshot(before)
                audit.assert_not_called()

    def test_deactivate_activate_lifecycle_is_idempotent_and_preserves_relations(self):
        pair = self.login().data
        outstanding = OutstandingToken.objects.get(token=pair["refresh"])
        membership_before = BusinessMembership.objects.values().get(pk=self.membership.pk)
        employee_before = Employee.objects.values().get(pk=self.employee.pk)
        before = self.snapshot(self.target)

        response = self.client.post(self.url("deactivate"), {}, format="json")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(set(response.data), RESPONSE_FIELDS)
        after_deactivate = self.snapshot(self.target)
        self.assertFalse(after_deactivate["is_active"])
        self.assertEqual(after_deactivate["auth_version"], before["auth_version"] + 1)
        for field in ("password", "is_superuser", "is_staff", "role", "email", "full_name"):
            self.assertEqual(after_deactivate[field], before[field])
        self.assertTrue(BlacklistedToken.objects.filter(token=outstanding).exists())
        self.assertEqual(
            APIClient().get(
                reverse("current-user"),
                HTTP_AUTHORIZATION=f"Bearer {pair['access']}",
            ).status_code,
            401,
        )
        self.assertEqual(
            APIClient().post(
                reverse("token_refresh"), {"refresh": pair["refresh"]}, format="json"
            ).status_code,
            401,
        )

        stable = self.snapshot(self.target)
        complete_stable = self.complete_snapshot()
        with patch("core.services.platform_users.log_action") as audit:
            second = self.client.post(self.url("deactivate"), {}, format="json")
        self.assertEqual(second.status_code, 200)
        self.assertEqual(self.snapshot(self.target), stable)
        self.assert_complete_snapshot(complete_stable)
        audit.assert_not_called()

        activated = self.client.post(self.url("activate"), {}, format="json")
        self.assertEqual(activated.status_code, 200)
        after_activate = self.snapshot(self.target)
        self.assertTrue(after_activate["is_active"])
        self.assertEqual(after_activate["auth_version"], stable["auth_version"])
        active_stable = self.snapshot(self.target)
        complete_active_stable = self.complete_snapshot()
        with patch("core.services.platform_users.log_action") as audit:
            again = self.client.post(self.url("activate"), {}, format="json")
        self.assertEqual(again.status_code, 200)
        self.assertEqual(self.snapshot(self.target), active_stable)
        self.assert_complete_snapshot(complete_active_stable)
        audit.assert_not_called()
        self.assertEqual(self.login().status_code, 200)
        self.assertEqual(BusinessMembership.objects.values().get(pk=self.membership.pk), membership_before)
        self.assertEqual(Employee.objects.values().get(pk=self.employee.pk), employee_before)

    def test_reset_password_is_closed_validated_and_revokes_once(self):
        pair = self.login().data
        before = self.snapshot(self.target)
        complete_before = self.complete_snapshot()
        invalid = (
            {},
            {"password": self.new_password},
            {"password_confirmation": self.new_password},
            {"password": None, "password_confirmation": None},
            {"password": "", "password_confirmation": ""},
            {"password": "short", "password_confirmation": "short"},
            {"password": self.new_password, "password_confirmation": "different"},
            {"password": self.new_password, "password_confirmation": self.new_password, "role": "owner"},
        )
        for payload in invalid:
            with self.subTest(payload=payload):
                with patch("core.services.platform_users.log_action") as audit:
                    response = self.client.post(self.url("reset-password"), payload, format="json")
                self.assertEqual(response.status_code, 400)
                self.assertEqual(self.snapshot(self.target), before)
                self.assert_complete_snapshot(complete_before)
                audit.assert_not_called()

        response = self.client.post(
            self.url("reset-password"),
            {"password": self.new_password, "password_confirmation": self.new_password},
            format="json",
        )
        self.assertEqual(response.status_code, 204)
        self.assertEqual(response.content, b"")
        self.target.refresh_from_db()
        self.assertTrue(self.target.check_password(self.new_password))
        self.assertFalse(self.target.check_password(self.password))
        self.assertEqual(self.target.auth_version, before["auth_version"] + 1)
        self.assertEqual(self.login().status_code, 401)
        self.assertEqual(self.login(self.new_password).status_code, 200)
        self.assertEqual(
            APIClient().post(
                reverse("token_refresh"), {"refresh": pair["refresh"]}, format="json"
            ).status_code,
            401,
        )

    def test_revoke_sessions_is_deliberate_each_time_and_preserves_password(self):
        first_pair = self.login().data
        before = self.snapshot(self.target)
        first = self.client.post(self.url("revoke-sessions"), {}, format="json")
        self.assertEqual(first.status_code, 204)
        self.target.refresh_from_db()
        self.assertEqual(self.target.auth_version, before["auth_version"] + 1)
        self.assertEqual(self.target.password, before["password"])
        self.assertTrue(self.target.is_active)
        self.assertEqual(
            APIClient().post(
                reverse("token_refresh"), {"refresh": first_pair["refresh"]}, format="json"
            ).status_code,
            401,
        )
        self.assertEqual(self.login().status_code, 200)
        second = self.client.post(self.url("revoke-sessions"), {}, format="json")
        self.assertEqual(second.status_code, 204)
        self.target.refresh_from_db()
        self.assertEqual(self.target.auth_version, before["auth_version"] + 2)

    def test_self_protection_has_no_side_effects(self):
        VersionedRefreshToken.for_user(self.platform)
        before = self.complete_snapshot()
        cases = (
            ("deactivate", {}),
            ("reset-password", {"password": self.new_password, "password_confirmation": self.new_password}),
            ("revoke-sessions", {}),
        )
        for action, payload in cases:
            with self.subTest(action=action):
                with patch("core.services.platform_users.log_action") as audit:
                    response = self.client.post(self.url(action, self.platform), payload, format="json")
                self.assertEqual(response.status_code, 409)
                self.assert_complete_snapshot(before)
                audit.assert_not_called()

    def test_failures_roll_back_state_sessions_and_audit(self):
        pair = self.login().data
        before = self.complete_snapshot()
        outstanding = OutstandingToken.objects.get(token=pair["refresh"])
        failures = (
            "_blacklist_locked_tokens",
            "_assert_admin_mutation_result",
            "_audit_platform_user_action",
        )
        patch_targets = {
            "_blacklist_locked_tokens": "core.services.auth_sessions._blacklist_locked_tokens",
            "_assert_admin_mutation_result": "core.services.platform_users._assert_admin_mutation_result",
            "_audit_platform_user_action": "core.services.platform_users._audit_platform_user_action",
        }
        for failure in failures:
            with self.subTest(failure=failure):
                with patch(patch_targets[failure], side_effect=RuntimeError("forced failure")):
                    with self.assertRaises(RuntimeError):
                        self.client.post(self.url("deactivate"), {}, format="json")
                self.assert_complete_snapshot(before)
                self.assertFalse(BlacklistedToken.objects.filter(token=outstanding).exists())

    def test_password_hashing_and_unexpected_save_failures_roll_back(self):
        VersionedRefreshToken.for_user(self.target)
        before = self.complete_snapshot()
        payload = {
            "password": self.new_password,
            "password_confirmation": self.new_password,
        }
        with patch.object(User, "set_password", side_effect=RuntimeError("hash failed")):
            with self.assertRaises(RuntimeError):
                self.client.post(self.url("reset-password"), payload, format="json")
        self.assert_complete_snapshot(before)

        original_save = User.save

        def fail_target_save(instance, *args, **kwargs):
            if instance.pk == self.target.pk:
                raise IntegrityError("unexpected save failure")
            return original_save(instance, *args, **kwargs)

        with patch.object(User, "save", new=fail_target_save):
            with self.assertRaises(IntegrityError):
                self.client.post(self.url("deactivate"), {}, format="json")
        self.assert_complete_snapshot(before)

    def test_audit_contains_only_public_ids_and_safe_action(self):
        with self.assertLogs("audit", level="INFO") as captured:
            response = self.client.post(self.url("revoke-sessions"), {}, format="json")
        self.assertEqual(response.status_code, 204)
        log = " ".join(captured.output)
        self.assertIn("revoke-sessions", log)
        self.assertIn(str(self.platform.public_id), log)
        self.assertIn(str(self.target.public_id), log)
        self.assertNotIn(self.password, log)
        self.assertNotIn(self.target.password, log)
        self.assertNotIn(self.target.email, log)

    def test_serializer_and_openapi_contract(self):
        self.assertEqual(set(EmptyPlatformUserActionSerializer().fields), set())
        password_fields = PlatformUserPasswordResetSerializer().fields
        self.assertEqual(set(password_fields), {"password", "password_confirmation"})
        self.assertTrue(all(field.write_only for field in password_fields.values()))
        schema = SchemaGenerator().get_schema(request=None, public=True)
        for action, success in (
            ("activate", "200"),
            ("deactivate", "200"),
            ("reset-password", "204"),
            ("revoke-sessions", "204"),
        ):
            path = f"/api/platform/users/{{public_id}}/{action}/"
            self.assertEqual(set(schema["paths"][path]), {"post"})
            operation = schema["paths"][path]["post"]
            self.assertIn({"BearerAuth": []}, operation["security"])
            self.assertIn(success, operation["responses"])
            for code in ("400", "401", "403", "404"):
                self.assertIn(code, operation["responses"])
        serialized = str(schema).lower()
        self.assertNotIn("auth_version", serialized)
        reset_operation = schema["paths"][
            "/api/platform/users/{public_id}/reset-password/"
        ]["post"]
        request_schema = reset_operation["requestBody"]["content"]["application/json"]["schema"]
        self.assertIn("PlatformUserPasswordResetRequest", request_schema["$ref"])


@patch.object(LoginView, "throttle_classes", [])
@patch.object(PlatformUserActionView, "throttle_classes", [])
class PlatformUserAdministrationConcurrencyTests(TransactionTestCase):
    reset_sequences = True

    def setUp(self):
        self.actor = create_user(
            email="concurrent-administration-platform@playnow.test",
            password="ConcurrentAdministration-1!",
            is_superuser=True,
        )
        self.target = create_user(
            email="concurrent-administration-target@playnow.test",
            password="ConcurrentAdministration-1!",
        )

    def _service_worker(self, service, barrier):
        close_old_connections()
        try:
            barrier.wait(timeout=15)
            target = service(
                actor_id=self.actor.pk,
                target_public_id=self.target.public_id,
                validate_payload=lambda locked_target: {},
            )
            return target.auth_version
        finally:
            close_old_connections()
            connections.close_all()

    def _admin_access(self):
        return str(VersionedRefreshToken.for_user(self.actor).access_token)

    def _http_worker(self, barrier, method, path, payload=None, access=None):
        close_old_connections()
        try:
            client = APIClient()
            if access:
                client.credentials(HTTP_AUTHORIZATION=f"Bearer {access}")
            barrier.wait(timeout=15)
            response = getattr(client, method)(path, payload or {}, format="json")
            return response.status_code, deepcopy(response.data)
        finally:
            close_old_connections()
            connections.close_all()

    def _race(self, first, second):
        barrier = Barrier(2)
        with ThreadPoolExecutor(max_workers=2) as executor:
            futures = [
                executor.submit(self._http_worker, barrier, *first),
                executor.submit(self._http_worker, barrier, *second),
            ]
            return [future.result(timeout=30) for future in futures]

    def test_two_simultaneous_deactivations_have_one_effective_transition(self):
        before = self.target.auth_version
        barrier = Barrier(2)
        with ThreadPoolExecutor(max_workers=2) as executor:
            futures = [
                executor.submit(self._service_worker, deactivate_platform_user, barrier)
                for _ in range(2)
            ]
            results = [future.result(timeout=30) for future in futures]
        self.target.refresh_from_db()
        self.assertFalse(self.target.is_active)
        self.assertEqual(self.target.auth_version, before + 1)
        self.assertEqual(results, [before + 1, before + 1])

    def test_two_simultaneous_revocations_each_increment_once(self):
        before = self.target.auth_version
        barrier = Barrier(2)
        with ThreadPoolExecutor(max_workers=2) as executor:
            futures = [
                executor.submit(self._service_worker, revoke_platform_user_sessions, barrier)
                for _ in range(2)
            ]
            results = [future.result(timeout=30) for future in futures]
        self.target.refresh_from_db()
        self.assertEqual(self.target.auth_version, before + 2)
        self.assertEqual(sorted(results), [before + 1, before + 2])

    def test_target_lock_prevents_lost_updates_from_different_admins(self):
        second_actor = create_user(
            email="second-concurrent-platform@playnow.test",
            password="ConcurrentAdministration-1!",
            is_superuser=True,
        )
        before = self.target.auth_version
        barrier = Barrier(2)

        def revoke(actor_id):
            close_old_connections()
            try:
                barrier.wait(timeout=15)
                return revoke_platform_user_sessions(
                    actor_id=actor_id,
                    target_public_id=self.target.public_id,
                    validate_payload=lambda locked_target: {},
                ).auth_version
            finally:
                close_old_connections()
                connections.close_all()

        with ThreadPoolExecutor(max_workers=2) as executor:
            futures = [
                executor.submit(revoke, actor_id)
                for actor_id in (self.actor.pk, second_actor.pk)
            ]
            results = [future.result(timeout=30) for future in futures]
        self.target.refresh_from_db()
        self.assertEqual(self.target.auth_version, before + 2)
        self.assertEqual(sorted(results), [before + 1, before + 2])

    def test_crossed_admins_wait_on_shared_advisory_lock_without_deadlock(self):
        second_actor = create_user(
            email="crossed-second-platform@playnow.test",
            password="ConcurrentAdministration-1!",
            is_superuser=True,
        )
        status_object = create_status("Activo")
        business = create_business(user=self.actor, status=status_object)
        employee = Employee.objects.create(
            business=business,
            full_name="Crossed Admin Employee",
            position="Administrator",
            status=status_object,
        )
        create_membership(
            user=second_actor,
            business=business,
            role=BusinessMembership.ROLE_ADMIN,
            employee=employee,
        )
        first_token = VersionedRefreshToken.for_user(self.actor)
        second_token = VersionedRefreshToken.for_user(second_actor)
        first_outstanding = OutstandingToken.objects.get(jti=first_token["jti"])
        second_outstanding = OutstandingToken.objects.get(jti=second_token["jti"])
        versions_before = {
            self.actor.pk: self.actor.auth_version,
            second_actor.pk: second_actor.auth_version,
        }
        memberships_before = tuple(BusinessMembership.objects.order_by("pk").values())
        employees_before = tuple(Employee.objects.order_by("pk").values())

        workers_ready = Barrier(2)
        holder_acquired = Event()
        release_holder = Event()
        state_guard = Lock()
        state = {"worker_pids": {}}
        original_acquire = acquire_multi_user_write_lock

        def observed_acquire():
            original_acquire()
            with connection.cursor() as cursor:
                cursor.execute("SELECT pg_backend_pid()")
                current_pid = cursor.fetchone()[0]
            with state_guard:
                is_holder = "holder_pid" not in state
                if is_holder:
                    state["holder_pid"] = current_pid
            if is_holder:
                holder_acquired.set()
                if not release_holder.wait(timeout=15):
                    raise TimeoutError("advisory lock holder was not released")

        def revoke(actor, target):
            close_old_connections()
            try:
                with connection.cursor() as cursor:
                    cursor.execute("SELECT pg_backend_pid()")
                    pid = cursor.fetchone()[0]
                with state_guard:
                    state["worker_pids"][actor.pk] = pid
                workers_ready.wait(timeout=15)
                result = revoke_platform_user_sessions(
                    actor_id=actor.pk,
                    target_public_id=target.public_id,
                    validate_payload=lambda locked_target: {},
                )
                return result.pk, result.auth_version
            finally:
                close_old_connections()
                connections.close_all()

        try:
            with patch(
                "core.services.multi_user_write_lock.acquire_multi_user_write_lock",
                side_effect=observed_acquire,
            ):
                with ThreadPoolExecutor(max_workers=2) as executor:
                    futures = [
                        executor.submit(revoke, self.actor, second_actor),
                        executor.submit(revoke, second_actor, self.actor),
                    ]
                    self.assertTrue(holder_acquired.wait(timeout=15))
                    with state_guard:
                        contender_pid = next(
                            pid for pid in state["worker_pids"].values()
                            if pid != state["holder_pid"]
                        )
                        holder_pid = state["holder_pid"]
                    deadline = monotonic() + 5
                    while monotonic() < deadline:
                        with connection.cursor() as cursor:
                            cursor.execute(
                                """
                                SELECT pg_blocking_pids(%s), wait_event_type
                                FROM pg_stat_activity WHERE pid = %s
                                """,
                                [contender_pid, contender_pid],
                            )
                            blocking = cursor.fetchone()
                        if (
                            blocking
                            and holder_pid in blocking[0]
                            and blocking[1] == "Lock"
                        ):
                            break
                        if any(future.done() for future in futures):
                            self.fail("crossed mutation finished before advisory contention")
                        release_holder.wait(timeout=0.05)
                    else:
                        self.fail("PostgreSQL did not report the advisory lock wait")
                    self.assertTrue(all(not future.done() for future in futures))
                    release_holder.set()
                    results = [future.result(timeout=30) for future in futures]
        finally:
            release_holder.set()

        self.actor.refresh_from_db()
        second_actor.refresh_from_db()
        self.assertEqual(self.actor.auth_version, versions_before[self.actor.pk] + 1)
        self.assertEqual(
            second_actor.auth_version,
            versions_before[second_actor.pk] + 1,
        )
        self.assertEqual({result[0] for result in results}, {self.actor.pk, second_actor.pk})
        self.assertTrue(BlacklistedToken.objects.filter(token=first_outstanding).exists())
        self.assertTrue(BlacklistedToken.objects.filter(token=second_outstanding).exists())
        self.assertEqual(
            tuple(BusinessMembership.objects.order_by("pk").values()),
            memberships_before,
        )
        self.assertEqual(tuple(Employee.objects.order_by("pk").values()), employees_before)

    def test_deactivate_vs_refresh_leaves_no_valid_session(self):
        pair = VersionedRefreshToken.for_user(self.target)
        results = self._race(
            (
                "post",
                reverse("platform-user-deactivate", kwargs={"public_id": self.target.public_id}),
                {},
                self._admin_access(),
            ),
            ("post", reverse("token_refresh"), {"refresh": str(pair)}, None),
        )
        self.assertEqual(results[0][0], 200)
        self.assertIn(results[1][0], (200, 401))
        self.target.refresh_from_db()
        self.assertFalse(self.target.is_active)
        refresh_to_check = results[1][1].get("refresh") if results[1][0] == 200 else str(pair)
        self.assertEqual(
            APIClient().post(
                reverse("token_refresh"), {"refresh": refresh_to_check}, format="json"
            ).status_code,
            401,
        )
        if results[1][0] == 200:
            self.assertEqual(
                APIClient().get(
                    reverse("current-user"),
                    HTTP_AUTHORIZATION=f"Bearer {results[1][1]['access']}",
                ).status_code,
                401,
            )

    def test_deactivate_vs_login_is_serializable(self):
        results = self._race(
            (
                "post",
                reverse("platform-user-deactivate", kwargs={"public_id": self.target.public_id}),
                {},
                self._admin_access(),
            ),
            (
                "post",
                reverse("token_obtain_pair"),
                {"email": self.target.email, "password": "ConcurrentAdministration-1!"},
                None,
            ),
        )
        self.assertEqual(results[0][0], 200)
        self.assertIn(results[1][0], (200, 401))
        self.target.refresh_from_db()
        self.assertFalse(self.target.is_active)
        if results[1][0] == 200:
            self.assertEqual(
                APIClient().get(
                    reverse("current-user"),
                    HTTP_AUTHORIZATION=f"Bearer {results[1][1]['access']}",
                ).status_code,
                401,
            )
        self.assertEqual(
            APIClient().post(
                reverse("token_obtain_pair"),
                {"email": self.target.email, "password": "ConcurrentAdministration-1!"},
                format="json",
            ).status_code,
            401,
        )

    def test_reactivate_vs_login_allows_login_only_after_activation(self):
        User.objects.filter(pk=self.target.pk).update(is_active=False)
        results = self._race(
            (
                "post",
                reverse("platform-user-activate", kwargs={"public_id": self.target.public_id}),
                {},
                self._admin_access(),
            ),
            (
                "post",
                reverse("token_obtain_pair"),
                {"email": self.target.email, "password": "ConcurrentAdministration-1!"},
                None,
            ),
        )
        self.assertEqual(results[0][0], 200)
        self.assertIn(results[1][0], (200, 401))
        self.target.refresh_from_db()
        self.assertTrue(self.target.is_active)
        self.assertEqual(
            APIClient().post(
                reverse("token_obtain_pair"),
                {"email": self.target.email, "password": "ConcurrentAdministration-1!"},
                format="json",
            ).status_code,
            200,
        )

    def test_reset_password_vs_refresh_leaves_old_refresh_invalid(self):
        old_pair = VersionedRefreshToken.for_user(self.target)
        new_password = "Cobalt-River-927!Quartz"
        results = self._race(
            (
                "post",
                reverse("platform-user-reset-password", kwargs={"public_id": self.target.public_id}),
                {"password": new_password, "password_confirmation": new_password},
                self._admin_access(),
            ),
            ("post", reverse("token_refresh"), {"refresh": str(old_pair)}, None),
        )
        self.assertEqual(results[0][0], 204)
        self.assertIn(results[1][0], (200, 401))
        self.target.refresh_from_db()
        self.assertTrue(self.target.check_password(new_password))
        refresh_to_check = results[1][1].get("refresh") if results[1][0] == 200 else str(old_pair)
        self.assertEqual(
            APIClient().post(
                reverse("token_refresh"), {"refresh": refresh_to_check}, format="json"
            ).status_code,
            401,
        )

    def test_reset_password_vs_old_login_is_serializable(self):
        new_password = "Cobalt-River-927!Quartz"
        results = self._race(
            (
                "post",
                reverse("platform-user-reset-password", kwargs={"public_id": self.target.public_id}),
                {"password": new_password, "password_confirmation": new_password},
                self._admin_access(),
            ),
            (
                "post",
                reverse("token_obtain_pair"),
                {"email": self.target.email, "password": "ConcurrentAdministration-1!"},
                None,
            ),
        )
        self.assertEqual(results[0][0], 204)
        self.assertIn(results[1][0], (200, 401))
        if results[1][0] == 200:
            self.assertEqual(
                APIClient().get(
                    reverse("current-user"),
                    HTTP_AUTHORIZATION=f"Bearer {results[1][1]['access']}",
                ).status_code,
                401,
            )
        self.assertEqual(
            APIClient().post(
                reverse("token_obtain_pair"),
                {"email": self.target.email, "password": new_password},
                format="json",
            ).status_code,
            200,
        )

    def test_revoke_sessions_vs_refresh_leaves_old_session_invalid(self):
        old_pair = VersionedRefreshToken.for_user(self.target)
        results = self._race(
            (
                "post",
                reverse("platform-user-revoke-sessions", kwargs={"public_id": self.target.public_id}),
                {},
                self._admin_access(),
            ),
            ("post", reverse("token_refresh"), {"refresh": str(old_pair)}, None),
        )
        self.assertEqual(results[0][0], 204)
        self.assertIn(results[1][0], (200, 401))
        refresh_to_check = results[1][1].get("refresh") if results[1][0] == 200 else str(old_pair)
        self.assertEqual(
            APIClient().post(
                reverse("token_refresh"), {"refresh": refresh_to_check}, format="json"
            ).status_code,
            401,
        )

    def _authority_revoked_while_waiting(self, field):
        holder_ready = Event()
        service_entered = Event()
        original_lock_actor = _lock_platform_admin

        def observed_lock_actor(actor_id):
            service_entered.set()
            return original_lock_actor(actor_id)

        def revoke_authority():
            close_old_connections()
            try:
                with transaction.atomic():
                    actor = User.objects.select_for_update().get(pk=self.actor.pk)
                    holder_ready.set()
                    if not service_entered.wait(timeout=15):
                        raise TimeoutError("administrative request did not start")
                    setattr(actor, field, False)
                    actor.save(update_fields=[field])
            finally:
                close_old_connections()
                connections.close_all()

        def request_mutation():
            close_old_connections()
            try:
                return deactivate_platform_user(
                    actor_id=self.actor.pk,
                    target_public_id=self.target.public_id,
                    validate_payload=lambda locked_target: {},
                )
            finally:
                close_old_connections()
                connections.close_all()

        before = User.objects.values("is_active", "auth_version").get(pk=self.target.pk)
        with patch(
            "core.services.platform_users._lock_platform_admin",
            side_effect=observed_lock_actor,
        ):
            with ThreadPoolExecutor(max_workers=2) as executor:
                holder = executor.submit(revoke_authority)
                self.assertTrue(holder_ready.wait(timeout=15))
                request = executor.submit(request_mutation)
                holder.result(timeout=30)
                with self.assertRaises(Exception) as captured:
                    request.result(timeout=30)
        self.assertIn("autorizada", str(captured.exception))
        self.assertEqual(
            User.objects.values("is_active", "auth_version").get(pk=self.target.pk),
            before,
        )

    def test_superuser_revocation_while_waiting_prevents_mutation(self):
        self._authority_revoked_while_waiting("is_superuser")

    def test_actor_deactivation_while_waiting_prevents_mutation(self):
        self._authority_revoked_while_waiting("is_active")

    def test_unauthorized_request_does_not_wait_for_locked_target(self):
        unauthorized = create_user(email="unauthorized-no-wait@playnow.test")
        target_locked = Event()
        release_target = Event()

        def hold_target():
            close_old_connections()
            try:
                with transaction.atomic():
                    User.objects.select_for_update().get(pk=self.target.pk)
                    target_locked.set()
                    if not release_target.wait(timeout=15):
                        raise TimeoutError("target lock was not released")
            finally:
                close_old_connections()
                connections.close_all()

        def unauthorized_request():
            close_old_connections()
            try:
                client = APIClient()
                client.force_authenticate(unauthorized)
                return client.post(
                    reverse(
                        "platform-user-deactivate",
                        kwargs={"public_id": self.target.public_id},
                    ),
                    {},
                    format="json",
                ).status_code
            finally:
                close_old_connections()
                connections.close_all()

        try:
            with ThreadPoolExecutor(max_workers=2) as executor:
                holder = executor.submit(hold_target)
                self.assertTrue(target_locked.wait(timeout=15))
                request = executor.submit(unauthorized_request)
                self.assertEqual(request.result(timeout=5), 403)
                release_target.set()
                holder.result(timeout=30)
        finally:
            release_target.set()

    def test_deactivation_waits_on_real_postgresql_target_lock(self):
        target_locked = Event()
        release_target = Event()
        request_pid_ready = Event()
        state = {}

        def hold_target():
            close_old_connections()
            try:
                with transaction.atomic():
                    User.objects.select_for_update().get(pk=self.target.pk)
                    with connection.cursor() as cursor:
                        cursor.execute("SELECT pg_backend_pid()")
                        state["holder_pid"] = cursor.fetchone()[0]
                    target_locked.set()
                    if not release_target.wait(timeout=15):
                        raise TimeoutError("target lock was not released")
            finally:
                close_old_connections()
                connections.close_all()

        def deactivate():
            close_old_connections()
            try:
                with connection.cursor() as cursor:
                    cursor.execute("SELECT pg_backend_pid()")
                    state["request_pid"] = cursor.fetchone()[0]
                request_pid_ready.set()
                deactivate_platform_user(
                    actor_id=self.actor.pk,
                    target_public_id=self.target.public_id,
                    validate_payload=lambda locked_target: {},
                )
            finally:
                close_old_connections()
                connections.close_all()

        try:
            with ThreadPoolExecutor(max_workers=2) as executor:
                holder = executor.submit(hold_target)
                self.assertTrue(target_locked.wait(timeout=15))
                request = executor.submit(deactivate)
                self.assertTrue(request_pid_ready.wait(timeout=15))
                deadline = monotonic() + 5
                while monotonic() < deadline:
                    with connection.cursor() as cursor:
                        cursor.execute(
                            """
                            SELECT pg_blocking_pids(%s), wait_event_type
                            FROM pg_stat_activity WHERE pid = %s
                            """,
                            [state["request_pid"], state["request_pid"]],
                        )
                        blocking = cursor.fetchone()
                    if (
                        blocking
                        and state["holder_pid"] in blocking[0]
                        and blocking[1] == "Lock"
                    ):
                        break
                    if request.done():
                        self.fail("deactivation did not wait for the target lock")
                    release_target.wait(timeout=0.05)
                else:
                    self.fail("PostgreSQL did not report the target lock wait")
                self.assertFalse(request.done())
                release_target.set()
                holder.result(timeout=30)
                request.result(timeout=30)
        finally:
            release_target.set()

        self.target.refresh_from_db()
        self.assertFalse(self.target.is_active)
