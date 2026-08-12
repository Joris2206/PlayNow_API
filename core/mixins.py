# core/mixins.py

from django.db import transaction as db_tx
from rest_framework import status as drf_status
from rest_framework.response import Response
from drf_spectacular.utils import (
    OpenApiParameter,
    OpenApiTypes,
    extend_schema,
)

from .models import EntityStatus

from django.shortcuts import get_object_or_404
from rest_framework.exceptions import (
    ValidationError,
    PermissionDenied,
)

from core.models import (
    Business,
    BusinessMembership,
)

class SoftDeleteByStatusMixin:
    """
    Reemplaza DELETE físico por cambio de estado.

    El hook y el cambio de estado se ejecutan dentro de la misma
    transacción para evitar dejar datos parcialmente actualizados.
    """

    SOFT_DELETE_STATUS_CANDIDATES = [
        "Eliminado",
        "Anulado",
        "Cancelado",
        "Void",
        "Deleted",
    ]

    def _get_soft_delete_status(self):
        return (
            EntityStatus.objects
            .filter(
                name__in=(
                    self.SOFT_DELETE_STATUS_CANDIDATES
                )
            )
            .first()
        )

    def on_soft_delete(self, instance):
        return None

    @db_tx.atomic
    def destroy(
        self,
        request,
        *args,
        **kwargs,
    ):
        instance = self.get_object()

        status_obj = (
            self._get_soft_delete_status()
        )

        if status_obj is None:
            return Response(
                {
                    "detail": (
                        "No se encontró un estado válido "
                        "para realizar la baja lógica."
                    )
                },
                status=(
                    drf_status.HTTP_409_CONFLICT
                ),
            )

        if instance.status_id == status_obj.id:
            return Response(
                {
                    "detail": (
                        "El recurso ya se encuentra "
                        "anulado o eliminado."
                    )
                },
                status=(
                    drf_status.HTTP_409_CONFLICT
                ),
            )

        validator = getattr(
            self,
            "validate_destroy_access",
            None,
        )

        if validator:
            validator(instance)

        # Primero ejecuta los efectos secundarios.
        # Si fallan, no se cambia el estado.
        self.on_soft_delete(instance)

        instance.status = status_obj

        update_fields = ["status"]

        if hasattr(instance, "updated_at"):
            update_fields.append("updated_at")

        instance.save(
            update_fields=update_fields
        )

        return Response(
            status=drf_status.HTTP_204_NO_CONTENT
        )

class RequireBusinessPublicIdListMixin:
    require_business_public_id_for_list = True

    business_query_param = (
        "business_public_id"
    )

    business_lookup = None
    list_allowed_roles = None

    @extend_schema(
        parameters=[
            OpenApiParameter(
                name="business_public_id",
                type=OpenApiTypes.UUID,
                location=OpenApiParameter.QUERY,
                required=True,
                description=(
                    "Public ID del negocio que delimita el listado."
                ),
            ),
        ],
    )
    def list(self, request, *args, **kwargs):
        return super().list(request, *args, **kwargs)

    def _get_list_allowed_roles(self):
        roles = getattr(
            self,
            "list_allowed_roles",
            None,
        )

        if roles is not None:
            return roles

        return getattr(
            self,
            "read_allowed_roles",
            None,
        )

    def _get_required_list_business(self):
        business_public_id = (
            self.request.query_params.get(
                self.business_query_param
            )
        )

        if not business_public_id:
            raise ValidationError({
                self.business_query_param: (
                    "Este parámetro es obligatorio."
                )
            })

        return get_object_or_404(
            Business,
            public_id=business_public_id,
        )

    def _validate_list_business_access(
        self,
        business,
    ):
        user = self.request.user

        if user.is_superuser:
            return

        filters = {
            "user": user,
            "business": business,
            "is_active": True,
        }

        allowed_roles = (
            self._get_list_allowed_roles()
        )

        if allowed_roles is not None:
            filters["role__in"] = (
                allowed_roles
            )

        has_access = (
            BusinessMembership.objects
            .filter(**filters)
            .exists()
        )

        if not has_access:
            raise PermissionDenied(
                "No tienes permiso para consultar "
                "este negocio."
            )

    def filter_queryset(
        self,
        queryset,
    ):
        if (
            getattr(self, "action", None)
            == "list"
            and self.require_business_public_id_for_list
            and self.business_lookup
        ):
            business = (
                self._get_required_list_business()
            )

            self._validate_list_business_access(
                business
            )

            queryset = queryset.filter(
                **{
                    (
                        f"{self.business_lookup}"
                        "__public_id"
                    ): business.public_id,
                }
            )

        return super().filter_queryset(
            queryset
        )
