from concurrent.futures import ThreadPoolExecutor
from threading import Barrier, Event
from unittest import skipUnless
from unittest.mock import patch

from django.contrib.auth import authenticate
from django.db import close_old_connections, connection, connections, transaction
from django.test import TransactionTestCase
from django.urls import resolve, reverse
from drf_spectacular.generators import SchemaGenerator
from rest_framework import status
from rest_framework.test import APIClient, APITestCase
from rest_framework_simplejwt.tokens import RefreshToken

from core.models import BusinessMembership, Employee, EntityStatus, User
from core.services.business_users import (
    BusinessUserProvisioningError,
    provision_business_user,
)
from core.tests.factories import (
    create_business,
    create_membership,
    create_role_user,
    create_status,
    create_user,
)
from core.views import BusinessUserCreateView


ALLOWED_ROLES = (
    BusinessMembership.ROLE_ADMIN,
    BusinessMembership.ROLE_CASHIER,
    BusinessMembership.ROLE_SELLER,
    BusinessMembership.ROLE_INVENTORY,
    BusinessMembership.ROLE_VIEWER,
)


class BusinessUserProvisioningAPITests(APITestCase):
    @classmethod
    def setUpTestData(cls):
        cls.active = create_status("Activo")
        cls.owner = create_user(email="phase4-owner@playnow.test")
        cls.business = create_business(user=cls.owner, status=cls.active)
        cls.owner_membership = BusinessMembership.objects.get(
            user=cls.owner,
            business=cls.business,
        )
        cls.platform = create_user(
            email="phase4-platform@playnow.test",
            is_superuser=True,
        )
        cls.outsider = create_user(email="phase4-outsider@playnow.test")
        cls.foreign_business = create_business(
            user=cls.outsider,
            status=cls.active,
        )
        cls.role_users = {}
        for role in ALLOWED_ROLES:
            cls.role_users[role] = create_role_user(
                business=cls.business,
                role=role,
                status=cls.active,
                email=f"phase4-{role}@playnow.test",
            )[0]
        cls.inactive_member = create_user(
            email="phase4-inactive-member@playnow.test"
        )
        create_membership(
            user=cls.inactive_member,
            business=cls.business,
            role=BusinessMembership.ROLE_OWNER,
            is_active=False,
        )
        cls.inactive_owner = create_user(
            email="phase4-inactive-owner@playnow.test"
        )
        create_membership(
            user=cls.inactive_owner,
            business=cls.business,
            role=BusinessMembership.ROLE_OWNER,
        )
        cls.inactive_owner.is_active = False
        cls.inactive_owner.save(update_fields=["is_active"])

    def url(self, business=None):
        return reverse(
            "business-user-create",
            kwargs={
                "public_id": (business or self.business).public_id,
            },
        )

    @staticmethod
    def payload(*, number=1, role=BusinessMembership.ROLE_SELLER, **extra):
        return {
            "email": f"phase4-new-{number}@playnow.test",
            "full_name": f"Phase Four User {number}",
            "password": f"SafePhase4Password-{number}!",
            "password_confirmation": f"SafePhase4Password-{number}!",
            "role": role,
            **extra,
        }

    def post_as(self, actor, payload, business=None):
        self.client.force_authenticate(user=actor)
        return self.client.post(self.url(business), payload, format="json")

    def assert_created_contract(self, response, payload):
        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        self.assertEqual(response.data["email"], payload["email"].lower())
        self.assertEqual(response.data["full_name"], payload["full_name"])
        self.assertEqual(response.data["role"], payload["role"])
        self.assertTrue(response.data["is_active"])
        self.assertIsNone(response.data["employee_public_id"])
        self.assertEqual(
            str(response.data["business_public_id"]),
            str(self.business.public_id),
        )
        self.assertNotIn("password", response.data)
        self.assertNotIn("password_confirmation", response.data)
        user = User.objects.get(public_id=response.data["user_public_id"])
        membership = BusinessMembership.objects.get(
            public_id=response.data["membership_public_id"]
        )
        self.assertNotEqual(user.password, payload["password"])
        self.assertTrue(user.check_password(payload["password"]))
        self.assertIsNotNone(
            authenticate(email=user.email, password=payload["password"])
        )
        self.assertEqual(user.role, User.Roles.EMPLOYEE)
        self.assertTrue(user.is_active)
        self.assertFalse(user.is_staff)
        self.assertFalse(user.is_superuser)
        self.assertEqual(membership.user, user)
        self.assertEqual(membership.business, self.business)
        self.assertIsNone(membership.employee)
        self.assertEqual(
            BusinessMembership.objects.filter(user=user).count(),
            1,
        )

    def test_platform_admin_and_owner_can_create_every_non_owner_role(self):
        number = 10
        before_employees = Employee.objects.count()
        for actor in (self.platform, self.owner):
            for role in ALLOWED_ROLES:
                number += 1
                with self.subTest(actor=actor.email, role=role):
                    payload = self.payload(number=number, role=role)
                    self.assert_created_contract(
                        self.post_as(actor, payload),
                        payload,
                    )
        self.assertEqual(Employee.objects.count(), before_employees)

    def test_only_owner_or_platform_admin_can_invoke_endpoint(self):
        actors_and_statuses = [
            (self.role_users[BusinessMembership.ROLE_ADMIN], 403),
            (self.role_users[BusinessMembership.ROLE_CASHIER], 403),
            (self.role_users[BusinessMembership.ROLE_SELLER], 403),
            (self.role_users[BusinessMembership.ROLE_INVENTORY], 403),
            (self.role_users[BusinessMembership.ROLE_VIEWER], 403),
            (self.outsider, 404),
            (self.inactive_member, 404),
            (self.inactive_owner, 404),
        ]
        before = (User.objects.count(), BusinessMembership.objects.count())
        for number, (actor, expected_status) in enumerate(
            actors_and_statuses,
            start=100,
        ):
            with self.subTest(actor=actor.email):
                response = self.post_as(actor, self.payload(number=number))
                self.assertEqual(response.status_code, expected_status)
        self.assertEqual(
            (User.objects.count(), BusinessMembership.objects.count()),
            before,
        )

    def test_anonymous_foreign_and_missing_business_are_hidden(self):
        response = self.client.post(self.url(), self.payload(number=200), format="json")
        self.assertEqual(response.status_code, status.HTTP_401_UNAUTHORIZED)
        foreign = self.post_as(self.outsider, self.payload(number=201))
        missing_url = reverse(
            "business-user-create",
            kwargs={"public_id": "00000000-0000-0000-0000-000000000001"},
        )
        self.client.force_authenticate(user=self.outsider)
        missing = self.client.post(missing_url, self.payload(number=202), format="json")
        self.assertEqual(foreign.status_code, status.HTTP_404_NOT_FOUND)
        self.assertEqual(missing.status_code, status.HTTP_404_NOT_FOUND)
        self.assertEqual(foreign.data, missing.data)

    def test_unauthorized_actor_cannot_enumerate_email_or_payload_validity(self):
        existing = self.payload(number=250)
        existing["email"] = self.owner.email
        missing = self.payload(number=251)
        malformed = {"email": self.owner.email}
        responses = [
            self.post_as(self.outsider, existing),
            self.post_as(self.outsider, missing),
            self.post_as(self.outsider, malformed),
        ]
        self.assertTrue(
            all(response.status_code == status.HTTP_404_NOT_FOUND for response in responses)
        )
        self.assertEqual(responses[0].data, responses[1].data)
        self.assertEqual(responses[1].data, responses[2].data)

    def test_role_user_role_legacy_and_payload_are_strict(self):
        self.owner.role = User.Roles.EMPLOYEE
        self.owner.save(update_fields=["role"])
        valid = self.payload(number=300)
        self.assert_created_contract(self.post_as(self.owner, valid), valid)

        for number, role in enumerate(("owner", "", "global_admin"), start=301):
            with self.subTest(role=role):
                response = self.post_as(
                    self.owner,
                    self.payload(number=number, role=role),
                )
                self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

        protected = (
            "is_superuser",
            "is_staff",
            "groups",
            "user_permissions",
            "is_active",
            "user_role",
            "business",
            "membership_is_active",
            "employee",
            "employee_public_id",
            "created_by",
            "public_id",
            "unknown",
        )
        for number, field in enumerate(protected, start=310):
            with self.subTest(field=field):
                response = self.post_as(
                    self.owner,
                    self.payload(number=number, **{field: True}),
                )
                self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
                self.assertIn(field, response.data)

    def test_password_confirmation_validators_hash_authentication_and_logging(self):
        mismatch = self.payload(number=400)
        mismatch["password_confirmation"] = "DifferentPassword-400!"
        response = self.post_as(self.owner, mismatch)
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn("password_confirmation", response.data)

        weak = self.payload(number=401)
        weak["password"] = weak["password_confirmation"] = "12345678"
        response = self.post_as(self.owner, weak)
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn("password", response.data)

        valid = self.payload(number=402)
        with self.assertLogs("audit", level="INFO") as captured:
            response = self.post_as(self.owner, valid)
        self.assert_created_contract(response, valid)
        self.assertNotIn(valid["password"], "\n".join(captured.output))

    def test_existing_email_never_changes_user_or_adds_membership(self):
        existing = create_user(
            email="phase4-existing@playnow.test",
            full_name="Existing Identity",
            password="OriginalPassword-500!",
        )
        existing.is_active = False
        existing.save(update_fields=["is_active"])
        snapshot = User.objects.values().get(pk=existing.pk)
        before = (User.objects.count(), BusinessMembership.objects.count())

        for number, email in enumerate(
            (existing.email, "  PHASE4-EXISTING@PLAYNOW.TEST  "),
            start=500,
        ):
            payload = self.payload(number=number)
            payload["email"] = email
            response = self.post_as(self.owner, payload)
            self.assertEqual(response.status_code, status.HTTP_409_CONFLICT)
            self.assertIn("agregar un usuario existente", str(response.data))

        self.assertEqual(User.objects.values().get(pk=existing.pk), snapshot)
        self.assertFalse(
            BusinessMembership.objects.filter(
                user=existing,
                business=self.business,
            ).exists()
        )
        self.assertEqual(
            (User.objects.count(), BusinessMembership.objects.count()),
            before,
        )

    def test_legacy_create_access_still_creates_employee_linked_access(self):
        self.client.force_authenticate(user=self.owner)
        before_employees = Employee.objects.count()
        response = self.client.post(
            reverse(
                "business-create-employee-access",
                kwargs={"public_id": self.business.public_id},
            ),
            {
                "email": "phase4-legacy-access@playnow.test",
                "password": "LegacyAccessPassword-600!",
                "full_name": "Legacy Employee",
                "position": "Seller",
                "phone": "",
                "role": BusinessMembership.ROLE_SELLER,
            },
            format="json",
        )
        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        membership = BusinessMembership.objects.get(
            user__email="phase4-legacy-access@playnow.test"
        )
        self.assertIsNotNone(membership.employee)
        self.assertEqual(Employee.objects.count(), before_employees + 1)

    def test_owner_blocking_changes_only_membership_and_self_password_change_isolated(self):
        payload = self.payload(number=650)
        response = self.post_as(self.owner, payload)
        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        user = User.objects.get(public_id=response.data["user_public_id"])
        membership = BusinessMembership.objects.get(
            public_id=response.data["membership_public_id"]
        )
        other_membership = create_membership(
            user=user,
            business=self.foreign_business,
            role=BusinessMembership.ROLE_VIEWER,
        )
        user_snapshot = User.objects.values().get(pk=user.pk)
        other_snapshot = BusinessMembership.objects.values().get(
            pk=other_membership.pk
        )

        self.client.force_authenticate(user=self.owner)
        membership_url = reverse(
            "business-membership-detail",
            kwargs={"membership_public_id": membership.public_id},
        )
        blocked = self.client.patch(
            membership_url,
            {"is_active": False},
            format="json",
        )
        self.assertEqual(blocked.status_code, status.HTTP_200_OK)
        membership.refresh_from_db()
        self.assertFalse(membership.is_active)
        self.assertEqual(User.objects.values().get(pk=user.pk), user_snapshot)
        self.assertEqual(
            BusinessMembership.objects.values().get(pk=other_membership.pk),
            other_snapshot,
        )

        reactivated = self.client.patch(
            membership_url,
            {"is_active": True},
            format="json",
        )
        self.assertEqual(reactivated.status_code, status.HTTP_200_OK)
        membership.refresh_from_db()
        self.assertTrue(membership.is_active)
        self.assertIsNone(membership.employee_id)

        self.client.force_authenticate(user=user)
        changed = self.client.post(
            reverse("user-change-password"),
            {
                "current_password": payload["password"],
                "new_password": "ChangedSelfPassword-651!",
            },
            format="json",
        )
        self.assertEqual(changed.status_code, status.HTTP_204_NO_CONTENT)
        membership.refresh_from_db()
        other_membership.refresh_from_db()
        self.assertTrue(membership.is_active)
        self.assertTrue(other_membership.is_active)

    def test_openapi_documents_closed_request_and_safe_response(self):
        schema = SchemaGenerator().get_schema(request=None, public=True)
        operation = schema["paths"][
            "/api/businesses/{public_id}/users/"
        ]["post"]
        self.assertEqual(set(operation["responses"]), {
            "201", "400", "401", "403", "404", "409"
        })
        parameter = operation["parameters"][0]
        self.assertEqual(parameter["name"], "public_id")
        self.assertEqual(parameter["schema"]["format"], "uuid")
        request_name = operation["requestBody"]["content"][
            "application/json"
        ]["schema"]["$ref"].rsplit("/", 1)[1]
        request_schema = schema["components"]["schemas"][request_name]
        self.assertEqual(set(request_schema["properties"]), {
            "email", "full_name", "password", "password_confirmation", "role"
        })
        self.assertTrue(request_schema["properties"]["password"]["writeOnly"])
        self.assertTrue(
            request_schema["properties"]["password_confirmation"]["writeOnly"]
        )

    def test_route_reverse_resolve_and_method_contract(self):
        url = self.url()
        self.assertEqual(
            url,
            f"/api/businesses/{self.business.public_id}/users/",
        )
        match = resolve(url)
        self.assertEqual(match.url_name, "business-user-create")
        self.assertIs(match.func.view_class, BusinessUserCreateView)
        self.assertEqual(match.kwargs["public_id"], self.business.public_id)
        self.client.force_authenticate(user=self.owner)
        for method in ("get", "put", "patch", "delete"):
            with self.subTest(method=method):
                response = getattr(self.client, method)(url, {}, format="json")
                self.assertEqual(response.status_code, status.HTTP_405_METHOD_NOT_ALLOWED)


class BusinessUserProvisioningRollbackTests(TransactionTestCase):
    reset_sequences = True

    def setUp(self):
        active = create_status("Activo")
        self.owner = create_user(email="phase4-rollback-owner@playnow.test")
        self.business = create_business(user=self.owner, status=active)

    def payload(self, email):
        return {
            "email": email,
            "full_name": "Rollback User",
            "password": "RollbackPassword-700!",
            "password_confirmation": "RollbackPassword-700!",
            "role": BusinessMembership.ROLE_SELLER,
        }

    def invoke(self, payload):
        return provision_business_user(
            actor=self.owner,
            business_public_id=self.business.public_id,
            validate_payload=lambda: payload,
        )

    def test_membership_final_validation_and_audit_failures_roll_back_user(self):
        before = (User.objects.count(), BusinessMembership.objects.count())
        cases = (
            ("core.services.business_users.User.objects.create_user", RuntimeError),
            ("core.services.business_users.BusinessMembership.objects.create", RuntimeError),
            ("core.services.business_users._assert_final_state", RuntimeError),
            ("core.services.business_users.log_action", RuntimeError),
        )
        for number, (target, failure) in enumerate(cases, start=700):
            with self.subTest(target=target):
                with patch(target, side_effect=failure("forced rollback")):
                    with self.assertRaises(RuntimeError):
                        self.invoke(self.payload(f"phase4-rollback-{number}@playnow.test"))
                self.assertEqual(
                    (User.objects.count(), BusinessMembership.objects.count()),
                    before,
                )

    def test_service_rejects_owner_role_without_serializer_dependency(self):
        before = (User.objects.count(), BusinessMembership.objects.count())
        payload = self.payload("phase4-service-owner@playnow.test")
        payload["role"] = BusinessMembership.ROLE_OWNER
        with self.assertRaisesMessage(
            BusinessUserProvisioningError,
            "rol permitido distinto de owner",
        ):
            self.invoke(payload)
        self.assertEqual(
            (User.objects.count(), BusinessMembership.objects.count()),
            before,
        )


@skipUnless(connection.vendor == "postgresql", "Requires PostgreSQL row locks")
class BusinessUserProvisioningConcurrencyTests(TransactionTestCase):
    reset_sequences = True

    def setUp(self):
        self.active = create_status("Activo")
        self.deleted = create_status("Eliminado")
        self.owner = create_user(email="phase4-race-owner@playnow.test")
        self.business = create_business(user=self.owner, status=self.active)
        self.owner_membership = BusinessMembership.objects.get(
            user=self.owner,
            business=self.business,
        )
        self.platform = create_user(
            email="phase4-race-platform@playnow.test",
            is_superuser=True,
        )

    def url(self):
        return reverse(
            "business-user-create",
            kwargs={"public_id": self.business.public_id},
        )

    @staticmethod
    def payload(email, password):
        return {
            "email": email,
            "full_name": "Concurrent User",
            "password": password,
            "password_confirmation": password,
            "role": BusinessMembership.ROLE_SELLER,
        }

    def jwt_client(self, actor):
        client = APIClient()
        token = RefreshToken.for_user(actor).access_token
        client.credentials(HTTP_AUTHORIZATION=f"Bearer {token}")
        return client

    def request_worker(self, actor, payload, barrier=None):
        close_old_connections()
        try:
            client = self.jwt_client(actor)
            if barrier is not None:
                barrier.wait(timeout=10)
            return client.post(self.url(), payload, format="json")
        finally:
            close_old_connections()
            connections.close_all()

    def test_same_email_different_casing_creates_exactly_once(self):
        barrier = Barrier(2)
        first_password = "ConcurrentWinnerPassword-A1!"
        second_password = "ConcurrentWinnerPassword-B2!"
        with ThreadPoolExecutor(max_workers=2) as executor:
            futures = [
                executor.submit(
                    self.request_worker,
                    self.owner,
                    self.payload("phase4-race-email@playnow.test", first_password),
                    barrier,
                ),
                executor.submit(
                    self.request_worker,
                    self.owner,
                    self.payload("PHASE4-RACE-EMAIL@PLAYNOW.TEST", second_password),
                    barrier,
                ),
            ]
            responses = [future.result(timeout=30) for future in futures]
        self.assertEqual(sorted(response.status_code for response in responses), [201, 409])
        users = User.objects.filter(email__iexact="phase4-race-email@playnow.test")
        self.assertEqual(users.count(), 1)
        user = users.get()
        self.assertTrue(
            user.check_password(first_password) or user.check_password(second_password)
        )
        self.assertEqual(
            BusinessMembership.objects.filter(
                user=user,
                business=self.business,
            ).count(),
            1,
        )

    def run_revocation_while_request_waits(
        self,
        *,
        actor,
        mutation,
        expected_status,
    ):
        business_locked = Event()
        request_before_lock = Event()
        release_mutation = Event()
        from core.services import business_users as service

        original_lock = service._lock_business_by_public_id

        def observed_lock(public_id):
            request_before_lock.set()
            return original_lock(public_id)

        def mutate():
            close_old_connections()
            try:
                with transaction.atomic():
                    business = type(self.business).objects.select_for_update().get(
                        pk=self.business.pk
                    )
                    business_locked.set()
                    if not request_before_lock.wait(timeout=15):
                        raise TimeoutError("Request did not reach the production lock")
                    mutation(business)
                    if not release_mutation.wait(timeout=15):
                        raise TimeoutError("Mutation was not released")
            finally:
                close_old_connections()
                connections.close_all()

        payload = self.payload(
            f"phase4-revoked-{actor.pk}@playnow.test",
            "RevokedActorPassword-800!",
        )
        with patch.object(service, "_lock_business_by_public_id", side_effect=observed_lock):
            with ThreadPoolExecutor(max_workers=2) as executor:
                mutation_future = executor.submit(mutate)
                self.assertTrue(business_locked.wait(timeout=10))
                request_future = executor.submit(self.request_worker, actor, payload)
                self.assertTrue(request_before_lock.wait(timeout=10))
                release_mutation.set()
                mutation_future.result(timeout=20)
                response = request_future.result(timeout=20)
        self.assertEqual(response.status_code, expected_status)
        self.assertFalse(User.objects.filter(email__iexact=payload["email"]).exists())

    def test_platform_privilege_revoked_while_waiting(self):
        def mutation(_business):
            actor = User.objects.select_for_update().get(pk=self.platform.pk)
            actor.is_superuser = False
            actor.save(update_fields=["is_superuser"])

        self.run_revocation_while_request_waits(
            actor=self.platform,
            mutation=mutation,
            expected_status=404,
        )

    def test_owner_user_deactivated_while_waiting(self):
        def mutation(business):
            BusinessMembership.objects.select_for_update().get(
                business=business,
                user=self.owner,
            )
            actor = User.objects.select_for_update().get(pk=self.owner.pk)
            actor.is_active = False
            actor.save(update_fields=["is_active"])

        self.run_revocation_while_request_waits(
            actor=self.owner,
            mutation=mutation,
            expected_status=404,
        )

    def test_owner_membership_deactivated_while_waiting(self):
        def mutation(business):
            membership = BusinessMembership.objects.select_for_update().get(
                business=business,
                user=self.owner,
            )
            membership.is_active = False
            membership.save(update_fields=["is_active", "updated_at"])

        self.run_revocation_while_request_waits(
            actor=self.owner,
            mutation=mutation,
            expected_status=404,
        )

    def test_owner_role_downgraded_while_waiting(self):
        def mutation(business):
            membership = BusinessMembership.objects.select_for_update().get(
                business=business,
                user=self.owner,
            )
            membership.role = BusinessMembership.ROLE_ADMIN
            membership.save(update_fields=["role", "updated_at"])

        self.run_revocation_while_request_waits(
            actor=self.owner,
            mutation=mutation,
            expected_status=403,
        )

    def test_business_becomes_deleted_while_waiting(self):
        def mutation(business):
            business.status = self.deleted
            business.save(update_fields=["status", "updated_at"])

        self.run_revocation_while_request_waits(
            actor=self.owner,
            mutation=mutation,
            expected_status=404,
        )
