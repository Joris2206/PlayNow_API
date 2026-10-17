from concurrent.futures import ThreadPoolExecutor
from datetime import date
from threading import Barrier
from unittest.mock import patch

from django.db import close_old_connections
from django.test import TransactionTestCase
from drf_spectacular.generators import SchemaGenerator
from rest_framework import status
from rest_framework.test import APIClient, APITestCase

from core.models import Business, BusinessMembership, Employee, User
from core.services.business_lifecycle import (
    BusinessLifecycleError,
    archive_business,
    reactivate_business,
    suspend_business,
)
from core.services import business_lifecycle as lifecycle_service
from core.tests.factories import (
    create_business,
    create_cash_register,
    create_category,
    create_commission_plan,
    create_employee,
    create_membership,
    create_product,
    create_role_user,
    create_status,
    create_transaction,
    create_user,
)
from core.tests.helpers import get_response_results


class BusinessSuspensionAPITests(APITestCase):
    @classmethod
    def setUpTestData(cls):
        cls.active = create_status("Activo")
        cls.inactive = create_status("Inactivo")
        cls.deleted = create_status("Eliminado")
        cls.owner = create_user(email="suspension-owner@playnow.test")
        cls.business = create_business(
            user=cls.owner,
            status=cls.active,
            business_name="Suspension Business",
        )
        cls.admin, _, cls.admin_membership = create_role_user(
            business=cls.business,
            role=BusinessMembership.ROLE_ADMIN,
            status=cls.active,
            email="suspension-admin@playnow.test",
        )
        cls.viewer, _, cls.viewer_membership = create_role_user(
            business=cls.business,
            role=BusinessMembership.ROLE_VIEWER,
            status=cls.active,
            email="suspension-viewer@playnow.test",
        )
        cls.lower_role_users = []
        for role in (
            BusinessMembership.ROLE_CASHIER,
            BusinessMembership.ROLE_SELLER,
            BusinessMembership.ROLE_INVENTORY,
        ):
            user = create_user(email=f"suspension-{role}@playnow.test")
            create_membership(user=user, business=cls.business, role=role)
            cls.lower_role_users.append(user)
        cls.platform = create_user(
            email="suspension-platform@playnow.test",
            is_superuser=True,
        )
        cls.employee = create_employee(
            business=cls.business,
            status=cls.active,
            full_name="Suspension Employee",
        )
        cls.product = create_product(
            business=cls.business,
            status=cls.active,
            title="Suspension Product",
        )
        cls.category = create_category(
            business=cls.business,
            status=cls.active,
            name="Suspension Category",
        )
        cls.transaction = create_transaction(
            business=cls.business,
            created_by=cls.owner,
            status=cls.active,
            employee=cls.employee,
        )
        cls.cash_register = create_cash_register(
            business=cls.business,
            employee=cls.employee,
            opened_by=cls.owner,
        )
        cls.commission_plan = create_commission_plan(employee=cls.employee)

    def authenticate(self, user):
        self.client.force_authenticate(user=user)

    @property
    def detail_url(self):
        return f"/api/businesses/{self.business.public_id}/"

    def action_url(self, action):
        return f"{self.detail_url}{action}/"

    def suspend(self, user=None):
        self.authenticate(user or self.owner)
        return self.client.post(self.action_url("suspend"), {}, format="json")

    def test_platform_admin_and_owner_suspend_and_reactivate_idempotently(self):
        with patch("core.services.business_lifecycle.log_action") as audit:
            response = self.suspend(self.owner)
            self.assertEqual(response.status_code, status.HTTP_200_OK)
            self.assertEqual(response.data["status_name"], "Inactivo")
            audit.assert_called_once()

            audit.reset_mock()
            repeated = self.suspend(self.owner)
            self.assertEqual(repeated.status_code, status.HTTP_200_OK)
            audit.assert_not_called()

            self.authenticate(self.platform)
            response = self.client.post(
                self.action_url("reactivate"), {}, format="json"
            )
            self.assertEqual(response.status_code, status.HTTP_200_OK)
            self.assertEqual(response.data["status_name"], "Activo")
            audit.assert_called_once()

            audit.reset_mock()
            repeated = self.client.post(
                self.action_url("reactivate"), {}, format="json"
            )
            self.assertEqual(repeated.status_code, status.HTTP_200_OK)
            audit.assert_not_called()

    def test_non_owner_roles_are_forbidden_and_body_is_closed(self):
        for actor in (self.admin, self.viewer, *self.lower_role_users):
            with self.subTest(actor=actor.email):
                self.authenticate(actor)
                response = self.client.post(
                    self.action_url("suspend"), {}, format="json"
                )
                self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)

        self.authenticate(self.owner)
        response = self.client.post(
            self.action_url("suspend"), {"status": "Inactivo"}, format="json"
        )
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

    def test_suspended_visibility_me_and_reactivation_preserve_relations(self):
        membership_ids = set(
            self.business.memberships.values_list("pk", flat=True)
        )
        employee_ids = set(
            self.business.employees.values_list("pk", flat=True)
        )
        self.assertEqual(self.suspend().status_code, status.HTTP_200_OK)

        listing = self.client.get("/api/businesses/")
        returned = {row["public_id"] for row in get_response_results(listing)}
        self.assertIn(str(self.business.public_id), returned)
        self.assertEqual(self.client.get(self.detail_url).status_code, status.HTTP_200_OK)
        me = self.client.get("/api/me/")
        self.assertEqual(me.status_code, status.HTTP_200_OK)
        self.assertEqual(me.data["memberships"], [])

        for actor in (self.admin, self.viewer, *self.lower_role_users):
            with self.subTest(actor=actor.email):
                self.authenticate(actor)
                listing = self.client.get("/api/businesses/")
                returned = {
                    row["public_id"] for row in get_response_results(listing)
                }
                self.assertNotIn(str(self.business.public_id), returned)
                self.assertEqual(
                    self.client.get(self.detail_url).status_code,
                    status.HTTP_404_NOT_FOUND,
                )

        self.authenticate(self.owner)
        response = self.client.post(
            self.action_url("reactivate"), {}, format="json"
        )
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(
            set(self.business.memberships.values_list("pk", flat=True)),
            membership_ids,
        )
        self.assertEqual(
            set(self.business.employees.values_list("pk", flat=True)),
            employee_ids,
        )
        self.assertEqual(len(self.client.get("/api/me/").data["memberships"]), 1)

    def test_operational_domains_and_public_catalog_return_not_found(self):
        self.assertEqual(self.suspend().status_code, status.HTTP_200_OK)
        business_param = {"business_public_id": str(self.business.public_id)}
        requests = (
            (f"/api/products/{self.product.public_id}/", None),
            (f"/api/categories/{self.category.public_id}/", None),
            ("/api/stock-movements/", business_param),
            (f"/api/transactions/{self.transaction.public_id}/", None),
            (f"/api/cash-registers/{self.cash_register.public_id}/", None),
            (f"/api/commission-plans/{self.commission_plan.public_id}/", None),
            (
                "/api/reports/monthly-summary/",
                {**business_param, "year": date.today().year, "month": 1},
            ),
            (f"/api/employees/{self.employee.public_id}/", None),
        )
        for endpoint, params in requests:
            with self.subTest(endpoint=endpoint):
                response = self.client.get(endpoint, params or {})
                self.assertEqual(
                    response.status_code,
                    status.HTTP_404_NOT_FOUND,
                    msg=getattr(response, "data", None),
                )

        self.assertEqual(
            self.client.patch(
                f"/api/products/{self.product.public_id}/",
                {"title": "Blocked update"},
                format="json",
            ).status_code,
            status.HTTP_404_NOT_FOUND,
        )

        self.authenticate(self.platform)
        self.assertEqual(
            self.client.get(
                f"/api/products/{self.product.public_id}/"
            ).status_code,
            status.HTTP_404_NOT_FOUND,
        )
        public = APIClient().get("/api/public/products/", business_param)
        self.assertEqual(public.status_code, status.HTTP_200_OK)
        self.assertEqual(get_response_results(public), [])
        public_categories = APIClient().get(
            "/api/public/categories/", business_param
        )
        self.assertEqual(get_response_results(public_categories), [])

    def test_delete_is_platform_only_terminal_and_audited(self):
        self.authenticate(self.owner)
        self.assertEqual(
            self.client.delete(self.detail_url).status_code,
            status.HTTP_403_FORBIDDEN,
        )
        self.authenticate(self.admin)
        self.assertEqual(
            self.client.delete(self.detail_url).status_code,
            status.HTTP_403_FORBIDDEN,
        )

        self.authenticate(self.platform)
        with patch("core.services.business_lifecycle.log_action") as audit:
            response = self.client.delete(self.detail_url)
            self.assertEqual(response.status_code, status.HTTP_204_NO_CONTENT)
            audit.assert_called_once()
        self.business.refresh_from_db()
        self.assertEqual(self.business.status, self.deleted)
        self.assertEqual(
            self.client.post(
                self.action_url("reactivate"), {}, format="json"
            ).status_code,
            status.HTTP_409_CONFLICT,
        )
        self.assertEqual(
            self.client.delete(self.detail_url).status_code,
            status.HTTP_409_CONFLICT,
        )
        self.authenticate(self.owner)
        self.assertEqual(
            self.client.get(self.detail_url).status_code,
            status.HTTP_404_NOT_FOUND,
        )

    def test_missing_status_configuration_and_stale_owner_state_are_safe(self):
        self.inactive.delete()
        response = self.suspend(self.owner)
        self.assertEqual(response.status_code, status.HTTP_409_CONFLICT)
        self.business.refresh_from_db()
        self.assertEqual(self.business.status, self.active)

        create_status("Inactivo")
        self.authenticate(self.owner)
        type(self.owner).objects.filter(pk=self.owner.pk).update(is_active=False)
        response = self.client.post(
            self.action_url("suspend"), {}, format="json"
        )
        self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND)

    def test_status_public_id_is_rejected_and_create_is_always_active(self):
        self.authenticate(self.owner)
        initial_counts = {
            "businesses": Business.objects.count(),
            "memberships": BusinessMembership.objects.count(),
            "users": User.objects.count(),
            "employees": Employee.objects.count(),
        }
        forbidden_responses = []
        initial_owner_values = (
            None,
            "not-an-email",
            "missing-owner@playnow.test",
            self.owner.email,
        )
        with patch("core.views.log_action") as audit:
            for initial_owner_email in initial_owner_values:
                payload = {
                    "business_name": "Forbidden Local Business",
                    "description": "",
                    "currency": "NIO",
                    "status_public_id": str(self.inactive.public_id),
                }
                if initial_owner_email is not None:
                    payload["initial_owner_email"] = initial_owner_email
                response = self.client.post(
                    "/api/businesses/",
                    payload,
                    format="json",
                )
                self.assertEqual(
                    response.status_code,
                    status.HTTP_403_FORBIDDEN,
                )
                forbidden_responses.append(response.data)
            audit.assert_not_called()

        self.assertTrue(
            all(
                response_data == forbidden_responses[0]
                for response_data in forbidden_responses[1:]
            )
        )
        self.assertEqual(
            {
                "businesses": Business.objects.count(),
                "memberships": BusinessMembership.objects.count(),
                "users": User.objects.count(),
                "employees": Employee.objects.count(),
            },
            initial_counts,
        )

        self.authenticate(self.platform)
        create_response = self.client.post(
            "/api/businesses/",
            {
                "business_name": "Rejected Status Business",
                "description": "",
                "currency": "NIO",
                "status_public_id": str(self.inactive.public_id),
                "initial_owner_email": self.owner.email,
            },
            format="json",
        )
        self.assertEqual(create_response.status_code, status.HTTP_400_BAD_REQUEST)
        update_response = self.client.patch(
            self.detail_url,
            {"status_public_id": str(self.inactive.public_id)},
            format="json",
        )
        self.assertEqual(update_response.status_code, status.HTTP_400_BAD_REQUEST)

        created = self.client.post(
            "/api/businesses/",
            {
                "business_name": "Always Active Business",
                "description": "",
                "currency": "NIO",
                "initial_owner_email": self.owner.email,
            },
            format="json",
        )
        self.assertEqual(created.status_code, status.HTTP_201_CREATED)
        self.assertEqual(created.data["status_name"], "Activo")

    def test_audit_failure_rolls_back_transition(self):
        self.authenticate(self.owner)
        with patch(
            "core.services.business_lifecycle.log_action",
            side_effect=RuntimeError("audit unavailable"),
        ):
            with self.assertRaises(RuntimeError):
                self.client.post(self.action_url("suspend"), {}, format="json")
        self.business.refresh_from_db()
        self.assertEqual(self.business.status, self.active)

    def test_openapi_documents_lifecycle_actions_without_artificial_503(self):
        schema = SchemaGenerator().get_schema(request=None, public=True)
        for action in ("suspend", "reactivate"):
            operation = schema["paths"][
                f"/api/businesses/{{public_id}}/{action}/"
            ]["post"]
            self.assertEqual(
                set(operation["responses"]),
                {"200", "400", "401", "403", "404", "409"},
            )


class BusinessLifecycleConcurrencyTests(TransactionTestCase):
    reset_sequences = True

    def setUp(self):
        self.active = create_status("Activo")
        self.inactive = create_status("Inactivo")
        self.deleted = create_status("Eliminado")
        self.platform = create_user(
            email="lifecycle-race-platform@playnow.test",
            is_superuser=True,
        )
        self.platform_two = create_user(
            email="lifecycle-race-platform-two@playnow.test",
            is_superuser=True,
        )
        self.platform_three = create_user(
            email="lifecycle-race-platform-three@playnow.test",
            is_superuser=True,
        )
        self.business = create_business(
            user=self.platform,
            status=self.active,
        )

    def test_suspend_reactivate_and_archive_serialize_on_postgresql(self):
        preauthorization_barrier = Barrier(3)
        original_preauthorize = lifecycle_service._preauthorize

        def coordinated_preauthorize(**kwargs):
            business_id = original_preauthorize(**kwargs)
            preauthorization_barrier.wait()
            return business_id

        def run(task):
            operation, actor_id = task
            close_old_connections()
            try:
                operation(
                    actor_id=actor_id,
                    business_public_id=self.business.public_id,
                )
                return "ok"
            except BusinessLifecycleError as exc:
                return f"{exc.kind}:{exc.detail}"
            finally:
                close_old_connections()

        with patch(
            "core.services.business_lifecycle._preauthorize",
            side_effect=coordinated_preauthorize,
        ):
            with ThreadPoolExecutor(max_workers=3) as executor:
                outcomes = list(
                    executor.map(
                        run,
                        (
                            (suspend_business, self.platform.pk),
                            (reactivate_business, self.platform_two.pk),
                            (archive_business, self.platform_three.pk),
                        ),
                    )
                )

        self.business.refresh_from_db()
        self.assertIn(outcomes[2], {"ok"}, msg=outcomes)
        self.assertEqual(self.business.status, self.deleted, msg=outcomes)
        self.assertTrue(
            all(
                outcome == "ok"
                or outcome.startswith("conflict:")
                or outcome.startswith("not_found:")
                for outcome in outcomes
            ),
            msg=outcomes,
        )
