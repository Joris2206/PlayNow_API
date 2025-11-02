from django.urls import path, include
from rest_framework.routers import DefaultRouter
from .views import (
    healthcheck, RegisterViewSet,
    BusinessViewSet, EntityStatusViewSet,
    ProductCategoryViewSet, ProductViewSet, ProductVariantTypeViewSet, ProductVariantViewSet,
    EmployeeViewSet, CustomerViewSet, SupplierViewSet, PaymentMethodViewSet,
    TransactionViewSet, DebtViewSet, DebtPaymentViewSet,
    NotificationViewSet, ReminderViewSet,
    BudgetViewSet, GoalViewSet, GoalProgressViewSet,
    StockMovementViewSet, UserViewSet, PasswordResetRequestView, PasswordResetConfirmView,
)

router = DefaultRouter()
# auth
router.register(r'auth/register', RegisterViewSet, basename='register')

# catálogos/estatus
router.register(r'statuses', EntityStatusViewSet, basename='entitystatus')
router.register(r'payment-methods', PaymentMethodViewSet, basename='paymentmethod')

# negocio
router.register(r'businesses', BusinessViewSet, basename='business')

# productos
router.register(r'categories', ProductCategoryViewSet, basename='productcategory')
router.register(r'products', ProductViewSet, basename='product')
router.register(r'variant-types', ProductVariantTypeViewSet, basename='productvarianttype')
router.register(r'variants', ProductVariantViewSet, basename='productvariant')

# entidades
router.register(r'employees', EmployeeViewSet, basename='employee')
router.register(r'customers', CustomerViewSet, basename='customer')
router.register(r'suppliers', SupplierViewSet, basename='supplier')

# transacciones / deudas
router.register(r'transactions', TransactionViewSet, basename='transaction')
router.register(r'debts', DebtViewSet, basename='debt')
router.register(r'debt-payments', DebtPaymentViewSet, basename='debtpayment')

# notificaciones / recordatorios
router.register(r'notifications', NotificationViewSet, basename='notification')
router.register(r'reminders', ReminderViewSet, basename='reminder')

# presupuesto / metas
router.register(r'budgets', BudgetViewSet, basename='budget')
router.register(r'goals', GoalViewSet, basename='goal')
router.register(r'goal-progress', GoalProgressViewSet, basename='goalprogress')

# inventario
router.register(r'stock-movements', StockMovementViewSet, basename='stockmovement')

# User
router.register(r'users', UserViewSet, basename='user')

urlpatterns = [
    path("auth/password/reset/", PasswordResetRequestView.as_view(), name="password-reset-request"),
    path("auth/password/reset/confirm/", PasswordResetConfirmView.as_view(), name="password-reset-confirm"),
    path('health/', healthcheck, name='healthcheck'),
    path('', include(router.urls)),
]
