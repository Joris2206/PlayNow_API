from decimal import Decimal

from django.db import transaction as db_tx
from django.utils import timezone
from django.views.generic import detail
from rest_framework import serializers
from .models import (
    User, Business, EntityStatus,
    ProductCategory, Product, ProductVariantType, ProductVariant,
    Employee, Customer, Supplier, PaymentMethod,
    Transaction, TransactionDetail, StockMovement,
    Debt, DebtPayment, Notification, Reminder,
    Budget, Goal, GoalProgress,
    SalesSummary, SuppliersSummary, CustomersSummary,
    PaymentsSummary, DebtsSummary, InventorySummary,
)

class HealthSerializer(serializers.Serializer):
    status = serializers.CharField()
    version = serializers.CharField()
    
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

# ---------- Catálogos/Estados ----------
class EntityStatusSerializer(serializers.ModelSerializer):
    class Meta:
        model = EntityStatus
        fields = ("public_id", "name")

class PaymentMethodSerializer(serializers.ModelSerializer):
    class Meta:
        model = PaymentMethod
        fields = ("public_id", "name")

# ---------- Business ----------
class BusinessSerializer(serializers.ModelSerializer):
    status = serializers.PrimaryKeyRelatedField(
        queryset=EntityStatus.objects.all(),
        required=False,
        allow_null=True
    )
    class Meta:
        model = Business
        fields = ("public_id", "business_name", "description", "currency", "status", "created_at", "updated_at")
        read_only_fields = ("public_id", "created_at", "updated_at")
        extra_kwargs = {
            "status": {"required": False}
        }

    def create(self, validated_data):
        if not validated_data.get("status"):
            active = EntityStatus.objects.filter(name__iexact="Activo").first()

            if not active:
                raise serializers.ValidationError({"status": 'No existe el estado "Activo".'})
            validated_data["status"] = active

        return super().create(validated_data)

# ---------- Productos ----------
class ProductCategorySerializer(serializers.ModelSerializer):
    class Meta:
        model = ProductCategory
        fields = ("public_id", "name", "created_at", "updated_at")

class ProductSerializer(serializers.ModelSerializer):
    status = serializers.PrimaryKeyRelatedField(
        queryset=EntityStatus.objects.all(),
        required=False,
        allow_null=True
    )
    class Meta:
        model = Product
        fields = (
            "public_id", "business", "category", "title", "description", "image_url",
            "base_price", "base_cost", "stock", "is_visible", "status",
            "created_at", "updated_at"
        )
        read_only_fields = ("public_id", "created_at", "updated_at")

    def create(self, validated_data):
        if not validated_data.get("status"):
            active = EntityStatus.objects.filter(name__iexact="Activo").first()
            if not active:
                raise serializers.ValidationError({"status": 'No existe el estado "Activo".'})
            validated_data["status"] = active
        return super().create(validated_data)

class ProductVariantTypeSerializer(serializers.ModelSerializer):
    status = serializers.PrimaryKeyRelatedField(
        queryset=EntityStatus.objects.all(),
        required=False,
        allow_null=True,
    )

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

class ProductVariantSerializer(serializers.ModelSerializer):
    status = serializers.PrimaryKeyRelatedField(
        queryset=EntityStatus.objects.all(),
        required=False,
        allow_null=True
    )
    class Meta:
        model = ProductVariant
        fields = ("public_id", "variant_type", "label", "additional_price", "stock", "status")

    def create(self, validated_data):
        if not validated_data.get("status"):
            active = EntityStatus.objects.filter(name__iexact="Activo").first()
            if not active:
                raise serializers.ValidationError({"status": 'No existe el estado "Activo".'})
            validated_data["status"] = active
        return super().create(validated_data)

# ---------- Personas/Entidades ----------
class EmployeeSerializer(serializers.ModelSerializer):
    status = serializers.PrimaryKeyRelatedField(
        queryset=EntityStatus.objects.all(),
        required=False,
        allow_null=True
    )
    class Meta:
        model = Employee
        fields = ("public_id", "business", "full_name", "phone", "role", "status", "created_at", "updated_at")

    def create(self, validated_data):
        if not validated_data.get("status"):
            active = EntityStatus.objects.filter(name__iexact="Activo").first()
            if not active:
                raise serializers.ValidationError({"status": 'No existe el estado "Activo".'})
            validated_data["status"] = active
        return super().create(validated_data)

class CustomerSerializer(serializers.ModelSerializer):
    status = serializers.PrimaryKeyRelatedField(
        queryset=EntityStatus.objects.all(),
        required=False,
        allow_null=True
    )
    class Meta:
        model = Customer
        fields = ("public_id", "business", "full_name", "phone", "email", "status", "created_at", "updated_at")

    def create(self, validated_data):
        if not validated_data.get("status"):
            active = EntityStatus.objects.filter(name__iexact="Activo").first()
            if not active:
                raise serializers.ValidationError({"status": 'No existe el estado "Activo".'})
            validated_data["status"] = active
        return super().create(validated_data)

class SupplierSerializer(serializers.ModelSerializer):
    status = serializers.PrimaryKeyRelatedField(
        queryset=EntityStatus.objects.all(),
        required=False,
        allow_null=True
    )
    class Meta:
        model = Supplier
        fields = ("public_id", "business", "name", "phone", "email", "status", "created_at", "updated_at")
    
    def create(self, validated_data):
        if not validated_data.get("status"):
            active = EntityStatus.objects.filter(name__iexact="Activo").first()
            if not active:
                raise serializers.ValidationError({"status": 'No existe el estado "Activo".'})
            validated_data["status"] = active
        return super().create(validated_data)

# ---------- Transacciones ----------
class TransactionDetailSerializer(
    serializers.ModelSerializer
):
    product_title = serializers.CharField(
        source="product.title",
        read_only=True,
    )

    variant_label = serializers.CharField(
        source="variant.label",
        read_only=True,
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
            "total_price",
        )

class TransactionSerializer(
    serializers.ModelSerializer
):
    status = serializers.PrimaryKeyRelatedField(
        queryset=EntityStatus.objects.all(),
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

    class Meta:
        model = Transaction
        fields = (
            "public_id",
            "business",
            "customer",
            "supplier",
            "employee",
            "payment_method",
            "type",
            "is_debt",
            "discount_percent",
            "concept",
            "total_value",
            "expense_amount",
            "status",
            "invoice_number",
            "payment_status",
            "invoice_series",
            "invoice_file_url",
            "details",
            "business_currency",
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
            unit_price = detail.get(
                "unit_price"
            )

            if product is None:
                raise serializers.ValidationError(
                    f"Detalle #{index}: debe "
                    "incluir producto."
                )

            if (
                quantity is None
                or quantity <= 0
            ):
                raise serializers.ValidationError(
                    f"Detalle #{index}: quantity "
                    "debe ser mayor que 0."
                )

            if (
                unit_price is not None
                and unit_price
                < Decimal("0.00")
            ):
                raise serializers.ValidationError(
                    f"Detalle #{index}: unit_price "
                    "no puede ser negativo."
                )

            if (
                variant is not None
                and
                variant.variant_type.product_id
                != product.id
            ):
                raise serializers.ValidationError(
                    f"Detalle #{index}: la variante "
                    "no pertenece al producto."
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
            variant = detail_data.get(
                "variant"
            )
            quantity = detail_data["quantity"]
            unit_price = detail_data.get(
                "unit_price"
            )

            if unit_price is None:
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
                        "calculado no puede ser "
                        "negativo."
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
    class Meta:
        model = Debt
        fields = ("public_id", "transaction", "total_amount", "paid_amount", "interest_rate",
                  "term_months", "due_date", "is_settled", "created_at", "updated_at")
        read_only_fields = ("public_id", "created_at", "updated_at", "is_settled")

class DebtPaymentSerializer(serializers.ModelSerializer):
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
    user_email = serializers.EmailField(source="user.email", read_only=True)
    class Meta:
        model = Notification
        fields = ("public_id", "title", "message", "type", "user", "user_email",
                  "business", "transaction", "is_read", "sent_at", "scheduled_at",
                  "created_at", "updated_at")

class ReminderSerializer(serializers.ModelSerializer):
    class Meta:
        model = Reminder
        fields = ("public_id", "title", "description", "due_date", "is_completed",
                  "user", "business", "transaction", "created_at", "updated_at")

# ---------- Presupuesto / Metas ----------
class BudgetSerializer(serializers.ModelSerializer):
    class Meta:
        model = Budget
        fields = ("public_id", "user", "business", "status", "period_start", "period_end",
                  "allocated_amount", "used_amount", "created_at", "updated_at")

class GoalSerializer(serializers.ModelSerializer):
    class Meta:
        model = Goal
        fields = ("public_id", "user", "business", "name", "description", "target_amount",
                  "current_amount", "start_date", "end_date", "is_completed",
                  "created_at", "updated_at")

class GoalProgressSerializer(serializers.ModelSerializer):
    class Meta:
        model = GoalProgress
        fields = ("public_id", "goal", "amount", "transaction", "status", "note",
                  "created_at", "updated_at")

    @db_tx.atomic
    def create(self, validated_data):
        gp = super().create(validated_data)
        goal = gp.goal
        goal.current_amount = (goal.current_amount or 0) + gp.amount
        if goal.current_amount >= goal.target_amount:
            goal.is_completed = True
        goal.save(update_fields=["current_amount", "is_completed"])
        return gp

# ---------- Lecturas (solo por si las quieres exponer) ----------
class StockMovementSerializer(serializers.ModelSerializer):
    class Meta:
        model = StockMovement
        fields = ("public_id", "product", "variant", "transaction", "note", "type", "quantity", "created_at", "updated_at")

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
