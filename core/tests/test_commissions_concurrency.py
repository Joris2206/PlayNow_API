from concurrent.futures import ThreadPoolExecutor
from datetime import date
from decimal import Decimal
from threading import Barrier
from unittest.mock import patch

from django.db import close_old_connections, connection
from django.test import TransactionTestCase
from rest_framework.test import APIClient

from core.models import CommissionSettlement, EmployeeCommissionPlan
from core.services.commissions import lock_employees as real_lock_employees
from core.tests.factories import (
    create_business,
    create_commission_plan,
    create_commission_settlement,
    create_employee,
    create_status,
    create_user,
)
from core.views import (
    CommissionSettlementViewSet,
    EmployeeCommissionPlanViewSet,
)


class CommissionConcurrencyTests(TransactionTestCase):
    reset_sequences = True

    def setUp(self):
        if connection.vendor != "postgresql":
            self.skipTest("Los locks de concurrencia requieren PostgreSQL.")

        self.active_status = create_status("Activo")
        self.owner = create_user(email="commission-lock-owner@playnow.test")
        self.business = create_business(
            user=self.owner,
            status=self.active_status,
        )
        self.employee = create_employee(
            business=self.business,
            status=self.active_status,
        )

    def _concurrent_requests(self, method, endpoint, payloads):
        barrier = Barrier(len(payloads))

        def submit(payload):
            close_old_connections()
            client = APIClient()
            client.force_authenticate(user=self.owner)
            barrier.wait(timeout=10)
            try:
                request = getattr(client, method)
                return request(endpoint, payload, format="json").status_code
            finally:
                close_old_connections()

        with ThreadPoolExecutor(max_workers=len(payloads)) as executor:
            return sorted(executor.map(submit, payloads))

    def test_overlapping_plans_are_serialized_by_employee_lock(self):
        lock_barrier = Barrier(2)

        def synchronized_lock(*employees):
            lock_barrier.wait(timeout=20)
            return real_lock_employees(*employees)

        with patch("core.views.lock_employees", side_effect=synchronized_lock):
            statuses = self._concurrent_requests(
                "post",
                "/api/commission-plans/",
                [
                    {
                        "business_public_id": str(self.business.public_id),
                        "employee_public_id": str(self.employee.public_id),
                        "percentage": "5.00",
                        "valid_from": "2026-08-01",
                        "valid_until": "2026-08-20",
                        "is_active": True,
                    },
                    {
                        "business_public_id": str(self.business.public_id),
                        "employee_public_id": str(self.employee.public_id),
                        "percentage": "7.00",
                        "valid_from": "2026-08-15",
                        "valid_until": "2026-08-31",
                        "is_active": True,
                    },
                ],
            )
        self.assertEqual(statuses, [201, 400])
        self.assertEqual(
            EmployeeCommissionPlan.objects.filter(employee=self.employee).count(),
            1,
        )

    def test_overlapping_settlements_are_serialized_by_employee_lock(self):
        create_commission_plan(
            employee=self.employee,
            valid_from=date(2026, 8, 1),
        )
        lock_barrier = Barrier(2)

        def synchronized_lock(*employees):
            lock_barrier.wait(timeout=20)
            return real_lock_employees(*employees)

        with patch("core.views.lock_employees", side_effect=synchronized_lock):
            statuses = self._concurrent_requests(
                "post",
                "/api/commission-settlements/",
                [
                    {
                        "business_public_id": str(self.business.public_id),
                        "employee_public_id": str(self.employee.public_id),
                        "period_start": "2026-08-01",
                        "period_end": "2026-08-20",
                    },
                    {
                        "business_public_id": str(self.business.public_id),
                        "employee_public_id": str(self.employee.public_id),
                        "period_start": "2026-08-15",
                        "period_end": "2026-08-31",
                    },
                ],
            )
        self.assertEqual(statuses, [201, 400])
        self.assertEqual(
            CommissionSettlement.objects.filter(employee=self.employee).count(),
            1,
        )

    def test_mark_paid_allows_only_one_concurrent_transition(self):
        settlement = create_commission_settlement(
            employee=self.employee,
            created_by=self.owner,
            period_start=date(2026, 8, 1),
            period_end=date(2026, 8, 31),
        )
        endpoint = (
            f"/api/commission-settlements/{settlement.public_id}/mark-paid/"
        )
        lock_barrier = Barrier(2)
        original = CommissionSettlementViewSet._get_locked_settlement

        def synchronized_get(view, settlement_pk):
            lock_barrier.wait(timeout=20)
            return original(view, settlement_pk)

        with patch.object(
            CommissionSettlementViewSet,
            "_get_locked_settlement",
            synchronized_get,
        ):
            statuses = self._concurrent_requests("post", endpoint, [{}, {}])
        self.assertEqual(statuses, [200, 400])

        settlement.refresh_from_db()
        self.assertEqual(settlement.status, CommissionSettlement.STATUS_PAID)
        self.assertIsNotNone(settlement.paid_at)

    def test_concurrent_patch_preserves_fields_committed_by_both_requests(self):
        plan = create_commission_plan(
            employee=self.employee,
            percentage=Decimal("5.00"),
            valid_from=date(2026, 8, 1),
            valid_until=date(2026, 8, 31),
        )
        endpoint = f"/api/commission-plans/{plan.public_id}/"
        lock_barrier = Barrier(2)
        original = EmployeeCommissionPlanViewSet._get_locked_plan

        def synchronized_get(view):
            lock_barrier.wait(timeout=20)
            return original(view)

        with patch.object(
            EmployeeCommissionPlanViewSet,
            "_get_locked_plan",
            synchronized_get,
        ):
            statuses = self._concurrent_requests(
                "patch",
                endpoint,
                [
                    {"percentage": "7.50"},
                    {"valid_until": "2026-09-30"},
                ],
            )

        self.assertEqual(statuses, [200, 200])
        plan.refresh_from_db()
        self.assertEqual(plan.percentage, Decimal("7.50"))
        self.assertEqual(plan.valid_until, date(2026, 9, 30))
