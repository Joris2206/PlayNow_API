from datetime import date, datetime, timezone
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import patch
from uuid import uuid4

from django.db import IntegrityError
from rest_framework import status

from core.models import (
    BusinessMembership,
    CashMovement,
    CashRegister,
    CommissionSettlement,
    PaymentMethod,
)
from core.tests.base import BusinessIsolationTestCase
from core.tests.factories import (
    create_cash_movement,
    create_cash_register,
    create_commission_plan,
    create_commission_settlement,
    create_employee,
    create_membership,
    create_payment_method,
    create_role_user,
    create_status,
    create_transaction,
    create_user,
)
from core.tests.helpers import get_response_results


class CommissionPlanHardeningTests(BusinessIsolationTestCase):
    @classmethod
    def setUpTestData(cls):
        super().setUpTestData()
        cls.inactive_status = create_status("Inactivo")
        cls.employee = create_employee(
            business=cls.business_a,
            status=cls.active_status,
        )
        cls.other_employee = create_employee(
            business=cls.business_a,
            status=cls.active_status,
        )
        cls.inactive_employee = create_employee(
            business=cls.business_a,
            status=cls.inactive_status,
        )

    def _create(self, **overrides):
        payload = {
            "business_public_id": str(self.business_a.public_id),
            "employee_public_id": str(self.employee.public_id),
            "percentage": "5.00",
            "valid_from": "2026-08-01",
            "valid_until": "2026-08-15",
            "is_active": True,
        }
        payload.update(overrides)
        return self.client.post(
            "/api/commission-plans/",
            payload,
            format="json",
        )

    def test_valid_create_and_self_update(self):
        response = self._create()
        self.assertEqual(response.status_code, status.HTTP_201_CREATED)

        response = self.client.patch(
            f"/api/commission-plans/{response.data['public_id']}/",
            {"percentage": "7.50"},
            format="json",
        )
        self.assertEqual(response.status_code, status.HTTP_200_OK)

    def test_inactive_employee_and_invalid_dates_are_rejected(self):
        response = self._create(
            employee_public_id=str(self.inactive_employee.public_id),
        )
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn("employee_public_id", response.data)

        response = self._create(
            valid_from="2026-08-16",
            valid_until="2026-08-15",
        )
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn("valid_until", response.data)

    def test_active_overlap_boundaries_and_open_range(self):
        create_commission_plan(
            employee=self.employee,
            valid_from=date(2026, 8, 1),
            valid_until=date(2026, 8, 15),
        )

        shared = self._create(
            valid_from="2026-08-15",
            valid_until="2026-08-31",
        )
        self.assertEqual(shared.status_code, status.HTTP_400_BAD_REQUEST)

        consecutive = self._create(
            valid_from="2026-08-16",
            valid_until="2026-08-31",
        )
        self.assertEqual(consecutive.status_code, status.HTTP_201_CREATED)

        open_ended = self._create(
            employee_public_id=str(self.other_employee.public_id),
            valid_from="2026-08-01",
            valid_until=None,
        )
        self.assertEqual(open_ended.status_code, status.HTTP_201_CREATED)
        overlap = self._create(
            employee_public_id=str(self.other_employee.public_id),
            valid_from="2026-09-01",
            valid_until="2026-09-30",
        )
        self.assertEqual(overlap.status_code, status.HTTP_400_BAD_REQUEST)

    def test_inactive_overlap_allowed_but_reactivation_rejected(self):
        create_commission_plan(
            employee=self.employee,
            valid_from=date(2026, 8, 1),
            valid_until=date(2026, 8, 31),
        )
        response = self._create(is_active=False)
        self.assertEqual(response.status_code, status.HTTP_201_CREATED)

        response = self.client.patch(
            f"/api/commission-plans/{response.data['public_id']}/",
            {"is_active": True},
            format="json",
        )
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

    def test_employee_change_validates_target_overlap(self):
        plan = create_commission_plan(
            employee=self.employee,
            valid_from=date(2026, 8, 1),
            valid_until=date(2026, 8, 31),
        )
        create_commission_plan(
            employee=self.other_employee,
            valid_from=date(2026, 8, 1),
            valid_until=date(2026, 8, 31),
        )
        response = self.client.patch(
            f"/api/commission-plans/{plan.public_id}/",
            {"employee_public_id": str(self.other_employee.public_id)},
            format="json",
        )
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

    def test_full_put_is_valid_and_revalidates_employee_and_overlap(self):
        plan = create_commission_plan(
            employee=self.employee,
            valid_from=date(2026, 8, 1),
            valid_until=date(2026, 8, 31),
        )
        endpoint = f"/api/commission-plans/{plan.public_id}/"
        payload = {
            "employee_public_id": str(self.employee.public_id),
            "percentage": "8.25",
            "valid_from": "2026-09-01",
            "valid_until": "2026-09-30",
            "is_active": True,
        }
        response = self.client.put(endpoint, payload, format="json")
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data["percentage"], "8.25")

        create_commission_plan(
            employee=self.other_employee,
            valid_from=date(2026, 9, 1),
            valid_until=date(2026, 9, 30),
        )
        overlap = self.client.put(
            endpoint,
            {**payload, "employee_public_id": str(self.other_employee.public_id)},
            format="json",
        )
        self.assertEqual(overlap.status_code, status.HTTP_400_BAD_REQUEST)

        self.employee.status = self.inactive_status
        self.employee.save(update_fields=["status", "updated_at"])
        inactive = self.client.put(endpoint, payload, format="json")
        self.assertEqual(inactive.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn("employee_public_id", inactive.data)


class CommissionPlanPermissionTests(BusinessIsolationTestCase):
    @classmethod
    def setUpTestData(cls):
        super().setUpTestData()
        cls.employee = create_employee(
            business=cls.business_a,
            status=cls.active_status,
        )
        cls.plan = create_commission_plan(
            employee=cls.employee,
            valid_from=date(2026, 8, 1),
        )
        cls.admin_user, _, _ = create_role_user(
            business=cls.business_a,
            role=BusinessMembership.ROLE_ADMIN,
            status=cls.active_status,
        )
        cls.superuser = create_user(
            email="commission-superuser@playnow.test",
            is_superuser=True,
        )
        cls.restricted_users = []
        for role in (
            BusinessMembership.ROLE_CASHIER,
            BusinessMembership.ROLE_SELLER,
            BusinessMembership.ROLE_INVENTORY,
            BusinessMembership.ROLE_VIEWER,
        ):
            user, _, _ = create_role_user(
                business=cls.business_a,
                role=role,
                status=cls.active_status,
            )
            cls.restricted_users.append(user)

    def test_owner_can_list_retrieve_and_write(self):
        listing = self.client.get(
            "/api/commission-plans/",
            {"business_public_id": str(self.business_a.public_id)},
        )
        retrieve = self.client.get(
            f"/api/commission-plans/{self.plan.public_id}/",
        )
        update = self.client.patch(
            f"/api/commission-plans/{self.plan.public_id}/",
            {"percentage": "6.00"},
            format="json",
        )
        self.assertEqual(listing.status_code, status.HTTP_200_OK)
        self.assertEqual(retrieve.status_code, status.HTTP_200_OK)
        self.assertEqual(update.status_code, status.HTTP_200_OK)
        deletion = self.client.delete(
            f"/api/commission-plans/{self.plan.public_id}/",
        )
        self.assertEqual(deletion.status_code, status.HTTP_204_NO_CONTENT)

    def test_restricted_roles_cannot_administer_plans(self):
        for user in self.restricted_users:
            with self.subTest(user=user.email):
                self.authenticate_as(user)
                listing = self.client.get(
                    "/api/commission-plans/",
                    {"business_public_id": str(self.business_a.public_id)},
                )
                retrieve = self.client.get(
                    f"/api/commission-plans/{self.plan.public_id}/",
                )
                update = self.client.patch(
                    f"/api/commission-plans/{self.plan.public_id}/",
                    {"percentage": "9.00"},
                    format="json",
                )
                deletion = self.client.delete(
                    f"/api/commission-plans/{self.plan.public_id}/",
                )
                creation = self.client.post(
                    "/api/commission-plans/",
                    {
                        "business_public_id": str(self.business_a.public_id),
                        "employee_public_id": str(self.employee.public_id),
                        "percentage": "5.00",
                        "valid_from": "2027-01-01",
                        "is_active": False,
                    },
                    format="json",
                )
                self.assertEqual(listing.status_code, status.HTTP_403_FORBIDDEN)
                self.assertEqual(retrieve.status_code, status.HTTP_404_NOT_FOUND)
                self.assertEqual(update.status_code, status.HTTP_404_NOT_FOUND)
                self.assertEqual(deletion.status_code, status.HTTP_404_NOT_FOUND)
                self.assertEqual(creation.status_code, status.HTTP_403_FORBIDDEN)

    def test_admin_can_list_retrieve_and_create(self):
        self.authenticate_as(self.admin_user)
        listing = self.client.get(
            "/api/commission-plans/",
            {"business_public_id": str(self.business_a.public_id)},
        )
        retrieve = self.client.get(
            f"/api/commission-plans/{self.plan.public_id}/",
        )
        creation = self.client.post(
            "/api/commission-plans/",
            {
                "business_public_id": str(self.business_a.public_id),
                "employee_public_id": str(self.employee.public_id),
                "percentage": "5.00",
                "valid_from": "2027-01-01",
                "is_active": False,
            },
            format="json",
        )
        self.assertEqual(listing.status_code, status.HTTP_200_OK)
        self.assertEqual(retrieve.status_code, status.HTTP_200_OK)
        self.assertEqual(creation.status_code, status.HTTP_201_CREATED)

    def test_superuser_keeps_global_plan_access(self):
        self.authenticate_as(self.superuser)
        listing = self.client.get(
            "/api/commission-plans/",
            {"business_public_id": str(self.business_a.public_id)},
        )
        retrieve = self.client.get(
            f"/api/commission-plans/{self.plan.public_id}/",
        )
        self.assertEqual(listing.status_code, status.HTTP_200_OK)
        self.assertEqual(retrieve.status_code, status.HTTP_200_OK)

    def test_foreign_plan_is_not_visible(self):
        foreign_employee = create_employee(
            business=self.business_b,
            status=self.active_status,
        )
        foreign_plan = create_commission_plan(
            employee=foreign_employee,
            valid_from=date(2026, 8, 1),
        )
        response = self.client.get(
            f"/api/commission-plans/{foreign_plan.public_id}/",
        )
        self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND)

    def test_create_authorization_precedes_all_payload_validation(self):
        foreign_employee = create_employee(
            business=self.business_b,
            status=self.active_status,
        )
        create_commission_plan(
            employee=self.employee,
            valid_from=date(2026, 8, 1),
            valid_until=date(2026, 8, 31),
        )
        no_membership = create_user(email="plan-early-none@playnow.test")
        inactive_user, _, inactive_membership = create_role_user(
            business=self.business_a,
            role=BusinessMembership.ROLE_ADMIN,
            status=self.active_status,
        )
        inactive_membership.is_active = False
        inactive_membership.save(update_fields=["is_active", "updated_at"])
        users = [*self.restricted_users, no_membership, inactive_user]
        invalid_payloads = (
            {},
            {
                "business_public_id": str(self.business_a.public_id),
                "employee_public_id": "not-a-uuid",
                "percentage": "invalid",
                "valid_from": "invalid",
            },
            {
                "business_public_id": str(self.business_a.public_id),
                "employee_public_id": str(uuid4()),
                "percentage": "999.00",
                "valid_from": "2026-99-99",
            },
            {
                "business_public_id": str(self.business_a.public_id),
                "employee_public_id": str(foreign_employee.public_id),
                "percentage": "5.00",
                "valid_from": "2026-08-01",
                "is_active": True,
            },
            {
                "business_public_id": str(self.business_a.public_id),
                "employee_public_id": str(self.employee.public_id),
                "percentage": "5.00",
                "valid_from": "2026-08-15",
                "valid_until": "2026-09-01",
                "is_active": True,
            },
            {
                "business_public_id": str(self.business_a.public_id),
                "employee_public_id": str(self.employee.public_id),
                "percentage": "5.00",
                "valid_from": "2027-01-01",
                "is_active": False,
            },
        )
        for user in users:
            self.authenticate_as(user)
            for payload in invalid_payloads:
                with self.subTest(user=user.email, payload=payload):
                    response = self.client.post(
                        "/api/commission-plans/", payload, format="json"
                    )
                    self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)

    def test_create_authorization_is_for_the_requested_business(self):
        create_membership(
            user=self.user_a,
            business=self.business_b,
            role=BusinessMembership.ROLE_CASHIER,
        )
        foreign_employee = create_employee(
            business=self.business_b,
            status=self.active_status,
        )
        response = self.client.post(
            "/api/commission-plans/",
            {
                "business_public_id": str(self.business_b.public_id),
                "employee_public_id": str(foreign_employee.public_id),
                "percentage": "5.00",
                "valid_from": "2026-08-01",
                "is_active": True,
            },
            format="json",
        )
        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)

    def test_authorized_create_scopes_employee_relation_to_business(self):
        foreign_employee = create_employee(
            business=self.business_b,
            status=self.active_status,
        )
        base = {
            "business_public_id": str(self.business_a.public_id),
            "percentage": "5.00",
            "valid_from": "2026-08-01",
            "is_active": True,
        }
        foreign = self.client.post(
            "/api/commission-plans/",
            {**base, "employee_public_id": str(foreign_employee.public_id)},
            format="json",
        )
        missing = self.client.post(
            "/api/commission-plans/",
            {**base, "employee_public_id": str(uuid4())},
            format="json",
        )
        self.assertEqual(foreign.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertEqual(missing.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertEqual(
            foreign.data["employee_public_id"][0].code,
            missing.data["employee_public_id"][0].code,
        )


class CommissionPeriodHardeningTests(BusinessIsolationTestCase):
    @classmethod
    def setUpTestData(cls):
        super().setUpTestData()
        cls.inactive_status = create_status("Inactivo")
        cls.employee = create_employee(
            business=cls.business_a,
            status=cls.active_status,
        )
        cls.other_employee = create_employee(
            business=cls.business_a,
            status=cls.active_status,
        )
        cls.inactive_employee = create_employee(
            business=cls.business_a,
            status=cls.inactive_status,
        )

    def _preview(self, employee=None, start="2026-08-01", end="2026-08-31"):
        return self.client.get(
            "/api/reports/employee-commission/",
            {
                "business_public_id": str(self.business_a.public_id),
                "employee_public_id": str((employee or self.employee).public_id),
                "date_from": start,
                "date_to": end,
            },
        )

    def _create(self, employee=None, start="2026-08-01", end="2026-08-31"):
        return self.client.post(
            "/api/commission-settlements/",
            {
                "business_public_id": str(self.business_a.public_id),
                "employee_public_id": str((employee or self.employee).public_id),
                "period_start": start,
                "period_end": end,
            },
            format="json",
        )

    def test_inverted_dates_and_inactive_employee_are_rejected(self):
        self.assertEqual(
            self._preview(start="2026-08-31", end="2026-08-01").status_code,
            status.HTTP_400_BAD_REQUEST,
        )
        self.assertEqual(
            self._create(start="2026-08-31", end="2026-08-01").status_code,
            status.HTTP_400_BAD_REQUEST,
        )
        self.assertEqual(
            self._preview(employee=self.inactive_employee).status_code,
            status.HTTP_400_BAD_REQUEST,
        )
        self.assertEqual(
            self._create(employee=self.inactive_employee).status_code,
            status.HTTP_400_BAD_REQUEST,
        )

    def test_foreign_employee_is_not_exposed_by_preview(self):
        foreign_employee = create_employee(
            business=self.business_b,
            status=self.active_status,
        )
        response = self._preview(employee=foreign_employee)
        self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND)

    def test_no_plan_partial_plan_and_crossing_plans_are_rejected(self):
        self.assertEqual(self._preview().status_code, status.HTTP_400_BAD_REQUEST)

        partial = create_commission_plan(
            employee=self.employee,
            valid_from=date(2026, 8, 5),
            valid_until=date(2026, 8, 31),
        )
        self.assertEqual(self._create().status_code, status.HTTP_400_BAD_REQUEST)
        partial.delete()

        create_commission_plan(
            employee=self.employee,
            valid_from=date(2026, 8, 1),
            valid_until=date(2026, 8, 15),
        )
        create_commission_plan(
            employee=self.employee,
            valid_from=date(2026, 8, 16),
            valid_until=date(2026, 8, 31),
        )
        self.assertEqual(self._preview().status_code, status.HTTP_400_BAD_REQUEST)
        self.assertEqual(self._create().status_code, status.HTTP_400_BAD_REQUEST)

    def test_multiple_covering_plans_are_rejected(self):
        create_commission_plan(
            employee=self.employee,
            valid_from=date(2026, 8, 1),
            valid_until=date(2026, 8, 31),
        )
        create_commission_plan(
            employee=self.employee,
            valid_from=date(2026, 7, 1),
            valid_until=None,
        )
        self.assertEqual(self._preview().status_code, status.HTTP_400_BAD_REQUEST)
        self.assertEqual(self._create().status_code, status.HTTP_400_BAD_REQUEST)

    def test_all_settlement_statuses_block_preview_and_create(self):
        create_commission_plan(
            employee=self.employee,
            valid_from=date(2026, 8, 1),
        )
        for settlement_status in (
            CommissionSettlement.STATUS_PENDING,
            CommissionSettlement.STATUS_PAID,
            CommissionSettlement.STATUS_CANCELLED,
        ):
            with self.subTest(status=settlement_status):
                settlement = create_commission_settlement(
                    employee=self.employee,
                    created_by=self.user_a,
                    period_start=date(2026, 8, 10),
                    period_end=date(2026, 8, 20),
                    settlement_status=settlement_status,
                )
                self.assertEqual(self._preview().status_code, status.HTTP_400_BAD_REQUEST)
                self.assertEqual(self._create().status_code, status.HTTP_400_BAD_REQUEST)
                settlement.delete()

    def test_settlement_overlap_edges_and_other_employee(self):
        create_commission_plan(
            employee=self.employee,
            valid_from=date(2026, 8, 1),
        )
        create_commission_plan(
            employee=self.other_employee,
            valid_from=date(2026, 8, 1),
        )
        create_commission_settlement(
            employee=self.employee,
            created_by=self.user_a,
            period_start=date(2026, 8, 1),
            period_end=date(2026, 8, 15),
        )
        self.assertEqual(
            self._create(start="2026-08-15", end="2026-08-31").status_code,
            status.HTTP_400_BAD_REQUEST,
        )
        self.assertEqual(
            self._create(start="2026-08-16", end="2026-08-31").status_code,
            status.HTTP_201_CREATED,
        )
        self.assertEqual(
            self._create(employee=self.other_employee).status_code,
            status.HTTP_201_CREATED,
        )

    def test_cancelled_cannot_be_marked_paid(self):
        settlement = create_commission_settlement(
            employee=self.employee,
            created_by=self.user_a,
            period_start=date(2026, 8, 1),
            period_end=date(2026, 8, 31),
            settlement_status=CommissionSettlement.STATUS_CANCELLED,
        )
        response = self.client.post(
            f"/api/commission-settlements/{settlement.public_id}/mark-paid/",
            {},
            format="json",
        )
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

    def test_mark_paid_preserves_snapshot_and_does_not_touch_cash(self):
        settlement = create_commission_settlement(
            employee=self.employee,
            created_by=self.user_a,
            period_start=date(2026, 8, 1),
            period_end=date(2026, 8, 31),
            sales_total=Decimal("1234.00"),
            commission_total=Decimal("61.70"),
        )
        cash_movements = CashMovement.objects.count()
        cash_registers = CashRegister.objects.count()

        response = self.client.post(
            f"/api/commission-settlements/{settlement.public_id}/mark-paid/",
            {},
            format="json",
        )
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data["sales_total"], "1234.00")
        self.assertEqual(response.data["commission_total"], "61.70")
        self.assertEqual(CashMovement.objects.count(), cash_movements)
        self.assertEqual(CashRegister.objects.count(), cash_registers)

    def test_foreign_settlement_cannot_be_marked_paid(self):
        foreign_employee = create_employee(
            business=self.business_b,
            status=self.active_status,
        )
        settlement = create_commission_settlement(
            employee=foreign_employee,
            created_by=self.user_b,
            period_start=date(2026, 8, 1),
            period_end=date(2026, 8, 31),
        )
        response = self.client.post(
            f"/api/commission-settlements/{settlement.public_id}/mark-paid/",
            {},
            format="json",
        )
        self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND)

    def test_preview_create_parity_and_snapshot_immutability(self):
        plan = create_commission_plan(
            employee=self.employee,
            percentage=Decimal("7.50"),
            valid_from=date(2026, 8, 1),
        )
        sale = create_transaction(
            business=self.business_a,
            created_by=self.user_a,
            employee=self.employee,
            status=self.active_status,
            total_value=Decimal("1200.00"),
            created_at=datetime(2026, 8, 31, 23, 59, tzinfo=timezone.utc),
        )
        preview = self._preview()
        created = self._create()
        self.assertEqual(preview.status_code, status.HTTP_200_OK)
        self.assertEqual(created.status_code, status.HTTP_201_CREATED)

        for field in (
            "sales_count",
            "sales_total",
            "commission_percentage",
            "commission_total",
            "employee_advances",
            "employee_repayments",
            "advance_balance",
            "net_commission_payable",
            "remaining_advance_balance",
        ):
            self.assertEqual(preview.data[field], created.data[field])

        plan.percentage = Decimal("50.00")
        plan.is_active = False
        plan.save(update_fields=["percentage", "is_active", "updated_at"])
        plan.delete()
        sale.total_value = Decimal("9999.00")
        sale.status = self.void_status
        sale.save(update_fields=["total_value", "status", "updated_at"])

        detail = self.client.get(
            f"/api/commission-settlements/{created.data['public_id']}/",
        )
        self.assertEqual(detail.status_code, status.HTTP_200_OK)
        self.assertEqual(detail.data["sales_total"], "1200.00")
        self.assertEqual(detail.data["commission_percentage"], "7.50")
        self.assertEqual(detail.data["commission_total"], "90.00")

    def test_sales_policy_uses_total_value_and_excludes_terminal_statuses(self):
        create_commission_plan(
            employee=self.employee,
            percentage=Decimal("10.00"),
            valid_from=date(2026, 8, 1),
        )
        created_at = datetime(2026, 8, 10, 12, 0, tzinfo=timezone.utc)
        create_transaction(
            business=self.business_a,
            created_by=self.user_a,
            employee=self.employee,
            status=self.active_status,
            total_value=Decimal("100.00"),
            created_at=created_at,
        )
        create_transaction(
            business=self.business_a,
            created_by=self.user_a,
            employee=self.employee,
            status=self.active_status,
            total_value=Decimal("200.00"),
            is_debt=True,
            created_at=created_at,
        )
        partial = create_transaction(
            business=self.business_a,
            created_by=self.user_a,
            employee=self.employee,
            status=self.active_status,
            total_value=Decimal("300.00"),
            is_debt=True,
            created_at=created_at,
        )
        partial.payment_status = "partial"
        partial.save(update_fields=["payment_status", "updated_at"])
        create_transaction(
            business=self.business_a,
            created_by=self.user_a,
            employee=self.employee,
            status=self.void_status,
            total_value=Decimal("5000.00"),
            created_at=created_at,
        )

        response = self._create()
        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        self.assertEqual(response.data["sales_count"], 3)
        self.assertEqual(response.data["sales_total"], "600.00")
        self.assertEqual(response.data["commission_total"], "60.00")

    def test_deleted_employee_rejects_new_operations_but_keeps_history_readable(self):
        deleted_status = create_status("Eliminado")
        plan = create_commission_plan(
            employee=self.employee,
            valid_from=date(2026, 8, 1),
        )
        settlement = create_commission_settlement(
            employee=self.employee,
            created_by=self.user_a,
            period_start=date(2026, 7, 1),
            period_end=date(2026, 7, 31),
        )
        self.employee.status = deleted_status
        self.employee.save(update_fields=["status", "updated_at"])

        plan_create = self.client.post(
            "/api/commission-plans/",
            {
                "business_public_id": str(self.business_a.public_id),
                "employee_public_id": str(self.employee.public_id),
                "percentage": "5.00",
                "valid_from": "2027-01-01",
                "is_active": False,
            },
            format="json",
        )
        self.assertEqual(plan_create.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertEqual(self._preview().status_code, status.HTTP_400_BAD_REQUEST)
        self.assertEqual(self._create().status_code, status.HTTP_400_BAD_REQUEST)
        self.assertEqual(
            self.client.get(f"/api/commission-plans/{plan.public_id}/").status_code,
            status.HTTP_200_OK,
        )
        self.assertEqual(
            self.client.get(
                f"/api/commission-settlements/{settlement.public_id}/"
            ).status_code,
            status.HTTP_200_OK,
        )

    def test_settlement_put_is_not_allowed(self):
        settlement = create_commission_settlement(
            employee=self.employee,
            created_by=self.user_a,
            period_start=date(2026, 8, 1),
            period_end=date(2026, 8, 31),
        )
        response = self.client.put(
            f"/api/commission-settlements/{settlement.public_id}/",
            {},
            format="json",
        )
        self.assertEqual(response.status_code, status.HTTP_405_METHOD_NOT_ALLOWED)

    def test_cash_movement_period_boundaries_are_inclusive_in_preview_and_snapshot(self):
        create_commission_plan(
            employee=self.employee,
            percentage=Decimal("10.00"),
            valid_from=date(2026, 8, 1),
        )
        payment_method = create_payment_method(
            business=self.business_a,
            status=self.active_status,
            method_type=PaymentMethod.TYPE_CASH,
        )
        register = create_cash_register(
            business=self.business_a,
            employee=self.employee,
            opened_by=self.user_a,
        )
        movements = (
            (CashMovement.TYPE_EMPLOYEE_ADVANCE, "100.00", datetime(2026, 8, 1, 0, 0, tzinfo=timezone.utc)),
            (CashMovement.TYPE_EMPLOYEE_ADVANCE, "40.00", datetime(2026, 8, 31, 23, 59, tzinfo=timezone.utc)),
            (CashMovement.TYPE_EMPLOYEE_REPAYMENT, "25.00", datetime(2026, 8, 1, 0, 0, tzinfo=timezone.utc)),
            (CashMovement.TYPE_EMPLOYEE_REPAYMENT, "15.00", datetime(2026, 8, 31, 23, 59, tzinfo=timezone.utc)),
            (CashMovement.TYPE_EMPLOYEE_ADVANCE, "900.00", datetime(2026, 7, 31, 23, 59, tzinfo=timezone.utc)),
            (CashMovement.TYPE_EMPLOYEE_REPAYMENT, "800.00", datetime(2026, 9, 1, 0, 0, tzinfo=timezone.utc)),
        )
        for movement_type, amount, created_at in movements:
            movement = create_cash_movement(
                cash_register=register,
                created_by=self.user_a,
                employee=self.employee,
                payment_method=payment_method,
                movement_type=movement_type,
                amount=Decimal(amount),
            )
            CashMovement.objects.filter(pk=movement.pk).update(created_at=created_at)

        preview = self._preview()
        created = self._create()
        self.assertEqual(preview.status_code, status.HTTP_200_OK)
        self.assertEqual(created.status_code, status.HTTP_201_CREATED)
        for response in (preview, created):
            self.assertEqual(response.data["employee_advances"], "140.00")
            self.assertEqual(response.data["employee_repayments"], "40.00")
            self.assertEqual(response.data["advance_balance"], "100.00")

    def test_unrelated_integrity_error_during_settlement_create_is_reraised(self):
        create_commission_plan(
            employee=self.employee,
            valid_from=date(2026, 8, 1),
        )
        error = IntegrityError("unrelated constraint")
        cause = Exception("database error")
        cause.diag = SimpleNamespace(constraint_name="future_unrelated_constraint")
        error.__cause__ = cause
        with patch(
            "core.views.CommissionSettlement.objects.create",
            side_effect=error,
        ):
            with self.assertRaises(IntegrityError):
                self._create()

    def test_exact_settlement_unique_constraint_is_mapped_to_domain_error(self):
        create_commission_plan(
            employee=self.employee,
            valid_from=date(2026, 8, 1),
        )
        error = IntegrityError("duplicate settlement")
        cause = Exception("unique violation")
        cause.diag = SimpleNamespace(
            constraint_name="unique_employee_commission_settlement_per_period"
        )
        error.__cause__ = cause
        with patch(
            "core.views.CommissionSettlement.objects.create",
            side_effect=error,
        ):
            response = self._create()
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn("period", response.data)


class CommissionRoleMatrixTests(BusinessIsolationTestCase):
    def test_unauthorized_settlement_create_precedes_payload_validation(self):
        employee = create_employee(
            business=self.business_a,
            status=self.active_status,
        )
        foreign_employee = create_employee(
            business=self.business_b,
            status=self.active_status,
        )
        create_commission_plan(employee=employee, valid_from=date(2026, 1, 1))
        create_commission_settlement(
            employee=employee,
            created_by=self.user_a,
            period_start=date(2026, 8, 1),
            period_end=date(2026, 8, 31),
        )
        users = []
        for role in (
            BusinessMembership.ROLE_CASHIER,
            BusinessMembership.ROLE_SELLER,
            BusinessMembership.ROLE_INVENTORY,
            BusinessMembership.ROLE_VIEWER,
        ):
            user, _, _ = create_role_user(
                business=self.business_a,
                role=role,
                status=self.active_status,
            )
            users.append(user)
        users.append(create_user(email="early-none@playnow.test"))
        inactive_user, _, membership = create_role_user(
            business=self.business_a,
            role=BusinessMembership.ROLE_ADMIN,
            status=self.active_status,
        )
        membership.is_active = False
        membership.save(update_fields=["is_active", "updated_at"])
        users.append(inactive_user)

        payloads = (
            {},
            {
                "business_public_id": str(self.business_a.public_id),
                "employee_public_id": "not-a-uuid",
                "period_start": "invalid",
                "period_end": "invalid",
            },
            {
                "business_public_id": str(self.business_a.public_id),
                "employee_public_id": str(uuid4()),
                "period_start": "2026-08-31",
                "period_end": "2026-08-01",
            },
            {
                "business_public_id": str(self.business_a.public_id),
                "employee_public_id": str(foreign_employee.public_id),
                "period_start": "2026-09-01",
                "period_end": "2026-09-30",
            },
            {
                "business_public_id": str(self.business_a.public_id),
                "employee_public_id": str(employee.public_id),
                "period_start": "2026-08-15",
                "period_end": "2026-09-15",
            },
            {
                "business_public_id": str(self.business_a.public_id),
                "employee_public_id": str(employee.public_id),
                "period_start": "2027-01-01",
                "period_end": "2027-01-31",
            },
        )
        for user in users:
            self.authenticate_as(user)
            for payload in payloads:
                with self.subTest(user=user.email, payload=payload):
                    response = self.client.post(
                        "/api/commission-settlements/", payload, format="json"
                    )
                    self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)

    def test_settlement_create_authorization_is_for_requested_business(self):
        create_membership(
            user=self.user_a,
            business=self.business_b,
            role=BusinessMembership.ROLE_CASHIER,
        )
        foreign_employee = create_employee(
            business=self.business_b,
            status=self.active_status,
        )
        response = self.client.post(
            "/api/commission-settlements/",
            {
                "business_public_id": str(self.business_b.public_id),
                "employee_public_id": str(foreign_employee.public_id),
                "period_start": "2026-08-01",
                "period_end": "2026-08-31",
            },
            format="json",
        )
        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)

    def test_preview_and_settlement_role_matrix(self):
        actors = [
            ("owner", self.user_a, True),
        ]
        for role in (
            BusinessMembership.ROLE_ADMIN,
            BusinessMembership.ROLE_CASHIER,
            BusinessMembership.ROLE_SELLER,
            BusinessMembership.ROLE_INVENTORY,
            BusinessMembership.ROLE_VIEWER,
        ):
            user, _, _ = create_role_user(
                business=self.business_a,
                role=role,
                status=self.active_status,
            )
            actors.append((role, user, role == BusinessMembership.ROLE_ADMIN))
        superuser = create_user(email="matrix-superuser@playnow.test", is_superuser=True)
        actors.append(("superuser", superuser, True))
        actors.append(("no_membership", create_user(email="matrix-none@playnow.test"), False))
        inactive_user, _, inactive_membership = create_role_user(
            business=self.business_a,
            role=BusinessMembership.ROLE_ADMIN,
            status=self.active_status,
        )
        inactive_membership.is_active = False
        inactive_membership.save(update_fields=["is_active", "updated_at"])
        actors.append(("inactive_membership", inactive_user, False))

        for label, user, allowed in actors:
            employee = create_employee(
                business=self.business_a,
                status=self.active_status,
                full_name=f"Matrix {label}",
            )
            create_commission_plan(
                employee=employee,
                valid_from=date(2025, 1, 1),
            )
            historical = create_commission_settlement(
                employee=employee,
                created_by=self.user_a,
                period_start=date(2025, 1, 1),
                period_end=date(2025, 1, 31),
            )
            self.authenticate_as(user)
            with self.subTest(role=label):
                preview = self.client.get(
                    "/api/reports/employee-commission/",
                    {
                        "business_public_id": str(self.business_a.public_id),
                        "employee_public_id": str(employee.public_id),
                        "date_from": "2027-01-01",
                        "date_to": "2027-01-31",
                    },
                )
                listing = self.client.get(
                    "/api/commission-settlements/",
                    {"business_public_id": str(self.business_a.public_id)},
                )
                retrieve = self.client.get(
                    f"/api/commission-settlements/{historical.public_id}/"
                )
                creation = self.client.post(
                    "/api/commission-settlements/",
                    {
                        "business_public_id": str(self.business_a.public_id),
                        "employee_public_id": str(employee.public_id),
                        "period_start": "2026-01-01",
                        "period_end": "2026-01-31",
                    },
                    format="json",
                )
                paid = self.client.post(
                    f"/api/commission-settlements/{historical.public_id}/mark-paid/",
                    {},
                    format="json",
                )
                self.assertEqual(preview.status_code, 200 if allowed else 403)
                self.assertEqual(listing.status_code, 200 if allowed else 403)
                self.assertEqual(retrieve.status_code, 200 if allowed else 404)
                self.assertEqual(creation.status_code, 201 if allowed else 403)
                self.assertEqual(paid.status_code, 200 if allowed else 404)


class CommissionFilterTests(BusinessIsolationTestCase):
    def test_plan_filters_ordering_pagination_and_business_isolation(self):
        employee = create_employee(business=self.business_a, status=self.active_status)
        other = create_employee(business=self.business_a, status=self.active_status)
        foreign = create_employee(business=self.business_b, status=self.active_status)
        older = create_commission_plan(employee=employee, valid_from=date(2026, 1, 1))
        newer = create_commission_plan(employee=employee, valid_from=date(2026, 2, 1), is_active=False)
        create_commission_plan(employee=other, valid_from=date(2026, 3, 1))
        foreign_plan = create_commission_plan(employee=foreign, valid_from=date(2026, 4, 1))
        base = {"business_public_id": str(self.business_a.public_id)}

        filtered = self.client.get(
            "/api/commission-plans/",
            {**base, "employee_public_id": str(employee.public_id), "is_active": "false", "ordering": "-valid_from"},
        )
        self.assertEqual(
            [item["public_id"] for item in get_response_results(filtered)],
            [str(newer.public_id)],
        )
        paged = self.client.get(
            "/api/commission-plans/",
            {**base, "ordering": "valid_from", "page_size": 1},
        )
        self.assertEqual(paged.status_code, status.HTTP_200_OK)
        self.assertEqual(paged.data["page_size"], 1)
        self.assertEqual(paged.data["count"], 3)
        self.assertEqual(paged.data["results"][0]["public_id"], str(older.public_id))
        isolated = self.client.get(
            "/api/commission-plans/",
            {**base, "employee_public_id": str(foreign.public_id)},
        )
        self.assertEqual(get_response_results(isolated), [])
        self.assertNotIn(str(foreign_plan.public_id), [item["public_id"] for item in get_response_results(paged)])

    def test_settlement_filters_and_ordering_are_functional_and_isolated(self):
        employee = create_employee(business=self.business_a, status=self.active_status)
        other = create_employee(business=self.business_a, status=self.active_status)
        foreign = create_employee(business=self.business_b, status=self.active_status)
        target = create_commission_settlement(
            employee=employee,
            created_by=self.user_a,
            period_start=date(2026, 2, 1),
            period_end=date(2026, 2, 28),
            settlement_status=CommissionSettlement.STATUS_PAID,
        )
        create_commission_settlement(
            employee=other,
            created_by=self.user_a,
            period_start=date(2026, 1, 1),
            period_end=date(2026, 1, 31),
        )
        foreign_settlement = create_commission_settlement(
            employee=foreign,
            created_by=self.user_b,
            period_start=date(2026, 3, 1),
            period_end=date(2026, 3, 31),
        )
        base = {"business_public_id": str(self.business_a.public_id)}
        response = self.client.get(
            "/api/commission-settlements/",
            {
                **base,
                "employee_public_id": str(employee.public_id),
                "status": CommissionSettlement.STATUS_PAID,
                "period_start": "2026-02-01",
                "period_end": "2026-02-28",
                "ordering": "period_start",
            },
        )
        self.assertEqual(
            [item["public_id"] for item in get_response_results(response)],
            [str(target.public_id)],
        )
        ordered = self.client.get(
            "/api/commission-settlements/",
            {**base, "ordering": "-period_start"},
        )
        ordered_ids = [item["public_id"] for item in get_response_results(ordered)]
        self.assertEqual(ordered_ids[0], str(target.public_id))
        self.assertNotIn(str(foreign_settlement.public_id), ordered_ids)
        isolated = self.client.get(
            "/api/commission-settlements/",
            {**base, "employee_public_id": str(foreign.public_id)},
        )
        self.assertEqual(get_response_results(isolated), [])
