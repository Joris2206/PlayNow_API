from rest_framework import serializers


def public_id_field(
    model,
    *,
    source=None,
    required=True,
    allow_null=False,
):
    """Campo relacional que recibe y devuelve el public_id (UUID)."""
    kwargs = {
        "slug_field": "public_id",
        "queryset": model.objects.all(),
        "required": required,
        "allow_null": allow_null,
    }

    if source is not None:
        kwargs["source"] = source

    return serializers.SlugRelatedField(
        **kwargs,
    )


def related_name_field(
    source,
    *,
    allow_null=False,
):
    return serializers.CharField(
        source=source,
        read_only=True,
        allow_null=allow_null,
    )
