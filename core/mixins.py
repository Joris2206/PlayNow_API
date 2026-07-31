# core/mixins.py

from django.db import transaction as db_tx
from rest_framework import status as drf_status
from rest_framework.response import Response

from .models import EntityStatus


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