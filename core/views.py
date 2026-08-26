from decimal import Decimal
from django.core.exceptions import FieldDoesNotExist, ObjectDoesNotExist
from django.utils import timezone
from rest_framework import viewsets, mixins, status
from rest_framework.views import APIView
from rest_framework.decorators import action
from rest_framework.permissions import IsAuthenticated, AllowAny
from rest_framework.response import Response
from rest_framework.exceptions import ValidationError, PermissionDenied
from rest_framework.generics import GenericAPIView
from drf_spectacular.utils import (
    OpenApiExample,
    OpenApiParameter,
    OpenApiResponse,
    OpenApiTypes,
    PolymorphicProxySerializer,
    extend_schema,
    extend_schema_view,
)
from django_filters import rest_framework as filters
from core.services.customer_supplier_reports import build_customers_summary, build_suppliers_summary
from core.services.dashboard import build_dashboard_overview
from core.services.inventory_report import build_inventory_summary
from core.services.inventory import (
    lock_products_for_inventory,
    record_locked_stock_movement,
)
from core.services.financial_flows import (
    direct_payment_transactions,
    exclude_terminal_transactions,
    recognized_debt_payments,
)
from core.services.monthly_summary import build_monthly_summary
from core.services.payment_debt_reports import build_debts_summary, build_payments_summary
from core.services.transaction_cancellation import cancel_transaction
from .filters import (
    DebtFilter,
    DebtPaymentFilter,
    StockMovementFilter,
    TransactionFilter,
)
from .pagination import StandardResultsSetPagination
from .mixins import RequireBusinessPublicIdListMixin, SoftDeleteByStatusMixin
from django.db import (
    IntegrityError,
    transaction as db_tx,
)
from django.db.models import (
    Avg,
    Count,
    DecimalField,
    ExpressionWrapper,
    F,
    Max,
    Q,
    Sum,
)
from core.utils import calculate_employee_advance_summary, log_action
from rest_framework.throttling import ScopedRateThrottle
from .serializers import (
    BusinessMembershipSerializer,
    BusinessMembershipUpdateSerializer,
    CashMovementSerializer,
    CashRegisterSummaryResponseSerializer,
    CashRegisterCloseSerializer,
    CashRegisterOpenSerializer,
    CashRegisterSerializer,
    CustomerSummaryQuerySerializer,
    DashboardOverviewQuerySerializer,
    DashboardOverviewResponseSerializer,
    DetailErrorResponseSerializer,
    InventoryValidationErrorResponseSerializer,
    DebtSummaryResponseSerializer,
    DebtSummaryQuerySerializer,
    EmployeeAccessCreateSerializer,
    EmployeeSelectionSerializer,
    InventorySummaryQuerySerializer,
    MonthlyClosureCreateSerializer,
    MonthlyClosureReopenSerializer,
    MonthlyClosureSerializer,
    MonthlySummaryQuerySerializer,
    MonthlySummaryResponseSerializer,
    PaymentSummaryQuerySerializer,
    PaymentSummaryResponseSerializer,
    SupplierSummaryQuerySerializer,
    TransactionCancellationConflictResponseSerializer,
    PublicProductCategorySerializer,
    PublicProductSerializer,
)
from datetime import (
    date,
    datetime,
    time,
    timedelta,
)
from uuid import UUID
from django.shortcuts import get_object_or_404
from django.utils import timezone as django_timezone

from core.api.views.auth import (
    FRONTEND_RESET_URL,
    PasswordResetConfirmView,
    PasswordResetRequestView,
    REGISTER_EXAMPLE,
    RegisterViewSet,
    UserViewSet,
)
from core.api.views.health import healthcheck


# ---------------------------------------------------------------------------
# Ejemplos de Swagger
#
# Los identificadores relacionales utilizan public_id en formato UUID.
# Los UUID definidos aquí son ejemplos y deben sustituirse por registros
# existentes al ejecutar solicitudes desde Swagger.
# ---------------------------------------------------------------------------

BUSINESS_PUBLIC_ID = "a0507c11-2617-41cd-90eb-da63917a5cdd"
CATEGORY_PUBLIC_ID = "4276b1bb-82fc-4806-9a5c-de70002e8e41"
PRODUCT_PUBLIC_ID = "7aa19d16-1915-4cfa-8658-3b967e198c70"
EMPLOYEE_PUBLIC_ID = "c9fc2d2d-148d-4ab7-9b24-a94389cf0c74"
CUSTOMER_PUBLIC_ID = "eb659af9-b488-4562-85cb-c4b4130aa607"
SUPPLIER_PUBLIC_ID = "e4069f25-0208-4094-9609-d7e40db38b27"
PAYMENT_METHOD_PUBLIC_ID = "9f98c592-fc72-4649-8c39-a1540c895737"

EMPLOYEE_PERIOD_QUERY_PARAMETERS = [
    OpenApiParameter(
        name="business_public_id",
        type=OpenApiTypes.UUID,
        location=OpenApiParameter.QUERY,
        required=True,
    ),
    OpenApiParameter(
        name="employee_public_id",
        type=OpenApiTypes.UUID,
        location=OpenApiParameter.QUERY,
        required=True,
    ),
    OpenApiParameter(
        name="date_from",
        type=OpenApiTypes.DATE,
        location=OpenApiParameter.QUERY,
        required=True,
    ),
    OpenApiParameter(
        name="date_to",
        type=OpenApiTypes.DATE,
        location=OpenApiParameter.QUERY,
        required=True,
    ),
]
TRANSACTION_PUBLIC_ID = "bcd85f11-e36d-4cac-94ca-b005f48843cf"
DEBT_PUBLIC_ID = "4da69052-bb85-483b-aef8-ad3d14579a49"
GOAL_PUBLIC_ID = "85af7d8e-8dc8-4616-8609-ce92df799ad6"
STATUS_PUBLIC_ID = "61823ecf-a0ec-45e3-b909-4ee8780a8246"

BUSINESS_CREATE_EXAMPLE = OpenApiExample(
    "Crear tienda",
    value={
        "business_name": "Variedades La Esperanza",
        "description": "Venta de ropa, calzado y artículos para el hogar.",
        "currency": "NIO",
    },
    request_only=True,
)

EMPLOYEE_ACCESS_EXAMPLE = OpenApiExample(
    "Crear acceso de cajero",
    value={
        "email": "carlos.mendez@example.com",
        "password": "ClaveSegura2026!",
        "full_name": "Carlos Méndez",
        "position": "Cajero principal",
        "phone": "8888-1234",
        "role": "cashier",
    },
    request_only=True,
)

MEMBERSHIP_UPDATE_EXAMPLE = OpenApiExample(
    "Cambiar rol y mantener acceso activo",
    value={
        "role": "seller",
        "is_active": True,
    },
    request_only=True,
)

CATEGORY_CREATE_EXAMPLE = OpenApiExample(
    "Crear categoría",
    value={
        "business_public_id": BUSINESS_PUBLIC_ID,
        "name": "Calzado",
    },
    request_only=True,
)

PRODUCT_CREATE_EXAMPLE = OpenApiExample(
    "Crear producto",
    value={
        "business_public_id": BUSINESS_PUBLIC_ID,
        "category_public_id": CATEGORY_PUBLIC_ID,
        "title": "Zapato deportivo unisex",
        "description": "Zapato cómodo para uso diario.",
        "image_url": "https://example.com/images/zapato-deportivo.jpg",
        "base_price": "850.00",
        "base_cost": "560.00",
        "stock": 24,
        "is_visible": True,
    },
    request_only=True,
)

EMPLOYEE_CREATE_EXAMPLE = OpenApiExample(
    "Registrar empleado sin acceso al sistema",
    value={
        "business_public_id": BUSINESS_PUBLIC_ID,
        "full_name": "Ana Martínez",
        "phone": "7777-4321",
        "position": "Dependiente",
    },
    request_only=True,
)

CUSTOMER_CREATE_EXAMPLE = OpenApiExample(
    "Registrar cliente",
    value={
        "business_public_id": BUSINESS_PUBLIC_ID,
        "full_name": "José Ramírez",
        "phone": "8666-1122",
        "email": "jose.ramirez@example.com",
    },
    request_only=True,
)

SUPPLIER_CREATE_EXAMPLE = OpenApiExample(
    "Registrar proveedor",
    value={
        "business_public_id": BUSINESS_PUBLIC_ID,
        "name": "Distribuidora Central",
        "phone": "2255-7788",
        "email": "ventas@distribuidoracentral.example.com",
    },
    request_only=True,
)

PAYMENT_METHOD_CREATE_EXAMPLE = OpenApiExample(
    "Crear método de pago",
    value={
        "business_public_id": BUSINESS_PUBLIC_ID,
        "name": "Transferencia bancaria",
        "method_type": "transfer",
    },
    request_only=True,
)

TRANSACTION_SALE_EXAMPLE = OpenApiExample(
    "Venta pagada",
    value={
        "business_public_id": BUSINESS_PUBLIC_ID,
        "customer_public_id": CUSTOMER_PUBLIC_ID,
        "supplier_public_id": None,
        "employee_public_id": EMPLOYEE_PUBLIC_ID,
        "payment_method_public_id": PAYMENT_METHOD_PUBLIC_ID,
        "type": "sale",
        "discount_percent": "5.00",
        "concept": "Venta en mostrador",
        "payment_status": "paid",
        "invoice_number": "000145",
        "invoice_series": "A",
        "invoice_file_url": "",
        "details": [
            {
                "product_public_id": PRODUCT_PUBLIC_ID,
                "quantity": 2,
                "unit_price": "850.00",
            }
        ],
    },
    request_only=True,
)

TRANSACTION_SALE_PENDING_EXAMPLE = OpenApiExample(
    "Venta pendiente",
    value={
        "business_public_id": BUSINESS_PUBLIC_ID,
        "customer_public_id": CUSTOMER_PUBLIC_ID,
        "employee_public_id": EMPLOYEE_PUBLIC_ID,
        "type": "sale",
        "payment_status": "pending",
        "details": [{
            "product_public_id": PRODUCT_PUBLIC_ID,
            "quantity": 2,
        }],
    },
    request_only=True,
)

TRANSACTION_SALE_PARTIAL_EXAMPLE = OpenApiExample(
    "Venta con pago inicial",
    value={
        "business_public_id": BUSINESS_PUBLIC_ID,
        "customer_public_id": CUSTOMER_PUBLIC_ID,
        "employee_public_id": EMPLOYEE_PUBLIC_ID,
        "payment_method_public_id": PAYMENT_METHOD_PUBLIC_ID,
        "type": "sale",
        "payment_status": "partial",
        "initial_paid_amount": "75.00",
        "details": [{
            "product_public_id": PRODUCT_PUBLIC_ID,
            "quantity": 2,
        }],
    },
    request_only=True,
)

TRANSACTION_PURCHASE_EXAMPLE = OpenApiExample(
    "Compra de inventario",
    value={
        "business_public_id": BUSINESS_PUBLIC_ID,
        "customer_public_id": None,
        "supplier_public_id": SUPPLIER_PUBLIC_ID,
        "payment_method_public_id": PAYMENT_METHOD_PUBLIC_ID,
        "type": "purchase",
        "discount_percent": "0.00",
        "concept": "Reposición semanal de inventario",
        "payment_status": "paid",
        "invoice_number": "FAC-9087",
        "invoice_series": "PROV",
        "invoice_file_url": "",
        "details": [
            {
                "product_public_id": PRODUCT_PUBLIC_ID,
                "quantity": 12,
                "unit_price": "560.00",
            }
        ],
    },
    request_only=True,
)

TRANSACTION_PURCHASE_PENDING_EXAMPLE = OpenApiExample(
    "Compra pendiente",
    value={
        "business_public_id": BUSINESS_PUBLIC_ID,
        "supplier_public_id": SUPPLIER_PUBLIC_ID,
        "type": "purchase",
        "payment_status": "pending",
        "details": [{
            "product_public_id": PRODUCT_PUBLIC_ID,
            "quantity": 12,
        }],
    },
    request_only=True,
)

TRANSACTION_PURCHASE_PARTIAL_EXAMPLE = OpenApiExample(
    "Compra con pago inicial",
    value={
        "business_public_id": BUSINESS_PUBLIC_ID,
        "supplier_public_id": SUPPLIER_PUBLIC_ID,
        "payment_method_public_id": PAYMENT_METHOD_PUBLIC_ID,
        "type": "purchase",
        "payment_status": "partial",
        "initial_paid_amount": "100.00",
        "details": [{
            "product_public_id": PRODUCT_PUBLIC_ID,
            "quantity": 12,
        }],
    },
    request_only=True,
)

TRANSACTION_EXPENSE_EXAMPLE = OpenApiExample(
    "Gasto general",
    value={
        "business_public_id": BUSINESS_PUBLIC_ID,
        "customer_public_id": None,
        "supplier_public_id": None,
        "payment_method_public_id": PAYMENT_METHOD_PUBLIC_ID,
        "type": "expense",
        "discount_percent": "0.00",
        "concept": "Pago mensual de energía eléctrica",
        "expense_amount": "2350.00",
        "payment_status": "paid",
        "invoice_number": "EN-0726",
        "invoice_series": "SERV",
        "invoice_file_url": "",
    },
    request_only=True,
)

TRANSACTION_UPDATE_EXAMPLE = OpenApiExample(
    "Actualizar datos permitidos",
    value={
        "business_public_id": BUSINESS_PUBLIC_ID,
        "type": "sale",
        "customer_public_id": CUSTOMER_PUBLIC_ID,
        "payment_method_public_id": PAYMENT_METHOD_PUBLIC_ID,
        "discount_percent": "5.00",
        "concept": "Venta corregida por solicitud del cliente",
        "invoice_number": "000145",
        "invoice_series": "A",
        "invoice_file_url": "",
    },
    request_only=True,
)

DEBT_CREATE_EXAMPLE = OpenApiExample(
    "Registrar deuda",
    value={
        "transaction_public_id": TRANSACTION_PUBLIC_ID,
        "total_amount": "1700.00",
        "paid_amount": "0.00",
        "interest_rate": "0.00",
        "term_months": 1,
        "due_date": "2026-08-30",
    },
    request_only=True,
)

DEBT_PAYMENT_CREATE_EXAMPLE = OpenApiExample(
    "Abono a deuda",
    value={
        "debt_public_id": DEBT_PUBLIC_ID,
        "amount": "500.00",
        "payment_date": "2026-07-30",
        "payment_method_public_id": PAYMENT_METHOD_PUBLIC_ID,
        "transaction_public_id": None,
    },
    request_only=True,
)

NOTIFICATION_CREATE_EXAMPLE = OpenApiExample(
    "Crear notificación",
    value={
        "title": "Stock bajo",
        "message": "El zapato deportivo talla 38 tiene solo 3 unidades.",
        "type": "warning",
        "business_public_id": BUSINESS_PUBLIC_ID,
        "transaction_public_id": None,
        "is_read": False,
        "scheduled_at": None,
    },
    request_only=True,
)

REMINDER_CREATE_EXAMPLE = OpenApiExample(
    "Crear recordatorio",
    value={
        "title": "Contactar al proveedor",
        "description": "Confirmar la entrega del nuevo inventario.",
        "due_date": "2026-08-02",
        "is_completed": False,
        "business_public_id": BUSINESS_PUBLIC_ID,
        "transaction_public_id": None,
    },
    request_only=True,
)

BUDGET_CREATE_EXAMPLE = OpenApiExample(
    "Crear presupuesto mensual",
    value={
        "business_public_id": BUSINESS_PUBLIC_ID,
        "status_public_id": STATUS_PUBLIC_ID,
        "period_start": "2026-08-01",
        "period_end": "2026-08-31",
        "allocated_amount": "50000.00",
        "used_amount": "0.00",
    },
    request_only=True,
)

GOAL_CREATE_EXAMPLE = OpenApiExample(
    "Crear meta de ventas",
    value={
        "business_public_id": BUSINESS_PUBLIC_ID,
        "name": "Meta de ventas de agosto",
        "description": "Alcanzar cincuenta mil córdobas en ventas.",
        "target_amount": "50000.00",
        "start_date": "2026-08-01",
        "end_date": "2026-08-31",
    },
    request_only=True,
)

GOAL_PROGRESS_CREATE_EXAMPLE = OpenApiExample(
    "Registrar avance de meta",
    value={
        "goal_public_id": GOAL_PUBLIC_ID,
        "amount": "3500.00",
        "transaction_public_id": TRANSACTION_PUBLIC_ID,
        "status_public_id": STATUS_PUBLIC_ID,
        "note": "Venta mayorista registrada.",
    },
    request_only=True,
)

from .models import (
    BusinessMembership, CashRegister, MonthlyClosure, User, Business, EntityStatus,
    ProductCategory, Product,
    Employee, Customer, Supplier, PaymentMethod,
    Transaction, TransactionDetail, StockMovement,
    Debt, DebtPayment, Notification, Reminder,
    Budget, Goal, GoalProgress, EmployeeCommissionPlan, CommissionSettlement, CashMovement
)
from .serializers import (
    UserSerializer,
    BusinessSerializer, EntityStatusSerializer,
    ProductCategorySerializer, ProductSerializer,
    EmployeeSerializer, CustomerSerializer, SupplierSerializer, PaymentMethodSerializer,
    TransactionSerializer, TransactionUpdateSchemaSerializer,
    TransactionDetailSerializer, StockMovementSerializer,
    DebtSerializer, DebtPaymentSerializer, NotificationSerializer, ReminderSerializer,
    BudgetSerializer, GoalSerializer, GoalProgressSerializer, 
    CommissionSettlementCreateSerializer, CommissionSettlementSerializer, EmployeeCommissionPlanSerializer,
)
from .permissions import IsOwnerOrBusinessOwner

def validate_report_business_access(
    *,
    user,
    business,
    allowed_roles=None,
):
    if user.is_superuser:
        return

    if allowed_roles is None:
        allowed_roles = [
            BusinessMembership.ROLE_OWNER,
            BusinessMembership.ROLE_ADMIN,
        ]

    has_access = (
        BusinessMembership.objects
        .filter(
            user=user,
            business=business,
            is_active=True,
            role__in=allowed_roles,
        )
        .exists()
    )

    if not has_access:
        raise PermissionDenied(
            "No tienes permisos para consultar este reporte."
        )

def calculate_cash_register_summary(
    cash_register,
    *,
    until=None,
):
    until = until or django_timezone.now()

    base_transactions = exclude_terminal_transactions(
        Transaction.objects
        .filter(
            business=cash_register.business,
            created_at__gte=cash_register.open_time,
            created_at__lte=until,
        )
    )

    direct_transactions = direct_payment_transactions(
        base_transactions
    )

    def transaction_total(
        transaction_type,
        method_type,
    ):
        result = (
            direct_transactions
            .filter(
                type=transaction_type,
                payment_method__method_type=method_type,
            )
            .aggregate(
                total=Sum("total_value")
            )
        )

        return (
            result["total"]
            or Decimal("0.00")
        )

    cash_sales = transaction_total(
        "sale",
        PaymentMethod.TYPE_CASH,
    )

    card_sales = transaction_total(
        "sale",
        PaymentMethod.TYPE_CARD,
    )

    transfer_sales = transaction_total(
        "sale",
        PaymentMethod.TYPE_TRANSFER,
    )

    other_sales = transaction_total(
        "sale",
        PaymentMethod.TYPE_OTHER,
    )

    cash_purchases = transaction_total(
        "purchase",
        PaymentMethod.TYPE_CASH,
    )

    cash_expenses = transaction_total(
        "expense",
        PaymentMethod.TYPE_CASH,
    )

    session_debt_payments = recognized_debt_payments(
        DebtPayment.objects
        .filter(
            debt__transaction__business=(
                cash_register.business
            ),
            payment_method__method_type=(
                PaymentMethod.TYPE_CASH
            ),
            created_at__gte=(
                cash_register.open_time
            ),
            created_at__lte=until,
        )
    )

    def cash_debt_payment_total(transaction_type):
        return (
            session_debt_payments
            .filter(
                debt__transaction__type=transaction_type,
            )
            .aggregate(total=Sum("amount"))
            .get("total")
            or Decimal("0.00")
        )

    cash_debt_payments_received = (
        cash_debt_payment_total("sale")
    )
    cash_debt_payments_made = (
        cash_debt_payment_total("purchase")
    )

    movements = (
        CashMovement.objects
        .filter(
            cash_register=cash_register,
            created_at__lte=until,
        )
    )

    movement_totals = {
        movement_type: Decimal("0.00")
        for movement_type, _
        in CashMovement.MOVEMENT_TYPES
    }

    movement_rows = (
        movements
        .values("movement_type")
        .annotate(total=Sum("amount"))
    )

    for row in movement_rows:
        movement_totals[
            row["movement_type"]
        ] = row["total"] or Decimal("0.00")

    deposits = movement_totals[
        CashMovement.TYPE_DEPOSIT
    ]

    withdrawals = movement_totals[
        CashMovement.TYPE_WITHDRAWAL
    ]

    employee_advances = movement_totals[
        CashMovement.TYPE_EMPLOYEE_ADVANCE
    ]

    employee_repayments = movement_totals[
        CashMovement.TYPE_EMPLOYEE_REPAYMENT
    ]

    other_income = movement_totals[
        CashMovement.TYPE_OTHER_INCOME
    ]

    other_expense = movement_totals[
        CashMovement.TYPE_OTHER_EXPENSE
    ]

    total_income_movements = (
        deposits
        + employee_repayments
        + other_income
    )

    total_outgoing_movements = (
        withdrawals
        + employee_advances
        + other_expense
    )

    expected_closing_balance = (
        cash_register.opening_balance
        + cash_sales
        + cash_debt_payments_received
        + total_income_movements
        - cash_purchases
        - cash_expenses
        - cash_debt_payments_made
        - total_outgoing_movements
    ).quantize(
        Decimal("0.01")
    )

    return {
        "period": {
            "open_time": cash_register.open_time,
            "until": until,
        },
        "opening_balance": (
            cash_register.opening_balance
        ),
        "sales": {
            "cash": cash_sales,
            "card": card_sales,
            "transfer": transfer_sales,
            "other": other_sales,
            "total": (
                cash_sales
                + card_sales
                + transfer_sales
                + other_sales
            ),
        },
        "cash_purchases": cash_purchases,
        "cash_expenses": cash_expenses,
        "cash_debt_payments": (
            cash_debt_payments_received
            + cash_debt_payments_made
        ),
        "cash_debt_payments_received": (
            cash_debt_payments_received
        ),
        "cash_debt_payments_made": (
            cash_debt_payments_made
        ),
        "automatic_cash_inflows": (
            cash_sales
            + cash_debt_payments_received
        ),
        "automatic_cash_outflows": (
            cash_purchases
            + cash_expenses
            + cash_debt_payments_made
        ),
        "movements": {
            "deposits": deposits,
            "withdrawals": withdrawals,
            "employee_advances": (
                employee_advances
            ),
            "employee_repayments": (
                employee_repayments
            ),
            "other_income": other_income,
            "other_expense": other_expense,
        },
        "expected_closing_balance": (
            expected_closing_balance
        ),
    }

def get_month_period(
    *,
    year: int,
    month: int,
):
    period_start_date = date(
        year,
        month,
        1,
    )

    if month == 12:
        next_month_date = date(
            year + 1,
            1,
            1,
        )
    else:
        next_month_date = date(
            year,
            month + 1,
            1,
        )

    current_timezone = (
        django_timezone.get_current_timezone()
    )

    period_start = (
        django_timezone.make_aware(
            datetime.combine(
                period_start_date,
                time.min,
            ),
            current_timezone,
        )
    )

    period_end = (
        django_timezone.make_aware(
            datetime.combine(
                next_month_date,
                time.min,
            ),
            current_timezone,
        )
    )

    return {
        "start_date": period_start_date,
        "end_date": (
            next_month_date
            - timedelta(days=1)
        ),
        "start_datetime": period_start,
        "end_datetime": period_end,
    }

def decimal_or_zero(value):
    return (
        value or Decimal("0.00")
    ).quantize(
        Decimal("0.01")
    )

# -------- Base mixin para filtrar por usuario --------

class BusinessScopedViewSet(RequireBusinessPublicIdListMixin, viewsets.ModelViewSet):
    """
    ViewSet base para recursos pertenecientes a un usuario o negocio.

    Reglas:

    - Un superusuario de Django puede consultar todos los registros.
    - Un usuario normal solo consulta registros de negocios donde tiene
      una BusinessMembership activa.
    - Los modelos personales con campo `user` continúan filtrándose por
      el usuario autenticado.
    - No se permite crear ni mover registros hacia negocios sin acceso.
    - Los endpoints administrativos conservan visibles todos los estados.
    - Se mantiene compatibilidad temporal con `owner_lookup`.
    """

    permission_classes = [
        IsAuthenticated,
        IsOwnerOrBusinessOwner,
    ]

    throttle_classes = [
        ScopedRateThrottle,
    ]

    require_business_public_id_for_list = True

    business_query_param = (
        "business_public_id"
    )

    # Nueva ruta recomendada hacia Business.
    #
    # Ejemplos:
    #
    # business_lookup = "business"
    # business_lookup = "product__business"
    # business_lookup = "transaction__business"
    # business_lookup = "debt__transaction__business"
    business_lookup = None

    # Compatibilidad temporal con los ViewSets existentes.
    #
    # Ejemplos antiguos:
    #
    # business_lookup = "business"
    # business_lookup = "product__business"
    owner_lookup = None

    # Roles permitidos por operación.
    #
    # None significa:
    # cualquier membresía activa puede realizar la operación.
    #
    # Cada ViewSet puede sobrescribir estas propiedades.
    read_allowed_roles = None
    create_allowed_roles = None
    update_allowed_roles = None
    destroy_allowed_roles = None

    def get_throttles(self):
        self.throttle_scope = (
            "public_read"
            if self.action in (
                "list",
                "retrieve",
            )
            else "admin_write"
        )

        return super().get_throttles()

    @staticmethod
    def _is_platform_admin(user) -> bool:
        """
        Solo `is_superuser` representa acceso global a la plataforma.

        Un administrador de negocio no debe confundirse con un
        administrador global de Django.
        """

        return bool(
            user
            and user.is_authenticated
            and user.is_superuser
        )

    @staticmethod
    def _model_has_field(
        model_cls,
        field_name: str,
    ) -> bool:
        try:
            model_cls._meta.get_field(
                field_name
            )
            return True

        except FieldDoesNotExist:
            return False

    def _get_business_lookup(self):
        """
        Obtiene la ruta desde el modelo del ViewSet hasta Business.

        Prioridad:

        1. business_lookup explícito.
        2. owner_lookup antiguo terminando en __user.
        3. Campo directo business.

        Ejemplos:

            business_lookup = "product__business"

        o, temporalmente:

            business_lookup = "product__business"

        Ambos producirán:

            product__business
        """

        business_lookup = getattr(
            self,
            "business_lookup",
            None,
        )

        if business_lookup:
            return business_lookup

        owner_lookup = getattr(
            self,
            "owner_lookup",
            None,
        )

        if owner_lookup:
            # Solo las rutas que realmente pasan por Business pueden
            # convertirse al nuevo business_lookup. Ejemplos válidos:
            # business__user / product__business__user.
            if owner_lookup == "business__user":
                return "business"

            if "__business__user" in owner_lookup:
                return owner_lookup.removesuffix("__user")

            # Rutas personales como "user" o "goal__user" deben
            # continuar filtrándose por propietario, no por membresía.
            return None

        queryset = super().get_queryset()
        model_cls = queryset.model

        if self._model_has_field(
            model_cls,
            "business",
        ):
            return "business"

        return None

    def _user_can_access_business(
        self,
        user,
        business,
        allowed_roles=None,
    ) -> bool:
        """
        Comprueba si el usuario tiene una membresía activa en el negocio.

        Si allowed_roles es None, basta con cualquier membresía activa.
        """

        if business is None:
            return False

        if self._is_platform_admin(user):
            return True

        filters = {
            "user": user,
            "business": business,
            "is_active": True,
        }

        if allowed_roles is not None:
            filters["role__in"] = (
                allowed_roles
            )

        return (
            BusinessMembership.objects
            .filter(**filters)
            .exists()
        )

    def _validate_business_access(
        self,
        business,
        allowed_roles=None,
    ) -> None:
        """
        Valida acceso a un negocio.

        Puede limitar el acceso a determinados roles:

            self._validate_business_access(
                business,
                allowed_roles=[
                    BusinessMembership.ROLE_OWNER,
                    BusinessMembership.ROLE_ADMIN,
                ],
            )
        """

        if business is None:
            raise PermissionDenied(
                "Debes indicar un negocio válido."
            )

        has_access = (
            self._user_can_access_business(
                self.request.user,
                business,
                allowed_roles=allowed_roles,
            )
        )

        if not has_access:
            raise PermissionDenied(
                "No tienes permiso para utilizar "
                "este negocio."
            )

    def validate_destroy_access(
        self,
        instance,
    ):
        business = (
            self._get_business_from_instance(
                instance
            )
        )

        if business is None:
            return

        self._validate_business_access(
            business,
            allowed_roles=(
                self.destroy_allowed_roles
            ),
        )

    def _resolve_attribute_path(
        self,
        obj,
        path,
    ):
        """
        Convierte una ruta como:

            product__business

        en:

            obj.product.business
        """

        current_object = obj

        try:
            for attribute in path.split("__"):
                if current_object is None:
                    return None

                current_object = getattr(
                    current_object,
                    attribute,
                )

        except (
            AttributeError,
            ObjectDoesNotExist,
        ):
            return None

        return current_object

    def _get_business_from_instance(
        self,
        instance,
    ):
        """
        Obtiene Business desde una instancia ya guardada.
        """

        business_lookup = (
            self._get_business_lookup()
        )

        if not business_lookup:
            return None

        return self._resolve_attribute_path(
            instance,
            business_lookup,
        )

    def _get_business_from_serializer(
        self,
        serializer,
    ):
        """
        Obtiene el negocio relacionado usando validated_data.

        Soporta relaciones directas e indirectas hacia Business.

        En actualizaciones parciales, si la relación principal no viene,
        utiliza la instancia existente.
        """

        business_lookup = (
            self._get_business_lookup()
        )

        if not business_lookup:
            return None

        lookup_parts = (
            business_lookup.split("__")
        )

        first_field = lookup_parts[0]

        related_object = (
            serializer.validated_data.get(
                first_field
            )
        )

        if related_object is None:
            instance = getattr(
                serializer,
                "instance",
                None,
            )

            if instance is None:
                return None

            return self._resolve_attribute_path(
                instance,
                business_lookup,
            )

        if len(lookup_parts) == 1:
            return related_object

        remaining_path = "__".join(
            lookup_parts[1:]
        )

        return self._resolve_attribute_path(
            related_object,
            remaining_path,
        )

    def _validate_business_relation(
        self,
        serializer,
        allowed_roles=None,
    ) -> None:
        """
        Valida relaciones directas e indirectas hacia Business.

        Ejemplos:

        Product:
            business_lookup = "business"

        """

        user = self.request.user

        if self._is_platform_admin(user):
            return

        business_lookup = (
            self._get_business_lookup()
        )

        if not business_lookup:
            return

        business = (
            self._get_business_from_serializer(
                serializer
            )
        )

        if business is None:
            # En creación, una relación que debe conducir al negocio
            # no puede quedar sin validar.
            if serializer.instance is None:
                raise PermissionDenied(
                    "No se pudo determinar el negocio "
                    "del recurso relacionado."
                )

            return

        self._validate_business_access(
            business,
            allowed_roles=allowed_roles,
        )

    def get_queryset(self):
        queryset = super().get_queryset()

        user = self.request.user
        if not user.is_authenticated:
            return queryset.none()

        business_lookup = (
            self._get_business_lookup()
        )

        owner_lookup = getattr(
            self,
            "owner_lookup",
            None,
        )

        # -------------------------------------------------
        # 1. Limitar por membresías
        # -------------------------------------------------

        if (
            not self._is_platform_admin(user)
            and business_lookup
        ):
            membership_filters = {
                (
                    f"{business_lookup}"
                    "__memberships__user"
                ): user,
                (
                    f"{business_lookup}"
                    "__memberships__is_active"
                ): True,
            }

            if (
                self.read_allowed_roles
                is not None
            ):
                membership_filters[
                    (
                        f"{business_lookup}"
                        "__memberships__role__in"
                    )
                ] = self.read_allowed_roles

            queryset = queryset.filter(
                **membership_filters
            )

        # -------------------------------------------------
        # 2. Recursos personales
        # -------------------------------------------------

        if (
            not self._is_platform_admin(user)
            and owner_lookup
        ):
            queryset = queryset.filter(
                **{
                    owner_lookup: user,
                }
            )

        return queryset.distinct()
    
    def perform_create(self, serializer):
        model_cls = serializer.Meta.model
        user = self.request.user
        extra = {}

        # Modelos personales con campo user.
        if self._model_has_field(
            model_cls,
            "user",
        ):
            submitted_user = (
                serializer.validated_data.get(
                    "user"
                )
            )

            if (
                self._is_platform_admin(user)
                and submitted_user is not None
            ):
                extra["user"] = submitted_user
            else:
                extra["user"] = user

        # Valida negocio directo o indirecto.
        self._validate_business_relation(
            serializer,
            allowed_roles=(
                self.create_allowed_roles
            ),
        )
        self._validate_legacy_owner_relation(serializer)
        self._validate_direct_business_field(
            serializer,
            allowed_roles=self.create_allowed_roles,
        )

        # Estado inicial automático.
        if (
            self._model_has_field(
                model_cls,
                "status",
            )
            and "status"
            not in serializer.validated_data
        ):
            active_status = (
                EntityStatus.objects
                .filter(
                    name__iexact="Activo"
                )
                .first()
            )

            if active_status is None:
                raise PermissionDenied(
                    "No existe el estado inicial "
                    "'Activo'. Ejecuta el comando "
                    "seed_statuses."
                )

            extra["status"] = active_status

        obj = serializer.save(
            **extra
        )

        log_action(
            user,
            "CREATE",
            model_cls.__name__,
            obj.pk,
        )

    def perform_update(self, serializer):
        model_cls = serializer.Meta.model
        user = self.request.user

        if (
            self._model_has_field(
                model_cls,
                "user",
            )
            and not self._is_platform_admin(
                user
            )
        ):
            # Un usuario normal no puede reasignar el propietario
            # de un recurso personal.
            serializer.validated_data.pop(
                "user",
                None,
            )

        self._validate_business_relation(
            serializer,
            allowed_roles=(
                self.update_allowed_roles
            ),
        )
        self._validate_legacy_owner_relation(serializer)
        self._validate_direct_business_field(
            serializer,
            allowed_roles=self.update_allowed_roles,
        )

        obj = serializer.save()

        log_action(
            user,
            "UPDATE",
            obj.__class__.__name__,
            obj.pk,
        )

    def perform_destroy(self, instance):
        business = (
            self._get_business_from_instance(
                instance
            )
        )

        if business is not None:
            self._validate_business_access(
                business,
                allowed_roles=(
                    self.destroy_allowed_roles
                ),
            )

        super().perform_destroy(
            instance
        )

        log_action(
            self.request.user,
            "DELETE",
            instance.__class__.__name__,
            instance.pk,
        )

    def _validate_direct_business_field(
        self,
        serializer,
        allowed_roles=None,
    ) -> None:
        """
        Para recursos personales que además tienen un campo `business`,
        valida que el negocio enviado pertenezca a una membresía activa.
        """
        model_cls = serializer.Meta.model

        if not self._model_has_field(model_cls, "business"):
            return

        # Si ya existe business_lookup, la validación se hizo antes.
        if self._get_business_lookup():
            return

        business = serializer.validated_data.get("business")

        if business is None and serializer.instance is not None:
            business = getattr(serializer.instance, "business", None)

        if business is not None:
            self._validate_business_access(
                business,
                allowed_roles=allowed_roles,
            )

    def _validate_legacy_owner_relation(self, serializer) -> None:
        """Valida owner_lookup personales como `goal__user`."""
        user = self.request.user

        if self._is_platform_admin(user):
            return

        owner_lookup = getattr(self, "owner_lookup", None)

        if not owner_lookup or self._get_business_lookup():
            return

        parts = owner_lookup.split("__")

        if len(parts) < 2:
            return

        related_object = serializer.validated_data.get(parts[0])

        if related_object is None:
            return

        related_user = self._resolve_attribute_path(
            related_object,
            "__".join(parts[1:]),
        )

        if related_user is None or related_user.pk != user.pk:
            raise PermissionDenied(
                "No tienes permiso para utilizar el recurso relacionado."
            )

    def _validate_owner_relation(self, serializer) -> None:
        """Alias temporal para código antiguo."""
        self._validate_business_relation(serializer)
        self._validate_legacy_owner_relation(serializer)

# -------- ViewSets --------
@extend_schema_view(
    list=extend_schema(
        tags=["Businesses"],
        summary="Listar negocios",
    ),
    retrieve=extend_schema(
        tags=["Businesses"],
        summary="Obtener un negocio",
    ),
    create=extend_schema(
        tags=["Businesses"],
        summary="Crear un negocio",
        description=(
            "Registra un negocio y crea automáticamente la membresía "
            "owner para el usuario autenticado."
        ),
        examples=[BUSINESS_CREATE_EXAMPLE],
    ),
    update=extend_schema(
        tags=["Businesses"],
        summary="Actualizar un negocio",
    ),
    partial_update=extend_schema(
        tags=["Businesses"],
        summary="Actualizar parcialmente un negocio",
    ),
    destroy=extend_schema(
        tags=["Businesses"],
        summary="Desactivar un negocio",
    ),
)
class BusinessViewSet(
    SoftDeleteByStatusMixin,
    viewsets.ModelViewSet,
):
    queryset = (
        Business.objects
        .select_related(
            "user",
            "status",
        )
        .prefetch_related(
            "memberships",
            "memberships__user",
            "memberships__employee",
        )
        .all()
    )

    serializer_class = BusinessSerializer

    lookup_field = "public_id"
    lookup_url_kwarg = "public_id"

    pagination_class = (
        StandardResultsSetPagination
    )

    public_id_filter_fields = {
        "status_public_id": (
            "status__public_id"
        ),
    }

    search_fields = ["business_name"]

    permission_classes = [
        IsAuthenticated,
    ]

    throttle_classes = [
        ScopedRateThrottle,
    ]

    def get_throttles(self):
        self.throttle_scope = (
            "public_read"
            if self.action
            in (
                "list",
                "retrieve",
                "members",
            )
            else "admin_write"
        )

        return super().get_throttles()

    def get_queryset(self):
        queryset = super().get_queryset()
        user = self.request.user

        if not user.is_authenticated:
            return queryset.none()

        if user.is_superuser:
            return queryset

        return (
            queryset
            .filter(
                memberships__user=user,
                memberships__is_active=True,
            )
            .distinct()
        )

    def _validate_management_access(
        self,
        business,
    ):
        user = self.request.user

        if user.is_superuser:
            return

        has_permission = (
            BusinessMembership.objects
            .filter(
                user=user,
                business=business,
                is_active=True,
                role__in=[
                    BusinessMembership.ROLE_OWNER,
                    BusinessMembership.ROLE_ADMIN,
                ],
            )
            .exists()
        )

        if not has_permission:
            raise PermissionDenied(
                "Solo el propietario o un "
                "administrador puede administrar "
                "los accesos de este negocio."
            )

    @db_tx.atomic
    def perform_create(
        self,
        serializer,
    ):
        self._validate_business_creation_access()

        business = serializer.save(
            user=self.request.user,
        )

        BusinessMembership.objects.create(
            user=self.request.user,
            business=business,
            role=BusinessMembership.ROLE_OWNER,
            is_active=True,
        )

        log_action(
            self.request.user,
            "CREATE",
            business.__class__.__name__,
            business.pk,
        )

    def perform_update(
        self,
        serializer,
    ):
        business = self.get_object()

        self._validate_management_access(
            business
        )

        obj = serializer.save()

        log_action(
            self.request.user,
            "UPDATE",
            obj.__class__.__name__,
            obj.pk,
        )

    def on_soft_delete(
        self,
        business,
    ):
        self._validate_management_access(
            business
        )

    def _validate_business_creation_access(self):
        user = self.request.user

        if user.is_superuser:
            return

        if user.role not in [
            User.Roles.BUSINESS_OWNER,
            User.Roles.BUSINESS_ADMIN,
        ]:
            raise PermissionDenied(
                "Tu cuenta no tiene permiso para registrar negocios."
            )

    @extend_schema(
        tags=["Business Access"],
        summary="Crear acceso para un trabajador",
        description=(
            "Crea una cuenta de usuario, un empleado "
            "y una membresía dentro del negocio indicado. "
            "Solo puede ejecutarlo el propietario o un "
            "administrador del negocio."
        ),
        request=EmployeeAccessCreateSerializer,
        responses={
            201: BusinessMembershipSerializer,
        },
        examples=[EMPLOYEE_ACCESS_EXAMPLE],
    )
    @action(
        detail=True,
        methods=["post"],
        url_path="employees/create-access",
    )
    def create_employee_access(
        self,
        request,
        public_id=None,
    ):
        business = self.get_object()

        self._validate_management_access(
            business
        )

        serializer = (
            EmployeeAccessCreateSerializer(
                data=request.data,
                context={
                    "request": request,
                    "business": business,
                },
            )
        )

        serializer.is_valid(
            raise_exception=True
        )

        membership = serializer.save()

        response_serializer = (
            BusinessMembershipSerializer(
                membership,
                context={
                    "request": request,
                },
            )
        )

        return Response(
            response_serializer.data,
            status=status.HTTP_201_CREATED,
        )

    @extend_schema(
        tags=["Business Access"],
        summary="Listar accesos del negocio",
        description=(
            "Devuelve las cuentas que tienen acceso "
            "al negocio indicado, incluyendo al "
            "propietario, administradores y trabajadores."
        ),
        responses={
            200: BusinessMembershipSerializer(
                many=True,
            ),
        },
    )
    @action(
        detail=True,
        methods=["get"],
        url_path="members",
    )
    def members(
        self,
        request,
        public_id=None,
    ):
        business = self.get_object()

        self._validate_management_access(
            business
        )

        memberships = (
            business.memberships
            .select_related(
                "user",
                "employee",
                "business",
            )
            .order_by(
                "role",
                "created_at",
            )
        )

        serializer = (
            BusinessMembershipSerializer(
                memberships,
                many=True,
                context={
                    "request": request,
                },
            )
        )

        return Response(
            serializer.data,
            status=status.HTTP_200_OK,
        )

    @extend_schema(
        tags=["Business Access"],
        summary="Actualizar acceso de un miembro",
        description=(
            "Permite cambiar el rol o activar/desactivar "
            "la membresía de un trabajador. No permite "
            "modificar al propietario ni asignar el rol owner."
        ),
        request=BusinessMembershipUpdateSerializer,
        responses={
            200: BusinessMembershipSerializer,
        },
        examples=[MEMBERSHIP_UPDATE_EXAMPLE],
    )

    @action(
        detail=True,
        methods=["patch"],
        url_path=(
            r"members/"
            r"(?P<membership_public_id>"
            r"[0-9a-fA-F-]{36})"
        ),
    )
    def update_member(
        self,
        request,
        public_id=None,
        membership_public_id=None,
    ):
        business = self.get_object()

        self._validate_management_access(
            business
        )

        membership = get_object_or_404(
            BusinessMembership.objects
            .select_related(
                "user",
                "employee",
                "business",
            ),
            public_id=membership_public_id,
            business=business,
        )

        serializer = (
            BusinessMembershipUpdateSerializer(
                membership,
                data=request.data,
                partial=True,
                context={
                    "request": request,
                    "business": business,
                },
            )
        )

        serializer.is_valid(
            raise_exception=True
        )

        updated_membership = serializer.save()

        log_action(
            request.user,
            "UPDATE_MEMBER_ACCESS",
            updated_membership.__class__.__name__,
            updated_membership.pk,
        )

        response_serializer = (
            BusinessMembershipSerializer(
                updated_membership,
                context={
                    "request": request,
                },
            )
        )

        return Response(
            response_serializer.data,
            status=status.HTTP_200_OK,
        )

    @extend_schema(
        tags=["Business Access"],
        summary="Desactivar acceso de un miembro",
        description=(
            "Desactiva la membresía sin eliminar físicamente "
            "el usuario ni el empleado. No permite desactivar "
            "al propietario ni la propia membresía."
        ),
        responses={
            204: None,
        },
    )
    @action(
        detail=True,
        methods=["delete"],
        url_path=(
            r"members/"
            r"(?P<membership_public_id>"
            r"[0-9a-fA-F-]{36})"
        ),
    )
    def deactivate_member(
        self,
        request,
        public_id=None,
        membership_public_id=None,
    ):
        business = self.get_object()

        self._validate_management_access(
            business
        )

        membership = get_object_or_404(
            BusinessMembership.objects
            .select_related(
                "user",
                "employee",
                "business",
            ),
            public_id=membership_public_id,
            business=business,
        )

        if membership.user_id == request.user.id:
            raise PermissionDenied(
                "No puedes desactivar tu propio acceso."
            )

        if (
            membership.role
            == BusinessMembership.ROLE_OWNER
        ):
            raise PermissionDenied(
                "No se puede desactivar al propietario "
                "del negocio."
            )

        requester_membership = (
            BusinessMembership.objects
            .filter(
                user=request.user,
                business=business,
                is_active=True,
            )
            .first()
        )

        if (
            not request.user.is_superuser
            and requester_membership is not None
            and requester_membership.role
            == BusinessMembership.ROLE_ADMIN
            and membership.role
            == BusinessMembership.ROLE_ADMIN
        ):
            raise PermissionDenied(
                "Un administrador no puede desactivar "
                "a otro administrador."
            )

        if not membership.is_active:
            return Response(
                status=status.HTTP_204_NO_CONTENT,
            )

        membership.is_active = False

        membership.save(
            update_fields=[
                "is_active",
                "updated_at",
            ]
        )

        log_action(
            request.user,
            "DEACTIVATE_MEMBER_ACCESS",
            membership.__class__.__name__,
            membership.pk,
        )

        return Response(
            status=status.HTTP_204_NO_CONTENT,
        )

@extend_schema_view(
    list=extend_schema(tags=["Statuses"]),
    retrieve=extend_schema(tags=["Statuses"]),
)
class EntityStatusViewSet(viewsets.ReadOnlyModelViewSet):
    queryset = EntityStatus.objects.all()
    serializer_class = EntityStatusSerializer
    permission_classes = [IsAuthenticated]
    lookup_field = "public_id"
    lookup_url_kwarg = "public_id"
    pagination_class = StandardResultsSetPagination   # opcional (por si lista crece)


PUBLIC_CATALOG_BUSINESS_PARAMETER = OpenApiParameter(
    name="business_public_id",
    type=OpenApiTypes.UUID,
    location=OpenApiParameter.QUERY,
    required=True,
    description=(
        "Public ID del negocio cuyo catálogo público se consulta."
    ),
)


class PublicCatalogViewSet(
    viewsets.ReadOnlyModelViewSet,
):
    permission_classes = [AllowAny]
    throttle_classes = [ScopedRateThrottle]
    throttle_scope = "public_read"
    pagination_class = StandardResultsSetPagination
    lookup_field = "public_id"
    lookup_url_kwarg = "public_id"
    http_method_names = [
        "get",
        "head",
        "options",
    ]
    business_lookup = None

    def get_queryset(self):
        queryset = super().get_queryset()
        business_public_id = (
            self.request.query_params.get(
                "business_public_id"
            )
        )

        if not business_public_id:
            raise ValidationError({
                "business_public_id": (
                    "Este parámetro es obligatorio."
                )
            })

        try:
            business_public_id = UUID(
                str(business_public_id)
            )
        except (TypeError, ValueError, AttributeError):
            raise ValidationError({
                "business_public_id": (
                    "Debe ser un UUID válido."
                )
            })

        return queryset.filter(
            **{
                (
                    f"{self.business_lookup}"
                    "__public_id"
                ): business_public_id,
            }
        )


@extend_schema_view(
    list=extend_schema(
        tags=["Public Catalog"],
        parameters=[PUBLIC_CATALOG_BUSINESS_PARAMETER],
        description=(
            "Lista únicamente categorías Activas del negocio."
        ),
    ),
    retrieve=extend_schema(
        tags=["Public Catalog"],
        parameters=[PUBLIC_CATALOG_BUSINESS_PARAMETER],
        description=(
            "Obtiene una categoría Activa del negocio."
        ),
    ),
)
class PublicProductCategoryViewSet(
    PublicCatalogViewSet,
):
    queryset = (
        ProductCategory.objects
        .select_related("business")
        .filter(status__name__iexact="Activo")
    )
    serializer_class = PublicProductCategorySerializer
    business_lookup = "business"
    search_fields = ["name"]
    ordering_fields = ["name"]
    ordering = ["name"]


@extend_schema_view(
    list=extend_schema(
        tags=["Public Catalog"],
        parameters=[PUBLIC_CATALOG_BUSINESS_PARAMETER],
        description=(
            "Lista únicamente productos Activos y visibles del negocio."
        ),
    ),
    retrieve=extend_schema(
        tags=["Public Catalog"],
        parameters=[PUBLIC_CATALOG_BUSINESS_PARAMETER],
        description=(
            "Obtiene un producto Activo y visible del negocio."
        ),
    ),
)
class PublicProductViewSet(
    PublicCatalogViewSet,
):
    queryset = (
        Product.objects
        .select_related("business", "category")
        .filter(
            status__name__iexact="Activo",
            is_visible=True,
        )
    )
    serializer_class = PublicProductSerializer
    business_lookup = "business"
    public_id_filter_fields = {
        "category_public_id": (
            "category__public_id"
        ),
    }
    search_fields = ["title"]
    ordering_fields = ["title", "base_price"]
    ordering = ["title"]


@extend_schema_view(
    list=extend_schema(tags=["Product Categories"]),
    retrieve=extend_schema(tags=["Product Categories"]),
    create=extend_schema(
        tags=["Product Categories"],
        description="Crea una categoría dentro de un negocio donde el usuario tenga permisos de inventario.",
        examples=[CATEGORY_CREATE_EXAMPLE],
    ),
    update=extend_schema(tags=["Product Categories"]),
    partial_update=extend_schema(tags=["Product Categories"]),
    destroy=extend_schema(tags=["Product Categories"]),
)
class ProductCategoryViewSet(SoftDeleteByStatusMixin, BusinessScopedViewSet):
    queryset = ProductCategory.objects.all()
    serializer_class = ProductCategorySerializer

    business_lookup = "business"

    read_allowed_roles = [
        BusinessMembership.ROLE_OWNER,
        BusinessMembership.ROLE_ADMIN,
        BusinessMembership.ROLE_CASHIER,
        BusinessMembership.ROLE_SELLER,
        BusinessMembership.ROLE_INVENTORY,
        BusinessMembership.ROLE_VIEWER,
    ]

    create_allowed_roles = [
        BusinessMembership.ROLE_OWNER,
        BusinessMembership.ROLE_ADMIN,
        BusinessMembership.ROLE_INVENTORY,
    ]

    update_allowed_roles = create_allowed_roles

    destroy_allowed_roles = [
        BusinessMembership.ROLE_OWNER,
        BusinessMembership.ROLE_ADMIN,
    ]

    lookup_field = "public_id"
    lookup_url_kwarg = "public_id"
    pagination_class = StandardResultsSetPagination

    public_id_filter_fields = {
        "status_public_id": (
            "status__public_id"
        ),
    }

    search_fields = ["name"]


@extend_schema_view(
    list=extend_schema(tags=["Products"]),
    retrieve=extend_schema(tags=["Products"]),
    create=extend_schema(
        tags=["Products"],
        description="Crea un producto dentro del negocio indicado.",
        examples=[PRODUCT_CREATE_EXAMPLE],
    ),
    update=extend_schema(tags=["Products"]),
    partial_update=extend_schema(tags=["Products"]),
    destroy=extend_schema(tags=["Products"]),
)
class ProductViewSet(SoftDeleteByStatusMixin, BusinessScopedViewSet):
    queryset = Product.objects.select_related("business", "category", "status").all()
    serializer_class = ProductSerializer

    business_lookup = "business"

    read_allowed_roles = [
        BusinessMembership.ROLE_OWNER,
        BusinessMembership.ROLE_ADMIN,
        BusinessMembership.ROLE_CASHIER,
        BusinessMembership.ROLE_SELLER,
        BusinessMembership.ROLE_INVENTORY,
        BusinessMembership.ROLE_VIEWER,
    ]

    create_allowed_roles = [
        BusinessMembership.ROLE_OWNER,
        BusinessMembership.ROLE_ADMIN,
        BusinessMembership.ROLE_INVENTORY,
    ]

    update_allowed_roles = create_allowed_roles

    destroy_allowed_roles = [
        BusinessMembership.ROLE_OWNER,
        BusinessMembership.ROLE_ADMIN,
    ]

    lookup_field = "public_id"
    lookup_url_kwarg = "public_id"

    public_id_filter_fields = {
        "category_public_id":
            "category__public_id",

        "status_public_id":
            "status__public_id",
    }

    simple_filter_fields = {
        "is_visible":
            filters.BooleanFilter(
                field_name="is_visible",
            ),
    }
    search_fields = ["title"]
    ordering_fields = ["title", "created_at", "updated_at"]
    ordering = ["-created_at"]

    pagination_class = StandardResultsSetPagination


@extend_schema_view(
    list=extend_schema(
        tags=["Employees"],
        description=(
            "Owner y admin reciben el contrato administrativo completo. "
            "Cashier y seller reciben exclusivamente public_id, full_name "
            "y position para seleccionar al responsable de una venta."
        ),
        responses=PolymorphicProxySerializer(
            component_name="EmployeeRead",
            serializers=[
                EmployeeSerializer,
                EmployeeSelectionSerializer,
            ],
            resource_type_field_name=None,
            many=True,
        ),
    ),
    retrieve=extend_schema(
        tags=["Employees"],
        description=(
            "Owner y admin reciben el contrato administrativo completo. "
            "Cashier y seller reciben exclusivamente public_id, full_name "
            "y position."
        ),
        responses=PolymorphicProxySerializer(
            component_name="EmployeeRead",
            serializers=[
                EmployeeSerializer,
                EmployeeSelectionSerializer,
            ],
            resource_type_field_name=None,
        ),
    ),
    create=extend_schema(
        tags=["Employees"],
        description="Registra un empleado. Este endpoint no crea automáticamente acceso al sistema.",
        examples=[EMPLOYEE_CREATE_EXAMPLE],
    ),
    update=extend_schema(tags=["Employees"]),
    partial_update=extend_schema(tags=["Employees"]),
    destroy=extend_schema(tags=["Employees"]),
)
class EmployeeViewSet(SoftDeleteByStatusMixin, BusinessScopedViewSet):
    queryset = Employee.objects.select_related("business", "status").all()
    serializer_class = EmployeeSerializer
    lookup_field = "public_id"
    lookup_url_kwarg = "public_id"

    business_lookup = "business"

    read_allowed_roles = [
        BusinessMembership.ROLE_OWNER,
        BusinessMembership.ROLE_ADMIN,
        BusinessMembership.ROLE_CASHIER,
        BusinessMembership.ROLE_SELLER,
    ]

    create_allowed_roles = [
        BusinessMembership.ROLE_OWNER,
        BusinessMembership.ROLE_ADMIN,
    ]
    update_allowed_roles = create_allowed_roles
    destroy_allowed_roles = create_allowed_roles

    public_id_filter_fields = {
        "status_public_id": (
            "status__public_id"
        ),
    }
    
    search_fields = ["full_name", "email", "phone"]
    ordering_fields = ["full_name", "created_at", "updated_at"]
    ordering = ["-created_at"]

    pagination_class = StandardResultsSetPagination

    def _request_membership_role(self):
        user = self.request.user

        if self._is_platform_admin(user):
            return BusinessMembership.ROLE_OWNER

        business_id = None

        if self.action == "list":
            business_public_id = self.request.query_params.get(
                "business_public_id"
            )
            try:
                business_public_id = UUID(str(business_public_id))
            except (TypeError, ValueError):
                return None

            business_id = (
                Business.objects
                .filter(public_id=business_public_id)
                .values_list("pk", flat=True)
                .first()
            )

        elif self.action == "retrieve":
            employee_public_id = self.kwargs.get("public_id")
            try:
                employee_public_id = UUID(str(employee_public_id))
            except (TypeError, ValueError):
                return None

            business_id = (
                Employee.objects
                .filter(public_id=employee_public_id)
                .values_list("business_id", flat=True)
                .first()
            )

        if business_id is None:
            return None

        return (
            BusinessMembership.objects
            .filter(
                user=user,
                business_id=business_id,
                is_active=True,
            )
            .values_list("role", flat=True)
            .first()
        )

    def get_serializer_class(self):
        if self.action in {"list", "retrieve"}:
            role = self._request_membership_role()
            if role in {
                BusinessMembership.ROLE_CASHIER,
                BusinessMembership.ROLE_SELLER,
            }:
                return EmployeeSelectionSerializer

        return EmployeeSerializer

    def get_search_fields(self):
        role = self._request_membership_role()

        if role in {
            BusinessMembership.ROLE_CASHIER,
            BusinessMembership.ROLE_SELLER,
        }:
            return ["full_name", "position"]

        return self.search_fields


@extend_schema_view(
    list=extend_schema(tags=["Customers"]),
    retrieve=extend_schema(tags=["Customers"]),
    create=extend_schema(
        tags=["Customers"],
        description="Registra un cliente en el negocio indicado.",
        examples=[CUSTOMER_CREATE_EXAMPLE],
    ),
    update=extend_schema(tags=["Customers"]),
    partial_update=extend_schema(tags=["Customers"]),
    destroy=extend_schema(tags=["Customers"]),
)
class CustomerViewSet(SoftDeleteByStatusMixin, BusinessScopedViewSet):
    queryset = Customer.objects.select_related("business", "status").all()
    serializer_class = CustomerSerializer
    lookup_field = "public_id"
    lookup_url_kwarg = "public_id"

    business_lookup = "business"

    read_allowed_roles = [
        BusinessMembership.ROLE_OWNER,
        BusinessMembership.ROLE_ADMIN,
        BusinessMembership.ROLE_CASHIER,
        BusinessMembership.ROLE_SELLER,
        BusinessMembership.ROLE_VIEWER,
    ]

    create_allowed_roles = [
        BusinessMembership.ROLE_OWNER,
        BusinessMembership.ROLE_ADMIN,
        BusinessMembership.ROLE_CASHIER,
        BusinessMembership.ROLE_SELLER,
    ]

    update_allowed_roles = create_allowed_roles

    destroy_allowed_roles = [
        BusinessMembership.ROLE_OWNER,
        BusinessMembership.ROLE_ADMIN,
    ]
    
    public_id_filter_fields = {
        "status_public_id": (
            "status__public_id"
        ),
    }
    
    search_fields = ["full_name", "email", "phone"]
    ordering_fields = ["full_name", "created_at", "updated_at"]
    ordering = ["-created_at"]

    pagination_class = StandardResultsSetPagination


@extend_schema_view(
    list=extend_schema(tags=["Suppliers"]),
    retrieve=extend_schema(tags=["Suppliers"]),
    create=extend_schema(
        tags=["Suppliers"],
        description="Registra un proveedor en el negocio indicado.",
        examples=[SUPPLIER_CREATE_EXAMPLE],
    ),
    update=extend_schema(tags=["Suppliers"]),
    partial_update=extend_schema(tags=["Suppliers"]),
    destroy=extend_schema(tags=["Suppliers"]),
)
class SupplierViewSet(SoftDeleteByStatusMixin, BusinessScopedViewSet):
    queryset = Supplier.objects.select_related("business", "status").all()
    serializer_class = SupplierSerializer
    lookup_field = "public_id"
    lookup_url_kwarg = "public_id"

    business_lookup = "business"

    read_allowed_roles = [
        BusinessMembership.ROLE_OWNER,
        BusinessMembership.ROLE_ADMIN,
        BusinessMembership.ROLE_INVENTORY,
        BusinessMembership.ROLE_VIEWER,
    ]

    create_allowed_roles = [
        BusinessMembership.ROLE_OWNER,
        BusinessMembership.ROLE_ADMIN,
        BusinessMembership.ROLE_INVENTORY,
    ]

    update_allowed_roles = create_allowed_roles

    destroy_allowed_roles = [
        BusinessMembership.ROLE_OWNER,
        BusinessMembership.ROLE_ADMIN,
    ]

    public_id_filter_fields = {
        "status_public_id": (
            "status__public_id"
        ),
    }
    
    search_fields = ["name", "email", "phone"]
    ordering_fields = ["name", "created_at", "updated_at"]
    ordering = ["-created_at"]

    pagination_class = StandardResultsSetPagination


@extend_schema_view(
    list=extend_schema(tags=["Payment Methods"]),
    retrieve=extend_schema(tags=["Payment Methods"]),
    create=extend_schema(
        tags=["Payment Methods"],
        description="Crea un método de pago disponible para el negocio.",
        examples=[PAYMENT_METHOD_CREATE_EXAMPLE],
    ),
    update=extend_schema(tags=["Payment Methods"]),
    partial_update=extend_schema(tags=["Payment Methods"]),
    destroy=extend_schema(tags=["Payment Methods"]),
)
class PaymentMethodViewSet(SoftDeleteByStatusMixin, BusinessScopedViewSet):
    queryset = PaymentMethod.objects.select_related(
        "business",
        "status",
    ).all()
    serializer_class = PaymentMethodSerializer

    business_lookup = "business"

    read_allowed_roles = [
        BusinessMembership.ROLE_OWNER,
        BusinessMembership.ROLE_ADMIN,
        BusinessMembership.ROLE_CASHIER,
        BusinessMembership.ROLE_SELLER,
        BusinessMembership.ROLE_INVENTORY,
        BusinessMembership.ROLE_VIEWER,
    ]

    create_allowed_roles = [
        BusinessMembership.ROLE_OWNER,
        BusinessMembership.ROLE_ADMIN,
    ]

    update_allowed_roles = create_allowed_roles
    destroy_allowed_roles = create_allowed_roles

    lookup_field = "public_id"
    lookup_url_kwarg = "public_id"
    pagination_class = StandardResultsSetPagination

    public_id_filter_fields = {
        "status_public_id": (
            "status__public_id"
        ),
    }

    search_fields = ["name"]

    administrative_read_roles = [
        BusinessMembership.ROLE_OWNER,
        BusinessMembership.ROLE_ADMIN,
        BusinessMembership.ROLE_VIEWER,
    ]

    operational_read_roles = [
        BusinessMembership.ROLE_CASHIER,
        BusinessMembership.ROLE_SELLER,
        BusinessMembership.ROLE_INVENTORY,
    ]

    def get_queryset(self):
        queryset = super().get_queryset()
        user = self.request.user

        if user.is_superuser:
            return queryset

        administrative_businesses = (
            BusinessMembership.objects
            .filter(
                user=user,
                is_active=True,
                role__in=(
                    self.administrative_read_roles
                ),
            )
            .values("business_id")
        )

        operational_businesses = (
            BusinessMembership.objects
            .filter(
                user=user,
                is_active=True,
                role__in=(
                    self.operational_read_roles
                ),
            )
            .values("business_id")
        )

        return queryset.filter(
            Q(
                business_id__in=(
                    administrative_businesses
                ),
            )
            | Q(
                business_id__in=(
                    operational_businesses
                ),
                status__name__iexact="Activo",
            )
        ).distinct()


@extend_schema_view(
    list=extend_schema(tags=["Stock Movements"]),
    retrieve=extend_schema(tags=["Stock Movements"]),
)
class StockMovementViewSet(BusinessScopedViewSet):
    queryset = (
        StockMovement.objects
        .select_related("product", "product__business", "transaction")
        .all()
    )
    serializer_class = StockMovementSerializer

    business_lookup = "product__business"

    read_allowed_roles = [
        BusinessMembership.ROLE_OWNER,
        BusinessMembership.ROLE_ADMIN,
        BusinessMembership.ROLE_INVENTORY,
        BusinessMembership.ROLE_VIEWER,
    ]
    
    lookup_field = "public_id"
    lookup_url_kwarg = "public_id"

    http_method_names = [
        "get",
        "head",
        "options",
    ]

    filterset_class = StockMovementFilter
    search_fields = ["product__title", "transaction__public_id"]
    ordering_fields = ["created_at", "id", "product__title"]
    ordering = ["-created_at"]

    pagination_class = StandardResultsSetPagination

@extend_schema_view(
    list=extend_schema(tags=["Transactions"]),
    retrieve=extend_schema(tags=["Transactions"]),
    create=extend_schema(
        tags=["Transactions"],
        summary="Crear una transacción",
        description=(
            "Crea una venta, compra o gasto. "
            "El usuario autenticado se registra automáticamente "
            "como creador de la transacción. En las ventas debe "
            "indicarse el empleado al que pertenece la venta. "
            "Un total positivo paid requiere método de pago; pending "
            "no admite método; partial requiere método e "
            "initial_paid_amount y registra el pago inicial de forma "
            "atómica. Los gastos solo admiten paid. Un total cero solo "
            "admite paid sin método ni pago inicial distinto de cero."
        ),
        examples=[
            TRANSACTION_SALE_EXAMPLE,
            TRANSACTION_SALE_PENDING_EXAMPLE,
            TRANSACTION_SALE_PARTIAL_EXAMPLE,
            TRANSACTION_PURCHASE_EXAMPLE,
            TRANSACTION_PURCHASE_PENDING_EXAMPLE,
            TRANSACTION_PURCHASE_PARTIAL_EXAMPLE,
            TRANSACTION_EXPENSE_EXAMPLE,
        ],
    ),
    update=extend_schema(
        tags=["Transactions"],
        request=TransactionUpdateSchemaSerializer,
        summary="Actualizar una transacción",
        description=(
            "PUT exige business_public_id y type con los valores actuales. "
            "No permite cambiar el negocio, el tipo, los detalles, "
            "el estado de pago ni el monto de un gasto."
        ),
        examples=[TRANSACTION_UPDATE_EXAMPLE],
    ),
    partial_update=extend_schema(
        tags=["Transactions"],
        request=TransactionUpdateSchemaSerializer,
        summary="Actualizar parcialmente una transacción",
        description=(
            "Permite modificar únicamente los campos editables de "
            "una transacción existente. No exige business_public_id "
            "ni type."
        ),
        examples=[TRANSACTION_UPDATE_EXAMPLE],
    ),
    destroy=extend_schema(
        tags=["Transactions"],
        summary="Baja lógica + neutralizar inventario",
        description=(
            "Anula la transacción y neutraliza su inventario. "
            "Si existe una deuda con cualquier actividad financiera, "
            "la operación se rechaza con 409 sin mutaciones parciales."
        ),
        responses={
            204: None,
            400: OpenApiResponse(
                response=InventoryValidationErrorResponseSerializer,
                description=(
                    "La neutralización de inventario no puede aplicarse, "
                    "por ejemplo porque dejaría stock negativo."
                ),
            ),
            403: OpenApiResponse(
                response=DetailErrorResponseSerializer,
                description="El rol no permite anular la transacción.",
            ),
            404: OpenApiResponse(
                response=DetailErrorResponseSerializer,
                description="La transacción no es visible para el usuario.",
            ),
            409: OpenApiResponse(
                response=TransactionCancellationConflictResponseSerializer,
                description=(
                    "La transacción ya es terminal, su deuda tiene pagos "
                    "o se detectó un conflicto concurrente."
                ),
            ),
        },
    ),
)
class TransactionViewSet(SoftDeleteByStatusMixin, BusinessScopedViewSet):
    queryset = (
        Transaction.objects
        .select_related(
            "business",
            "customer",
            "supplier",
            "employee",
            "payment_method",
            "status",
            "created_by",
            "updated_by",
        )
        .prefetch_related("details", "details__product")
        .all()
    )
    serializer_class = TransactionSerializer
    business_lookup = "business"
    soft_delete_status_name = "Anulado"
    destroy_allowed_roles = [
        BusinessMembership.ROLE_OWNER,
        BusinessMembership.ROLE_ADMIN,
    ]

    read_allowed_roles = [
        BusinessMembership.ROLE_OWNER,
        BusinessMembership.ROLE_ADMIN,
        BusinessMembership.ROLE_CASHIER,
        BusinessMembership.ROLE_SELLER,
        BusinessMembership.ROLE_INVENTORY,
        BusinessMembership.ROLE_VIEWER,
    ]
    
    lookup_field = "public_id"
    lookup_url_kwarg = "public_id"
    pagination_class = StandardResultsSetPagination

    # Filtros/Búsqueda/Orden
    filterset_class = TransactionFilter
    search_fields = ["public_id", "invoice_number", "invoice_series", "concept",
                     "customer__full_name", "supplier__name", "employee__full_name"]
    ordering_fields = ["created_at", "updated_at", "total_value", "invoice_number"]
    ordering = ["-created_at"]

    # ------ helpers ------
    def _sign_for_tx(self, tx_type: str) -> int | None:
        if tx_type == "sale":     return -1
        if tx_type == "purchase": return  1
        return None

    def _get_request_membership(
        self,
        business,
    ):
        user = self.request.user

        if user.is_superuser:
            return None

        membership = (
            BusinessMembership.objects
            .select_related(
                "employee",
                "business",
                "user",
            )
            .filter(
                user=user,
                business=business,
                is_active=True,
            )
            .first()
        )

        if membership is None:
            raise PermissionDenied(
                "No tienes una membresía activa "
                "en este negocio."
            )

        return membership

    @db_tx.atomic
    def perform_create(self, serializer):
        business = serializer.validated_data.get("business")

        if business is None:
            raise PermissionDenied(
                "Debes indicar un negocio válido."
            )

        transaction_type = (
            serializer.validated_data.get("type")
        )

        roles_by_transaction_type = {
            "sale": [
                BusinessMembership.ROLE_OWNER,
                BusinessMembership.ROLE_ADMIN,
                BusinessMembership.ROLE_CASHIER,
                BusinessMembership.ROLE_SELLER,
            ],
            "purchase": [
                BusinessMembership.ROLE_OWNER,
                BusinessMembership.ROLE_ADMIN,
                BusinessMembership.ROLE_INVENTORY,
            ],
            "expense": [
                BusinessMembership.ROLE_OWNER,
                BusinessMembership.ROLE_ADMIN,
            ],
        }

        allowed_roles = roles_by_transaction_type.get(
            transaction_type
        )

        if allowed_roles is None:
            raise ValidationError({
                "type": "Tipo de transacción inválido."
            })

        self._validate_business_access(
            business,
            allowed_roles=allowed_roles,
        )

        tx = serializer.save(
            created_by=self.request.user,
        )

        sign = self._sign_for_tx(tx.type)

        if sign is not None:
            details = list(tx.details.select_related("product"))
            locked_products = lock_products_for_inventory(
                product_ids=(detail.product_id for detail in details),
                business_id=tx.business_id,
                require_active=True,
            )

            for detail in details:
                product = locked_products[detail.product_id]
                quantity = detail.quantity

                record_locked_stock_movement(
                    product=product,
                    transaction=tx,
                    transaction_detail=detail,
                    created_by=self.request.user,
                    movement_type=(
                        "sale"
                        if sign == -1
                        else "entry"
                    ),
                    quantity=sign * quantity,
                    note=(
                        f"Auto base from "
                        f"{tx.type} {tx.public_id}"
                    ),
                )

        log_action(
            self.request.user,
            "CREATE",
            tx.__class__.__name__,
            tx.pk,
        )
    
    @db_tx.atomic
    def perform_update(self, serializer):
        business = serializer.instance.business
        transaction_type = serializer.instance.type

        roles_by_transaction_type = {
            "sale": [
                BusinessMembership.ROLE_OWNER,
                BusinessMembership.ROLE_ADMIN,
                BusinessMembership.ROLE_CASHIER,
                BusinessMembership.ROLE_SELLER,
            ],
            "purchase": [
                BusinessMembership.ROLE_OWNER,
                BusinessMembership.ROLE_ADMIN,
                BusinessMembership.ROLE_INVENTORY,
            ],
            "expense": [
                BusinessMembership.ROLE_OWNER,
                BusinessMembership.ROLE_ADMIN,
            ],
        }

        allowed_roles = roles_by_transaction_type.get(
            transaction_type
        )

        if allowed_roles is None:
            raise ValidationError({
                "type": "Tipo de transacción inválido."
            })

        self._validate_business_access(
            business,
            allowed_roles=allowed_roles,
        )

        tx = serializer.save(
            updated_by=self.request.user,
        )

        log_action(
            self.request.user,
            "UPDATE",
            tx.__class__.__name__,
            tx.pk,
        )

    @db_tx.atomic
    def destroy(self, request, *args, **kwargs):
        instance = self.get_object()
        self.validate_destroy_access(instance)

        terminal_status = self._get_soft_delete_status()
        if terminal_status is None:
            return Response(
                {"detail": "No se encontró un estado válido para la anulación."},
                status=status.HTTP_409_CONFLICT,
            )

        transaction = cancel_transaction(
            transaction_id=instance.pk,
            business_id=instance.business_id,
            terminal_status=terminal_status,
            actor=request.user,
        )

        log_action(
            request.user,
            "CANCEL",
            transaction.__class__.__name__,
            transaction.pk,
        )
        return Response(status=status.HTTP_204_NO_CONTENT)
    
@extend_schema_view(
    list=extend_schema(tags=["Debts"], summary="Listar deudas"),
    retrieve=extend_schema(tags=["Debts"], summary="Consultar una deuda"),
)
class DebtViewSet(BusinessScopedViewSet):
    queryset = (
        Debt.objects
        .select_related(
            "transaction",
            "transaction__business",
            "transaction__customer",
            "transaction__supplier",
            "transaction__employee",
            "transaction__status",
        )
        .annotate(
            outstanding_amount=ExpressionWrapper(
                F("total_amount")
                - F("paid_amount"),
                output_field=DecimalField(
                    max_digits=12,
                    decimal_places=2,
                ),
            ),
        )
        .order_by("-created_at")
    )
    
    serializer_class = DebtSerializer
    business_lookup = "transaction__business"
    filterset_class = DebtFilter

    read_allowed_roles = [
        BusinessMembership.ROLE_OWNER,
        BusinessMembership.ROLE_ADMIN,
        BusinessMembership.ROLE_CASHIER,
        BusinessMembership.ROLE_SELLER,
        BusinessMembership.ROLE_VIEWER,
    ]

    lookup_field = "public_id"
    lookup_url_kwarg = "public_id"
    pagination_class = StandardResultsSetPagination

    http_method_names = [
        "get",
        "head",
        "options",
    ]


@extend_schema_view(
    list=extend_schema(
        tags=["Debt Payments"],
        summary="Listar pagos de deudas",
    ),
    retrieve=extend_schema(
        tags=["Debt Payments"],
        summary="Consultar un pago de deuda",
    ),
    create=extend_schema(
        tags=["Debt Payments"],
        summary="Registrar un abono",
        description=(
            "Registra un abono y actualiza "
            "automáticamente el monto pagado "
            "y el estado de la deuda."
        ),
        examples=[
            DEBT_PAYMENT_CREATE_EXAMPLE,
        ],
    ),
)
class DebtPaymentViewSet(BusinessScopedViewSet):
    queryset = DebtPayment.objects.select_related(
        "debt",
        "debt__transaction",
        "debt__transaction__business",
        "debt__transaction__customer",
        "debt__transaction__supplier",
        "payment_method",
        "transaction",
        "created_by",
    ).all()
    serializer_class = DebtPaymentSerializer

    business_lookup = "debt__transaction__business"
    filterset_class = DebtPaymentFilter

    read_allowed_roles = [
        BusinessMembership.ROLE_OWNER,
        BusinessMembership.ROLE_ADMIN,
        BusinessMembership.ROLE_CASHIER,
        BusinessMembership.ROLE_SELLER,
        BusinessMembership.ROLE_VIEWER,
    ]
    create_allowed_roles = [
        BusinessMembership.ROLE_OWNER,
        BusinessMembership.ROLE_ADMIN,
        BusinessMembership.ROLE_CASHIER,
    ]

    lookup_field = "public_id"
    lookup_url_kwarg = "public_id"
    pagination_class = StandardResultsSetPagination

    http_method_names = [
        "get",
        "post",
        "head",
        "options",
    ]

@extend_schema_view(
    list=extend_schema(tags=["Notifications"], summary="Listar notificaciones"),
    retrieve=extend_schema(tags=["Notifications"], summary="Consultar una notificación"),
    create=extend_schema(
        tags=["Notifications"],
        description="Crea una notificación personal asociada al negocio.",
        examples=[NOTIFICATION_CREATE_EXAMPLE],
    ),
    update=extend_schema(tags=["Notifications"]),
    partial_update=extend_schema(tags=["Notifications"]),
    destroy=extend_schema(tags=["Notifications"]),
)
class NotificationViewSet(SoftDeleteByStatusMixin, BusinessScopedViewSet):
    queryset = Notification.objects.select_related("user", "business", "transaction").all()
    serializer_class = NotificationSerializer

    business_lookup = "business"
    owner_lookup = "user"

    lookup_field = "public_id"
    lookup_url_kwarg = "public_id"
    pagination_class = StandardResultsSetPagination

    public_id_filter_fields = {
        "status_public_id": (
            "status__public_id"
        ),
    }

    search_fields = ["title"]


@extend_schema_view(
    list=extend_schema(tags=["Reminders"]),
    retrieve=extend_schema(tags=["Reminders"]),
    create=extend_schema(
        tags=["Reminders"],
        description="Crea un recordatorio personal asociado al negocio.",
        examples=[REMINDER_CREATE_EXAMPLE],
    ),
    update=extend_schema(tags=["Reminders"]),
    partial_update=extend_schema(tags=["Reminders"]),
    destroy=extend_schema(tags=["Reminders"]),
)
class ReminderViewSet(SoftDeleteByStatusMixin, BusinessScopedViewSet):
    queryset = Reminder.objects.select_related("user", "business", "transaction").all()
    serializer_class = ReminderSerializer

    business_lookup = "business"
    owner_lookup = "user"

    lookup_field = "public_id"
    lookup_url_kwarg = "public_id"
    pagination_class = StandardResultsSetPagination

    public_id_filter_fields = {
        "status_public_id": (
            "status__public_id"
        ),
    }

    search_fields = ["title"]


@extend_schema_view(
    list=extend_schema(tags=["Budgets"]),
    retrieve=extend_schema(tags=["Budgets"]),
    create=extend_schema(
        tags=["Budgets"],
        description="Crea un presupuesto personal para un período del negocio.",
        examples=[BUDGET_CREATE_EXAMPLE],
    ),
    update=extend_schema(tags=["Budgets"]),
    partial_update=extend_schema(tags=["Budgets"]),
    destroy=extend_schema(tags=["Budgets"]),
)
class BudgetViewSet(SoftDeleteByStatusMixin, BusinessScopedViewSet):
    queryset = Budget.objects.select_related("user", "business").all()
    serializer_class = BudgetSerializer

    business_lookup = "business"
    owner_lookup = "user"

    lookup_field = "public_id"
    lookup_url_kwarg = "public_id"
    pagination_class = StandardResultsSetPagination

    public_id_filter_fields = {
        "status_public_id": (
            "status__public_id"
        ),
    }


@extend_schema_view(
    list=extend_schema(tags=["Goals"]),
    retrieve=extend_schema(tags=["Goals"]),
    create=extend_schema(
        tags=["Goals"],
        description="Crea una meta personal asociada al negocio.",
        examples=[GOAL_CREATE_EXAMPLE],
    ),
    update=extend_schema(tags=["Goals"]),
    partial_update=extend_schema(tags=["Goals"]),
    destroy=extend_schema(tags=["Goals"]),
)
class GoalViewSet(SoftDeleteByStatusMixin, BusinessScopedViewSet):
    queryset = Goal.objects.select_related("user", "business").all()
    serializer_class = GoalSerializer

    business_lookup = "business"
    owner_lookup = "user"

    lookup_field = "public_id"
    lookup_url_kwarg = "public_id"
    pagination_class = StandardResultsSetPagination

    public_id_filter_fields = {
        "status_public_id": (
            "status__public_id"
        ),
    }

    search_fields = ["name"]

@extend_schema_view(
    list=extend_schema(tags=["Goal Progress"]),
    retrieve=extend_schema(tags=["Goal Progress"]),
    create=extend_schema(
        tags=["Goal Progress"],
        description="Registra un avance y actualiza automáticamente el progreso de la meta.",
        examples=[GOAL_PROGRESS_CREATE_EXAMPLE],
    ),
)
class GoalProgressViewSet(BusinessScopedViewSet):
    queryset = GoalProgress.objects.select_related("goal", "goal__business").all()
    serializer_class = GoalProgressSerializer

    business_lookup = "goal__business"
    owner_lookup = "goal__user"

    lookup_field = "public_id"
    lookup_url_kwarg = "public_id"

    http_method_names = [
        "get",
        "post",
        "head",
        "options",
    ]

    pagination_class = StandardResultsSetPagination

    public_id_filter_fields = {
        "status_public_id": (
            "status__public_id"
        ),
    }

@extend_schema_view(
    list=extend_schema(
        tags=["Commissions"],
        summary="Listar planes de comisión",
    ),
    retrieve=extend_schema(
        tags=["Commissions"],
        summary="Consultar plan de comisión",
    ),
    create=extend_schema(
        tags=["Commissions"],
        summary="Crear plan de comisión",
    ),
    update=extend_schema(
        tags=["Commissions"],
        summary="Actualizar plan de comisión",
    ),
    partial_update=extend_schema(
        tags=["Commissions"],
        summary="Actualizar parcialmente un plan",
    ),
    destroy=extend_schema(
        tags=["Commissions"],
        summary="Eliminar plan de comisión",
    ),
)
class EmployeeCommissionPlanViewSet(
    RequireBusinessPublicIdListMixin,
    viewsets.ModelViewSet
):
    queryset = (
        EmployeeCommissionPlan.objects
        .select_related(
            "employee",
            "employee__business",
        )
        .order_by(
            "-valid_from",
            "-created_at",
        )
    )

    serializer_class = (
        EmployeeCommissionPlanSerializer
    )

    permission_classes = [
        IsAuthenticated,
    ]

    lookup_field = "public_id"
    lookup_url_kwarg = "public_id"

    pagination_class = (
        StandardResultsSetPagination
    )

    public_id_filter_fields = {
        "employee_public_id": (
            "employee__public_id"
        ),
    }

    simple_filter_fields = {
        "is_active": filters.BooleanFilter(
            field_name="is_active",
        ),
    }

    ordering_fields = [
        "valid_from",
        "valid_until",
        "percentage",
        "created_at",
    ]

    ordering = [
        "-valid_from",
    ]

    business_lookup = (
        "employee__business"
    )

    list_allowed_roles = [
        BusinessMembership.ROLE_OWNER,
        BusinessMembership.ROLE_ADMIN,
    ]

    def get_queryset(self):
        queryset = super().get_queryset()
        user = self.request.user

        if user.is_superuser:
            return queryset

        return (
            queryset
            .filter(
                employee__business__memberships__user=user,
                employee__business__memberships__is_active=True,
            )
            .distinct()
        )

    def _validate_management_access(
        self,
        employee,
    ):
        validate_report_business_access(
            user=self.request.user,
            business=employee.business,
        )

    @db_tx.atomic
    def perform_create(
        self,
        serializer,
    ):
        employee = (
            serializer.validated_data[
                "employee"
            ]
        )

        self._validate_management_access(
            employee
        )

        plan = serializer.save()

        log_action(
            self.request.user,
            "CREATE",
            plan.__class__.__name__,
            plan.pk,
        )

    @db_tx.atomic
    def perform_update(
        self,
        serializer,
    ):
        employee = (
            serializer.validated_data.get(
                "employee",
                serializer.instance.employee,
            )
        )

        self._validate_management_access(
            employee
        )

        plan = serializer.save()

        log_action(
            self.request.user,
            "UPDATE",
            plan.__class__.__name__,
            plan.pk,
        )

    @db_tx.atomic
    def perform_destroy(
        self,
        instance,
    ):
        self._validate_management_access(
            instance.employee
        )

        instance.delete()

        log_action(
            self.request.user,
            "DELETE",
            instance.__class__.__name__,
            instance.pk,
        )

class EmployeeSalesReportView(
    GenericAPIView
):
    permission_classes = [
        IsAuthenticated,
    ]

    @extend_schema(
        tags=["Reports"],
        summary="Reporte de ventas por empleado",
        description=(
            "Calcula las ventas correspondientes "
            "a un empleado dentro de un período."
        ),
        parameters=EMPLOYEE_PERIOD_QUERY_PARAMETERS,
        responses=OpenApiTypes.OBJECT,
    )
    def get(
        self,
        request,
    ):
        business_public_id = (
            request.query_params.get(
                "business_public_id"
            )
        )

        employee_public_id = (
            request.query_params.get(
                "employee_public_id"
            )
        )

        date_from_value = (
            request.query_params.get(
                "date_from"
            )
        )

        date_to_value = (
            request.query_params.get(
                "date_to"
            )
        )

        errors = {}

        if not business_public_id:
            errors["business_public_id"] = (
                "Este parámetro es requerido."
            )

        if not employee_public_id:
            errors["employee_public_id"] = (
                "Este parámetro es requerido."
            )

        if not date_from_value:
            errors["date_from"] = (
                "Este parámetro es requerido."
            )

        if not date_to_value:
            errors["date_to"] = (
                "Este parámetro es requerido."
            )

        if errors:
            raise ValidationError(errors)

        try:
            date_from = (
                timezone.datetime
                .fromisoformat(
                    date_from_value
                )
                .date()
            )

            date_to = (
                timezone.datetime
                .fromisoformat(
                    date_to_value
                )
                .date()
            )
        except ValueError:
            raise ValidationError({
                "dates": (
                    "Las fechas deben usar "
                    "el formato YYYY-MM-DD."
                )
            })

        if date_to < date_from:
            raise ValidationError({
                "date_to": (
                    "La fecha final no puede ser "
                    "anterior a la fecha inicial."
                )
            })

        business = get_object_or_404(
            Business,
            public_id=business_public_id,
        )

        validate_report_business_access(
            user=request.user,
            business=business,
        )

        employee = get_object_or_404(
            Employee.objects.select_related(
                "business"
            ),
            public_id=employee_public_id,
            business=business,
        )

        sales = (
            Transaction.objects
            .select_related(
                "customer",
                "employee",
                "business",
            )
            .filter(
                business=business,
                employee=employee,
                type="sale",
                created_at__date__gte=date_from,
                created_at__date__lte=date_to,
            )
            .order_by("-created_at")
        )
        sales = exclude_terminal_transactions(sales)

        summary = sales.aggregate(
            sales_count=Count("id"),
            sales_total=Sum("total_value"),
            average_sale=Avg("total_value"),
        )

        sales_total = (
            summary["sales_total"]
            or Decimal("0.00")
        )

        average_sale = (
            summary["average_sale"]
            or Decimal("0.00")
        )

        transactions = [
            {
                "public_id": str(
                    transaction.public_id
                ),
                "created_at": (
                    transaction.created_at
                ),
                "customer_name": (
                    transaction.customer.full_name
                    if transaction.customer
                    else None
                ),
                "invoice_series": (
                    transaction.invoice_series
                ),
                "invoice_number": (
                    transaction.invoice_number
                ),
                "total_value": str(
                    transaction.total_value
                ),
            }
            for transaction in sales
        ]

        return Response({
            "business": {
                "public_id": str(
                    business.public_id
                ),
                "name": (
                    business.business_name
                ),
                "currency": business.currency,
            },
            "employee": {
                "public_id": str(
                    employee.public_id
                ),
                "full_name": (
                    employee.full_name
                ),
                "position": employee.position,
            },
            "period": {
                "date_from": date_from,
                "date_to": date_to,
            },
            "summary": {
                "sales_count": (
                    summary["sales_count"]
                ),
                "sales_total": str(
                    sales_total.quantize(
                        Decimal("0.01")
                    )
                ),
                "average_sale": str(
                    average_sale.quantize(
                        Decimal("0.01")
                    )
                ),
            },
            "transactions": transactions,
        })

class EmployeeCommissionPreviewView(
    GenericAPIView
):
    permission_classes = [
        IsAuthenticated,
    ]

    @extend_schema(
        tags=["Commissions"],
        summary="Calcular comisión de un empleado",
        parameters=EMPLOYEE_PERIOD_QUERY_PARAMETERS,
        responses=OpenApiTypes.OBJECT,
    )
    def get(
        self,
        request,
    ):
        business_public_id = (
            request.query_params.get(
                "business_public_id"
            )
        )

        employee_public_id = (
            request.query_params.get(
                "employee_public_id"
            )
        )

        date_from_value = (
            request.query_params.get(
                "date_from"
            )
        )

        date_to_value = (
            request.query_params.get(
                "date_to"
            )
        )

        if not all([
            business_public_id,
            employee_public_id,
            date_from_value,
            date_to_value,
        ]):
            raise ValidationError({
                "detail": (
                    "Debes indicar negocio, empleado, "
                    "fecha inicial y fecha final."
                )
            })

        try:
            date_from = (
                timezone.datetime
                .fromisoformat(
                    date_from_value
                )
                .date()
            )

            date_to = (
                timezone.datetime
                .fromisoformat(
                    date_to_value
                )
                .date()
            )
        except ValueError:
            raise ValidationError({
                "dates": (
                    "Las fechas deben usar "
                    "el formato YYYY-MM-DD."
                )
            })

        business = get_object_or_404(
            Business,
            public_id=business_public_id,
        )

        validate_report_business_access(
            user=request.user,
            business=business,
        )

        employee = get_object_or_404(
            Employee,
            public_id=employee_public_id,
            business=business,
        )

        commission_plan = (
            EmployeeCommissionPlan.objects
            .filter(
                employee=employee,
                is_active=True,
                valid_from__lte=date_to,
            )
            .filter(
                Q(valid_until__isnull=True)
                | Q(
                    valid_until__gte=date_from
                )
            )
            .order_by("-valid_from")
            .first()
        )

        if commission_plan is None:
            raise ValidationError({
                "commission_plan": (
                    "El empleado no tiene un plan "
                    "de comisión vigente para "
                    "ese período."
                )
            })

        sales = (
            Transaction.objects
            .filter(
                business=business,
                employee=employee,
                type="sale",
                created_at__date__range=(
                    date_from,
                    date_to,
                ),
            )
        )
        sales = exclude_terminal_transactions(sales)

        summary = sales.aggregate(
            sales_count=Count("id"),
            sales_total=Sum("total_value"),
        )

        sales_total = (
            summary["sales_total"]
            or Decimal("0.00")
        )

        percentage = (
            commission_plan.percentage
        )

        commission_total = (
            sales_total
            * percentage
            / Decimal("100.00")
        ).quantize(
            Decimal("0.01")
        )

        advance_summary = (
            calculate_employee_advance_summary(
                employee=employee,
                period_start=date_from,
                period_end=date_to,
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

        return Response({
            "business": {
                "public_id": str(
                    business.public_id
                ),
                "name": business.business_name,
                "currency": business.currency,
            },
            "employee": {
                "public_id": str(
                    employee.public_id
                ),
                "full_name": employee.full_name,
            },
            "period": {
                "date_from": date_from,
                "date_to": date_to,
            },
            "sales_count": summary["sales_count"],
            "sales_total": str(
                sales_total.quantize(
                    Decimal("0.01")
                )
            ),
            "commission_percentage": str(
                percentage.quantize(
                    Decimal("0.01")
                )
            ),
            "commission_total": str(
                commission_total
            ),
            "employee_advances": str(
                employee_advances
            ),
            "employee_repayments": str(
                employee_repayments
            ),
            "advance_balance": str(
                advance_balance
            ),
            "net_commission_payable": str(
                net_commission_payable
            ),
            "remaining_advance_balance": str(
                remaining_advance_balance
            ),
            "commission_plan_public_id": str(
                commission_plan.public_id
            ),
        })
@extend_schema_view(
    list=extend_schema(
        tags=["Commissions"],
        summary=(
            "Listar liquidaciones "
            "de comisiones"
        ),
    ),
    retrieve=extend_schema(
        tags=["Commissions"],
        summary=(
            "Consultar liquidación "
            "de comisión"
        ),
    ),
    create=extend_schema(
        tags=["Commissions"],
        summary=(
            "Crear liquidación "
            "de comisión"
        ),
        description=(
            "Calcula y congela la comisión "
            "de un empleado para un período. "
            "Los totales son calculados por "
            "el backend."
        ),
        request=(
            CommissionSettlementCreateSerializer
        ),
        responses={
            201: CommissionSettlementSerializer,
        },
    ),
)
class CommissionSettlementViewSet(
    RequireBusinessPublicIdListMixin,
    viewsets.GenericViewSet,
    mixins.ListModelMixin,
    mixins.RetrieveModelMixin,
    mixins.CreateModelMixin,
):
    queryset = (
        CommissionSettlement.objects
        .select_related(
            "employee",
            "employee__business",
            "created_by",
        )
        .order_by(
            "-period_end",
            "-created_at",
        )
    )

    permission_classes = [
        IsAuthenticated,
    ]

    lookup_field = "public_id"
    lookup_url_kwarg = "public_id"

    pagination_class = (
        StandardResultsSetPagination
    )

    public_id_filter_fields = {
        "employee_public_id": (
            "employee__public_id"
        ),
    }

    simple_filter_fields = {
        "status": filters.CharFilter(
            field_name="status",
        ),
        "period_start": filters.DateFilter(
            field_name="period_start",
        ),
        "period_end": filters.DateFilter(
            field_name="period_end",
        ),
    }

    ordering_fields = [
        "period_start",
        "period_end",
        "sales_total",
        "commission_total",
        "created_at",
        "paid_at",
    ]

    ordering = [
        "-period_end",
        "-created_at",
    ]

    business_lookup = (
        "employee__business"
    )

    list_allowed_roles = [
        BusinessMembership.ROLE_OWNER,
        BusinessMembership.ROLE_ADMIN,
    ]

    def get_serializer_class(self):
        if self.action == "create":
            return (
                CommissionSettlementCreateSerializer
            )

        return (
            CommissionSettlementSerializer
        )

    def get_queryset(self):
        queryset = super().get_queryset()
        user = self.request.user

        if user.is_superuser:
            return queryset

        return (
            queryset
            .filter(
                employee__business__memberships__user=user,
                employee__business__memberships__is_active=True,
                employee__business__memberships__role__in=[
                    BusinessMembership
                    .ROLE_OWNER,
                    BusinessMembership
                    .ROLE_ADMIN,
                ],
            )
            .distinct()
        )

    def _validate_management_access(
        self,
        business,
    ):
        user = self.request.user

        if user.is_superuser:
            return

        has_access = (
            BusinessMembership.objects
            .filter(
                user=user,
                business=business,
                is_active=True,
                role__in=[
                    BusinessMembership
                    .ROLE_OWNER,
                    BusinessMembership
                    .ROLE_ADMIN,
                ],
            )
            .exists()
        )

        if not has_access:
            raise PermissionDenied(
                "Solo el propietario o un "
                "administrador puede gestionar "
                "liquidaciones de comisiones."
            )

    @db_tx.atomic
    def create(
        self,
        request,
        *args,
        **kwargs,
    ):
        serializer = (
            self.get_serializer(
                data=request.data,
            )
        )

        serializer.is_valid(
            raise_exception=True
        )

        employee = (
            serializer.validated_data[
                "employee"
            ]
        )

        self._validate_management_access(
            employee.business
        )

        settlement = serializer.save()

        log_action(
            request.user,
            "CREATE",
            settlement.__class__.__name__,
            settlement.pk,
        )

        response_serializer = (
            CommissionSettlementSerializer(
                settlement,
                context={
                    "request": request,
                },
            )
        )

        return Response(
            response_serializer.data,
            status=status.HTTP_201_CREATED,
        )

    @extend_schema(
        tags=["Commissions"],
        summary=(
            "Marcar liquidación como pagada"
        ),
        request=None,
        responses={
            200: CommissionSettlementSerializer,
        },
    )
    @action(
        detail=True,
        methods=["post"],
        url_path="mark-paid",
    )
    @db_tx.atomic
    def mark_paid(
        self,
        request,
        public_id=None,
    ):
        settlement = self.get_object()

        self._validate_management_access(
            settlement.employee.business
        )

        if (
            settlement.status
            == CommissionSettlement.STATUS_PAID
        ):
            raise ValidationError({
                "status": (
                    "La liquidación ya fue "
                    "marcada como pagada."
                )
            })

        if (
            settlement.status
            == CommissionSettlement
            .STATUS_CANCELLED
        ):
            raise ValidationError({
                "status": (
                    "Una liquidación cancelada "
                    "no puede marcarse como pagada."
                )
            })

        settlement.status = (
            CommissionSettlement.STATUS_PAID
        )

        settlement.paid_at = timezone.now()

        settlement.save(
            update_fields=[
                "status",
                "paid_at",
                "updated_at",
            ]
        )

        log_action(
            request.user,
            "MARK_COMMISSION_PAID",
            settlement.__class__.__name__,
            settlement.pk,
        )

        serializer = (
            CommissionSettlementSerializer(
                settlement,
                context={
                    "request": request,
                },
            )
        )

        return Response(
            serializer.data,
            status=status.HTTP_200_OK,
        )

@extend_schema_view(
    list=extend_schema(
        tags=["Cash Registers"],
        summary="Listar cajas",
    ),
    retrieve=extend_schema(
        tags=["Cash Registers"],
        summary="Consultar una caja",
    ),
    create=extend_schema(
        tags=["Cash Registers"],
        summary="Abrir caja",
        request=CashRegisterOpenSerializer,
        responses={
            201: CashRegisterSerializer,
        },
    ),
)
class CashRegisterViewSet(
    RequireBusinessPublicIdListMixin,
    viewsets.GenericViewSet,
    mixins.ListModelMixin,
    mixins.RetrieveModelMixin,
    mixins.CreateModelMixin,
):
    queryset = (
        CashRegister.objects
        .select_related(
            "business",
            "employee",
            "opened_by",
            "closed_by",
        )
        .order_by("-open_time")
    )

    permission_classes = [
        IsAuthenticated,
    ]

    lookup_field = "public_id"
    lookup_url_kwarg = "public_id"

    pagination_class = (
        StandardResultsSetPagination
    )

    public_id_filter_fields = {
        "employee_public_id": (
            "employee__public_id"
        ),
    }

    simple_filter_fields = {
        "status": filters.CharFilter(
            field_name="status",
        ),
    }

    ordering_fields = [
        "open_time",
        "close_time",
        "opening_balance",
        "closing_balance",
        "difference",
    ]

    ordering = [
        "-open_time",
    ]

    business_lookup = "business"

    list_allowed_roles = [
        BusinessMembership.ROLE_OWNER,
        BusinessMembership.ROLE_ADMIN,
        BusinessMembership.ROLE_CASHIER,
    ]

    read_roles = [
        BusinessMembership.ROLE_OWNER,
        BusinessMembership.ROLE_ADMIN,
        BusinessMembership.ROLE_CASHIER,
    ]

    management_roles = [
        BusinessMembership.ROLE_OWNER,
        BusinessMembership.ROLE_ADMIN,
        BusinessMembership.ROLE_CASHIER,
    ]

    def get_serializer_class(self):
        if self.action == "create":
            return CashRegisterOpenSerializer

        if self.action == "close":
            return CashRegisterCloseSerializer

        return CashRegisterSerializer

    def get_queryset(self):
        queryset = super().get_queryset()
        user = self.request.user

        if not user.is_superuser:
            queryset = (
                queryset
                .filter(
                    business__memberships__user=user,
                    business__memberships__is_active=True,
                    business__memberships__role__in=(
                        self.read_roles
                    ),
                )
                .distinct()
            )

        return queryset

    def _validate_cash_access(
        self,
        business,
        *,
        roles=None,
    ):
        user = self.request.user

        if user.is_superuser:
            return

        allowed_roles = (
            roles
            or self.management_roles
        )

        has_access = (
            BusinessMembership.objects
            .filter(
                user=user,
                business=business,
                is_active=True,
                role__in=allowed_roles,
            )
            .exists()
        )

        if not has_access:
            raise PermissionDenied(
                "No tienes permiso para gestionar "
                "la caja de este negocio."
            )

    @db_tx.atomic
    def create(
        self,
        request,
        *args,
        **kwargs,
    ):
        serializer = self.get_serializer(
            data=request.data
        )

        serializer.is_valid(
            raise_exception=True
        )

        business = serializer.validated_data[
            "business"
        ]

        employee = serializer.validated_data[
            "employee"
        ]

        self._validate_cash_access(
            business
        )

        cash_register = serializer.save(
            opened_by=request.user,
            open_time=django_timezone.now(),
            status=CashRegister.STATUS_OPEN,
        )

        log_action(
            request.user,
            "OPEN_CASH_REGISTER",
            cash_register.__class__.__name__,
            cash_register.pk,
        )

        response_serializer = (
            CashRegisterSerializer(
                cash_register,
                context={
                    "request": request,
                },
            )
        )

        return Response(
            response_serializer.data,
            status=status.HTTP_201_CREATED,
        )

    @extend_schema(
        tags=["Cash Registers"],
        summary="Vista previa del cierre",
        responses={
            200: OpenApiResponse(
                response=CashRegisterSummaryResponseSerializer,
                description=(
                    "Resumen calculado de la caja."
                )
            ),
        },
    )
    @action(
        detail=True,
        methods=["get"],
        url_path="closing-preview",
    )
    def closing_preview(
        self,
        request,
        public_id=None,
    ):
        cash_register = self.get_object()

        self._validate_cash_access(
            cash_register.business
        )

        if (
            cash_register.status
            != CashRegister.STATUS_OPEN
        ):
            raise ValidationError({
                "status": (
                    "Solo se puede calcular la "
                    "vista previa de una caja abierta."
                )
            })

        summary = calculate_cash_register_summary(
            cash_register
        )

        return Response(
            summary,
            status=status.HTTP_200_OK,
        )

    @extend_schema(
        tags=["Cash Registers"],
        summary="Cerrar caja",
        request=CashRegisterCloseSerializer,
        responses={
            200: CashRegisterSerializer,
        },
    )
    @action(
        detail=True,
        methods=["post"],
        url_path="close",
    )
    @db_tx.atomic
    def close(
        self,
        request,
        public_id=None,
    ):
        cash_register = (
            CashRegister.objects
            .select_for_update()
            .get(
                public_id=public_id
            )
        )

        self.check_object_permissions(
            request,
            cash_register,
        )

        self._validate_cash_access(
            cash_register.business
        )

        if (
            cash_register.status
            != CashRegister.STATUS_OPEN
        ):
            raise ValidationError({
                "status": (
                    "Esta caja ya está cerrada."
                )
            })

        serializer = (
            CashRegisterCloseSerializer(
                data=request.data
            )
        )

        serializer.is_valid(
            raise_exception=True
        )

        close_time = django_timezone.now()

        summary = calculate_cash_register_summary(
            cash_register,
            until=close_time,
        )

        expected_balance = summary[
            "expected_closing_balance"
        ]

        closing_balance = (
            serializer.validated_data[
                "closing_balance"
            ]
        )

        difference = (
            closing_balance
            - expected_balance
        ).quantize(
            Decimal("0.01")
        )

        cash_register.close_time = close_time
        cash_register.closing_balance = (
            closing_balance
        )
        cash_register.expected_closing_balance = (
            expected_balance
        )
        cash_register.difference = difference
        cash_register.closing_notes = (
            serializer.validated_data.get(
                "closing_notes",
                "",
            )
        )
        cash_register.closed_by = request.user
        cash_register.status = (
            CashRegister.STATUS_CLOSED
        )

        cash_register.save(
            update_fields=[
                "close_time",
                "closing_balance",
                "expected_closing_balance",
                "difference",
                "closing_notes",
                "closed_by",
                "status",
                "updated_at",
            ]
        )

        log_action(
            request.user,
            "CLOSE_CASH_REGISTER",
            cash_register.__class__.__name__,
            cash_register.pk,
            extra={
                "expected": str(
                    expected_balance
                ),
                "counted": str(
                    closing_balance
                ),
                "difference": str(
                    difference
                ),
            },
        )

        response_serializer = (
            CashRegisterSerializer(
                cash_register,
                context={
                    "request": request,
                },
            )
        )

        return Response(
            response_serializer.data,
            status=status.HTTP_200_OK,
        )

@extend_schema_view(
    list=extend_schema(
        tags=["Cash Movements"],
        summary="Listar movimientos de caja",
    ),
    retrieve=extend_schema(
        tags=["Cash Movements"],
        summary="Consultar movimiento de caja",
    ),
    create=extend_schema(
        tags=["Cash Movements"],
        summary="Registrar movimiento de caja",
    ),
)
class CashMovementViewSet(
    RequireBusinessPublicIdListMixin,
    viewsets.GenericViewSet,
    mixins.ListModelMixin,
    mixins.RetrieveModelMixin,
    mixins.CreateModelMixin,
):
    queryset = (
        CashMovement.objects
        .select_related(
            "cash_register",
            "cash_register__business",
            "employee",
            "payment_method",
            "created_by",
        )
        .order_by("-created_at")
    )

    serializer_class = CashMovementSerializer

    permission_classes = [
        IsAuthenticated,
    ]

    lookup_field = "public_id"
    lookup_url_kwarg = "public_id"

    pagination_class = (
        StandardResultsSetPagination
    )

    public_id_filter_fields = {
        "cash_register_public_id": (
            "cash_register__public_id"
        ),
        "employee_public_id": (
            "employee__public_id"
        ),
        "payment_method_public_id": (
            "payment_method__public_id"
        ),
    }

    simple_filter_fields = {
        "movement_type": filters.CharFilter(
            field_name="movement_type",
        ),
    }

    ordering_fields = [
        "created_at",
        "amount",
        "movement_type",
    ]

    ordering = [
        "-created_at",
    ]

    business_lookup = (
        "cash_register__business"
    )

    list_allowed_roles = [
        BusinessMembership.ROLE_OWNER,
        BusinessMembership.ROLE_ADMIN,
        BusinessMembership.ROLE_CASHIER,
    ]

    allowed_roles = [
        BusinessMembership.ROLE_OWNER,
        BusinessMembership.ROLE_ADMIN,
        BusinessMembership.ROLE_CASHIER,
    ]
    
    def get_queryset(self):
        queryset = super().get_queryset()
        user = self.request.user

        if user.is_superuser:
            return queryset

        return (
            queryset
            .filter(
                cash_register__business__memberships__user=user,
                cash_register__business__memberships__is_active=True,
                cash_register__business__memberships__role__in=(
                    self.allowed_roles
                ),
            )
            .distinct()
        )

    def _validate_access(
        self,
        business,
    ):
        user = self.request.user

        if user.is_superuser:
            return

        has_access = (
            BusinessMembership.objects
            .filter(
                user=user,
                business=business,
                is_active=True,
                role__in=self.allowed_roles,
            )
            .exists()
        )

        if not has_access:
            raise PermissionDenied(
                "No tienes permiso para registrar "
                "movimientos en esta caja."
            )

    @db_tx.atomic
    def perform_create(
        self,
        serializer,
    ):
        cash_register = (
            serializer.validated_data[
                "cash_register"
            ]
        )

        locked_register = (
            CashRegister.objects
            .select_for_update()
            .get(
                pk=cash_register.pk
            )
        )

        self._validate_access(
            locked_register.business
        )

        if (
            locked_register.status
            != CashRegister.STATUS_OPEN
        ):
            raise ValidationError({
                "cash_register_public_id": (
                    "No se pueden registrar "
                    "movimientos en una caja cerrada."
                )
            })

        movement = serializer.save(
            cash_register=locked_register,
            created_by=self.request.user,
        )

        log_action(
            self.request.user,
            "CREATE_CASH_MOVEMENT",
            movement.__class__.__name__,
            movement.pk,
            extra={
                "movement_type": (
                    movement.movement_type
                ),
                "amount": str(
                    movement.amount
                ),
            },
        )

class MonthlySummaryView(
    APIView
):
    permission_classes = [
        IsAuthenticated,
    ]

    @extend_schema(
        tags=["Reports"],
        summary="Resumen mensual del negocio",
        description=(
            "Separa volumen comercial, dinero recibido/pagado "
            "y saldos por cobrar/pagar durante un mes."
        ),
        parameters=[
            MonthlySummaryQuerySerializer,
        ],
        responses={
            200: OpenApiResponse(
                response=MonthlySummaryResponseSerializer,
                description=(
                    "Resumen mensual calculado."
                )
            ),
        },
    )
    def get(
        self,
        request,
    ):
        query_serializer = (
            MonthlySummaryQuerySerializer(
                data=request.query_params
            )
        )

        query_serializer.is_valid(
            raise_exception=True
        )

        business = get_object_or_404(
            Business,
            public_id=(
                query_serializer
                .validated_data[
                    "business_public_id"
                ]
            ),
        )

        validate_report_business_access(
            user=request.user,
            business=business,
        )

        summary = build_monthly_summary(
            business=business,
            year=(
                query_serializer
                .validated_data["year"]
            ),
            month=(
                query_serializer
                .validated_data["month"]
            ),
        )

        return Response(
            summary,
            status=status.HTTP_200_OK,
        )
    
@extend_schema_view(
    list=extend_schema(
        tags=["Monthly Closures"],
        summary="Listar cierres mensuales",
    ),
    retrieve=extend_schema(
        tags=["Monthly Closures"],
        summary="Consultar un cierre mensual",
    ),
    create=extend_schema(
        tags=["Monthly Closures"],
        summary="Cerrar un período mensual",
        request=MonthlyClosureCreateSerializer,
        responses={
            201: MonthlyClosureSerializer,
        },
    ),
)
class MonthlyClosureViewSet(
    RequireBusinessPublicIdListMixin,
    viewsets.GenericViewSet,
    mixins.ListModelMixin,
    mixins.RetrieveModelMixin,
    mixins.CreateModelMixin,
):
    queryset = (
        MonthlyClosure.objects
        .select_related(
            "business",
            "closed_by",
            "reopened_by",
        )
        .order_by(
            "-year",
            "-month",
            "-version",
        )
    )

    permission_classes = [
        IsAuthenticated,
    ]

    lookup_field = "public_id"
    lookup_url_kwarg = "public_id"

    pagination_class = (
        StandardResultsSetPagination
    )

    simple_filter_fields = {
        "year": filters.NumberFilter(
            field_name="year",
        ),
        "month": filters.NumberFilter(
            field_name="month",
        ),
        "version": filters.NumberFilter(
            field_name="version",
        ),
        "status": filters.CharFilter(
            field_name="status",
        ),
    }

    ordering_fields = [
        "year",
        "month",
        "version",
        "closed_at",
        "reopened_at",
    ]

    ordering = [
        "-year",
        "-month",
        "-version",
    ]

    allowed_roles = [
        BusinessMembership.ROLE_OWNER,
        BusinessMembership.ROLE_ADMIN,
    ]

    business_lookup = "business"

    list_allowed_roles = [
        BusinessMembership.ROLE_OWNER,
        BusinessMembership.ROLE_ADMIN,
    ]
    
    def get_serializer_class(self):
        if self.action == "create":
            return (
                MonthlyClosureCreateSerializer
            )

        if self.action == "reopen":
            return (
                MonthlyClosureReopenSerializer
            )

        return MonthlyClosureSerializer

    def get_queryset(self):
        queryset = super().get_queryset()
        user = self.request.user

        if user.is_superuser:
            return queryset

        return (
            queryset
            .filter(
                business__memberships__user=user,
                business__memberships__is_active=True,
                business__memberships__role__in=(
                    self.allowed_roles
                ),
            )
            .distinct()
        )

    def _validate_management_access(
        self,
        business,
    ):
        user = self.request.user

        if user.is_superuser:
            return

        has_access = (
            BusinessMembership.objects
            .filter(
                user=user,
                business=business,
                is_active=True,
                role__in=self.allowed_roles,
            )
            .exists()
        )

        if not has_access:
            raise PermissionDenied(
                "Solo el propietario o un "
                "administrador puede gestionar "
                "cierres mensuales."
            )

    def _validate_period_can_close(
        self,
        *,
        business,
        year,
        month,
    ):
        period = get_month_period(
            year=year,
            month=month,
        )

        today = django_timezone.localdate()

        if today < period["end_date"]:
            raise ValidationError({
                "period": (
                    "No se puede cerrar un mes "
                    "que todavía no ha finalizado."
                )
            })

        open_register_exists = (
            CashRegister.objects
            .filter(
                business=business,
                status=CashRegister.STATUS_OPEN,
                open_time__lt=(
                    period["end_datetime"]
                ),
            )
            .exists()
        )

        if open_register_exists:
            raise ValidationError({
                "cash_register": (
                    "Existe una caja abierta "
                    "correspondiente al período. "
                    "Debes cerrarla antes de "
                    "realizar el cierre mensual."
                )
            })

        active_closure_exists = (
            MonthlyClosure.objects
            .filter(
                business=business,
                year=year,
                month=month,
                status=(
                    MonthlyClosure
                    .STATUS_CLOSED
                ),
            )
            .exists()
        )

        if active_closure_exists:
            raise ValidationError({
                "period": (
                    "Este período ya tiene un "
                    "cierre mensual vigente."
                )
            })

    @db_tx.atomic
    def create(
        self,
        request,
        *args,
        **kwargs,
    ):
        serializer = self.get_serializer(
            data=request.data
        )

        serializer.is_valid(
            raise_exception=True
        )

        business = (
            serializer.validated_data[
                "business"
            ]
        )

        year = (
            serializer.validated_data[
                "year"
            ]
        )

        month = (
            serializer.validated_data[
                "month"
            ]
        )

        self._validate_management_access(
            business
        )

        # Bloquea el negocio durante el cierre
        # para evitar dos cierres simultáneos.
        business = (
            Business.objects
            .select_for_update()
            .get(
                pk=business.pk
            )
        )

        self._validate_period_can_close(
            business=business,
            year=year,
            month=month,
        )

        latest_version = (
            MonthlyClosure.objects
            .filter(
                business=business,
                year=year,
                month=month,
            )
            .aggregate(
                latest=Max("version")
            )
            .get("latest")
            or 0
        )

        next_version = latest_version + 1

        summary = build_monthly_summary(
            business=business,
            year=year,
            month=month,
        )

        try:
            closure = (
                MonthlyClosure.objects
                .create(
                    business=business,
                    year=year,
                    month=month,
                    version=next_version,
                    status=(
                        MonthlyClosure
                        .STATUS_CLOSED
                    ),
                    summary=summary,
                    closed_by=request.user,
                    closed_at=(
                        django_timezone.now()
                    ),
                )
            )
        except IntegrityError as exc:
            raise ValidationError({
                "period": (
                    "No fue posible crear el cierre "
                    "mensual porque ya existe otro "
                    "cierre vigente o la versión "
                    "ya fue utilizada."
                )
            }) from exc

        log_action(
            request.user,
            "CREATE_MONTHLY_CLOSURE",
            closure.__class__.__name__,
            closure.pk,
            extra={
                "year": year,
                "month": month,
                "version": next_version,
            },
        )

        response_serializer = (
            MonthlyClosureSerializer(
                closure,
                context={
                    "request": request,
                },
            )
        )

        return Response(
            response_serializer.data,
            status=status.HTTP_201_CREATED,
        )

    @extend_schema(
        tags=["Monthly Closures"],
        summary="Reabrir un cierre mensual",
        request=MonthlyClosureReopenSerializer,
        responses={
            200: MonthlyClosureSerializer,
        },
    )
    @action(
        detail=True,
        methods=["post"],
        url_path="reopen",
    )
    @db_tx.atomic
    def reopen(
        self,
        request,
        public_id=None,
    ):
        serializer = self.get_serializer(
            data=request.data
        )

        serializer.is_valid(
            raise_exception=True
        )

        closure = (
            MonthlyClosure.objects
            .select_for_update()
            .get(
                public_id=public_id
            )
        )

        self.check_object_permissions(
            request,
            closure,
        )

        self._validate_management_access(
            closure.business
        )

        if (
            closure.status
            != MonthlyClosure.STATUS_CLOSED
        ):
            raise ValidationError({
                "status": (
                    "Este cierre mensual ya está "
                    "reabierto."
                )
            })

        closure.status = (
            MonthlyClosure.STATUS_REOPENED
        )

        closure.reopened_by = request.user

        closure.reopened_at = (
            django_timezone.now()
        )

        closure.reopen_reason = (
            serializer.validated_data[
                "reason"
            ]
        )

        closure.save(
            update_fields=[
                "status",
                "reopened_by",
                "reopened_at",
                "reopen_reason",
                "updated_at",
            ]
        )

        log_action(
            request.user,
            "REOPEN_MONTHLY_CLOSURE",
            closure.__class__.__name__,
            closure.pk,
            extra={
                "year": closure.year,
                "month": closure.month,
                "version": closure.version,
                "reason": (
                    closure.reopen_reason
                ),
            },
        )

        response_serializer = (
            MonthlyClosureSerializer(
                closure,
                context={
                    "request": request,
                },
            )
        )

        return Response(
            response_serializer.data,
            status=status.HTTP_200_OK,
        )

class CustomerSummaryView(
    APIView
):
    permission_classes = [
        IsAuthenticated,
    ]

    @extend_schema(
        tags=["Reports"],
        summary="Resumen de clientes",
        description=(
            "Calcula las ventas acumuladas por "
            "cliente dentro de un rango de fechas."
        ),
        parameters=[
            CustomerSummaryQuerySerializer,
        ],
        responses={
            200: OpenApiResponse(
                description=(
                    "Resumen dinámico de clientes."
                )
            ),
        },
    )
    def get(
        self,
        request,
    ):
        query_serializer = (
            CustomerSummaryQuerySerializer(
                data=request.query_params
            )
        )

        query_serializer.is_valid(
            raise_exception=True
        )

        validated_data = (
            query_serializer.validated_data
        )

        business = get_object_or_404(
            Business,
            public_id=validated_data[
                "business_public_id"
            ],
        )

        validate_report_business_access(
            user=request.user,
            business=business,
        )

        customer = None

        customer_public_id = (
            validated_data.get(
                "customer_public_id"
            )
        )

        if customer_public_id is not None:
            customer = get_object_or_404(
                Customer,
                public_id=customer_public_id,
                business=business,
            )

        summary = build_customers_summary(
            business=business,
            date_from=validated_data[
                "date_from"
            ],
            date_to=validated_data[
                "date_to"
            ],
            customer=customer,
        )

        return Response(
            summary,
            status=status.HTTP_200_OK,
        )


class SupplierSummaryView(
    APIView
):
    permission_classes = [
        IsAuthenticated,
    ]

    @extend_schema(
        tags=["Reports"],
        summary="Resumen de proveedores",
        description=(
            "Calcula las compras acumuladas por "
            "proveedor dentro de un rango de fechas."
        ),
        parameters=[
            SupplierSummaryQuerySerializer,
        ],
        responses={
            200: OpenApiResponse(
                description=(
                    "Resumen dinámico de proveedores."
                )
            ),
        },
    )
    def get(
        self,
        request,
    ):
        query_serializer = (
            SupplierSummaryQuerySerializer(
                data=request.query_params
            )
        )

        query_serializer.is_valid(
            raise_exception=True
        )

        validated_data = (
            query_serializer.validated_data
        )

        business = get_object_or_404(
            Business,
            public_id=validated_data[
                "business_public_id"
            ],
        )

        validate_report_business_access(
            user=request.user,
            business=business,
        )

        supplier = None

        supplier_public_id = (
            validated_data.get(
                "supplier_public_id"
            )
        )

        if supplier_public_id is not None:
            supplier = get_object_or_404(
                Supplier,
                public_id=supplier_public_id,
                business=business,
            )

        summary = build_suppliers_summary(
            business=business,
            date_from=validated_data[
                "date_from"
            ],
            date_to=validated_data[
                "date_to"
            ],
            supplier=supplier,
        )

        return Response(
            summary,
            status=status.HTTP_200_OK,
        )

class PaymentSummaryView(
    APIView
):
    permission_classes = [
        IsAuthenticated,
    ]

    @extend_schema(
        tags=["Reports"],
        summary="Resumen de pagos",
        description=(
            "Separa pagos recibidos y realizados por método. "
            "Las transacciones con deuda histórica se reconocen "
            "exclusivamente mediante sus pagos de deuda."
        ),
        parameters=[
            PaymentSummaryQuerySerializer,
        ],
        responses={
            200: OpenApiResponse(
                response=PaymentSummaryResponseSerializer,
                description=(
                    "Resumen dinámico de pagos."
                )
            ),
        },
    )
    def get(
        self,
        request,
    ):
        query_serializer = (
            PaymentSummaryQuerySerializer(
                data=request.query_params
            )
        )

        query_serializer.is_valid(
            raise_exception=True
        )

        validated_data = (
            query_serializer.validated_data
        )

        business = get_object_or_404(
            Business,
            public_id=validated_data[
                "business_public_id"
            ],
        )

        validate_report_business_access(
            user=request.user,
            business=business,
        )

        payment_method = None

        payment_method_public_id = (
            validated_data.get(
                "payment_method_public_id"
            )
        )

        if (
            payment_method_public_id
            is not None
        ):
            payment_method = get_object_or_404(
                PaymentMethod,
                public_id=(
                    payment_method_public_id
                ),
                business=business,
            )

        summary = build_payments_summary(
            business=business,
            date_from=validated_data[
                "date_from"
            ],
            date_to=validated_data[
                "date_to"
            ],
            payment_method=payment_method,
        )

        return Response(
            summary,
            status=status.HTTP_200_OK,
        )

class DebtSummaryView(
    APIView
):
    permission_classes = [
        IsAuthenticated,
    ]

    @extend_schema(
        tags=["Reports"],
        summary="Resumen de deudas",
        description=(
            "Separa cuentas por cobrar de ventas, cuentas por "
            "pagar de compras y deudas históricas no clasificadas."
        ),
        parameters=[
            DebtSummaryQuerySerializer,
        ],
        responses={
            200: OpenApiResponse(
                response=DebtSummaryResponseSerializer,
                description=(
                    "Resumen dinámico de deudas."
                )
            ),
        },
    )
    def get(
        self,
        request,
    ):
        query_serializer = (
            DebtSummaryQuerySerializer(
                data=request.query_params
            )
        )

        query_serializer.is_valid(
            raise_exception=True
        )

        validated_data = (
            query_serializer.validated_data
        )

        business = get_object_or_404(
            Business,
            public_id=validated_data[
                "business_public_id"
            ],
        )

        validate_report_business_access(
            user=request.user,
            business=business,
        )

        summary = build_debts_summary(
            business=business,
            date_from=validated_data[
                "date_from"
            ],
            date_to=validated_data[
                "date_to"
            ],
        )

        return Response(
            summary,
            status=status.HTTP_200_OK,
        )

class InventorySummaryView(
    APIView
):
    permission_classes = [
        IsAuthenticated,
    ]

    @extend_schema(
        tags=["Reports"],
        summary="Resumen histórico de inventario",
        description=(
            "Calcula existencias iniciales, "
            "entradas, ventas, ajustes y "
            "existencias finales por producto dentro de un período."
        ),
        parameters=[
            InventorySummaryQuerySerializer,
        ],
        responses={
            200: OpenApiResponse(
                description=(
                    "Resumen dinámico de inventario."
                )
            ),
        },
    )
    def get(
        self,
        request,
    ):
        query_serializer = (
            InventorySummaryQuerySerializer(
                data=request.query_params
            )
        )

        query_serializer.is_valid(
            raise_exception=True
        )

        validated_data = (
            query_serializer.validated_data
        )

        business = get_object_or_404(
            Business,
            public_id=validated_data[
                "business_public_id"
            ],
        )

        validate_report_business_access(
            user=request.user,
            business=business,
            allowed_roles=[
                BusinessMembership.ROLE_OWNER,
                BusinessMembership.ROLE_ADMIN,
                BusinessMembership.ROLE_INVENTORY,
            ],
        )

        product = None
        product_public_id = (
            validated_data.get(
                "product_public_id"
            )
        )

        if product_public_id is not None:
            product = get_object_or_404(
                Product,
                public_id=(
                    product_public_id
                ),
                business=business,
            )

        summary = build_inventory_summary(
            business=business,
            date_from=validated_data[
                "date_from"
            ],
            date_to=validated_data[
                "date_to"
            ],
            product=product,
        )

        return Response(
            summary,
            status=status.HTTP_200_OK,
        )

class DashboardOverviewView(
    APIView
):
    permission_classes = [
        IsAuthenticated,
    ]

    @extend_schema(
        tags=["Dashboard"],
        summary="Vista general del negocio",
        description=(
            "Devuelve volumen comercial, pagos recibidos y hechos, "
            "cuentas por cobrar/pagar, caja, comisiones e inventario. "
            "outstanding_debt se conserva como agregado bruto legado."
        ),
        parameters=[
            DashboardOverviewQuerySerializer,
        ],
        responses={
            200: OpenApiResponse(
                response=DashboardOverviewResponseSerializer,
                description=(
                    "Indicadores generales del "
                    "negocio."
                )
            ),
        },
    )
    def get(
        self,
        request,
    ):
        query_serializer = (
            DashboardOverviewQuerySerializer(
                data=request.query_params
            )
        )

        query_serializer.is_valid(
            raise_exception=True
        )

        validated_data = (
            query_serializer.validated_data
        )

        business = get_object_or_404(
            Business,
            public_id=validated_data[
                "business_public_id"
            ],
        )

        validate_report_business_access(
            user=request.user,
            business=business,
            allowed_roles=[
                BusinessMembership.ROLE_OWNER,
                BusinessMembership.ROLE_ADMIN,
                BusinessMembership.ROLE_VIEWER,
            ],
        )

        overview = build_dashboard_overview(
            business=business,
            date_from=validated_data[
                "date_from"
            ],
            date_to=validated_data[
                "date_to"
            ],
            low_stock_threshold=(
                validated_data[
                    "low_stock_threshold"
                ]
            ),
        )

        return Response(
            overview,
            status=status.HTTP_200_OK,
        )

from core.api.views.current_user import CurrentUserView
