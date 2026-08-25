from drf_spectacular.utils import extend_schema
from rest_framework import status
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from core.api.serializers.current_user import CurrentUserSerializer
from core.models import BusinessMembership


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
            )
            .order_by(
                "business__business_name",
            )
        )

        data = {
            "public_id": user.public_id,
            "email": user.email,
            "full_name": user.full_name,

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
