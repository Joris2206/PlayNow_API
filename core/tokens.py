from rest_framework_simplejwt.settings import api_settings
from rest_framework_simplejwt.token_blacklist.models import OutstandingToken
from rest_framework_simplejwt.tokens import RefreshToken, Token
from rest_framework_simplejwt.utils import datetime_from_epoch


AUTH_VERSION_CLAIM = "auth_version"


class VersionedRefreshToken(RefreshToken):
    """Refresh token whose persisted and encoded forms include auth_version."""

    @classmethod
    def for_user(cls, user):
        token = Token.for_user.__func__(cls, user)
        token[AUTH_VERSION_CLAIM] = user.auth_version
        OutstandingToken.objects.create(
            user=user,
            jti=token[api_settings.JTI_CLAIM],
            token=str(token),
            created_at=token.current_time,
            expires_at=datetime_from_epoch(token["exp"]),
        )
        return token


class SignedRefreshToken(VersionedRefreshToken):
    """Validate a refresh cryptographically without a pre-lock blacklist read."""

    def verify(self, *args, **kwargs):
        Token.verify(self)
