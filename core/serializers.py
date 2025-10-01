from django.db import transaction as db_tx
from django.utils import timezone
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
    password = serializers.CharField(write_only=True, min_length=6)

    class Meta:
        model = User
        fields = ("public_id", "email", "full_name", "password")
        read_only_fields = ("public_id",)  # ← importante

    def create(self, validated_data):
        return User.objects.create_user(
            email=validated_data["email"],
            full_name=validated_data["full_name"],
            password=validated_data["password"],
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
    class Meta:
        model = Business
        fields = ("public_id", "business_name", "description", "currency", "status", "created_at", "updated_at")
        read_only_fields = ("public_id", "created_at", "updated_at")
    def create(self, validated_data):
        # amarra el negocio al usuario autenticado
        validated_data["user"] = self.context["request"].user
        return super().create(validated_data)

# ---------- Productos ----------
class ProductCategorySerializer(serializers.ModelSerializer):
    class Meta:
        model = ProductCategory
        fields = ("public_id", "name", "created_at", "updated_at")

class ProductSerializer(serializers.ModelSerializer):
    class Meta:
        model = Product
        fields = (
            "public_id", "business", "category", "title", "description", "image_url",
            "base_price", "base_cost", "stock", "is_visible", "status",
            "created_at", "updated_at"
        )
        read_only_fields = ("public_id", "created_at", "updated_at")

class ProductVariantTypeSerializer(serializers.ModelSerializer):
    class Meta:
        model = ProductVariantType
        fields = ("public_id", "product", "name")

class ProductVariantSerializer(serializers.ModelSerializer):
    class Meta:
        model = ProductVariant
        fields = ("public_id", "variant_type", "label", "additional_price", "stock", "status")

# ---------- Personas/Entidades ----------
class EmployeeSerializer(serializers.ModelSerializer):
    class Meta:
        model = Employee
        fields = ("public_id", "business", "full_name", "phone", "role", "status", "created_at", "updated_at")

class CustomerSerializer(serializers.ModelSerializer):
    class Meta:
        model = Customer
        fields = ("public_id", "business", "full_name", "phone", "email", "status", "created_at", "updated_at")

class SupplierSerializer(serializers.ModelSerializer):
    class Meta:
        model = Supplier
        fields = ("public_id", "business", "name", "phone", "email", "status", "created_at", "updated_at")

# ---------- Transacciones ----------
class TransactionDetailSerializer(serializers.ModelSerializer):
    product_title = serializers.CharField(source="product.title", read_only=True)
    variant_label = serializers.CharField(source="variant.label", read_only=True)

    class Meta:
        model = TransactionDetail
        fields = ("public_id", "product", "product_title", "variant", "variant_label",
                  "quantity", "unit_price", "total_price")
        read_only_fields = ("public_id", "total_price")

class TransactionSerializer(serializers.ModelSerializer):
    details = TransactionDetailSerializer(many=True)
    business_currency = serializers.CharField(source="business.currency", read_only=True)

    class Meta:
        model = Transaction
        fields = (
            "public_id", "business", "customer", "supplier", "employee", "payment_method",
            "type", "is_debt", "discount_percent", "concept", "total_value",
            "status", "invoice_number", "payment_status", "invoice_series", "invoice_file_url",
            "details", "business_currency", "created_at", "updated_at"
        )
        read_only_fields = ("public_id", "total_value", "created_at", "updated_at")

    def validate(self, attrs):
        ttype = attrs.get("type")
        if ttype not in dict(Transaction.TRANSACTION_TYPES):
            raise serializers.ValidationError({"type": "Invalid transaction type."})
        return attrs

    @db_tx.atomic
    def create(self, validated_data):
        details_data = validated_data.pop("details", [])
        # crear transacción base con total 0 y luego acumular
        tx_obj = Transaction.objects.create(**validated_data, total_value=0)

        total = 0
        for d in details_data:
            product = d["product"]
            variant = d.get("variant")
            qty = d["quantity"]

            # calcular unit_price si no viene
            unit_price = d.get("unit_price")
            if unit_price is None:
                base = product.base_price
                if variant:
                    base = base + variant.additional_price
                unit_price = base

            line_total = unit_price * qty

            # validar stock (en sales/purchases/adjust)
            if tx_obj.type == "sale":
                # baja stock de variant si existe, si no, del producto
                if variant:
                    if variant.stock < qty:
                        raise serializers.ValidationError({"details": f"Stock insuficiente en variante {variant.label}"})
                    variant.stock -= qty
                    variant.save(update_fields=["stock"])
                else:
                    if product.stock < qty:
                        raise serializers.ValidationError({"details": f"Stock insuficiente en producto {product.title}"})
                    product.stock -= qty
                    product.save(update_fields=["stock"])

                StockMovement.objects.create(
                    product=product,
                    variant=variant,
                    transaction=tx_obj,
                    note="Venta",
                    type="sale",
                    quantity=qty
                )

            elif tx_obj.type == "purchase":
                # compra -> entra stock
                if variant:
                    variant.stock += qty
                    variant.save(update_fields=["stock"])
                else:
                    product.stock += qty
                    product.save(update_fields=["stock"])

                StockMovement.objects.create(
                    product=product,
                    variant=variant,
                    transaction=tx_obj,
                    note="Compra",
                    type="entry",
                    quantity=qty
                )
            else:  # expense
                # gasto: no toca inventario por defecto; ajusta si quieres
                pass

            TransactionDetail.objects.create(
                transaction=tx_obj,
                product=product,
                variant=variant,
                quantity=qty,
                unit_price=unit_price,
                total_price=line_total
            )
            total += line_total

        # aplicar descuento si hay
        discount = validated_data.get("discount_percent") or 0
        if discount:
            total = total * (1 - (discount / 100))

        tx_obj.total_value = total
        tx_obj.save(update_fields=["total_value"])

        # si es deuda, crear registro Debt
        if tx_obj.is_debt or (tx_obj.payment_status and tx_obj.payment_status.lower() in {"partial", "pending"}):
            Debt.objects.create(
                transaction=tx_obj,
                total_amount=tx_obj.total_value,
                paid_amount=0,
                interest_rate=0,
                term_months=0,
                due_date=timezone.now().date(),
                is_settled=False
            )

        return tx_obj

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
        fields = ("public_id", "debt", "amount", "payment_date", "method", "transaction", "created_at", "updated_at")
        read_only_fields = ("public_id", "created_at", "updated_at")

    @db_tx.atomic
    def create(self, validated_data):
        payment = super().create(validated_data)
        debt = payment.debt
        debt.paid_amount = (debt.paid_amount or 0) + payment.amount
        if debt.paid_amount >= debt.total_amount:
            debt.is_settled = True
        debt.save(update_fields=["paid_amount", "is_settled"])
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
