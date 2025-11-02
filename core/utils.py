# core/utils.py
from rest_framework.throttling import ScopedRateThrottle
from rest_framework.decorators import api_view, throttle_classes, permission_classes
from rest_framework_simplejwt.views import TokenObtainPairView
from rest_framework.permissions import AllowAny
import logging

class LoginView(TokenObtainPairView):
    throttle_classes = [ScopedRateThrottle]
    throttle_scope = 'login'
    permission_classes = [AllowAny]


audit_logger = logging.getLogger("audit")

def log_action(user, action, entity_type, entity_id, extra=None):
    audit_logger.info(
        f"{action} {entity_type}={entity_id} by user={getattr(user, 'id', None)} extra={extra}"
    )