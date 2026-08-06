from datetime import date
from decimal import Decimal
from itertools import count

from core.models import (
    Business,
    BusinessMembership,
    CashMovement,
    CashRegister,
    CommissionSettlement,
    Customer,
    Debt,
    DebtPayment,
    Employee,
    EmployeeCommissionPlan,
    EntityStatus,
    PaymentMethod,
    Product,
    ProductCategory,
    ProductVariant,
    ProductVariantType,
    StockMovement,
    Supplier,
    Transaction,
    TransactionDetail,
    User,
)

_sequence = count(1)


def next_number():
    return next(_sequence)


def create_status(name="Activo"):
    obj, _ = EntityStatus.objects.get_or_create(name=name)
    return obj


def create_user(*, email=None, full_name=None, password="TestPassword123!", role=None, is_superuser=False):
    n = next_number()
    email = email or f"user{n}@playnow.test"
    full_name = full_name or f"Usuario {n}"
    if is_superuser:
        return User.objects.create_superuser(email=email, full_name=full_name, password=password)
    return User.objects.create_user(
        email=email,
        full_name=full_name,
        password=password,
        role=role or User.Roles.BUSINESS_OWNER,
    )


def create_business(*, user, status=None, business_name=None, currency="NIO", create_owner_membership=True):
    n = next_number()
    business = Business.objects.create(
        user=user,
        business_name=business_name or f"Negocio {n}",
        description="",
        currency=currency,
        status=status or create_status(),
    )
    if create_owner_membership:
        BusinessMembership.objects.get_or_create(
            user=user,
            business=business,
            defaults={"role": BusinessMembership.ROLE_OWNER, "is_active": True},
        )
    return business


def create_employee(*, business, status=None, full_name=None, position="Vendedor"):
    n = next_number()
    return Employee.objects.create(
        business=business,
        full_name=full_name or f"Empleado {n}",
        phone="",
        position=position,
        status=status or create_status(),
    )


def create_membership(*, user, business, role, employee=None, is_active=True):
    obj, _ = BusinessMembership.objects.update_or_create(
        user=user,
        business=business,
        defaults={"role": role, "employee": employee, "is_active": is_active},
    )
    return obj


def create_role_user(
    *,
    business: Business,
    role: str,
    status: EntityStatus | None = None,
    email: str | None = None,
    full_name: str | None = None,
    position: str | None = None,
) -> tuple[
    User,
    Employee,
    BusinessMembership,
]:
    number = next_number()

    user = create_user(
        email=(
            email
            or f"{role}{number}@playnow.test"
        ),
        full_name=(
            full_name
            or f"{role.title()} {number}"
        ),
        role=getattr(
            User.Roles,
            "EMPLOYEE",
            "employee",
        ),
    )

    employee = create_employee(
        business=business,
        status=status,
        full_name=user.full_name,
        position=(
            position
            or role.title()
        ),
    )

    membership = create_membership(
        user=user,
        business=business,
        role=role,
        employee=employee,
    )

    return (
        user,
        employee,
        membership,
    )


def create_category(*, business, status=None, name=None):
    n = next_number()
    return ProductCategory.objects.create(
        business=business,
        name=name or f"Categoría {n}",
        status=status or create_status(),
    )


def create_product(*, business, status=None, category=None, title=None, base_price=Decimal("100.00"), base_cost=Decimal("60.00"), stock=10):
    n = next_number()
    return Product.objects.create(
        business=business,
        category=category,
        title=title or f"Producto {n}",
        description="",
        image_url="",
        base_price=base_price,
        base_cost=base_cost,
        stock=stock,
        is_visible=True,
        status=status or create_status(),
    )


def create_variant_type(*, product, status=None, name=None):
    n = next_number()
    return ProductVariantType.objects.create(
        product=product,
        name=name or f"Tipo {n}",
        status=status or create_status(),
    )


def create_variant(*, variant_type, status=None, label=None, additional_price=Decimal("0.00"), stock=10):
    n = next_number()
    return ProductVariant.objects.create(
        variant_type=variant_type,
        label=label or f"Variante {n}",
        additional_price=additional_price,
        stock=stock,
        status=status or create_status(),
    )


def create_customer(*, business, status=None, full_name=None):
    n = next_number()
    return Customer.objects.create(
        business=business,
        full_name=full_name or f"Cliente {n}",
        phone="",
        email="",
        status=status or create_status(),
    )


def create_supplier(*, business, status=None, name=None):
    n = next_number()
    return Supplier.objects.create(
        business=business,
        name=name or f"Proveedor {n}",
        phone="",
        email="",
        status=status or create_status(),
    )


def create_payment_method(
    *,
    business,
    status=None,
    name=None,
    method_type=PaymentMethod.TYPE_OTHER,
):
    n = next_number()

    return PaymentMethod.objects.create(
        business=business,
        name=name or f"Método {n}",
        method_type=method_type,
        status=status or create_status(),
    )


def create_transaction(
    *,
    business,
    created_by,
    status=None,
    employee=None,
    payment_method=None,
    customer=None,
    supplier=None,
    transaction_type="sale",
    total_value=Decimal("100.00"),
    is_debt=False,
    created_at=None,
):
    tx = Transaction.objects.create(
        business=business,
        employee=employee,
        customer=customer,
        supplier=supplier,
        payment_method=payment_method,
        type=transaction_type,
        is_debt=is_debt,
        concept="Transacción de prueba",
        total_value=total_value,
        status=status or create_status(),
        payment_status=(
            "pending"
            if is_debt
            else "paid"
        ),
        created_by=created_by,
    )

    if created_at is not None:
        Transaction.objects.filter(
            pk=tx.pk
        ).update(
            created_at=created_at
        )

        tx.refresh_from_db()

    return tx


def create_transaction_detail(*, transaction, product, variant=None, quantity=1, unit_price=Decimal("100.00")):
    return TransactionDetail.objects.create(
        transaction=transaction,
        product=product,
        variant=variant,
        quantity=quantity,
        unit_price=unit_price,
        total_price=unit_price * quantity,
    )


def create_debt(*, transaction, total_amount=None, paid_amount=Decimal("0.00")):
    total = total_amount or transaction.total_value
    return Debt.objects.create(
        transaction=transaction,
        total_amount=total,
        paid_amount=paid_amount,
        interest_rate=Decimal("0.00"),
        term_months=0,
        due_date=date.today(),
        is_settled=paid_amount >= total,
    )

def create_debt_payment(*, debt, payment_method, amount=Decimal("25.00")):
    return DebtPayment.objects.create(
        debt=debt,
        amount=amount,
        payment_date=date.today(),
        payment_method=payment_method,
    )

def create_commission_plan(
    *,
    employee: Employee,
    percentage: Decimal = Decimal("5.00"),
    valid_from: date | None = None,
    valid_until: date | None = None,
    is_active: bool = True,
) -> EmployeeCommissionPlan:
    return (
        EmployeeCommissionPlan.objects
        .create(
            employee=employee,
            percentage=percentage,
            valid_from=(
                valid_from
                or date.today()
            ),
            valid_until=valid_until,
            is_active=is_active,
        )
    )

def create_commission_settlement(
    *,
    employee: Employee,
    created_by: User,
    period_start: date,
    period_end: date,
    sales_count: int = 1,
    sales_total: Decimal = Decimal("1000.00"),
    commission_percentage: Decimal = Decimal("5.00"),
    commission_total: Decimal = Decimal("50.00"),
    employee_advances: Decimal = Decimal("0.00"),
    employee_repayments: Decimal = Decimal("0.00"),
    advance_balance: Decimal = Decimal("0.00"),
    net_commission_payable: Decimal | None = None,
    remaining_advance_balance: Decimal = Decimal("0.00"),
    settlement_status: str = (
        CommissionSettlement.STATUS_PENDING
    ),
) -> CommissionSettlement:
    net_payable = (
        net_commission_payable
        if net_commission_payable is not None
        else commission_total
    )

    return CommissionSettlement.objects.create(
        employee=employee,
        period_start=period_start,
        period_end=period_end,
        sales_count=sales_count,
        sales_total=sales_total,
        commission_percentage=commission_percentage,
        commission_total=commission_total,
        employee_advances=employee_advances,
        employee_repayments=employee_repayments,
        advance_balance=advance_balance,
        net_commission_payable=net_payable,
        remaining_advance_balance=(
            remaining_advance_balance
        ),
        status=settlement_status,
        created_by=created_by,
    )

def create_cash_register(
    *,
    business,
    employee,
    opened_by,
    opening_balance=Decimal("1000.00"),
    register_status=CashRegister.STATUS_OPEN,
    open_time=None,
    closing_balance=None,
    expected_closing_balance=None,
    difference=None,
    closed_by=None,
    close_time=None,
    opening_notes="",
    closing_notes="",
):
    from django.utils import timezone

    return CashRegister.objects.create(
        business=business,
        employee=employee,
        opened_by=opened_by,
        closed_by=closed_by,
        open_time=(
            open_time
            or timezone.now()
        ),
        close_time=close_time,
        opening_balance=opening_balance,
        closing_balance=closing_balance,
        expected_closing_balance=(
            expected_closing_balance
        ),
        difference=difference,
        opening_notes=opening_notes,
        closing_notes=closing_notes,
        status=register_status,
    )

def create_cash_movement(
    *,
    cash_register,
    created_by,
    movement_type=CashMovement.TYPE_DEPOSIT,
    amount=Decimal("100.00"),
    employee=None,
    payment_method=None,
    note="Movimiento de prueba",
):
    return CashMovement.objects.create(
        cash_register=cash_register,
        employee=employee,
        payment_method=payment_method,
        movement_type=movement_type,
        amount=amount,
        note=note,
        created_by=created_by,
    )

def create_stock_movement(
    *,
    product,
    created_by,
    movement_type,
    quantity,
    variant=None,
    transaction=None,
    transaction_detail=None,
    note="Movimiento de inventario de prueba",
    created_at=None,
):
    movement = StockMovement.objects.create(
        product=product,
        variant=variant,
        transaction=transaction,
        transaction_detail=(
            transaction_detail
        ),
        note=note,
        type=movement_type,
        quantity=quantity,
        created_by=created_by,
    )

    if created_at is not None:
        StockMovement.objects.filter(
            pk=movement.pk
        ).update(
            created_at=created_at
        )

        movement.refresh_from_db()

    return movement