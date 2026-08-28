from rest_framework import serializers

from core.api.serializers.fields import (
    public_id_field,
    related_name_field,
)
from core.api.serializers.status import DefaultActiveStatusMixin
from core.models import Business, Employee, EntityStatus


class EmployeeSerializer(
    DefaultActiveStatusMixin,
    serializers.ModelSerializer,
):
    business_public_id = public_id_field(
        Business,
        source="business",
    )
    status_public_id = public_id_field(
        EntityStatus,
        source="status",
        required=False,
    )
    status_name = related_name_field("status.name")
    class Meta:
        model = Employee
        fields = ("public_id", "business_public_id", "full_name", "phone", "email", "position", "status_public_id", "status_name", "created_at", "updated_at")
        read_only_fields = (
            "public_id",
            "created_at",
            "updated_at",
        )


class EmployeeSelectionSerializer(serializers.ModelSerializer):
    """Minimal employee identity exposed to operational sales roles."""

    class Meta:
        model = Employee
        fields = (
            "public_id",
            "full_name",
            "position",
        )
        read_only_fields = fields
