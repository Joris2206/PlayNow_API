from django.contrib import admin
from django.contrib.auth.admin import UserAdmin as BaseUserAdmin

from .models import (
    User,
    Business,
    EntityStatus,
    ProductCategory,
    Product,
    ProductVariantType,
    ProductVariant,
    Employee,
    Customer,
    Supplier,
    PaymentMethod,
    Transaction,
    TransactionDetail,
    Notification,
    Reminder,
    Debt,
    DebtPayment,
    Budget,
    Goal,
    GoalProgress,
    CashRegister,
    ActivityLog,
    StockMovement,
)


# ---------------------------------------------------------------------
# Usuarios
# ---------------------------------------------------------------------

@admin.register(User)
class UserAdmin(BaseUserAdmin):
    ordering = ("email",)

    list_display = (
        "email",
        "full_name",
        "role",
        "is_active",
        "is_staff",
        "is_superuser",
        "created_at",
    )

    list_filter = (
        "role",
        "is_active",
        "is_staff",
        "is_superuser",
    )

    search_fields = (
        "email",
        "full_name",
        "phone",
    )

    readonly_fields = (
        "public_id",
        "last_login",
        "created_at",
        "updated_at",
    )

    fieldsets = (
        (
            "Identificación",
            {
                "fields": (
                    "public_id",
                    "email",
                    "full_name",
                    "phone",
                    "role",
                )
            },
        ),
        (
            "Seguridad",
            {
                "fields": (
                    "password",
                    "is_active",
                    "is_staff",
                    "is_superuser",
                    "groups",
                    "user_permissions",
                )
            },
        ),
        (
            "Fechas",
            {
                "fields": (
                    "last_login",
                    "created_at",
                    "updated_at",
                )
            },
        ),
    )

    add_fieldsets = (
        (
            "Crear usuario",
            {
                "classes": ("wide",),
                "fields": (
                    "email",
                    "full_name",
                    "phone",
                    "role",
                    "password1",
                    "password2",
                    "is_active",
                    "is_staff",
                    "is_superuser",
                ),
            },
        ),
    )

    filter_horizontal = (
        "groups",
        "user_permissions",
    )


# ---------------------------------------------------------------------
# Negocios y catálogos
# ---------------------------------------------------------------------

@admin.register(Business)
class BusinessAdmin(admin.ModelAdmin):
    list_display = (
        "business_name",
        "user",
        "currency",
        "status",
        "created_at",
    )

    search_fields = (
        "business_name",
        "user__email",
        "public_id",
    )

    list_filter = (
        "status",
        "currency",
    )

    readonly_fields = (
        "public_id",
        "created_at",
        "updated_at",
    )

    list_select_related = (
        "user",
        "status",
    )


@admin.register(EntityStatus)
class EntityStatusAdmin(admin.ModelAdmin):
    list_display = (
        "name",
        "public_id",
    )

    search_fields = (
        "name",
        "public_id",
    )

    readonly_fields = (
        "public_id",
    )


@admin.register(PaymentMethod)
class PaymentMethodAdmin(admin.ModelAdmin):
    list_display = (
        "name",
        "public_id",
    )

    search_fields = (
        "name",
        "public_id",
    )

    readonly_fields = (
        "public_id",
    )


# ---------------------------------------------------------------------
# Productos e inventario
# ---------------------------------------------------------------------

@admin.register(ProductCategory)
class ProductCategoryAdmin(admin.ModelAdmin):
    list_display = (
        "name",
        "business",
        "status",
        "created_at",
    )

    search_fields = (
        "name",
        "business__business_name",
        "public_id",
    )

    list_filter = (
        "status",
        "business",
    )

    readonly_fields = (
        "public_id",
        "created_at",
        "updated_at",
    )

    list_select_related = (
        "business",
        "status",
    )


@admin.register(Product)
class ProductAdmin(admin.ModelAdmin):
    list_display = (
        "title",
        "business",
        "category",
        "base_price",
        "base_cost",
        "stock",
        "is_visible",
        "status",
    )

    list_filter = (
        "status",
        "is_visible",
        "business",
        "category",
    )

    search_fields = (
        "title",
        "business__business_name",
        "public_id",
    )

    readonly_fields = (
        "public_id",
        "created_at",
        "updated_at",
    )

    list_select_related = (
        "business",
        "category",
        "status",
    )


@admin.register(ProductVariantType)
class ProductVariantTypeAdmin(admin.ModelAdmin):
    list_display = (
        "name",
        "product",
        "status",
        "created_at",
    )

    search_fields = (
        "name",
        "product__title",
        "product__business__business_name",
        "public_id",
    )

    list_filter = (
        "status",
    )

    readonly_fields = (
        "public_id",
        "created_at",
        "updated_at",
    )

    list_select_related = (
        "product",
        "status",
    )


@admin.register(ProductVariant)
class ProductVariantAdmin(admin.ModelAdmin):
    list_display = (
        "label",
        "variant_type",
        "additional_price",
        "stock",
        "status",
        "created_at",
    )

    list_filter = (
        "status",
    )

    search_fields = (
        "label",
        "variant_type__name",
        "variant_type__product__title",
        "public_id",
    )

    readonly_fields = (
        "public_id",
        "created_at",
        "updated_at",
    )

    list_select_related = (
        "variant_type",
        "status",
    )


# ---------------------------------------------------------------------
# Empleados, clientes y proveedores
# ---------------------------------------------------------------------

@admin.register(Employee)
class EmployeeAdmin(admin.ModelAdmin):
    list_display = (
        "full_name",
        "business",
        "position",
        "phone",
        "status",
        "created_at",
    )

    list_filter = (
        "status",
        "position",
        "business",
    )

    search_fields = (
        "full_name",
        "phone",
        "position",
        "business__business_name",
        "public_id",
    )

    readonly_fields = (
        "public_id",
        "created_at",
        "updated_at",
    )

    list_select_related = (
        "business",
        "status",
    )


@admin.register(Customer)
class CustomerAdmin(admin.ModelAdmin):
    list_display = (
        "full_name",
        "business",
        "phone",
        "email",
        "status",
        "created_at",
    )

    list_filter = (
        "status",
        "business",
    )

    search_fields = (
        "full_name",
        "email",
        "phone",
        "business__business_name",
        "public_id",
    )

    readonly_fields = (
        "public_id",
        "created_at",
        "updated_at",
    )

    list_select_related = (
        "business",
        "status",
    )


@admin.register(Supplier)
class SupplierAdmin(admin.ModelAdmin):
    list_display = (
        "name",
        "business",
        "phone",
        "email",
        "status",
        "created_at",
    )

    list_filter = (
        "status",
        "business",
    )

    search_fields = (
        "name",
        "email",
        "phone",
        "business__business_name",
        "public_id",
    )

    readonly_fields = (
        "public_id",
        "created_at",
        "updated_at",
    )

    list_select_related = (
        "business",
        "status",
    )


# ---------------------------------------------------------------------
# Transacciones
# ---------------------------------------------------------------------

class TransactionDetailInline(admin.TabularInline):
    model = TransactionDetail
    extra = 0

    fields = (
        "product",
        "variant",
        "quantity",
        "unit_price",
        "total_price",
    )

    readonly_fields = (
        "total_price",
    )


@admin.register(Transaction)
class TransactionAdmin(admin.ModelAdmin):
    list_display = (
        "public_id",
        "business",
        "type",
        "employee",
        "payment_method",
        "total_value",
        "is_debt",
        "status",
        "created_at",
    )

    list_filter = (
        "type",
        "status",
        "is_debt",
        "payment_method",
        "business",
    )

    search_fields = (
        "public_id",
        "invoice_number",
        "invoice_series",
        "business__business_name",
        "customer__full_name",
        "supplier__name",
        "employee__full_name",
    )

    readonly_fields = (
        "public_id",
        "created_at",
        "updated_at",
    )

    list_select_related = (
        "business",
        "customer",
        "supplier",
        "employee",
        "payment_method",
        "status",
    )

    inlines = (
        TransactionDetailInline,
    )


@admin.register(TransactionDetail)
class TransactionDetailAdmin(admin.ModelAdmin):
    list_display = (
        "transaction",
        "product",
        "variant",
        "quantity",
        "unit_price",
        "total_price",
    )

    search_fields = (
        "transaction__public_id",
        "product__title",
        "variant__label",
        "public_id",
    )

    readonly_fields = (
        "public_id",
        "total_price",
    )

    list_select_related = (
        "transaction",
        "product",
        "variant",
    )


# ---------------------------------------------------------------------
# Notificaciones y recordatorios
# ---------------------------------------------------------------------

@admin.register(Notification)
class NotificationAdmin(admin.ModelAdmin):
    list_display = (
        "title",
        "user",
        "business",
        "type",
        "is_read",
        "sent_at",
        "scheduled_at",
        "created_at",
    )

    list_filter = (
        "type",
        "is_read",
        "business",
    )

    search_fields = (
        "title",
        "message",
        "user__email",
        "business__business_name",
        "public_id",
    )

    readonly_fields = (
        "public_id",
        "created_at",
        "updated_at",
    )

    list_select_related = (
        "user",
        "business",
        "transaction",
    )


@admin.register(Reminder)
class ReminderAdmin(admin.ModelAdmin):
    list_display = (
        "title",
        "user",
        "business",
        "due_date",
        "is_completed",
        "created_at",
    )

    list_filter = (
        "is_completed",
        "due_date",
        "business",
    )

    search_fields = (
        "title",
        "description",
        "user__email",
        "business__business_name",
        "public_id",
    )

    readonly_fields = (
        "public_id",
        "created_at",
        "updated_at",
    )

    list_select_related = (
        "user",
        "business",
        "transaction",
    )


# ---------------------------------------------------------------------
# Deudas y pagos
# ---------------------------------------------------------------------

@admin.register(Debt)
class DebtAdmin(admin.ModelAdmin):
    list_display = (
        "transaction",
        "total_amount",
        "paid_amount",
        "is_settled",
        "due_date",
        "created_at",
    )

    list_filter = (
        "is_settled",
        "due_date",
    )

    search_fields = (
        "transaction__public_id",
        "public_id",
    )

    readonly_fields = (
        "public_id",
        "created_at",
        "updated_at",
    )

    list_select_related = (
        "transaction",
    )


@admin.register(DebtPayment)
class DebtPaymentAdmin(admin.ModelAdmin):
    list_display = (
        "debt",
        "amount",
        "payment_date",
        "payment_method",
        "transaction",
        "created_at",
    )

    list_filter = (
        "payment_method",
        "payment_date",
    )

    search_fields = (
        "debt__public_id",
        "debt__transaction__public_id",
        "transaction__public_id",
        "payment_method__name",
        "public_id",
    )

    readonly_fields = (
        "public_id",
        "created_at",
        "updated_at",
    )

    list_select_related = (
        "debt",
        "payment_method",
        "transaction",
    )

    ordering = (
        "-payment_date",
        "-created_at",
    )


# ---------------------------------------------------------------------
# Presupuestos y metas
# ---------------------------------------------------------------------

@admin.register(Budget)
class BudgetAdmin(admin.ModelAdmin):
    list_display = (
        "user",
        "business",
        "period_start",
        "period_end",
        "allocated_amount",
        "used_amount",
        "status",
    )

    list_filter = (
        "status",
        "business",
        "period_start",
    )

    search_fields = (
        "user__email",
        "business__business_name",
        "public_id",
    )

    readonly_fields = (
        "public_id",
        "created_at",
        "updated_at",
    )

    list_select_related = (
        "user",
        "business",
        "status",
    )


@admin.register(Goal)
class GoalAdmin(admin.ModelAdmin):
    list_display = (
        "name",
        "user",
        "business",
        "target_amount",
        "current_amount",
        "is_completed",
        "start_date",
        "end_date",
    )

    list_filter = (
        "is_completed",
        "business",
        "start_date",
        "end_date",
    )

    search_fields = (
        "name",
        "user__email",
        "business__business_name",
        "public_id",
    )

    readonly_fields = (
        "public_id",
        "created_at",
        "updated_at",
    )

    list_select_related = (
        "user",
        "business",
    )


@admin.register(GoalProgress)
class GoalProgressAdmin(admin.ModelAdmin):
    list_display = (
        "goal",
        "amount",
        "status",
        "transaction",
        "created_at",
    )

    list_filter = (
        "status",
        "created_at",
    )

    search_fields = (
        "goal__name",
        "goal__public_id",
        "transaction__public_id",
        "public_id",
    )

    readonly_fields = (
        "public_id",
        "created_at",
        "updated_at",
    )

    list_select_related = (
        "goal",
        "status",
        "transaction",
    )


# ---------------------------------------------------------------------
# Caja
# ---------------------------------------------------------------------

@admin.register(CashRegister)
class CashRegisterAdmin(admin.ModelAdmin):
    list_display = (
        "business",
        "employee",
        "open_time",
        "close_time",
        "opening_balance",
        "closing_balance",
        "status",
    )

    list_filter = (
        "status",
        "business",
    )

    search_fields = (
        "business__business_name",
        "employee__full_name",
        "public_id",
    )

    readonly_fields = (
        "public_id",
    )

    list_select_related = (
        "business",
        "employee",
    )


# ---------------------------------------------------------------------
# Auditoría e inventario inmutable
# ---------------------------------------------------------------------

@admin.register(ActivityLog)
class ActivityLogAdmin(admin.ModelAdmin):
    list_display = (
        "user",
        "business",
        "action",
        "entity_type",
        "entity_id",
        "created_at",
    )

    list_filter = (
        "action",
        "entity_type",
        "business",
        "created_at",
    )

    search_fields = (
        "user__email",
        "action",
        "entity_type",
        "entity_id",
        "business__business_name",
        "public_id",
    )

    readonly_fields = (
        "public_id",
        "user",
        "business",
        "action",
        "entity_type",
        "entity_id",
        "metadata",
        "created_at",
    )

    list_select_related = (
        "user",
        "business",
    )

    ordering = (
        "-created_at",
    )

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False


@admin.register(StockMovement)
class StockMovementAdmin(admin.ModelAdmin):
    list_display = (
        "product",
        "variant",
        "transaction",
        "type",
        "quantity",
        "created_at",
    )

    list_filter = (
        "type",
        "created_at",
    )

    search_fields = (
        "product__title",
        "variant__label",
        "transaction__public_id",
        "public_id",
    )

    readonly_fields = (
        "public_id",
        "product",
        "variant",
        "transaction",
        "transaction_detail",
        "note",
        "type",
        "quantity",
        "created_at",
        "updated_at",
    )

    list_select_related = (
        "product",
        "variant",
        "transaction",
        "transaction_detail",
    )

    ordering = (
        "-created_at",
    )

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False


