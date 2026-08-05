# core/utils.py
from decimal import Decimal

from django.db.models.aggregates import Sum
from rest_framework.throttling import ScopedRateThrottle
from rest_framework.decorators import api_view, throttle_classes, permission_classes
from rest_framework_simplejwt.views import TokenObtainPairView
from rest_framework.permissions import AllowAny
import logging

from core.models import CashMovement

class LoginView(TokenObtainPairView):
    throttle_classes = [ScopedRateThrottle]
    throttle_scope = 'login'
    permission_classes = [AllowAny]


audit_logger = logging.getLogger("audit")

def log_action(user, action, entity_type, entity_id, extra=None):
    audit_logger.info(
        f"{action} {entity_type}={entity_id} by user={getattr(user, 'id', None)} extra={extra}"
    )

def calculate_employee_advance_summary(
    *,
    employee,
    period_start,
    period_end,
):
    movements = (
        CashMovement.objects
        .filter(
            employee=employee,
            created_at__date__gte=period_start,
            created_at__date__lte=period_end,
            movement_type__in=[
                CashMovement.TYPE_EMPLOYEE_ADVANCE,
                CashMovement.TYPE_EMPLOYEE_REPAYMENT,
            ],
        )
        .values("movement_type")
        .annotate(total=Sum("amount"))
    )

    totals = {
        CashMovement.TYPE_EMPLOYEE_ADVANCE: Decimal("0.00"),
        CashMovement.TYPE_EMPLOYEE_REPAYMENT: Decimal("0.00"),
    }

    for row in movements:
        totals[row["movement_type"]] = (
            row["total"] or Decimal("0.00")
        )

    employee_advances = totals[
        CashMovement.TYPE_EMPLOYEE_ADVANCE
    ].quantize(
        Decimal("0.01")
    )

    employee_repayments = totals[
        CashMovement.TYPE_EMPLOYEE_REPAYMENT
    ].quantize(
        Decimal("0.01")
    )

    advance_balance = max(
        employee_advances - employee_repayments,
        Decimal("0.00"),
    ).quantize(
        Decimal("0.01")
    )

    return {
        "employee_advances": employee_advances,
        "employee_repayments": employee_repayments,
        "advance_balance": advance_balance,
    }