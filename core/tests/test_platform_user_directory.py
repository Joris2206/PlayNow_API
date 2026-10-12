from concurrent.futures import ThreadPoolExecutor
from contextlib import ExitStack
from copy import deepcopy
from threading import Barrier, Event, Lock
from time import monotonic
from unittest import skipUnless
from unittest.mock import patch

from django.db import (
    IntegrityError,
    close_old_connections,
    connection,
    connections,
    transaction,
)
from django.test import TransactionTestCase
from django.test.utils import CaptureQueriesContext
from django.urls import resolve, reverse
from drf_spectacular.generators import SchemaGenerator
from rest_framework import status
from rest_framework.test import APIClient, APITestCase
from rest_framework_simplejwt.token_blacklist.models import (
    BlacklistedToken,
    OutstandingToken,
)

from core.api.serializers.platform_users import (
    PlatformUserIdentityUpdateSerializer,
    PlatformUserSerializer,
)
from core.api.views.platform_users import (
    PlatformUserDetailView,
    PlatformUserListView,
)
from core.models import BusinessMembership, Employee, User
from core.permissions import IsActivePlatformAdmin
from core.services.platform_users import update_platform_user_identity
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
PROHIBITED_FIELDS = {
    "password",
    "auth_version",
    "is_staff",
    "groups",
    "user_permissions",
    "role",
    "last_login",
    "memberships",
    "employee",
    "business",
    "id",
}


class PlatformUserDirectoryAPITests(APITestCase):
    password = "PlatformDirectoryPassword-1!"

    @classmethod
    def setUpTestData(cls):
        cls.active_status = create_status("Activo")
        cls.platform = create_user(
            email="directory-platform@playnow.test",
            full_name="Directory Platform",
            password=cls.password,
            is_superuser=True,
        )
        cls.owner = create_user(email="directory-owner@playnow.test")
        cls.business = create_business(
            user=cls.owner,
            status=cls.active_status,
        )
        cls.roles = {}
        for role, _ in BusinessMembership.ROLES:
            if role == BusinessMembership.ROLE_OWNER:
                cls.roles[role] = cls.owner
            else:
                cls.roles[role] = create_role_user(
                    business=cls.business,
                    role=role,
                    status=cls.active_status,
                    email=f"directory-{role}@playnow.test",
                )[0]
        cls.target = create_user(
            email="directory-target@playnow.test",
            full_name="Directory Target",
            password=cls.password,
        )
        cls.target_employee = Employee.objects.create(
            business=cls.business,
            full_name="Independent Employee Identity",
            phone="",
            position="Tester",
            status=cls.active_status,
        )
        cls.target_membership = create_membership(
            user=cls.target,
            business=cls.business,
            role=BusinessMembership.ROLE_VIEWER,
            employee=cls.target_employee,
        )

    def setUp(self):
        self.client.force_authenticate(user=self.platform)

    @staticmethod
    def list_url():
        return reverse("platform-user-list")

    def detail_url(self, user=None):
        return reverse(
            "platform-user-detail",
            kwargs={"public_id": (user or self.target).public_id},
        )

    @staticmethod
    def identity_snapshot(user):
        return User.objects.values(
            "email",
            "full_name",
            "password",
            "is_active",
            "is_superuser",
            "is_staff",
            "role",
            "auth_version",
        ).get(pk=user.pk)

    def test_routes_resolve_without_colliding_with_legacy_users(self):
        self.assertEqual(self.list_url(), "/api/platform/users/")
        self.assertEqual(
            self.detail_url(),
            f"/api/platform/users/{self.target.public_id}/",
        )
        self.assertEqual(resolve(self.list_url()).url_name, "platform-user-list")
        self.assertEqual(
            resolve(self.detail_url()).url_name,
            "platform-user-detail",
        )
        self.assertEqual(reverse("user-change-password"), "/api/users/change-password/")
        self.assertEqual(
            self.client.get("/api/platform/users/not-a-uuid/").status_code,
            status.HTTP_404_NOT_FOUND,
        )
        for method, url in (
            ("post", self.list_url()),
            ("put", self.detail_url()),
            ("delete", self.detail_url()),
        ):
            with self.subTest(method=method):
                self.assertEqual(
                    getattr(self.client, method)(url, {}, format="json").status_code,
                    status.HTTP_405_METHOD_NOT_ALLOWED,
                )

    def test_only_current_active_platform_admin_is_authorized(self):
        existing = self.detail_url()
        missing = reverse(
            "platform-user-detail",
            kwargs={"public_id": "00000000-0000-0000-0000-000000000001"},
        )
        self.client.force_authenticate(user=None)
        self.assertEqual(
            self.client.get(self.list_url()).status_code,
            status.HTTP_401_UNAUTHORIZED,
        )

        for role, actor in self.roles.items():
            self.client.force_authenticate(user=actor)
            with self.subTest(role=role):
                self.assertEqual(self.client.get(self.list_url()).status_code, 403)
                self.assertEqual(self.client.get(existing).status_code, 403)
                self.assertEqual(self.client.get(missing).status_code, 403)
                self.assertEqual(
                    self.client.patch(existing, {"full_name": "No"}, format="json").status_code,
                    403,
                )
                self.assertEqual(
                    self.client.patch(missing, {"full_name": "No"}, format="json").status_code,
                    403,
                )

        self.client.force_authenticate(user=self.platform)
        User.objects.filter(pk=self.platform.pk).update(is_superuser=False)
        self.assertEqual(self.client.get(self.list_url()).status_code, 403)
        self.assertEqual(self.client.get(existing).status_code, 403)
        self.assertEqual(self.client.get(missing).status_code, 403)

        User.objects.filter(pk=self.platform.pk).update(
            is_superuser=True,
            is_active=False,
        )
        self.assertEqual(self.client.get(self.list_url()).status_code, 403)
        self.assertEqual(self.client.get(existing).status_code, 403)
        self.assertEqual(self.client.get(missing).status_code, 403)

    def test_list_and_detail_expose_only_the_global_identity_contract(self):
        listing = self.client.get(self.list_url(), {"page_size": 200})
        detail = self.client.get(self.detail_url())
        self.assertEqual(listing.status_code, status.HTTP_200_OK)
        self.assertEqual(detail.status_code, status.HTTP_200_OK)
        row = next(
            item
            for item in listing.data["results"]
            if item["public_id"] == str(self.target.public_id)
        )
        self.assertEqual(set(row), RESPONSE_FIELDS)
        self.assertEqual(set(detail.data), RESPONSE_FIELDS)
        self.assertTrue(PROHIBITED_FIELDS.isdisjoint(row))
        self.assertEqual(detail.data, row)

        missing = reverse(
            "platform-user-detail",
            kwargs={"public_id": "00000000-0000-0000-0000-000000000001"},
        )
        self.assertEqual(self.client.get(missing).status_code, 404)

    def test_list_pagination_search_filters_and_ordering(self):
        for index in range(27):
            create_user(
                email=f"paged-{index:02d}@playnow.test",
                full_name=f"Paged Person {26 - index:02d}",
                is_superuser=index % 7 == 0,
            )
        User.objects.filter(email__startswith="paged-").update(
            created_at=self.platform.created_at,
        )
        all_ids = set(User.objects.values_list("public_id", flat=True))
        expected_ids = [
            str(public_id)
            for public_id in User.objects.order_by("created_at", "pk").values_list(
                "public_id",
                flat=True,
            )
        ]
        first = self.client.get(self.list_url(), {"page_size": 17, "page": 1})
        second = self.client.get(self.list_url(), {"page_size": 17, "page": 2})
        self.assertEqual(first.status_code, 200)
        self.assertEqual(second.status_code, 200)
        self.assertEqual(first.data["count"], len(all_ids))
        self.assertEqual(first.data["total_pages"], 3)
        self.assertEqual(first.data["current_page"], 1)
        self.assertEqual(first.data["page_size"], 17)
        self.assertEqual(
            [item["public_id"] for item in first.data["results"]],
            expected_ids[:17],
        )
        self.assertEqual(
            [item["public_id"] for item in second.data["results"]],
            expected_ids[17:34],
        )
        page_ids = {
            item["public_id"]
            for page in (first, second)
            for item in page.data["results"]
        }
        third = self.client.get(self.list_url(), {"page_size": 17, "page": 3})
        self.assertEqual(
            [item["public_id"] for item in third.data["results"]],
            expected_ids[34:],
        )
        page_ids.update(item["public_id"] for item in third.data["results"])
        self.assertEqual(page_ids, {str(value) for value in all_ids})
        self.assertEqual(
            sum(len(page.data["results"]) for page in (first, second, third)),
            len(page_ids),
        )

        for term, expected_id in (
            ("directory-target@", self.target.public_id),
            ("Directory Target", self.target.public_id),
        ):
            response = self.client.get(self.list_url(), {"search": term})
            self.assertEqual(
                [item["public_id"] for item in response.data["results"]],
                [str(expected_id)],
            )
        self.assertEqual(
            self.client.get(self.list_url(), {"search": "absent-term"}).data["count"],
            0,
        )

        for field in ("is_active", "is_superuser"):
            for value in ("true", "false"):
                with self.subTest(field=field, value=value):
                    response = self.client.get(
                        self.list_url(),
                        {field: value, "page_size": 200},
                    )
                    expected = value == "true"
                    expected_ids = {
                        str(public_id)
                        for public_id in User.objects.filter(
                            **{field: expected}
                        ).values_list("public_id", flat=True)
                    }
                    actual_ids = {
                        item["public_id"]
                        for item in response.data["results"]
                    }
                    self.assertEqual(actual_ids, expected_ids)
                    self.assertEqual(response.data["count"], len(expected_ids))
                    self.assertTrue(
                        all(item[field] is expected for item in response.data["results"])
                    )
            for invalid in ("invalid", "TRUE", "False"):
                with self.subTest(field=field, invalid=invalid):
                    self.assertEqual(
                        self.client.get(
                            self.list_url(),
                            {field: invalid},
                        ).status_code,
                        status.HTTP_400_BAD_REQUEST,
                    )
            multiple = self.client.get(
                f"{self.list_url()}?{field}=true&{field}=false&page_size=200"
            )
            expected_multiple_ids = {
                str(public_id)
                for public_id in User.objects.filter(
                    **{field: False}
                ).values_list("public_id", flat=True)
            }
            self.assertEqual(
                {
                    item["public_id"]
                    for item in multiple.data["results"]
                },
                expected_multiple_ids,
            )

        allowed = (
            "email",
            "full_name",
            "is_active",
            "is_superuser",
            "created_at",
            "updated_at",
        )
        for field in allowed:
            for prefix in ("", "-"):
                response = self.client.get(
                    self.list_url(),
                    {"ordering": f"{prefix}{field}", "page_size": 200},
                )
                self.assertEqual(response.status_code, 200)
                expected_order = [
                    str(public_id)
                    for public_id in User.objects.order_by(
                        f"{prefix}{field}",
                        "pk",
                    ).values_list("public_id", flat=True)
                ]
                self.assertEqual(
                    [
                        item["public_id"]
                        for item in response.data["results"]
                    ],
                    expected_order,
                )
        unsafe = self.client.get(
            self.list_url(),
            {"ordering": "password", "page_size": 200},
        )
        default = self.client.get(self.list_url(), {"page_size": 200})
        self.assertEqual(
            [item["public_id"] for item in unsafe.data["results"]],
            [item["public_id"] for item in default.data["results"]],
        )

    def test_patch_is_closed_normalized_and_has_no_related_side_effects(self):
        user_before = self.identity_snapshot(self.target)
        membership_before = BusinessMembership.objects.values().get(
            pk=self.target_membership.pk
        )
        employee_before = Employee.objects.values().get(pk=self.target_employee.pk)
        outstanding_before = OutstandingToken.objects.filter(user=self.target).count()
        blacklisted_before = BlacklistedToken.objects.filter(
            token__user=self.target
        ).count()

        response = self.client.patch(
            self.detail_url(),
            {
                "email": "  UPDATED.Target@PLAYNOW.TEST ",
                "full_name": "  Updated Target Name  ",
            },
            format="json",
        )
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data["email"], "updated.target@playnow.test")
        self.assertEqual(response.data["full_name"], "Updated Target Name")
        after = self.identity_snapshot(self.target)
        self.assertEqual(after["email"], "updated.target@playnow.test")
        self.assertEqual(after["full_name"], "Updated Target Name")
        for field in set(user_before) - {"email", "full_name"}:
            self.assertEqual(after[field], user_before[field])
        self.assertEqual(
            BusinessMembership.objects.values().get(pk=self.target_membership.pk),
            membership_before,
        )
        self.assertEqual(
            Employee.objects.values().get(pk=self.target_employee.pk),
            employee_before,
        )
        self.assertEqual(
            OutstandingToken.objects.filter(user=self.target).count(),
            outstanding_before,
        )
        self.assertEqual(
            BlacklistedToken.objects.filter(token__user=self.target).count(),
            blacklisted_before,
        )

        invalid_payloads = (
            {},
            {"full_name": "   "},
            {"full_name": None},
            {"email": None},
            {"email": "not-email"},
        )
        stable = self.identity_snapshot(self.target)
        for payload in invalid_payloads:
            with self.subTest(payload=payload):
                self.assertEqual(
                    self.client.patch(
                        self.detail_url(), payload, format="json"
                    ).status_code,
                    status.HTTP_400_BAD_REQUEST,
                )
                self.assertEqual(self.identity_snapshot(self.target), stable)

        prohibited_payloads = {
            "unknown": "value",
            "public_id": str(self.target.public_id),
            "is_active": False,
            "is_superuser": False,
            "is_staff": True,
            "groups": [],
            "user_permissions": [],
            "role": User.Roles.EMPLOYEE,
            "password": "NewPassword-2!",
            "password_confirmation": "NewPassword-2!",
            "auth_version": 99,
            "memberships": [],
            "membership": str(self.target_membership.public_id),
            "employee": str(self.target_employee.public_id),
            "employee_public_id": str(self.target_employee.public_id),
            "business": str(self.business.public_id),
            "business_public_id": str(self.business.public_id),
            "created_at": "2026-01-01T00:00:00Z",
            "updated_at": "2026-01-01T00:00:00Z",
            "id": self.target.pk,
            "pk": self.target.pk,
        }
        for field, value in prohibited_payloads.items():
            with self.subTest(field=field):
                with patch(
                    "core.services.platform_users.log_action"
                ) as audit:
                    response = self.client.patch(
                        self.detail_url(),
                        {field: value},
                        format="json",
                    )
                self.assertEqual(
                    response.status_code,
                    status.HTTP_400_BAD_REQUEST,
                )
                self.assertIn(field, response.data)
                audit.assert_not_called()
                self.assertEqual(self.identity_snapshot(self.target), stable)
                self.assertEqual(
                    BusinessMembership.objects.values().get(
                        pk=self.target_membership.pk
                    ),
                    membership_before,
                )
                self.assertEqual(
                    Employee.objects.values().get(
                        pk=self.target_employee.pk
                    ),
                    employee_before,
                )
                self.assertEqual(
                    OutstandingToken.objects.filter(
                        user=self.target
                    ).count(),
                    outstanding_before,
                )
                self.assertEqual(
                    BlacklistedToken.objects.filter(
                        token__user=self.target
                    ).count(),
                    blacklisted_before,
                )

    def test_email_conflicts_are_controlled_and_case_only_update_is_deterministic(self):
        other = create_user(email="email-owner@playnow.test")
        for email in (other.email, other.email.upper()):
            response = self.client.patch(
                self.detail_url(),
                {"email": email},
                format="json",
            )
            self.assertEqual(response.status_code, status.HTTP_409_CONFLICT)
        other.refresh_from_db()
        self.assertEqual(other.email, "email-owner@playnow.test")

        User.objects.filter(pk=self.target.pk).update(
            email="Directory.Case@PlayNow.Test"
        )
        response = self.client.patch(
            self.detail_url(),
            {"email": "DIRECTORY.CASE@PLAYNOW.TEST"},
            format="json",
        )
        self.assertEqual(response.status_code, 200)
        self.target.refresh_from_db()
        self.assertEqual(self.target.email, "directory.case@playnow.test")

    def test_self_edit_preserves_current_tokens_and_uses_new_login_email(self):
        auth_client = APIClient()
        login = auth_client.post(
            reverse("token_obtain_pair"),
            {"email": self.platform.email, "password": self.password},
            format="json",
        )
        self.assertEqual(login.status_code, 200)
        outstanding_before = OutstandingToken.objects.filter(user=self.platform).count()
        auth_client.credentials(
            HTTP_AUTHORIZATION=f"Bearer {login.data['access']}"
        )
        changed = auth_client.patch(
            self.detail_url(self.platform),
            {
                "email": "self-edited-platform@playnow.test",
                "full_name": "Self Edited Platform",
            },
            format="json",
        )
        self.assertEqual(changed.status_code, 200)
        self.assertEqual(auth_client.get(reverse("current-user")).status_code, 200)
        self.assertEqual(
            APIClient().post(
                reverse("token_refresh"),
                {"refresh": login.data["refresh"]},
                format="json",
            ).status_code,
            200,
        )
        self.assertEqual(
            APIClient().post(
                reverse("token_obtain_pair"),
                {"email": "directory-platform@playnow.test", "password": self.password},
                format="json",
            ).status_code,
            401,
        )
        self.assertEqual(
            APIClient().post(
                reverse("token_obtain_pair"),
                {"email": "self-edited-platform@playnow.test", "password": self.password},
                format="json",
            ).status_code,
            200,
        )
        self.platform.refresh_from_db()
        self.assertTrue(self.platform.is_superuser)
        self.assertEqual(self.platform.auth_version, 0)
        self.assertEqual(
            OutstandingToken.objects.filter(user=self.platform).count(),
            outstanding_before + 2,
        )

    def test_audit_failure_rolls_back_and_logs_only_field_names(self):
        before = self.identity_snapshot(self.target)
        with patch(
            "core.services.platform_users.log_action",
            side_effect=RuntimeError("audit unavailable"),
        ):
            with self.assertRaises(RuntimeError):
                update_platform_user_identity(
                    actor=self.platform,
                    target_public_id=self.target.public_id,
                    validate_payload=lambda target: {"full_name": "Rollback Name"},
                )
        self.assertEqual(self.identity_snapshot(self.target), before)

        with self.assertLogs("audit", level="INFO") as captured:
            response = self.client.patch(
                self.detail_url(),
                {"full_name": "Audited Name"},
                format="json",
            )
        self.assertEqual(response.status_code, 200)
        log = " ".join(captured.output)
        self.assertIn("UPDATE_PLATFORM_USER_IDENTITY", log)
        self.assertIn("full_name", log)
        self.assertNotIn("Audited Name", log)
        self.assertNotIn(self.target.email, log)

    def test_unexpected_integrity_error_is_not_converted_to_email_conflict(self):
        unexpected = IntegrityError("unexpected constraint")
        with patch.object(User, "save", side_effect=unexpected):
            with self.assertRaises(IntegrityError):
                update_platform_user_identity(
                    actor=self.platform,
                    target_public_id=self.target.public_id,
                    validate_payload=lambda target: {"full_name": "Never Saved"},
                )

    def test_list_query_count_is_constant_and_does_not_load_relations(self):
        for index in range(25):
            create_user(email=f"query-{index}@playnow.test")

        def captured(page_size):
            with CaptureQueriesContext(connection) as queries:
                response = self.client.get(
                    self.list_url(),
                    {"page_size": page_size},
                )
                self.assertEqual(response.status_code, 200)
                list(response.data["results"])
            return queries.captured_queries

        small = captured(2)
        large = captured(20)
        self.assertEqual(len(small), len(large))
        for query in large:
            sql = query["sql"].lower()
            self.assertNotIn("core_businessmembership", sql)
            self.assertNotIn("core_employee", sql)
            self.assertNotIn("core_business\"", sql)

    def test_serializer_and_openapi_contract_are_closed(self):
        response_serializer = PlatformUserSerializer()
        self.assertEqual(set(response_serializer.fields), RESPONSE_FIELDS)
        self.assertTrue(
            all(field.read_only for field in response_serializer.fields.values())
        )
        self.assertEqual(
            set(PlatformUserIdentityUpdateSerializer().fields),
            {"email", "full_name"},
        )

        schema = SchemaGenerator().get_schema(request=None, public=True)
        list_path = schema["paths"]["/api/platform/users/"]
        detail_path = schema["paths"]["/api/platform/users/{public_id}/"]
        self.assertEqual(set(list_path), {"get"})
        self.assertEqual(set(detail_path), {"get", "patch"})
        patch_operation = detail_path["patch"]
        request_ref = patch_operation["requestBody"]["content"]["application/json"][
            "schema"
        ]["$ref"]
        request_schema = schema["components"]["schemas"][request_ref.rsplit("/", 1)[1]]
        self.assertEqual(set(request_schema["properties"]), {"email", "full_name"})
        paginated = schema["components"]["schemas"]["PaginatedPlatformUserList"]
        self.assertEqual(
            set(paginated["properties"]),
            {
                "count",
                "total_pages",
                "current_page",
                "page_size",
                "next",
                "previous",
                "results",
            },
        )
        serialized = str({"list": list_path["get"], "patch": patch_operation})
        for forbidden in PROHIBITED_FIELDS:
            self.assertNotIn(f"'{forbidden}'", serialized)


@skipUnless(connection.vendor == "postgresql", "Requires PostgreSQL row locks")
class PlatformUserDirectoryConcurrencyTests(TransactionTestCase):
    reset_sequences = True

    def setUp(self):
        self.platform = create_user(
            email="concurrent-platform@playnow.test",
            is_superuser=True,
        )
        self.target_a = create_user(email="concurrent-a@playnow.test")
        self.target_b = create_user(email="concurrent-b@playnow.test")

    @staticmethod
    def _request(actor, method, path, payload, barrier=None):
        close_old_connections()
        try:
            client = APIClient()
            client.force_authenticate(user=actor)
            if barrier is not None:
                barrier.wait(timeout=15)
            response = getattr(client, method)(path, payload, format="json")
            data = dict(response.data) if response.data is not None else {}
            return response.status_code, data
        finally:
            close_old_connections()
            connections.close_all()

    @staticmethod
    def _jwt_get(access, path, params=None):
        close_old_connections()
        try:
            client = APIClient()
            client.credentials(HTTP_AUTHORIZATION=f"Bearer {access}")
            response = client.get(path, params or {})
            return response.status_code, deepcopy(response.data)
        finally:
            close_old_connections()
            connections.close_all()

    def detail_url(self, target):
        return reverse(
            "platform-user-detail",
            kwargs={"public_id": target.public_id},
        )

    @staticmethod
    def _read_path(endpoint, target):
        if endpoint == "list":
            return reverse("platform-user-list")
        if endpoint == "existing":
            return reverse(
                "platform-user-detail",
                kwargs={"public_id": target.public_id},
            )
        return reverse(
            "platform-user-detail",
            kwargs={
                "public_id": "00000000-0000-0000-0000-000000000001"
            },
        )

    def _read_revocation_commits_first(self, *, field, endpoint):
        actor = create_user(
            email=f"before-{field}-{endpoint}@playnow.test",
            is_superuser=True,
        )
        target = create_user(
            email=f"before-target-{field}-{endpoint}@playnow.test"
        )
        target_before = User.objects.values().get(pk=target.pk)
        access = str(VersionedRefreshToken.for_user(actor).access_token)
        path = self._read_path(endpoint, target)
        preauthorized = Event()
        revocation_committed = Event()
        original_permission = IsActivePlatformAdmin.has_permission

        def pause_after_preauthorization(permission, request, view):
            allowed = original_permission(permission, request, view)
            if allowed:
                preauthorized.set()
                if not revocation_committed.wait(timeout=15):
                    raise TimeoutError("Revocation did not commit")
            return allowed

        def revoke():
            close_old_connections()
            try:
                if not preauthorized.wait(timeout=15):
                    raise TimeoutError("GET did not pass preauthorization")
                with transaction.atomic():
                    locked_actor = User.objects.select_for_update().get(
                        pk=actor.pk
                    )
                    setattr(locked_actor, field, False)
                    locked_actor.save(update_fields=[field])
                revocation_committed.set()
            finally:
                close_old_connections()
                connections.close_all()

        view_class = (
            PlatformUserListView
            if endpoint == "list"
            else PlatformUserDetailView
        )
        with ExitStack() as stack:
            stack.enter_context(patch.object(
                IsActivePlatformAdmin,
                "has_permission",
                new=pause_after_preauthorization,
            ))
            stack.enter_context(patch.object(
                view_class,
                "get_queryset",
                side_effect=AssertionError(
                    "Global User queryset evaluated after revocation"
                ),
            ))
            executor = stack.enter_context(ThreadPoolExecutor(max_workers=2))
            request = executor.submit(self._jwt_get, access, path)
            revoker = executor.submit(revoke)
            revoker.result(timeout=30)
            first = request.result(timeout=30)

        second = self._jwt_get(access, path)
        self.assertEqual(first[0], status.HTTP_403_FORBIDDEN)
        self.assertEqual(
            second[0],
            status.HTTP_401_UNAUTHORIZED
            if field == "is_active"
            else status.HTTP_403_FORBIDDEN,
        )
        self.assertEqual(User.objects.values().get(pk=target.pk), target_before)
        return first

    def test_read_revocation_committed_first_is_uniform_and_reads_no_targets(self):
        for field in ("is_superuser", "is_active"):
            results = {}
            for endpoint in ("list", "existing", "missing"):
                with self.subTest(field=field, endpoint=endpoint):
                    results[endpoint] = self._read_revocation_commits_first(
                        field=field,
                        endpoint=endpoint,
                    )
            self.assertEqual(results["list"], results["existing"])
            self.assertEqual(results["existing"], results["missing"])

    def _read_lock_acquired_first(self, *, field, endpoint):
        actor = create_user(
            email=f"locked-{field}-{endpoint}@playnow.test",
            is_superuser=True,
        )
        target = create_user(
            email=f"locked-target-{field}-{endpoint}@playnow.test"
        )
        access = str(VersionedRefreshToken.for_user(actor).access_token)
        path = self._read_path(endpoint, target)
        read_materializing = Event()
        release_read = Event()
        revoker_pid_ready = Event()
        revocation_committed = Event()
        state_lock = Lock()
        state = {"holder_pid": None, "revoker_pid": None, "held": False}

        def hold_during_materialization():
            with state_lock:
                first = not state["held"]
                if first:
                    state["held"] = True
            if not first:
                if revocation_committed.is_set():
                    raise AssertionError("Revocation committed during serialization")
                return
            with connection.cursor() as cursor:
                cursor.execute("SELECT pg_backend_pid()")
                holder_pid = cursor.fetchone()[0]
            with state_lock:
                state["holder_pid"] = holder_pid
            read_materializing.set()
            if not release_read.wait(timeout=15):
                raise TimeoutError("GET materialization was not released")
            if revocation_committed.is_set():
                raise AssertionError("Actor lock ended before materialization")

        original_representation = PlatformUserSerializer.to_representation

        def observed_representation(serializer, *args, **kwargs):
            hold_during_materialization()
            return original_representation(serializer, *args, **kwargs)

        original_get_object = PlatformUserDetailView.get_object

        def observed_get_object(view):
            hold_during_materialization()
            return original_get_object(view)

        def revoke():
            close_old_connections()
            try:
                with connection.cursor() as cursor:
                    cursor.execute("SELECT pg_backend_pid()")
                    revoker_pid = cursor.fetchone()[0]
                with state_lock:
                    state["revoker_pid"] = revoker_pid
                revoker_pid_ready.set()
                with transaction.atomic():
                    locked_actor = User.objects.select_for_update().get(
                        pk=actor.pk
                    )
                    setattr(locked_actor, field, False)
                    locked_actor.save(update_fields=[field])
                revocation_committed.set()
            finally:
                close_old_connections()
                connections.close_all()

        try:
            with ExitStack() as stack:
                if endpoint == "missing":
                    stack.enter_context(patch.object(
                        PlatformUserDetailView,
                        "get_object",
                        new=observed_get_object,
                    ))
                else:
                    stack.enter_context(patch.object(
                        PlatformUserSerializer,
                        "to_representation",
                        new=observed_representation,
                    ))
                executor = stack.enter_context(ThreadPoolExecutor(max_workers=2))
                request = executor.submit(self._jwt_get, access, path)
                self.assertTrue(read_materializing.wait(timeout=15))
                revoker = executor.submit(revoke)
                self.assertTrue(revoker_pid_ready.wait(timeout=15))

                deadline = monotonic() + 5
                blocking_state = None
                while monotonic() < deadline:
                    with state_lock:
                        holder_pid = state["holder_pid"]
                        revoker_pid = state["revoker_pid"]
                    with connection.cursor() as cursor:
                        cursor.execute(
                            """
                            SELECT pg_blocking_pids(%s), wait_event_type,
                                   wait_event
                            FROM pg_stat_activity WHERE pid = %s
                            """,
                            [revoker_pid, revoker_pid],
                        )
                        blocking_state = cursor.fetchone()
                    if (
                        blocking_state
                        and holder_pid in blocking_state[0]
                        and blocking_state[1] == "Lock"
                    ):
                        break
                    if revoker.done():
                        self.fail("Revocation did not wait for the GET actor lock")
                    release_read.wait(timeout=0.05)
                else:
                    self.fail("PostgreSQL did not report the actor lock wait")

                self.assertFalse(revoker.done())
                release_read.set()
                first = request.result(timeout=30)
                revoker.result(timeout=30)
        finally:
            release_read.set()

        expected = (
            status.HTTP_404_NOT_FOUND
            if endpoint == "missing"
            else status.HTTP_200_OK
        )
        self.assertEqual(first[0], expected)
        self.assertTrue(revocation_committed.is_set())
        second = self._jwt_get(access, path)
        self.assertEqual(
            second[0],
            status.HTTP_401_UNAUTHORIZED
            if field == "is_active"
            else status.HTTP_403_FORBIDDEN,
        )

    def test_read_lock_serializes_revocation_through_response_materialization(self):
        for field in ("is_superuser", "is_active"):
            for endpoint in ("list", "existing", "missing"):
                with self.subTest(field=field, endpoint=endpoint):
                    self._read_lock_acquired_first(
                        field=field,
                        endpoint=endpoint,
                    )

    def test_concurrent_email_and_name_updates_preserve_both_changes(self):
        barrier = Barrier(2)
        calls = (
            (
                self.platform,
                "patch",
                self.detail_url(self.target_a),
                {"email": "concurrent-updated@playnow.test"},
                barrier,
            ),
            (
                self.platform,
                "patch",
                self.detail_url(self.target_a),
                {"full_name": "Concurrent Updated Name"},
                barrier,
            ),
        )
        with ThreadPoolExecutor(max_workers=2) as executor:
            results = [
                future.result(timeout=30)
                for future in [executor.submit(self._request, *call) for call in calls]
            ]
        self.assertEqual([result[0] for result in results], [200, 200])
        self.target_a.refresh_from_db()
        self.assertEqual(self.target_a.email, "concurrent-updated@playnow.test")
        self.assertEqual(self.target_a.full_name, "Concurrent Updated Name")

    def test_concurrent_same_field_updates_are_serializable(self):
        barrier = Barrier(2)
        names = {"Serializable First", "Serializable Second"}
        with ThreadPoolExecutor(max_workers=2) as executor:
            futures = [
                executor.submit(
                    self._request,
                    self.platform,
                    "patch",
                    self.detail_url(self.target_a),
                    {"full_name": name},
                    barrier,
                )
                for name in names
            ]
            results = [future.result(timeout=30) for future in futures]
        self.assertEqual(sorted(result[0] for result in results), [200, 200])
        self.target_a.refresh_from_db()
        self.assertIn(self.target_a.full_name, names)
        self.assertIn(
            self.target_a.full_name,
            {result[1]["full_name"] for result in results},
        )

    def test_concurrent_email_collision_has_one_controlled_winner(self):
        barrier = Barrier(2)
        shared = "shared-concurrent@playnow.test"
        with ThreadPoolExecutor(max_workers=2) as executor:
            futures = [
                executor.submit(
                    self._request,
                    self.platform,
                    "patch",
                    self.detail_url(target),
                    {"email": shared.upper() if index else shared},
                    barrier,
                )
                for index, target in enumerate((self.target_a, self.target_b))
            ]
            results = [future.result(timeout=30) for future in futures]
        self.assertEqual(sorted(result[0] for result in results), [200, 409])
        self.assertEqual(User.objects.filter(email__iexact=shared).count(), 1)
        self.assertEqual(User.objects.count(), 3)

    def _assert_revocation_while_waiting(self, field):
        service_entered = Event()
        holder_acquired = Event()
        original = update_platform_user_identity

        def observed_service(*args, **kwargs):
            service_entered.set()
            return original(*args, **kwargs)

        def revoke_under_lock():
            close_old_connections()
            try:
                with transaction.atomic():
                    actor = User.objects.select_for_update().get(pk=self.platform.pk)
                    holder_acquired.set()
                    if not service_entered.wait(timeout=15):
                        raise TimeoutError("PATCH did not reach the production service")
                    setattr(actor, field, False)
                    actor.save(update_fields=[field])
            finally:
                close_old_connections()
                connections.close_all()

        with ThreadPoolExecutor(max_workers=2) as executor:
            holder = executor.submit(revoke_under_lock)
            self.assertTrue(holder_acquired.wait(timeout=15))
            with patch(
                "core.api.views.platform_users.update_platform_user_identity",
                side_effect=observed_service,
            ):
                request = executor.submit(
                    self._request,
                    self.platform,
                    "patch",
                    self.detail_url(self.target_a),
                    {"full_name": "Must Not Persist"},
                )
                holder.result(timeout=30)
                result = request.result(timeout=30)
        self.assertEqual(result[0], status.HTTP_403_FORBIDDEN)
        self.target_a.refresh_from_db()
        self.assertNotEqual(self.target_a.full_name, "Must Not Persist")

    def test_superuser_revocation_is_revalidated_after_real_lock_wait(self):
        self._assert_revocation_while_waiting("is_superuser")

    def test_actor_deactivation_is_revalidated_after_real_lock_wait(self):
        self._assert_revocation_while_waiting("is_active")

    def test_self_edit_deduplicates_actor_and_target_lock(self):
        status_code, data = self._request(
            self.platform,
            "patch",
            self.detail_url(self.platform),
            {"full_name": "Concurrent Self Edit"},
        )
        self.assertEqual(status_code, 200)
        self.assertEqual(data["full_name"], "Concurrent Self Edit")
        self.platform.refresh_from_db()
        self.assertTrue(self.platform.is_superuser)

    def test_second_patch_waits_on_the_real_target_row_lock(self):
        first_validating = Event()
        release_first = Event()
        second_validating = Event()
        call_lock = Lock()
        call_count = {"value": 0}
        request_pids = []
        original_validate = PlatformUserIdentityUpdateSerializer.is_valid
        original_service = update_platform_user_identity

        def observe_validate(serializer, *args, **kwargs):
            with call_lock:
                call_count["value"] += 1
                current = call_count["value"]
            if current == 1:
                first_validating.set()
                if not release_first.wait(timeout=15):
                    raise TimeoutError("First PATCH was not released")
            else:
                second_validating.set()
            return original_validate(serializer, *args, **kwargs)

        def observe_service(*args, **kwargs):
            with connection.cursor() as cursor:
                cursor.execute("SELECT pg_backend_pid()")
                pid = cursor.fetchone()[0]
            with call_lock:
                request_pids.append(pid)
            return original_service(*args, **kwargs)

        try:
            with ExitStack() as stack:
                stack.enter_context(patch.object(
                    PlatformUserIdentityUpdateSerializer,
                    "is_valid",
                    new=observe_validate,
                ))
                stack.enter_context(patch(
                    "core.api.views.platform_users.update_platform_user_identity",
                    side_effect=observe_service,
                ))
                executor = stack.enter_context(ThreadPoolExecutor(max_workers=2))
                stack.callback(release_first.set)
                first = executor.submit(
                    self._request,
                    self.platform,
                    "patch",
                    self.detail_url(self.target_a),
                    {"email": "locked-target@playnow.test"},
                )
                self.assertTrue(first_validating.wait(timeout=15))
                second = executor.submit(
                    self._request,
                    self.platform,
                    "patch",
                    self.detail_url(self.target_a),
                    {"full_name": "Locked Target Name"},
                )

                deadline = monotonic() + 5
                blocking_state = None
                while monotonic() < deadline:
                    with call_lock:
                        pids = list(request_pids)
                    if len(pids) == 2:
                        with connection.cursor() as cursor:
                            cursor.execute(
                                """
                                SELECT pg_blocking_pids(%s), wait_event_type,
                                       wait_event
                                FROM pg_stat_activity WHERE pid = %s
                                """,
                                [pids[1], pids[1]],
                            )
                            blocking_state = cursor.fetchone()
                        if (
                            blocking_state
                            and pids[0] in blocking_state[0]
                            and blocking_state[1] == "Lock"
                        ):
                            break
                    if second.done() or second_validating.is_set():
                        self.fail("Second PATCH passed the locked target row")
                    release_first.wait(timeout=0.05)
                else:
                    self.fail("PostgreSQL did not report the target lock wait")

                self.assertFalse(second.done())
                self.assertFalse(second_validating.is_set())
                release_first.set()
                self.assertEqual(first.result(timeout=30)[0], 200)
                self.assertEqual(second.result(timeout=30)[0], 200)
        finally:
            release_first.set()

        self.target_a.refresh_from_db()
        self.assertEqual(self.target_a.email, "locked-target@playnow.test")
        self.assertEqual(self.target_a.full_name, "Locked Target Name")
