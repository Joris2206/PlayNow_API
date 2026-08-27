from drf_spectacular.utils import extend_schema, extend_schema_view
from rest_framework import viewsets
from rest_framework.permissions import IsAuthenticated

from core.api.serializers.status import EntityStatusSerializer
from core.models import EntityStatus
from core.pagination import StandardResultsSetPagination


@extend_schema_view(
    list=extend_schema(tags=["Statuses"]),
    retrieve=extend_schema(tags=["Statuses"]),
)
class EntityStatusViewSet(viewsets.ReadOnlyModelViewSet):
    queryset = EntityStatus.objects.all()
    serializer_class = EntityStatusSerializer
    permission_classes = [IsAuthenticated]
    lookup_field = "public_id"
    lookup_url_kwarg = "public_id"
    pagination_class = StandardResultsSetPagination   # opcional (por si lista crece)
