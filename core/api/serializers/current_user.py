from rest_framework import serializers


class CurrentMembershipSerializer(
    serializers.Serializer
):
    membership_public_id = serializers.UUIDField()
    business_public_id = serializers.UUIDField()
    business_name = serializers.CharField()
    role = serializers.CharField()
    employee_public_id = serializers.UUIDField(
        allow_null=True,
    )


class CurrentUserSerializer(
    serializers.Serializer
):
    public_id = serializers.UUIDField()
    email = serializers.EmailField()
    full_name = serializers.CharField()

    memberships = CurrentMembershipSerializer(
        many=True
    )
