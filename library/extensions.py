"""Extension categorization and validation utilities.

This module provides utilities for:
- Categorizing file extensions (ROM, archive, non-ROM)
- Loading extension configurations
- Validating extensions against system requirements
"""

import json
from functools import lru_cache
from pathlib import Path

# Compressed extensions that can contain ROMs
COMPRESSED_EXTENSIONS = {".zip", ".7z"}

# Image extensions for game artwork
IMAGE_EXTENSIONS = {".png", ".jpg", ".jpeg", ".gif", ".bmp", ".webp"}

# Compound extensions that end with image suffix but are actually ROMs
COMPOUND_ROM_EXTENSIONS = {".p8.png"}


@lru_cache(maxsize=1)
def load_non_rom_extensions() -> set[str]:
    """Load non-ROM extensions from config file.

    Returns:
        Set of extensions that should never be treated as ROMs.
    """
    config_path = Path(__file__).parent / "non_rom_extensions.json"
    try:
        with open(config_path) as f:
            data = json.load(f)
            return {ext.lower() for ext in data.get("non_rom_extensions", [])}
    except (FileNotFoundError, json.JSONDecodeError):
        # Fallback to essential blocklist if config missing
        return {".png", ".jpg", ".jpeg", ".gif", ".txt", ".pdf", ".mp3", ".exe"}


def is_non_rom_extension(ext: str) -> bool:
    """Check if extension is a known non-ROM type.

    Args:
        ext: File extension including the dot (e.g., ".png")

    Returns:
        True if the extension is definitely not a ROM file.
    """
    return ext.lower() in load_non_rom_extensions()


def is_archive_extension(ext: str) -> bool:
    """Check if extension is a supported archive format.

    Args:
        ext: File extension including the dot (e.g., ".zip")

    Returns:
        True if the extension is a supported archive format.
    """
    return ext.lower() in COMPRESSED_EXTENSIONS


def is_image_extension(ext: str) -> bool:
    """Check if extension is an image format.

    Args:
        ext: File extension including the dot (e.g., ".png")

    Returns:
        True if the extension is an image format.
    """
    return ext.lower() in IMAGE_EXTENSIONS


def is_compound_rom_extension(filename: str) -> bool:
    """Check if file has a compound ROM extension.

    Compound extensions like .p8.png look like images but are actually ROMs.

    Args:
        filename: The filename to check

    Returns:
        True if the filename ends with a compound ROM extension.
    """
    filename_lower = filename.lower()
    return any(filename_lower.endswith(ext) for ext in COMPOUND_ROM_EXTENSIONS)


def get_full_extension(filename: str) -> str:
    """Get extension, handling compound extensions like .p8.png.

    Args:
        filename: The filename to extract extension from

    Returns:
        The full extension in lowercase (e.g., ".p8.png" or ".gba")
    """
    filename_lower = filename.lower()
    for compound in COMPOUND_ROM_EXTENSIONS:
        if filename_lower.endswith(compound):
            return compound
    return Path(filename).suffix.lower()


def is_acceptable_extension(ext: str, system) -> bool:
    """Check if extension is acceptable for a system.

    An extension is acceptable if:
    - It's in the system's extensions list, OR
    - It's an archive format (.zip, .7z)

    Args:
        ext: File extension including the dot
        system: System object with extensions attribute

    Returns:
        True if the extension is acceptable for this system.
    """
    ext_lower = ext.lower()

    # Archive formats are always acceptable
    if is_archive_extension(ext_lower):
        return True

    # Check against system's valid extensions
    system_exts = {e.lower() for e in system.extensions}
    return ext_lower in system_exts


def build_exclusive_extension_map(systems: list) -> dict:
    """Build map of exclusive extensions to systems.

    Uses the explicit exclusive_extensions field from system config.
    Compressed extensions (.zip, .7z) are never exclusive.

    Args:
        systems: List of System objects with exclusive_extensions attribute

    Returns:
        Dict mapping extension (str) to System object
    """
    exclusive_map = {}

    for system in systems:
        # Get exclusive_extensions from the system
        exclusive_exts = getattr(system, "exclusive_extensions", []) or []

        for ext in exclusive_exts:
            ext_lower = ext.lower()
            # Skip compressed extensions - they're never exclusive
            if ext_lower in COMPRESSED_EXTENSIONS:
                continue
            # First system to claim an extension wins
            if ext_lower not in exclusive_map:
                exclusive_map[ext_lower] = system

    return exclusive_map
