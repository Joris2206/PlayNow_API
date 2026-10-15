from concurrent.futures import ThreadPoolExecutor
from threading import Event, Lock
from time import monotonic
from unittest.mock import patch

from django.db import close_old_connections, connection, connections, transaction
from django.db.utils import OperationalError
from django.core.exceptions import ImproperlyConfigured
from django.test import TransactionTestCase
from django.test.utils import override_settings
from django.urls import reverse
from rest_framework.test import APIClient
from rest_framework_simplejwt.token_blacklist.models import (
    BlacklistedToken,
    OutstandingToken,
)

from core.models import Business, BusinessMembership, Employee, User
from core.services import multi_user_write_lock
from core.services.business_memberships import (
    MembershipDomainError,
    promote_to_owner_by_public_id,
    remove_owner_by_public_id,
    transfer_ownership_by_public_id,
    update_membership,
)
from core.services.business_provisioning import (
    BusinessProvisioningError,
    provision_business,
)
from core.services.platform_users import (
    revoke_platform_user_sessions,
    update_platform_user_identity,
)
from core.tests.factories import (
    create_business,
    create_employee,
    create_membership,
    create_status,
    create_user,
)
from core.tokens import VersionedRefreshToken


class MultiUserWriteLockConcurrencyTests(TransactionTestCase):
    reset_sequences = True

    def setUp(self):
        self.status = create_status("Activo")

    def _user(self, local_part, *, platform=False):
        return create_user(
            email=f"{local_part}@multi-user-lock.test",
            is_superuser=platform,
        )

    def _business_data(self, name):
        return {
            "business_name": name,
            "description": "",
            "currency": "NIO",
            "status": self.status,
        }

    def _revoke(self, actor, target):
        return revoke_platform_user_sessions(
            actor_id=actor.pk,
            target_public_id=target.public_id,
            validate_payload=lambda locked_target: {},
        )

    def _assert_advisory_wait(
        self,
        first_operation,
        second_operation,
        *,
        assert_before_release=None,
    ):
        original_acquire = multi_user_write_lock.acquire_multi_user_write_lock
        holder_acquired = Event()
        release_holder = Event()
        second_started = Event()
        state_lock = Lock()
        state = {"worker_pids": {}}

        def observed_acquire():
            original_acquire()
            with connection.cursor() as cursor:
                cursor.execute("SELECT pg_backend_pid()")
                current_pid = cursor.fetchone()[0]
            with state_lock:
                is_holder = "holder_pid" not in state
                if is_holder:
                    state["holder_pid"] = current_pid
            if is_holder:
                holder_acquired.set()
                if not release_holder.wait(timeout=10):
                    raise TimeoutError("shared advisory lock holder was not released")

        def run(label, operation, started=None):
            close_old_connections()
            try:
                with connection.cursor() as cursor:
                    cursor.execute("SELECT pg_backend_pid()")
                    pid = cursor.fetchone()[0]
                with state_lock:
                    state["worker_pids"][label] = pid
                if started is not None:
                    started.set()
                return operation()
            finally:
                close_old_connections()
                connections.close_all()

        first_future = None
        second_future = None
        try:
            with patch(
                "core.services.multi_user_write_lock.acquire_multi_user_write_lock",
                side_effect=observed_acquire,
            ):
                with ThreadPoolExecutor(max_workers=2) as executor:
                    first_future = executor.submit(
                        run,
                        "first",
                        first_operation,
                    )
                    self.assertTrue(holder_acquired.wait(timeout=20))
                    second_future = executor.submit(
                        run,
                        "second",
                        second_operation,
                        second_started,
                    )
                    self.assertTrue(second_started.wait(timeout=20))

                    deadline = monotonic() + 5
                    sample = None
                    while monotonic() < deadline:
                        with state_lock:
                            second_pid = state["worker_pids"].get("second")
                            holder_pid = state.get("holder_pid")
                        if second_pid is not None and holder_pid is not None:
                            with connection.cursor() as cursor:
                                cursor.execute(
                                    """
                                    SELECT pg_blocking_pids(pid), wait_event_type,
                                           wait_event
                                    FROM pg_stat_activity
                                    WHERE pid = %s
                                    """,
                                    [second_pid],
                                )
                                sample = cursor.fetchone()
                            if (
                                sample is not None
                                and holder_pid in sample[0]
                                and sample[1] == "Lock"
                                and sample[2] == "advisory"
                            ):
                                break
                        second_started.wait(timeout=0.05)

                    self.assertIsNotNone(sample)
                    self.assertIn(holder_pid, sample[0])
                    self.assertEqual(sample[1:], ("Lock", "advisory"))
                    if assert_before_release is not None:
                        assert_before_release()
                    release_holder.set()
                    return (
                        first_future.result(timeout=30),
                        second_future.result(timeout=30),
                    )
        finally:
            release_holder.set()
            if first_future is not None and first_future.done():
                first_future.exception()
            if second_future is not None and second_future.done():
                second_future.exception()

    def test_phase4b3_and_platform_patch_wait_before_user_locks(self):
        lower_actor = self._user("patch-lower", platform=True)
        higher_actor = self._user("patch-higher", platform=True)
        self.assertLess(lower_actor.pk, higher_actor.pk)
        refresh = VersionedRefreshToken.for_user(lower_actor)
        outstanding = OutstandingToken.objects.get(jti=refresh["jti"])
        before_version = lower_actor.auth_version

        self._assert_advisory_wait(
            lambda: self._revoke(higher_actor, lower_actor),
            lambda: update_platform_user_identity(
                actor=lower_actor,
                target_public_id=higher_actor.public_id,
                validate_payload=lambda target: {
                    "full_name": "Patched After Advisory Wait",
                },
            ),
        )

        lower_actor.refresh_from_db()
        higher_actor.refresh_from_db()
        self.assertEqual(lower_actor.auth_version, before_version + 1)
        self.assertEqual(higher_actor.full_name, "Patched After Advisory Wait")
        self.assertTrue(
            BlacklistedToken.objects.filter(token=outstanding).exists()
        )

    def test_phase4b3_and_business_provisioning_share_protocol(self):
        lower_actor = self._user("provision-lower", platform=True)
        higher_owner = self._user("provision-higher", platform=True)
        self.assertLess(lower_actor.pk, higher_owner.pk)
        before_version = lower_actor.auth_version

        _, provisioned = self._assert_advisory_wait(
            lambda: self._revoke(higher_owner, lower_actor),
            lambda: provision_business(
                actor=lower_actor,
                validated_business_data=self._business_data(
                    "Cross Protocol Provisioning"
                ),
                initial_owner_email=higher_owner.email,
            ),
        )

        business, membership = provisioned
        lower_actor.refresh_from_db()
        self.assertEqual(lower_actor.auth_version, before_version + 1)
        self.assertTrue(Business.objects.filter(pk=business.pk).exists())
        self.assertEqual(membership.user_id, higher_owner.pk)
        self.assertEqual(membership.role, BusinessMembership.ROLE_OWNER)
        self.assertTrue(membership.is_active)
        self.assertEqual(
            BusinessMembership.objects.filter(business=business).count(),
            1,
        )

    def test_phase4b3_and_membership_update_share_protocol(self):
        lower_owner = self._user("membership-lower")
        higher_actor = self._user("membership-higher", platform=True)
        self.assertLess(lower_owner.pk, higher_actor.pk)
        business = create_business(user=lower_owner, status=self.status)
        target_membership = create_membership(
            user=higher_actor,
            business=business,
            role=BusinessMembership.ROLE_ADMIN,
        )
        before_version = lower_owner.auth_version

        self._assert_advisory_wait(
            lambda: self._revoke(higher_actor, lower_owner),
            lambda: update_membership(
                actor=lower_owner,
                membership=target_membership,
                changes={"role": BusinessMembership.ROLE_SELLER},
            ),
        )

        lower_owner.refresh_from_db()
        target_membership.refresh_from_db()
        self.assertEqual(lower_owner.auth_version, before_version + 1)
        self.assertEqual(target_membership.role, BusinessMembership.ROLE_SELLER)
        self.assertEqual(
            BusinessMembership.objects.filter(
                business=business,
                role=BusinessMembership.ROLE_OWNER,
                is_active=True,
                user__is_active=True,
            ).count(),
            1,
        )

    def test_platform_patch_and_provisioning_share_protocol(self):
        lower_actor = self._user("external-lower", platform=True)
        higher_actor = self._user("external-higher", platform=True)
        self.assertLess(lower_actor.pk, higher_actor.pk)

        patch_result, provisioned = self._assert_advisory_wait(
            lambda: update_platform_user_identity(
                actor=higher_actor,
                target_public_id=lower_actor.public_id,
                validate_payload=lambda target: {
                    "full_name": "External Writer Serialized",
                },
            ),
            lambda: provision_business(
                actor=lower_actor,
                validated_business_data=self._business_data(
                    "External Writers Provisioning"
                ),
                initial_owner_email=higher_actor.email,
            ),
        )

        business, membership = provisioned
        lower_actor.refresh_from_db()
        self.assertEqual(patch_result.pk, lower_actor.pk)
        self.assertEqual(lower_actor.full_name, "External Writer Serialized")
        self.assertEqual(membership.business_id, business.pk)
        self.assertEqual(membership.user_id, higher_actor.pk)

    def _ownership_context(self):
        owner = self._user("ownership-owner")
        candidate = self._user("ownership-candidate")
        second_owner = self._user("ownership-second")
        platform = self._user("ownership-platform", platform=True)
        business = create_business(user=owner, status=self.status)
        owner_membership = BusinessMembership.objects.get(
            business=business,
            user=owner,
        )
        candidate_employee = create_employee(
            business=business,
            status=self.status,
            full_name="Ownership Candidate Employee",
        )
        candidate_membership = create_membership(
            user=candidate,
            business=business,
            role=BusinessMembership.ROLE_ADMIN,
            employee=candidate_employee,
        )
        second_owner_membership = create_membership(
            user=second_owner,
            business=business,
            role=BusinessMembership.ROLE_OWNER,
        )
        return {
            "owner": owner,
            "candidate": candidate,
            "second_owner": second_owner,
            "platform": platform,
            "business": business,
            "owner_membership": owner_membership,
            "candidate_membership": candidate_membership,
            "second_owner_membership": second_owner_membership,
            "candidate_employee": candidate_employee,
        }

    def _assert_ownership_rows_are_not_locked(self, context):
        with transaction.atomic():
            list(
                Business.objects.select_for_update(nowait=True).filter(
                    pk=context["business"].pk
                )
            )
            list(
                BusinessMembership.objects.select_for_update(nowait=True)
                .filter(business=context["business"])
                .order_by("pk")
            )
            list(
                User.objects.select_for_update(nowait=True)
                .filter(
                    pk__in=[
                        context["owner"].pk,
                        context["candidate"].pk,
                        context["second_owner"].pk,
                        context["platform"].pk,
                    ]
                )
                .order_by("pk")
            )

    def _assert_ownership_invariants(self, context):
        self.assertGreaterEqual(
            BusinessMembership.objects.filter(
                business=context["business"],
                role=BusinessMembership.ROLE_OWNER,
                is_active=True,
                user__is_active=True,
            ).count(),
            1,
        )
        self.assertEqual(
            BusinessMembership.objects.filter(
                business=context["business"]
            ).count(),
            3,
        )
        context["candidate_employee"].refresh_from_db()
        self.assertEqual(
            context["candidate_employee"].business_id,
            context["business"].pk,
        )
        self.assertEqual(
            Employee.objects.filter(pk=context["candidate_employee"].pk).count(),
            1,
        )

    def _ownership_stable_snapshot(self, context):
        return {
            "users": tuple(
                User.objects.filter(
                    pk__in=[
                        context["owner"].pk,
                        context["candidate"].pk,
                        context["second_owner"].pk,
                        context["platform"].pk,
                    ]
                )
                .order_by("pk")
                .values()
            ),
            "memberships": tuple(
                BusinessMembership.objects.filter(business=context["business"])
                .order_by("pk")
                .values(
                    "pk",
                    "business_id",
                    "user_id",
                    "employee_id",
                    "is_active",
                )
            ),
            "employees": tuple(
                Employee.objects.filter(business=context["business"])
                .order_by("pk")
                .values()
            ),
        }

    def _ownership_post(self, actor, url_name, kwargs, payload):
        client = APIClient()
        client.force_authenticate(user=actor)
        return client.post(reverse(url_name, kwargs=kwargs), payload, format="json")

    def _hold_with_phase4b3(self):
        actor = self._user("ownership-holder-actor", platform=True)
        target = self._user("ownership-holder-target", platform=True)
        return lambda: self._revoke(actor, target)

    def test_unauthorized_ownership_and_admin_provisioning_skip_advisory(self):
        context = self._ownership_context()
        outsider = self._user("preauthorization-outsider")
        acquired = Event()
        release = Event()

        def hold_advisory():
            close_old_connections()
            try:
                with transaction.atomic():
                    multi_user_write_lock.acquire_multi_user_write_lock()
                    acquired.set()
                    if not release.wait(timeout=20):
                        raise TimeoutError("advisory holder was not released")
            finally:
                close_old_connections()
                connections.close_all()

        def run(operation, expected_error):
            close_old_connections()
            try:
                operation()
            except expected_error as exc:
                return exc.kind
            finally:
                close_old_connections()
                connections.close_all()
            self.fail("Unauthorized operation unexpectedly succeeded")

        before = {
            "users": tuple(User.objects.order_by("pk").values()),
            "businesses": tuple(Business.objects.order_by("pk").values()),
            "memberships": tuple(
                BusinessMembership.objects.order_by("pk").values()
            ),
            "employees": tuple(Employee.objects.order_by("pk").values()),
        }
        ownership_operations = (
            lambda: promote_to_owner_by_public_id(
                actor=outsider,
                business_public_id=context["business"].public_id,
                candidate_membership_public_id=context[
                    "candidate_membership"
                ].public_id,
                validate_payload=lambda: self.fail("payload was validated"),
            ),
            lambda: transfer_ownership_by_public_id(
                actor=outsider,
                business_public_id=context["business"].public_id,
                candidate_membership_public_ids=[
                    context["owner_membership"].public_id,
                    context["candidate_membership"].public_id,
                ],
                validate_payload=lambda: self.fail("payload was validated"),
            ),
            lambda: remove_owner_by_public_id(
                actor=outsider,
                business_public_id=context["business"].public_id,
                membership_public_id=context[
                    "second_owner_membership"
                ].public_id,
                validate_payload=lambda: self.fail("payload was validated"),
            ),
        )

        try:
            with ThreadPoolExecutor(max_workers=2) as executor:
                holder = executor.submit(hold_advisory)
                self.assertTrue(acquired.wait(timeout=10))
                for operation in ownership_operations:
                    with patch(
                        "core.services.business_memberships."
                        "_locked_actor_for_business"
                    ) as sensitive_lookup:
                        future = executor.submit(
                            run,
                            operation,
                            MembershipDomainError,
                        )
                        self.assertEqual(future.result(timeout=2), "not_found")
                        sensitive_lookup.assert_not_called()
                        self.assertFalse(holder.done())

                with patch(
                    "core.services.business_provisioning._candidate_owner_ids"
                ) as sensitive_lookup:
                    future = executor.submit(
                        run,
                        lambda: provision_business(
                            actor=outsider,
                            validated_business_data=self._business_data(
                                "Unauthorized Administrative Provisioning"
                            ),
                            initial_owner_email=context["candidate"].email,
                        ),
                        BusinessProvisioningError,
                    )
                    self.assertEqual(future.result(timeout=2), "forbidden")
                    sensitive_lookup.assert_not_called()
                    self.assertFalse(holder.done())
                release.set()
                holder.result(timeout=20)
        finally:
            release.set()

        after = {
            "users": tuple(User.objects.order_by("pk").values()),
            "businesses": tuple(Business.objects.order_by("pk").values()),
            "memberships": tuple(
                BusinessMembership.objects.order_by("pk").values()
            ),
            "employees": tuple(Employee.objects.order_by("pk").values()),
        }
        self.assertEqual(after, before)

    def test_promote_owner_waits_on_advisory_before_specific_rows(self):
        context = self._ownership_context()
        stable_before = self._ownership_stable_snapshot(context)
        with patch("core.views.log_action") as audit:
            _, response = self._assert_advisory_wait(
                self._hold_with_phase4b3(),
                lambda: self._ownership_post(
                    context["owner"],
                    "business-promote-owner",
                    {"public_id": context["business"].public_id},
                    {
                    "membership_public_id": context[
                        "candidate_membership"
                    ].public_id,
                    },
                ),
                assert_before_release=lambda: self._assert_ownership_rows_are_not_locked(
                    context
                ),
            )
            self.assertEqual(response.status_code, 200)
            audit.assert_called_once()
        context["candidate_membership"].refresh_from_db()
        self.assertEqual(
            context["candidate_membership"].role,
            BusinessMembership.ROLE_OWNER,
        )
        self._assert_ownership_invariants(context)
        self.assertEqual(self._ownership_stable_snapshot(context), stable_before)

    def test_transfer_ownership_waits_on_advisory_before_specific_rows(self):
        context = self._ownership_context()
        stable_before = self._ownership_stable_snapshot(context)
        with patch("core.views.log_action") as audit:
            _, response = self._assert_advisory_wait(
                self._hold_with_phase4b3(),
                lambda: self._ownership_post(
                    context["owner"],
                    "business-ownership-transfer",
                    {"public_id": context["business"].public_id},
                    {
                    "from_membership_public_id": context[
                        "owner_membership"
                    ].public_id,
                    "to_membership_public_id": context[
                        "candidate_membership"
                    ].public_id,
                    "from_role": BusinessMembership.ROLE_ADMIN,
                    },
                ),
                assert_before_release=lambda: self._assert_ownership_rows_are_not_locked(
                    context
                ),
            )
            self.assertEqual(response.status_code, 200)
            audit.assert_called_once()
        context["owner_membership"].refresh_from_db()
        context["candidate_membership"].refresh_from_db()
        self.assertEqual(context["owner_membership"].role, BusinessMembership.ROLE_ADMIN)
        self.assertEqual(
            context["candidate_membership"].role,
            BusinessMembership.ROLE_OWNER,
        )
        self._assert_ownership_invariants(context)
        self.assertEqual(self._ownership_stable_snapshot(context), stable_before)

    def test_remove_owner_waits_on_advisory_before_specific_rows(self):
        context = self._ownership_context()
        stable_before = self._ownership_stable_snapshot(context)
        with patch("core.views.log_action") as audit:
            _, response = self._assert_advisory_wait(
                self._hold_with_phase4b3(),
                lambda: self._ownership_post(
                    context["platform"],
                    "business-remove-business-owner",
                    {
                        "public_id": context["business"].public_id,
                        "membership_public_id": context[
                            "second_owner_membership"
                        ].public_id,
                    },
                    {
                    "replacement_role": BusinessMembership.ROLE_ADMIN,
                    },
                ),
                assert_before_release=lambda: self._assert_ownership_rows_are_not_locked(
                    context
                ),
            )
            self.assertEqual(response.status_code, 200)
            audit.assert_called_once()
        context["second_owner_membership"].refresh_from_db()
        self.assertEqual(
            context["second_owner_membership"].role,
            BusinessMembership.ROLE_ADMIN,
        )
        self._assert_ownership_invariants(context)
        self.assertEqual(self._ownership_stable_snapshot(context), stable_before)


@override_settings(MULTI_USER_WRITE_LOCK_TIMEOUT_MS=250)
class MultiUserWriteLockTimeoutTests(TransactionTestCase):
    reset_sequences = True

    def setUp(self):
        self.status = create_status("Activo")
        self.actor = create_user(
            email="timeout-actor@example.com",
            is_superuser=True,
        )
        self.target = create_user(
            email="timeout-target@example.com",
            is_superuser=True,
        )

    def _hold_advisory(self, acquired, release, state=None):
        close_old_connections()
        try:
            with transaction.atomic():
                multi_user_write_lock.acquire_multi_user_write_lock()
                with connection.cursor() as cursor:
                    cursor.execute("SELECT pg_backend_pid()")
                    pid = cursor.fetchone()[0]
                if state is not None:
                    state["pid"] = pid
                acquired.set()
                if not release.wait(timeout=5):
                    raise TimeoutError("advisory holder was not released")
                return pid
        finally:
            close_old_connections()
            connections.close_all()

    def _state_snapshot(self):
        return {
            "users": tuple(User.objects.order_by("pk").values()),
            "businesses": tuple(Business.objects.order_by("pk").values()),
            "memberships": tuple(
                BusinessMembership.objects.order_by("pk").values()
            ),
            "employees": tuple(Employee.objects.order_by("pk").values()),
            "outstanding": tuple(
                OutstandingToken.objects.order_by("pk").values()
            ),
            "blacklisted": tuple(
                BlacklistedToken.objects.order_by("pk").values()
            ),
        }

    def test_timeout_setting_is_positive_bounded_and_transaction_local(self):
        with connection.cursor() as cursor:
            cursor.execute("SHOW lock_timeout")
            original_timeout = cursor.fetchone()[0]

        with transaction.atomic():
            multi_user_write_lock.acquire_multi_user_write_lock()
            with connection.cursor() as cursor:
                cursor.execute("SHOW lock_timeout")
                self.assertEqual(cursor.fetchone()[0], "250ms")

        with connection.cursor() as cursor:
            cursor.execute("SHOW lock_timeout")
            self.assertEqual(cursor.fetchone()[0], original_timeout)

        for invalid in ("not-an-integer", True, 0, -1, 2_147_483_648):
            with self.subTest(invalid=invalid), override_settings(
                MULTI_USER_WRITE_LOCK_TIMEOUT_MS=invalid
            ):
                with transaction.atomic():
                    with self.assertRaises(ImproperlyConfigured):
                        multi_user_write_lock.acquire_multi_user_write_lock()

    @override_settings(MULTI_USER_WRITE_LOCK_TIMEOUT_MS=1_500)
    def test_advisory_timeout_maps_to_503_rolls_back_and_connection_recovers(self):
        business = create_business(user=self.target, status=self.status)
        employee = create_employee(business=business, status=self.status)
        membership = BusinessMembership.objects.get(
            business=business,
            user=self.target,
        )
        membership.employee = employee
        membership.save(update_fields=["employee", "updated_at"])
        refresh = VersionedRefreshToken.for_user(self.target)
        outstanding = OutstandingToken.objects.get(jti=refresh["jti"])
        before = self._state_snapshot()
        acquired = Event()
        release = Event()
        client = APIClient()
        client.force_authenticate(user=self.actor)
        classification_states = []
        waiter_ready = Event()
        waiter_state = {}
        holder_state = {}
        original_sqlstate = multi_user_write_lock._database_sqlstate

        def observe_sqlstate(exc):
            classification_states.append(
                (connection.in_atomic_block, connection.needs_rollback)
            )
            return original_sqlstate(exc)

        def make_request():
            close_old_connections()
            try:
                with connection.cursor() as cursor:
                    cursor.execute("SELECT pg_backend_pid()")
                    waiter_state["pid"] = cursor.fetchone()[0]
                waiter_ready.set()
                return client.post(
                    reverse(
                        "platform-user-revoke-sessions",
                        kwargs={"public_id": self.target.public_id},
                    ),
                    {},
                    format="json",
                )
            finally:
                close_old_connections()
                connections.close_all()

        try:
            with ThreadPoolExecutor(max_workers=2) as executor:
                holder = executor.submit(
                    self._hold_advisory,
                    acquired,
                    release,
                    holder_state,
                )
                self.assertTrue(acquired.wait(timeout=20))
                with (
                    patch("core.services.platform_users.log_action") as audit,
                    patch(
                        "core.services.multi_user_write_lock._database_sqlstate",
                        side_effect=observe_sqlstate,
                    ),
                ):
                    waiter = executor.submit(make_request)
                    self.assertTrue(waiter_ready.wait(timeout=5))
                    deadline = monotonic() + 1
                    sample = None
                    while monotonic() < deadline:
                        with connection.cursor() as cursor:
                            cursor.execute(
                                """
                                SELECT pg_blocking_pids(pid), wait_event_type,
                                       wait_event
                                FROM pg_stat_activity WHERE pid = %s
                                """,
                                [waiter_state["pid"]],
                            )
                            sample = cursor.fetchone()
                        if (
                            sample
                            and holder_state["pid"] in sample[0]
                            and sample[1:] == ("Lock", "advisory")
                        ):
                            break
                        waiter_ready.wait(timeout=0.01)
                    self.assertIsNotNone(sample)
                    self.assertIn(holder_state["pid"], sample[0])
                    self.assertEqual(sample[1:], ("Lock", "advisory"))
                    response = waiter.result(timeout=5)
                    self.assertEqual(response.status_code, 503)
                    self.assertEqual(
                        response.data,
                        {
                            "detail": (
                                "La operación está temporalmente ocupada. "
                                "Inténtalo nuevamente."
                            )
                        },
                    )
                    audit.assert_not_called()
                    self.assertFalse(holder.done())
                self.assertEqual(classification_states, [(False, False)])
                self.assertEqual(self._state_snapshot(), before)
                with connection.cursor() as cursor:
                    cursor.execute("SELECT 1")
                    self.assertEqual(cursor.fetchone(), (1,))
                release.set()
                holder.result(timeout=20)
        finally:
            release.set()

        response = client.post(
            reverse(
                "platform-user-revoke-sessions",
                kwargs={"public_id": self.target.public_id},
            ),
            {},
            format="json",
        )
        self.assertEqual(response.status_code, 204)
        self.assertTrue(
            BlacklistedToken.objects.filter(token=outstanding).exists()
        )

    @override_settings(MULTI_USER_WRITE_LOCK_TIMEOUT_MS=2_000)
    def test_row_timeout_releases_advisory_for_waiting_participant(self):
        blocked_target = self.target
        waiting_actor = create_user(
            email="timeout-waiting-actor@example.com",
            is_superuser=True,
        )
        waiting_target = create_user(
            email="timeout-waiting-target@example.com",
            is_superuser=True,
        )
        before_blocked = User.objects.values(
            "auth_version", "is_active", "password"
        ).get(pk=blocked_target.pk)
        row_locked = Event()
        release_row = Event()
        advisory_acquired = Event()
        original_acquire = multi_user_write_lock.acquire_multi_user_write_lock
        state_lock = Lock()
        state = {}

        def hold_target_row():
            close_old_connections()
            try:
                with transaction.atomic():
                    with connection.cursor() as cursor:
                        cursor.execute("SELECT pg_backend_pid()")
                        with state_lock:
                            state["row_holder_pid"] = cursor.fetchone()[0]
                    User.objects.select_for_update().get(pk=blocked_target.pk)
                    row_locked.set()
                    if not release_row.wait(timeout=5):
                        raise TimeoutError("row holder was not released")
            finally:
                close_old_connections()
                connections.close_all()

        def observed_acquire():
            original_acquire()
            with state_lock:
                if "advisory_holder" not in state:
                    state["advisory_holder"] = True
                    advisory_acquired.set()

        def run(label, operation):
            close_old_connections()
            try:
                with connection.cursor() as cursor:
                    cursor.execute("SELECT pg_backend_pid()")
                    pid = cursor.fetchone()[0]
                with state_lock:
                    state[f"{label}_pid"] = pid
                try:
                    result = operation()
                except multi_user_write_lock.MultiUserWriteContentionError:
                    with connection.cursor() as cursor:
                        cursor.execute("SELECT 1")
                        reusable = cursor.fetchone()
                    return "contention", reusable
                return "ok", result.pk
            finally:
                close_old_connections()
                connections.close_all()

        release_row.clear()
        try:
            with patch(
                "core.services.multi_user_write_lock.acquire_multi_user_write_lock",
                side_effect=observed_acquire,
            ):
                with ThreadPoolExecutor(max_workers=3) as executor:
                    row_holder = executor.submit(hold_target_row)
                    self.assertTrue(row_locked.wait(timeout=20))
                    first = executor.submit(
                        run,
                        "first",
                        lambda: revoke_platform_user_sessions(
                            actor_id=self.actor.pk,
                            target_public_id=blocked_target.public_id,
                            validate_payload=lambda target: {},
                        ),
                    )
                    self.assertTrue(advisory_acquired.wait(timeout=20))

                    deadline = monotonic() + 10
                    first_wait = None
                    while monotonic() < deadline:
                        with state_lock:
                            first_pid = state.get("first_pid")
                        if first_pid is not None:
                            with connection.cursor() as cursor:
                                cursor.execute(
                                    """
                                    SELECT pg_blocking_pids(pid), wait_event_type,
                                           wait_event
                                    FROM pg_stat_activity WHERE pid = %s
                                    """,
                                    [first_pid],
                                )
                                first_wait = cursor.fetchone()
                            with state_lock:
                                row_holder_pid = state.get("row_holder_pid")
                            if (
                                first_wait
                                and row_holder_pid in first_wait[0]
                                and first_wait[1] == "Lock"
                                and first_wait[2] in {"transactionid", "tuple"}
                            ):
                                break
                    self.assertIsNotNone(first_wait)
                    self.assertIn(row_holder_pid, first_wait[0])
                    self.assertEqual(first_wait[1], "Lock")
                    self.assertIn(first_wait[2], {"transactionid", "tuple"})

                    second = executor.submit(
                        run,
                        "second",
                        lambda: revoke_platform_user_sessions(
                            actor_id=waiting_actor.pk,
                            target_public_id=waiting_target.public_id,
                            validate_payload=lambda target: {},
                        ),
                    )

                    deadline = monotonic() + 10
                    second_wait = None
                    while monotonic() < deadline:
                        with state_lock:
                            second_pid = state.get("second_pid")
                            first_pid = state.get("first_pid")
                        if second_pid is not None and first_pid is not None:
                            with connection.cursor() as cursor:
                                cursor.execute(
                                    """
                                    SELECT pg_blocking_pids(pid), wait_event_type,
                                           wait_event
                                    FROM pg_stat_activity WHERE pid = %s
                                    """,
                                    [second_pid],
                                )
                                second_wait = cursor.fetchone()
                            if (
                                second_wait
                                and first_pid in second_wait[0]
                                and second_wait[1:] == ("Lock", "advisory")
                            ):
                                break
                    self.assertIsNotNone(second_wait)
                    self.assertIn(first_pid, second_wait[0])
                    self.assertEqual(second_wait[1:], ("Lock", "advisory"))

                    self.assertEqual(
                        first.result(timeout=10),
                        ("contention", (1,)),
                    )
                    self.assertEqual(second.result(timeout=10)[0], "ok")
                    release_row.set()
                    row_holder.result(timeout=20)
        finally:
            release_row.set()

        self.assertEqual(
            User.objects.values("auth_version", "is_active", "password").get(
                pk=blocked_target.pk
            ),
            before_blocked,
        )
        waiting_target.refresh_from_db()
        self.assertEqual(waiting_target.auth_version, 1)

    def test_unexpected_operational_error_is_not_translated_and_rolls_back(self):
        class UnexpectedDatabaseFailure(Exception):
            sqlstate = "40P01"

        original_name = self.target.full_name

        @multi_user_write_lock.translate_multi_user_write_contention
        @transaction.atomic
        def fail_after_write():
            multi_user_write_lock.acquire_multi_user_write_lock()
            User.objects.filter(pk=self.target.pk).update(full_name="Must Roll Back")
            try:
                raise UnexpectedDatabaseFailure("deadlock detected")
            except UnexpectedDatabaseFailure as cause:
                raise OperationalError("unexpected database failure") from cause

        with self.assertRaises(OperationalError):
            fail_after_write()
        self.target.refresh_from_db()
        self.assertEqual(self.target.full_name, original_name)
        with connection.cursor() as cursor:
            cursor.execute("SELECT 1")
            self.assertEqual(cursor.fetchone(), (1,))

    def test_sqlstate_precedence_activation_and_preserved_cause(self):
        class DriverFailure(Exception):
            def __init__(self, sqlstate):
                super().__init__(sqlstate)
                self.sqlstate = sqlstate

        direct_lock_error = OperationalError("lock timeout")
        direct_lock_error.sqlstate = "55P03"

        @multi_user_write_lock.translate_multi_user_write_contention
        @transaction.atomic
        def activated_lock_timeout():
            multi_user_write_lock.acquire_multi_user_write_lock()
            raise direct_lock_error

        with self.assertRaises(
            multi_user_write_lock.MultiUserWriteContentionError
        ) as caught:
            activated_lock_timeout()
        self.assertIs(caught.exception.__cause__, direct_lock_error)

        direct_deadlock = OperationalError("deadlock")
        direct_deadlock.sqlstate = "40P01"

        @multi_user_write_lock.translate_multi_user_write_contention
        @transaction.atomic
        def activated_deadlock():
            multi_user_write_lock.acquire_multi_user_write_lock()
            raise direct_deadlock

        with self.assertRaises(OperationalError) as caught:
            activated_deadlock()
        self.assertIs(caught.exception, direct_deadlock)

        mixed = OperationalError("mixed chain")
        mixed.__cause__ = DriverFailure("40P01")
        mixed.__context__ = DriverFailure("55P03")

        @multi_user_write_lock.translate_multi_user_write_contention
        @transaction.atomic
        def activated_mixed_error():
            multi_user_write_lock.acquire_multi_user_write_lock()
            raise mixed

        with self.assertRaises(OperationalError) as caught:
            activated_mixed_error()
        self.assertIs(caught.exception, mixed)

        unrelated_lock_error = OperationalError("unrelated lock error")
        unrelated_lock_error.sqlstate = "55P03"

        @multi_user_write_lock.translate_multi_user_write_contention
        @transaction.atomic
        def single_user_operation():
            raise unrelated_lock_error

        with self.assertRaises(OperationalError) as caught:
            single_user_operation()
        self.assertIs(caught.exception, unrelated_lock_error)
