from django.conf import settings
from drf_spectacular.utils import (
    OpenApiExample,
    OpenApiResponse,
    extend_schema,
    extend_schema_view,
)
from rest_framework import mixins, status, viewsets
from rest_framework.decorators import action
from rest_framework.permissions import AllowAny, IsAuthenticated
from rest_framework.response import Response
from rest_framework.throttling import ScopedRateThrottle
from rest_framework.views import APIView

from core.api.serializers.auth import (
    ChangePasswordSerializer,
    PasswordResetConfirmSerializer,
    PasswordResetRequestSerializer,
    RegisterSerializer,
)
from core.models import User


FRONTEND_RESET_URL = settings.FRONTEND_RESET_URL


REGISTER_EXAMPLE = OpenApiExample(
    "Registro de propietario",
    value={
        "email": "maria.lopez@example.com",
        "full_name": "María López",
        "password": "ClaveSegura2026!",
    },
    request_only=True,
)


@extend_schema_view(
    create=extend_schema(
        tags=["Auth"],
        summary="Registrar propietario",
        description=(
            "Crea una cuenta de propietario. El negocio se registra "
            "posteriormente desde el módulo Businesses."
        ),
        examples=[REGISTER_EXAMPLE],
    ),
)
class RegisterViewSet(mixins.CreateModelMixin, viewsets.GenericViewSet):
    queryset = User.objects.all()
    serializer_class = RegisterSerializer
    permission_classes = [AllowAny]
    throttle_classes = [ScopedRateThrottle]
    throttle_scope = "auth_register"


@extend_schema(tags=["Users"])
class UserViewSet(viewsets.GenericViewSet):
    permission_classes = [IsAuthenticated]
    throttle_classes = [ScopedRateThrottle]
    throttle_scope = "admin_write"

    queryset = User.objects.none()

    @extend_schema(
        request=ChangePasswordSerializer,
        responses={204: OpenApiResponse(description="Contraseña actualizada exitosamente.")},
        tags=["Users"]
    )
    @action(methods=["post"], detail=False, url_path="change-password")
    def change_password(self, request):
        ser = ChangePasswordSerializer(data=request.data, context={"request": request})
        ser.is_valid(raise_exception=True)
        user = request.user
        user.set_password(ser.validated_data["new_password"])
        user.save(update_fields=["password"])
        return Response(status=status.HTTP_204_NO_CONTENT)


@extend_schema(
    tags=["Auth"],
    request=PasswordResetRequestSerializer,
    responses={200: None},
    summary="Solicitar restablecimiento de contraseña"
)
class PasswordResetRequestView(APIView):
    permission_classes = [AllowAny]
    throttle_classes = [ScopedRateThrottle]
    throttle_scope = "auth_reset_request"

    def post(self, request):
        ser = PasswordResetRequestSerializer(data=request.data)
        ser.is_valid(raise_exception=True)
        ser.save(frontend_reset_url=FRONTEND_RESET_URL)
        return Response(status=status.HTTP_200_OK)


@extend_schema(
    tags=["Auth"],
    request=PasswordResetConfirmSerializer,
    responses={204: None},
    summary="Confirmar restablecimiento de contraseña"
)
class PasswordResetConfirmView(APIView):
    permission_classes = [AllowAny]
    throttle_classes = [ScopedRateThrottle]
    throttle_scope = "auth_reset_confirm"

    def post(self, request):
        ser = PasswordResetConfirmSerializer(data=request.data)
        ser.is_valid(raise_exception=True)
        ser.save()
        return Response(status=status.HTTP_204_NO_CONTENT)
