from rest_framework import status
from rest_framework.exceptions import APIException
from rest_framework.views import exception_handler

from core.services.multi_user_write_lock import MultiUserWriteContentionError


MULTI_USER_WRITE_CONTENTION_DETAIL = (
    "La operación está temporalmente ocupada. Inténtalo nuevamente."
)
MULTI_USER_WRITE_CONTENTION_OPENAPI_SCHEMA = {
    "type": "object",
    "properties": {
        "detail": {
            "type": "string",
            "enum": [MULTI_USER_WRITE_CONTENTION_DETAIL],
        },
    },
    "required": ["detail"],
    "additionalProperties": False,
}


class MultiUserWriteContentionAPIException(APIException):
    status_code = status.HTTP_503_SERVICE_UNAVAILABLE
    default_detail = MULTI_USER_WRITE_CONTENTION_DETAIL
    default_code = "multi_user_write_contention"


def playnow_exception_handler(exc, context):
    """Map expected multi-User lock contention without exposing DB details."""

    if isinstance(exc, MultiUserWriteContentionError):
        exc = MultiUserWriteContentionAPIException()
    return exception_handler(exc, context)
