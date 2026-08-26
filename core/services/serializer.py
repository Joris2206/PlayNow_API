from django.contrib.auth import get_user_model

from core.api.serializers.auth import (
    BLACKLIST_ENABLED,
    ChangePasswordSerializer,
    PasswordResetTokenGenerator,
    PasswordResetConfirmSerializer,
    PasswordResetRequestSerializer,
    User,
    force_bytes,
    force_str,
    serializers,
    token_generator,
    urlsafe_base64_decode,
    urlsafe_base64_encode,
)

try:
    from core.api.serializers.auth import BlacklistedToken, OutstandingToken
except ImportError:
    pass
