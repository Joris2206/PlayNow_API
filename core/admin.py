from django.contrib import admin

# Register your models here.
from .models import (
    User, Business, EntityStatus, ProductCategory, Product, ProductVariantType,
    ProductVariant, Employee, Customer, Supplier, Transaction, TransactionDetail,
    Notification, Reminder, Debt, DebtPayment, Budget, Goal, GoalProgress,
    CashRegister, ActivityLog, StockMovement, SalesSummary, SuppliersSummary,
    CustomersSummary, PaymentsSummary, DebtsSummary, InventorySummary
)

# Usuarios
@admin.register(User)
class UserAdmin(admin.ModelAdmin):
    list_display = ('email', 'full_name', 'role', 'is_active', 'created_at')
    search_fields = ('email', 'full_name', 'role')
    list_filter = ('role', 'is_active')

# Empresas
@admin.register(Business)
class BusinessAdmin(admin.ModelAdmin):
    list_display = ('business_name', 'user', 'currency', 'status', 'created_at')
    search_fields = ('business_name', 'user__email')
    list_filter = ('status',)

# Estados generales
admin.site.register(EntityStatus)

# Categorías y productos
@admin.register(ProductCategory)
class ProductCategoryAdmin(admin.ModelAdmin):
    list_display = ('name', 'created_at')
    search_fields = ('name',)

@admin.register(Product)
class ProductAdmin(admin.ModelAdmin):
    list_display = ('title', 'business', 'category', 'base_price', 'stock', 'status')
    list_filter = ('status', 'category')
    search_fields = ('title', 'business__business_name')

@admin.register(ProductVariantType)
class ProductVariantTypeAdmin(admin.ModelAdmin):
    list_display = ('name', 'product')
    search_fields = ('name', 'product__title')

@admin.register(ProductVariant)
class ProductVariantAdmin(admin.ModelAdmin):
    list_display = ('label', 'variant_type', 'additional_price', 'stock', 'status')
    list_filter = ('status',)
    search_fields = ('label', 'variant_type__name')

# Empleados, clientes, proveedores
@admin.register(Employee)
class EmployeeAdmin(admin.ModelAdmin):
    list_display = ('full_name', 'business', 'role', 'status')
    list_filter = ('status', 'role')
    search_fields = ('full_name', 'business__business_name')

@admin.register(Customer)
class CustomerAdmin(admin.ModelAdmin):
    list_display = ('full_name', 'business', 'phone', 'email', 'status')
    list_filter = ('status',)
    search_fields = ('full_name', 'email', 'business__business_name')

@admin.register(Supplier)
class SupplierAdmin(admin.ModelAdmin):
    list_display = ('name', 'business', 'phone', 'email', 'status')
    list_filter = ('status',)
    search_fields = ('name', 'email', 'business__business_name')

# Transacciones y detalles
@admin.register(Transaction)
class TransactionAdmin(admin.ModelAdmin):
    list_display = ('id', 'business', 'type', 'total_value', 'status', 'created_at')
    list_filter = ('type', 'status')
    search_fields = ('id', 'business__business_name')

@admin.register(TransactionDetail)
class TransactionDetailAdmin(admin.ModelAdmin):
    list_display = ('transaction', 'product', 'variant', 'quantity', 'unit_price', 'total_price')
    search_fields = ('transaction__id', 'product__title')

# Notificaciones y recordatorios
@admin.register(Notification)
class NotificationAdmin(admin.ModelAdmin):
    list_display = ('title', 'user', 'business', 'is_read', 'sent_at')
    list_filter = ('is_read',)
    search_fields = ('title', 'user__email')

@admin.register(Reminder)
class ReminderAdmin(admin.ModelAdmin):
    list_display = ('title', 'user', 'business', 'due_date', 'is_completed')
    list_filter = ('is_completed',)
    search_fields = ('title', 'user__email')

# Deudas y pagos
@admin.register(Debt)
class DebtAdmin(admin.ModelAdmin):
    list_display = ('transaction', 'total_amount', 'paid_amount', 'is_settled', 'due_date')
    list_filter = ('is_settled',)
    search_fields = ('transaction__id',)

@admin.register(DebtPayment)
class DebtPaymentAdmin(admin.ModelAdmin):
    list_display = ('debt', 'amount', 'payment_date', 'method', 'transaction')
    search_fields = ('debt__id', 'transaction__id')

# Presupuestos, metas y progreso
@admin.register(Budget)
class BudgetAdmin(admin.ModelAdmin):
    list_display = ('user', 'business', 'allocated_amount', 'used_amount', 'status')
    list_filter = ('status',)
    search_fields = ('user__email', 'business__business_name')

@admin.register(Goal)
class GoalAdmin(admin.ModelAdmin):
    list_display = ('name', 'user', 'business', 'target_amount', 'current_amount', 'is_completed')
    list_filter = ('is_completed',)
    search_fields = ('name', 'user__email')

@admin.register(GoalProgress)
class GoalProgressAdmin(admin.ModelAdmin):
    list_display = ('goal', 'amount', 'status', 'transaction', 'created_at')
    list_filter = ('status',)
    search_fields = ('goal__name', 'transaction__id')

# Caja, movimientos y logs
@admin.register(CashRegister)
class CashRegisterAdmin(admin.ModelAdmin):
    list_display = ('business', 'employee', 'open_time', 'close_time', 'status')
    list_filter = ('status',)
    search_fields = ('business__business_name', 'employee__full_name')

@admin.register(ActivityLog)
class ActivityLogAdmin(admin.ModelAdmin):
    list_display = ('user', 'action', 'entity_type', 'entity_id', 'created_at')
    search_fields = ('user__email', 'action', 'entity_type')

@admin.register(StockMovement)
class StockMovementAdmin(admin.ModelAdmin):
    list_display = ('product', 'variant', 'transaction', 'type', 'quantity', 'created_at')
    list_filter = ('type',)
    search_fields = ('product__title', 'variant__label', 'transaction__id')

# Resúmenes / reportes
admin.site.register(SalesSummary)
admin.site.register(SuppliersSummary)
admin.site.register(CustomersSummary)
admin.site.register(PaymentsSummary)
admin.site.register(DebtsSummary)
admin.site.register(InventorySummary)