from rest_framework import serializers

from core.models import EntityStatus


def get_active_status():
    active = EntityStatus.objects.filter(name__iexact="Activo").first()
    if active is None:
        raise serializers.ValidationError({
            "status_public_id": (
                "No existe el estado inicial 'Activo'. "
                "Ejecuta el comando seed_statuses."
            )
        })
    return active


class DefaultActiveStatusMixin:
    """Asigna el estado Activo cuando el cliente no envía status."""

    def create(self, validated_data):
        validated_data.setdefault("status", get_active_status())
        return super().create(validated_data)
