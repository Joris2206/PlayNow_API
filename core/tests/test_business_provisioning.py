from concurrent.futures import ThreadPoolExecutor
from threading import Barrier, Event
from unittest.mock import patch
from uuid import uuid4

from django.db import IntegrityError, close_old_connections, connections, transaction
from django.test import TransactionTestCase
from django.urls import reverse
from drf_spectacular.generators import SchemaGenerator
from rest_framework import status
from rest_framework.test import APIClient, APITestCase

from core.models import Business, BusinessMembership, Employee, User
from core.services import business_provisioning as business_provisioning_service
from core.services.business_provisioning import (
    INITIAL_OWNER_ERROR,
    BusinessProvisioningError,
    provision_business,
)
from core.tests.factories import create_business, create_status, create_user


class BusinessProvisioningAPITests(APITestCase):
    @classmethod
    def setUpTestData(cls):
        cls.active = create_status("Activo")
        cls.inactive = create_status("Inactivo")
        cls.platform = create_user(
            email="provision-platform@playnow.test",
            is_superuser=True,
        )
        cls.owner = create_user(email="provision-owner@playnow.test")
        cls.inactive_owner = create_user(email="provision-inactive@playnow.test")
        User.objects.filter(pk=cls.inactive_owner.pk).update(is_active=False)
        cls.other_creator = create_user(email="provision-other@playnow.test")
        cls.other_business = create_business(
            user=cls.other_creator,
            status=cls.active,
        )

    def payload(self, **overrides):
        data = {
            "business_name": "Provisioned Business",
            "description": "Created atomically",
            "currency": "NIO",
        }
        data.update(overrides)
        return data

    def authenticate(self, user):
        self.client.force_authenticate(user=user)

    def assert_business_contract(self, response):
        self.assertEqual(
            tuple(response.data),
            (
                "public_id",
                "business_name",
                "description",
                "currency",
                "status_public_id",
                "status_name",
                "created_at",
                "updated_at",
            ),
        )
        self.assertNotIn("initial_owner_email", response.data)

    def test_all_legacy_roles_use_the_same_normal_creation_policy(self):
        for role in User.Roles.values:
            with self.subTest(role=role):
                user = create_user(
                    email=f"normal-{role}@playnow.test",
                    role=role,
                )
                self.authenticate(user)
                response = self.client.post(
                    reverse("business-list"),
                    self.payload(business_name=f"Business {role}"),
                    format="json",
                )
                self.assertEqual(response.status_code, status.HTTP_201_CREATED)
                self.assert_business_contract(response)
                business = Business.objects.get(public_id=response.data["public_id"])
                self.assertEqual(business.user_id, user.pk)
                membership = BusinessMembership.objects.get(
                    business=business,
                    user=user,
                )
                self.assertEqual(membership.role, BusinessMembership.ROLE_OWNER)
                self.assertTrue(membership.is_active)
                self.assertIsNone(membership.employee_id)

    def test_normal_user_cannot_submit_initial_owner_email(self):
        self.authenticate(self.owner)
        before = Business.objects.count()
        response = self.client.post(
            reverse("business-list"),
            self.payload(initial_owner_email=self.other_creator.email),
            format="json",
        )
        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)
        self.assertEqual(Business.objects.count(), before)

    def test_platform_admin_assigns_existing_owner_without_implicit_membership(self):
        self.authenticate(self.platform)
        users_before = User.objects.count()
        employees_before = Employee.objects.count()
        response = self.client.post(
            reverse("business-list"),
            self.payload(
                initial_owner_email=f"  {self.owner.email.upper()}  ",
            ),
            format="json",
        )
        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        self.assert_business_contract(response)
        business = Business.objects.get(public_id=response.data["public_id"])
        self.assertEqual(business.user_id, self.platform.pk)
        membership = BusinessMembership.objects.get(business=business)
        self.assertEqual(membership.user_id, self.owner.pk)
        self.assertEqual(membership.role, BusinessMembership.ROLE_OWNER)
        self.assertTrue(membership.is_active)
        self.assertIsNone(membership.employee_id)
        self.assertFalse(
            BusinessMembership.objects.filter(
                business=business,
                user=self.platform,
            ).exists()
        )
        self.assertEqual(User.objects.count(), users_before)
        self.assertEqual(Employee.objects.count(), employees_before)

    def test_owner_can_already_belong_to_another_business(self):
        existing = BusinessMembership.objects.create(
            user=self.owner,
            business=self.other_business,
            role=BusinessMembership.ROLE_VIEWER,
            is_active=True,
        )
        self.authenticate(self.platform)
        response = self.client.post(
            reverse("business-list"),
            self.payload(initial_owner_email=self.owner.email),
            format="json",
        )
        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        existing.refresh_from_db()
        self.assertEqual(existing.role, BusinessMembership.ROLE_VIEWER)

    def test_platform_admin_can_assign_another_platform_admin_or_self(self):
        other_platform = create_user(
            email="provision-other-platform@playnow.test",
            is_superuser=True,
        )
        self.authenticate(self.platform)
        for owner in (other_platform, self.platform):
            with self.subTest(owner=owner.email):
                response = self.client.post(
                    reverse("business-list"),
                    self.payload(
                        business_name=f"Business {owner.pk}",
                        initial_owner_email=owner.email,
                    ),
                    format="json",
                )
                self.assertEqual(response.status_code, status.HTTP_201_CREATED)
                business = Business.objects.get(public_id=response.data["public_id"])
                memberships = BusinessMembership.objects.filter(business=business)
                self.assertEqual(memberships.count(), 1)
                self.assertEqual(memberships.get().user_id, owner.pk)
                self.assertIsNone(memberships.get().employee_id)

    def test_platform_admin_owner_errors_are_uniform_and_atomic(self):
        self.authenticate(self.platform)
        cases = (
            ({}, "missing"),
            ({"initial_owner_email": "missing@playnow.test"}, "missing-user"),
            ({"initial_owner_email": self.inactive_owner.email}, "inactive"),
        )
        errors = []
        for fields, label in cases:
            with self.subTest(case=label):
                before = (Business.objects.count(), BusinessMembership.objects.count())
                response = self.client.post(
                    reverse("business-list"),
                    self.payload(**fields),
                    format="json",
                )
                self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
                self.assertIn("initial_owner_email", response.data)
                self.assertEqual(
                    (Business.objects.count(), BusinessMembership.objects.count()),
                    before,
                )
                errors.append(str(response.data["initial_owner_email"][0]))
        self.assertEqual(errors[1:], [INITIAL_OWNER_ERROR, INITIAL_OWNER_ERROR])

    def test_invalid_email_unknown_and_privileged_fields_are_rejected(self):
        self.authenticate(self.platform)
        invalid_email = self.client.post(
            reverse("business-list"),
            self.payload(initial_owner_email="not-an-email"),
            format="json",
        )
        self.assertEqual(invalid_email.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn("initial_owner_email", invalid_email.data)
        response = self.client.post(
            reverse("business-list"),
            self.payload(
                initial_owner_email=self.owner.email,
                is_superuser=True,
                is_staff=True,
                groups=[],
                user_permissions=[],
                role=User.Roles.BUSINESS_OWNER,
                password="secret",
                user_public_id=str(uuid4()),
            ),
            format="json",
        )
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        for field in (
            "is_superuser",
            "is_staff",
            "groups",
            "user_permissions",
            "role",
            "password",
            "user_public_id",
        ):
            self.assertIn(field, response.data)

    def test_anonymous_is_rejected_and_explicit_status_is_preserved(self):
        anonymous = self.client.post(
            reverse("business-list"),
            self.payload(),
            format="json",
        )
        self.assertEqual(anonymous.status_code, status.HTTP_401_UNAUTHORIZED)
        self.authenticate(self.owner)
        response = self.client.post(
            reverse("business-list"),
            self.payload(status_public_id=str(self.inactive.public_id)),
            format="json",
        )
        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        business = Business.objects.get(public_id=response.data["public_id"])
        self.assertEqual(business.status_id, self.inactive.pk)

    def test_openapi_documents_conditional_write_only_owner_email(self):
        schema = SchemaGenerator().get_schema(request=None, public=True)
        operation = schema["paths"]["/api/businesses/"]["post"]
        request_schema = operation["requestBody"]["content"][
            "application/json"
        ]["schema"]
        self.assertEqual(
            request_schema["$ref"],
            "#/components/schemas/BusinessCreateRequest",
        )
        component = schema["components"]["schemas"]["BusinessCreateRequest"]
        owner_field = component["properties"]["initial_owner_email"]
        self.assertEqual(owner_field["format"], "email")
        self.assertTrue(owner_field["writeOnly"])
        self.assertNotIn("initial_owner_email", component["required"])
        self.assertEqual(
            operation["responses"]["201"]["content"]["application/json"][
                "schema"
            ]["$ref"],
            "#/components/schemas/Business",
        )

    def test_logging_failure_rolls_back_business_and_membership(self):
        self.authenticate(self.owner)
        before = (Business.objects.count(), BusinessMembership.objects.count())
        with patch("core.views.log_action", side_effect=RuntimeError("log failed")):
            with self.assertRaises(RuntimeError):
                self.client.post(
                    reverse("business-list"),
                    self.payload(business_name="Rolled Back Log"),
                    format="json",
                )
        self.assertEqual(
            (Business.objects.count(), BusinessMembership.objects.count()),
            before,
        )


class BusinessProvisioningTransactionTests(TransactionTestCase):
    reset_sequences = True

    def setUp(self):
        self.active = create_status("Activo")
        self.actor = create_user(email="provision-race-actor@playnow.test")
        self.platform = create_user(
            email="provision-race-platform@playnow.test",
            is_superuser=True,
        )
        self.owner = create_user(email="provision-race-owner@playnow.test")

    def data(self, name):
        return {
            "business_name": name,
            "description": "",
            "currency": "NIO",
            "status": self.active,
        }

    def state(self):
        return (
            Business.objects.count(),
            BusinessMembership.objects.count(),
            Employee.objects.count(),
        )

    def run_after_committed_change(self, *, prepare_operation, commit_change):
        ready = Event()
        proceed = Event()

        def worker():
            close_old_connections()
            try:
                operation = prepare_operation()
                ready.set()
                if not proceed.wait(timeout=10):
                    raise TimeoutError("The provisioning request was not released.")
                try:
                    return operation()
                except BusinessProvisioningError as exc:
                    return exc.kind, exc.detail
            finally:
                close_old_connections()
                connections.close_all()

        with ThreadPoolExecutor(max_workers=1) as executor:
            future = executor.submit(worker)
            if not ready.wait(timeout=10):
                proceed.set()
                future.result(timeout=20)
                self.fail("The provisioning operation was not prepared.")
            try:
                commit_change()
            finally:
                proceed.set()
            return future.result(timeout=20)

    def run_service_while_user_is_locked(
        self,
        *,
        locked_user_id,
        changes,
        operation,
    ):
        row_locked = Event()
        service_at_lock = Event()
        release_holder = Event()
        service_finished = Event()
        original_lock_users = business_provisioning_service._lock_users

        def holder():
            close_old_connections()
            try:
                with transaction.atomic():
                    locked_user = User.objects.select_for_update().get(
                        pk=locked_user_id,
                    )
                    row_locked.set()
                    if not service_at_lock.wait(timeout=10):
                        raise TimeoutError("The service did not reach its User lock.")
                    for field, value in changes.items():
                        setattr(locked_user, field, value)
                    locked_user.save(
                        update_fields=[*changes.keys(), "updated_at"],
                    )
                    if not release_holder.wait(timeout=10):
                        raise TimeoutError("The holder transaction was not released.")
            finally:
                close_old_connections()
                connections.close_all()

        def synchronized_lock_users(user_ids):
            service_at_lock.set()
            return original_lock_users(user_ids)

        def service_worker():
            close_old_connections()
            try:
                if not row_locked.wait(timeout=10):
                    raise TimeoutError("The User row was not locked.")
                with patch(
                    "core.services.business_provisioning._lock_users",
                    side_effect=synchronized_lock_users,
                ):
                    try:
                        return operation()
                    except BusinessProvisioningError as exc:
                        return exc.kind, exc.detail
            finally:
                service_finished.set()
                close_old_connections()
                connections.close_all()

        with ThreadPoolExecutor(max_workers=2) as executor:
            holder_future = executor.submit(holder)
            if not row_locked.wait(timeout=10):
                release_holder.set()
                holder_future.result(timeout=20)
                self.fail("The holder did not acquire the User lock.")
            service_future = executor.submit(service_worker)
            if not service_at_lock.wait(timeout=10):
                release_holder.set()
                service_future.result(timeout=20)
                holder_future.result(timeout=20)
                self.fail("The service did not attempt its User lock.")
            try:
                self.assertFalse(
                    service_finished.wait(timeout=0.5),
                    "The provisioning service did not wait on the User row lock.",
                )
            finally:
                release_holder.set()
            holder_future.result(timeout=20)
            return service_future.result(timeout=20)

    def test_revoked_platform_admin_does_not_fall_back_to_normal_creation(self):
        before = self.state()

        def prepare():
            actor = User.objects.get(pk=self.platform.pk)
            return lambda: provision_business(
                actor=actor,
                validated_business_data=self.data("Revoked Platform"),
                initial_owner_email=self.owner.email,
            )

        result = self.run_after_committed_change(
            prepare_operation=prepare,
            commit_change=lambda: User.objects.filter(pk=self.platform.pk).update(
                is_superuser=False,
                is_staff=False,
            ),
        )
        self.assertEqual(result[0], "forbidden")
        self.assertEqual(self.state(), before)

    def test_actor_deactivation_rolls_back_everything(self):
        before = self.state()

        def prepare():
            actor = User.objects.get(pk=self.actor.pk)
            return lambda: provision_business(
                actor=actor,
                validated_business_data=self.data("Inactive Actor"),
            )

        result = self.run_after_committed_change(
            prepare_operation=prepare,
            commit_change=lambda: User.objects.filter(pk=self.actor.pk).update(
                is_active=False,
            ),
        )
        self.assertEqual(result[0], "forbidden")
        self.assertEqual(self.state(), before)

    def test_owner_deactivation_and_email_change_are_revalidated(self):
        for field, value in (
            ("is_active", False),
            ("email", "changed-owner@playnow.test"),
        ):
            with self.subTest(field=field):
                User.objects.filter(pk=self.owner.pk).update(
                    is_active=True,
                    email="provision-race-owner@playnow.test",
                )
                self.owner.refresh_from_db()
                captured_email = self.owner.email
                before = self.state()

                def prepare():
                    actor = User.objects.get(pk=self.platform.pk)
                    return lambda: provision_business(
                        actor=actor,
                        validated_business_data=self.data(f"Changed {field}"),
                        initial_owner_email=captured_email,
                    )

                result = self.run_after_committed_change(
                    prepare_operation=prepare,
                    commit_change=lambda: User.objects.filter(
                        pk=self.owner.pk,
                    ).update(**{field: value}),
                )
                self.assertEqual(result[0], "invalid")
                self.assertEqual(self.state(), before)

    def test_actor_gaining_superuser_does_not_silently_keep_normal_mode(self):
        before = self.state()

        def prepare():
            actor = User.objects.get(pk=self.actor.pk)
            return lambda: provision_business(
                actor=actor,
                validated_business_data=self.data("Promoted Actor"),
            )

        result = self.run_after_committed_change(
            prepare_operation=prepare,
            commit_change=lambda: User.objects.filter(pk=self.actor.pk).update(
                is_superuser=True,
                is_staff=True,
            ),
        )
        self.assertEqual(result[0], "invalid")
        self.assertIn("initial_owner_email", result[1])
        self.assertEqual(self.state(), before)

    def test_stale_actor_attributes_never_decide_the_requested_mode(self):
        stale_platform = User.objects.get(pk=self.platform.pk)
        User.objects.filter(pk=self.platform.pk).update(
            is_superuser=False,
            is_staff=False,
        )
        fresh_normal = User.objects.get(pk=self.platform.pk)
        outcomes = []
        for actor in (stale_platform, fresh_normal):
            with self.assertRaises(BusinessProvisioningError) as context:
                provision_business(
                    actor=actor,
                    validated_business_data=self.data("Rejected Admin Mode"),
                    initial_owner_email=self.owner.email,
                )
            outcomes.append((context.exception.kind, context.exception.detail))
        self.assertEqual(outcomes[0], outcomes[1])
        self.assertEqual(outcomes[0][0], "forbidden")

        stale_normal = User.objects.get(pk=self.actor.pk)
        User.objects.filter(pk=self.actor.pk).update(
            is_superuser=True,
            is_staff=True,
        )
        fresh_platform = User.objects.get(pk=self.actor.pk)
        outcomes = []
        for actor in (stale_normal, fresh_platform):
            with self.assertRaises(BusinessProvisioningError) as context:
                provision_business(
                    actor=actor,
                    validated_business_data=self.data("Rejected Normal Mode"),
                )
            outcomes.append((context.exception.kind, context.exception.detail))
        self.assertEqual(outcomes[0], outcomes[1])
        self.assertEqual(outcomes[0][0], "invalid")
        self.assertIn("initial_owner_email", outcomes[0][1])

    def test_real_user_locks_revalidate_actor_and_owner_after_waiting(self):
        cases = (
            (
                "revoked-platform",
                self.platform.pk,
                {"is_superuser": False, "is_staff": False},
                lambda: provision_business(
                    actor=self.platform,
                    validated_business_data=self.data("Locked Revoked Platform"),
                    initial_owner_email=self.owner.email,
                ),
                "forbidden",
            ),
            (
                "inactive-actor",
                self.actor.pk,
                {"is_active": False},
                lambda: provision_business(
                    actor=self.actor,
                    validated_business_data=self.data("Locked Inactive Actor"),
                ),
                "forbidden",
            ),
            (
                "inactive-owner",
                self.owner.pk,
                {"is_active": False},
                lambda: provision_business(
                    actor=self.platform,
                    validated_business_data=self.data("Locked Inactive Owner"),
                    initial_owner_email=self.owner.email,
                ),
                "invalid",
            ),
            (
                "changed-owner-email",
                self.owner.pk,
                {"email": "locked-owner-changed@playnow.test"},
                lambda: provision_business(
                    actor=self.platform,
                    validated_business_data=self.data("Locked Changed Owner"),
                    initial_owner_email=self.owner.email,
                ),
                "invalid",
            ),
        )
        for label, user_id, changes, operation, expected_kind in cases:
            with self.subTest(case=label):
                before = self.state()
                result = self.run_service_while_user_is_locked(
                    locked_user_id=user_id,
                    changes=changes,
                    operation=operation,
                )
                self.assertEqual(result[0], expected_kind)
                self.assertEqual(self.state(), before)
                User.objects.filter(pk=self.actor.pk).update(
                    is_active=True,
                    is_superuser=False,
                    is_staff=False,
                )
                User.objects.filter(pk=self.platform.pk).update(
                    is_active=True,
                    is_superuser=True,
                    is_staff=True,
                )
                User.objects.filter(pk=self.owner.pk).update(
                    is_active=True,
                    email="provision-race-owner@playnow.test",
                )

    def test_case_insensitive_constraint_rejects_direct_create_with_savepoint(self):
        original = User.objects.create(
            email="Constraint.Owner@example.com",
            full_name="Constraint Owner",
        )
        with transaction.atomic():
            with self.assertRaises(IntegrityError):
                with transaction.atomic():
                    User.objects.create(
                        email="CONSTRAINT.OWNER@example.com",
                        full_name="Case Variant",
                    )
            self.assertTrue(User.objects.filter(pk=original.pk).exists())
        original.refresh_from_db()
        self.assertEqual(original.email, "Constraint.Owner@example.com")

    def test_case_insensitive_constraint_rejects_email_update_with_savepoint(self):
        original = User.objects.create(
            email="Update.Owner@example.com",
            full_name="Update Owner",
        )
        other = User.objects.create(
            email="other-update@example.com",
            full_name="Other User",
        )
        with transaction.atomic():
            with self.assertRaises(IntegrityError):
                with transaction.atomic():
                    User.objects.filter(pk=other.pk).update(
                        email="UPDATE.OWNER@example.com",
                    )
            self.assertTrue(User.objects.filter(pk=original.pk).exists())
        original.refresh_from_db()
        other.refresh_from_db()
        self.assertEqual(original.email, "Update.Owner@example.com")
        self.assertEqual(other.email, "other-update@example.com")

    def test_constraint_prevents_ambiguous_case_variant_before_provisioning(self):
        owner = User.objects.create(
            email="Provision.Constraint@example.com",
            full_name="Provision Constraint Owner",
        )
        with self.assertRaises(IntegrityError):
            with transaction.atomic():
                User.objects.create(
                    email="PROVISION.CONSTRAINT@example.com",
                    full_name="Ambiguous Variant",
                )
        business, membership = provision_business(
            actor=self.platform,
            validated_business_data=self.data("Constraint Protected"),
            initial_owner_email="PROVISION.CONSTRAINT@example.com",
        )
        self.assertEqual(membership.user_id, owner.pk)
        self.assertEqual(
            BusinessMembership.objects.filter(business=business).count(),
            1,
        )

    def test_self_owner_lock_is_deduplicated_and_complete(self):
        business, membership = provision_business(
            actor=self.platform,
            validated_business_data=self.data("Self Owned"),
            initial_owner_email=self.platform.email,
        )
        self.assertEqual(business.user_id, self.platform.pk)
        self.assertEqual(membership.user_id, self.platform.pk)
        self.assertEqual(BusinessMembership.objects.filter(business=business).count(), 1)
        self.assertIsNone(membership.employee_id)

    def test_ambiguous_case_insensitive_owner_match_is_generic_and_atomic(self):
        second_owner = create_user(email="second-owner@playnow.test")
        before = self.state()
        with patch(
            "core.services.business_provisioning._candidate_owner_ids",
            return_value=[self.owner.pk, second_owner.pk],
        ):
            with self.assertRaises(BusinessProvisioningError) as context:
                provision_business(
                    actor=self.platform,
                    validated_business_data=self.data("Ambiguous Owner"),
                    initial_owner_email=self.owner.email,
                )
        self.assertEqual(context.exception.kind, "invalid")
        self.assertEqual(
            context.exception.detail,
            {"initial_owner_email": [INITIAL_OWNER_ERROR]},
        )
        self.assertEqual(self.state(), before)

    def test_simultaneous_creations_are_serializable_and_complete(self):
        barrier = Barrier(2)

        def worker(number):
            close_old_connections()
            try:
                actor = User.objects.get(pk=self.actor.pk)
                status_obj = type(self.active).objects.get(pk=self.active.pk)
                barrier.wait(timeout=10)
                business, membership = provision_business(
                    actor=actor,
                    validated_business_data={
                        "business_name": f"Concurrent {number}",
                        "description": "",
                        "currency": "NIO",
                        "status": status_obj,
                    },
                )
                return business.pk, membership.pk
            finally:
                close_old_connections()
                connections.close_all()

        with ThreadPoolExecutor(max_workers=2) as executor:
            results = [
                future.result(timeout=20)
                for future in (
                    executor.submit(worker, 1),
                    executor.submit(worker, 2),
                )
            ]
        self.assertEqual(len({business_id for business_id, _ in results}), 2)
        for business_id, membership_id in results:
            membership = BusinessMembership.objects.get(pk=membership_id)
            self.assertEqual(membership.business_id, business_id)
            self.assertEqual(membership.user_id, self.actor.pk)
            self.assertEqual(membership.role, BusinessMembership.ROLE_OWNER)
            self.assertTrue(membership.is_active)

    def test_failures_after_business_creation_roll_back_all_objects(self):
        for target, side_effect, expected in (
            (
                "core.services.business_provisioning.Business.objects.create",
                RuntimeError("business failed"),
                RuntimeError,
            ),
            (
                "core.services.business_provisioning.BusinessMembership.objects.create",
                RuntimeError("membership failed"),
                RuntimeError,
            ),
            (
                "core.services.business_provisioning._has_effective_owner",
                False,
                BusinessProvisioningError,
            ),
            (
                "core.services.business_provisioning.BusinessMembership.objects.create",
                IntegrityError("unexpected integrity failure"),
                IntegrityError,
            ),
        ):
            with self.subTest(target=target, expected=expected.__name__):
                before = self.state()
                kwargs = (
                    {"return_value": side_effect}
                    if side_effect is False
                    else {"side_effect": side_effect}
                )
                with patch(target, **kwargs):
                    with self.assertRaises(expected):
                        provision_business(
                            actor=self.actor,
                            validated_business_data=self.data(
                                f"Rollback {expected.__name__}"
                            ),
                        )
                self.assertEqual(self.state(), before)
