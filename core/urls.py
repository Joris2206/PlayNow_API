from django.urls import path, include
from rest_framework.routers import DefaultRouter
from .views import (
    CommissionSettlementViewSet, CustomerSummaryView, DebtSummaryView, InventorySummaryView, SupplierSummaryView, healthcheck, RegisterViewSet,
    BusinessViewSet, EntityStatusViewSet,
    ProductCategoryViewSet, ProductViewSet, ProductVariantTypeViewSet, ProductVariantViewSet,
    EmployeeViewSet, CustomerViewSet, SupplierViewSet, PaymentMethodViewSet,
    TransactionViewSet, DebtViewSet, DebtPaymentViewSet,
    NotificationViewSet, ReminderViewSet,
    BudgetViewSet, GoalViewSet, GoalProgressViewSet,
    StockMovementViewSet, UserViewSet, PasswordResetRequestView, PasswordResetConfirmView,
    EmployeeCommissionPlanViewSet, EmployeeCommissionPreviewView, EmployeeSalesReportView,
    CashMovementViewSet, CashRegisterViewSet, MonthlySummaryView, MonthlyClosureViewSet, PaymentSummaryView,
    DashboardOverviewView
)

router = DefaultRouter()
# auth
router.register(r'auth/register', RegisterViewSet, basename='register')

# catálogos/estatus
router.register(r'statuses', EntityStatusViewSet, basename='entity-status')
router.register(r'payment-methods', PaymentMethodViewSet, basename='payment-method')

# negocio
router.register(r'businesses', BusinessViewSet, basename='business')

# productos
router.register(r'categories', ProductCategoryViewSet, basename='product-category')
router.register(r'products', ProductViewSet, basename='product')
router.register(r'variant-types', ProductVariantTypeViewSet, basename='product-variant-type')
router.register(r'variants', ProductVariantViewSet, basename='product-variant')

# entidades
router.register(r'employees', EmployeeViewSet, basename='employee')
router.register(r'customers', CustomerViewSet, basename='customer')
router.register(r'suppliers', SupplierViewSet, basename='supplier')

# transacciones / deudas
router.register(r'transactions', TransactionViewSet, basename='transaction')
router.register(r'debts', DebtViewSet, basename='debt')
router.register(r'debt-payments', DebtPaymentViewSet, basename='debt-payment')

# notificaciones / recordatorios
router.register(r'notifications', NotificationViewSet, basename='notification')
router.register(r'reminders', ReminderViewSet, basename='reminder')

# presupuesto / metas
router.register(r'budgets', BudgetViewSet, basename='budget')
router.register(r'goals', GoalViewSet, basename='goal')
router.register(r'goal-progress', GoalProgressViewSet, basename='goal-progress')

# inventario
router.register(r'stock-movements', StockMovementViewSet, basename='stock-movement')

# User
router.register(r'users', UserViewSet, basename='user')

#Comission Plans
router.register(r'commission-plans', EmployeeCommissionPlanViewSet, basename='commission-plan')
router.register(r"commission-settlements", CommissionSettlementViewSet, basename="commission-settlement")

#Caja
router.register(r'cash-movements', CashMovementViewSet, basename='cash-movement')
router.register(r'cash-registers', CashRegisterViewSet, basename='cash-register')

#Cierres mensuales
router.register(r'monthly-closures', MonthlyClosureViewSet, basename='monthly-closure')

urlpatterns = [
    path(
        "reports/employee-sales/",
        EmployeeSalesReportView.as_view(),
        name="employee-sales-report",
    ),
    path(
        "reports/employee-commission/",
        EmployeeCommissionPreviewView.as_view(),
        name="employee-commission-preview",
    ),
    path(
        "auth/password/reset/",
        PasswordResetRequestView.as_view(),
        name="password-reset-request",
    ),
    path(
        "auth/password/reset/confirm/",
        PasswordResetConfirmView.as_view(),
        name="password-reset-confirm",
    ),
    path(
        "reports/monthly-summary/",
        MonthlySummaryView.as_view(),
        name="monthly-summary",
    ),
    path(
        "reports/customers-summary/",
        CustomerSummaryView.as_view(),
        name="customers-summary",
    ),
    path(
        "reports/suppliers-summary/",
        SupplierSummaryView.as_view(),
        name="suppliers-summary",
    ),
    path(
        "reports/debts-summary/",
        DebtSummaryView.as_view(),
        name="debts-summary",
    ),
    path(
        "reports/payments-summary/",
        PaymentSummaryView.as_view(),
        name="payments-summary",
    ),
    path(
        "reports/inventory-summary/",
        InventorySummaryView.as_view(),
        name="inventory-summary",
    ),
    path(
        "dashboard/overview/",
        DashboardOverviewView.as_view(),
        name="dashboard-overview",
    ),
    path(
        "health/",
        healthcheck,
        name="healthcheck",
    ),
    path(
        "",
        include(router.urls),
    ),
]