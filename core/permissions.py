from rest_framework.permissions import BasePermission

class IsOwnerOrBusinessOwner(BasePermission):
    """
    Autoriza el acceso cuando:

    - El usuario es superusuario de la plataforma.
    - El objeto pertenece al usuario autenticado.
    - El ViewSet define una ruta válida hacia el propietario.

    La ruta se define mediante `owner_lookup`, por ejemplo:

        owner_lookup = "business__user"
        owner_lookup = "product__business__user"
        owner_lookup = "debt__transaction__business__user"
    """

    message = "No tienes permiso para acceder a este recurso."

    def has_permission(self, request, view):
        return bool(
            request.user
            and request.user.is_authenticated
        )

    def has_object_permission(self, request, view, obj):
        user = request.user

        if user.is_superuser:
            return True

        owner_lookup = getattr(view, "owner_lookup", None)

        if owner_lookup:
            owner = self._resolve_attribute_path(
                obj,
                owner_lookup,
            )
            return owner == user

        # Compatibilidad para modelos con usuario directo.
        if hasattr(obj, "user"):
            return obj.user_id == user.id

        # Compatibilidad para modelos con negocio directo.
        if hasattr(obj, "business"):
            business = obj.business

            if business is not None:
                return business.user_id == user.id

        return False

    @staticmethod
    def _resolve_attribute_path(obj, path):
        """
        Convierte:

            product__business__user

        en:

            obj.product.business.user
        """

        current = obj

        for attribute in path.split("__"):
            if current is None:
                return None

            current = getattr(current, attribute, None)

        return current