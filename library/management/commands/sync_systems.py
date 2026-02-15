from django.core.management.base import BaseCommand
from library.system_loader import sync_systems


class Command(BaseCommand):
    help = "Sync system definitions from config file to database"

    def handle(self, *args, **options):
        sync_systems()
        self.stdout.write(self.style.SUCCESS("Systems synced successfully"))
