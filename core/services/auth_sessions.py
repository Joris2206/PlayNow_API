from dataclasses import dataclass

from django.contrib.auth.password_validation import validate_password
from django.core.exceptions import ValidationError as DjangoValidationError
from django.db import transaction
from rest_framework_simplejwt.settings import api_settings
from rest_framework_simplejwt.token_blacklist.models import (
    BlacklistedToken,
    OutstandingToken,
)

from core.models import User
from core.tokens import AUTH_VERSION_CLAIM, SignedRefreshToken, VersionedRefreshToken


INVALID_CREDENTIALS = "No existe una cuenta activa con las credenciales indicadas."
INVALID_TOKEN = "El token no es válido."


@dataclass(frozen=True)
class AuthSessionError(Exception):
    detail: object
    code: str = "authentication_failed"

    def __str__(self):
        return str(self.detail)


def _dummy_password_check(raw_password):
    dummy = User(email="dummy@example.invalid", full_name="Dummy")
    dummy.set_password(raw_password)


def _user_id_from_token(token):
    value = token.get(api_settings.USER_ID_CLAIM)
    if isinstance(value, bool):
        raise AuthSessionError(INVALID_TOKEN)
    try:
        return int(value)
    except (TypeError, ValueError, OverflowError) as exc:
        raise AuthSessionError(INVALID_TOKEN) from exc


def _auth_version_from_token(token):
    value = token.get(AUTH_VERSION_CLAIM)
    if type(value) is not int or value < 0:
        raise AuthSessionError(INVALID_TOKEN)
    return value


def _jti_from_token(token):
    value = token.get(api_settings.JTI_CLAIM)
    if not isinstance(value, str) or not value:
        raise AuthSessionError(INVALID_TOKEN)
    return value


def parse_signed_refresh(raw_token):
    return SignedRefreshToken(raw_token)


def issue_token_pair(user):
    refresh = VersionedRefreshToken.for_user(user)
    return {
        "refresh": str(refresh),
        "access": str(refresh.access_token),
    }


@transaction.atomic
def login_and_issue_tokens(*, email, password):
    try:
        user = User.objects.select_for_update().get(email__iexact=email)
    except User.DoesNotExist as exc:
        _dummy_password_check(password)
        raise AuthSessionError(INVALID_CREDENTIALS) from exc

    password_matches = user.check_password(password)
    if not password_matches or not api_settings.USER_AUTHENTICATION_RULE(user):
        raise AuthSessionError(INVALID_CREDENTIALS)

    return issue_token_pair(user)


@transaction.atomic
def rotate_refresh_token(*, raw_token):
    token = parse_signed_refresh(raw_token)
    user_id = _user_id_from_token(token)
    token_version = _auth_version_from_token(token)
    jti = _jti_from_token(token)

    try:
        user = User.objects.select_for_update().get(pk=user_id)
    except User.DoesNotExist as exc:
        raise AuthSessionError(INVALID_TOKEN) from exc

    if (
        not api_settings.USER_AUTHENTICATION_RULE(user)
        or token_version != user.auth_version
    ):
        raise AuthSessionError(INVALID_TOKEN)

    try:
        outstanding = (
            OutstandingToken.objects.select_for_update()
            .get(user=user, jti=jti)
        )
    except OutstandingToken.DoesNotExist as exc:
        raise AuthSessionError(INVALID_TOKEN) from exc

    if BlacklistedToken.objects.filter(token=outstanding).exists():
        raise AuthSessionError(INVALID_TOKEN)

    BlacklistedToken.objects.create(token=outstanding)
    return issue_token_pair(user)


def _lock_outstanding_tokens(user):
    return list(
        OutstandingToken.objects.select_for_update()
        .filter(user=user)
        .order_by("pk")
    )


def _blacklist_locked_tokens(tokens):
    for outstanding in tokens:
        BlacklistedToken.objects.get_or_create(token=outstanding)


def _revoke_locked_user_sessions(user, *, update_fields=()):
    user.auth_version += 1
    fields = ["auth_version", *update_fields]
    user.save(update_fields=fields)
    _blacklist_locked_tokens(_lock_outstanding_tokens(user))
    return user


@transaction.atomic
def revoke_user_sessions(*, user_id):
    try:
        user = User.objects.select_for_update().get(pk=user_id)
    except User.DoesNotExist as exc:
        raise AuthSessionError(INVALID_TOKEN) from exc
    return _revoke_locked_user_sessions(user)


@transaction.atomic
def logout_user_sessions(*, raw_token):
    token = parse_signed_refresh(raw_token)
    user_id = _user_id_from_token(token)
    token_version = _auth_version_from_token(token)

    try:
        user = User.objects.select_for_update().get(pk=user_id)
    except User.DoesNotExist as exc:
        raise AuthSessionError(INVALID_TOKEN) from exc

    if token_version > user.auth_version:
        raise AuthSessionError(INVALID_TOKEN)
    if token_version == user.auth_version:
        _revoke_locked_user_sessions(user)
    return user


@transaction.atomic
def change_user_password(
    *,
    user_id,
    expected_auth_version,
    current_password,
    new_password,
):
    try:
        user = User.objects.select_for_update().get(pk=user_id)
    except User.DoesNotExist as exc:
        raise AuthSessionError(INVALID_TOKEN) from exc

    if (
        type(expected_auth_version) is not int
        or expected_auth_version != user.auth_version
        or not api_settings.USER_AUTHENTICATION_RULE(user)
    ):
        raise AuthSessionError(INVALID_TOKEN)
    if not user.check_password(current_password):
        raise AuthSessionError(
            {"current_password": ["Contraseña actual incorrecta."]},
            code="invalid_password",
        )
    try:
        validate_password(new_password, user=user)
    except DjangoValidationError as exc:
        raise AuthSessionError(
            {"new_password": list(exc.messages)},
            code="invalid_password",
        ) from exc

    user.set_password(new_password)
    return _revoke_locked_user_sessions(user, update_fields=("password",))


@transaction.atomic
def reset_user_password(*, user_id, reset_token, new_password, token_generator):
    try:
        user = User.objects.select_for_update().get(pk=user_id)
    except User.DoesNotExist as exc:
        raise AuthSessionError(INVALID_TOKEN) from exc

    if (
        not api_settings.USER_AUTHENTICATION_RULE(user)
        or not token_generator.check_token(user, reset_token)
    ):
        raise AuthSessionError(INVALID_TOKEN)
    try:
        validate_password(new_password, user=user)
    except DjangoValidationError as exc:
        raise AuthSessionError(
            {"new_password": list(exc.messages)},
            code="invalid_password",
        ) from exc

    user.set_password(new_password)
    return _revoke_locked_user_sessions(user, update_fields=("password",))
