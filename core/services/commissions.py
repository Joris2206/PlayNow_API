from dataclasses import dataclass
from datetime import date
from decimal import Decimal

from django.db.models import Count, Q, Sum

from core.models import (
    CommissionSettlement,
    Employee,
    EmployeeCommissionPlan,
    Transaction,
)
from core.services.financial_flows import exclude_terminal_transactions
from core.utils import calculate_employee_advance_summary


SETTLEMENT_PERIOD_UNIQUE_CONSTRAINT = (
    "unique_employee_commission_settlement_per_period"
)


class CommissionDomainError(Exception):
    def __init__(self, errors):
        self.errors = errors
        super().__init__(str(errors))


def is_settlement_period_unique_violation(exc):
    cause = getattr(exc, "__cause__", None)
    diag = getattr(cause, "diag", None)
    return (
        getattr(diag, "constraint_name", None)
        == SETTLEMENT_PERIOD_UNIQUE_CONSTRAINT
    )


@dataclass(frozen=True)
class CommissionCalculation:
    plan: EmployeeCommissionPlan
    sales_count: int
    sales_total: Decimal
    commission_percentage: Decimal
    commission_total: Decimal
    employee_advances: Decimal
    employee_repayments: Decimal
    advance_balance: Decimal
    net_commission_payable: Decimal
    remaining_advance_balance: Decimal


def validate_employee_is_active(employee):
    if employee.status.name.casefold() != "activo":
        raise CommissionDomainError({
            "employee_public_id": (
                "El empleado debe estar Activo para realizar esta operación."
            )
        })


def validate_period(*, period_start, period_end, end_field="period_end"):
    if period_end < period_start:
        raise CommissionDomainError({
            end_field: (
                "La fecha final no puede ser anterior a la fecha inicial."
            )
        })


def validate_employee_business(*, employee, business):
    if employee.business_id != business.pk:
        raise CommissionDomainError({
            "employee_public_id": (
                "El empleado no pertenece al negocio indicado."
            )
        })


def commission_plan_overlaps(
    *,
    employee,
    valid_from,
    valid_until,
    exclude_plan=None,
):
    plans = EmployeeCommissionPlan.objects.filter(
        employee=employee,
        is_active=True,
    ).filter(
        Q(valid_until__isnull=True) | Q(valid_until__gte=valid_from),
    )

    if valid_until is not None:
        plans = plans.filter(valid_from__lte=valid_until)

    if exclude_plan is not None:
        plans = plans.exclude(pk=exclude_plan.pk)

    return plans.exists()


def validate_commission_plan_candidate(
    *,
    employee,
    valid_from,
    valid_until,
    is_active,
    exclude_plan=None,
    require_employee_active=True,
):
    if require_employee_active:
        validate_employee_is_active(employee)

    if valid_until is not None and valid_until < valid_from:
        raise CommissionDomainError({
            "valid_until": (
                "La fecha final no puede ser anterior a la fecha inicial."
            )
        })

    if not is_active:
        return

    if commission_plan_overlaps(
        employee=employee,
        valid_from=valid_from,
        valid_until=valid_until,
        exclude_plan=exclude_plan,
    ):
        raise CommissionDomainError({
            "valid_from": (
                "El empleado ya tiene un plan de comisión activo que "
                "coincide con ese período."
            )
        })


def settlement_overlaps(*, employee, period_start, period_end):
    return CommissionSettlement.objects.filter(
        employee=employee,
        period_start__lte=period_end,
        period_end__gte=period_start,
    ).exists()


def validate_no_settlement_overlap(*, employee, period_start, period_end):
    if settlement_overlaps(
        employee=employee,
        period_start=period_start,
        period_end=period_end,
    ):
        raise CommissionDomainError({
            "period": (
                "Ya existe una liquidación para este empleado con un "
                "período que se solapa."
            )
        })


def resolve_covering_commission_plan(*, employee, period_start, period_end):
    plans = list(
        EmployeeCommissionPlan.objects.filter(
            employee=employee,
            is_active=True,
            valid_from__lte=period_start,
        ).filter(
            Q(valid_until__isnull=True) | Q(valid_until__gte=period_end),
        )[:2]
    )

    if len(plans) != 1:
        raise CommissionDomainError({
            "commission_plan": (
                "El período de liquidación debe estar cubierto "
                "completamente por un único plan de comisión."
            )
        })

    return plans[0]


def calculate_commission(
    *,
    employee,
    business,
    period_start: date,
    period_end: date,
    reject_settlement_overlap=True,
):
    validate_employee_business(employee=employee, business=business)
    validate_employee_is_active(employee)
    validate_period(period_start=period_start, period_end=period_end)

    if reject_settlement_overlap:
        validate_no_settlement_overlap(
            employee=employee,
            period_start=period_start,
            period_end=period_end,
        )

    plan = resolve_covering_commission_plan(
        employee=employee,
        period_start=period_start,
        period_end=period_end,
    )

    sales = exclude_terminal_transactions(
        Transaction.objects.filter(
            business=business,
            employee=employee,
            type="sale",
            created_at__date__gte=period_start,
            created_at__date__lte=period_end,
        )
    )
    sales_summary = sales.aggregate(
        sales_count=Count("id"),
        sales_total=Sum("total_value"),
    )
    sales_total = (
        sales_summary["sales_total"] or Decimal("0.00")
    ).quantize(Decimal("0.01"))
    commission_percentage = plan.percentage.quantize(Decimal("0.01"))
    commission_total = (
        sales_total * commission_percentage / Decimal("100.00")
    ).quantize(Decimal("0.01"))

    advance_summary = calculate_employee_advance_summary(
        employee=employee,
        period_start=period_start,
        period_end=period_end,
    )
    advance_balance = advance_summary["advance_balance"]
    net_commission_payable = max(
        commission_total - advance_balance,
        Decimal("0.00"),
    ).quantize(Decimal("0.01"))
    remaining_advance_balance = max(
        advance_balance - commission_total,
        Decimal("0.00"),
    ).quantize(Decimal("0.01"))

    return CommissionCalculation(
        plan=plan,
        sales_count=sales_summary["sales_count"],
        sales_total=sales_total,
        commission_percentage=commission_percentage,
        commission_total=commission_total,
        employee_advances=advance_summary["employee_advances"],
        employee_repayments=advance_summary["employee_repayments"],
        advance_balance=advance_balance,
        net_commission_payable=net_commission_payable,
        remaining_advance_balance=remaining_advance_balance,
    )


def lock_employees(*employees):
    """Serialize API commission writes by stable rows in deterministic order.

    Direct database writes that bypass this service are not protected by the
    application-level overlap guarantee.
    """
    employee_ids = sorted({employee.pk for employee in employees})
    locked = Employee.objects.select_for_update().select_related(
        "business",
        "status",
    ).filter(pk__in=employee_ids).order_by("pk")
    return {employee.pk: employee for employee in locked}
