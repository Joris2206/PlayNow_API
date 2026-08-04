from decimal import Decimal
from django.core.exceptions import FieldDoesNotExist, ObjectDoesNotExist
from django.utils import timezone
from rest_framework import viewsets, mixins, status
from rest_framework.views import APIView
from rest_framework.decorators import action, api_view, permission_classes, throttle_classes
from rest_framework.permissions import IsAuthenticated, AllowAny
from rest_framework.response import Response
from rest_framework.exceptions import ValidationError, PermissionDenied
from rest_framework.generics import GenericAPIView
from drf_spectacular.utils import extend_schema, extend_schema_view, OpenApiExample, OpenApiResponse
from .filters import StockMovementFilter, TransactionFilter
from .pagination import StandardResultsSetPagination
from .mixins import SoftDeleteByStatusMixin
from django.db import transaction as db_tx
from django.db.models import (Avg, Count, Q, Sum)
from collections import defaultdict
from core.utils import log_action
from rest_framework.throttling import ScopedRateThrottle
from .serializers import (
    BusinessMembershipSerializer,
    BusinessMembershipUpdateSerializer,
    EmployeeAccessCreateSerializer,
    HealthSerializer,
)
from .services.serializer import ChangePasswordSerializer, PasswordResetRequestSerializer, PasswordResetConfirmSerializer
from django.conf import settings
from django.shortcuts import get_object_or_404


FRONTEND_RESET_URL = settings.FRONTEND_RESET_URL


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
VARIANT_TYPE_PUBLIC_ID = "664de038-94c0-451c-969f-1ac5c93c6220"
VARIANT_PUBLIC_ID = "c5067b51-b848-4c5e-98dc-b2cfcf6d63db"
CUSTOMER_PUBLIC_ID = "eb659af9-b488-4562-85cb-c4b4130aa607"
SUPPLIER_PUBLIC_ID = "e4069f25-0208-4094-9609-d7e40db38b27"
PAYMENT_METHOD_PUBLIC_ID = "9f98c592-fc72-4649-8c39-a1540c895737"
TRANSACTION_PUBLIC_ID = "bcd85f11-e36d-4cac-94ca-b005f48843cf"
DEBT_PUBLIC_ID = "4da69052-bb85-483b-aef8-ad3d14579a49"
GOAL_PUBLIC_ID = "85af7d8e-8dc8-4616-8609-ce92df799ad6"
STATUS_PUBLIC_ID = "61823ecf-a0ec-45e3-b909-4ee8780a8246"

REGISTER_EXAMPLE = OpenApiExample(
    "Registro de propietario",
    value={
        "email": "maria.lopez@example.com",
        "full_name": "María López",
        "password": "ClaveSegura2026!",
    },
    request_only=True,
)

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
        "business": BUSINESS_PUBLIC_ID,
        "name": "Calzado",
    },
    request_only=True,
)

PRODUCT_CREATE_EXAMPLE = OpenApiExample(
    "Crear producto",
    value={
        "business": BUSINESS_PUBLIC_ID,
        "category": CATEGORY_PUBLIC_ID,
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

VARIANT_TYPE_CREATE_EXAMPLE = OpenApiExample(
    "Crear tipo de variante",
    value={
        "product": PRODUCT_PUBLIC_ID,
        "name": "Talla",
    },
    request_only=True,
)

VARIANT_CREATE_EXAMPLE = OpenApiExample(
    "Crear variante",
    value={
        "variant_type": VARIANT_TYPE_PUBLIC_ID,
        "label": "Talla 38",
        "additional_price": "0.00",
        "stock": 8,
    },
    request_only=True,
)

EMPLOYEE_CREATE_EXAMPLE = OpenApiExample(
    "Registrar empleado sin acceso al sistema",
    value={
        "business": BUSINESS_PUBLIC_ID,
        "full_name": "Ana Martínez",
        "phone": "7777-4321",
        "position": "Dependiente",
    },
    request_only=True,
)

CUSTOMER_CREATE_EXAMPLE = OpenApiExample(
    "Registrar cliente",
    value={
        "business": BUSINESS_PUBLIC_ID,
        "full_name": "José Ramírez",
        "phone": "8666-1122",
        "email": "jose.ramirez@example.com",
    },
    request_only=True,
)

SUPPLIER_CREATE_EXAMPLE = OpenApiExample(
    "Registrar proveedor",
    value={
        "business": BUSINESS_PUBLIC_ID,
        "name": "Distribuidora Central",
        "phone": "2255-7788",
        "email": "ventas@distribuidoracentral.example.com",
    },
    request_only=True,
)

PAYMENT_METHOD_CREATE_EXAMPLE = OpenApiExample(
    "Crear método de pago",
    value={
        "business": BUSINESS_PUBLIC_ID,
        "name": "Transferencia bancaria",
    },
    request_only=True,
)

TRANSACTION_SALE_EXAMPLE = OpenApiExample(
    "Venta pagada",
    value={
        "business": BUSINESS_PUBLIC_ID,
        "customer": CUSTOMER_PUBLIC_ID,
        "supplier": None,
        "payment_method": PAYMENT_METHOD_PUBLIC_ID,
        "type": "sale",
        "discount_percent": "5.00",
        "concept": "Venta en mostrador",
        "payment_status": "paid",
        "invoice_number": "000145",
        "invoice_series": "A",
        "invoice_file_url": "",
        "details": [
            {
                "product": PRODUCT_PUBLIC_ID,
                "variant": VARIANT_PUBLIC_ID,
                "quantity": 2,
                "unit_price": "850.00",
            }
        ],
    },
    request_only=True,
)

TRANSACTION_PURCHASE_EXAMPLE = OpenApiExample(
    "Compra de inventario",
    value={
        "business": BUSINESS_PUBLIC_ID,
        "customer": None,
        "supplier": SUPPLIER_PUBLIC_ID,
        "payment_method": PAYMENT_METHOD_PUBLIC_ID,
        "type": "purchase",
        "discount_percent": "0.00",
        "concept": "Reposición semanal de inventario",
        "payment_status": "paid",
        "invoice_number": "FAC-9087",
        "invoice_series": "PROV",
        "invoice_file_url": "",
        "details": [
            {
                "product": PRODUCT_PUBLIC_ID,
                "variant": VARIANT_PUBLIC_ID,
                "quantity": 12,
                "unit_price": "560.00",
            }
        ],
    },
    request_only=True,
)

TRANSACTION_EXPENSE_EXAMPLE = OpenApiExample(
    "Gasto general",
    value={
        "business": BUSINESS_PUBLIC_ID,
        "customer": None,
        "supplier": None,
        "payment_method": PAYMENT_METHOD_PUBLIC_ID,
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
        "customer": CUSTOMER_PUBLIC_ID,
        "payment_method": PAYMENT_METHOD_PUBLIC_ID,
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
        "transaction": TRANSACTION_PUBLIC_ID,
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
        "debt": DEBT_PUBLIC_ID,
        "amount": "500.00",
        "payment_date": "2026-07-30",
        "payment_method": PAYMENT_METHOD_PUBLIC_ID,
        "transaction": None,
    },
    request_only=True,
)

NOTIFICATION_CREATE_EXAMPLE = OpenApiExample(
    "Crear notificación",
    value={
        "title": "Stock bajo",
        "message": "El zapato deportivo talla 38 tiene solo 3 unidades.",
        "type": "warning",
        "business": BUSINESS_PUBLIC_ID,
        "transaction": None,
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
        "business": BUSINESS_PUBLIC_ID,
        "transaction": None,
    },
    request_only=True,
)

BUDGET_CREATE_EXAMPLE = OpenApiExample(
    "Crear presupuesto mensual",
    value={
        "business": BUSINESS_PUBLIC_ID,
        "status": STATUS_PUBLIC_ID,
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
        "business": BUSINESS_PUBLIC_ID,
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
        "goal": GOAL_PUBLIC_ID,
        "amount": "3500.00",
        "transaction": TRANSACTION_PUBLIC_ID,
        "status": STATUS_PUBLIC_ID,
        "note": "Venta mayorista registrada.",
    },
    request_only=True,
)

from .models import (
    BusinessMembership, User, Business, EntityStatus,
    ProductCategory, Product, ProductVariantType, ProductVariant,
    Employee, Customer, Supplier, PaymentMethod,
    Transaction, TransactionDetail, StockMovement,
    Debt, DebtPayment, Notification, Reminder,
    Budget, Goal, GoalProgress, EmployeeCommissionPlan, CommissionSettlement
)
from .serializers import (
    UserSerializer, RegisterSerializer,
    BusinessSerializer, EntityStatusSerializer,
    ProductCategorySerializer, ProductSerializer, ProductVariantTypeSerializer, ProductVariantSerializer,
    EmployeeSerializer, CustomerSerializer, SupplierSerializer, PaymentMethodSerializer,
    TransactionSerializer, TransactionDetailSerializer, StockMovementSerializer,
    DebtSerializer, DebtPaymentSerializer, NotificationSerializer, ReminderSerializer,
    BudgetSerializer, GoalSerializer, GoalProgressSerializer, CommissionSettlementCreateSerializer, CommissionSettlementSerializer, EmployeeCommissionPlanSerializer
)
from .permissions import IsOwnerOrBusinessOwner

def validate_report_business_access(
    *,
    user,
    business,
):
    if user.is_superuser:
        return

    has_access = (
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

    if not has_access:
        raise PermissionDenied(
            "Solo el propietario o un "
            "administrador puede consultar "
            "este reporte."
        )

# -------- Healthcheck (ya lo usaste en /api/health/) --------
@extend_schema(
    responses=HealthSerializer,
    tags=["Health"],
    examples=[OpenApiExample("OK", value={"status": "ok", "service": "PlayNow API"})],
)
@api_view(["GET"])
@permission_classes([AllowAny])
@throttle_classes([ScopedRateThrottle])
def healthcheck(request):
    return Response({"status": "ok", "service": "PlayNow API"})

# Define el scope para que use tu rate "public_read"
healthcheck.throttle_scope = "public_read"
# -------- Auth --------
@extend_schema_view(
    create=extend_schema(
        tags=["Auth"],
        summary="Registrar propietario",
        description=(
            "Crea una cuenta de propietario. El negocio se registra "
            "posteriormente desde el módulo Businesses."
        ),
        examples=[REGISTER_EXAMPLE],
    ),
)
class RegisterViewSet(mixins.CreateModelMixin, viewsets.GenericViewSet):
    queryset = User.objects.all()
    serializer_class = RegisterSerializer
    permission_classes = [AllowAny]
    throttle_classes = [ScopedRateThrottle]
    throttle_scope = "auth_register"

# -------- Base mixin para filtrar por usuario --------

class BusinessScopedViewSet(viewsets.ModelViewSet):
    """
    ViewSet base para recursos pertenecientes a un usuario o negocio.

    Reglas:

    - Un superusuario de Django puede consultar todos los registros.
    - Un usuario normal solo consulta registros de negocios donde tiene
      una BusinessMembership activa.
    - Los modelos personales con campo `user` continúan filtrándose por
      el usuario autenticado.
    - No se permite crear ni mover registros hacia negocios sin acceso.
    - Los registros inactivos se excluyen por defecto.
    - Se mantiene compatibilidad temporal con `owner_lookup`.
    """

    permission_classes = [
        IsAuthenticated,
        IsOwnerOrBusinessOwner,
    ]

    throttle_classes = [
        ScopedRateThrottle,
    ]

    EXCLUDED_STATUS_NAMES = [
        "Eliminado",
        "Anulado",
        "Inactivo",
        "Cancelado",
    ]

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

        Soporta:

            business_lookup = "business"
            business_lookup = "product__business"
            business_lookup = "variant_type__product__business"

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

        ProductVariantType:
            business_lookup = "product__business"

        ProductVariant:
            business_lookup = (
                "variant_type__product__business"
            )
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
        model_cls = queryset.model

        if not user.is_authenticated:
            return queryset.none()

        if not self._is_platform_admin(user):
            business_lookup = (
                self._get_business_lookup()
            )

            if business_lookup:
                membership_user_lookup = (
                    f"{business_lookup}"
                    "__memberships__user"
                )

                membership_active_lookup = (
                    f"{business_lookup}"
                    "__memberships__is_active"
                )

                membership_filters = {
                    membership_user_lookup: user,
                    membership_active_lookup: True,
                }

                if self.read_allowed_roles is not None:
                    membership_role_lookup = (
                        f"{business_lookup}"
                        "__memberships__role__in"
                    )

                    membership_filters[
                        membership_role_lookup
                    ] = self.read_allowed_roles

                queryset = queryset.filter(
                    **membership_filters
                )

            else:
                owner_lookup = getattr(self, "owner_lookup", None)

                if owner_lookup:
                    # Recursos personales, por ejemplo:
                    # owner_lookup = "user"
                    # owner_lookup = "goal__user"
                    queryset = queryset.filter(
                        **{owner_lookup: user}
                    )

                elif self._model_has_field(
                    model_cls,
                    "user",
                ):
                    queryset = queryset.filter(
                        user=user
                    )

                else:
                    # Un modelo sin usuario ni ruta hacia un negocio no
                    # debe mostrar accidentalmente todos los registros.
                    queryset = queryset.none()

        include_inactive = (
            self.request.query_params.get(
                "include_inactive"
            )
        )

        want_inactive = str(
            include_inactive
        ).lower() in (
            "1",
            "true",
            "yes",
            "y",
        )

        if (
            self._model_has_field(
                model_cls,
                "status",
            )
            and not want_inactive
        ):
            queryset = queryset.exclude(
                status__name__in=(
                    self.EXCLUDED_STATUS_NAMES
                )
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

    filterset_fields = ["status", "business", "business__public_id"]
    search_fields = ["title"]
    ordering_fields = ["title", "created_at", "updated_at"]
    ordering = ["-created_at"]

    pagination_class = StandardResultsSetPagination


@extend_schema_view(
    list=extend_schema(tags=["Product Variant Types"]),
    retrieve=extend_schema(tags=["Product Variant Types"]),
    create=extend_schema(
        tags=["Product Variant Types"],
        description="Crea un tipo de variante, por ejemplo talla o color.",
        examples=[VARIANT_TYPE_CREATE_EXAMPLE],
    ),
    update=extend_schema(tags=["Product Variant Types"]),
    partial_update=extend_schema(tags=["Product Variant Types"]),
    destroy=extend_schema(tags=["Product Variant Types"]),
)
class ProductVariantTypeViewSet(SoftDeleteByStatusMixin, BusinessScopedViewSet):
    queryset = ProductVariantType.objects.select_related("product", "product__business", "status").order_by("-created_at", "-id")
    serializer_class = ProductVariantTypeSerializer

    business_lookup = "product__business"

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


@extend_schema_view(
    list=extend_schema(tags=["Product Variants"]),
    retrieve=extend_schema(tags=["Product Variants"]),
    create=extend_schema(
        tags=["Product Variants"],
        description="Crea una opción concreta de variante para un producto.",
        examples=[VARIANT_CREATE_EXAMPLE],
    ),
    update=extend_schema(tags=["Product Variants"]),
    partial_update=extend_schema(tags=["Product Variants"]),
    destroy=extend_schema(tags=["Product Variants"]),
)
class ProductVariantViewSet(SoftDeleteByStatusMixin, BusinessScopedViewSet):
    queryset = ProductVariant.objects.select_related("variant_type", "variant_type__product", "variant_type__product__business", "status").order_by("-created_at", "-id")
    serializer_class = ProductVariantSerializer

    business_lookup = "variant_type__product__business"

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


@extend_schema_view(
    list=extend_schema(tags=["Employees"]),
    retrieve=extend_schema(tags=["Employees"]),
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
    ]

    create_allowed_roles = read_allowed_roles
    update_allowed_roles = read_allowed_roles
    destroy_allowed_roles = read_allowed_roles

    filterset_fields = ["status", "business", "business__public_id"]
    search_fields = ["full_name", "phone"]
    ordering_fields = ["full_name", "created_at", "updated_at"]
    ordering = ["-created_at"]

    pagination_class = StandardResultsSetPagination


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
    
    filterset_fields = ["status", "business", "business__public_id"]
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

    filterset_fields = ["status", "business", "business__public_id"]
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
    queryset = PaymentMethod.objects.select_related("business").all()
    serializer_class = PaymentMethodSerializer

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
    ]

    update_allowed_roles = create_allowed_roles
    destroy_allowed_roles = create_allowed_roles

    lookup_field = "public_id"
    lookup_url_kwarg = "public_id"
    pagination_class = StandardResultsSetPagination


@extend_schema_view(
    list=extend_schema(tags=["Stock Movements"]),
    retrieve=extend_schema(tags=["Stock Movements"]),
)
class StockMovementViewSet(SoftDeleteByStatusMixin, BusinessScopedViewSet):
    queryset = (
        StockMovement.objects
        .select_related("product", "product__business", "variant", "variant__variant_type", "transaction")
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
    search_fields = ["product__title", "variant__label", "variant__variant_type__name", "transaction__public_id"]
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
            "indicarse el empleado al que pertenece la venta."
        ),
        examples=[
            TRANSACTION_SALE_EXAMPLE,
            TRANSACTION_PURCHASE_EXAMPLE,
            TRANSACTION_EXPENSE_EXAMPLE,
        ],
    ),
    update=extend_schema(
        tags=["Transactions"],
        summary="Actualizar una transacción",
        description=(
            "No permite cambiar el negocio, el tipo, los detalles, "
            "el estado de pago ni el monto de un gasto."
        ),
        examples=[TRANSACTION_UPDATE_EXAMPLE],
    ),
    partial_update=extend_schema(
        tags=["Transactions"],
        summary="Actualizar parcialmente una transacción",
        description=(
            "Permite modificar únicamente los campos editables de "
            "una transacción existente."
        ),
        examples=[TRANSACTION_UPDATE_EXAMPLE],
    ),
    destroy=extend_schema(tags=["Transactions"], summary="Baja lógica + neutralizar inventario"),
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
        .prefetch_related("details", "details__product", "details__variant")
        .all()
    )
    serializer_class = TransactionSerializer
    business_lookup = "business"

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
            rows = []

            for detail in tx.details.select_related(
                "product",
                "variant",
            ):
                product = detail.product
                variant = detail.variant
                quantity = detail.quantity

                if variant is not None:
                    stock_target = (
                        ProductVariant.objects
                        .select_for_update()
                        .get(pk=variant.pk)
                    )
                else:
                    stock_target = (
                        Product.objects
                        .select_for_update()
                        .get(pk=product.pk)
                    )

                new_stock = stock_target.stock + (
                    sign * quantity
                )

                if new_stock < 0:
                    resource = (
                        variant.label
                        if variant is not None
                        else product.title
                    )

                    raise ValidationError({
                        "details": (
                            f"Stock insuficiente en {resource}."
                        )
                    })

                stock_target.stock = new_stock
                stock_target.save(
                    update_fields=["stock"]
                )

                rows.append(
                    StockMovement(
                        product=product,
                        variant=variant,
                        transaction=tx,
                        transaction_detail=detail,
                        created_by=self.request.user,
                        type=(
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
                )

            if rows:
                StockMovement.objects.bulk_create(rows)

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
    def on_soft_delete(self, tx: Transaction):
        self._validate_business_access(
            tx.business,
            allowed_roles=[
                BusinessMembership.ROLE_OWNER,
                BusinessMembership.ROLE_ADMIN,
            ],
        )

        # neutraliza inventario y stock
        totals = defaultdict(int)
        for pid, vid, qty in tx.stock_movements.all().values_list("product_id", "variant_id", "quantity"):
            totals[(pid, vid)] += qty

        to_movs = []
        for (pid, vid), total_qty in totals.items():
            if total_qty:
                # revertir existencias
                if vid:
                    variant = (
                        ProductVariant.objects
                        .select_for_update()
                        .get(pk=vid)
                    )

                    new_stock = variant.stock - total_qty

                    if new_stock < 0:
                        raise ValidationError({
                            "details": (
                                "No se puede eliminar la transacción porque "
                                "dejaría stock negativo en una variante."
                            )
                        })

                    variant.stock = new_stock
                    variant.save(update_fields=["stock"])
                else:
                    product = (
                        Product.objects
                        .select_for_update()
                        .get(pk=pid)
                    )

                    new_stock = product.stock - total_qty

                    if new_stock < 0:
                        raise ValidationError({
                            "details": (
                                "No se puede eliminar la transacción porque "
                                "dejaría stock negativo en un producto."
                            )
                        })

                    product.stock = new_stock
                    product.save(update_fields=["stock"])

                to_movs.append(StockMovement(
                    product_id=pid,
                    variant_id=vid,
                    transaction=tx,
                    created_by=self.request.user,
                    type="adjustment",
                    quantity=-total_qty,
                    note=f"Auto neutralize {tx.public_id}",
                ))
        if to_movs:
            StockMovement.objects.bulk_create(to_movs)
    
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
            "transaction__employee",
        )
        .order_by("-created_at")
    )
    
    serializer_class = DebtSerializer
    business_lookup = "transaction__business"

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
    queryset = DebtPayment.objects.select_related("debt", "debt__transaction", "debt__transaction__business", "payment_method", "transaction").all()
    serializer_class = DebtPaymentSerializer

    business_lookup = "debt__transaction__business"

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

    owner_lookup = "user"

    lookup_field = "public_id"
    lookup_url_kwarg = "public_id"
    pagination_class = StandardResultsSetPagination


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

    owner_lookup = "user"

    lookup_field = "public_id"
    lookup_url_kwarg = "public_id"
    pagination_class = StandardResultsSetPagination


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

    owner_lookup = "user"

    lookup_field = "public_id"
    lookup_url_kwarg = "public_id"
    pagination_class = StandardResultsSetPagination


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

    owner_lookup = "user"

    lookup_field = "public_id"
    lookup_url_kwarg = "public_id"
    pagination_class = StandardResultsSetPagination

@extend_schema_view(
    list=extend_schema(tags=["Goal Progress"]),
    retrieve=extend_schema(tags=["Goal Progress"]),
    create=extend_schema(
        tags=["Goal Progress"],
        description="Registra un avance y actualiza automáticamente el progreso de la meta.",
        examples=[GOAL_PROGRESS_CREATE_EXAMPLE],
    ),
    update=extend_schema(tags=["Goal Progress"]),
    partial_update=extend_schema(tags=["Goal Progress"]),
    destroy=extend_schema(tags=["Goal Progress"]),
)
class GoalProgressViewSet(SoftDeleteByStatusMixin, BusinessScopedViewSet):
    queryset = GoalProgress.objects.select_related("goal", "goal__business").all()
    serializer_class = GoalProgressSerializer

    owner_lookup = "goal__user"

    lookup_field = "public_id"
    lookup_url_kwarg = "public_id"
    pagination_class = StandardResultsSetPagination

@extend_schema(tags=["Users"])
class UserViewSet(viewsets.GenericViewSet):
    permission_classes = [IsAuthenticated]
    throttle_classes = [ScopedRateThrottle]
    throttle_scope = "admin_write"

    queryset = User.objects.none()

    @extend_schema(
        request=ChangePasswordSerializer,
        responses={204: OpenApiResponse(description="Contraseña actualizada exitosamente.")},
        tags=["Users"]
    )
    @action(methods=["post"], detail=False, url_path="change-password")
    def change_password(self, request):
        ser = ChangePasswordSerializer(data=request.data, context={"request": request})
        ser.is_valid(raise_exception=True)
        user = request.user
        user.set_password(ser.validated_data["new_password"])
        user.save(update_fields=["password"])
        return Response(status=status.HTTP_204_NO_CONTENT)
    
@extend_schema(
    tags=["Auth"],
    request=PasswordResetRequestSerializer,
    responses={200: None},
    summary="Solicitar restablecimiento de contraseña"
)
class PasswordResetRequestView(APIView):
    permission_classes = [AllowAny]
    throttle_classes = [ScopedRateThrottle]
    throttle_scope = "auth_reset_request"

    def post(self, request):
        ser = PasswordResetRequestSerializer(data=request.data)
        ser.is_valid(raise_exception=True)
        ser.save(frontend_reset_url=FRONTEND_RESET_URL)
        return Response(status=status.HTTP_200_OK)


@extend_schema(
    tags=["Auth"],
    request=PasswordResetConfirmSerializer,
    responses={204: None},
    summary="Confirmar restablecimiento de contraseña"
)
class PasswordResetConfirmView(APIView):
    permission_classes = [AllowAny]
    throttle_classes = [ScopedRateThrottle]
    throttle_scope = "auth_reset_confirm"

    def post(self, request):
        ser = PasswordResetConfirmSerializer(data=request.data)
        ser.is_valid(raise_exception=True)
        ser.save()
        return Response(status=status.HTTP_204_NO_CONTENT)

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

    filterset_fields = [
        "employee__public_id",
        "employee__business__public_id",
        "is_active",
    ]

    ordering_fields = [
        "valid_from",
        "valid_until",
        "percentage",
        "created_at",
    ]

    ordering = [
        "-valid_from",
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
        parameters=[
            {
                "name": "business_public_id",
                "required": True,
                "in": "query",
                "schema": {
                    "type": "string",
                    "format": "uuid",
                },
            },
            {
                "name": "employee_public_id",
                "required": True,
                "in": "query",
                "schema": {
                    "type": "string",
                    "format": "uuid",
                },
            },
            {
                "name": "date_from",
                "required": True,
                "in": "query",
                "schema": {
                    "type": "string",
                    "format": "date",
                },
            },
            {
                "name": "date_to",
                "required": True,
                "in": "query",
                "schema": {
                    "type": "string",
                    "format": "date",
                },
            },
        ],
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

        excluded_statuses = [
            "Eliminado",
            "Anulado",
            "Cancelado",
            "Void",
            "Deleted",
        ]

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
            .exclude(
                status__name__in=excluded_statuses
            )
            .order_by("-created_at")
        )

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

        excluded_statuses = [
            "Eliminado",
            "Anulado",
            "Cancelado",
            "Void",
            "Deleted",
        ]

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
            .exclude(
                status__name__in=excluded_statuses
            )
        )

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
            },
            "period": {
                "date_from": date_from,
                "date_to": date_to,
            },
            "sales_count": (
                summary["sales_count"]
            ),
            "sales_total": str(
                sales_total.quantize(
                    Decimal("0.01")
                )
            ),
            "commission_percentage": str(
                percentage
            ),
            "commission_total": str(
                commission_total
            ),
            "commission_plan": str(
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

    filterset_fields = [
        "employee__public_id",
        "employee__business__public_id",
        "status",
        "period_start",
        "period_end",
    ]

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