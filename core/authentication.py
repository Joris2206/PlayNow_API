from rest_framework_simplejwt.authentication import JWTAuthentication
from rest_framework_simplejwt.exceptions import AuthenticationFailed, InvalidToken
from rest_framework_simplejwt.settings import api_settings

from core.tokens import AUTH_VERSION_CLAIM


INVALID_AUTHENTICATION = "La sesión no es válida."


class VersionedJWTAuthentication(JWTAuthentication):
    """Authenticate access tokens against the User's current auth version."""

    def get_user(self, validated_token):
        try:
            user_id = validated_token[api_settings.USER_ID_CLAIM]
        except KeyError as exc:
            raise InvalidToken(INVALID_AUTHENTICATION) from exc

        try:
            user = self.user_model.objects.get(
                **{api_settings.USER_ID_FIELD: user_id}
            )
        except (self.user_model.DoesNotExist, ValueError, TypeError) as exc:
            raise AuthenticationFailed(
                INVALID_AUTHENTICATION,
                code="authentication_failed",
            ) from exc

        if not api_settings.USER_AUTHENTICATION_RULE(user):
            raise AuthenticationFailed(
                INVALID_AUTHENTICATION,
                code="authentication_failed",
            )

        token_version = validated_token.get(AUTH_VERSION_CLAIM)
        if type(token_version) is not int or token_version != user.auth_version:
            raise AuthenticationFailed(
                INVALID_AUTHENTICATION,
                code="authentication_failed",
            )

        return user
