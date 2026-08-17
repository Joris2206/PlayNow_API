# core/filters.py
from django_filters import rest_framework as filters
from .models import StockMovement, Transaction
from django_filters.rest_framework import (
    DjangoFilterBackend,
)
from rest_framework.filters import SearchFilter


class ConfiguredSearchFilter(SearchFilter):
    """
    Only advertise search when the view declares searchable fields.

    DRF safely ignores ``search`` when ``search_fields`` is absent, but its
    default schema parameter still suggests that the query parameter works.
    """

    def get_search_fields(self, view, request):
        dynamic_fields = getattr(
            view,
            "get_search_fields",
            None,
        )

        if callable(dynamic_fields):
            return dynamic_fields()

        return super().get_search_fields(
            view,
            request,
        )

    def get_schema_operation_parameters(self, view):
        if not getattr(view, "search_fields", None):
            return []

        return super().get_schema_operation_parameters(view)


class TransactionFilter(
    filters.FilterSet
):
    business_public_id = filters.UUIDFilter(
        field_name="business__public_id",
    )

    customer_public_id = filters.UUIDFilter(
        field_name="customer__public_id",
    )

    supplier_public_id = filters.UUIDFilter(
        field_name="supplier__public_id",
    )

    employee_public_id = filters.UUIDFilter(
        field_name="employee__public_id",
    )

    payment_method_public_id = (
        filters.UUIDFilter(
            field_name=(
                "payment_method__public_id"
            ),
        )
    )

    status_public_id = filters.UUIDFilter(
        field_name="status__public_id",
    )

    type = filters.CharFilter(
        field_name="type",
    )

    payment_status = filters.CharFilter(
        field_name="payment_status",
    )

    is_debt = filters.BooleanFilter(
        field_name="is_debt",
    )

    date_from = filters.DateFilter(
        field_name="created_at",
        lookup_expr="date__gte",
    )

    date_to = filters.DateFilter(
        field_name="created_at",
        lookup_expr="date__lte",
    )

    class Meta:
        model = Transaction
        fields = []

class StockMovementFilter(
    filters.FilterSet
):
    created_at = (
        filters.IsoDateTimeFromToRangeFilter(
            field_name="created_at",
        )
    )

    business_public_id = filters.UUIDFilter(
        field_name=(
            "product__business__public_id"
        )
    )

    transaction_business_public_id = (
        filters.UUIDFilter(
            field_name=(
                "transaction__business__public_id"
            ),
        )
    )

    product_public_id = filters.UUIDFilter(
        field_name="product__public_id",
    )

    transaction_public_id = (
        filters.UUIDFilter(
            field_name="transaction__public_id",
        )
    )

    type = filters.CharFilter(
        field_name="type",
        lookup_expr="iexact",
    )

    class Meta:
        model = StockMovement
        fields = []

class PublicIdFilterBackend(
    DjangoFilterBackend
):
    def get_filterset_class(
        self,
        view,
        queryset=None,
    ):
        explicit_class = getattr(
            view,
            "filterset_class",
            None,
        )

        if explicit_class is not None:
            return explicit_class

        if queryset is None:
            return None

        public_id_fields = dict(
            getattr(
                view,
                "public_id_filter_fields",
                {},
            )
            or {}
        )

        simple_fields = dict(
            getattr(
                view,
                "simple_filter_fields",
                {},
            )
            or {}
        )

        business_lookup = getattr(
            view,
            "business_lookup",
            None,
        )

        attrs = {
            "__module__": __name__,
        }

        if business_lookup:
            attrs["business_public_id"] = (
                filters.UUIDFilter(
                    field_name=(
                        f"{business_lookup}__public_id"
                    ),
                )
            )

        for public_name, orm_field in (
            public_id_fields.items()
        ):
            attrs[public_name] = (
                filters.UUIDFilter(
                    field_name=orm_field,
                )
            )

        for public_name, filter_obj in (
            simple_fields.items()
        ):
            attrs[public_name] = filter_obj

        if len(attrs) == 1:
            return super().get_filterset_class(
                view,
                queryset,
            )

        meta = type(
            "Meta",
            (),
            {
                "model": queryset.model,
                "fields": [],
            },
        )

        attrs["Meta"] = meta

        return type(
            (
                f"{queryset.model.__name__}"
                "AutoFilter"
            ),
            (filters.FilterSet,),
            attrs,
        )
