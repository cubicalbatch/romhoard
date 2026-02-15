from django.core.management.base import BaseCommand

from devices.preset_loader import sync_presets


class Command(BaseCommand):
    help = "Sync device presets from config file to database"

    def handle(self, *args, **options):
        count = sync_presets()
        self.stdout.write(self.style.SUCCESS(f"Synced {count} device presets"))
