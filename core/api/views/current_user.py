from drf_spectacular.utils import extend_schema
from rest_framework import status
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from core.api.serializers.current_user import CurrentUserSerializer
from core.models import BusinessMembership
from core.business_policy import ACTIVE_BUSINESS_STATUS
from core.services.business_lifecycle import (
    can_access_business_administration,
)


class CurrentUserView(APIView):
    permission_classes = [
        IsAuthenticated,
    ]

    @extend_schema(
        tags=["Authentication"],
        summary="Obtener usuario autenticado",
        responses={
            200: CurrentUserSerializer,
        },
    )
    def get(
        self,
        request,
    ):
        user = request.user

        memberships = (
            BusinessMembership.objects
            .select_related(
                "business",
                "employee",
            )
            .filter(
                user=user,
                is_active=True,
                business__status__name__iexact=ACTIVE_BUSINESS_STATUS,
            )
            .order_by(
                "business__business_name",
            )
        )

        data = {
            "public_id": user.public_id,
            "email": user.email,
            "full_name": user.full_name,
            "is_superuser": user.is_superuser,
            "can_access_business_administration": (
                can_access_business_administration(actor_id=user.pk)
            ),

            "memberships": [
                {
                    "membership_public_id": (
                        membership.public_id
                    ),
                    "business_public_id": (
                        membership.business.public_id
                    ),
                    "business_name": (
                        membership.business.business_name
                    ),
                    "role": membership.role,
                    "employee_public_id": (
                        membership.employee.public_id
                        if membership.employee_id is not None
                        else None
                    ),
                }
                for membership in memberships
            ],
        }

        serializer = CurrentUserSerializer(
            data
        )

        return Response(
            serializer.data,
            status=status.HTTP_200_OK,
        )
