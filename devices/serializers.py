"""JSON serialization for device import/export."""

from typing import Any

from django.utils import timezone

from .models import Device

EXPORT_VERSION = "3.0"


def export_device(device: Device) -> dict[str, Any]:
    """Export a device to a portable JSON-serializable dict.

    Args:
        device: Device instance to export

    Returns:
        Dictionary ready for JSON serialization
    """
    return {
        "romhoard_device": {
            "version": EXPORT_VERSION,
            "exported_at": timezone.now().isoformat(),
        },
        "device": {
            "slug": device.slug,
            "name": device.name,
            "description": device.description,
            # ROM Organization
            "root_path": device.root_path,
            "system_paths": device.system_paths,
            # Network
            "has_wifi": device.has_wifi,
            # Image Configuration
            "include_images": device.include_images,
            "image_type": device.image_type,
            "image_path_template": device.image_path_template,
            "image_max_width": device.image_max_width,
            # Note: Transfer credentials intentionally excluded for security
        },
    }


class ImportError(Exception):
    """Raised when import validation fails."""

    pass


def validate_import_data(data: dict[str, Any]) -> None:
    """Validate import data structure.

    Args:
        data: Dictionary from JSON import

    Raises:
        ImportError: If validation fails
    """
    if not isinstance(data, dict):
        raise ImportError("Invalid format: expected JSON object")

    if "romhoard_device" not in data:
        raise ImportError("Invalid format: missing 'romhoard_device' header")

    header = data["romhoard_device"]
    if not isinstance(header, dict) or "version" not in header:
        raise ImportError("Invalid format: invalid header")

    if "device" not in data:
        raise ImportError("Invalid format: missing 'device' data")

    device_data = data["device"]
    required_fields = ["slug", "name"]
    for field in required_fields:
        if field not in device_data:
            raise ImportError(f"Invalid format: missing required field '{field}'")


def import_device(data: dict[str, Any], overwrite: bool = False) -> Device:
    """Import a device from JSON data.

    Args:
        data: Dictionary from JSON import
        overwrite: If True, overwrite existing device with same slug

    Returns:
        Device instance

    Raises:
        ImportError: If import fails
    """
    validate_import_data(data)

    device_data = data["device"]
    slug = device_data["slug"]

    existing = Device.objects.filter(slug=slug).first()
    if existing and not overwrite:
        raise ImportError(
            f"Device with slug '{slug}' already exists. "
            "Use overwrite=True to replace it."
        )

    if existing:
        device = existing
        device.name = device_data["name"]
        device.description = device_data.get("description", "")
        device.root_path = device_data.get("root_path", "Roms/")
        device.system_paths = device_data.get("system_paths", {})
        device.has_wifi = device_data.get("has_wifi", True)
        # Image Configuration
        device.include_images = device_data.get("include_images", False)
        device.image_type = device_data.get("image_type", "cover")
        device.image_path_template = device_data.get("image_path_template", "")
        device.image_max_width = device_data.get("image_max_width")
        device.save()
    else:
        device = Device.objects.create(
            slug=slug,
            name=device_data["name"],
            description=device_data.get("description", ""),
            root_path=device_data.get("root_path", "Roms/"),
            system_paths=device_data.get("system_paths", {}),
            has_wifi=device_data.get("has_wifi", True),
            # Image Configuration
            include_images=device_data.get("include_images", False),
            image_type=device_data.get("image_type", "cover"),
            image_path_template=device_data.get("image_path_template", ""),
            image_max_width=device_data.get("image_max_width"),
        )

    return device
