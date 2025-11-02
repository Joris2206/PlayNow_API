from django.contrib.auth import get_user_model
from django.contrib.auth.tokens import PasswordResetTokenGenerator
from django.utils.encoding import force_bytes, force_str
from django.utils.http import urlsafe_base64_encode, urlsafe_base64_decode
from rest_framework import serializers

try:
    from rest_framework_simplejwt.token_blacklist.models import BlacklistedToken, OutstandingToken
    BLACKLIST_ENABLED = True
except ImportError:
    BLACKLIST_ENABLED = False


User = get_user_model()
token_generator = PasswordResetTokenGenerator()


class ChangePasswordSerializer(serializers.Serializer):
    current_password = serializers.CharField(write_only=True, trim_whitespace=False)
    new_password = serializers.CharField(write_only=True, trim_whitespace=False)

    def validate(self, attrs):
        user = self.context["request"].user
        if not user.check_password(attrs["current_password"]):
            raise serializers.ValidationError({"current_password": "Contraseña actual incorrecta."})
        return attrs


class PasswordResetRequestSerializer(serializers.Serializer):
    email = serializers.EmailField()

    def validate(self, attrs):
        # No revelar si existe o no
        self.user = User.objects.filter(email__iexact=attrs["email"]).first()
        return attrs

    def save(self, *, frontend_reset_url: str):
        if not self.user:
            return  # siempre devolvemos 200

        uidb64 = urlsafe_base64_encode(force_bytes(self.user.pk))
        token = token_generator.make_token(self.user)
        reset_link = f"{frontend_reset_url}?uid={uidb64}&token={token}"

        display_name = (
            getattr(self.user, "full_name", None)
            or f"{getattr(self.user, 'first_name', '')} {getattr(self.user, 'last_name', '')}".strip()
            or self.user.email
        )

        from django.core.mail import send_mail
        subject = "Restablecer contraseña"
        message = (
            f"Hola {display_name},\n\n"
            "Recibimos una solicitud para restablecer tu contraseña. "
            f"Abre este enlace para continuar:\n{reset_link}\n\n"
            "Si no fuiste tú, ignora este mensaje."
        )
        send_mail(subject, message, None, [self.user.email], fail_silently=False)


class PasswordResetConfirmSerializer(serializers.Serializer):
    uid = serializers.CharField()
    token = serializers.CharField()
    new_password = serializers.CharField(write_only=True, min_length=8, trim_whitespace=False)

    def validate(self, attrs):
        try:
            uid = force_str(urlsafe_base64_decode(attrs["uid"]))
            user = User.objects.get(pk=uid)
        except Exception:
            raise serializers.ValidationError({"uid": "UID inválido."})

        if not token_generator.check_token(user, attrs["token"]):
            raise serializers.ValidationError({"token": "Token inválido o expirado."})

        self.user = user
        return attrs

    def save(self):
        user = self.user
        user.set_password(self.validated_data["new_password"])
        user.save(update_fields=["password"])

        if BLACKLIST_ENABLED:
            try:
                for token in OutstandingToken.objects.filter(user=user):
                    BlacklistedToken.objects.get_or_create(token=token)
            except Exception:
                pass
