from decimal import Decimal
from django.core.exceptions import ValidationError as DjangoValidationError
from django.db import transaction as db_tx
from django.db.models import Sum, Q
from django.utils import timezone
from rest_framework import serializers

from core.utils import calculate_employee_advance_summary
from .models import (
    BusinessMembership, User, Business, EntityStatus,
    ProductCategory, Product, ProductVariantType, ProductVariant,
    Employee, Customer, Supplier, PaymentMethod,
    Transaction, TransactionDetail, StockMovement,
    Debt, DebtPayment, Notification, Reminder,
    Budget, Goal, GoalProgress,
    SalesSummary, SuppliersSummary, CustomersSummary,
    PaymentsSummary, DebtsSummary, InventorySummary,
    CommissionSettlement, EmployeeCommissionPlan,
    CashMovement, CashRegister
)


# ---------- Relaciones mediante public_id ----------
def public_id_field(model, *, required=True, allow_null=False):
    """Campo relacional que recibe y devuelve el public_id (UUID)."""
    return serializers.SlugRelatedField(
        slug_field="public_id",
        queryset=model.objects.all(),
        required=required,
        allow_null=allow_null,
    )


def public_id_read_only(*, allow_null=False):
    """Campo relacional de solo lectura representado por public_id."""
    return serializers.SlugRelatedField(
        slug_field="public_id",
        read_only=True,
        allow_null=allow_null,
    )


def get_active_status():
    active = EntityStatus.objects.filter(name__iexact="Activo").first()
    if active is None:
        raise serializers.ValidationError({
            "status": (
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


class HealthSerializer(serializers.Serializer):
    status = serializers.CharField()
    service = serializers.CharField()
    
# ---------- Usuarios ----------
class UserSerializer(serializers.ModelSerializer):
    class Meta:
        model = User
        fields = ("public_id", "email", "full_name", "phone", "role", "is_active", "created_at", "updated_at")
        read_only_fields = ("public_id", "is_active", "created_at", "updated_at")

class RegisterSerializer(serializers.ModelSerializer):
    password = serializers.CharField(write_only=True, min_length=8)

    class Meta:
        model = User
        fields = ("public_id", "email", "full_name", "password")
        read_only_fields = ("public_id",)  # ← importante

    def create(self, validated_data):
        return User.objects.create_user(
            email=validated_data["email"],
            full_name=validated_data["full_name"],
            password=validated_data["password"],
            role=User.Roles.BUSINESS_OWNER
        )

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
    business = public_id_field(Business)
    status = public_id_field(EntityStatus, required=False)

    class Meta:
        model = PaymentMethod
        fields = (
            "public_id",
            "business",
            "name",
            "status",
        )
        read_only_fields = ("public_id",)

class BusinessSerializer(
    DefaultActiveStatusMixin,
    serializers.ModelSerializer,
):
    status = public_id_field(EntityStatus, required=False)

    class Meta:
        model = Business
        fields = (
            "public_id",
            "business_name",
            "description",
            "currency",
            "status",
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
    business = public_id_read_only()
    business_name = serializers.CharField(
        source="business.business_name",
        read_only=True,
    )
    employee = public_id_read_only(allow_null=True)
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
            "business",
            "business_name",
            "employee",
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
    business = public_id_field(Business)
    status = public_id_field(EntityStatus, required=False)

    class Meta:
        model = ProductCategory
        fields = (
            "public_id",
            "business",
            "name",
            "status",
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
    business = public_id_field(Business)
    category = public_id_field(
        ProductCategory,
        required=False,
        allow_null=True,
    )
    status = public_id_field(EntityStatus, required=False)

    class Meta:
        model = Product
        fields = (
            "public_id",
            "business",
            "category",
            "title",
            "description",
            "image_url",
            "base_price",
            "base_cost",
            "stock",
            "is_visible",
            "status",
            "created_at",
            "updated_at",
        )
        read_only_fields = (
            "public_id",
            "created_at",
            "updated_at",
        )

    def validate(self, attrs):
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
                "category": (
                    "La categoría no pertenece al negocio seleccionado."
                )
            })

        return attrs

class ProductVariantTypeSerializer(
    DefaultActiveStatusMixin,
    serializers.ModelSerializer,
):
    product = public_id_field(Product)
    status = public_id_field(EntityStatus, required=False)

    class Meta:
        model = ProductVariantType
        fields = (
            "public_id",
            "product",
            "name",
            "status",
            "created_at",
            "updated_at",
        )
        read_only_fields = (
            "public_id",
            "created_at",
            "updated_at",
        )

class ProductVariantSerializer(
    DefaultActiveStatusMixin,
    serializers.ModelSerializer,
):
    variant_type = public_id_field(ProductVariantType)
    status = public_id_field(EntityStatus, required=False)

    class Meta:
        model = ProductVariant
        fields = (
            "public_id",
            "variant_type",
            "label",
            "additional_price",
            "stock",
            "status",
            "created_at",
            "updated_at",
        )
        read_only_fields = (
            "public_id",
            "created_at",
            "updated_at",
        )

class EmployeeSerializer(
    DefaultActiveStatusMixin,
    serializers.ModelSerializer,
):
    business = public_id_field(Business)
    status = public_id_field(EntityStatus, required=False)

    class Meta:
        model = Employee
        fields = ("public_id", "business", "full_name", "phone", "email", "position", "status", "created_at", "updated_at")
        read_only_fields = (
            "public_id",
            "created_at",
            "updated_at",
        )

class CustomerSerializer(
    DefaultActiveStatusMixin,
    serializers.ModelSerializer,
):
    business = public_id_field(Business)
    status = public_id_field(EntityStatus, required=False)

    class Meta:
        model = Customer
        fields = ("public_id", "business", "full_name", "phone", "email", "status", "created_at", "updated_at")
        read_only_fields = (
            "public_id",
            "created_at",
            "updated_at",
        )

class SupplierSerializer(
    DefaultActiveStatusMixin,
    serializers.ModelSerializer,
):
    business = public_id_field(Business)
    status = public_id_field(EntityStatus, required=False)

    class Meta:
        model = Supplier
        fields = ("public_id", "business", "name", "phone", "email", "status", "created_at", "updated_at")
        read_only_fields = (
            "public_id",
            "created_at",
            "updated_at",
        )

class TransactionDetailSerializer(
    serializers.ModelSerializer
):
    product = public_id_field(
        Product
    )

    variant = public_id_field(
        ProductVariant,
        required=False,
        allow_null=True,
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

    product_title = serializers.CharField(
        source="product.title",
        read_only=True,
    )

    variant_label = serializers.CharField(
        source="variant.label",
        read_only=True,
        allow_null=True,
    )

    class Meta:
        model = TransactionDetail

        fields = (
            "public_id",
            "product",
            "product_title",
            "variant",
            "variant_label",
            "quantity",
            "unit_price",
            "total_price",
        )

        read_only_fields = (
            "public_id",
            "product_title",
            "variant_label",
            "total_price",
        )

class TransactionSerializer(
    serializers.ModelSerializer
):
    business = public_id_field(Business)
    customer = public_id_field(
        Customer,
        required=False,
        allow_null=True,
    )
    supplier = public_id_field(
        Supplier,
        required=False,
        allow_null=True,
    )
    employee = public_id_field(
        Employee, 
        required=False, 
        allow_null=True
    )
    payment_method = public_id_field(
        PaymentMethod,
        required=False,
        allow_null=True,
    )
    status = public_id_field(
        EntityStatus,
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
            "business",
            "business_name",
            "customer",
            "customer_name",
            "supplier",
            "supplier_name",
            "employee",
            "employee_name",
            "payment_method",
            "payment_method_name",
            "type",
            "is_debt",
            "discount_percent",
            "concept",
            "total_value",
            "expense_amount",
            "status",
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

    def validate(self, attrs):
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
                "business": (
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
                "employee": (
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

        return attrs

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
                raise serializers.ValidationError({
                    field: (
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
            variant = detail.get("variant")
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

            if (
                variant is not None
                and variant.variant_type.product_id
                != product.id
            ):
                raise serializers.ValidationError(
                    f"Detalle #{index}: la variante no "
                    "pertenece al producto indicado."
                )

            has_variants = (
                product.variant_types
                .filter(
                    variants__isnull=False,
                )
                .exists()
            )

            if (
                has_variants
                and variant is None
            ):
                raise serializers.ValidationError(
                    f"Detalle #{index}: el producto "
                    "utiliza variantes. Debes seleccionar "
                    "una variante."
                )

            if (
                not has_variants
                and variant is not None
            ):
                raise serializers.ValidationError(
                    f"Detalle #{index}: este producto "
                    "no administra stock mediante variantes."
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
                "business": (
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
        details_data = validated_data.pop(
            "details",
            [],
        )

        expense_amount = validated_data.pop(
            "expense_amount",
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

        transaction.save(
            update_fields=[
                "total_value",
            ]
        )

        self._create_debt_if_required(
            transaction
        )

        return transaction

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
                "status": (
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
            variant = detail_data.get("variant")
            quantity = detail_data["quantity"]
            unit_price = detail_data.get("unit_price")

            if unit_price is None:
                if transaction.type == "purchase":
                    unit_price = product.base_cost
                else:
                    additional_price = (
                        variant.additional_price
                        if variant is not None
                        else Decimal("0.00")
                    )

                    unit_price = (
                        product.base_price
                        + additional_price
                    )    

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
                    variant=variant,
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

    def _create_debt_if_required(
        self,
        transaction,
    ):
        if not transaction.is_debt:
            return

        Debt.objects.create(
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

# ---------- Pagos de Deuda ----------
class DebtSerializer(serializers.ModelSerializer):
    transaction = public_id_field(Transaction)

    class Meta:
        model = Debt
        fields = (
            "public_id",
            "transaction",
            "total_amount",
            "paid_amount",
            "interest_rate",
            "term_months",
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
    debt = public_id_field(Debt)
    payment_method = public_id_field(PaymentMethod)
    transaction = public_id_field(
        Transaction,
        required=False,
        allow_null=True,
    )
    class Meta:
        model = DebtPayment
        fields = ("public_id", "debt", "amount", "payment_date", "payment_method", "transaction", "created_at", "updated_at")
        read_only_fields = ("public_id", "created_at", "updated_at")

    def validate(self, attrs):
        debt = attrs.get(
            "debt",
            getattr(self.instance, "debt", None),
        )

        amount = attrs.get(
            "amount",
            getattr(self.instance, "amount", None),
        )

        payment_method = attrs.get(
            "payment_method",
            getattr(self.instance, "payment_method", None),
        )

        if (
            debt is not None
            and payment_method is not None
            and payment_method.business_id
            != debt.transaction.business_id
        ):
            raise serializers.ValidationError({
                "payment_method": (
                    "El método de pago no pertenece "
                    "al negocio de la deuda."
                )
            })

        if debt and amount:
            previous_amount = (
                self.instance.amount
                if self.instance
                else 0
            )

            remaining_amount = (
                debt.total_amount
                - debt.paid_amount
                + previous_amount
            )

            if amount > remaining_amount:
                raise serializers.ValidationError({
                    "amount": (
                        "El pago no puede superar el saldo pendiente."
                    )
                })

        related_transaction = attrs.get("transaction")

        if (
            debt
            and related_transaction
            and related_transaction.business_id
            != debt.transaction.business_id
        ):
            raise serializers.ValidationError({
                "transaction": (
                    "La transacción del pago no pertenece "
                    "al mismo negocio de la deuda."
                )
            })

        return attrs

    @db_tx.atomic
    def create(self, validated_data):
        payment = super().create(validated_data)

        debt = Debt.objects.select_for_update().get(
            pk=payment.debt_id
        )

        debt.paid_amount += payment.amount
        debt.is_settled = (
            debt.paid_amount >= debt.total_amount
        )

        debt.save(
            update_fields=[
                "paid_amount",
                "is_settled",
            ]
        )

        return payment

# ---------- Notificaciones / Recordatorios ----------
class NotificationSerializer(serializers.ModelSerializer):
    user_email = serializers.EmailField(
        source="user.email",
        read_only=True,
    )
    business = public_id_field(
        Business,
        required=False,
        allow_null=True,
    )
    transaction = public_id_field(
        Transaction,
        required=False,
        allow_null=True,
    )

    class Meta:
        model = Notification
        fields = (
            "public_id",
            "title",
            "message",
            "type",
            "user",
            "user_email",
            "business",
            "transaction",
            "is_read",
            "sent_at",
            "scheduled_at",
            "created_at",
            "updated_at",
        )
        read_only_fields = (
            "public_id",
            "user",
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
                "transaction": (
                    "La transacción no pertenece al negocio seleccionado."
                )
            })

        return attrs

class ReminderSerializer(serializers.ModelSerializer):
    business = public_id_field(
        Business,
        required=False,
        allow_null=True,
    )
    transaction = public_id_field(
        Transaction,
        required=False,
        allow_null=True,
    )

    class Meta:
        model = Reminder
        fields = (
            "public_id",
            "title",
            "description",
            "due_date",
            "is_completed",
            "user",
            "business",
            "transaction",
            "created_at",
            "updated_at",
        )
        read_only_fields = (
            "public_id",
            "user",
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
                "transaction": (
                    "La transacción no pertenece al negocio seleccionado."
                )
            })

        return attrs

class BudgetSerializer(serializers.ModelSerializer):
    business = public_id_field(Business)
    status = public_id_field(EntityStatus, required=False)

    class Meta:
        model = Budget
        fields = (
            "public_id",
            "user",
            "business",
            "status",
            "period_start",
            "period_end",
            "allocated_amount",
            "used_amount",
            "created_at",
            "updated_at",
        )
        read_only_fields = (
            "public_id",
            "user",
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
    business = public_id_field(Business)

    class Meta:
        model = Goal
        fields = (
            "public_id",
            "user",
            "business",
            "name",
            "description",
            "target_amount",
            "current_amount",
            "start_date",
            "end_date",
            "is_completed",
            "created_at",
            "updated_at",
        )
        read_only_fields = (
            "public_id",
            "user",
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
    goal = public_id_field(Goal)
    transaction = public_id_field(
        Transaction,
        required=False,
        allow_null=True,
    )
    status = public_id_field(
        EntityStatus,
        required=False,
    )
    class Meta:
        model = GoalProgress
        fields = ("public_id", "goal", "amount", "transaction", "status", "note",
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
                "transaction": (
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
    employee = public_id_field(
        Employee
    )

    employee_name = serializers.CharField(
        source="employee.full_name",
        read_only=True,
    )

    business = serializers.SlugRelatedField(
        source="employee.business",
        slug_field="public_id",
        read_only=True,
    )

    business_name = serializers.CharField(
        source="employee.business.business_name",
        read_only=True,
    )

    class Meta:
        model = EmployeeCommissionPlan

        fields = (
            "public_id",
            "business",
            "business_name",
            "employee",
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
            "business",
            "business_name",
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
    employee = public_id_read_only()

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

    business = serializers.SlugRelatedField(
        source="employee.business",
        slug_field="public_id",
        read_only=True,
    )

    business_name = serializers.CharField(
        source=(
            "employee.business."
            "business_name"
        ),
        read_only=True,
    )

    business_currency = (
        serializers.CharField(
            source="employee.business.currency",
            read_only=True,
        )
    )

    created_by = serializers.SlugRelatedField(
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
            "business",
            "business_name",
            "business_currency",
            "employee",
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
            "created_by",
            "created_by_name",
            "created_at",
            "updated_at",
        )

        read_only_fields = fields

class CommissionSettlementCreateSerializer(
    serializers.Serializer
):
    employee = public_id_field(
        Employee
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

        excluded_status_names = [
            "Eliminado",
            "Anulado",
            "Cancelado",
            "Void",
            "Deleted",
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
            .exclude(
                status__name__in=(
                    excluded_status_names
                )
            )
        )

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
    business = public_id_read_only()
    employee = public_id_read_only(
        allow_null=True,
    )

    opened_by = serializers.SlugRelatedField(
        slug_field="public_id",
        read_only=True,
        allow_null=True,
    )

    closed_by = serializers.SlugRelatedField(
        slug_field="public_id",
        read_only=True,
        allow_null=True,
    )

    business_name = serializers.CharField(
        source="business.business_name",
        read_only=True,
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

            "business",
            "business_name",
            "business_currency",

            "employee",
            "employee_name",

            "opened_by",
            "opened_by_name",
            "closed_by",
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
    business = public_id_field(
        Business
    )

    employee = public_id_field(
        Employee
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
            "business",
            "employee",
            "opening_balance",
            "opening_notes",
        )

    def validate(self, attrs):
        business = attrs["business"]
        employee = attrs["employee"]

        if employee.business_id != business.id:
            raise serializers.ValidationError({
                "employee": (
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
                "business": (
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
    cash_register = public_id_field(
        CashRegister
    )

    employee = public_id_field(
        Employee,
        required=False,
        allow_null=True,
    )

    payment_method = public_id_field(
        PaymentMethod,
        required=False,
        allow_null=True,
    )

    created_by = serializers.SlugRelatedField(
        slug_field="public_id",
        read_only=True,
    )

    cash_register_status = serializers.CharField(
        source="cash_register.status",
        read_only=True,
    )

    business = serializers.SlugRelatedField(
        source="cash_register.business",
        slug_field="public_id",
        read_only=True,
    )

    business_name = serializers.CharField(
        source="cash_register.business.business_name",
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

            "cash_register",
            "cash_register_status",

            "business",
            "business_name",

            "employee",
            "employee_name",

            "payment_method",
            "payment_method_name",

            "movement_type",
            "amount",
            "signed_amount",
            "note",

            "created_by",
            "created_by_name",
            "created_at",
        )

        read_only_fields = (
            "public_id",
            "cash_register_status",
            "business",
            "business_name",
            "employee_name",
            "payment_method_name",
            "signed_amount",
            "created_by",
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
                "cash_register": (
                    "Debes indicar una caja."
                )
            })

        if (
            cash_register.status
            != CashRegister.STATUS_OPEN
        ):
            raise serializers.ValidationError({
                "cash_register": (
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
                "employee": (
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
                "payment_method": (
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
                "employee": (
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

# ---------- Lecturas (solo por si las quieres exponer) ----------
class StockMovementSerializer(serializers.ModelSerializer):
    product = public_id_read_only()
    variant = public_id_read_only(allow_null=True)
    transaction = public_id_read_only(allow_null=True)
    transaction_detail = public_id_read_only(allow_null=True)
    created_by_email = serializers.EmailField(
        source="created_by.email",
        read_only=True,
        allow_null=True,
    )

    class Meta:
        model = StockMovement
        fields = (
            "public_id",
            "product",
            "variant",
            "transaction",
            "transaction_detail",
            "note",
            "type",
            "quantity",
            "created_by_email",
            "created_at",
            "updated_at",
        )
        read_only_fields = fields

class SalesSummarySerializer(serializers.ModelSerializer):
    class Meta:
        model = SalesSummary
        fields = "__all__"

class SuppliersSummarySerializer(serializers.ModelSerializer):
    class Meta:
        model = SuppliersSummary
        fields = "__all__"

class CustomersSummarySerializer(serializers.ModelSerializer):
    class Meta:
        model = CustomersSummary
        fields = "__all__"

class PaymentsSummarySerializer(serializers.ModelSerializer):
    class Meta:
        model = PaymentsSummary
        fields = "__all__"

class DebtsSummarySerializer(serializers.ModelSerializer):
    class Meta:
        model = DebtsSummary
        fields = "__all__"

class InventorySummarySerializer(serializers.ModelSerializer):
    class Meta:
        model = InventorySummary
        fields = "__all__"