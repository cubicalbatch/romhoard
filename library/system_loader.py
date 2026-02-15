"""Load system definitions from config file."""

import json
from pathlib import Path


def get_systems_config() -> list[dict]:
    """Load systems from JSON config file."""
    config_path = Path(__file__).parent / "systems.json"
    with open(config_path) as f:
        data = json.load(f)
    return data.get("systems", [])


def sync_systems():
    """Sync systems from config to database."""
    from .models import System

    systems = get_systems_config()
    set(System.objects.values_list("slug", flat=True))
    {s["slug"] for s in systems}

    for system_data in systems:
        System.objects.update_or_create(
            slug=system_data["slug"],
            defaults={
                "name": system_data["name"],
                "extensions": system_data["extensions"],
                "exclusive_extensions": system_data.get("exclusive_extensions", []),
                "folder_names": system_data["folder_names"],
                "archive_as_rom": system_data.get("archive_as_rom", False),
                "screenscraper_ids": system_data.get("screenscraper_ids", []),
            },
        )

    # Optionally: Remove systems not in config (be careful with FK relationships)
    # Systems in DB but not in config are left as-is to preserve user data
