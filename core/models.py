from django.utils import timezone as django_timezone
import uuid
from decimal import Decimal

from django.conf import settings
from django.contrib.auth.models import (
    AbstractBaseUser,
    BaseUserManager,
    PermissionsMixin,
)
from django.core.exceptions import ValidationError
from django.db import models
from django.db.models import Q
from django.db.models.functions import Lower

# Opcional: Custom user manager
class UserManager(BaseUserManager):
    def create_user(
        self,
        email,
        full_name,
        password=None,
        **extra_fields,
    ):
        if not email:
            raise ValueError("El correo electrónico es obligatorio.")

        if not full_name:
            raise ValueError("El nombre completo es obligatorio.")

        email = self.normalize_email(email).strip().lower()

        user = self.model(
            email=email,
            full_name=full_name,
            **extra_fields,
        )

        user.set_password(password)
        user.save(using=self._db)

        return user

    def create_superuser(
        self,
        email,
        full_name,
        password=None,
        **extra_fields,
    ):
        extra_fields.setdefault("role", User.Roles.BUSINESS_ADMIN)
        extra_fields.setdefault("is_active", True)
        extra_fields.setdefault("is_staff", True)
        extra_fields.setdefault("is_superuser", True)

        if extra_fields.get("is_staff") is not True:
            raise ValueError(
                "El superusuario debe tener is_staff=True."
            )

        if extra_fields.get("is_superuser") is not True:
            raise ValueError(
                "El superusuario debe tener is_superuser=True."
            )

        return self.create_user(
            email,
            full_name,
            password,
            **extra_fields,
        )
class User(AbstractBaseUser, PermissionsMixin):
    class Roles(models.TextChoices):
        """
        Rol global/legado de la cuenta.

        Los permisos dentro de cada negocio se controlan mediante
        BusinessMembership.role. Este campo se mantiene para compatibilidad
        con autenticación, panel administrativo y código existente.
        """

        BUSINESS_OWNER = "business_owner", "Propietario"
        BUSINESS_ADMIN = "business_admin", "Administrador"
        EMPLOYEE = "employee", "Colaborador"

    public_id = models.UUIDField(default=uuid.uuid4, unique=True, db_index=True, editable=False)
    full_name = models.CharField(max_length=255)
    phone = models.CharField(max_length=20, blank=True, null=True)
    email = models.EmailField(unique=True)
    role = models.CharField(max_length=50, choices=Roles.choices, default=Roles.BUSINESS_OWNER)
    is_active = models.BooleanField(default=True)
    is_staff = models.BooleanField(default=False)
    is_superuser = models.BooleanField(default=False)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    objects = UserManager()

    USERNAME_FIELD = 'email'
    REQUIRED_FIELDS = ['full_name']

    def __str__(self):
        return f"{self.full_name} <{self.email}>"


class Business(models.Model):
    public_id = models.UUIDField(default=uuid.uuid4, unique=True, db_index=True, editable=False)
    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name='businesses')
    business_name = models.CharField(max_length=255)
    description = models.TextField(blank=True)
    currency = models.CharField(max_length=10)
    status = models.ForeignKey('EntityStatus', on_delete=models.PROTECT)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self):
        cur = f" · {self.currency}" if self.currency else ""
        return f"{self.business_name}{cur}"

    def has_active_member(self, user, roles=None) -> bool:
        """
        Indica si un usuario posee una membresía activa en este negocio.

        `roles` puede ser None o una colección de roles de
        BusinessMembership.
        """
        if user is None or not user.is_authenticated:
            return False

        if user.is_superuser:
            return True

        memberships = self.memberships.filter(
            user=user,
            is_active=True,
        )

        if roles is not None:
            memberships = memberships.filter(
                role__in=roles,
            )

        return memberships.exists()


class BusinessMembership(models.Model):
    ROLE_OWNER = "owner"
    ROLE_ADMIN = "admin"
    ROLE_CASHIER = "cashier"
    ROLE_SELLER = "seller"
    ROLE_INVENTORY = "inventory"
    ROLE_VIEWER = "viewer"

    ROLES = [
        (ROLE_OWNER, "Propietario"),
        (ROLE_ADMIN, "Administrador"),
        (ROLE_CASHIER, "Cajero"),
        (ROLE_SELLER, "Vendedor"),
        (ROLE_INVENTORY, "Inventario"),
        (ROLE_VIEWER, "Solo lectura"),
    ]

    public_id = models.UUIDField(
        default=uuid.uuid4,
        unique=True,
        db_index=True,
        editable=False,
    )

    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="business_memberships",
    )

    business = models.ForeignKey(
        "Business",
        on_delete=models.CASCADE,
        related_name="memberships",
    )

    employee = models.OneToOneField(
        "Employee",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="membership",
    )

    role = models.CharField(
        max_length=20,
        choices=ROLES,
    )

    is_active = models.BooleanField(
        default=True,
    )

    created_at = models.DateTimeField(
        auto_now_add=True,
    )

    updated_at = models.DateTimeField(
        auto_now=True,
    )

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=[
                    "user",
                    "business",
                ],
                name="unique_user_membership_per_business",
            ),
            models.UniqueConstraint(
                fields=[
                    "business",
                    "employee",
                ],
                condition=Q(employee__isnull=False),
                name="unique_employee_membership_per_business",
            ),
        ]
        indexes = [
            models.Index(
                fields=["user", "is_active"],
                name="membership_user_active_idx",
            ),
            models.Index(
                fields=["business", "is_active"],
                name="membership_business_active_idx",
            ),
            models.Index(
                fields=["business", "role", "is_active"],
                name="membership_role_active_idx",
            ),
        ]
        ordering = ["business_id", "role", "created_at"]

    def clean(self):
        super().clean()

        if (
            self.employee_id is not None
            and self.business_id is not None
            and self.employee.business_id != self.business_id
        ):
            raise ValidationError({
                "employee": (
                    "El empleado debe pertenecer al mismo "
                    "negocio que la membresía."
                )
            })

        if (
            self.role == self.ROLE_OWNER
            and self.employee_id is not None
        ):
            # El propietario puede tener un Employee asociado, pero no es
            # obligatorio. No se rechaza; esta condición queda documentada
            # para mantener el modelo flexible.
            pass

    def save(self, *args, **kwargs):
        self.full_clean()
        return super().save(*args, **kwargs)

    def __str__(self):
        return (
            f"{self.user} · "
            f"{self.business} · "
            f"{self.get_role_display()}"
        )

class EntityStatus(models.Model):
    public_id = models.UUIDField(default=uuid.uuid4, unique=True, db_index=True, editable=False)
    name = models.CharField(max_length=50)

    def __str__(self):
        return self.name

class ProductCategory(models.Model):
    public_id = models.UUIDField(default=uuid.uuid4, unique=True, db_index=True, editable=False)
    business = models.ForeignKey(Business, on_delete=models.CASCADE, related_name="categories")
    name = models.CharField(max_length=255)
    status = models.ForeignKey(EntityStatus, on_delete=models.PROTECT)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["business", "name"],
                name="unique_category_name_per_business",
            ),
        ]
        indexes = [
            models.Index(fields=["business", "status"]),
        ]
        
    def __str__(self):
        return self.name
    
class Product(models.Model):
    public_id = models.UUIDField(default=uuid.uuid4, unique=True, db_index=True, editable=False)
    business = models.ForeignKey(Business, on_delete=models.CASCADE, related_name='products')
    category = models.ForeignKey(ProductCategory, on_delete=models.SET_NULL, null=True, blank=True, related_name='products')
    title = models.CharField(max_length=255)
    description = models.TextField(blank=True)
    image_url = models.TextField(blank=True)
    base_price = models.DecimalField(max_digits=12, decimal_places=2)
    base_cost = models.DecimalField(max_digits=12, decimal_places=2)
    stock = models.IntegerField()
    is_visible = models.BooleanField(default=True)
    status = models.ForeignKey(EntityStatus, on_delete=models.PROTECT)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    class Meta:
        constraints = [
            models.CheckConstraint(check=models.Q(stock__gte=0), name="product_stock_gte_0"),
        ]
        indexes = [
            models.Index(fields=["business", "created_at"]),
            models.Index(fields=["status"]),
        ]

    def __str__(self):
        cat = f" · {self.category.name}" if self.category_id else ""
        return f"{self.title}{cat}"

class ProductVariantType(models.Model):
    public_id = models.UUIDField(default=uuid.uuid4, unique=True, db_index=True, editable=False)
    product = models.ForeignKey(Product, on_delete=models.CASCADE, related_name='variant_types')
    name = models.CharField(max_length=255)
    status = models.ForeignKey(
        EntityStatus,
        on_delete=models.PROTECT,
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["product", "name"],
                name="unique_variant_type_per_product",
            ),
        ]

    def __str__(self):
        return f"{self.product.title} - {self.name}"

class ProductVariant(models.Model):
    public_id = models.UUIDField(default=uuid.uuid4, unique=True, db_index=True, editable=False)
    variant_type = models.ForeignKey(ProductVariantType, on_delete=models.CASCADE, related_name='variants')
    label = models.CharField(max_length=255)
    additional_price = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    stock = models.IntegerField()
    status = models.ForeignKey(EntityStatus, on_delete=models.PROTECT)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        constraints = [
            models.CheckConstraint(
                condition=models.Q(stock__gte=0),
                name="variant_stock_gte_0",
            ),
            models.UniqueConstraint(
                fields=["variant_type", "label"],
                name="unique_variant_label_per_type",
            ),
        ]

    def __str__(self):
        prod = self.variant_type.product.title
        return f"{self.variant_type.name}: {self.label} · {prod}"
    
class Employee(models.Model):
    public_id = models.UUIDField(default=uuid.uuid4, unique=True, db_index=True, editable=False)
    business = models.ForeignKey(Business, on_delete=models.CASCADE, related_name='employees')
    full_name = models.CharField(max_length=200)
    position = models.CharField(max_length=100)
    phone = models.CharField(max_length=30, blank=True)
    email = models.EmailField(blank=True)
    status = models.ForeignKey(EntityStatus, on_delete=models.PROTECT)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self):
        return (
            f"{self.full_name} - "
            f"{self.position}"
        )
    
class Customer(models.Model):
    public_id = models.UUIDField(default=uuid.uuid4, unique=True, db_index=True, editable=False)
    business = models.ForeignKey(Business, on_delete=models.CASCADE, related_name='customers')
    full_name = models.CharField(max_length=255)
    phone = models.CharField(max_length=50)
    email = models.EmailField(blank=True)
    status = models.ForeignKey(EntityStatus, on_delete=models.PROTECT)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    
    def __str__(self):
        phone = f" · {self.phone}" if self.phone else ""
        return f"{self.full_name}{phone}"

class Supplier(models.Model):
    public_id = models.UUIDField(default=uuid.uuid4, unique=True, db_index=True, editable=False)
    business = models.ForeignKey(Business, on_delete=models.CASCADE, related_name='suppliers')
    name = models.CharField(max_length=255)
    phone = models.CharField(max_length=50)
    email = models.EmailField(blank=True)
    status = models.ForeignKey(EntityStatus, on_delete=models.PROTECT)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    
    def __str__(self):
        phone = f" · {self.phone}" if self.phone else ""
        return f"{self.name}{phone}"
    
class PaymentMethod(models.Model):
    TYPE_CASH = "cash"
    TYPE_CARD = "card"
    TYPE_TRANSFER = "transfer"
    TYPE_OTHER = "other"

    METHOD_TYPES = [
        (TYPE_CASH, "Efectivo"),
        (TYPE_CARD, "Tarjeta"),
        (TYPE_TRANSFER, "Transferencia"),
        (TYPE_OTHER, "Otro"),
    ]

    public_id = models.UUIDField(
        default=uuid.uuid4,
        unique=True,
        db_index=True,
        editable=False,
    )

    business = models.ForeignKey(
        "Business",
        on_delete=models.CASCADE,
        related_name="payment_methods",
    )

    name = models.CharField(
        max_length=100,
    )

    method_type = models.CharField(
        max_length=20,
        choices=METHOD_TYPES,
        default=TYPE_OTHER,
    )

    status = models.ForeignKey(
        "EntityStatus",
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="payment_methods",
    )

    created_at = models.DateTimeField(
        auto_now_add=True,
    )

    updated_at = models.DateTimeField(
        auto_now=True,
    )

    class Meta:
        constraints = [
            models.UniqueConstraint(
                Lower("name"),
                "business",
                name="unique_payment_method_per_business",
            ),
        ]

        indexes = [
            models.Index(
                fields=["business", "status"],
                name="paymethod_biz_status_idx",
            ),
            models.Index(
                fields=["business", "method_type"],
                name="paymethod_biz_type_idx",
            ),
        ]

        ordering = ["name"]

    def __str__(self):
        return self.name

class CashRegister(models.Model):
    STATUS_OPEN = "open"
    STATUS_CLOSED = "closed"

    STATUSES = [
        (STATUS_OPEN, "Abierta"),
        (STATUS_CLOSED, "Cerrada"),
    ]

    public_id = models.UUIDField(
        default=uuid.uuid4,
        unique=True,
        db_index=True,
        editable=False,
    )

    business = models.ForeignKey(
        "Business",
        on_delete=models.CASCADE,
        related_name="cash_registers",
    )

    employee = models.ForeignKey(
        "Employee",
        on_delete=models.PROTECT,
        related_name="cash_registers",
    )

    opened_by = models.ForeignKey(
        User,
        on_delete=models.PROTECT,
        related_name="opened_cash_registers",
    )

    closed_by = models.ForeignKey(
        User,
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="closed_cash_registers",
    )

    open_time = models.DateTimeField(
        default=django_timezone.now,
    )

    close_time = models.DateTimeField(
        null=True,
        blank=True,
    )

    opening_balance = models.DecimalField(
        max_digits=12,
        decimal_places=2,
    )

    # Dinero contado físicamente al cerrar.
    closing_balance = models.DecimalField(
        max_digits=12,
        decimal_places=2,
        null=True,
        blank=True,
    )

    # Saldo que el sistema esperaba encontrar.
    expected_closing_balance = models.DecimalField(
        max_digits=12,
        decimal_places=2,
        null=True,
        blank=True,
    )

    # closing_balance - expected_closing_balance
    difference = models.DecimalField(
        max_digits=12,
        decimal_places=2,
        null=True,
        blank=True,
    )

    opening_notes = models.TextField(
        blank=True,
    )

    closing_notes = models.TextField(
        blank=True,
    )

    status = models.CharField(
        max_length=10,
        choices=STATUSES,
        default=STATUS_OPEN,
    )

    created_at = models.DateTimeField(
        auto_now_add=True,
    )

    updated_at = models.DateTimeField(
        auto_now=True,
    )

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["business"],
                condition=Q(status="open"),
                name="one_open_register_per_business",
            ),
            models.CheckConstraint(
                condition=Q(
                    opening_balance__gte=0,
                ),
                name=(
                    "cash_register_"
                    "opening_balance_gte_0"
                ),
            ),
            models.CheckConstraint(
                condition=(
                    Q(closing_balance__isnull=True)
                    | Q(closing_balance__gte=0)
                ),
                name=(
                    "cash_register_"
                    "closing_balance_gte_0"
                ),
            ),
        ]

        ordering = [
            "-open_time",
        ]

    def clean(self):
        super().clean()

        if (
            self.employee_id
            and self.business_id
            and self.employee.business_id
            != self.business_id
        ):
            raise ValidationError({
                "employee": (
                    "El empleado debe pertenecer al mismo "
                    "negocio de la caja."
                )
            })

        if (
            self.close_time is not None
            and self.open_time is not None
            and self.close_time < self.open_time
        ):
            raise ValidationError({
                "close_time": (
                    "La fecha de cierre no puede ser "
                    "anterior a la apertura."
                )
            })

        if self.status == self.STATUS_CLOSED:
            required_fields = {
                "close_time": self.close_time,
                "closing_balance": self.closing_balance,
                "expected_closing_balance": (
                    self.expected_closing_balance
                ),
                "difference": self.difference,
                "closed_by": self.closed_by,
            }

            missing_fields = [
                field
                for field, value in required_fields.items()
                if value is None
            ]

            if missing_fields:
                raise ValidationError({
                    "status": (
                        "Una caja cerrada debe contener "
                        "todos los datos del cierre."
                    )
                })

    def __str__(self):
        return (
            f"{self.business.business_name} · "
            f"{self.employee.full_name} · "
            f"{self.status}"
        )


class CashMovement(models.Model):
    TYPE_DEPOSIT = "deposit"
    TYPE_WITHDRAWAL = "withdrawal"
    TYPE_EMPLOYEE_ADVANCE = "employee_advance"
    TYPE_EMPLOYEE_REPAYMENT = "employee_repayment"
    TYPE_OTHER_INCOME = "other_income"
    TYPE_OTHER_EXPENSE = "other_expense"

    MOVEMENT_TYPES = [
        (TYPE_DEPOSIT, "Depósito en caja"),
        (TYPE_WITHDRAWAL, "Retiro de caja"),
        (
            TYPE_EMPLOYEE_ADVANCE,
            "Adelanto a empleado",
        ),
        (
            TYPE_EMPLOYEE_REPAYMENT,
            "Devolución de adelanto",
        ),
        (TYPE_OTHER_INCOME, "Otro ingreso"),
        (TYPE_OTHER_EXPENSE, "Otro egreso"),
    ]

    public_id = models.UUIDField(
        default=uuid.uuid4,
        unique=True,
        db_index=True,
        editable=False,
    )

    cash_register = models.ForeignKey(
        CashRegister,
        on_delete=models.PROTECT,
        related_name="movements",
    )

    employee = models.ForeignKey(
        Employee,
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="cash_movements",
    )

    payment_method = models.ForeignKey(
        PaymentMethod,
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="cash_movements",
    )

    movement_type = models.CharField(
        max_length=30,
        choices=MOVEMENT_TYPES,
    )

    amount = models.DecimalField(
        max_digits=12,
        decimal_places=2,
    )

    note = models.CharField(
        max_length=255,
        blank=True,
    )

    created_by = models.ForeignKey(
        User,
        on_delete=models.PROTECT,
        related_name="created_cash_movements",
    )

    created_at = models.DateTimeField(
        auto_now_add=True,
    )

    class Meta:
        constraints = [
            models.CheckConstraint(
                condition=Q(amount__gt=0),
                name="cash_movement_amount_gt_0",
            ),
        ]

        indexes = [
            models.Index(
                fields=[
                    "cash_register",
                    "created_at",
                ],
                name="cashmov_register_date_idx",
            ),
            models.Index(
                fields=[
                    "employee",
                    "created_at",
                ],
                name="cashmov_employee_date_idx",
            ),
            models.Index(
                fields=[
                    "movement_type",
                    "created_at",
                ],
                name="cashmov_type_date_idx",
            ),
        ]

        ordering = [
            "-created_at",
        ]

    def clean(self):
        super().clean()

        register = (
            self.cash_register
            if self.cash_register_id
            else None
        )

        if (
            register is not None
            and register.status
            != CashRegister.STATUS_OPEN
        ):
            raise ValidationError({
                "cash_register": (
                    "No se pueden registrar movimientos "
                    "en una caja cerrada."
                )
            })

        if (
            self.employee_id
            and register is not None
            and self.employee.business_id
            != register.business_id
        ):
            raise ValidationError({
                "employee": (
                    "El empleado debe pertenecer al mismo "
                    "negocio de la caja."
                )
            })

        if (
            self.payment_method_id
            and register is not None
            and self.payment_method.business_id
            != register.business_id
        ):
            raise ValidationError({
                "payment_method": (
                    "El método de pago debe pertenecer al "
                    "mismo negocio de la caja."
                )
            })

        employee_required_types = {
            self.TYPE_EMPLOYEE_ADVANCE,
            self.TYPE_EMPLOYEE_REPAYMENT,
        }

        if (
            self.movement_type
            in employee_required_types
            and self.employee_id is None
        ):
            raise ValidationError({
                "employee": (
                    "Debes indicar el empleado para "
                    "este tipo de movimiento."
                )
            })

    @property
    def signed_amount(self):
        income_types = {
            self.TYPE_DEPOSIT,
            self.TYPE_EMPLOYEE_REPAYMENT,
            self.TYPE_OTHER_INCOME,
        }

        if self.movement_type in income_types:
            return self.amount

        return -self.amount

    def __str__(self):
        return (
            f"{self.get_movement_type_display()} · "
            f"{self.amount}"
        )

class Transaction(models.Model):
    TRANSACTION_TYPES = [
        ('sale', 'Sale'),
        ('purchase', 'Purchase'),
        ('expense', 'Expense'),
    ]
    PAYMENT_STATUSES = [
        ("paid", "Paid"),
        ("partial", "Partial"),
        ("pending", "Pending"),
    ]

    public_id = models.UUIDField(default=uuid.uuid4, unique=True, db_index=True, editable=False)
    business = models.ForeignKey('Business', on_delete=models.CASCADE, related_name='transactions')
    customer = models.ForeignKey('Customer', on_delete=models.SET_NULL, null=True, blank=True, related_name='transactions')
    supplier = models.ForeignKey('Supplier', on_delete=models.SET_NULL, null=True, blank=True, related_name='transactions')
    employee = models.ForeignKey('Employee', on_delete=models.SET_NULL, null=True, blank=True, related_name='transactions')
    payment_method = models.ForeignKey('PaymentMethod', on_delete=models.SET_NULL, null=True, blank=True, related_name='transactions')
    type = models.CharField(max_length=20, choices=TRANSACTION_TYPES)
    is_debt = models.BooleanField(default=False)
    discount_percent = models.DecimalField(max_digits=5, decimal_places=2, null=True, blank=True)
    concept = models.TextField(blank=True)
    total_value = models.DecimalField(max_digits=12, decimal_places=2)
    status = models.ForeignKey('EntityStatus', on_delete=models.PROTECT)
    invoice_number = models.CharField(max_length=100, blank=True, null=True)
    payment_status = models.CharField(max_length=20, choices=PAYMENT_STATUSES, default="paid")
    invoice_series = models.CharField(max_length=50, blank=True, null=True)
    invoice_file_url = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    created_by = models.ForeignKey(User, on_delete=models.PROTECT, related_name="created_transactions")
    updated_by = models.ForeignKey(User, on_delete=models.PROTECT, related_name="updated_transactions", null=True, blank=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=[
                    "business",
                    "invoice_series",
                    "invoice_number",
                ],
                condition=(
                    Q(invoice_number__isnull=False)
                    & ~Q(invoice_number="")
                ),
                name="unique_invoice_per_business_when_present",
            ),
            models.CheckConstraint(
                condition=Q(total_value__gte=0),
                name="transaction_total_value_gte_0",
            ),
            models.CheckConstraint(
                condition=(
                    Q(discount_percent__isnull=True)
                    | (
                        Q(discount_percent__gte=0)
                        & Q(discount_percent__lte=100)
                    )
                ),
                name="transaction_discount_between_0_and_100",
            ),
            models.CheckConstraint(
                condition=(
                    Q(
                        payment_status="paid",
                        is_debt=False,
                    )
                    | Q(
                        payment_status__in=[
                            "partial",
                            "pending",
                        ],
                        is_debt=True,
                    )
                ),
                name="transaction_payment_status_matches_debt",
            ),
        ]

    def __str__(self):
        t = self.get_type_display()
        inv = ""
        if self.invoice_series or self.invoice_number:
            inv = f" #{self.invoice_series}-{self.invoice_number}".replace("# -", "").replace("--", "-").strip()
        return f"{t}{inv} · {self.public_id}"

class TransactionDetail(models.Model):
    public_id = models.UUIDField(default=uuid.uuid4, unique=True, db_index=True, editable=False)
    transaction = models.ForeignKey(Transaction, on_delete=models.CASCADE, related_name='details')
    product = models.ForeignKey(Product, on_delete=models.PROTECT)
    variant = models.ForeignKey(ProductVariant, on_delete=models.SET_NULL, null=True, blank=True)
    quantity = models.PositiveIntegerField()
    unit_price = models.DecimalField(max_digits=12, decimal_places=2)
    total_price = models.DecimalField(max_digits=12, decimal_places=2)

    class Meta:
        constraints = [
            models.CheckConstraint(
                condition=Q(quantity__gt=0),
                name="transaction_detail_quantity_gt_0",
            ),
            models.CheckConstraint(
                condition=Q(unit_price__gte=0),
                name="transaction_detail_unit_price_gte_0",
            ),
            models.CheckConstraint(
                condition=Q(total_price__gte=0),
                name="transaction_detail_total_price_gte_0",
            ),
        ]

    def clean(self):
        super().clean()

        if (
            self.transaction_id
            and self.product_id
            and self.transaction.business_id
            != self.product.business_id
        ):
            raise ValidationError({
                "product": (
                    "El producto debe pertenecer al mismo negocio "
                    "de la transacción."
                )
            })

        if (
            self.variant_id
            and self.product_id
            and self.variant.variant_type.product_id
            != self.product_id
        ):
            raise ValidationError({
                "variant": (
                    "La variante seleccionada no pertenece al producto."
                )
            })

    def save(self, *args, **kwargs):
        should_recalculate = (
            self.unit_price is not None
            and self.quantity is not None
        )

        if should_recalculate:
            self.total_price = (
                self.unit_price
                * self.quantity
            ).quantize(
                Decimal("0.01")
            )

        update_fields = kwargs.get("update_fields")

        if (
            update_fields is not None
            and should_recalculate
            and (
                "quantity" in update_fields
                or "unit_price" in update_fields
            )
        ):
            update_fields = set(update_fields)
            update_fields.add("total_price")
            kwargs["update_fields"] = list(
                update_fields
            )

        super().save(*args, **kwargs)
    
class Notification(models.Model):
    NOTIFICATION_TYPES = [
        ('info', 'Info'),
        ('warning', 'Warning'),
        ('alert', 'Alert'),
    ]
    public_id = models.UUIDField(default=uuid.uuid4, unique=True, db_index=True, editable=False)
    title = models.CharField(max_length=255)
    message = models.TextField()
    type = models.CharField(max_length=20, choices=NOTIFICATION_TYPES, default='info')
    user = models.ForeignKey('User', on_delete=models.CASCADE, related_name='notifications')
    business = models.ForeignKey('Business', on_delete=models.CASCADE, related_name='notifications')
    transaction = models.ForeignKey('Transaction', on_delete=models.SET_NULL, null=True, blank=True, related_name='notifications')
    is_read = models.BooleanField(default=False)
    sent_at = models.DateTimeField(null=True, blank=True)
    scheduled_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self):
        kind = dict(self.NOTIFICATION_TYPES).get(self.type, self.type)
        return f"[{kind}] {self.title}"

class Reminder(models.Model):
    public_id = models.UUIDField(default=uuid.uuid4, unique=True, db_index=True, editable=False)
    title = models.CharField(max_length=255)
    description = models.TextField(blank=True)
    due_date = models.DateField(null=True, blank=True)
    is_completed = models.BooleanField(default=False)
    user = models.ForeignKey('User', on_delete=models.CASCADE, related_name='reminders')
    business = models.ForeignKey('Business', on_delete=models.CASCADE, related_name='reminders')
    transaction = models.ForeignKey('Transaction', on_delete=models.SET_NULL, null=True, blank=True, related_name='reminders')
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self):
        due = f" · due {self.due_date}" if self.due_date else ""
        return f"{self.title}{due}"
    
class Debt(models.Model):
    public_id = models.UUIDField(default=uuid.uuid4, unique=True, db_index=True, editable=False)
    transaction = models.OneToOneField(Transaction, on_delete=models.CASCADE, related_name='debts')
    total_amount = models.DecimalField(max_digits=12, decimal_places=2)
    paid_amount = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    interest_rate = models.DecimalField(max_digits=5, decimal_places=2, default=0)
    term_months = models.IntegerField(default=0)
    due_date = models.DateField()
    is_settled = models.BooleanField(default=False)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        constraints = [
            models.CheckConstraint(
                condition=Q(total_amount__gte=0),
                name="debt_total_amount_gte_0",
            ),
            models.CheckConstraint(
                condition=Q(paid_amount__gte=0),
                name="debt_paid_amount_gte_0",
            ),
            models.CheckConstraint(
                condition=Q(paid_amount__lte=models.F("total_amount")),
                name="debt_paid_not_greater_than_total",
            ),
            models.CheckConstraint(
                condition=Q(interest_rate__gte=0),
                name="debt_interest_rate_gte_0",
            ),
            models.CheckConstraint(
                condition=Q(term_months__gte=0),
                name="debt_term_months_gte_0",
            ),
        ]

    def __str__(self):
        ratio = f"{self.paid_amount}/{self.total_amount}"
        return f"Debt {self.public_id} · Tx {self.transaction.public_id} · {ratio}"

class DebtPayment(models.Model):
    public_id = models.UUIDField(default=uuid.uuid4, unique=True, db_index=True, editable=False)
    debt = models.ForeignKey('Debt', on_delete=models.CASCADE, related_name='payments')
    amount = models.DecimalField(max_digits=12, decimal_places=2)
    payment_date = models.DateField()
    transaction = models.ForeignKey('Transaction', on_delete=models.SET_NULL, null=True, blank=True, related_name='debt_payments')
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    payment_method = models.ForeignKey(PaymentMethod, on_delete=models.PROTECT, related_name='debt_payments')

    class Meta:
        constraints = [
            models.CheckConstraint(
                condition=Q(amount__gt=0),
                name="debt_payment_amount_gt_0",
            ),
        ]
        indexes = [
            models.Index(
                fields=["debt", "payment_date"],
                name="debt_payment_debt_date_idx",
            ),
        ]
        ordering = ["-payment_date", "-created_at"]

    def clean(self):
        super().clean()

        if (
            self.debt_id
            and self.payment_method_id
            and self.debt.transaction.business_id
            != self.payment_method.business_id
        ):
            raise ValidationError({
                "payment_method": (
                    "El método de pago debe pertenecer al mismo "
                    "negocio de la deuda."
                )
            })

        if (
            self.debt_id
            and self.transaction_id
            and self.debt.transaction.business_id
            != self.transaction.business_id
        ):
            raise ValidationError({
                "transaction": (
                    "La transacción asociada al pago debe pertenecer "
                    "al mismo negocio de la deuda."
                )
            })

    def __str__(self):
        return f"{self.amount} on {self.payment_date} · Debt {self.debt.public_id}"
    
class Budget(models.Model):
    public_id = models.UUIDField(default=uuid.uuid4, unique=True, db_index=True, editable=False)
    user = models.ForeignKey('User', on_delete=models.CASCADE, related_name='budgets')
    business = models.ForeignKey('Business', on_delete=models.CASCADE, related_name='budgets')
    status = models.ForeignKey('EntityStatus', on_delete=models.PROTECT)
    period_start = models.DateField()
    period_end = models.DateField()
    allocated_amount = models.DecimalField(max_digits=12, decimal_places=2)
    used_amount = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self):
        rng = f"{self.period_start}→{self.period_end}"
        return f"Budget {self.public_id} · {self.business.business_name} · {rng}"


class Goal(models.Model):
    public_id = models.UUIDField(default=uuid.uuid4, unique=True, db_index=True, editable=False)
    user = models.ForeignKey('User', on_delete=models.CASCADE, related_name='goals')
    business = models.ForeignKey('Business', on_delete=models.CASCADE, related_name='goals')
    name = models.CharField(max_length=255)
    description = models.TextField(blank=True)
    target_amount = models.DecimalField(max_digits=12, decimal_places=2)
    current_amount = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    start_date = models.DateField()
    end_date = models.DateField()
    is_completed = models.BooleanField(default=False)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self):
        prog = f"{self.current_amount}/{self.target_amount}"
        return f"{self.name} · {prog}"

class GoalProgress(models.Model):
    public_id = models.UUIDField(default=uuid.uuid4, unique=True, db_index=True, editable=False)
    goal = models.ForeignKey('Goal', on_delete=models.CASCADE, related_name='progress')
    amount = models.DecimalField(max_digits=12, decimal_places=2)
    transaction = models.ForeignKey('Transaction', on_delete=models.SET_NULL, null=True, blank=True, related_name='goal_progress')
    status = models.ForeignKey('EntityStatus', on_delete=models.PROTECT)
    note = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self):
        return f"{self.amount} towards {self.goal.name}"
    
class ActivityLog(models.Model):
    public_id = models.UUIDField(default=uuid.uuid4, unique=True, db_index=True, editable=False)
    user = models.ForeignKey(User, on_delete=models.SET_NULL, null=True, blank=True, related_name='activity_logs')
    business = models.ForeignKey(Business, on_delete=models.SET_NULL, null=True, blank=True, related_name="activity_logs")
    action = models.CharField(max_length=50)
    entity_type = models.CharField(max_length=100)
    entity_id = models.CharField( max_length=100, blank=True)
    metadata = models.JSONField(default=dict, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        indexes = [
            models.Index(
                fields=["business", "created_at"],
            ),
            models.Index(
                fields=["user", "created_at"],
            ),
            models.Index(
                fields=["entity_type", "entity_id"],
            ),
        ]
        ordering = ["-created_at"]

    def __str__(self):
        who = self.user.email if self.user_id else "system"
        entity = (
            f"{self.entity_type}#{self.entity_id}"
            if self.entity_id
            else self.entity_type
        )

        return f"{self.action} {entity} by {who}"

class StockMovement(models.Model):
    public_id = models.UUIDField(default=uuid.uuid4, unique=True, db_index=True, editable=False)
    product = models.ForeignKey('Product', on_delete=models.PROTECT, related_name='stock_movements')
    variant = models.ForeignKey('ProductVariant', on_delete=models.SET_NULL, null=True, blank=True, related_name='stock_movements')
    transaction = models.ForeignKey('Transaction', on_delete=models.SET_NULL, null=True, blank=True, related_name='stock_movements')
    note = models.CharField(max_length=255, blank=True)
    type = models.CharField(max_length=20, choices=[('entry', 'Entry'), ('sale', 'Sale'), ('adjustment', 'Adjustment')])
    quantity = models.IntegerField()
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    created_by = models.ForeignKey(User, on_delete=models.SET_NULL, null=True, blank=True, related_name="created_stock_movements")
    transaction_detail = models.ForeignKey(
        'TransactionDetail', on_delete=models.SET_NULL, null=True, blank=True,
        related_name='stock_movements'
    )

    class Meta:
        constraints = [
            models.CheckConstraint(
                condition=~Q(quantity=0),
                name="stock_movement_quantity_not_zero",
            ),
        ]
        indexes = [
            models.Index(fields=["product", "created_at"]),
            models.Index(fields=["transaction", "created_at"]),
            models.Index(fields=["variant", "created_at"]),
        ]
        ordering = ["-created_at"]
    
    def __str__(self):
        qty = f"{self.quantity:+d}"
        var = ""
        if self.variant_id:
            var = f" · {self.variant.variant_type.name}: {self.variant.label}"
        return f"{self.type} {qty} · {self.product.title}{var}"
    
class EmployeeCommissionPlan(models.Model):
    public_id = models.UUIDField(
        default=uuid.uuid4,
        unique=True,
        db_index=True,
        editable=False,
    )

    employee = models.ForeignKey(
        Employee,
        on_delete=models.CASCADE,
        related_name="commission_plans",
    )

    percentage = models.DecimalField(
        max_digits=5,
        decimal_places=2,
    )

    valid_from = models.DateField()

    valid_until = models.DateField(
        null=True,
        blank=True,
    )

    is_active = models.BooleanField(
        default=True,
    )

    created_at = models.DateTimeField(
        auto_now_add=True,
    )

    updated_at = models.DateTimeField(
        auto_now=True,
    )

    class Meta:
        ordering = [
            "-valid_from",
        ]

        constraints = [
            models.CheckConstraint(
                condition=(
                    models.Q(percentage__gte=0)
                    & models.Q(percentage__lte=100)
                ),
                name=(
                    "employee_commission_percentage_"
                    "between_0_and_100"
                ),
            ),
        ]

    def __str__(self):
        return (
            f"{self.employee.full_name} · "
            f"{self.percentage}%"
        )

class CommissionSettlement(models.Model):
    STATUS_PENDING = "pending"
    STATUS_PAID = "paid"
    STATUS_CANCELLED = "cancelled"

    STATUSES = [
        (STATUS_PENDING, "Pendiente"),
        (STATUS_PAID, "Pagada"),
        (STATUS_CANCELLED, "Cancelada"),
    ]

    public_id = models.UUIDField(
        default=uuid.uuid4,
        unique=True,
        db_index=True,
        editable=False,
    )

    employee = models.ForeignKey(
        Employee,
        on_delete=models.PROTECT,
        related_name="commission_settlements",
    )

    period_start = models.DateField()
    period_end = models.DateField()

    sales_count = models.PositiveIntegerField(
        default=0,
    )

    sales_total = models.DecimalField(
        max_digits=14,
        decimal_places=2,
    )

    commission_percentage = models.DecimalField(
        max_digits=5,
        decimal_places=2,
    )

    commission_total = models.DecimalField(
        max_digits=14,
        decimal_places=2,
    )

    employee_advances = models.DecimalField(
        max_digits=14,
        decimal_places=2,
        default=0,
    )

    employee_repayments = models.DecimalField(
        max_digits=14,
        decimal_places=2,
        default=0,
    )

    advance_balance = models.DecimalField(
        max_digits=14,
        decimal_places=2,
        default=0,
    )

    net_commission_payable = models.DecimalField(
        max_digits=14,
        decimal_places=2,
        default=0,
    )

    remaining_advance_balance = models.DecimalField(
        max_digits=14,
        decimal_places=2,
        default=0,
    )

    status = models.CharField(
        max_length=20,
        choices=STATUSES,
        default=STATUS_PENDING,
    )

    paid_at = models.DateTimeField(
        null=True,
        blank=True,
    )

    created_by = models.ForeignKey(
        User,
        on_delete=models.PROTECT,
        related_name="created_commission_settlements",
    )

    created_at = models.DateTimeField(
        auto_now_add=True,
    )

    updated_at = models.DateTimeField(
        auto_now=True,
    )

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=[
                    "employee",
                    "period_start",
                    "period_end",
                ],
                name=(
                    "unique_employee_commission_"
                    "settlement_per_period"
                ),
            ),
            models.CheckConstraint(
                condition=Q(employee_advances__gte=0),
                name="commission_employee_advances_gte_0",
            ),
            models.CheckConstraint(
                condition=Q(employee_repayments__gte=0),
                name="commission_employee_repayments_gte_0",
            ),
            models.CheckConstraint(
                condition=Q(advance_balance__gte=0),
                name="commission_advance_balance_gte_0",
            ),
            models.CheckConstraint(
                condition=Q(net_commission_payable__gte=0),
                name="commission_net_payable_gte_0",
            ),
            models.CheckConstraint(
                condition=Q(remaining_advance_balance__gte=0),
                name="commission_remaining_advance_gte_0",
            ),
        ]

    def __str__(self):
        return (
            f"{self.employee.full_name} · "
            f"{self.period_start} - {self.period_end}"
        )

class MonthlyClosure(models.Model):
    STATUS_CLOSED = "closed"
    STATUS_REOPENED = "reopened"

    STATUS_CHOICES = [
        (
            STATUS_CLOSED,
            "Cerrado",
        ),
        (
            STATUS_REOPENED,
            "Reabierto",
        ),
    ]

    public_id = models.UUIDField(
        default=uuid.uuid4,
        unique=True,
        db_index=True,
        editable=False,
    )

    business = models.ForeignKey(
        "Business",
        on_delete=models.PROTECT,
        related_name="monthly_closures",
    )

    year = models.PositiveSmallIntegerField()

    month = models.PositiveSmallIntegerField()

    version = models.PositiveIntegerField(
        default=1,
    )

    status = models.CharField(
        max_length=20,
        choices=STATUS_CHOICES,
        default=STATUS_CLOSED,
    )

    # Copia completa del Monthly Summary al momento del cierre.
    summary = models.JSONField()

    closed_by = models.ForeignKey(
        User,
        on_delete=models.PROTECT,
        related_name="closed_monthly_periods",
    )

    closed_at = models.DateTimeField(
        default=django_timezone.now,
    )

    reopened_by = models.ForeignKey(
        User,
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="reopened_monthly_periods",
    )

    reopened_at = models.DateTimeField(
        null=True,
        blank=True,
    )

    reopen_reason = models.TextField(
        blank=True,
    )

    created_at = models.DateTimeField(
        auto_now_add=True,
    )

    updated_at = models.DateTimeField(
        auto_now=True,
    )

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=[
                    "business",
                    "year",
                    "month",
                    "version",
                ],
                name=(
                    "unique_monthly_closure_version"
                ),
            ),
            models.UniqueConstraint(
                fields=[
                    "business",
                    "year",
                    "month",
                ],
                condition=Q(status="closed"),
                name=(
                    "one_active_monthly_closure"
                ),
            ),
            models.CheckConstraint(
                condition=(
                    Q(month__gte=1)
                    & Q(month__lte=12)
                ),
                name=(
                    "monthly_closure_month_1_12"
                ),
            ),
            models.CheckConstraint(
                condition=Q(year__gte=2000),
                name=(
                    "monthly_closure_year_gte_2000"
                ),
            ),
            models.CheckConstraint(
                condition=Q(version__gt=0),
                name=(
                    "monthly_closure_version_gt_0"
                ),
            ),
        ]

        indexes = [
            models.Index(
                fields=[
                    "business",
                    "year",
                    "month",
                ],
                name="monthclose_biz_period_idx",
            ),
            models.Index(
                fields=[
                    "business",
                    "status",
                ],
                name="monthclose_biz_status_idx",
            ),
            models.Index(
                fields=[
                    "closed_at",
                ],
                name="monthclose_closed_at_idx",
            ),
        ]

        ordering = [
            "-year",
            "-month",
            "-version",
        ]

    def clean(self):
        super().clean()

        if self.status == self.STATUS_REOPENED:
            errors = {}

            if self.reopened_by_id is None:
                errors["reopened_by"] = (
                    "Debes indicar quién reabrió "
                    "el cierre mensual."
                )

            if self.reopened_at is None:
                errors["reopened_at"] = (
                    "Debes indicar la fecha de "
                    "reapertura."
                )

            if not self.reopen_reason.strip():
                errors["reopen_reason"] = (
                    "Debes indicar el motivo de "
                    "la reapertura."
                )

            if errors:
                raise ValidationError(errors)

        if (
            self.reopened_at is not None
            and self.reopened_at < self.closed_at
        ):
            raise ValidationError({
                "reopened_at": (
                    "La reapertura no puede ser "
                    "anterior al cierre."
                )
            })

    def __str__(self):
        return (
            f"{self.business.business_name} · "
            f"{self.year}-{self.month:02d} · "
            f"v{self.version} · "
            f"{self.status}"
        )