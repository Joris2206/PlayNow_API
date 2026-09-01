from core.models import Business, BusinessMembership


def active_membership_business_ids(context):
    request = context.get("request")
    user = getattr(request, "user", None)

    if (
        user is not None
        and user.is_authenticated
        and user.is_superuser
    ):
        return None

    if user is None or not user.is_authenticated:
        return (
            Business.objects.none()
            .values_list("pk", flat=True)
        )

    return (
        BusinessMembership.objects
        .filter(
            user=user,
            is_active=True,
        )
        .values_list("business_id", flat=True)
    )
