from rest_framework import serializers

from core.models import User
from core.serializers import StrictFieldsSerializerMixin


class PlatformUserSerializer(serializers.ModelSerializer):
    class Meta:
        model = User
        fields = (
            "public_id",
            "email",
            "full_name",
            "is_active",
            "is_superuser",
            "created_at",
            "updated_at",
        )
        read_only_fields = fields


class PlatformUserIdentityUpdateSerializer(
    StrictFieldsSerializerMixin,
    serializers.Serializer,
):
    email = serializers.EmailField(max_length=254, required=False)
    full_name = serializers.CharField(
        max_length=255,
        required=False,
        allow_blank=False,
        trim_whitespace=True,
    )

    def validate_email(self, value):
        return User.objects.normalize_email(value).strip().lower()

    def validate_full_name(self, value):
        return value.strip()

    def validate(self, attrs):
        if not attrs:
            raise serializers.ValidationError(
                "Debe proporcionar email o full_name."
            )
        return attrs
