from django.apps import AppConfig


class CoreConfig(AppConfig):
    default_auto_field = 'django.db.models.BigAutoField'
    name = 'core'

    def ready(self):
        # Importa la extensión para que drf-spectacular la registre
        from . import schema  # noqa
        from . import signals