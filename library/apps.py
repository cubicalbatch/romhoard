from django.apps import AppConfig


class LibraryConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "library"

    def ready(self):
        # Only sync in main process, not in migrations or management commands
        import sys

        if "runserver" in sys.argv or "db_worker" in sys.argv:
            from .models import System

            # Only sync on fresh DB (no systems exist)
            if not System.objects.exists():
                from .system_loader import sync_systems

                sync_systems()

            # Validate ScreenScraper credentials on startup
            self._validate_screenscraper_on_startup()

    def _validate_screenscraper_on_startup(self):
        """Validate ScreenScraper credentials if configured but not yet validated."""
        from .metadata.screenscraper import (
            screenscraper_available,
            get_credentials_valid,
            validate_credentials,
            set_credentials_valid,
        )
        import logging

        logger = logging.getLogger(__name__)

        if not screenscraper_available():
            return

        current_status = get_credentials_valid()
        if current_status is not None:
            return  # Already validated

        logger.info("Validating ScreenScraper credentials on startup...")
        is_valid, error = validate_credentials()
        set_credentials_valid(is_valid)

        if is_valid:
            logger.info("ScreenScraper credentials validated successfully.")
        else:
            logger.warning(f"ScreenScraper credentials invalid: {error}")
