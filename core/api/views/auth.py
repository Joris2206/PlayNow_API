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
from rest_framework_simplejwt.views import (
    TokenObtainPairView,
    TokenRefreshView,
    TokenViewBase,
)

from core.api.serializers.auth import (
    ChangePasswordSerializer,
    LoginSerializer,
    LogoutSerializer,
    PasswordResetConfirmSerializer,
    PasswordResetRequestSerializer,
    RefreshSerializer,
    RegisterSerializer,
    _raise_auth_session_error,
)
from core.models import User
from core.services.auth_sessions import AuthSessionError, change_user_password
from core.tokens import AUTH_VERSION_CLAIM


FRONTEND_RESET_URL = settings.FRONTEND_RESET_URL


class LoginView(TokenObtainPairView):
    serializer_class = LoginSerializer
    permission_classes = [AllowAny]
    throttle_classes = [ScopedRateThrottle]
    throttle_scope = "login"


class RefreshView(TokenRefreshView):
    serializer_class = RefreshSerializer


@extend_schema(
    tags=["Auth"],
    request=LogoutSerializer,
    responses={
        204: OpenApiResponse(description="Sesiones revocadas."),
        401: OpenApiResponse(description="Refresh token no válido."),
    },
    summary="Cerrar todas las sesiones del usuario",
    description=(
        "Logout global: invalida access y refresh de todas las pestañas y "
        "dispositivos del usuario. No requiere un access token vigente."
    ),
    auth=[{}],
)
class LogoutView(TokenViewBase):
    serializer_class = LogoutSerializer
    authentication_classes = []
    permission_classes = [AllowAny]

    def post(self, request):
        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        return Response(status=status.HTTP_204_NO_CONTENT)


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
        expected_auth_version = (
            request.auth.get(AUTH_VERSION_CLAIM)
            if request.auth is not None
            else request.user.auth_version
        )
        try:
            change_user_password(
                user_id=request.user.pk,
                expected_auth_version=expected_auth_version,
                current_password=ser.validated_data["current_password"],
                new_password=ser.validated_data["new_password"],
            )
        except AuthSessionError as exc:
            _raise_auth_session_error(exc)
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
