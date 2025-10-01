# core/mixins.py
from rest_framework import status as drf_status
from rest_framework.response import Response
from .models import EntityStatus

class SoftDeleteByStatusMixin:
    """
    Reemplaza DELETE físico por cambio de status (soft-delete).
    Cambia el status del objeto al primer EntityStatus cuyo name coincida con alguno
    de SOFT_DELETE_STATUS_CANDIDATES.
    """
    SOFT_DELETE_STATUS_CANDIDATES = ["Eliminado", "Anulado", "Cancelado", "Void", "Deleted"]

    def _get_soft_delete_status(self):
        return EntityStatus.objects.filter(name__in=self.SOFT_DELETE_STATUS_CANDIDATES).first()

    def on_soft_delete(self, instance):
        """Hook para efectos secundarios (override opcional en la vista)."""
        return None

    def destroy(self, request, *args, **kwargs):
        instance = self.get_object()
        status_obj = self._get_soft_delete_status()
        if not status_obj:
            return Response(
                {"detail": "No se encontró un EntityStatus válido para baja lógica (p. ej. 'Eliminado')."},
                status=drf_status.HTTP_409_CONFLICT,
            )
        instance.status = status_obj
        # si el modelo tiene updated_at, auto_now=True lo actualizará, si no, igual guardamos
        try:
            instance.save(update_fields=["status", "updated_at"])
        except Exception:
            instance.save(update_fields=["status"])
        self.on_soft_delete(instance)
        return Response(status=drf_status.HTTP_204_NO_CONTENT)
