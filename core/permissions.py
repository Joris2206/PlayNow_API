from rest_framework.permissions import BasePermission

from core.models import (
    Business,
    BusinessMembership,
)


def get_business_from_object(obj):
    """
    Intenta obtener el negocio relacionado con un objeto.

    Casos soportados:

        Business
        obj.business
        obj.product.business
        obj.transaction.business
        obj.debt.transaction.business
    """

    if isinstance(obj, Business):
        return obj

    business = getattr(
        obj,
        "business",
        None,
    )

    if business is not None:
        return business

    product = getattr(
        obj,
        "product",
        None,
    )

    if product is not None:
        business = getattr(
            product,
            "business",
            None,
        )

        if business is not None:
            return business

    transaction = getattr(
        obj,
        "transaction",
        None,
    )

    if transaction is not None:
        business = getattr(
            transaction,
            "business",
            None,
        )

        if business is not None:
            return business

    debt = getattr(
        obj,
        "debt",
        None,
    )

    if debt is not None:
        transaction = getattr(
            debt,
            "transaction",
            None,
        )

        if transaction is not None:
            return getattr(
                transaction,
                "business",
                None,
            )

    return None


class IsAuthenticatedUser(BasePermission):
    """
    Solo verifica que el usuario esté autenticado.
    """

    message = "Debes iniciar sesión."

    def has_permission(
        self,
        request,
        view,
    ):
        return bool(
            request.user
            and request.user.is_authenticated
        )


class HasBusinessMembership(BasePermission):
    """
    Permiso base para recursos asociados a un negocio.

    Verifica que:

    - El usuario esté autenticado.
    - El usuario sea superusuario, o
    - Exista una membresía activa en el negocio.
    - El rol pertenezca a allowed_roles cuando se defina.
    """

    message = (
        "No tienes acceso a este negocio."
    )

    allowed_roles = None

    def has_permission(
        self,
        request,
        view,
    ):
        return bool(
            request.user
            and request.user.is_authenticated
        )

    def has_object_permission(
        self,
        request,
        view,
        obj,
    ):
        user = request.user

        if user.is_superuser:
            return True

        business = get_business_from_object(
            obj
        )

        if business is None:
            return False

        filters = {
            "user": user,
            "business": business,
            "is_active": True,
        }

        if self.allowed_roles is not None:
            filters["role__in"] = (
                self.allowed_roles
            )

        return (
            BusinessMembership.objects
            .filter(**filters)
            .exists()
        )


class IsBusinessMember(
    HasBusinessMembership
):
    """
    Cualquier usuario con membresía activa.
    """

    allowed_roles = [
        BusinessMembership.ROLE_OWNER,
        BusinessMembership.ROLE_ADMIN,
        BusinessMembership.ROLE_CASHIER,
        BusinessMembership.ROLE_SELLER,
        BusinessMembership.ROLE_INVENTORY,
        BusinessMembership.ROLE_VIEWER,
    ]


class IsBusinessOwner(
    HasBusinessMembership
):
    """
    Solo el propietario del negocio.
    """

    message = (
        "Solo el propietario puede realizar "
        "esta operación."
    )

    allowed_roles = [
        BusinessMembership.ROLE_OWNER,
    ]


class IsBusinessOwnerOrAdmin(
    HasBusinessMembership
):
    """
    Propietario o administrador.
    """

    message = (
        "Solo el propietario o un administrador "
        "puede realizar esta operación."
    )

    allowed_roles = [
        BusinessMembership.ROLE_OWNER,
        BusinessMembership.ROLE_ADMIN,
    ]


class CanManageTransactions(
    HasBusinessMembership
):
    """
    Roles autorizados para crear o modificar
    transacciones.
    """

    message = (
        "No tienes permiso para administrar "
        "transacciones."
    )

    allowed_roles = [
        BusinessMembership.ROLE_OWNER,
        BusinessMembership.ROLE_ADMIN,
        BusinessMembership.ROLE_CASHIER,
    ]


class CanViewTransactions(
    HasBusinessMembership
):
    """
    Roles autorizados para consultar transacciones.
    """

    message = (
        "No tienes permiso para consultar "
        "transacciones."
    )

    allowed_roles = [
        BusinessMembership.ROLE_OWNER,
        BusinessMembership.ROLE_ADMIN,
        BusinessMembership.ROLE_CASHIER,
        BusinessMembership.ROLE_SELLER,
        BusinessMembership.ROLE_VIEWER,
    ]


class CanManageInventory(
    HasBusinessMembership
):
    """
    Roles autorizados para crear o modificar
    inventario.
    """

    message = (
        "No tienes permiso para administrar "
        "el inventario."
    )

    allowed_roles = [
        BusinessMembership.ROLE_OWNER,
        BusinessMembership.ROLE_ADMIN,
        BusinessMembership.ROLE_INVENTORY,
    ]


class CanViewInventory(
    HasBusinessMembership
):
    """
    Roles autorizados para consultar inventario.
    """

    message = (
        "No tienes permiso para consultar "
        "el inventario."
    )

    allowed_roles = [
        BusinessMembership.ROLE_OWNER,
        BusinessMembership.ROLE_ADMIN,
        BusinessMembership.ROLE_CASHIER,
        BusinessMembership.ROLE_SELLER,
        BusinessMembership.ROLE_INVENTORY,
        BusinessMembership.ROLE_VIEWER,
    ]


class CanManageEmployees(
    HasBusinessMembership
):
    """
    Solo propietario o administrador pueden
    administrar empleados.
    """

    message = (
        "No tienes permiso para administrar "
        "empleados."
    )

    allowed_roles = [
        BusinessMembership.ROLE_OWNER,
        BusinessMembership.ROLE_ADMIN,
    ]


class CanManageBusinessSettings(
    HasBusinessMembership
):
    """
    Permiso para modificar datos sensibles
    del negocio.
    """

    message = (
        "No tienes permiso para modificar "
        "la configuración del negocio."
    )

    allowed_roles = [
        BusinessMembership.ROLE_OWNER,
        BusinessMembership.ROLE_ADMIN,
    ]


class IsOwnerOrBusinessOwner(BasePermission):
    """
    Compatibilidad temporal con recursos antiguos.

    Usa esta clase solo mientras migras los ViewSets
    que todavía dependen de owner_lookup.
    """

    message = (
        "No tienes permiso para acceder "
        "a este recurso."
    )

    def has_permission(
        self,
        request,
        view,
    ):
        return bool(
            request.user
            and request.user.is_authenticated
        )

    def has_object_permission(
        self,
        request,
        view,
        obj,
    ):
        user = request.user

        if user.is_superuser:
            return True

        owner_lookup = getattr(
            view,
            "owner_lookup",
            None,
        )

        if owner_lookup:
            owner = self._resolve_attribute_path(
                obj,
                owner_lookup,
            )

            if owner == user:
                return True

        if hasattr(obj, "user"):
            if obj.user_id == user.id:
                return True

        business = get_business_from_object(
            obj
        )

        if business is None:
            return False

        return (
            BusinessMembership.objects
            .filter(
                user=user,
                business=business,
                is_active=True,
            )
            .exists()
        )

    @staticmethod
    def _resolve_attribute_path(
        obj,
        path,
    ):
        current = obj

        for attribute in path.split("__"):
            if current is None:
                return None

            current = getattr(
                current,
                attribute,
                None,
            )

        return current