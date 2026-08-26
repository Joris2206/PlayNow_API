from django.core.exceptions import FieldDoesNotExist, ObjectDoesNotExist
from rest_framework import viewsets
from rest_framework.exceptions import PermissionDenied
from rest_framework.permissions import IsAuthenticated
from rest_framework.throttling import ScopedRateThrottle

from core.mixins import RequireBusinessPublicIdListMixin
from core.models import BusinessMembership, EntityStatus
from core.permissions import IsOwnerOrBusinessOwner
from core.utils import log_action

# -------- Base mixin para filtrar por usuario --------

class BusinessScopedViewSet(RequireBusinessPublicIdListMixin, viewsets.ModelViewSet):
    """
    ViewSet base para recursos pertenecientes a un usuario o negocio.

    Reglas:

    - Un superusuario de Django puede consultar todos los registros.
    - Un usuario normal solo consulta registros de negocios donde tiene
      una BusinessMembership activa.
    - Los modelos personales con campo `user` continúan filtrándose por
      el usuario autenticado.
    - No se permite crear ni mover registros hacia negocios sin acceso.
    - Los endpoints administrativos conservan visibles todos los estados.
    - Se mantiene compatibilidad temporal con `owner_lookup`.
    """

    permission_classes = [
        IsAuthenticated,
        IsOwnerOrBusinessOwner,
    ]

    throttle_classes = [
        ScopedRateThrottle,
    ]

    require_business_public_id_for_list = True

    business_query_param = (
        "business_public_id"
    )

    # Nueva ruta recomendada hacia Business.
    #
    # Ejemplos:
    #
    # business_lookup = "business"
    # business_lookup = "product__business"
    # business_lookup = "transaction__business"
    # business_lookup = "debt__transaction__business"
    business_lookup = None

    # Compatibilidad temporal con los ViewSets existentes.
    #
    # Ejemplos antiguos:
    #
    # business_lookup = "business"
    # business_lookup = "product__business"
    owner_lookup = None

    # Roles permitidos por operación.
    #
    # None significa:
    # cualquier membresía activa puede realizar la operación.
    #
    # Cada ViewSet puede sobrescribir estas propiedades.
    read_allowed_roles = None
    create_allowed_roles = None
    update_allowed_roles = None
    destroy_allowed_roles = None

    def get_throttles(self):
        self.throttle_scope = (
            "public_read"
            if self.action in (
                "list",
                "retrieve",
            )
            else "admin_write"
        )

        return super().get_throttles()

    @staticmethod
    def _is_platform_admin(user) -> bool:
        """
        Solo `is_superuser` representa acceso global a la plataforma.

        Un administrador de negocio no debe confundirse con un
        administrador global de Django.
        """

        return bool(
            user
            and user.is_authenticated
            and user.is_superuser
        )

    @staticmethod
    def _model_has_field(
        model_cls,
        field_name: str,
    ) -> bool:
        try:
            model_cls._meta.get_field(
                field_name
            )
            return True

        except FieldDoesNotExist:
            return False

    def _get_business_lookup(self):
        """
        Obtiene la ruta desde el modelo del ViewSet hasta Business.

        Prioridad:

        1. business_lookup explícito.
        2. owner_lookup antiguo terminando en __user.
        3. Campo directo business.

        Ejemplos:

            business_lookup = "product__business"

        o, temporalmente:

            business_lookup = "product__business"

        Ambos producirán:

            product__business
        """

        business_lookup = getattr(
            self,
            "business_lookup",
            None,
        )

        if business_lookup:
            return business_lookup

        owner_lookup = getattr(
            self,
            "owner_lookup",
            None,
        )

        if owner_lookup:
            # Solo las rutas que realmente pasan por Business pueden
            # convertirse al nuevo business_lookup. Ejemplos válidos:
            # business__user / product__business__user.
            if owner_lookup == "business__user":
                return "business"

            if "__business__user" in owner_lookup:
                return owner_lookup.removesuffix("__user")

            # Rutas personales como "user" o "goal__user" deben
            # continuar filtrándose por propietario, no por membresía.
            return None

        queryset = super().get_queryset()
        model_cls = queryset.model

        if self._model_has_field(
            model_cls,
            "business",
        ):
            return "business"

        return None

    def _user_can_access_business(
        self,
        user,
        business,
        allowed_roles=None,
    ) -> bool:
        """
        Comprueba si el usuario tiene una membresía activa en el negocio.

        Si allowed_roles es None, basta con cualquier membresía activa.
        """

        if business is None:
            return False

        if self._is_platform_admin(user):
            return True

        filters = {
            "user": user,
            "business": business,
            "is_active": True,
        }

        if allowed_roles is not None:
            filters["role__in"] = (
                allowed_roles
            )

        return (
            BusinessMembership.objects
            .filter(**filters)
            .exists()
        )

    def _validate_business_access(
        self,
        business,
        allowed_roles=None,
    ) -> None:
        """
        Valida acceso a un negocio.

        Puede limitar el acceso a determinados roles:

            self._validate_business_access(
                business,
                allowed_roles=[
                    BusinessMembership.ROLE_OWNER,
                    BusinessMembership.ROLE_ADMIN,
                ],
            )
        """

        if business is None:
            raise PermissionDenied(
                "Debes indicar un negocio válido."
            )

        has_access = (
            self._user_can_access_business(
                self.request.user,
                business,
                allowed_roles=allowed_roles,
            )
        )

        if not has_access:
            raise PermissionDenied(
                "No tienes permiso para utilizar "
                "este negocio."
            )

    def validate_destroy_access(
        self,
        instance,
    ):
        business = (
            self._get_business_from_instance(
                instance
            )
        )

        if business is None:
            return

        self._validate_business_access(
            business,
            allowed_roles=(
                self.destroy_allowed_roles
            ),
        )

    def _resolve_attribute_path(
        self,
        obj,
        path,
    ):
        """
        Convierte una ruta como:

            product__business

        en:

            obj.product.business
        """

        current_object = obj

        try:
            for attribute in path.split("__"):
                if current_object is None:
                    return None

                current_object = getattr(
                    current_object,
                    attribute,
                )

        except (
            AttributeError,
            ObjectDoesNotExist,
        ):
            return None

        return current_object

    def _get_business_from_instance(
        self,
        instance,
    ):
        """
        Obtiene Business desde una instancia ya guardada.
        """

        business_lookup = (
            self._get_business_lookup()
        )

        if not business_lookup:
            return None

        return self._resolve_attribute_path(
            instance,
            business_lookup,
        )

    def _get_business_from_serializer(
        self,
        serializer,
    ):
        """
        Obtiene el negocio relacionado usando validated_data.

        Soporta relaciones directas e indirectas hacia Business.

        En actualizaciones parciales, si la relación principal no viene,
        utiliza la instancia existente.
        """

        business_lookup = (
            self._get_business_lookup()
        )

        if not business_lookup:
            return None

        lookup_parts = (
            business_lookup.split("__")
        )

        first_field = lookup_parts[0]

        related_object = (
            serializer.validated_data.get(
                first_field
            )
        )

        if related_object is None:
            instance = getattr(
                serializer,
                "instance",
                None,
            )

            if instance is None:
                return None

            return self._resolve_attribute_path(
                instance,
                business_lookup,
            )

        if len(lookup_parts) == 1:
            return related_object

        remaining_path = "__".join(
            lookup_parts[1:]
        )

        return self._resolve_attribute_path(
            related_object,
            remaining_path,
        )

    def _validate_business_relation(
        self,
        serializer,
        allowed_roles=None,
    ) -> None:
        """
        Valida relaciones directas e indirectas hacia Business.

        Ejemplos:

        Product:
            business_lookup = "business"

        """

        user = self.request.user

        if self._is_platform_admin(user):
            return

        business_lookup = (
            self._get_business_lookup()
        )

        if not business_lookup:
            return

        business = (
            self._get_business_from_serializer(
                serializer
            )
        )

        if business is None:
            # En creación, una relación que debe conducir al negocio
            # no puede quedar sin validar.
            if serializer.instance is None:
                raise PermissionDenied(
                    "No se pudo determinar el negocio "
                    "del recurso relacionado."
                )

            return

        self._validate_business_access(
            business,
            allowed_roles=allowed_roles,
        )

    def get_queryset(self):
        queryset = super().get_queryset()

        user = self.request.user
        if not user.is_authenticated:
            return queryset.none()

        business_lookup = (
            self._get_business_lookup()
        )

        owner_lookup = getattr(
            self,
            "owner_lookup",
            None,
        )

        # -------------------------------------------------
        # 1. Limitar por membresías
        # -------------------------------------------------

        if (
            not self._is_platform_admin(user)
            and business_lookup
        ):
            membership_filters = {
                (
                    f"{business_lookup}"
                    "__memberships__user"
                ): user,
                (
                    f"{business_lookup}"
                    "__memberships__is_active"
                ): True,
            }

            if (
                self.read_allowed_roles
                is not None
            ):
                membership_filters[
                    (
                        f"{business_lookup}"
                        "__memberships__role__in"
                    )
                ] = self.read_allowed_roles

            queryset = queryset.filter(
                **membership_filters
            )

        # -------------------------------------------------
        # 2. Recursos personales
        # -------------------------------------------------

        if (
            not self._is_platform_admin(user)
            and owner_lookup
        ):
            queryset = queryset.filter(
                **{
                    owner_lookup: user,
                }
            )

        return queryset.distinct()
    
    def perform_create(self, serializer):
        model_cls = serializer.Meta.model
        user = self.request.user
        extra = {}

        # Modelos personales con campo user.
        if self._model_has_field(
            model_cls,
            "user",
        ):
            submitted_user = (
                serializer.validated_data.get(
                    "user"
                )
            )

            if (
                self._is_platform_admin(user)
                and submitted_user is not None
            ):
                extra["user"] = submitted_user
            else:
                extra["user"] = user

        # Valida negocio directo o indirecto.
        self._validate_business_relation(
            serializer,
            allowed_roles=(
                self.create_allowed_roles
            ),
        )
        self._validate_legacy_owner_relation(serializer)
        self._validate_direct_business_field(
            serializer,
            allowed_roles=self.create_allowed_roles,
        )

        # Estado inicial automático.
        if (
            self._model_has_field(
                model_cls,
                "status",
            )
            and "status"
            not in serializer.validated_data
        ):
            active_status = (
                EntityStatus.objects
                .filter(
                    name__iexact="Activo"
                )
                .first()
            )

            if active_status is None:
                raise PermissionDenied(
                    "No existe el estado inicial "
                    "'Activo'. Ejecuta el comando "
                    "seed_statuses."
                )

            extra["status"] = active_status

        obj = serializer.save(
            **extra
        )

        log_action(
            user,
            "CREATE",
            model_cls.__name__,
            obj.pk,
        )

    def perform_update(self, serializer):
        model_cls = serializer.Meta.model
        user = self.request.user

        if (
            self._model_has_field(
                model_cls,
                "user",
            )
            and not self._is_platform_admin(
                user
            )
        ):
            # Un usuario normal no puede reasignar el propietario
            # de un recurso personal.
            serializer.validated_data.pop(
                "user",
                None,
            )

        self._validate_business_relation(
            serializer,
            allowed_roles=(
                self.update_allowed_roles
            ),
        )
        self._validate_legacy_owner_relation(serializer)
        self._validate_direct_business_field(
            serializer,
            allowed_roles=self.update_allowed_roles,
        )

        obj = serializer.save()

        log_action(
            user,
            "UPDATE",
            obj.__class__.__name__,
            obj.pk,
        )

    def perform_destroy(self, instance):
        business = (
            self._get_business_from_instance(
                instance
            )
        )

        if business is not None:
            self._validate_business_access(
                business,
                allowed_roles=(
                    self.destroy_allowed_roles
                ),
            )

        super().perform_destroy(
            instance
        )

        log_action(
            self.request.user,
            "DELETE",
            instance.__class__.__name__,
            instance.pk,
        )

    def _validate_direct_business_field(
        self,
        serializer,
        allowed_roles=None,
    ) -> None:
        """
        Para recursos personales que además tienen un campo `business`,
        valida que el negocio enviado pertenezca a una membresía activa.
        """
        model_cls = serializer.Meta.model

        if not self._model_has_field(model_cls, "business"):
            return

        # Si ya existe business_lookup, la validación se hizo antes.
        if self._get_business_lookup():
            return

        business = serializer.validated_data.get("business")

        if business is None and serializer.instance is not None:
            business = getattr(serializer.instance, "business", None)

        if business is not None:
            self._validate_business_access(
                business,
                allowed_roles=allowed_roles,
            )

    def _validate_legacy_owner_relation(self, serializer) -> None:
        """Valida owner_lookup personales como `goal__user`."""
        user = self.request.user

        if self._is_platform_admin(user):
            return

        owner_lookup = getattr(self, "owner_lookup", None)

        if not owner_lookup or self._get_business_lookup():
            return

        parts = owner_lookup.split("__")

        if len(parts) < 2:
            return

        related_object = serializer.validated_data.get(parts[0])

        if related_object is None:
            return

        related_user = self._resolve_attribute_path(
            related_object,
            "__".join(parts[1:]),
        )

        if related_user is None or related_user.pk != user.pk:
            raise PermissionDenied(
                "No tienes permiso para utilizar el recurso relacionado."
            )

    def _validate_owner_relation(self, serializer) -> None:
        """Alias temporal para código antiguo."""
        self._validate_business_relation(serializer)
        self._validate_legacy_owner_relation(serializer)
