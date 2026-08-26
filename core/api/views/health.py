from drf_spectacular.utils import OpenApiExample, extend_schema
from rest_framework.decorators import api_view, permission_classes, throttle_classes
from rest_framework.permissions import AllowAny
from rest_framework.response import Response
from rest_framework.throttling import ScopedRateThrottle

from core.api.serializers.auth import HealthSerializer


@extend_schema(
    responses=HealthSerializer,
    tags=["Health"],
    examples=[OpenApiExample("OK", value={"status": "ok", "service": "PlayNow API"})],
)
@api_view(["GET"])
@permission_classes([AllowAny])
@throttle_classes([ScopedRateThrottle])
def healthcheck(request):
    return Response({"status": "ok", "service": "PlayNow API"})


healthcheck.throttle_scope = "public_read"
