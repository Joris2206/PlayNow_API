from django.core.exceptions import FieldDoesNotExist, ObjectDoesNotExist
from rest_framework import viewsets, mixins, status
from rest_framework.views import APIView
from rest_framework.decorators import action, api_view, permission_classes, throttle_classes
from rest_framework.permissions import IsAuthenticated, AllowAny
from rest_framework.response import Response
from rest_framework.exceptions import ValidationError, PermissionDenied
from drf_spectacular.utils import extend_schema, extend_schema_view, OpenApiExample, OpenApiResponse
from .filters import StockMovementFilter
from .pagination import StandardResultsSetPagination
from .mixins import SoftDeleteByStatusMixin
from django.db import transaction as db_tx
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
# Los identificadores numéricos representan las PK internas que actualmente
# reciben los serializers relacionales. Sustitúyelos por registros existentes
# al ejecutar las solicitudes desde Swagger.
# ---------------------------------------------------------------------------

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
        "business": 1,
        "name": "Calzado",
    },
    request_only=True,
)

PRODUCT_CREATE_EXAMPLE = OpenApiExample(
    "Crear producto",
    value={
        "business": 1,
        "category": 2,
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
        "product": 5,
        "name": "Talla",
    },
    request_only=True,
)

VARIANT_CREATE_EXAMPLE = OpenApiExample(
    "Crear variante",
    value={
        "variant_type": 3,
        "label": "Talla 38",
        "additional_price": "0.00",
        "stock": 8,
    },
    request_only=True,
)

EMPLOYEE_CREATE_EXAMPLE = OpenApiExample(
    "Registrar empleado sin acceso al sistema",
    value={
        "business": 1,
        "full_name": "Ana Martínez",
        "phone": "7777-4321",
        "position": "Dependiente",
    },
    request_only=True,
)

CUSTOMER_CREATE_EXAMPLE = OpenApiExample(
    "Registrar cliente",
    value={
        "business": 1,
        "full_name": "José Ramírez",
        "phone": "8666-1122",
        "email": "jose.ramirez@example.com",
    },
    request_only=True,
)

SUPPLIER_CREATE_EXAMPLE = OpenApiExample(
    "Registrar proveedor",
    value={
        "business": 1,
        "name": "Distribuidora Central",
        "phone": "2255-7788",
        "email": "ventas@distribuidoracentral.example.com",
    },
    request_only=True,
)

PAYMENT_METHOD_CREATE_EXAMPLE = OpenApiExample(
    "Crear método de pago",
    value={
        "business": 1,
        "name": "Transferencia bancaria",
    },
    request_only=True,
)

TRANSACTION_SALE_EXAMPLE = OpenApiExample(
    "Venta pagada",
    value={
        "business": 1,
        "customer": 4,
        "supplier": None,
        "payment_method": 2,
        "type": "sale",
        "discount_percent": "5.00",
        "concept": "Venta en mostrador",
        "payment_status": "paid",
        "invoice_number": "000145",
        "invoice_series": "A",
        "invoice_file_url": "",
        "details": [
            {
                "product": 5,
                "variant": 8,
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
        "business": 1,
        "customer": None,
        "supplier": 3,
        "payment_method": 1,
        "type": "purchase",
        "discount_percent": "0.00",
        "concept": "Reposición semanal de inventario",
        "payment_status": "paid",
        "invoice_number": "FAC-9087",
        "invoice_series": "PROV",
        "invoice_file_url": "",
        "details": [
            {
                "product": 5,
                "variant": 8,
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
        "business": 1,
        "customer": None,
        "supplier": None,
        "payment_method": 1,
        "type": "expense",
        "discount_percent": "0.00",
        "concept": "Pago mensual de energía eléctrica",
        "expense_amount": "2350.00",
        "payment_status": "paid",
        "invoice_number": "EN-0726",
        "invoice_series": "SERV",
        "invoice_file_url": "",
        "details": [],
    },
    request_only=True,
)

TRANSACTION_UPDATE_EXAMPLE = OpenApiExample(
    "Actualizar datos permitidos",
    value={
        "customer": 4,
        "payment_method": 2,
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
        "transaction": 12,
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
        "debt": 6,
        "amount": "500.00",
        "payment_date": "2026-07-30",
        "payment_method": 2,
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
        "business": 1,
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
        "business": 1,
        "transaction": None,
    },
    request_only=True,
)

BUDGET_CREATE_EXAMPLE = OpenApiExample(
    "Crear presupuesto mensual",
    value={
        "business": 1,
        "status": 1,
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
        "business": 1,
        "name": "Meta de ventas de agosto",
        "description": "Alcanzar cincuenta mil córdobas en ventas.",
        "target_amount": "50000.00",
        "current_amount": "0.00",
        "start_date": "2026-08-01",
        "end_date": "2026-08-31",
        "is_completed": False,
    },
    request_only=True,
)

GOAL_PROGRESS_CREATE_EXAMPLE = OpenApiExample(
    "Registrar avance de meta",
    value={
        "goal": 2,
        "amount": "3500.00",
        "transaction": 12,
        "status": 1,
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
    Budget, Goal, GoalProgress,
)
from .serializers import (
    UserSerializer, RegisterSerializer,
    BusinessSerializer, EntityStatusSerializer,
    ProductCategorySerializer, ProductSerializer, ProductVariantTypeSerializer, ProductVariantSerializer,
    EmployeeSerializer, CustomerSerializer, SupplierSerializer, PaymentMethodSerializer,
    TransactionSerializer, TransactionDetailSerializer, StockMovementSerializer,
    DebtSerializer, DebtPaymentSerializer, NotificationSerializer, ReminderSerializer,
    BudgetSerializer, GoalSerializer, GoalProgressSerializer,
)
from .permissions import IsOwnerOrBusinessOwner

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
            "Crea una venta, compra o gasto. El usuario y el empleado "
            "se vinculan automáticamente desde la sesión y la membresía."
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
    filterset_fields = [
        "type", "business", "business__public_id", "status",
        "customer", "supplier", "employee", "payment_method",
    ]
    search_fields = ["public_id", "invoice_number", "invoice_series", "concept",
                     "customer__full_name", "supplier__name"]
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

        membership = (
            self._get_request_membership(
                business
            )
        )

        employee = (
            membership.employee
            if membership is not None
            else None
        )

        tx = serializer.save(
            created_by=self.request.user,
            employee=employee,
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
        business = (
            serializer.validated_data.get(
                "business",
                serializer.instance.business,
            )
        )

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

        serializer.validated_data.pop(
            "employee",
            None,
        )

        tx = serializer.save(
            updated_by=self.request.user,
        )
        
        sign = self._sign_for_tx(tx.type)  # sale:-1, purchase:+1, expense:None

        # Si AHORA no debería afectar inventario (expense), neutraliza todo lo previo.
        if sign is None:
            current_totals = defaultdict(int)
            for pid, vid, qty in tx.stock_movements.all().values_list("product_id", "variant_id", "quantity"):
                current_totals[(pid, vid)] += qty

            to_movs = []
            for (pid, vid), cur_qty in current_totals.items():
                if cur_qty:
                    # revertir existencias con lock
                    if vid:
                        v = ProductVariant.objects.select_for_update().get(pk=vid)
                        if v.stock - cur_qty < 0:
                            raise ValidationError({"details": "Stock negativo no permitido"})
                        v.stock -= cur_qty
                        v.save(update_fields=["stock"])
                    else:
                        p = Product.objects.select_for_update().get(pk=pid)
                        if p.stock - cur_qty < 0:
                            raise ValidationError({"details": "Stock negativo no permitido"})
                        p.stock -= cur_qty
                        p.save(update_fields=["stock"])

                    to_movs.append(StockMovement(
                        product_id=pid,
                        variant_id=vid,
                        transaction=tx,
                        created_by=self.request.user,
                        type="adjustment",
                        quantity=-cur_qty,
                        note=f"Auto neutralize {tx.public_id}",
                    ))

            if to_movs:
                StockMovement.objects.bulk_create(to_movs)

            log_action(self.request.user, "UPDATE", tx.__class__.__name__, tx.pk)
            return

        # ------- sale / purchase: calcular deseado vs actual y ajustar -------
        # Deseado = suma del efecto que DEBERÍA tener la transacción con sus detalles actuales
        desired = defaultdict(int)
        for d in tx.details.select_related("product", "variant"):
            key = (d.product_id, d.variant_id)
            desired[key] += sign * d.quantity

        # Actual = suma del efecto que YA tuvo (todos los movimientos ligados a esta TX)
        current = defaultdict(int)
        for pid, vid, qty in tx.stock_movements.all().values_list("product_id", "variant_id", "quantity"):
            current[(pid, vid)] += qty

        to_movs = []

        # 1) Ajustar diferencias (delta) para cada clave presente en "desired"
        for (pid, vid), desired_qty in desired.items():
            delta = desired_qty - current.get((pid, vid), 0)
            if delta:
                # actualizar stock acorde al delta con lock
                if vid:
                    v = ProductVariant.objects.select_for_update().get(pk=vid)
                    # delta positivo => entra stock; delta negativo => sale stock
                    if v.stock + delta < 0:
                        raise ValidationError({"details": "Stock negativo no permitido"})
                    v.stock += delta
                    v.save(update_fields=["stock"])
                else:
                    p = Product.objects.select_for_update().get(pk=pid)
                    if p.stock + delta < 0:
                        raise ValidationError({"details": "Stock negativo no permitido"})
                    p.stock += delta
                    p.save(update_fields=["stock"])

                to_movs.append(StockMovement(
                    product_id=pid,
                    variant_id=vid,
                    transaction=tx,
                    created_by=self.request.user,
                    type="adjustment",
                    quantity=delta,
                    note=f"Auto adjust for {tx.public_id}",
                ))

        # 2) Si había movimientos que ya no deberían existir (claves que están en current pero no en desired), neutralízalos
        for (pid, vid), cur_qty in list(current.items()):
            if (pid, vid) not in desired and cur_qty:
                # revertir su efecto en stock con lock
                if vid:
                    v = ProductVariant.objects.select_for_update().get(pk=vid)
                    if v.stock - cur_qty < 0:
                        raise ValidationError({"details": "Stock negativo no permitido"})
                    v.stock -= cur_qty
                    v.save(update_fields=["stock"])
                else:
                    p = Product.objects.select_for_update().get(pk=pid)
                    if p.stock - cur_qty < 0:
                        raise ValidationError({"details": "Stock negativo no permitido"})
                    p.stock -= cur_qty
                    p.save(update_fields=["stock"])

                to_movs.append(StockMovement(
                    product_id=pid,
                    variant_id=vid,
                    transaction=tx,
                    created_by=self.request.user,
                    type="adjustment",
                    quantity=-cur_qty,
                    note=f"Auto adjust remove for {tx.public_id}",
                ))

        if to_movs:
            StockMovement.objects.bulk_create(to_movs)

        log_action(self.request.user, "UPDATE", tx.__class__.__name__, tx.pk)

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
    list=extend_schema(tags=["Debts"]),
    retrieve=extend_schema(tags=["Debts"]),
    create=extend_schema(
        tags=["Debts"],
        description="Registra una deuda asociada a una transacción del mismo negocio.",
        examples=[DEBT_CREATE_EXAMPLE],
    ),
    update=extend_schema(tags=["Debts"]),
    partial_update=extend_schema(tags=["Debts"]),
    destroy=extend_schema(tags=["Debts"]),
)
class DebtViewSet(SoftDeleteByStatusMixin, BusinessScopedViewSet):
    queryset = Debt.objects.select_related("transaction", "transaction__business").all()
    serializer_class = DebtSerializer

    business_lookup = "transaction__business"

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

    update_allowed_roles = create_allowed_roles

    destroy_allowed_roles = [
        BusinessMembership.ROLE_OWNER,
        BusinessMembership.ROLE_ADMIN,
    ]

    lookup_field = "public_id"
    lookup_url_kwarg = "public_id"
    pagination_class = StandardResultsSetPagination


@extend_schema_view(
    list=extend_schema(tags=["Debt Payments"]),
    retrieve=extend_schema(tags=["Debt Payments"]),
    create=extend_schema(
        tags=["Debt Payments"],
        description="Registra un abono y actualiza automáticamente el saldo de la deuda.",
        examples=[DEBT_PAYMENT_CREATE_EXAMPLE],
    ),
    update=extend_schema(tags=["Debt Payments"]),
    partial_update=extend_schema(tags=["Debt Payments"]),
    destroy=extend_schema(tags=["Debt Payments"]),
)
class DebtPaymentViewSet(SoftDeleteByStatusMixin, BusinessScopedViewSet):
    queryset = DebtPayment.objects.select_related("debt", "debt__transaction").all()
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
    update_allowed_roles = create_allowed_roles
    destroy_allowed_roles = [
        BusinessMembership.ROLE_OWNER,
        BusinessMembership.ROLE_ADMIN,
    ]

    lookup_field = "public_id"
    lookup_url_kwarg = "public_id"
    pagination_class = StandardResultsSetPagination


@extend_schema_view(
    list=extend_schema(tags=["Notifications"]),
    retrieve=extend_schema(tags=["Notifications"]),
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