"""Load device presets from individual JSON files."""

import json
from pathlib import Path


def get_presets_config() -> list[dict]:
    """Load presets from device_presets/*.json directory."""
    presets_dir = Path(__file__).parent / "device_presets"
    if not presets_dir.exists():
        return []

    presets = []
    for json_file in sorted(presets_dir.glob("*.json")):
        with open(json_file) as f:
            preset_data = json.load(f)
            presets.append(preset_data)

    return presets


def sync_presets() -> int:
    """Sync presets from config to database.

    Returns:
        Number of presets synced.
    """
    from .models import DevicePreset

    presets = get_presets_config()

    for preset_data in presets:
        DevicePreset.objects.update_or_create(
            slug=preset_data["slug"],
            defaults={
                "name": preset_data["name"],
                "description": preset_data.get("description", ""),
                "tags": preset_data.get("tags", []),
                "is_builtin": True,
                "folders_config": preset_data.get("folders"),
                "images_config": preset_data.get("images"),
                "transfer_config": preset_data.get("transfer"),
            },
        )

    return len(presets)
