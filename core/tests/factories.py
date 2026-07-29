from decimal import Decimal
from itertools import count
from datetime import date
from decimal import Decimal

from core.models import (
    Business,
    Debt,
    DebtPayment,
    EntityStatus,
    PaymentMethod,
    Product,
    ProductVariant,
    ProductVariantType,
    Transaction,
    TransactionDetail,
    User,
)

_sequence = count(1)


def next_number() -> int:
    return next(_sequence)


def create_status(
    name: str = "Activo",
) -> EntityStatus:
    status, _ = EntityStatus.objects.get_or_create(
        name=name,
    )
    return status


def create_user(
    *,
    email: str | None = None,
    full_name: str | None = None,
    password: str = "TestPassword123!",
    is_superuser: bool = False,
) -> User:
    number = next_number()

    email = email or f"owner{number}@playnow.test"
    full_name = full_name or f"Propietario {number}"

    if is_superuser:
        return User.objects.create_superuser(
            email=email,
            full_name=full_name,
            password=password,
        )

    return User.objects.create_user(
        email=email,
        full_name=full_name,
        password=password,
        role=User.Roles.BUSINESS_OWNER,
    )


def create_business(
    *,
    user: User,
    status: EntityStatus | None = None,
    business_name: str | None = None,
    description: str = "",
    currency: str = "NIO",
) -> Business:
    number = next_number()

    return Business.objects.create(
        user=user,
        business_name=business_name or f"Negocio {number}",
        description=description,
        currency=currency,
        status=status or create_status(),
    )


def create_product(
    *,
    business: Business,
    status: EntityStatus | None = None,
    title: str | None = None,
    description: str = "",
    base_price: Decimal = Decimal("100.00"),
    base_cost: Decimal = Decimal("60.00"),
    stock: int = 10,
    is_visible: bool = True,
) -> Product:
    number = next_number()

    return Product.objects.create(
        business=business,
        category=None,
        title=title or f"Producto {number}",
        description=description,
        image_url="",
        base_price=base_price,
        base_cost=base_cost,
        stock=stock,
        is_visible=is_visible,
        status=status or create_status(),
    )

def create_variant_type(
    *,
    product: Product,
    status: EntityStatus | None = None,
    name: str | None = None,
) -> ProductVariantType:
    number = next_number()

    return ProductVariantType.objects.create(
        product=product,
        name=name or f"Talla {number}",
        status=status or create_status(),
    )


def create_variant(
    *,
    variant_type: ProductVariantType,
    status: EntityStatus | None = None,
    label: str | None = None,
    additional_price: Decimal = Decimal("0.00"),
    stock: int = 10,
) -> ProductVariant:
    number = next_number()

    return ProductVariant.objects.create(
        variant_type=variant_type,
        label=label or f"Variante {number}",
        additional_price=additional_price,
        stock=stock,
        status=status or create_status(),
    )


def create_payment_method(
    *,
    name: str | None = None,
) -> PaymentMethod:
    number = next_number()

    return PaymentMethod.objects.create(
        name=name or f"Método {number}",
    )


def create_transaction(
    *,
    business: Business,
    created_by: User,
    status: EntityStatus | None = None,
    transaction_type: str = "sale",
    total_value: Decimal = Decimal("100.00"),
    is_debt: bool = False,
) -> Transaction:
    return Transaction.objects.create(
        business=business,
        type=transaction_type,
        is_debt=is_debt,
        concept="Transacción de prueba",
        total_value=total_value,
        status=status or create_status(),
        payment_status="pending" if is_debt else "paid",
        created_by=created_by,
    )


def create_transaction_detail(
    *,
    transaction: Transaction,
    product: Product,
    variant: ProductVariant | None = None,
    quantity: int = 1,
    unit_price: Decimal = Decimal("100.00"),
) -> TransactionDetail:
    return TransactionDetail.objects.create(
        transaction=transaction,
        product=product,
        variant=variant,
        quantity=quantity,
        unit_price=unit_price,
        total_price=unit_price * quantity,
    )


def create_debt(
    *,
    transaction: Transaction,
    total_amount: Decimal | None = None,
    paid_amount: Decimal = Decimal("0.00"),
) -> Debt:
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