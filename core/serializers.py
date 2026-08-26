from decimal import Decimal
from django.core.exceptions import ValidationError as DjangoValidationError
from django.db import transaction as db_tx
from django.db.models import Sum, Q
from django.utils import timezone
from drf_spectacular.utils import (
    extend_schema_field,
    extend_schema_serializer,
)
from rest_framework import serializers

from core.services.debt_payments import (
    get_locked_active_payment_method,
    register_debt_payment,
)
from core.services.financial_flows import (
    exclude_terminal_transactions,
    is_terminal_transaction_status,
)
from core.utils import calculate_employee_advance_summary
from .models import (
    BusinessMembership, MonthlyClosure, User, Business, EntityStatus,
    ProductCategory, Product,
    Employee, Customer, Supplier, PaymentMethod,
    Transaction, TransactionDetail, StockMovement,
    Debt, DebtPayment, Notification, Reminder,
    Budget, Goal, GoalProgress,
    CommissionSettlement, EmployeeCommissionPlan,
    CashMovement, CashRegister
)


# ---------- Relaciones mediante public_id ----------
def public_id_field(
    model,
    *,
    source=None,
    required=True,
    allow_null=False,
):
    """Campo relacional que recibe y devuelve el public_id (UUID)."""
    kwargs = {
        "slug_field": "public_id",
        "queryset": model.objects.all(),
        "required": required,
        "allow_null": allow_null,
    }

    if source is not None:
        kwargs["source"] = source

    return serializers.SlugRelatedField(
        **kwargs,
    )


def public_id_read_only(
    *,
    source=None,
    allow_null=False,
):
    """Campo relacional de solo lectura representado por public_id."""
    kwargs = {
        "slug_field": "public_id",
        "read_only": True,
        "allow_null": allow_null,
    }

    if source is not None:
        kwargs["source"] = source

    return serializers.SlugRelatedField(**kwargs)


class SecurePublicIdRelatedField(
    serializers.SlugRelatedField
):
    default_error_messages = {
        **serializers.SlugRelatedField.default_error_messages,
        "does_not_exist": (
            "La relación indicada no es válida."
        ),
    }


def secure_public_id_field(
    model,
    *,
    source=None,
    required=True,
    allow_null=False,
):
    kwargs = {
        "slug_field": "public_id",
        "queryset": model.objects.all(),
        "required": required,
        "allow_null": allow_null,
    }

    if source is not None:
        kwargs["source"] = source

    return SecurePublicIdRelatedField(
        **kwargs,
    )


def active_membership_business_ids(context):
    request = context.get("request")
    user = getattr(request, "user", None)

    if (
        user is not None
        and user.is_authenticated
        and user.is_superuser
    ):
        return None

    if user is None or not user.is_authenticated:
        return (
            Business.objects.none()
            .values_list("pk", flat=True)
        )

    return (
        BusinessMembership.objects
        .filter(
            user=user,
            is_active=True,
        )
        .values_list("business_id", flat=True)
    )

def related_name_field(
    source,
    *,
    allow_null=False,
):
    return serializers.CharField(
        source=source,
        read_only=True,
        allow_null=allow_null,
    )

def get_active_status():
    active = EntityStatus.objects.filter(name__iexact="Activo").first()
    if active is None:
        raise serializers.ValidationError({
            "status_public_id": (
                "No existe el estado inicial 'Activo'. "
                "Ejecuta el comando seed_statuses."
            )
        })
    return active


class DefaultActiveStatusMixin:
    """Asigna el estado Activo cuando el cliente no envía status."""

    def create(self, validated_data):
        validated_data.setdefault("status", get_active_status())
        return super().create(validated_data)


from core.api.serializers.auth import (
    HealthSerializer,
    RegisterSerializer,
)

# ---------- Usuarios ----------
class UserSerializer(serializers.ModelSerializer):
    class Meta:
        model = User
        fields = ("public_id", "email", "full_name", "phone", "role", "is_active", "created_at", "updated_at")
        read_only_fields = ("public_id", "is_active", "created_at", "updated_at")

class EmployeeAccessCreateSerializer(serializers.Serializer):
    """
    Crea en una sola operación:

    - La cuenta User del colaborador.
    - Su registro Employee dentro del negocio.
    - La BusinessMembership que controla su acceso y permisos.

    Este serializer no devuelve tokens. El colaborador debe autenticarse
    posteriormente mediante el endpoint normal de login.
    """

    email = serializers.EmailField()
    password = serializers.CharField(
        write_only=True,
        min_length=8,
        trim_whitespace=False,
    )
    full_name = serializers.CharField(max_length=255)
    position = serializers.CharField(max_length=100)
    phone = serializers.CharField(
        max_length=50,
        required=False,
        allow_blank=True,
        default="",
    )
    role = serializers.ChoiceField(
        choices=[
            (BusinessMembership.ROLE_ADMIN, "admin"),
            (BusinessMembership.ROLE_CASHIER, "cashier"),
            (BusinessMembership.ROLE_SELLER, "seller"),
            (BusinessMembership.ROLE_INVENTORY, "inventory"),
            (BusinessMembership.ROLE_VIEWER, "viewer"),
        ]
    )

    def validate_email(self, value):
        email = value.strip().lower()

        if User.objects.filter(email__iexact=email).exists():
            raise serializers.ValidationError(
                "Ya existe un usuario con este correo."
            )

        return email

    def validate(self, attrs):
        business = self.context.get("business")

        if business is None:
            raise serializers.ValidationError({
                "business": "No se proporcionó un negocio válido."
            })

        return attrs

    @db_tx.atomic
    def create(self, validated_data):
        business = self.context["business"]
        password = validated_data.pop("password")
        membership_role = validated_data.pop("role")
        email = validated_data.pop("email")
        full_name = validated_data.pop("full_name")
        phone = validated_data.pop("phone", "")
        position = validated_data.pop("position")

        active_status = EntityStatus.objects.filter(
            name__iexact="Activo"
        ).first()

        if active_status is None:
            raise serializers.ValidationError({
                "status": (
                    "No existe el estado inicial 'Activo'. "
                    "Ejecuta el comando seed_statuses."
                )
            })

        # User.role se conserva por compatibilidad con el modelo actual.
        # La autorización real dentro del negocio depende exclusivamente
        # de BusinessMembership.role.
        legacy_user_role = getattr(
            getattr(User, "Roles", None),
            "EMPLOYEE",
            "employee",
        )

        user = User.objects.create_user(
            email=email,
            full_name=full_name,
            password=password,
            phone=phone,
            role=legacy_user_role,
        )

        employee = Employee.objects.create(
            business=business,
            full_name=full_name,
            phone=phone,
            position=position,
            status=active_status,
        )

        return BusinessMembership.objects.create(
            user=user,
            business=business,
            employee=employee,
            role=membership_role,
            is_active=True,
        )

# ---------- Catálogos/Estados ----------
class EntityStatusSerializer(serializers.ModelSerializer):
    class Meta:
        model = EntityStatus
        fields = ("public_id", "name")
        read_only_fields = ("public_id",)

class PaymentMethodSerializer(
    DefaultActiveStatusMixin,
    serializers.ModelSerializer,
):
    business_public_id = public_id_field(
        Business,
        source="business",
    )
    status_public_id = public_id_field(
        EntityStatus,
        source="status",
        required=False,
    )
    status_name = related_name_field("status.name")

    class Meta:
        model = PaymentMethod
        fields = (
            "public_id",
            "business_public_id",
            "name",
            "method_type",
            "status_public_id",
            "status_name",
        )
        read_only_fields = ("public_id",)
        extra_kwargs = {
            "method_type": {
                "required": True,
            },
        }

class BusinessSerializer(
    DefaultActiveStatusMixin,
    serializers.ModelSerializer,
):
    status_public_id = public_id_field(
        EntityStatus,
        source="status",
        required=False,
    )
    status_name = related_name_field("status.name")
    class Meta:
        model = Business
        fields = (
            "public_id",
            "business_name",
            "description",
            "currency",
            "status_public_id",
            "status_name",
            "created_at",
            "updated_at",
        )
        read_only_fields = (
            "public_id",
            "created_at",
            "updated_at",
        )

class BusinessMembershipSerializer(serializers.ModelSerializer):
    user_email = serializers.EmailField(
        source="user.email",
        read_only=True,
    )
    business_public_id = public_id_read_only(
        source="business",
    )
    business_name = serializers.CharField(
        source="business.business_name",
        read_only=True,
    )
    employee_public_id = public_id_read_only(
        source="employee",
        allow_null=True,
    )
    employee_name = serializers.CharField(
        source="employee.full_name",
        read_only=True,
        allow_null=True,
    )
    role_display = serializers.CharField(
        source="get_role_display",
        read_only=True,
    )

    class Meta:
        model = BusinessMembership
        fields = (
            "public_id",
            "user_email",
            "business_public_id",
            "business_name",
            "employee_public_id",
            "employee_name",
            "role",
            "role_display",
            "is_active",
            "created_at",
            "updated_at",
        )
        read_only_fields = fields

class BusinessMembershipUpdateSerializer(
    serializers.ModelSerializer
):
    role = serializers.ChoiceField(
        choices=[
            (
                BusinessMembership.ROLE_ADMIN,
                "Administrador",
            ),
            (
                BusinessMembership.ROLE_CASHIER,
                "Cajero",
            ),
            (
                BusinessMembership.ROLE_SELLER,
                "Vendedor",
            ),
            (
                BusinessMembership.ROLE_INVENTORY,
                "Inventario",
            ),
            (
                BusinessMembership.ROLE_VIEWER,
                "Solo lectura",
            ),
        ],
        required=False,
    )

    is_active = serializers.BooleanField(
        required=False,
    )

    class Meta:
        model = BusinessMembership

        fields = (
            "role",
            "is_active",
        )

    def validate(self, attrs):
        if not attrs:
            raise serializers.ValidationError(
                "Debe proporcionar al menos un campo "
                "para actualizar."
            )

        request = self.context.get("request")
        membership = self.instance

        if request is None:
            raise serializers.ValidationError(
                "No se encontró el contexto de la solicitud."
            )

        current_user = request.user

        if membership.user_id == current_user.id:
            raise serializers.ValidationError(
                "No puedes modificar tu propia membresía."
            )

        current_membership = (
            BusinessMembership.objects
            .filter(
                user=current_user,
                business=membership.business,
                is_active=True,
            )
            .first()
        )

        if (
            not current_user.is_superuser
            and current_membership is None
        ):
            raise serializers.ValidationError(
                "No tienes una membresía activa "
                "en este negocio."
            )

        if (
            not current_user.is_superuser
            and current_membership.role
            not in {
                BusinessMembership.ROLE_OWNER,
                BusinessMembership.ROLE_ADMIN,
            }
        ):
            raise serializers.ValidationError(
                "No tienes permiso para administrar "
                "membresías."
            )

        if (
            membership.role
            == BusinessMembership.ROLE_OWNER
            and not current_user.is_superuser
        ):
            raise serializers.ValidationError(
                "La membresía del propietario no puede "
                "modificarse desde este endpoint."
            )

        requested_role = attrs.get("role")

        if (
            requested_role
            == BusinessMembership.ROLE_OWNER
        ):
            raise serializers.ValidationError({
                "role": (
                    "No se puede asignar el rol de "
                    "propietario desde este endpoint."
                )
            })

        if (
            not current_user.is_superuser
            and current_membership.role
            == BusinessMembership.ROLE_ADMIN
            and membership.role
            == BusinessMembership.ROLE_ADMIN
        ):
            raise serializers.ValidationError(
                "Un administrador no puede modificar "
                "a otro administrador."
            )

        return attrs

# ---------- Productos ----------
class ProductCategorySerializer(
    DefaultActiveStatusMixin,
    serializers.ModelSerializer,
):
    business_public_id = public_id_field(
        Business,
        source="business",
    )
    status_public_id = public_id_field(
        EntityStatus,
        source="status",
        required=False,
    )
    status_name = related_name_field("status.name")
    class Meta:
        model = ProductCategory
        fields = (
            "public_id",
            "business_public_id",
            "name",
            "status_public_id",
            "status_name",
            "created_at",
            "updated_at",
        )
        read_only_fields = (
            "public_id",
            "created_at",
            "updated_at",
        )

class ProductSerializer(
    DefaultActiveStatusMixin,
    serializers.ModelSerializer,
):
    business_public_id = public_id_field(
        Business,
        source="business",
    )

    category_public_id = public_id_field(
        ProductCategory,
        source="category",
        required=False,
        allow_null=True,
    )

    category_name = serializers.CharField(
        source="category.name",
        read_only=True,
        allow_null=True,
    )

    status_public_id = public_id_field(
        EntityStatus,
        source="status",
        required=False,
    )

    status_name = serializers.CharField(
        source="status.name",
        read_only=True,
    )

    class Meta:
        model = Product

        fields = (
            "public_id",
            "business_public_id",

            "category_public_id",
            "category_name",

            "title",
            "description",
            "image_url",

            "base_price",
            "base_cost",
            "stock",
            "is_visible",

            "status_public_id",
            "status_name",

            "created_at",
            "updated_at",
        )

        read_only_fields = (
            "public_id",
            "category_name",
            "status_name",
            "created_at",
            "updated_at",
        )
    
    def validate(self, attrs):
        if (
            self.instance is not None
            and "stock" in attrs
            and attrs["stock"] != self.instance.stock
        ):
            raise serializers.ValidationError({
                "stock": (
                    "El stock de un producto existente solo puede "
                    "cambiar mediante movimientos de inventario."
                )
            })

        business = attrs.get(
            "business",
            getattr(self.instance, "business", None),
        )
        category = attrs.get(
            "category",
            getattr(self.instance, "category", None),
        )

        if (
            business is not None
            and category is not None
            and category.business_id != business.id
        ):
            raise serializers.ValidationError({
                "category_public_id": (
                    "La categoría no pertenece al negocio seleccionado."
                )
            })

        return attrs

class PublicProductCategorySerializer(
    serializers.ModelSerializer,
):
    business_public_id = public_id_read_only(
        source="business",
    )

    class Meta:
        model = ProductCategory
        fields = (
            "public_id",
            "business_public_id",
            "name",
        )
        read_only_fields = fields

class PublicProductSerializer(
    serializers.ModelSerializer,
):
    business_public_id = public_id_read_only(
        source="business",
    )
    category_public_id = public_id_read_only(
        source="category",
        allow_null=True,
    )
    category_name = related_name_field(
        "category.name",
        allow_null=True,
    )

    class Meta:
        model = Product
        fields = (
            "public_id",
            "business_public_id",
            "category_public_id",
            "category_name",
            "title",
            "description",
            "image_url",
            "base_price",
            "stock",
        )
        read_only_fields = fields

class EmployeeSerializer(
    DefaultActiveStatusMixin,
    serializers.ModelSerializer,
):
    business_public_id = public_id_field(
        Business,
        source="business",
    )
    status_public_id = public_id_field(
        EntityStatus,
        source="status",
        required=False,
    )
    status_name = related_name_field("status.name")
    class Meta:
        model = Employee
        fields = ("public_id", "business_public_id", "full_name", "phone", "email", "position", "status_public_id", "status_name", "created_at", "updated_at")
        read_only_fields = (
            "public_id",
            "created_at",
            "updated_at",
        )


class EmployeeSelectionSerializer(serializers.ModelSerializer):
    """Minimal employee identity exposed to operational sales roles."""

    class Meta:
        model = Employee
        fields = (
            "public_id",
            "full_name",
            "position",
        )
        read_only_fields = fields

class CustomerSerializer(
    DefaultActiveStatusMixin,
    serializers.ModelSerializer,
):
    business_public_id = public_id_field(
        Business,
        source="business",
    )
    status_public_id = public_id_field(
        EntityStatus,
        source="status",
        required=False,
    )
    status_name = related_name_field("status.name")
    class Meta:
        model = Customer
        fields = ("public_id", "business_public_id", "full_name", "phone", "email", "status_public_id", "status_name", "created_at", "updated_at")
        read_only_fields = (
            "public_id",
            "created_at",
            "updated_at",
        )

class SupplierSerializer(
    DefaultActiveStatusMixin,
    serializers.ModelSerializer,
):
    business_public_id = public_id_field(
        Business,
        source="business",
    )
    status_public_id = public_id_field(
        EntityStatus,
        source="status",
        required=False,
    )
    status_name = related_name_field("status.name")
    class Meta:
        model = Supplier
        fields = ("public_id", "business_public_id", "name", "phone", "email", "status_public_id", "status_name", "created_at", "updated_at")
        read_only_fields = (
            "public_id",
            "created_at",
            "updated_at",
        )

class TransactionDetailSerializer(
    serializers.ModelSerializer
):
    product_public_id = secure_public_id_field(
        Product,
        source="product",
    )

    quantity = serializers.IntegerField(
        min_value=1,
        max_value=100_000,
    )

    unit_price = serializers.DecimalField(
        max_digits=12,
        decimal_places=2,
        required=False,
        allow_null=True,
    )

    product_name = serializers.CharField(
        source="product.title",
        read_only=True,
    )

    class Meta:
        model = TransactionDetail

        fields = (
            "public_id",
            "product_public_id",
            "product_name",
            "quantity",
            "unit_price",
            "total_price",
        )

        read_only_fields = (
            "public_id",
            "product_name",
            "total_price",
        )

class TransactionSerializer(
    serializers.ModelSerializer
):
    business_public_id = secure_public_id_field(
        Business,
        source="business",
    )
    customer_public_id = secure_public_id_field(
        Customer,
        source="customer",
        required=False,
        allow_null=True,
    )
    supplier_public_id = secure_public_id_field(
        Supplier,
        source="supplier",
        required=False,
        allow_null=True,
    )
    employee_public_id = secure_public_id_field(
        Employee,
        source="employee",
        required=False,
        allow_null=True
    )
    payment_method_public_id = secure_public_id_field(
        PaymentMethod,
        source="payment_method",
        required=False,
        allow_null=True,
    )
    status_public_id = public_id_field(
        EntityStatus,
        source="status",
        required=False,
    )

    details = TransactionDetailSerializer(
        many=True,
        required=False,
    )

    expense_amount = serializers.DecimalField(
        max_digits=12,
        decimal_places=2,
        min_value=Decimal("0.01"),
        write_only=True,
        required=False,
    )

    initial_paid_amount = serializers.DecimalField(
        max_digits=12,
        decimal_places=2,
        write_only=True,
        required=False,
        help_text=(
            "Pago inicial usado exclusivamente al crear una "
            "transacción partial; se registra atómicamente."
        ),
    )

    business_currency = serializers.CharField(
        source="business.currency",
        read_only=True,
    )

    created_by_email = serializers.EmailField(
        source="created_by.email",
        read_only=True,
    )

    updated_by_email = serializers.EmailField(
        source="updated_by.email",
        read_only=True,
        allow_null=True,
    )

    business_name = serializers.CharField(
        source="business.business_name",
        read_only=True,
    )

    customer_name = serializers.CharField(
        source="customer.full_name",
        read_only=True,
        allow_null=True,
    )

    supplier_name = serializers.CharField(
        source="supplier.name",
        read_only=True,
        allow_null=True,
    )

    employee_name = serializers.CharField(
        source="employee.full_name",
        read_only=True,
        allow_null=True,
    )

    payment_method_name = serializers.CharField(
        source="payment_method.name",
        read_only=True,
        allow_null=True,
    )

    status_name = serializers.CharField(
        source="status.name",
        read_only=True,
    )
    class Meta:
        model = Transaction
        fields = (
            "public_id",
            "business_public_id",
            "business_name",
            "customer_public_id",
            "customer_name",
            "supplier_public_id",
            "supplier_name",
            "employee_public_id",
            "employee_name",
            "payment_method_public_id",
            "payment_method_name",
            "type",
            "is_debt",
            "discount_percent",
            "concept",
            "total_value",
            "expense_amount",
            "initial_paid_amount",
            "status_public_id",
            "status_name",
            "invoice_number",
            "payment_status",
            "invoice_series",
            "invoice_file_url",
            "details",
            "business_currency",
            "created_by_email",
            "updated_by_email",
            "created_at",
            "updated_at",
        )

        read_only_fields = (
            "public_id",
            "total_value",
            "is_debt",
            "created_at",
            "updated_at",
        )

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)

        business_ids = active_membership_business_ids(
            self.context
        )

        if business_ids is None:
            return

        self.fields["business_public_id"].queryset = (
            Business.objects.filter(pk__in=business_ids)
        )
        self.fields["customer_public_id"].queryset = (
            Customer.objects.filter(
                business_id__in=business_ids,
            )
        )
        self.fields["supplier_public_id"].queryset = (
            Supplier.objects.filter(
                business_id__in=business_ids,
            )
        )
        self.fields["employee_public_id"].queryset = (
            Employee.objects.filter(
                business_id__in=business_ids,
            )
        )
        self.fields[
            "payment_method_public_id"
        ].queryset = PaymentMethod.objects.filter(
            business_id__in=business_ids,
        )
        self.fields["details"].child.fields[
            "product_public_id"
        ].queryset = Product.objects.filter(
            business_id__in=business_ids,
        )

    def validate(self, attrs):
        if (
            self.instance is not None
            and is_terminal_transaction_status(
                self.instance.status
            )
        ):
            requested_status = attrs.get("status")

            if (
                requested_status is not None
                and requested_status.pk
                != self.instance.status_id
            ):
                raise serializers.ValidationError({
                    "status_public_id": (
                        "Una transacción anulada no puede "
                        "reactivarse mediante PUT o PATCH."
                    )
                })

            raise serializers.ValidationError({
                "non_field_errors": (
                    "Una transacción anulada no puede "
                    "modificarse mediante PUT o PATCH."
                )
            })

        transaction_type = attrs.get(
            "type",
            getattr(
                self.instance,
                "type",
                None,
            ),
        )

        valid_transaction_types = dict(
            Transaction.TRANSACTION_TYPES
        )

        if transaction_type not in valid_transaction_types:
            raise serializers.ValidationError({
                "type": (
                    "Tipo de transacción inválido."
                )
            })

        business = attrs.get(
            "business",
            getattr(
                self.instance,
                "business",
                None,
            ),
        )

        if business is None:
            raise serializers.ValidationError({
                "business_public_id": (
                    "Este campo es requerido."
                )
            })

        employee = attrs.get(
            "employee",
            getattr(
                self.instance,
                "employee",
                None,
            ),
        )

        if (
            transaction_type == "sale"
            and employee is None
        ):
            raise serializers.ValidationError({
                "employee_public_id": (
                    "Debe indicar el empleado al que "
                    "pertenece la venta."
                )
            })

        self._validate_related_business(
            attrs=attrs,
            business=business,
        )

        details = attrs.get("details")
        expense_amount = attrs.get(
            "expense_amount"
        )

        self._validate_transaction_content(
            transaction_type=transaction_type,
            details=details,
            expense_amount=expense_amount,
        )

        self._validate_products_business(
            details=details,
            business=business,
        )

        self._validate_expense_discount(
            attrs=attrs,
            transaction_type=transaction_type,
        )

        self._normalize_payment_status(
            attrs=attrs,
        )

        self._validate_initial_payment_structure(
            attrs=attrs,
            transaction_type=transaction_type,
        )

        return attrs

    def _validate_initial_payment_structure(
        self,
        *,
        attrs,
        transaction_type,
    ):
        if self.instance is not None:
            if "initial_paid_amount" in attrs:
                raise serializers.ValidationError({
                    "initial_paid_amount": (
                        "Este campo solo puede utilizarse al crear "
                        "una transacción."
                    ),
                })
            return

        payment_status = attrs["payment_status"]
        payment_method = attrs.get("payment_method")
        initial_provided = "initial_paid_amount" in attrs

        if (
            transaction_type == "expense"
            and payment_status != "paid"
        ):
            raise serializers.ValidationError({
                "payment_status": (
                    "Los gastos solo admiten el estado paid."
                ),
            })

        if payment_status == "pending":
            if payment_method is not None:
                raise serializers.ValidationError({
                    "payment_method_public_id": (
                        "Debe omitirse para una transacción pendiente."
                    ),
                })
        if payment_status == "partial":
            if payment_method is None:
                raise serializers.ValidationError({
                    "payment_method_public_id": (
                        "Este campo es requerido para una "
                        "transacción parcial."
                    ),
                })
            if not initial_provided:
                raise serializers.ValidationError({
                    "initial_paid_amount": (
                        "Este campo es requerido para una "
                        "transacción parcial."
                    ),
                })

    def _validate_related_business(
        self,
        attrs,
        business,
    ):
        related_fields = (
            "customer",
            "supplier",
            "employee",
            "payment_method",
        )

        for field in related_fields:
            obj = attrs.get(
                field,
                getattr(
                    self.instance,
                    field,
                    None,
                )
                if self.instance
                else None,
            )

            if obj is None:
                continue

            if obj.business_id != business.id:
                if field == "payment_method":
                    raise serializers.ValidationError({
                        "payment_method_public_id": (
                            "La relación indicada no es válida."
                        ),
                    })
                raise serializers.ValidationError({
                    f"{field}_public_id": (
                        f"El recurso indicado en {field} "
                        "no pertenece al negocio "
                        "seleccionado."
                    )
                })

    def _validate_transaction_content(
        self,
        transaction_type,
        details,
        expense_amount,
    ):
        if self.instance is not None:
            return

        if transaction_type in {
            "sale",
            "purchase",
        }:
            if not details:
                raise serializers.ValidationError({
                    "details": (
                        "Las ventas y compras deben "
                        "incluir al menos un detalle."
                    )
                })

            if expense_amount is not None:
                raise serializers.ValidationError({
                    "expense_amount": (
                        "Este campo solo puede utilizarse "
                        "en transacciones de tipo expense."
                    )
                })

        if transaction_type == "expense":
            if details:
                raise serializers.ValidationError({
                    "details": (
                        "Los gastos generales no deben "
                        "incluir productos."
                    )
                })

            if expense_amount is None:
                raise serializers.ValidationError({
                    "expense_amount": (
                        "Debe indicar el monto del gasto."
                    )
                })

    def _validate_products_business(
        self,
        details,
        business,
    ):
        if details is None:
            return

        for index, detail in enumerate(
            details,
            start=1,
        ):
            product = detail.get("product")

            if (
                product is not None
                and product.business_id
                != business.id
            ):
                raise serializers.ValidationError({
                    "details": (
                        f"Detalle #{index}: el producto "
                        "no pertenece al negocio "
                        "seleccionado."
                    )
                })

            if (
                self.instance is None
                and product is not None
                and product.status.name.casefold()
                != "activo".casefold()
            ):
                raise serializers.ValidationError({
                    "details": (
                        f"Detalle #{index}: el producto "
                        "no se encuentra Activo."
                    )
                })

    def _validate_expense_discount(
        self,
        attrs,
        transaction_type,
    ):
        discount = attrs.get(
            "discount_percent",
            getattr(
                self.instance,
                "discount_percent",
                None,
            ),
        )

        if (
            transaction_type == "expense"
            and discount is not None
            and discount > Decimal("0.00")
        ):
            raise serializers.ValidationError({
                "discount_percent": (
                    "No se puede aplicar descuento "
                    "a un gasto general."
                )
            })

    def _normalize_payment_status(
        self,
        attrs,
    ):
        payment_status_provided = (
            "payment_status" in attrs
        )

        if payment_status_provided:
            payment_status = attrs.get(
                "payment_status"
            )

            if isinstance(
                payment_status,
                str,
            ):
                payment_status = (
                    payment_status
                    .strip()
                    .lower()
                )

            if not payment_status:
                raise serializers.ValidationError({
                    "payment_status": (
                        "El estado de pago no puede "
                        "estar vacío."
                    )
                })

            valid_payment_statuses = dict(
                Transaction.PAYMENT_STATUSES
            )

            if (
                payment_status
                not in valid_payment_statuses
            ):
                raise serializers.ValidationError({
                    "payment_status": (
                        "El estado de pago no "
                        "es válido."
                    )
                })

            attrs["payment_status"] = (
                payment_status
            )

            attrs["is_debt"] = (
                payment_status
                in {
                    "partial",
                    "pending",
                }
            )

        elif self.instance is None:
            attrs["payment_status"] = "paid"
            attrs["is_debt"] = False

    def validate_discount_percent(
        self,
        value,
    ):
        if value is None:
            return value

        if not (
            Decimal("0.00")
            <= value
            <= Decimal("100.00")
        ):
            raise serializers.ValidationError(
                "El descuento debe estar "
                "entre 0 y 100."
            )

        return value

    def validate_details(
        self,
        value,
    ):
        for index, detail in enumerate(
            value,
            start=1,
        ):
            product = detail.get("product")
            quantity = detail.get("quantity")
            unit_price = detail.get("unit_price")

            if product is None:
                raise serializers.ValidationError(
                    f"Detalle #{index}: debe incluir "
                    "un producto."
                )

            if (
                quantity is None
                or quantity <= 0
            ):
                raise serializers.ValidationError(
                    f"Detalle #{index}: quantity debe "
                    "ser mayor que 0."
                )

            if (
                unit_price is not None
                and unit_price < Decimal("0.00")
            ):
                raise serializers.ValidationError(
                    f"Detalle #{index}: unit_price no "
                    "puede ser negativo."
                )

        return value
    
    def update(
        self,
        instance,
        validated_data,
    ):
        if "details" in validated_data:
            raise serializers.ValidationError({
                "details": (
                    "La actualización de los detalles "
                    "todavía no está habilitada."
                )
            })

        if "payment_status" in validated_data:
            raise serializers.ValidationError({
                "payment_status": (
                    "El estado de pago debe modificarse "
                    "mediante el módulo de pagos "
                    "o deudas."
                )
            })

        if "expense_amount" in validated_data:
            raise serializers.ValidationError({
                "expense_amount": (
                    "La modificación del monto de un "
                    "gasto todavía no está habilitada."
                )
            })

        new_business = validated_data.get(
            "business"
        )

        if (
            new_business is not None
            and new_business.pk
            != instance.business_id
        ):
            raise serializers.ValidationError({
                "business_public_id": (
                    "No se puede cambiar el negocio "
                    "de una transacción existente."
                )
            })

        new_type = validated_data.get("type")

        if (
            new_type is not None
            and new_type != instance.type
        ):
            raise serializers.ValidationError({
                "type": (
                    "No se puede cambiar el tipo "
                    "de una transacción existente."
                )
            })

        return super().update(
            instance,
            validated_data,
        )

    @db_tx.atomic
    def create(
        self,
        validated_data,
    ):
        initial_amount_provided = (
            "initial_paid_amount" in validated_data
        )
        initial_paid_amount = validated_data.pop(
            "initial_paid_amount",
            None,
        )
        details_data = validated_data.pop(
            "details",
            [],
        )

        expense_amount = validated_data.pop(
            "expense_amount",
            None,
        )
        submitted_payment_method = validated_data.pop(
            "payment_method",
            None,
        )

        self._set_default_status(
            validated_data
        )

        transaction = Transaction.objects.create(
            **validated_data,
            total_value=Decimal("0.00"),
        )

        if transaction.type == "expense":
            transaction.total_value = (
                expense_amount.quantize(
                    Decimal("0.01")
                )
            )
        else:
            transaction.total_value = (
                self._create_details_and_calculate_total(
                    transaction=transaction,
                    details_data=details_data,
                )
            )

        self._validate_final_payment_contract(
            transaction=transaction,
            payment_method=submitted_payment_method,
            initial_amount_provided=(
                initial_amount_provided
            ),
            initial_paid_amount=initial_paid_amount,
        )

        transaction.save(
            update_fields=[
                "total_value",
            ]
        )

        self._create_financial_records(
            transaction=transaction,
            payment_method=submitted_payment_method,
            initial_paid_amount=initial_paid_amount,
        )

        return transaction

    def _validate_final_payment_contract(
        self,
        *,
        transaction,
        payment_method,
        initial_amount_provided,
        initial_paid_amount,
    ):
        total = transaction.total_value
        payment_status = transaction.payment_status

        if total == Decimal("0.00"):
            if payment_status != "paid":
                raise serializers.ValidationError({
                    "payment_status": (
                        "Una transacción con total cero solo admite paid."
                    ),
                })
            if payment_method is not None:
                raise serializers.ValidationError({
                    "payment_method_public_id": (
                        "Debe omitirse cuando el total es cero."
                    ),
                })
            if (
                initial_amount_provided
                and initial_paid_amount != Decimal("0.00")
            ):
                raise serializers.ValidationError({
                    "initial_paid_amount": (
                        "Debe ser cero cuando el total es cero."
                    ),
                })
            transaction.is_debt = False
            return

        if payment_status == "paid":
            if initial_amount_provided:
                raise serializers.ValidationError({
                    "initial_paid_amount": (
                        "Este campo debe omitirse para una "
                        "transacción pagada."
                    ),
                })
            if payment_method is None:
                raise serializers.ValidationError({
                    "payment_method_public_id": (
                        "Este campo es requerido para una "
                        "transacción pagada."
                    ),
                })
            transaction.is_debt = False
            return

        if payment_status == "pending":
            if (
                initial_amount_provided
                and initial_paid_amount != Decimal("0.00")
            ):
                raise serializers.ValidationError({
                    "initial_paid_amount": (
                        "Debe ser cero para una transacción pendiente."
                    ),
                })
            transaction.is_debt = True
            return

        if initial_paid_amount <= Decimal("0.00"):
            raise serializers.ValidationError({
                "initial_paid_amount": (
                    "Debe ser mayor que cero."
                ),
            })

        if initial_paid_amount >= total:
            raise serializers.ValidationError({
                "initial_paid_amount": (
                    "Debe ser menor que el total de la transacción."
                ),
            })

        transaction.is_debt = True

    def _set_default_status(
        self,
        validated_data,
    ):
        if validated_data.get("status"):
            return

        active_status = (
            EntityStatus.objects
            .filter(
                name__iexact="Activo",
            )
            .first()
        )

        if active_status is None:
            raise serializers.ValidationError({
                "status_public_id": (
                    'No existe el estado "Activo".'
                )
            })

        validated_data["status"] = (
            active_status
        )

    def _create_details_and_calculate_total(
        self,
        transaction,
        details_data,
    ):
        total = Decimal("0.00")

        for index, detail_data in enumerate(
            details_data,
            start=1,
        ):
            product = detail_data["product"]
            quantity = detail_data["quantity"]
            unit_price = detail_data.get("unit_price")

            if unit_price is None:
                if transaction.type == "purchase":
                    unit_price = product.base_cost
                else:
                    unit_price = product.base_price

            if unit_price < Decimal("0.00"):
                raise serializers.ValidationError({
                    "details": (
                        f"Detalle #{index}: el precio "
                        "no puede ser negativo."
                    )
                })

            detail = (
                TransactionDetail.objects.create(
                    transaction=transaction,
                    product=product,
                    quantity=quantity,
                    unit_price=unit_price,
                    total_price=Decimal("0.00"),
                )
            )

            total += detail.total_price

        discount = (
            transaction.discount_percent
            or Decimal("0.00")
        )

        if discount > Decimal("0.00"):
            multiplier = (
                Decimal("1.00")
                - (
                    discount
                    / Decimal("100.00")
                )
            )

            total *= multiplier

        return total.quantize(
            Decimal("0.01")
        )

    def _create_financial_records(
        self,
        *,
        transaction,
        payment_method,
        initial_paid_amount,
    ):
        if (
            transaction.total_value > Decimal("0.00")
            and transaction.payment_status == "paid"
        ):
            locked_method = get_locked_active_payment_method(
                payment_method_id=payment_method.pk,
                business_id=transaction.business_id,
            )
            transaction.payment_method = locked_method
            transaction.save(
                update_fields=[
                    "payment_method",
                    "updated_at",
                ],
            )
            return

        if not transaction.is_debt:
            return

        debt = Debt.objects.create(
            transaction=transaction,
            total_amount=(
                transaction.total_value
            ),
            paid_amount=Decimal("0.00"),
            interest_rate=Decimal("0.00"),
            term_months=0,
            due_date=timezone.localdate(),
            is_settled=False,
        )

        if transaction.payment_status != "partial":
            return

        payment = register_debt_payment(
            debt_id=debt.pk,
            amount=initial_paid_amount,
            payment_date=timezone.localdate(),
            payment_method_id=payment_method.pk,
            actor=transaction.created_by,
            submitted_transaction_id=transaction.pk,
            observed_remaining_amount=transaction.total_value,
        )
        transaction.payment_method = payment.payment_method
        transaction.save(
            update_fields=[
                "payment_method",
                "updated_at",
            ],
        )

@extend_schema_field(
    {
        "type": "string",
        "enum": [
            "sale",
            "purchase",
            "expense",
        ],
    },
    component_name="TransactionUpdateType",
)
class TransactionUpdateTypeSchemaField(
    serializers.ChoiceField
):
    pass


@extend_schema_serializer(
    exclude_fields=(
        "details",
        "expense_amount",
        "initial_paid_amount",
        "payment_status",
    ),
)
class TransactionUpdateSchemaSerializer(
    TransactionSerializer
):
    """OpenAPI-only request shape for PUT/PATCH."""

    type = TransactionUpdateTypeSchemaField(
        choices=Transaction.TRANSACTION_TYPES,
        required=True,
    )


# ---------- Pagos de Deuda ----------
class DebtSerializer(serializers.ModelSerializer):
    business_public_id = public_id_read_only(
        source="transaction.business",
    )
    transaction_public_id = public_id_field(
        Transaction,
        source="transaction",
    )
    customer_public_id = serializers.UUIDField(
        source="transaction.customer.public_id",
        read_only=True,
        allow_null=True,
    )
    supplier_public_id = serializers.UUIDField(
        source="transaction.supplier.public_id",
        read_only=True,
        allow_null=True,
    )
    transaction_status_name = serializers.CharField(
        source="transaction.status.name",
        read_only=True,
    )
    payment_status = serializers.ChoiceField(
        source="transaction.payment_status",
        choices=Transaction.PAYMENT_STATUSES,
        read_only=True,
    )
    direction = serializers.SerializerMethodField()
    outstanding_amount = serializers.DecimalField(
        max_digits=12,
        decimal_places=2,
        read_only=True,
    )
    customer_name = related_name_field("transaction.customer.full_name", allow_null=True)
    supplier_name = related_name_field("transaction.supplier.name", allow_null=True)
    transaction_type = related_name_field("transaction.type")

    @extend_schema_field({
        "type": "string",
        "enum": [
            "receivable",
            "payable",
        ],
        "nullable": True,
    })
    def get_direction(self, obj):
        directions = {
            "sale": "receivable",
            "purchase": "payable",
        }

        return directions.get(
            obj.transaction.type
        )

    class Meta:
        model = Debt
        fields = (
            "public_id",
            "business_public_id",
            "transaction_public_id",
            "customer_public_id",
            "supplier_public_id",
            "transaction_status_name",
            "payment_status",
            "direction",
            "total_amount",
            "paid_amount",
            "outstanding_amount",
            "interest_rate",
            "term_months",
            "customer_name",
            "supplier_name",
            "transaction_type",
            "due_date",
            "is_settled",
            "created_at",
            "updated_at",
        )
        read_only_fields = (
            "public_id",
            "created_at",
            "updated_at",
            "is_settled",
        )

class DebtPaymentSerializer(serializers.ModelSerializer):
    business_public_id = public_id_read_only(
        source="debt.transaction.business",
    )
    debt_public_id = secure_public_id_field(
        Debt,
        source="debt",
    )
    payment_method_public_id = secure_public_id_field(
        PaymentMethod,
        source="payment_method",
    )
    payment_method_name = related_name_field("payment_method.name")
    customer_name = related_name_field("debt.transaction.customer.full_name", allow_null=True)
    supplier_name = related_name_field("debt.transaction.supplier.name", allow_null=True)
    transaction_public_id = secure_public_id_field(
        Transaction,
        source="transaction",
        required=False,
        allow_null=True,
    )
    created_by_public_id = serializers.SlugRelatedField(
        source="created_by",
        slug_field="public_id",
        read_only=True,
        allow_null=True,
    )
    created_by_name = serializers.CharField(
        source="created_by.full_name",
        read_only=True,
        allow_null=True,
    )
    class Meta:
        model = DebtPayment
        fields = ("public_id", "business_public_id", "debt_public_id", "amount", "payment_date", "payment_method_public_id", "payment_method_name", "customer_name", "supplier_name", "transaction_public_id", "created_by_public_id", "created_by_name", "created_at", "updated_at")
        read_only_fields = ("public_id", "created_by_public_id", "created_by_name", "created_at", "updated_at")

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)

        business_ids = active_membership_business_ids(
            self.context
        )

        if business_ids is None:
            return

        debt_queryset = Debt.objects.filter(
            transaction__business_id__in=business_ids,
        )
        payment_method_queryset = (
            PaymentMethod.objects.filter(
                business_id__in=business_ids,
            )
        )
        transaction_queryset = Transaction.objects.filter(
            business_id__in=business_ids,
        )

        self.fields["debt_public_id"].queryset = (
            debt_queryset
        )
        self.fields[
            "payment_method_public_id"
        ].queryset = payment_method_queryset
        self.fields[
            "transaction_public_id"
        ].queryset = transaction_queryset

    def validate(self, attrs):
        forbidden_actor_fields = {
            field_name
            for field_name in (
                "created_by_public_id",
                "created_by_name",
            )
            if field_name in self.initial_data
        }
        if forbidden_actor_fields:
            raise serializers.ValidationError({
                field_name: "Este campo es de solo lectura."
                for field_name in sorted(forbidden_actor_fields)
            })
        return attrs

    def create(self, validated_data):
        debt = validated_data["debt"]
        payment_method = validated_data[
            "payment_method"
        ]
        submitted_transaction = (
            validated_data.get("transaction")
        )

        return register_debt_payment(
            debt_id=debt.pk,
            amount=validated_data["amount"],
            payment_date=validated_data[
                "payment_date"
            ],
            payment_method_id=payment_method.pk,
            actor=self.context["request"].user,
            submitted_transaction_id=(
                submitted_transaction.pk
                if submitted_transaction is not None
                else None
            ),
            observed_remaining_amount=(
                debt.total_amount
                - debt.paid_amount
            ),
        )

# ---------- Notificaciones / Recordatorios ----------
class NotificationSerializer(serializers.ModelSerializer):
    user_public_id = public_id_read_only(
        source="user",
    )
    user_email = serializers.EmailField(
        source="user.email",
        read_only=True,
    )
    business_public_id = public_id_field(
        Business,
        source="business",
        required=False,
        allow_null=True,
    )
    transaction_public_id = public_id_field(
        Transaction,
        source="transaction",
        required=False,
        allow_null=True,
    )
    status_public_id = public_id_field(
        EntityStatus,
        source="status",
        required=False,
    )
    status_name = related_name_field("status.name")
    class Meta:
        model = Notification
        fields = (
            "public_id",
            "title",
            "message",
            "type",
            "user_public_id",
            "user_email",
            "business_public_id",
            "transaction_public_id",
            "is_read",
            "sent_at",
            "status_public_id",
            "status_name",
            "scheduled_at",
            "created_at",
            "updated_at",
        )
        read_only_fields = (
            "public_id",
            "user_public_id",
            "user_email",
            "sent_at",
            "created_at",
            "updated_at",
        )

    def validate(self, attrs):
        business = attrs.get(
            "business",
            getattr(self.instance, "business", None),
        )
        transaction = attrs.get(
            "transaction",
            getattr(self.instance, "transaction", None),
        )

        if (
            business is not None
            and transaction is not None
            and transaction.business_id != business.id
        ):
            raise serializers.ValidationError({
                "transaction_public_id": (
                    "La transacción no pertenece al negocio seleccionado."
                )
            })

        return attrs

class ReminderSerializer(serializers.ModelSerializer):
    user_public_id = public_id_read_only(
        source="user",
    )
    business_public_id = public_id_field(
        Business,
        source="business",
        required=False,
        allow_null=True,
    )
    transaction_public_id = public_id_field(
        Transaction,
        source="transaction",
        required=False,
        allow_null=True,
    )
    status_public_id = public_id_field(
        EntityStatus,
        source="status",
        required=False,
    )
    status_name = related_name_field("status.name")
    class Meta:
        model = Reminder
        fields = (
            "public_id",
            "title",
            "description",
            "due_date",
            "is_completed",
            "user_public_id",
            "business_public_id",
            "transaction_public_id",
            "status_public_id",
            "status_name",
            "created_at",
            "updated_at",
        )
        read_only_fields = (
            "public_id",
            "user_public_id",
            "created_at",
            "updated_at",
        )

    def validate(self, attrs):
        business = attrs.get(
            "business",
            getattr(self.instance, "business", None),
        )
        transaction = attrs.get(
            "transaction",
            getattr(self.instance, "transaction", None),
        )

        if (
            business is not None
            and transaction is not None
            and transaction.business_id != business.id
        ):
            raise serializers.ValidationError({
                "transaction_public_id": (
                    "La transacción no pertenece al negocio seleccionado."
                )
            })

        return attrs

class BudgetSerializer(serializers.ModelSerializer):
    user_public_id = public_id_read_only(
        source="user",
    )
    business_public_id = public_id_field(
        Business,
        source="business",
    )
    status_public_id = public_id_field(
        EntityStatus,
        source="status",
        required=False,
    )
    status_name = related_name_field("status.name")
    class Meta:
        model = Budget
        fields = (
            "public_id",
            "user_public_id",
            "business_public_id",
            "status_public_id",
            "status_name",
            "period_start",
            "period_end",
            "allocated_amount",
            "used_amount",
            "created_at",
            "updated_at",
        )
        read_only_fields = (
            "public_id",
            "user_public_id",
            "created_at",
            "updated_at",
        )

    def validate(self, attrs):
        start = attrs.get(
            "period_start",
            getattr(self.instance, "period_start", None),
        )
        end = attrs.get(
            "period_end",
            getattr(self.instance, "period_end", None),
        )

        if start is not None and end is not None and end < start:
            raise serializers.ValidationError({
                "period_end": (
                    "La fecha final no puede ser anterior a la inicial."
                )
            })

        return attrs

    def create(self, validated_data):
        validated_data.setdefault("status", get_active_status())
        return super().create(validated_data)

class GoalSerializer(serializers.ModelSerializer):
    user_public_id = public_id_read_only(
        source="user",
    )
    business_public_id = public_id_field(
        Business,
        source="business",
    )
    status_public_id = public_id_field(
        EntityStatus,
        source="status",
        required=False,
    )
    status_name = related_name_field("status.name")
    class Meta:
        model = Goal
        fields = (
            "public_id",
            "user_public_id",
            "business_public_id",
            "name",
            "description",
            "target_amount",
            "current_amount",
            "status_public_id",
            "status_name",
            "start_date",
            "end_date",
            "is_completed",
            "created_at",
            "updated_at",
        )
        read_only_fields = (
            "public_id",
            "user_public_id",
            "current_amount",
            "is_completed",
            "created_at",
            "updated_at",
        )

    def validate(self, attrs):
        start = attrs.get(
            "start_date",
            getattr(self.instance, "start_date", None),
        )
        end = attrs.get(
            "end_date",
            getattr(self.instance, "end_date", None),
        )

        if start is not None and end is not None and end < start:
            raise serializers.ValidationError({
                "end_date": (
                    "La fecha final no puede ser anterior a la inicial."
                )
            })

        return attrs

class GoalProgressSerializer(serializers.ModelSerializer):
    goal_public_id = public_id_field(
        Goal,
        source="goal",
    )
    goal_name = related_name_field("goal.name")
    transaction_public_id = public_id_field(
        Transaction,
        source="transaction",
        required=False,
        allow_null=True,
    )
    status_public_id = public_id_field(
        EntityStatus,
        source="status",
        required=False,
    )
    status_name = related_name_field("status.name")
    class Meta:
        model = GoalProgress
        fields = ("public_id", "goal_public_id", "goal_name", "amount", "transaction_public_id", "status_public_id", "status_name", "note",
                  "created_at", "updated_at")

    def validate(self, attrs):
        goal = attrs.get(
            "goal",
            getattr(self.instance, "goal", None),
        )
        transaction = attrs.get(
            "transaction",
            getattr(self.instance, "transaction", None),
        )

        if (
            goal is not None
            and transaction is not None
            and transaction.business_id != goal.business_id
        ):
            raise serializers.ValidationError({
                "transaction_public_id": (
                    "La transacción no pertenece al negocio de la meta."
                )
            })

        return attrs

    @db_tx.atomic
    def create(self, validated_data):
        validated_data.setdefault("status", get_active_status())
        progress = super().create(validated_data)

        goal = (
            Goal.objects
            .select_for_update()
            .get(pk=progress.goal_id)
        )

        goal.current_amount = (
            goal.current_amount or Decimal("0.00")
        ) + progress.amount
        goal.is_completed = (
            goal.current_amount >= goal.target_amount
        )
        goal.save(
            update_fields=[
                "current_amount",
                "is_completed",
            ]
        )
        return progress

class EmployeeCommissionPlanSerializer(
    serializers.ModelSerializer
):
    employee_public_id = public_id_field(
        Employee,
        source="employee",
    )

    employee_name = serializers.CharField(
        source="employee.full_name",
        read_only=True,
    )

    business_public_id = serializers.SlugRelatedField(
        source="employee.business",
        slug_field="public_id",
        read_only=True,
    )

    class Meta:
        model = EmployeeCommissionPlan

        fields = (
            "public_id",
            "business_public_id",
            "employee_public_id",
            "employee_name",
            "percentage",
            "valid_from",
            "valid_until",
            "is_active",
            "created_at",
            "updated_at",
        )

        read_only_fields = (
            "public_id",
            "business_public_id",
            "employee_name",
            "created_at",
            "updated_at",
        )

    def validate_percentage(
        self,
        value,
    ):
        if (
            value < Decimal("0.00")
            or value > Decimal("100.00")
        ):
            raise serializers.ValidationError(
                "El porcentaje debe estar "
                "entre 0 y 100."
            )

        return value

    def validate(self, attrs):
        employee = attrs.get(
            "employee",
            getattr(
                self.instance,
                "employee",
                None,
            ),
        )

        valid_from = attrs.get(
            "valid_from",
            getattr(
                self.instance,
                "valid_from",
                None,
            ),
        )

        valid_until = attrs.get(
            "valid_until",
            getattr(
                self.instance,
                "valid_until",
                None,
            ),
        )

        if (
            valid_from is not None
            and valid_until is not None
            and valid_until < valid_from
        ):
            raise serializers.ValidationError({
                "valid_until": (
                    "La fecha final no puede ser "
                    "anterior a la fecha inicial."
                )
            })

        if employee is None:
            return attrs

        overlapping_plans = (
            EmployeeCommissionPlan.objects
            .filter(
                employee=employee,
                is_active=True,
            )
        )

        if self.instance is not None:
            overlapping_plans = (
                overlapping_plans.exclude(
                    pk=self.instance.pk
                )
            )

        if valid_from is not None:
            overlapping_plans = (
                overlapping_plans.filter(
                    Q(valid_until__isnull=True)
                    | Q(
                        valid_until__gte=valid_from
                    )
                )
            )

        if valid_until is not None:
            overlapping_plans = (
                overlapping_plans.filter(
                    valid_from__lte=valid_until
                )
            )

        if overlapping_plans.exists():
            raise serializers.ValidationError({
                "valid_from": (
                    "El empleado ya tiene un plan "
                    "de comisión activo que coincide "
                    "con ese período."
                )
            })

        return attrs

class CommissionSettlementSerializer(
    serializers.ModelSerializer
):
    employee_public_id = public_id_read_only(
        source="employee",
    )

    employee_name = serializers.CharField(
        source="employee.full_name",
        read_only=True,
    )

    employee_position = (
        serializers.CharField(
            source="employee.position",
            read_only=True,
        )
    )

    business_public_id = serializers.SlugRelatedField(
        source="employee.business",
        slug_field="public_id",
        read_only=True,
    )

    business_currency = (
        serializers.CharField(
            source="employee.business.currency",
            read_only=True,
        )
    )

    created_by_public_id = serializers.SlugRelatedField(
        source="created_by",
        slug_field="public_id",
        read_only=True,
    )

    created_by_name = serializers.CharField(
        source="created_by.full_name",
        read_only=True,
    )

    class Meta:
        model = CommissionSettlement

        fields = (
            "public_id",
            "business_public_id",
            "business_currency",
            "employee_public_id",
            "employee_name",
            "employee_position",
            "period_start",
            "period_end",
            "sales_count",
            "sales_total",
            "commission_percentage",
            "commission_total",
            "employee_advances",
            "employee_repayments",
            "advance_balance",
            "net_commission_payable",
            "remaining_advance_balance",
            "status",
            "paid_at",
            "created_by_public_id",
            "created_by_name",
            "created_at",
            "updated_at",
        )

        read_only_fields = fields

class CommissionSettlementCreateSerializer(
    serializers.Serializer
):
    employee_public_id = public_id_field(
        Employee,
        source="employee",
    )

    period_start = serializers.DateField()

    period_end = serializers.DateField()

    def validate(self, attrs):
        employee = attrs["employee"]
        period_start = attrs["period_start"]
        period_end = attrs["period_end"]

        if period_end < period_start:
            raise serializers.ValidationError({
                "period_end": (
                    "La fecha final no puede ser "
                    "anterior a la fecha inicial."
                )
            })

        existing_settlement = (
            CommissionSettlement.objects
            .filter(
                employee=employee,
                period_start=period_start,
                period_end=period_end,
            )
            .exists()
        )

        if existing_settlement:
            raise serializers.ValidationError({
                "period": (
                    "Ya existe una liquidación para "
                    "este empleado y período."
                )
            })

        commission_plan = (
            EmployeeCommissionPlan.objects
            .filter(
                employee=employee,
                is_active=True,
                valid_from__lte=period_end,
            )
            .filter(
                Q(valid_until__isnull=True)
                | Q(
                    valid_until__gte=period_start
                )
            )
            .order_by("-valid_from")
            .first()
        )

        if commission_plan is None:
            raise serializers.ValidationError({
                "commission_plan": (
                    "El empleado no tiene un plan "
                    "de comisión vigente para "
                    "este período."
                )
            })

        attrs["commission_plan"] = (
            commission_plan
        )

        return attrs

    def create(self, validated_data):
        request = self.context["request"]

        employee = validated_data[
            "employee"
        ]

        period_start = validated_data[
            "period_start"
        ]

        period_end = validated_data[
            "period_end"
        ]

        commission_plan = validated_data[
            "commission_plan"
        ]

        sales = (
            Transaction.objects
            .filter(
                business=employee.business,
                employee=employee,
                type="sale",
                created_at__date__gte=(
                    period_start
                ),
                created_at__date__lte=(
                    period_end
                ),
            )
        )
        sales = exclude_terminal_transactions(sales)

        summary = sales.aggregate(
            sales_total=Sum("total_value"),
        )

        sales_count = sales.count()

        sales_total = (
            summary["sales_total"]
            or Decimal("0.00")
        ).quantize(
            Decimal("0.01")
        )

        commission_percentage = (
            commission_plan.percentage
        )

        commission_total = (
            sales_total
            * commission_percentage
            / Decimal("100.00")
        ).quantize(
            Decimal("0.01")
        )

        advance_summary = (
            calculate_employee_advance_summary(
                employee=employee,
                period_start=period_start,
                period_end=period_end,
            )
        )

        employee_advances = advance_summary[
            "employee_advances"
        ]

        employee_repayments = advance_summary[
            "employee_repayments"
        ]

        advance_balance = advance_summary[
            "advance_balance"
        ]

        net_commission_payable = max(
            commission_total - advance_balance,
            Decimal("0.00"),
        ).quantize(
            Decimal("0.01")
        )

        remaining_advance_balance = max(
            advance_balance - commission_total,
            Decimal("0.00"),
        ).quantize(
            Decimal("0.01")
        )

        return CommissionSettlement.objects.create(
            employee=employee,
            period_start=period_start,
            period_end=period_end,
            sales_count=sales_count,
            sales_total=sales_total,
            commission_percentage=(
                commission_percentage
            ),
            commission_total=(
                commission_total
            ),
            employee_advances=(
                employee_advances
            ),
            employee_repayments=(
                employee_repayments
            ),
            advance_balance=(
                advance_balance
            ),
            net_commission_payable=(
                net_commission_payable
            ),
            remaining_advance_balance=(
                remaining_advance_balance
            ),
            status=(
                CommissionSettlement.STATUS_PENDING
            ),
            created_by=request.user,
        )
        
class CashRegisterSerializer(
    serializers.ModelSerializer
):
    business_public_id = public_id_read_only(
        source="business",
    )
    employee_public_id = public_id_read_only(
        source="employee",
        allow_null=True,
    )

    opened_by_public_id = serializers.SlugRelatedField(
        source="opened_by",
        slug_field="public_id",
        read_only=True,
        allow_null=True,
    )

    closed_by_public_id = serializers.SlugRelatedField(
        source="closed_by",
        slug_field="public_id",
        read_only=True,
        allow_null=True,
    )

    business_currency = serializers.CharField(
        source="business.currency",
        read_only=True,
    )

    employee_name = serializers.CharField(
        source="employee.full_name",
        read_only=True,
        allow_null=True,
    )

    opened_by_name = serializers.CharField(
        source="opened_by.full_name",
        read_only=True,
        allow_null=True,
    )

    closed_by_name = serializers.CharField(
        source="closed_by.full_name",
        read_only=True,
        allow_null=True,
    )

    class Meta:
        model = CashRegister

        fields = (
            "public_id",

            "business_public_id",
            "business_currency",

            "employee_public_id",
            "employee_name",

            "opened_by_public_id",
            "opened_by_name",
            "closed_by_public_id",
            "closed_by_name",

            "open_time",
            "close_time",

            "opening_balance",
            "closing_balance",
            "expected_closing_balance",
            "difference",

            "opening_notes",
            "closing_notes",

            "status",

            "created_at",
            "updated_at",
        )

        read_only_fields = fields

class CashRegisterOpenSerializer(
    serializers.ModelSerializer
):
    business_public_id = public_id_field(
        Business,
        source="business",
    )

    employee_public_id = public_id_field(
        Employee,
        source="employee",
    )

    opening_balance = serializers.DecimalField(
        max_digits=12,
        decimal_places=2,
        min_value=Decimal("0.00"),
    )

    opening_notes = serializers.CharField(
        required=False,
        allow_blank=True,
    )

    class Meta:
        model = CashRegister

        fields = (
            "business_public_id",
            "employee_public_id",
            "opening_balance",
            "opening_notes",
        )

    def validate(self, attrs):
        business = attrs["business"]
        employee = attrs["employee"]

        if employee.business_id != business.id:
            raise serializers.ValidationError({
                "employee_public_id": (
                    "El empleado debe pertenecer "
                    "al negocio seleccionado."
                )
            })

        open_register_exists = (
            CashRegister.objects
            .filter(
                business=business,
                status=CashRegister.STATUS_OPEN,
            )
            .exists()
        )

        if open_register_exists:
            raise serializers.ValidationError({
                "business_public_id": (
                    "Este negocio ya tiene una "
                    "caja abierta."
                )
            })

        return attrs

class CashRegisterCloseSerializer(
    serializers.Serializer
):
    closing_balance = serializers.DecimalField(
        max_digits=12,
        decimal_places=2,
        min_value=Decimal("0.00"),
    )

    closing_notes = serializers.CharField(
        required=False,
        allow_blank=True,
        default="",
    )

class CashMovementSerializer(
    serializers.ModelSerializer
):
    cash_register_public_id = public_id_field(
        CashRegister,
        source="cash_register",
    )

    employee_public_id = public_id_field(
        Employee,
        source="employee",
        required=False,
        allow_null=True,
    )

    payment_method_public_id = public_id_field(
        PaymentMethod,
        source="payment_method",
        required=False,
        allow_null=True,
    )

    created_by_public_id = serializers.SlugRelatedField(
        source="created_by",
        slug_field="public_id",
        read_only=True,
    )

    cash_register_status = serializers.CharField(
        source="cash_register.status",
        read_only=True,
    )

    business_public_id = serializers.SlugRelatedField(
        source="cash_register.business",
        slug_field="public_id",
        read_only=True,
    )

    employee_name = serializers.CharField(
        source="employee.full_name",
        read_only=True,
        allow_null=True,
    )

    payment_method_name = serializers.CharField(
        source="payment_method.name",
        read_only=True,
        allow_null=True,
    )

    created_by_name = serializers.CharField(
        source="created_by.full_name",
        read_only=True,
    )

    signed_amount = serializers.DecimalField(
        max_digits=12,
        decimal_places=2,
        read_only=True,
    )

    amount = serializers.DecimalField(
        max_digits=12,
        decimal_places=2,
        min_value=Decimal("0.01"),
    )

    class Meta:
        model = CashMovement

        fields = (
            "public_id",

            "cash_register_public_id",
            "cash_register_status",

            "business_public_id",

            "employee_public_id",
            "employee_name",

            "payment_method_public_id",
            "payment_method_name",

            "movement_type",
            "amount",
            "signed_amount",
            "note",

            "created_by_public_id",
            "created_by_name",
            "created_at",
        )

        read_only_fields = (
            "public_id",
            "cash_register_status",
            "business_public_id",
            "employee_name",
            "payment_method_name",
            "signed_amount",
            "created_by_public_id",
            "created_by_name",
            "created_at",
        )

    def validate(self, attrs):
        instance = self.instance

        cash_register = attrs.get(
            "cash_register",
            getattr(
                instance,
                "cash_register",
                None,
            ),
        )

        employee = attrs.get(
            "employee",
            getattr(
                instance,
                "employee",
                None,
            ),
        )

        payment_method = attrs.get(
            "payment_method",
            getattr(
                instance,
                "payment_method",
                None,
            ),
        )

        movement_type = attrs.get(
            "movement_type",
            getattr(
                instance,
                "movement_type",
                None,
            ),
        )

        if cash_register is None:
            raise serializers.ValidationError({
                "cash_register_public_id": (
                    "Debes indicar una caja."
                )
            })

        if (
            cash_register.status
            != CashRegister.STATUS_OPEN
        ):
            raise serializers.ValidationError({
                "cash_register_public_id": (
                    "No se pueden registrar "
                    "movimientos en una caja cerrada."
                )
            })

        if (
            employee is not None
            and employee.business_id
            != cash_register.business_id
        ):
            raise serializers.ValidationError({
                "employee_public_id": (
                    "El empleado no pertenece al "
                    "negocio de la caja."
                )
            })

        if (
            payment_method is not None
            and payment_method.business_id
            != cash_register.business_id
        ):
            raise serializers.ValidationError({
                "payment_method_public_id": (
                    "El método de pago no pertenece "
                    "al negocio de la caja."
                )
            })

        employee_required_types = {
            CashMovement.TYPE_EMPLOYEE_ADVANCE,
            CashMovement.TYPE_EMPLOYEE_REPAYMENT,
        }

        if (
            movement_type in employee_required_types
            and employee is None
        ):
            raise serializers.ValidationError({
                "employee_public_id": (
                    "Debes indicar el empleado para "
                    "este tipo de movimiento."
                )
            })

        return attrs

class MonthlySummaryQuerySerializer(
    serializers.Serializer
):
    business_public_id = serializers.UUIDField()

    year = serializers.IntegerField(
        min_value=2000,
        max_value=2100,
    )

    month = serializers.IntegerField(
        min_value=1,
        max_value=12,
    )


# ---------- Contratos estructurados de reportes financieros ----------
def financial_amount_field(**kwargs):
    return serializers.DecimalField(
        max_digits=14,
        decimal_places=2,
        **kwargs,
    )


class FinancialBusinessResponseSerializer(serializers.Serializer):
    public_id = serializers.UUIDField()
    name = serializers.CharField()
    currency = serializers.CharField()


class FinancialDatePeriodResponseSerializer(serializers.Serializer):
    date_from = serializers.DateField()
    date_to = serializers.DateField()


class DetailErrorResponseSerializer(serializers.Serializer):
    detail = serializers.CharField()


class InventoryValidationErrorResponseSerializer(serializers.Serializer):
    details = serializers.CharField()


class TransactionCancellationConflictResponseSerializer(serializers.Serializer):
    detail = serializers.CharField(required=False)
    non_field_errors = serializers.CharField(required=False)


class CountAmountResponseSerializer(serializers.Serializer):
    count = serializers.IntegerField()
    total = financial_amount_field()


class CashSummaryPeriodResponseSerializer(serializers.Serializer):
    open_time = serializers.DateTimeField()
    until = serializers.DateTimeField()


class CashSummarySalesResponseSerializer(serializers.Serializer):
    cash = financial_amount_field()
    card = financial_amount_field()
    transfer = financial_amount_field()
    other = financial_amount_field()
    total = financial_amount_field()


class CashSummaryMovementsResponseSerializer(serializers.Serializer):
    deposits = financial_amount_field()
    withdrawals = financial_amount_field()
    employee_advances = financial_amount_field()
    employee_repayments = financial_amount_field()
    other_income = financial_amount_field()
    other_expense = financial_amount_field()


class CashRegisterSummaryResponseSerializer(serializers.Serializer):
    period = CashSummaryPeriodResponseSerializer()
    opening_balance = financial_amount_field()
    sales = CashSummarySalesResponseSerializer()
    cash_purchases = financial_amount_field()
    cash_expenses = financial_amount_field()
    cash_debt_payments = financial_amount_field(
        help_text=(
            "Campo legado: suma bruta de DebtPayments cash recibidos "
            "y realizados. No debe usarse para calcular efectivo neto."
        ),
    )
    cash_debt_payments_received = financial_amount_field()
    cash_debt_payments_made = financial_amount_field()
    automatic_cash_inflows = financial_amount_field()
    automatic_cash_outflows = financial_amount_field()
    movements = CashSummaryMovementsResponseSerializer()
    expected_closing_balance = financial_amount_field()


class PaymentMethodSummaryResponseSerializer(serializers.Serializer):
    public_id = serializers.UUIDField()
    name = serializers.CharField()
    method_type = serializers.ChoiceField(
        choices=PaymentMethod.METHOD_TYPES,
    )


class PaymentMethodBreakdownResponseSerializer(serializers.Serializer):
    payment_method = PaymentMethodSummaryResponseSerializer()
    sales = CountAmountResponseSerializer()
    purchases = CountAmountResponseSerializer()
    expenses = CountAmountResponseSerializer()
    debt_payments = CountAmountResponseSerializer()
    debt_payments_received = CountAmountResponseSerializer()
    debt_payments_made = CountAmountResponseSerializer()
    total_incoming = financial_amount_field()
    total_outgoing = financial_amount_field()
    net_amount = financial_amount_field()


class PaymentSummaryTotalsResponseSerializer(serializers.Serializer):
    sales = CountAmountResponseSerializer()
    purchases = CountAmountResponseSerializer()
    expenses = CountAmountResponseSerializer()
    debt_payments = CountAmountResponseSerializer()
    debt_payments_received = CountAmountResponseSerializer()
    debt_payments_made = CountAmountResponseSerializer()
    payments_received = financial_amount_field()
    payments_made = financial_amount_field()
    incoming_total = financial_amount_field()
    outgoing_total = financial_amount_field()
    net_amount = financial_amount_field()


class PaymentSummaryResponseSerializer(serializers.Serializer):
    business = FinancialBusinessResponseSerializer()
    period = FinancialDatePeriodResponseSerializer()
    totals = PaymentSummaryTotalsResponseSerializer()
    results = PaymentMethodBreakdownResponseSerializer(many=True)


class DebtDirectionSummaryResponseSerializer(serializers.Serializer):
    count = serializers.IntegerField()
    settled_count = serializers.IntegerField()
    pending_count = serializers.IntegerField()
    original_total = financial_amount_field()
    paid_total = financial_amount_field()
    outstanding = financial_amount_field()


class DebtPartyResponseSerializer(serializers.Serializer):
    public_id = serializers.UUIDField()
    name = serializers.CharField()
    full_name = serializers.CharField(required=False)


class DebtIdentityResponseSerializer(serializers.Serializer):
    public_id = serializers.UUIDField()
    transaction_public_id = serializers.UUIDField()


class DebtTransactionResponseSerializer(serializers.Serializer):
    public_id = serializers.UUIDField()
    type = serializers.SerializerMethodField()

    @extend_schema_field({
        "type": "string",
        "enum": ["sale", "purchase", "expense"],
    })
    def get_type(self, obj):
        if isinstance(obj, dict):
            return obj.get("type")
        return obj.type


class DebtDetailResponseSerializer(serializers.Serializer):
    debt = DebtIdentityResponseSerializer()
    transaction = DebtTransactionResponseSerializer()
    direction = serializers.ChoiceField(
        choices=("receivable", "payable", "unclassified"),
    )
    customer = DebtPartyResponseSerializer(allow_null=True)
    supplier = DebtPartyResponseSerializer(allow_null=True)
    employee = DebtPartyResponseSerializer(allow_null=True)
    total_amount = financial_amount_field()
    total = financial_amount_field()
    paid_until_period_end = financial_amount_field()
    paid = financial_amount_field()
    pending_at_period_end = financial_amount_field()
    outstanding = financial_amount_field()
    is_settled_at_period_end = serializers.BooleanField()
    is_settled = serializers.BooleanField()
    due_date = serializers.DateField()
    was_overdue_at_period_end = serializers.BooleanField()


class DebtPortfolioResponseSerializer(serializers.Serializer):
    original_debt_total = financial_amount_field()
    paid_until_period_end = financial_amount_field()
    outstanding = financial_amount_field()
    overdue_outstanding = financial_amount_field()


class DebtSummaryResponseSerializer(serializers.Serializer):
    business = FinancialBusinessResponseSerializer()
    period = FinancialDatePeriodResponseSerializer()
    generated = CountAmountResponseSerializer()
    payments_received = CountAmountResponseSerializer()
    payments_made = CountAmountResponseSerializer()
    accounts_receivable = DebtDirectionSummaryResponseSerializer()
    accounts_payable = DebtDirectionSummaryResponseSerializer()
    unclassified = DebtDirectionSummaryResponseSerializer()
    portfolio_at_period_end = DebtPortfolioResponseSerializer()
    results = DebtDetailResponseSerializer(many=True)


class DashboardCardsResponseSerializer(serializers.Serializer):
    sales_total = financial_amount_field()
    purchases_total = financial_amount_field()
    expenses_total = financial_amount_field()
    gross_margin_before_costs = financial_amount_field()
    outstanding_debt = financial_amount_field(
        help_text=(
            "Agregado bruto legado. Para nuevas integraciones use "
            "outstanding_receivables y outstanding_payables."
        ),
    )
    outstanding_receivables = financial_amount_field()
    outstanding_payables = financial_amount_field()
    debt_payments_received = financial_amount_field()
    debt_payments_made = financial_amount_field()
    payments_received = financial_amount_field()
    payments_made = financial_amount_field()
    pending_commissions = financial_amount_field()
    cash_difference = financial_amount_field()
    current_inventory_units = serializers.IntegerField()


class DashboardActivityResponseSerializer(serializers.Serializer):
    sales_count = serializers.IntegerField()
    purchases_count = serializers.IntegerField()
    expenses_count = serializers.IntegerField()
    debt_payments_count = serializers.IntegerField()
    pending_debts_count = serializers.IntegerField()
    closed_cash_registers_count = serializers.IntegerField()
    open_cash_register = serializers.BooleanField()
    low_stock_items_count = serializers.IntegerField()
    out_of_stock_items_count = serializers.IntegerField()


class DashboardCommissionsResponseSerializer(serializers.Serializer):
    gross_total = financial_amount_field()
    net_total = financial_amount_field()
    pending_total = financial_amount_field()
    paid_total = financial_amount_field()


class DashboardCashResponseSerializer(serializers.Serializer):
    closed_count = serializers.IntegerField()
    expected_total = financial_amount_field()
    counted_total = financial_amount_field()
    difference_total = financial_amount_field()


class DashboardOverviewResponseSerializer(serializers.Serializer):
    business = FinancialBusinessResponseSerializer()
    period = FinancialDatePeriodResponseSerializer()
    cards = DashboardCardsResponseSerializer()
    activity = DashboardActivityResponseSerializer()
    commissions = DashboardCommissionsResponseSerializer()
    cash = DashboardCashResponseSerializer()


class MonthlyPeriodResponseSerializer(serializers.Serializer):
    year = serializers.IntegerField()
    month = serializers.IntegerField()
    date_from = serializers.DateField()
    date_to = serializers.DateField()


class MonthlyTransactionVolumeResponseSerializer(serializers.Serializer):
    count = serializers.IntegerField()
    total = financial_amount_field()


class MonthlySalesResponseSerializer(MonthlyTransactionVolumeResponseSerializer):
    paid_count = serializers.IntegerField()
    paid_total = financial_amount_field()
    debt_count = serializers.IntegerField()
    debt_total = financial_amount_field()


class MonthlyTransactionsResponseSerializer(serializers.Serializer):
    sales = MonthlySalesResponseSerializer()
    purchases = MonthlyTransactionVolumeResponseSerializer()
    expenses = MonthlyTransactionVolumeResponseSerializer()


class MonthlyDebtsResponseSerializer(serializers.Serializer):
    generated_count = serializers.IntegerField()
    generated_total = financial_amount_field()
    payments_count = serializers.IntegerField()
    payments_total = financial_amount_field()
    payments_received = financial_amount_field()
    payments_made = financial_amount_field()
    outstanding_receivables = financial_amount_field()
    outstanding_payables = financial_amount_field()
    outstanding_unclassified = financial_amount_field()
    outstanding_at_period_end = financial_amount_field(
        help_text="Agregado bruto legado de saldos pendientes al cierre.",
    )


class MonthlyPaymentsResponseSerializer(serializers.Serializer):
    received = financial_amount_field()
    made = financial_amount_field()
    net = financial_amount_field()
    direct_sales = financial_amount_field()
    direct_purchases_and_expenses = financial_amount_field()
    debt_payments_received = financial_amount_field()
    debt_payments_made = financial_amount_field()


class MonthlyCashRegistersResponseSerializer(serializers.Serializer):
    closed_count = serializers.IntegerField()
    opening_total = financial_amount_field()
    expected_total = financial_amount_field()
    counted_total = financial_amount_field()
    difference_total = financial_amount_field()
    shortages_total = financial_amount_field()
    surpluses_total = financial_amount_field()


class MonthlyCommissionsResponseSerializer(serializers.Serializer):
    settlements_count = serializers.IntegerField()
    settled_sales_total = financial_amount_field()
    gross_commission_total = financial_amount_field()
    employee_advances = financial_amount_field()
    employee_repayments = financial_amount_field()
    advance_balance = financial_amount_field()
    net_commission_payable = financial_amount_field()
    remaining_advance_balance = financial_amount_field()
    paid = CountAmountResponseSerializer()
    pending = CountAmountResponseSerializer()


class MonthlySummaryResponseSerializer(serializers.Serializer):
    business = FinancialBusinessResponseSerializer()
    period = MonthlyPeriodResponseSerializer()
    transactions = MonthlyTransactionsResponseSerializer()
    debts = MonthlyDebtsResponseSerializer()
    payments = MonthlyPaymentsResponseSerializer()
    cash_registers = MonthlyCashRegistersResponseSerializer()
    commissions = MonthlyCommissionsResponseSerializer()

class MonthlyClosureCreateSerializer(
    serializers.Serializer
):
    business_public_id = public_id_field(
        Business,
        source="business",
    )

    year = serializers.IntegerField(
        min_value=2000,
        max_value=2100,
    )

    month = serializers.IntegerField(
        min_value=1,
        max_value=12,
    )

class MonthlyClosureReopenSerializer(
    serializers.Serializer
):
    reason = serializers.CharField(
        min_length=5,
        max_length=1000,
        trim_whitespace=True,
    )

class MonthlyClosureSerializer(
    serializers.ModelSerializer
):
    business_public_id = public_id_read_only(
        source="business",
    )

    business_name = serializers.CharField(
        source="business.business_name",
        read_only=True,
    )

    business_currency = (
        serializers.CharField(
            source="business.currency",
            read_only=True,
        )
    )

    closed_by_public_id = serializers.SlugRelatedField(
        source="closed_by",
        slug_field="public_id",
        read_only=True,
    )

    closed_by_name = serializers.CharField(
        source="closed_by.full_name",
        read_only=True,
    )

    reopened_by_public_id = serializers.SlugRelatedField(
        source="reopened_by",
        slug_field="public_id",
        read_only=True,
        allow_null=True,
    )

    reopened_by_name = serializers.CharField(
        source="reopened_by.full_name",
        read_only=True,
        allow_null=True,
    )

    class Meta:
        model = MonthlyClosure

        fields = (
            "public_id",
            "business_public_id",
            "business_name",
            "business_currency",
            "year",
            "month",
            "version",
            "status",
            "summary",
            "closed_by_public_id",
            "closed_by_name",
            "closed_at",
            "reopened_by_public_id",
            "reopened_by_name",
            "reopened_at",
            "reopen_reason",
            "created_at",
            "updated_at",
        )

        read_only_fields = fields

class CustomerSummaryQuerySerializer(
    serializers.Serializer
):
    business_public_id = (
        serializers.UUIDField()
    )

    date_from = serializers.DateField()

    date_to = serializers.DateField()

    customer_public_id = (
        serializers.UUIDField(
            required=False,
            allow_null=True,
        )
    )

    def validate(self, attrs):
        if (
            attrs["date_from"]
            > attrs["date_to"]
        ):
            raise serializers.ValidationError({
                "date_to": (
                    "La fecha final no puede ser "
                    "anterior a la fecha inicial."
                )
            })

        return attrs

class SupplierSummaryQuerySerializer(
    serializers.Serializer
):
    business_public_id = (
        serializers.UUIDField()
    )

    date_from = serializers.DateField()

    date_to = serializers.DateField()

    supplier_public_id = (
        serializers.UUIDField(
            required=False,
            allow_null=True,
        )
    )

    def validate(self, attrs):
        if (
            attrs["date_from"]
            > attrs["date_to"]
        ):
            raise serializers.ValidationError({
                "date_to": (
                    "La fecha final no puede ser "
                    "anterior a la fecha inicial."
                )
            })

        return attrs

# ---------- Lecturas (solo por si las quieres exponer) ----------
class StockMovementSerializer(serializers.ModelSerializer):
    product_public_id = public_id_read_only(
        source="product",
    )
    product_name = related_name_field("product.title")
    transaction_public_id = public_id_read_only(
        source="transaction",
        allow_null=True,
    )
    transaction_detail_public_id = public_id_read_only(
        source="transaction_detail",
        allow_null=True,
    )
    created_by_email = serializers.EmailField(
        source="created_by.email",
        read_only=True,
        allow_null=True,
    )

    class Meta:
        model = StockMovement
        fields = (
            "public_id",
            "product_public_id",
            "product_name",
            "transaction_public_id",
            "transaction_detail_public_id",
            "note",
            "type",
            "quantity",
            "created_by_email",
            "created_at",
            "updated_at",
        )
        read_only_fields = fields

class PaymentSummaryQuerySerializer(
    serializers.Serializer
):
    business_public_id = serializers.UUIDField()

    date_from = serializers.DateField()

    date_to = serializers.DateField()

    payment_method_public_id = (
        serializers.UUIDField(
            required=False,
            allow_null=True,
        )
    )

    def validate(self, attrs):
        if (
            attrs["date_from"]
            > attrs["date_to"]
        ):
            raise serializers.ValidationError({
                "date_to": (
                    "La fecha final no puede ser "
                    "anterior a la fecha inicial."
                )
            })

        return attrs


class DebtSummaryQuerySerializer(
    serializers.Serializer
):
    business_public_id = serializers.UUIDField()

    date_from = serializers.DateField()

    date_to = serializers.DateField()

    def validate(self, attrs):
        if (
            attrs["date_from"]
            > attrs["date_to"]
        ):
            raise serializers.ValidationError({
                "date_to": (
                    "La fecha final no puede ser "
                    "anterior a la fecha inicial."
                )
            })

        return attrs

class InventorySummaryQuerySerializer(
    serializers.Serializer
):
    business_public_id = (
        serializers.UUIDField()
    )

    date_from = serializers.DateField()

    date_to = serializers.DateField()

    product_public_id = (
        serializers.UUIDField(
            required=False,
            allow_null=True,
        )
    )

    def validate(self, attrs):
        if (
            attrs["date_from"]
            > attrs["date_to"]
        ):
            raise serializers.ValidationError({
                "date_to": (
                    "La fecha final no puede ser "
                    "anterior a la fecha inicial."
                )
            })

        return attrs

class DashboardOverviewQuerySerializer(
    serializers.Serializer
):
    business_public_id = (
        serializers.UUIDField()
    )

    date_from = serializers.DateField()

    date_to = serializers.DateField()

    low_stock_threshold = (
        serializers.IntegerField(
            required=False,
            default=5,
            min_value=0,
            max_value=1_000_000,
        )
    )

    def validate(self, attrs):
        if (
            attrs["date_from"]
            > attrs["date_to"]
        ):
            raise serializers.ValidationError({
                "date_to": (
                    "La fecha final no puede ser "
                    "anterior a la fecha inicial."
                )
            })

        return attrs

from core.api.serializers.current_user import (
    CurrentMembershipSerializer,
    CurrentUserSerializer,
)
