from concurrent.futures import ThreadPoolExecutor
from threading import Barrier, Event
from unittest.mock import patch
from uuid import uuid4

from django.db import close_old_connections, connections
from django.test import TransactionTestCase
from django.urls import resolve, reverse
from rest_framework import status
from rest_framework.test import APIClient, APITestCase
from rest_framework_simplejwt.tokens import RefreshToken

from core.models import BusinessMembership, Employee, User
from core.services.business_memberships import (
    MembershipDomainError,
    promote_to_owner,
    remove_owner,
    transfer_ownership,
    update_membership,
)
from core.views import BusinessMembershipViewSet, BusinessViewSet
from core.tests.factories import (
    create_business,
    create_membership,
    create_role_user,
    create_status,
    create_user,
)


class BusinessMembershipAPITests(APITestCase):
    @classmethod
    def setUpTestData(cls):
        cls.active = create_status("Activo")
        cls.owner = create_user(email="membership-owner@playnow.test")
        cls.business = create_business(user=cls.owner, status=cls.active)
        cls.owner_membership = BusinessMembership.objects.get(
            user=cls.owner,
            business=cls.business,
        )
        cls.admin, _, cls.admin_membership = create_role_user(
            business=cls.business,
            role=BusinessMembership.ROLE_ADMIN,
            status=cls.active,
        )
        cls.cashier, _, cls.cashier_membership = create_role_user(
            business=cls.business,
            role=BusinessMembership.ROLE_CASHIER,
            status=cls.active,
        )
        cls.seller, _, cls.seller_membership = create_role_user(
            business=cls.business,
            role=BusinessMembership.ROLE_SELLER,
            status=cls.active,
        )
        cls.inventory, _, cls.inventory_membership = create_role_user(
            business=cls.business,
            role=BusinessMembership.ROLE_INVENTORY,
            status=cls.active,
        )
        cls.viewer, _, cls.viewer_membership = create_role_user(
            business=cls.business,
            role=BusinessMembership.ROLE_VIEWER,
            status=cls.active,
        )
        cls.platform_admin = create_user(
            email="membership-platform@playnow.test",
            is_superuser=True,
        )
        cls.foreign_owner = create_user(email="membership-foreign@playnow.test")
        cls.foreign_business = create_business(
            user=cls.foreign_owner,
            status=cls.active,
        )
        cls.foreign_membership = BusinessMembership.objects.get(
            user=cls.foreign_owner,
            business=cls.foreign_business,
        )

    def authenticate(self, user):
        self.client.force_authenticate(user=user)

    def detail_url(self, membership):
        return reverse(
            "business-membership-detail",
            kwargs={"membership_public_id": membership.public_id},
        )

    def test_membership_routes_are_unambiguous_and_legacy_delete_is_preserved(self):
        canonical = self.detail_url(self.cashier_membership)
        match = resolve(canonical)
        self.assertEqual(match.url_name, "business-membership-detail")
        self.assertEqual(
            match.func.actions,
            {"patch": "partial_update", "delete": "destroy"},
        )
        legacy = reverse(
            "business-deactivate-member",
            kwargs={
                "public_id": self.business.public_id,
                "membership_public_id": self.cashier_membership.public_id,
            },
        )
        legacy_match = resolve(legacy)
        self.assertEqual(legacy_match.func.actions, {"delete": "deactivate_member"})
        self.authenticate(self.owner)
        self.assertEqual(self.client.put(canonical, {}, format="json").status_code, 405)
        self.assertEqual(self.client.patch(legacy, {}, format="json").status_code, 405)

    def test_owner_and_platform_admin_can_patch_lower_memberships(self):
        for actor, membership in (
            (self.owner, self.cashier_membership),
            (self.platform_admin, self.seller_membership),
        ):
            with self.subTest(actor=actor.email):
                self.authenticate(actor)
                response = self.client.patch(
                    self.detail_url(membership),
                    {"role": BusinessMembership.ROLE_VIEWER},
                    format="json",
                )
                self.assertEqual(response.status_code, status.HTTP_200_OK)
                membership.refresh_from_db()
                self.assertEqual(membership.role, BusinessMembership.ROLE_VIEWER)

    def test_admin_can_only_manage_lower_roles_without_escalation(self):
        self.authenticate(self.admin)
        allowed = self.client.patch(
            self.detail_url(self.cashier_membership),
            {"role": BusinessMembership.ROLE_VIEWER},
            format="json",
        )
        peer = self.client.patch(
            self.detail_url(self.admin_membership),
            {"is_active": False},
            format="json",
        )
        escalation = self.client.patch(
            self.detail_url(self.seller_membership),
            {"role": BusinessMembership.ROLE_ADMIN},
            format="json",
        )
        self.assertEqual(allowed.status_code, 200)
        self.assertEqual(peer.status_code, 403)
        self.assertEqual(escalation.status_code, 403)

    def test_lower_roles_no_membership_inactive_and_cross_business_are_hidden_or_denied(self):
        for actor in (self.cashier, self.seller, self.inventory, self.viewer):
            with self.subTest(actor=actor.email):
                self.authenticate(actor)
                response = self.client.patch(
                    self.detail_url(self.seller_membership),
                    {"role": BusinessMembership.ROLE_VIEWER},
                    format="json",
                )
                self.assertEqual(response.status_code, 403)
        outsider = create_user(email="membership-outsider@playnow.test")
        self.authenticate(outsider)
        self.assertEqual(
            self.client.patch(
                self.detail_url(self.cashier_membership),
                {"role": BusinessMembership.ROLE_VIEWER},
                format="json",
            ).status_code,
            404,
        )
        self.admin_membership.is_active = False
        self.admin_membership.save(update_fields=["is_active", "updated_at"])
        self.authenticate(self.admin)
        self.assertEqual(
            self.client.patch(
                self.detail_url(self.cashier_membership),
                {"role": BusinessMembership.ROLE_VIEWER},
                format="json",
            ).status_code,
            404,
        )
        self.authenticate(self.owner)
        self.assertEqual(
            self.client.patch(
                self.detail_url(self.foreign_membership),
                {"role": BusinessMembership.ROLE_VIEWER},
                format="json",
            ).status_code,
            404,
        )

    def test_patch_blocks_self_owner_and_unknown_or_privileged_fields(self):
        self.authenticate(self.owner)
        self.assertEqual(
            self.client.patch(
                self.detail_url(self.owner_membership),
                {"role": BusinessMembership.ROLE_ADMIN},
                format="json",
            ).status_code,
            403,
        )
        for payload in (
            {"role": BusinessMembership.ROLE_OWNER},
            {"is_superuser": True},
            {"is_staff": True},
            {"user": str(self.platform_admin.public_id)},
            {"business": str(self.foreign_business.public_id)},
            {"employee": None},
            {"public_id": str(self.foreign_membership.public_id)},
            {"user_role": "business_admin"},
        ):
            with self.subTest(payload=payload):
                response = self.client.patch(
                    self.detail_url(self.cashier_membership),
                    payload,
                    format="json",
                )
                self.assertEqual(response.status_code, 400)

    def test_patch_activation_deactivation_and_legacy_delete_are_functional(self):
        self.authenticate(self.owner)
        deactivate = self.client.patch(
            self.detail_url(self.cashier_membership),
            {"is_active": False},
            format="json",
        )
        self.assertEqual(deactivate.status_code, 200)
        self.cashier_membership.refresh_from_db()
        self.assertFalse(self.cashier_membership.is_active)
        reactivate = self.client.patch(
            self.detail_url(self.cashier_membership),
            {"is_active": True},
            format="json",
        )
        self.assertEqual(reactivate.status_code, 200)
        canonical = self.detail_url(self.cashier_membership)
        self.assertEqual(self.client.delete(canonical).status_code, 204)
        self.assertEqual(self.client.delete(canonical).status_code, 204)
        self.assertEqual(
            self.client.patch(
                canonical,
                {"is_active": True},
                format="json",
            ).status_code,
            200,
        )
        legacy = reverse(
            "business-deactivate-member",
            kwargs={
                "public_id": self.business.public_id,
                "membership_public_id": self.cashier_membership.public_id,
            },
        )
        self.assertEqual(self.client.delete(legacy).status_code, 204)
        self.assertEqual(self.client.delete(legacy).status_code, 204)
        self.assertTrue(User.objects.filter(pk=self.cashier.pk).exists())
        self.assertTrue(Employee.objects.filter(pk=self.cashier_membership.employee_id).exists())

    def test_generic_delete_blocks_self_admin_peer_and_all_owners(self):
        self.authenticate(self.admin)
        self.assertEqual(self.client.delete(self.detail_url(self.admin_membership)).status_code, 403)
        self.authenticate(self.owner)
        self.assertEqual(self.client.delete(self.detail_url(self.owner_membership)).status_code, 403)
        self.authenticate(self.platform_admin)
        self.assertEqual(self.client.delete(self.detail_url(self.owner_membership)).status_code, 403)

    def test_promote_owner_contract_and_idempotency(self):
        url = reverse("business-promote-owner", kwargs={"public_id": self.business.public_id})
        self.authenticate(self.owner)
        response = self.client.post(
            url,
            {"membership_public_id": str(self.admin_membership.public_id)},
            format="json",
        )
        self.assertEqual(response.status_code, 200)
        self.admin_membership.refresh_from_db()
        self.assertEqual(self.admin_membership.role, BusinessMembership.ROLE_OWNER)
        self.admin_membership.employee = None
        self.admin_membership.save(update_fields=["employee", "updated_at"])
        self.assertEqual(self.client.post(url, {"membership_public_id": str(self.admin_membership.public_id)}, format="json").status_code, 200)
        self.assertIsNone(self.admin_membership.employee_id)

    def test_promotion_rejects_admin_inactive_targets_foreign_and_unknown_fields(self):
        url = reverse("business-promote-owner", kwargs={"public_id": self.business.public_id})
        self.authenticate(self.admin)
        self.assertEqual(self.client.post(url, {"membership_public_id": str(self.cashier_membership.public_id)}, format="json").status_code, 403)
        self.authenticate(self.owner)
        self.cashier_membership.is_active = False
        self.cashier_membership.save(update_fields=["is_active", "updated_at"])
        self.assertEqual(self.client.post(url, {"membership_public_id": str(self.cashier_membership.public_id)}, format="json").status_code, 400)
        self.seller.is_active = False
        self.seller.save(update_fields=["is_active", "updated_at"])
        self.assertEqual(self.client.post(url, {"membership_public_id": str(self.seller_membership.public_id)}, format="json").status_code, 400)
        self.assertEqual(self.client.post(url, {"membership_public_id": str(self.foreign_membership.public_id)}, format="json").status_code, 404)
        self.assertEqual(self.client.post(url, {"membership_public_id": str(uuid4())}, format="json").status_code, 404)
        self.assertEqual(self.client.post(url, {"membership_public_id": str(self.viewer_membership.public_id), "role": "owner"}, format="json").status_code, 400)

    def test_platform_admin_without_membership_can_promote(self):
        self.authenticate(self.platform_admin)
        response = self.client.post(
            reverse("business-promote-owner", kwargs={"public_id": self.business.public_id}),
            {"membership_public_id": str(self.admin_membership.public_id)},
            format="json",
        )
        self.assertEqual(response.status_code, 200)

    def test_transfer_is_atomic_and_validates_targets(self):
        url = reverse("business-ownership-transfer", kwargs={"public_id": self.business.public_id})
        self.authenticate(self.owner)
        before_users = User.objects.count()
        before_employees = Employee.objects.count()
        response = self.client.post(
            url,
            {
                "from_membership_public_id": str(self.owner_membership.public_id),
                "to_membership_public_id": str(self.admin_membership.public_id),
                "from_role": BusinessMembership.ROLE_ADMIN,
            },
            format="json",
        )
        self.assertEqual(response.status_code, 200)
        self.owner_membership.refresh_from_db()
        self.admin_membership.refresh_from_db()
        self.assertEqual(self.owner_membership.role, BusinessMembership.ROLE_ADMIN)
        self.assertEqual(self.admin_membership.role, BusinessMembership.ROLE_OWNER)
        self.assertEqual(User.objects.count(), before_users)
        self.assertEqual(Employee.objects.count(), before_employees)
        self.authenticate(self.platform_admin)
        same = self.client.post(
            url,
            {
                "from_membership_public_id": str(self.admin_membership.public_id),
                "to_membership_public_id": str(self.admin_membership.public_id),
                "from_role": "admin",
            },
            format="json",
        )
        self.assertEqual(same.status_code, 400)

    def test_transfer_rolls_back_if_second_membership_save_fails(self):
        url = reverse("business-ownership-transfer", kwargs={"public_id": self.business.public_id})
        self.authenticate(self.owner)
        original_save = BusinessMembership.save

        def fail_source_save(instance, *args, **kwargs):
            if (
                instance.pk == self.owner_membership.pk
                and instance.role == BusinessMembership.ROLE_ADMIN
            ):
                raise RuntimeError("forced transfer failure")
            return original_save(instance, *args, **kwargs)

        with patch.object(BusinessMembership, "save", autospec=True, side_effect=fail_source_save):
            with self.assertRaisesRegex(RuntimeError, "forced transfer failure"):
                self.client.post(
                    url,
                    {
                        "from_membership_public_id": str(self.owner_membership.public_id),
                        "to_membership_public_id": str(self.admin_membership.public_id),
                        "from_role": BusinessMembership.ROLE_ADMIN,
                    },
                    format="json",
                )

        self.owner_membership.refresh_from_db()
        self.admin_membership.refresh_from_db()
        self.assertEqual(self.owner_membership.role, BusinessMembership.ROLE_OWNER)
        self.assertEqual(self.admin_membership.role, BusinessMembership.ROLE_ADMIN)

    def test_transfer_rejects_inactive_destination_user_and_foreign_membership(self):
        url = reverse("business-ownership-transfer", kwargs={"public_id": self.business.public_id})
        self.authenticate(self.cashier)
        denied = self.client.post(
            url,
            {
                "from_membership_public_id": str(self.owner_membership.public_id),
                "to_membership_public_id": str(self.admin_membership.public_id),
                "from_role": "admin",
            },
            format="json",
        )
        self.assertEqual(denied.status_code, 403)
        self.authenticate(self.owner)
        base = {
            "from_membership_public_id": str(self.owner_membership.public_id),
            "from_role": "admin",
        }
        self.cashier_membership.is_active = False
        self.cashier_membership.save(update_fields=["is_active", "updated_at"])
        response = self.client.post(url, {**base, "to_membership_public_id": str(self.cashier_membership.public_id)}, format="json")
        self.assertEqual(response.status_code, 400)
        self.seller.is_active = False
        self.seller.save(update_fields=["is_active", "updated_at"])
        response = self.client.post(url, {**base, "to_membership_public_id": str(self.seller_membership.public_id)}, format="json")
        self.assertEqual(response.status_code, 400)
        response = self.client.post(url, {**base, "to_membership_public_id": str(self.foreign_membership.public_id)}, format="json")
        self.assertEqual(response.status_code, 404)
        self.owner_membership.refresh_from_db()
        self.assertEqual(self.owner_membership.role, BusinessMembership.ROLE_OWNER)

    def test_additional_owner_can_be_removed_but_last_owner_and_self_are_protected(self):
        create_membership(
            user=self.admin,
            business=self.business,
            role=BusinessMembership.ROLE_OWNER,
            employee=None,
        )
        remove_url = reverse(
            "business-remove-business-owner",
            kwargs={
                "public_id": self.business.public_id,
                "membership_public_id": self.admin_membership.public_id,
            },
        )
        self.authenticate(self.owner)
        response = self.client.post(remove_url, {"replacement_role": "admin"}, format="json")
        self.assertEqual(response.status_code, 200)
        self.admin_membership.refresh_from_db()
        self.assertEqual(self.admin_membership.role, BusinessMembership.ROLE_ADMIN)
        self.assertTrue(self.admin_membership.is_active)
        self.assertIsNone(self.admin_membership.employee_id)
        self.assertTrue(User.objects.filter(pk=self.admin.pk).exists())
        self.authenticate(self.platform_admin)
        last_url = reverse(
            "business-remove-business-owner",
            kwargs={
                "public_id": self.business.public_id,
                "membership_public_id": self.owner_membership.public_id,
            },
        )
        self.assertEqual(self.client.post(last_url, {"replacement_role": "admin"}, format="json").status_code, 409)
        self.authenticate(self.owner)
        self.assertEqual(self.client.post(last_url, {"replacement_role": "admin"}, format="json").status_code, 403)

    def test_inactive_owner_membership_can_be_demoted_when_an_effective_owner_remains(self):
        create_membership(
            user=self.admin,
            business=self.business,
            role=BusinessMembership.ROLE_OWNER,
            employee=self.admin_membership.employee,
            is_active=False,
        )
        employee_id = self.admin_membership.employee_id
        self.authenticate(self.owner)
        response = self.client.post(
            reverse(
                "business-remove-business-owner",
                kwargs={
                    "public_id": self.business.public_id,
                    "membership_public_id": self.admin_membership.public_id,
                },
            ),
            {"replacement_role": BusinessMembership.ROLE_ADMIN},
            format="json",
        )
        self.assertEqual(response.status_code, 200)
        self.admin_membership.refresh_from_db()
        self.assertEqual(self.admin_membership.role, BusinessMembership.ROLE_ADMIN)
        self.assertFalse(self.admin_membership.is_active)
        self.assertTrue(User.objects.filter(pk=self.admin.pk).exists())
        self.assertTrue(Employee.objects.filter(pk=employee_id).exists())
        self.assertEqual(
            BusinessMembership.objects.filter(
                business=self.business,
                role=BusinessMembership.ROLE_OWNER,
                is_active=True,
                user__is_active=True,
            ).count(),
            1,
        )

    def test_owner_with_inactive_user_can_be_demoted_when_an_effective_owner_remains(self):
        create_membership(
            user=self.admin,
            business=self.business,
            role=BusinessMembership.ROLE_OWNER,
            employee=self.admin_membership.employee,
        )
        employee_id = self.admin_membership.employee_id
        self.admin.is_active = False
        self.admin.save(update_fields=["is_active", "updated_at"])
        self.authenticate(self.owner)
        response = self.client.post(
            reverse(
                "business-remove-business-owner",
                kwargs={
                    "public_id": self.business.public_id,
                    "membership_public_id": self.admin_membership.public_id,
                },
            ),
            {"replacement_role": BusinessMembership.ROLE_ADMIN},
            format="json",
        )
        self.assertEqual(response.status_code, 200)
        self.admin_membership.refresh_from_db()
        self.admin.refresh_from_db()
        self.assertEqual(self.admin_membership.role, BusinessMembership.ROLE_ADMIN)
        self.assertTrue(self.admin_membership.is_active)
        self.assertFalse(self.admin.is_active)
        self.assertTrue(Employee.objects.filter(pk=employee_id).exists())
        self.assertEqual(
            BusinessMembership.objects.filter(
                business=self.business,
                role=BusinessMembership.ROLE_OWNER,
                is_active=True,
                user__is_active=True,
            ).count(),
            1,
        )

    def test_membership_changes_apply_with_same_jwt(self):
        token = RefreshToken.for_user(self.admin).access_token
        client = APIClient()
        client.credentials(HTTP_AUTHORIZATION=f"Bearer {token}")
        business_url = reverse("business-detail", kwargs={"public_id": self.business.public_id})
        self.assertEqual(client.get(business_url).status_code, 200)
        self.client.force_authenticate(self.owner)
        self.assertEqual(
            self.client.patch(self.detail_url(self.admin_membership), {"role": "viewer"}, format="json").status_code,
            200,
        )
        create_access_url = reverse("business-create-employee-access", kwargs={"public_id": self.business.public_id})
        self.assertEqual(client.post(create_access_url, {}, format="json").status_code, 403)
        self.client.force_authenticate(self.owner)
        self.assertEqual(
            self.client.patch(self.detail_url(self.admin_membership), {"is_active": False}, format="json").status_code,
            200,
        )
        self.assertEqual(client.get(business_url).status_code, 404)


class BusinessMembershipConcurrencyTests(TransactionTestCase):
    reset_sequences = True

    def setUp(self):
        self.active = create_status("Activo")
        self.owner = create_user(email="membership-race-owner@playnow.test")
        self.business = create_business(user=self.owner, status=self.active)
        self.owner_membership = BusinessMembership.objects.get(
            user=self.owner,
            business=self.business,
        )
        self.second_owner = create_user(email="membership-race-owner2@playnow.test")
        self.second_owner_membership = create_membership(
            user=self.second_owner,
            business=self.business,
            role=BusinessMembership.ROLE_OWNER,
        )
        self.admin = create_user(email="membership-race-admin@playnow.test")
        self.admin_membership = create_membership(
            user=self.admin,
            business=self.business,
            role=BusinessMembership.ROLE_ADMIN,
        )
        self.viewer = create_user(email="membership-race-viewer@playnow.test")
        self.viewer_membership = create_membership(
            user=self.viewer,
            business=self.business,
            role=BusinessMembership.ROLE_VIEWER,
        )
        self.platform = create_user(
            email="membership-race-platform@playnow.test",
            is_superuser=True,
        )
        self.second_platform = create_user(
            email="membership-race-platform2@playnow.test",
            is_superuser=True,
        )

    def run_concurrently(self, prepare_operations):
        barrier = Barrier(len(prepare_operations))

        def worker(prepare_operation):
            close_old_connections()
            try:
                operation = prepare_operation()
                barrier.wait(timeout=10)
                return operation()
            except MembershipDomainError as exc:
                return exc.kind
            finally:
                close_old_connections()
                connections.close_all()

        with ThreadPoolExecutor(max_workers=len(prepare_operations)) as executor:
            futures = [
                executor.submit(worker, prepare_operation)
                for prepare_operation in prepare_operations
            ]
            return [future.result(timeout=20) for future in futures]

    def run_after_committed_change(self, *, prepare_operation, commit_change):
        ready = Event()
        proceed = Event()

        def worker():
            close_old_connections()
            try:
                operation = prepare_operation()
                ready.set()
                if not proceed.wait(timeout=10):
                    raise TimeoutError("The committed change was not released.")
                return operation()
            except MembershipDomainError as exc:
                return exc.kind
            finally:
                close_old_connections()
                connections.close_all()

        with ThreadPoolExecutor(max_workers=1) as executor:
            future = executor.submit(worker)
            if not ready.wait(timeout=10):
                proceed.set()
                future.result(timeout=20)
                self.fail("The protected operation was not prepared.")
            try:
                commit_change()
            finally:
                proceed.set()
            return future.result(timeout=20)

    def fresh(self, model, pk):
        return model.objects.get(pk=pk)

    def run_http_after_committed_user_change(
        self,
        *,
        viewset_class,
        method_name,
        request_operation,
        user_change,
    ):
        ready = Event()
        proceed = Event()
        original = getattr(viewset_class, method_name)

        def synchronized(view, request, *args, **kwargs):
            if not request.user.is_superuser or not request.user.is_active:
                raise AssertionError("The request did not authenticate with stale privileges.")
            ready.set()
            if not proceed.wait(timeout=10):
                raise TimeoutError("The HTTP request was not released.")
            return original(view, request, *args, **kwargs)

        def worker():
            close_old_connections()
            try:
                return request_operation()
            finally:
                close_old_connections()
                connections.close_all()

        with patch.object(viewset_class, method_name, synchronized):
            with ThreadPoolExecutor(max_workers=1) as executor:
                future = executor.submit(worker)
                if not ready.wait(timeout=10):
                    proceed.set()
                    future.result(timeout=20)
                    self.fail("The authenticated HTTP request was not prepared.")
                try:
                    user_change()
                finally:
                    proceed.set()
                return future.result(timeout=20)

    def stale_platform_request(self, *, method, url, payload=None):
        User.objects.filter(pk=self.platform.pk).update(
            is_active=True,
            is_staff=True,
            is_superuser=True,
        )
        self.platform.refresh_from_db()
        token = RefreshToken.for_user(self.platform).access_token

        def request_operation():
            client = APIClient()
            client.credentials(HTTP_AUTHORIZATION=f"Bearer {token}")
            request_method = getattr(client, method)
            if payload is None:
                return request_method(url)
            return request_method(url, payload, format="json")

        return request_operation

    def membership_state(self):
        return list(
            BusinessMembership.objects.order_by("pk").values_list(
                "pk",
                "role",
                "is_active",
            )
        )

    def assert_hidden_after_platform_change(
        self,
        *,
        viewset_class,
        method_name,
        method,
        url,
        payload=None,
        user_change=None,
    ):
        before = self.membership_state()
        response = self.run_http_after_committed_user_change(
            viewset_class=viewset_class,
            method_name=method_name,
            request_operation=self.stale_platform_request(
                method=method,
                url=url,
                payload=payload,
            ),
            user_change=(
                user_change
                or (lambda: User.objects.filter(pk=self.platform.pk).update(
                    is_superuser=False,
                    is_staff=False,
                ))
            ),
        )
        self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND)
        self.assertEqual(
            response.data,
            {"detail": "El recurso solicitado no se encuentra disponible."},
        )
        self.assertEqual(self.membership_state(), before)
        return response

    def test_http_mutations_hide_existing_and_missing_resources_after_revocation(self):
        unknown_business = uuid4()
        unknown_membership = uuid4()
        canonical_existing = reverse(
            "business-membership-detail",
            kwargs={"membership_public_id": self.admin_membership.public_id},
        )
        canonical_missing = reverse(
            "business-membership-detail",
            kwargs={"membership_public_id": unknown_membership},
        )
        business_urls = {
            "promote_owner": lambda business_id, membership_id: reverse(
                "business-promote-owner",
                kwargs={"public_id": business_id},
            ),
            "ownership_transfer": lambda business_id, membership_id: reverse(
                "business-ownership-transfer",
                kwargs={"public_id": business_id},
            ),
            "remove_business_owner": lambda business_id, membership_id: reverse(
                "business-remove-business-owner",
                kwargs={
                    "public_id": business_id,
                    "membership_public_id": membership_id,
                },
            ),
            "deactivate_member": lambda business_id, membership_id: reverse(
                "business-deactivate-member",
                kwargs={
                    "public_id": business_id,
                    "membership_public_id": membership_id,
                },
            ),
        }

        cases = [
            (
                BusinessMembershipViewSet,
                "partial_update",
                "patch",
                canonical_existing,
                {"role": "owner", "unexpected": True},
            ),
            (
                BusinessMembershipViewSet,
                "partial_update",
                "patch",
                canonical_missing,
                {"role": "owner", "unexpected": True},
            ),
            (
                BusinessMembershipViewSet,
                "destroy",
                "delete",
                canonical_existing,
                None,
            ),
            (
                BusinessMembershipViewSet,
                "destroy",
                "delete",
                canonical_missing,
                None,
            ),
        ]
        valid_payloads = {
            "promote_owner": {
                "membership_public_id": str(self.admin_membership.public_id),
            },
            "ownership_transfer": {
                "from_membership_public_id": str(self.owner_membership.public_id),
                "to_membership_public_id": str(self.admin_membership.public_id),
                "from_role": BusinessMembership.ROLE_ADMIN,
            },
            "remove_business_owner": {
                "replacement_role": BusinessMembership.ROLE_ADMIN,
            },
            "deactivate_member": None,
        }
        missing_target_payloads = {
            "promote_owner": {"membership_public_id": str(unknown_membership)},
            "ownership_transfer": {
                "from_membership_public_id": str(self.owner_membership.public_id),
                "to_membership_public_id": str(unknown_membership),
                "from_role": BusinessMembership.ROLE_ADMIN,
            },
            "remove_business_owner": {
                "replacement_role": BusinessMembership.ROLE_ADMIN,
            },
            "deactivate_member": None,
        }
        invalid_payloads = {
            "promote_owner": {"membership_public_id": "invalid"},
            "ownership_transfer": {"from_role": "owner"},
            "remove_business_owner": {"replacement_role": "owner"},
            "deactivate_member": None,
        }
        target_ids = {
            "promote_owner": self.admin_membership.public_id,
            "ownership_transfer": self.admin_membership.public_id,
            "remove_business_owner": self.second_owner_membership.public_id,
            "deactivate_member": self.admin_membership.public_id,
        }

        for action_name, url_builder in business_urls.items():
            cases.extend([
                (
                    BusinessViewSet,
                    action_name,
                    "delete" if action_name == "deactivate_member" else "post",
                    url_builder(self.business.public_id, target_ids[action_name]),
                    valid_payloads[action_name],
                ),
                (
                    BusinessViewSet,
                    action_name,
                    "delete" if action_name == "deactivate_member" else "post",
                    url_builder(self.business.public_id, unknown_membership),
                    missing_target_payloads[action_name],
                ),
                (
                    BusinessViewSet,
                    action_name,
                    "delete" if action_name == "deactivate_member" else "post",
                    url_builder(self.business.public_id, target_ids[action_name]),
                    invalid_payloads[action_name],
                ),
                (
                    BusinessViewSet,
                    action_name,
                    "delete" if action_name == "deactivate_member" else "post",
                    url_builder(unknown_business, target_ids[action_name]),
                    invalid_payloads[action_name],
                ),
            ])

        responses = []
        for viewset_class, method_name, method, url, payload in cases:
            with self.subTest(action=method_name, method=method, url=url):
                responses.append(self.assert_hidden_after_platform_change(
                    viewset_class=viewset_class,
                    method_name=method_name,
                    method=method,
                    url=url,
                    payload=payload,
                ))
        self.assertTrue(responses)
        self.assertEqual(
            {response.status_code for response in responses},
            {status.HTTP_404_NOT_FOUND},
        )
        self.assertEqual(
            {str(response.data["detail"]) for response in responses},
            {"El recurso solicitado no se encuentra disponible."},
        )

    def test_http_mutation_hides_resource_after_actor_deactivation(self):
        url = reverse(
            "business-membership-detail",
            kwargs={"membership_public_id": self.admin_membership.public_id},
        )
        self.assert_hidden_after_platform_change(
            viewset_class=BusinessMembershipViewSet,
            method_name="partial_update",
            method="patch",
            url=url,
            payload={"role": "owner", "unexpected": True},
            user_change=lambda: User.objects.filter(pk=self.platform.pk).update(
                is_active=False,
            ),
        )

    def test_service_rejects_all_non_owner_role_violations_without_writes(self):
        invalid_roles = (BusinessMembership.ROLE_OWNER, "invalid", "", None)
        for invalid_role in invalid_roles:
            with self.subTest(operation="transfer", role=invalid_role):
                before = self.membership_state()
                with self.assertRaises(MembershipDomainError) as context:
                    transfer_ownership(
                        actor=self.platform,
                        business=self.business,
                        from_membership=self.owner_membership,
                        to_membership=self.admin_membership,
                        from_role=invalid_role,
                    )
                self.assertEqual(context.exception.kind, "invalid")
                self.assertEqual(
                    list(context.exception.detail),
                    ["from_role"],
                )
                self.assertEqual(self.membership_state(), before)

            with self.subTest(operation="remove", role=invalid_role):
                before = self.membership_state()
                with self.assertRaises(MembershipDomainError) as context:
                    remove_owner(
                        actor=self.platform,
                        business=self.business,
                        membership=self.second_owner_membership,
                        replacement_role=invalid_role,
                    )
                self.assertEqual(context.exception.kind, "invalid")
                self.assertEqual(
                    list(context.exception.detail),
                    ["replacement_role"],
                )
                self.assertEqual(self.membership_state(), before)
        self.assertEqual(
            BusinessMembership.objects.filter(
                business=self.business,
                role=BusinessMembership.ROLE_OWNER,
                is_active=True,
                user__is_active=True,
            ).count(),
            2,
        )

    def test_revoked_platform_admin_cannot_use_stale_global_bypass(self):
        def prepare_operation():
            actor = self.fresh(User, self.platform.pk)
            membership = self.fresh(
                BusinessMembership,
                self.admin_membership.pk,
            )
            return lambda: update_membership(
                actor=actor,
                membership=membership,
                changes={"role": BusinessMembership.ROLE_VIEWER},
            )

        result = self.run_after_committed_change(
            prepare_operation=prepare_operation,
            commit_change=lambda: User.objects.filter(pk=self.platform.pk).update(
                is_superuser=False,
                is_staff=False,
            ),
        )

        self.assertEqual(result, "forbidden")
        self.admin_membership.refresh_from_db()
        self.assertEqual(
            self.admin_membership.role,
            BusinessMembership.ROLE_ADMIN,
        )

    def test_inactive_owner_user_cannot_authorize_with_stale_user(self):
        def prepare_operation():
            actor = self.fresh(User, self.owner.pk)
            business = self.fresh(type(self.business), self.business.pk)
            membership = self.fresh(
                BusinessMembership,
                self.admin_membership.pk,
            )
            return lambda: promote_to_owner(
                actor=actor,
                business=business,
                membership=membership,
            )

        result = self.run_after_committed_change(
            prepare_operation=prepare_operation,
            commit_change=lambda: User.objects.filter(pk=self.owner.pk).update(
                is_active=False,
            ),
        )

        self.assertEqual(result, "forbidden")
        self.admin_membership.refresh_from_db()
        self.assertEqual(
            self.admin_membership.role,
            BusinessMembership.ROLE_ADMIN,
        )

    def test_inactive_actor_membership_is_revalidated_after_wait(self):
        def prepare_operation():
            actor = self.fresh(User, self.owner.pk)
            business = self.fresh(type(self.business), self.business.pk)
            membership = self.fresh(
                BusinessMembership,
                self.admin_membership.pk,
            )
            return lambda: promote_to_owner(
                actor=actor,
                business=business,
                membership=membership,
            )

        result = self.run_after_committed_change(
            prepare_operation=prepare_operation,
            commit_change=lambda: BusinessMembership.objects.filter(
                pk=self.owner_membership.pk,
            ).update(is_active=False),
        )

        self.assertEqual(result, "forbidden")
        self.admin_membership.refresh_from_db()
        self.assertEqual(
            self.admin_membership.role,
            BusinessMembership.ROLE_ADMIN,
        )

    def test_actor_role_is_revalidated_after_wait(self):
        def prepare_operation():
            actor = self.fresh(User, self.owner.pk)
            business = self.fresh(type(self.business), self.business.pk)
            membership = self.fresh(
                BusinessMembership,
                self.admin_membership.pk,
            )
            return lambda: promote_to_owner(
                actor=actor,
                business=business,
                membership=membership,
            )

        result = self.run_after_committed_change(
            prepare_operation=prepare_operation,
            commit_change=lambda: BusinessMembership.objects.filter(
                pk=self.owner_membership.pk,
            ).update(role=BusinessMembership.ROLE_VIEWER),
        )

        self.assertEqual(result, "forbidden")
        self.admin_membership.refresh_from_db()
        self.assertEqual(
            self.admin_membership.role,
            BusinessMembership.ROLE_ADMIN,
        )

    def test_two_last_owner_removals_leave_at_least_one_owner(self):
        def prepare_remove(pk, actor_pk):
            def prepare():
                actor = self.fresh(User, actor_pk)
                business = self.fresh(type(self.business), self.business.pk)
                membership = self.fresh(BusinessMembership, pk)
                return lambda: remove_owner(
                    actor=actor,
                    business=business,
                    membership=membership,
                    replacement_role=BusinessMembership.ROLE_ADMIN,
                ).role
            return prepare

        results = self.run_concurrently([
            prepare_remove(self.owner_membership.pk, self.platform.pk),
            prepare_remove(
                self.second_owner_membership.pk,
                self.second_platform.pk,
            ),
        ])
        self.assertEqual(results.count(BusinessMembership.ROLE_ADMIN), 1)
        self.assertEqual(results.count("conflict"), 1)
        self.assertEqual(
            BusinessMembership.objects.filter(
                business=self.business,
                role=BusinessMembership.ROLE_OWNER,
                is_active=True,
                user__is_active=True,
            ).count(),
            1,
        )

    def test_two_removals_of_same_owner_are_consistent(self):
        def prepare_operation():
            actor = self.fresh(User, self.platform.pk)
            business = self.fresh(type(self.business), self.business.pk)
            membership = self.fresh(
                BusinessMembership,
                self.second_owner_membership.pk,
            )
            return lambda: remove_owner(
                actor=actor,
                business=business,
                membership=membership,
                replacement_role=BusinessMembership.ROLE_ADMIN,
            ).role

        results = self.run_concurrently([prepare_operation, prepare_operation])
        self.assertEqual(results.count(BusinessMembership.ROLE_ADMIN), 1)
        self.assertEqual(results.count("invalid"), 1)

    def test_promotion_and_transfer_are_serializable(self):
        def prepare_promotion():
            actor = self.fresh(User, self.platform.pk)
            business = self.fresh(type(self.business), self.business.pk)
            membership = self.fresh(
                BusinessMembership,
                self.viewer_membership.pk,
            )
            return lambda: promote_to_owner(
                actor=actor,
                business=business,
                membership=membership,
            ).role

        def prepare_transfer():
            source = self.fresh(BusinessMembership, self.owner_membership.pk)
            target = self.fresh(BusinessMembership, self.admin_membership.pk)
            actor = self.fresh(User, self.platform.pk)
            business = self.fresh(type(self.business), self.business.pk)

            def operation():
                transfer_ownership(
                    actor=actor,
                    business=business,
                    from_membership=source,
                    to_membership=target,
                    from_role=BusinessMembership.ROLE_ADMIN,
                )
                return "transferred"
            return operation

        results = self.run_concurrently([prepare_promotion, prepare_transfer])
        self.assertCountEqual(results, [BusinessMembership.ROLE_OWNER, "transferred"])
        self.assertGreaterEqual(
            BusinessMembership.objects.filter(
                business=self.business,
                role=BusinessMembership.ROLE_OWNER,
                is_active=True,
                user__is_active=True,
            ).count(),
            1,
        )

    def test_two_promotions_of_same_membership_are_idempotent(self):
        def prepare_operation():
            actor = self.fresh(User, self.platform.pk)
            business = self.fresh(type(self.business), self.business.pk)
            membership = self.fresh(
                BusinessMembership,
                self.admin_membership.pk,
            )
            return lambda: promote_to_owner(
                actor=actor,
                business=business,
                membership=membership,
            ).role

        results = self.run_concurrently([prepare_operation, prepare_operation])
        self.assertEqual(results, [BusinessMembership.ROLE_OWNER] * 2)
        self.assertEqual(
            BusinessMembership.objects.filter(
                business=self.business,
                user=self.admin,
            ).count(),
            1,
        )

    def test_incompatible_transfers_have_no_partial_state(self):
        def prepare_transfer(target_pk):
            def prepare():
                actor = self.fresh(User, self.platform.pk)
                business = self.fresh(type(self.business), self.business.pk)
                source = self.fresh(
                    BusinessMembership,
                    self.owner_membership.pk,
                )
                target = self.fresh(BusinessMembership, target_pk)

                def operation():
                    transfer_ownership(
                        actor=actor,
                        business=business,
                        from_membership=source,
                        to_membership=target,
                        from_role=BusinessMembership.ROLE_ADMIN,
                    )
                    return "transferred"
                return operation
            return prepare

        results = self.run_concurrently([
            prepare_transfer(self.admin_membership.pk),
            prepare_transfer(self.viewer_membership.pk),
        ])
        self.assertEqual(results.count("transferred"), 1)
        self.assertEqual(results.count("conflict"), 1)
        self.assertGreaterEqual(
            BusinessMembership.objects.filter(
                business=self.business,
                role=BusinessMembership.ROLE_OWNER,
                is_active=True,
                user__is_active=True,
            ).count(),
            1,
        )
