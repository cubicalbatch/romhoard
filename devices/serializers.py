"""JSON serialization for device import/export."""

from typing import Any

from django.core.exceptions import ValidationError
from django.core.validators import validate_slug
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
    if not isinstance(device_data, dict):
        raise ImportError("Invalid format: 'device' must be an object")
    required_fields = ["slug", "name"]
    for field in required_fields:
        if field not in device_data:
            raise ImportError(f"Invalid format: missing required field '{field}'")

    for field in (
        "slug",
        "name",
        "description",
        "root_path",
        "image_type",
        "image_path_template",
    ):
        if field not in device_data:
            continue
        value = device_data[field]
        max_length = Device._meta.get_field(field).max_length
        if not isinstance(value, str) or "\x00" in value:
            raise ImportError(
                f"Invalid format: '{field}' must be a string without NUL characters"
            )
        if field in required_fields and not value.strip():
            raise ImportError(f"Invalid format: '{field}' must not be empty")
        if max_length and len(value) > max_length:
            raise ImportError(
                f"Invalid format: '{field}' exceeds {max_length} characters"
            )
    try:
        validate_slug(device_data["slug"])
    except ValidationError as exc:
        raise ImportError(
            "Invalid format: 'slug' must contain only letters, numbers, hyphens or underscores"
        ) from exc
    for field in ("has_wifi", "include_images"):
        if field in device_data and not isinstance(device_data[field], bool):
            raise ImportError(f"Invalid format: '{field}' must be a boolean")
    width = device_data.get("image_max_width")
    if width is not None and (type(width) is not int or not 0 <= width <= 2147483647):
        raise ImportError(
            "Invalid format: 'image_max_width' must be a nonnegative integer"
        )
    if device_data.get("image_type", "cover") not in dict(Device.IMAGE_TYPE_CHOICES):
        raise ImportError("Invalid format: unsupported 'image_type'")

    system_paths = device_data.get("system_paths", {})
    if not isinstance(system_paths, dict):
        raise ImportError("Invalid format: 'system_paths' must be an object")
    for system, config in system_paths.items():
        if not isinstance(system, str) or not isinstance(config, (str, dict)):
            raise ImportError(
                "Invalid format: each system path must be a string or object"
            )
        if isinstance(config, dict):
            if not isinstance(config.get("folder", ""), str):
                raise ImportError("Invalid format: system folder must be a string")
            if not isinstance(config.get("game_folders", False), bool):
                raise ImportError("Invalid format: game_folders must be a boolean")


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
