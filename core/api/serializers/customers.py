from rest_framework import serializers

from core.api.serializers.fields import (
    public_id_field,
    related_name_field,
)
from core.api.serializers.status import DefaultActiveStatusMixin
from core.models import Business, Customer, EntityStatus


class CustomerSerializer(
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
        model = Customer
        fields = ("public_id", "business_public_id", "full_name", "phone", "email", "status_public_id", "status_name", "created_at", "updated_at")
        read_only_fields = (
            "public_id",
            "created_at",
            "updated_at",
        )
