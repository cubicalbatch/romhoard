from django.apps import AppConfig


class RomcollectionsConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "romcollections"
    verbose_name = "Collections"

    def ready(self):
        """Load signals when app is ready."""
        from . import signals  # noqa: F401
