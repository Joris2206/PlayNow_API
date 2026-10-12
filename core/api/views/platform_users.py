from drf_spectacular.utils import OpenApiResponse, extend_schema
from rest_framework import generics, status
from rest_framework.exceptions import APIException, NotFound, PermissionDenied
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.throttling import ScopedRateThrottle

from core.api.serializers.platform_users import (
    PlatformUserIdentityUpdateSerializer,
    PlatformUserSerializer,
)
from core.filters import (
    ConfiguredSearchFilter,
    PlatformUserFilter,
    StableOrderingFilter,
)
from core.models import User
from core.pagination import StandardResultsSetPagination
from core.permissions import IsActivePlatformAdmin
from core.services.platform_users import (
    PlatformUserIdentityError,
    locked_platform_admin_read,
    update_platform_user_identity,
)
from django_filters.rest_framework import DjangoFilterBackend


class PlatformUserConflict(APIException):
    status_code = status.HTTP_409_CONFLICT
    default_code = "platform_user_conflict"


def _raise_platform_user_error(exc):
    if exc.kind == "forbidden":
        raise PermissionDenied(exc.detail)
    if exc.kind == "not_found":
        raise NotFound(exc.detail)
    if exc.kind == "conflict":
        raise PlatformUserConflict(exc.detail)
    raise APIException(exc.detail)


class PlatformUserAccessMixin:
    permission_classes = [IsAuthenticated, IsActivePlatformAdmin]
    throttle_classes = [ScopedRateThrottle]
    throttle_scope = "admin_write"

    def materialize_locked_read(self, operation):
        try:
            with locked_platform_admin_read(actor_id=self.request.user.pk):
                return operation()
        except PlatformUserIdentityError as exc:
            _raise_platform_user_error(exc)


class PlatformUserPagination(StandardResultsSetPagination):
    def get_paginated_response_schema(self, schema):
        paginated = super().get_paginated_response_schema(schema)
        paginated["properties"].update({
            "total_pages": {"type": "integer", "example": 7},
            "current_page": {"type": "integer", "example": 1},
            "page_size": {"type": "integer", "example": 20},
        })
        paginated["required"] = [
            "count",
            "total_pages",
            "current_page",
            "page_size",
            "results",
        ]
        return paginated


class PlatformUserListView(PlatformUserAccessMixin, generics.ListAPIView):
    serializer_class = PlatformUserSerializer
    pagination_class = PlatformUserPagination
    filter_backends = [
        DjangoFilterBackend,
        ConfiguredSearchFilter,
        StableOrderingFilter,
    ]
    filterset_class = PlatformUserFilter
    search_fields = ["email", "full_name"]
    ordering_fields = [
        "email",
        "full_name",
        "is_active",
        "is_superuser",
        "created_at",
        "updated_at",
    ]
    ordering = ["created_at", "pk"]

    def get_queryset(self):
        return User.objects.all()

    @extend_schema(
        tags=["Platform Users"],
        summary="Listar Users globales",
        responses={200: PlatformUserSerializer(many=True)},
    )
    def get(self, request, *args, **kwargs):
        return self.materialize_locked_read(
            lambda: super(PlatformUserListView, self).get(
                request,
                *args,
                **kwargs,
            )
        )


class PlatformUserDetailView(PlatformUserAccessMixin, generics.GenericAPIView):
    serializer_class = PlatformUserSerializer
    lookup_field = "public_id"

    def get_queryset(self):
        return User.objects.all()

    @extend_schema(
        tags=["Platform Users"],
        summary="Obtener User global",
        responses={200: PlatformUserSerializer},
    )
    def get(self, request, *args, **kwargs):
        return self.materialize_locked_read(
            lambda: Response(PlatformUserSerializer(self.get_object()).data)
        )

    @extend_schema(
        tags=["Platform Users"],
        summary="Actualizar identidad global de un User",
        request=PlatformUserIdentityUpdateSerializer,
        responses={
            200: PlatformUserSerializer,
            409: OpenApiResponse(description="El email ya está en uso."),
        },
    )
    def patch(self, request, public_id):
        def validate_payload(locked_target):
            serializer = PlatformUserIdentityUpdateSerializer(
                locked_target,
                data=request.data,
                partial=True,
            )
            serializer.is_valid(raise_exception=True)
            return serializer.validated_data

        try:
            target = update_platform_user_identity(
                actor=request.user,
                target_public_id=public_id,
                validate_payload=validate_payload,
            )
        except PlatformUserIdentityError as exc:
            _raise_platform_user_error(exc)
        return Response(PlatformUserSerializer(target).data)
