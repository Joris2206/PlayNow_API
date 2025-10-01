# core/exceptions.py
from rest_framework.views import exception_handler as drf_exception_handler
from rest_framework.response import Response
from rest_framework import status

def custom_exception_handler(exc, context):
    """
    Envuelve el handler por defecto y normaliza el formato.
    """
    response = drf_exception_handler(exc, context)

    if response is None:
        # Errores no manejados por DRF -> 500 genérico
        return Response(
            {"error": {"code": "internal_error", "message": "Internal server error"}},
            status=status.HTTP_500_INTERNAL_SERVER_ERROR,
        )

    # Normaliza el shape
    detail = response.data
    if isinstance(detail, dict) and "detail" in detail:
        message = detail["detail"]
    else:
        message = detail

    response.data = {
        "error": {
            "code": getattr(getattr(exc, "default_code", None), "__str__", lambda: None)() or "error",
            "message": message,
        }
    }
    return response
