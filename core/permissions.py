from rest_framework.permissions import BasePermission, SAFE_METHODS

class IsOwnerOrBusinessOwner(BasePermission):
    """
    Permite acceso si el objeto pertenece al negocio del usuario o si el recurso
    es del propio usuario. Para Admins por rol, permite todo.
    """
    def has_object_permission(self, request, view, obj):
        user = request.user
        if getattr(user, "role", "") == "admin":
            return True

        # objetos con campo 'user'
        if hasattr(obj, "user"):
            return obj.user == user

        # objetos con campo 'business' -> 'user'
        if hasattr(obj, "business") and hasattr(obj.business, "user"):
            return obj.business.user == user

        return False