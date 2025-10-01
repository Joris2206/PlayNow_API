from rest_framework import viewsets, mixins
from rest_framework.decorators import action, api_view, permission_classes, throttle_classes
from rest_framework.permissions import IsAuthenticated, AllowAny
from rest_framework.response import Response
from drf_spectacular.utils import extend_schema, extend_schema_view, OpenApiExample
from .filters import StockMovementFilter
from .pagination import StandardResultsSetPagination
from .mixins import SoftDeleteByStatusMixin
from django.db import transaction as db_tx
from collections import defaultdict
from core.utils import log_action
from rest_framework.throttling import ScopedRateThrottle
from drf_spectacular.types import OpenApiTypes
from rest_framework import status as drf_status
from .serializers import HealthSerializer

from .models import (
    User, Business, EntityStatus,
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
class RegisterViewSet(mixins.CreateModelMixin, viewsets.GenericViewSet):
    queryset = User.objects.all()
    serializer_class = RegisterSerializer
    permission_classes = [AllowAny]

# -------- Base mixin para filtrar por usuario --------

class BusinessScopedViewSet(viewsets.ModelViewSet):
    """
    Filtra por negocio/usuario y excluye inactivos por defecto.
    Admin ve todo.
    """
    permission_classes = [IsAuthenticated, IsOwnerOrBusinessOwner]
    throttle_classes = [ScopedRateThrottle]
    EXCLUDED_STATUS_NAMES = ["Eliminado", "Anulado", "Inactivo", "Cancelado"]

    def get_throttles(self):
        # Lecturas rápidas, escrituras más estrictas (usa los rates que pusiste en settings)
        self.throttle_scope = "public_read" if self.action in ("list", "retrieve") else "admin_write"
        return super().get_throttles()

    def _model_has_field(self, model_cls, field_name: str) -> bool:
        return any(f.name == field_name for f in model_cls._meta.get_fields())

    def get_queryset(self):
        qs = super().get_queryset()
        user = self.request.user
        model_cls = self.queryset.model

        # Scoping por rol
        if getattr(user, "role", "") != "admin":
            if self._model_has_field(model_cls, "business"):
                qs = qs.filter(business__user=user)
            elif self._model_has_field(model_cls, "user"):
                qs = qs.filter(user=user)

        # Excluir inactivos por defecto si el modelo tiene 'status'
        include_inactive = self.request.query_params.get("include_inactive")
        want_inactive = str(include_inactive).lower() in ("1", "true", "yes", "y")
        if self._model_has_field(model_cls, "status") and not want_inactive:
            qs = qs.exclude(status__name__in=self.EXCLUDED_STATUS_NAMES)

        return qs

    def perform_create(self, serializer):
        model_cls = serializer.Meta.model
        extra = {}

        # asigna user si aplica
        if self._model_has_field(model_cls, "user") and "user" not in serializer.validated_data:
            extra["user"] = self.request.user

        # asigna status=Activo si aplica y no vino en el payload
        if self._model_has_field(model_cls, "status") and "status" not in serializer.validated_data:
            active = EntityStatus.objects.filter(name__iexact="Activo").first()
            extra["status"] = active

        obj = serializer.save(**extra)
        log_action(self.request.user, "CREATE", model_cls.__name__, obj.pk)

    def perform_update(self, serializer):
        obj = serializer.save()
        # Audit
        log_action(self.request.user, "UPDATE", obj.__class__.__name__, obj.pk)

    def perform_destroy(self, instance):
        super().perform_destroy(instance)
        # Audit
        log_action(self.request.user, "DELETE", instance.__class__.__name__, instance.pk)

# -------- ViewSets --------

@extend_schema_view(
    list=extend_schema(tags=["Businesses"]),
    retrieve=extend_schema(tags=["Businesses"]),
    create=extend_schema(tags=["Businesses"]),
    update=extend_schema(tags=["Businesses"]),
    partial_update=extend_schema(tags=["Businesses"]),
    destroy=extend_schema(tags=["Businesses"]),
)
class BusinessViewSet(BusinessScopedViewSet):
    queryset = Business.objects.select_related("status", "user").all()
    serializer_class = BusinessSerializer
    lookup_field = "public_id"
    lookup_url_kwarg = "public_id"
    pagination_class = StandardResultsSetPagination

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
    create=extend_schema(tags=["Product Categories"]),
    update=extend_schema(tags=["Product Categories"]),
    partial_update=extend_schema(tags=["Product Categories"]),
    destroy=extend_schema(tags=["Product Categories"]),
)
class ProductCategoryViewSet(BusinessScopedViewSet):
    queryset = ProductCategory.objects.all()
    serializer_class = ProductCategorySerializer
    lookup_field = "public_id"
    lookup_url_kwarg = "public_id"
    pagination_class = StandardResultsSetPagination


@extend_schema_view(
    list=extend_schema(tags=["Products"]),
    retrieve=extend_schema(tags=["Products"]),
    create=extend_schema(tags=["Products"]),
    update=extend_schema(tags=["Products"]),
    partial_update=extend_schema(tags=["Products"]),
    destroy=extend_schema(tags=["Products"]),
)
class ProductViewSet(BusinessScopedViewSet):
    queryset = Product.objects.select_related("business", "category", "status").all()
    serializer_class = ProductSerializer
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
    create=extend_schema(tags=["Product Variant Types"]),
    update=extend_schema(tags=["Product Variant Types"]),
    partial_update=extend_schema(tags=["Product Variant Types"]),
    destroy=extend_schema(tags=["Product Variant Types"]),
)
class ProductVariantTypeViewSet(BusinessScopedViewSet):
    queryset = ProductVariantType.objects.select_related("product").all()
    serializer_class = ProductVariantTypeSerializer
    lookup_field = "public_id"
    lookup_url_kwarg = "public_id"
    pagination_class = StandardResultsSetPagination


@extend_schema_view(
    list=extend_schema(tags=["Product Variants"]),
    retrieve=extend_schema(tags=["Product Variants"]),
    create=extend_schema(tags=["Product Variants"]),
    update=extend_schema(tags=["Product Variants"]),
    partial_update=extend_schema(tags=["Product Variants"]),
    destroy=extend_schema(tags=["Product Variants"]),
)
class ProductVariantViewSet(BusinessScopedViewSet):
    queryset = ProductVariant.objects.select_related("variant_type", "variant_type__product").all()
    serializer_class = ProductVariantSerializer
    lookup_field = "public_id"
    lookup_url_kwarg = "public_id"
    pagination_class = StandardResultsSetPagination


@extend_schema_view(
    list=extend_schema(tags=["Employees"]),
    retrieve=extend_schema(tags=["Employees"]),
    create=extend_schema(tags=["Employees"]),
    update=extend_schema(tags=["Employees"]),
    partial_update=extend_schema(tags=["Employees"]),
    destroy=extend_schema(tags=["Employees"]),
)
class EmployeeViewSet(BusinessScopedViewSet):
    queryset = Employee.objects.select_related("business", "status").all()
    serializer_class = EmployeeSerializer
    lookup_field = "public_id"
    lookup_url_kwarg = "public_id"

    filterset_fields = ["status", "business", "business__public_id"]
    search_fields = ["full_name", "phone"]
    ordering_fields = ["full_name", "created_at", "updated_at"]
    ordering = ["-created_at"]

    pagination_class = StandardResultsSetPagination


@extend_schema_view(
    list=extend_schema(tags=["Customers"]),
    retrieve=extend_schema(tags=["Customers"]),
    create=extend_schema(tags=["Customers"]),
    update=extend_schema(tags=["Customers"]),
    partial_update=extend_schema(tags=["Customers"]),
    destroy=extend_schema(tags=["Customers"]),
)
class CustomerViewSet(BusinessScopedViewSet):
    queryset = Customer.objects.select_related("business", "status").all()
    serializer_class = CustomerSerializer
    lookup_field = "public_id"
    lookup_url_kwarg = "public_id"

    filterset_fields = ["status", "business", "business__public_id"]
    search_fields = ["full_name", "email", "phone"]
    ordering_fields = ["full_name", "created_at", "updated_at"]
    ordering = ["-created_at"]

    pagination_class = StandardResultsSetPagination


@extend_schema_view(
    list=extend_schema(tags=["Suppliers"]),
    retrieve=extend_schema(tags=["Suppliers"]),
    create=extend_schema(tags=["Suppliers"]),
    update=extend_schema(tags=["Suppliers"]),
    partial_update=extend_schema(tags=["Suppliers"]),
    destroy=extend_schema(tags=["Suppliers"]),
)
class SupplierViewSet(BusinessScopedViewSet):
    queryset = Supplier.objects.select_related("business", "status").all()
    serializer_class = SupplierSerializer
    lookup_field = "public_id"
    lookup_url_kwarg = "public_id"

    filterset_fields = ["status", "business", "business__public_id"]
    search_fields = ["name", "email", "phone"]
    ordering_fields = ["name", "created_at", "updated_at"]
    ordering = ["-created_at"]

    pagination_class = StandardResultsSetPagination


@extend_schema_view(
    list=extend_schema(tags=["Payment Methods"]),
    retrieve=extend_schema(tags=["Payment Methods"]),
    create=extend_schema(tags=["Payment Methods"]),
    update=extend_schema(tags=["Payment Methods"]),
    partial_update=extend_schema(tags=["Payment Methods"]),
    destroy=extend_schema(tags=["Payment Methods"]),
)
class PaymentMethodViewSet(viewsets.ModelViewSet):
    queryset = PaymentMethod.objects.all()
    serializer_class = PaymentMethodSerializer
    permission_classes = [IsAuthenticated]
    lookup_field = "public_id"
    lookup_url_kwarg = "public_id"
    pagination_class = StandardResultsSetPagination


@extend_schema_view(
    list=extend_schema(tags=["Stock Movements"]),
    retrieve=extend_schema(tags=["Stock Movements"]),
    create=extend_schema(tags=["Stock Movements"]),
    update=extend_schema(tags=["Stock Movements"]),
    partial_update=extend_schema(tags=["Stock Movements"]),
    destroy=extend_schema(tags=["Stock Movements"]),
)
class StockMovementViewSet(BusinessScopedViewSet):
    queryset = (
        StockMovement.objects
        .select_related("product", "product__business", "variant", "variant__variant_type", "transaction")
        .all()
    )
    serializer_class = StockMovementSerializer
    lookup_field = "public_id"
    lookup_url_kwarg = "public_id"

    def get_queryset(self):
        qs = super().get_queryset()
        user = self.request.user
        if getattr(user, "role", "") == "admin":
            return qs
        return qs.filter(product__business__user=user)

    filterset_class = StockMovementFilter
    search_fields = ["product__title", "variant__label", "variant__variant_type__name", "transaction__public_id"]
    ordering_fields = ["created_at", "id", "product__title"]
    ordering = ["-created_at"]

    pagination_class = StandardResultsSetPagination

@extend_schema_view(
    list=extend_schema(tags=["Transactions"]),
    retrieve=extend_schema(tags=["Transactions"]),
    create=extend_schema(tags=["Transactions"]),
    update=extend_schema(tags=["Transactions"]),
    partial_update=extend_schema(tags=["Transactions"]),
    destroy=extend_schema(tags=["Transactions"], summary="Baja lógica + neutralizar inventario"),
)
class TransactionViewSet(SoftDeleteByStatusMixin, BusinessScopedViewSet):
    queryset = (
        Transaction.objects
        .select_related("business", "customer", "supplier", "employee", "payment_method", "status")
        .prefetch_related("details")
        .all()
    )
    serializer_class = TransactionSerializer
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
        if tx_type == "sale":
            return -1
        if tx_type == "purchase":
            return 1
        return None  # expense -> no stock

    @db_tx.atomic
    def perform_create(self, serializer):
        tx = serializer.save()
        sign = self._sign_for_tx(tx.type)
        if sign is not None:
            rows = []
            for d in tx.details.select_related("product", "variant").all():
                rows.append(StockMovement(
                    product=d.product,
                    variant=d.variant,
                    transaction=tx,
                    type="sale" if sign == -1 else "entry",
                    quantity=sign * d.quantity,
                    note=f"Auto base from {tx.type} {tx.public_id}",
                ))
            if rows:
                StockMovement.objects.bulk_create(rows)
        log_action(self.request.user, "CREATE", tx.__class__.__name__, tx.pk)

    @db_tx.atomic
    def perform_update(self, serializer):
        tx = serializer.save()
        sign = self._sign_for_tx(tx.type)
        if sign is not None:
            desired = defaultdict(int)
            for d in tx.details.select_related("product", "variant").all():
                key = (d.product_id, d.variant_id)
                desired[key] += sign * d.quantity

            current = defaultdict(int)
            for pid, vid, qty in tx.stock_movements.all().values_list("product_id", "variant_id", "quantity"):
                current[(pid, vid)] += qty

            to_create = []
            for key, desired_qty in desired.items():
                delta = desired_qty - current.get(key, 0)
                if delta:
                    pid, vid = key
                    to_create.append(StockMovement(
                        product_id=pid,
                        variant_id=vid,
                        transaction=tx,
                        type="adjustment",
                        quantity=delta,
                        note=f"Auto adjust for {tx.public_id}",
                    ))

            for key, cur_qty in list(current.items()):
                if key not in desired and cur_qty:
                    pid, vid = key
                    to_create.append(StockMovement(
                        product_id=pid,
                        variant_id=vid,
                        transaction=tx,
                        type="adjustment",
                        quantity=-cur_qty,
                        note=f"Auto adjust remove for {tx.public_id}",
                    ))

            if to_create:
                StockMovement.objects.bulk_create(to_create)
        log_action(self.request.user, "UPDATE", tx.__class__.__name__, tx.pk)

    # Neutraliza inventario al “borrar” (soft-delete)
    def on_soft_delete(self, tx: Transaction):
        totals = defaultdict(int)
        for pid, vid, qty in tx.stock_movements.all().values_list("product_id", "variant_id", "quantity"):
            totals[(pid, vid)] += qty

        to_create = []
        for (pid, vid), total_qty in totals.items():
            if total_qty:
                to_create.append(StockMovement(
                    product_id=pid,
                    variant_id=vid,
                    transaction=tx,
                    type="adjustment",
                    quantity=-total_qty,
                    note=f"Auto neutralize {tx.public_id}",
                ))
        if to_create:
            StockMovement.objects.bulk_create(to_create)

    # Log de soft-delete
    def destroy(self, request, *args, **kwargs):
        instance = self.get_object()
        response = super().destroy(request, *args, **kwargs)
        if response.status_code == drf_status.HTTP_204_NO_CONTENT:
            log_action(request.user, "DELETE", instance.__class__.__name__, instance.pk)
        return response

    # ------- acciones -------
    @extend_schema(
        tags=["Transactions"],
        summary="Detalles (renglones) de la transacción",
        responses={200: TransactionDetailSerializer(many=True)}
    )
    @action(detail=True, methods=["get"])
    def details(self, request, public_id=None):
        tx = self.get_object()
        qs = tx.details.all()
        page = self.paginate_queryset(qs)
        if page is not None:
            ser = TransactionDetailSerializer(page, many=True)
            return self.get_paginated_response(ser.data)
        return Response(TransactionDetailSerializer(qs, many=True).data)

    @extend_schema(
        tags=["Transactions"],
        summary="Movimientos de stock originados por la transacción",
        responses={200: StockMovementSerializer(many=True)}
    )
    @action(detail=True, methods=["get"], url_path="stock-movements")
    def stock_movements(self, request, public_id=None):
        tx = self.get_object()
        qs = tx.stock_movements.select_related("product", "variant").all()
        page = self.paginate_queryset(qs)
        if page is not None:
            ser = StockMovementSerializer(page, many=True)
            return self.get_paginated_response(ser.data)
        return Response(StockMovementSerializer(qs, many=True).data)

    @extend_schema(
        tags=["Transactions"],
        summary="Registrar devolución (return) parcial/total",
        description=(
            "Para `sale`: devuelve stock (ajuste positivo). "
            "Para `purchase`: retorna al proveedor (ajuste negativo)."
        ),
        request={"application/json": OpenApiTypes.OBJECT},
        responses={201: OpenApiTypes.OBJECT},
    )
    @action(detail=True, methods=["post"], url_path="return")
    def register_return(self, request, public_id=None):
        """
        Body:
        {
          "items": [{"detail_public_id": "<uuid>", "quantity": 2}],
          "note": "Opcional"
        }
        """
        tx = self.get_object()
        data = request.data or {}
        items = data.get("items") or []
        note = data.get("note") or "Return"

        if tx.type not in ("sale", "purchase"):
            return Response({"detail": "Solo aplica a sale/purchase."}, status=drf_status.HTTP_400_BAD_REQUEST)
        if not items:
            return Response({"detail": "Debes enviar items a devolver."}, status=drf_status.HTTP_400_BAD_REQUEST)

        sign = 1 if tx.type == "sale" else -1

        # Mapeo seguro por texto (evita choque str vs UUID)
        details_qs = tx.details.select_related("product", "variant").all()
        details_map = {str(d.public_id): d for d in details_qs}

        to_create = []
        for it in items:
            dpid = it.get("detail_public_id")
            qty = it.get("quantity")
            try:
                qty = int(qty)
            except (TypeError, ValueError):
                return Response({"detail": f"Cantidad inválida: {it}"}, status=drf_status.HTTP_400_BAD_REQUEST)

            if not dpid or qty <= 0:
                return Response({"detail": f"Ítem inválido: {it}"}, status=drf_status.HTTP_400_BAD_REQUEST)

            d = details_map.get(str(dpid))
            if not d:
                return Response({"detail": f"detail_public_id no pertenece a la transacción: {dpid}"},
                                status=drf_status.HTTP_400_BAD_REQUEST)

            to_create.append(StockMovement(
                product=d.product,
                variant=d.variant,
                transaction=tx,
                type="adjustment",
                quantity=sign * qty,
                note=f"{note} for {tx.public_id} (detail {d.public_id})",
            ))

        if to_create:
            StockMovement.objects.bulk_create(to_create)
            log_action(request.user, "RETURN", tx.__class__.__name__, tx.pk)

        return Response({"ok": True, "count": len(to_create)}, status=drf_status.HTTP_201_CREATED)  
    
@extend_schema_view(
    list=extend_schema(tags=["Debts"]),
    retrieve=extend_schema(tags=["Debts"]),
    create=extend_schema(tags=["Debts"]),
    update=extend_schema(tags=["Debts"]),
    partial_update=extend_schema(tags=["Debts"]),
    destroy=extend_schema(tags=["Debts"]),
)
class DebtViewSet(BusinessScopedViewSet):
    queryset = Debt.objects.select_related("transaction", "transaction__business").all()
    serializer_class = DebtSerializer
    lookup_field = "public_id"
    lookup_url_kwarg = "public_id"
    pagination_class = StandardResultsSetPagination


@extend_schema_view(
    list=extend_schema(tags=["Debt Payments"]),
    retrieve=extend_schema(tags=["Debt Payments"]),
    create=extend_schema(tags=["Debt Payments"]),
    update=extend_schema(tags=["Debt Payments"]),
    partial_update=extend_schema(tags=["Debt Payments"]),
    destroy=extend_schema(tags=["Debt Payments"]),
)
class DebtPaymentViewSet(BusinessScopedViewSet):
    queryset = DebtPayment.objects.select_related("debt", "debt__transaction").all()
    serializer_class = DebtPaymentSerializer
    lookup_field = "public_id"
    lookup_url_kwarg = "public_id"
    pagination_class = StandardResultsSetPagination


@extend_schema_view(
    list=extend_schema(tags=["Notifications"]),
    retrieve=extend_schema(tags=["Notifications"]),
    create=extend_schema(tags=["Notifications"]),
    update=extend_schema(tags=["Notifications"]),
    partial_update=extend_schema(tags=["Notifications"]),
    destroy=extend_schema(tags=["Notifications"]),
)
class NotificationViewSet(BusinessScopedViewSet):
    queryset = Notification.objects.select_related("user", "business", "transaction").all()
    serializer_class = NotificationSerializer
    lookup_field = "public_id"
    lookup_url_kwarg = "public_id"
    pagination_class = StandardResultsSetPagination


@extend_schema_view(
    list=extend_schema(tags=["Reminders"]),
    retrieve=extend_schema(tags=["Reminders"]),
    create=extend_schema(tags=["Reminders"]),
    update=extend_schema(tags=["Reminders"]),
    partial_update=extend_schema(tags=["Reminders"]),
    destroy=extend_schema(tags=["Reminders"]),
)
class ReminderViewSet(BusinessScopedViewSet):
    queryset = Reminder.objects.select_related("user", "business", "transaction").all()
    serializer_class = ReminderSerializer
    lookup_field = "public_id"
    lookup_url_kwarg = "public_id"
    pagination_class = StandardResultsSetPagination


@extend_schema_view(
    list=extend_schema(tags=["Budgets"]),
    retrieve=extend_schema(tags=["Budgets"]),
    create=extend_schema(tags=["Budgets"]),
    update=extend_schema(tags=["Budgets"]),
    partial_update=extend_schema(tags=["Budgets"]),
    destroy=extend_schema(tags=["Budgets"]),
)
class BudgetViewSet(BusinessScopedViewSet):
    queryset = Budget.objects.select_related("user", "business").all()
    serializer_class = BudgetSerializer
    lookup_field = "public_id"
    lookup_url_kwarg = "public_id"
    pagination_class = StandardResultsSetPagination


@extend_schema_view(
    list=extend_schema(tags=["Goals"]),
    retrieve=extend_schema(tags=["Goals"]),
    create=extend_schema(tags=["Goals"]),
    update=extend_schema(tags=["Goals"]),
    partial_update=extend_schema(tags=["Goals"]),
    destroy=extend_schema(tags=["Goals"]),
)
class GoalViewSet(BusinessScopedViewSet):
    queryset = Goal.objects.select_related("user", "business").all()
    serializer_class = GoalSerializer
    lookup_field = "public_id"
    lookup_url_kwarg = "public_id"
    pagination_class = StandardResultsSetPagination


@extend_schema_view(
    list=extend_schema(tags=["Goal Progress"]),
    retrieve=extend_schema(tags=["Goal Progress"]),
    create=extend_schema(tags=["Goal Progress"]),
    update=extend_schema(tags=["Goal Progress"]),
    partial_update=extend_schema(tags=["Goal Progress"]),
    destroy=extend_schema(tags=["Goal Progress"]),
)
class GoalProgressViewSet(BusinessScopedViewSet):
    queryset = GoalProgress.objects.select_related("goal", "goal__business").all()
    serializer_class = GoalProgressSerializer
    lookup_field = "public_id"
    lookup_url_kwarg = "public_id"
    pagination_class = StandardResultsSetPagination