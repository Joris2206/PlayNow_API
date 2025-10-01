from django.core.management import BaseCommand
from core.models import EntityStatus

BASICS = ["Activo", "Inactivo", "Eliminado", "Anulado", "Cancelado"]

class Command(BaseCommand):
    help = "Crea EntityStatus básicos si no existen"

    def handle(self, *args, **kwargs):
        for name in BASICS:
            EntityStatus.objects.get_or_create(name=name)
        self.stdout.write(self.style.SUCCESS("EntityStatus básicos listos."))