# core/schema.py
from drf_spectacular.extensions import OpenApiAuthenticationExtension

class SimpleJWTScheme(OpenApiAuthenticationExtension):
    target_class = 'rest_framework_simplejwt.authentication.JWTAuthentication'  # ⚠️ ruta exacta
    name = 'BearerAuth'  # cómo se llamará en components.securitySchemes

    def get_security_definition(self, auto_schema):
        return {
            'type': 'http',
            'scheme': 'bearer',
            'bearerFormat': 'JWT',
        }