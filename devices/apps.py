from django.apps import AppConfig


class DevicesConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "devices"

    def ready(self):
        # Only sync in main process, not in migrations or management commands
        import sys

        if "runserver" in sys.argv or "db_worker" in sys.argv:
            from .preset_loader import sync_presets

            sync_presets()
