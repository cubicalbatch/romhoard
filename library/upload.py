"""Upload handling utilities for adding games to the library.

This module provides utilities for:
- Getting the configured library root path
- Managing temporary upload directories
- Detecting systems from file extensions
- Checking for duplicate ROMs
- Identifying ROMs by hash via Hasheous
"""

import logging
import os
import tempfile
from pathlib import Path
from typing import TYPE_CHECKING

from django.conf import settings

from .archive import compute_file_crc32, is_archive_file, list_archive_contents
from .extensions import (
    build_exclusive_extension_map,
    get_full_extension,
    is_non_rom_extension,
)
from .models import ROM, Setting

if TYPE_CHECKING:
    from .models import System

logger = logging.getLogger(__name__)


def get_library_root() -> str:
    """Get the configured library root for uploads.

    Returns:
        The library root path, or empty string if not configured.
    """
    try:
        setting = Setting.objects.get(key="library_root")
        return setting.value
    except Setting.DoesNotExist:
        # Fall back to ROM_LIBRARY_ROOT environment variable
        return getattr(settings, "ROM_LIBRARY_ROOT", "")


def get_upload_temp_dir() -> str:
    """Get temporary directory for uploads.

    Creates the directory if it doesn't exist.

    Returns:
        Path to the upload temp directory.
    """
    base = getattr(settings, "UPLOAD_TEMP_DIR", None)
    if not base:
        base = os.path.join(tempfile.gettempdir(), "romhoard_uploads")
    os.makedirs(base, exist_ok=True)
    return base


def compute_destination_path(filename: str, system_slug: str) -> str:
    """Compute final destination path for uploaded file.

    Args:
        filename: The filename to store
        system_slug: The system slug for subfolder

    Returns:
        Full path where the file should be stored.

    Raises:
        ValueError: If library_root is not configured.
    """
    library_root = get_library_root()
    if not library_root:
        raise ValueError("library_root setting not configured")

    return os.path.join(library_root, system_slug, filename)


def detect_system_from_extension(filename: str) -> "System | None":
    """Detect system from file extension only (no folder context).

    Uses exclusive extension mapping to identify systems.
    Only works for extensions that uniquely identify a system.

    Args:
        filename: The filename to check

    Returns:
        System object if detected, None otherwise.
    """
    from .models import System

    all_systems = list(System.objects.all())
    exclusive_map = build_exclusive_extension_map(all_systems)

    ext = get_full_extension(filename)
    if ext in exclusive_map:
        return exclusive_map[ext]

    return None


def check_duplicate(filename: str, system: "System") -> bool:
    """Check if ROM already exists in library for this system.

    Args:
        filename: The filename to check
        system: The system to check against

    Returns:
        True if a ROM with this filename exists for this system.
    """
    return ROM.objects.filter(file_name=filename, rom_set__game__system=system).exists()


def build_extension_map_for_frontend() -> dict[str, str]:
    """Build extension to system slug map for frontend preview.

    Returns:
        Dict mapping extension (e.g., ".gba") to system slug (e.g., "gba")
    """
    from .models import System

    all_systems = list(System.objects.all())
    exclusive_map = build_exclusive_extension_map(all_systems)

    return {ext: system.slug for ext, system in exclusive_map.items()}


def ensure_destination_dir(system_slug: str) -> str:
    """Ensure destination directory exists for a system.

    Args:
        system_slug: The system slug

    Returns:
        Path to the system directory.

    Raises:
        ValueError: If library_root is not configured.
    """
    library_root = get_library_root()
    if not library_root:
        raise ValueError("library_root setting not configured")

    dest_dir = os.path.join(library_root, system_slug)
    os.makedirs(dest_dir, exist_ok=True)
    return dest_dir


def get_unique_filepath(dest_dir: str, filename: str) -> str:
    """Get a unique filepath, appending _1, _2 etc if file exists.

    Args:
        dest_dir: Destination directory
        filename: Desired filename

    Returns:
        Path that doesn't already exist.
    """
    dest_path = os.path.join(dest_dir, filename)

    if not os.path.exists(dest_path):
        return dest_path

    base, ext = os.path.splitext(filename)
    counter = 1
    while os.path.exists(dest_path):
        dest_path = os.path.join(dest_dir, f"{base}_{counter}{ext}")
        counter += 1

    return dest_path


def identify_system_by_hash(
    crc32: str = "",
    sha1: str = "",
    md5: str = "",
) -> "System | None":
    """Identify system from ROM hash via Hasheous lookup.

    Unlike the regular lookup functions, this doesn't require a system
    upfront - it returns the system identified from the Hasheous response.

    Args:
        crc32: CRC32 hash (8 hex chars)
        sha1: SHA1 hash (40 hex chars)
        md5: MD5 hash (32 hex chars)

    Returns:
        System object if identified, None otherwise.
    """
    from .lookup.hasheous import PLATFORM_TO_SLUG, HasheousLookupService
    from .models import System

    service = HasheousLookupService()

    # Try each hash type in order of reliability
    for hash_type, hash_value in [("sha1", sha1), ("md5", md5), ("crc32", crc32)]:
        if not hash_value:
            continue

        # Use internal _api_lookup to get raw response
        if hash_type == "sha1":
            result = service._api_lookup(sha1=hash_value)
        elif hash_type == "md5":
            result = service._api_lookup(md5=hash_value)
        else:
            result = service._api_lookup(crc=hash_value)

        if not result:
            continue

        # Extract platform from response and map to our system
        platform_name = result.get("platform", {}).get("name", "")
        if not platform_name:
            continue

        system_slug = PLATFORM_TO_SLUG.get(platform_name)
        if not system_slug:
            logger.debug(
                "Hasheous returned unknown platform '%s' for hash %s",
                platform_name,
                hash_value[:8],
            )
            continue

        try:
            system = System.objects.get(slug=system_slug)
            logger.info(
                "Identified system '%s' from %s=%s via Hasheous",
                system.name,
                hash_type,
                hash_value[:8],
            )
            return system
        except System.DoesNotExist:
            logger.warning(
                "System slug '%s' from Hasheous not found in database",
                system_slug,
            )
            continue

    return None


def identify_rom_by_hash(file_path: str) -> "System | None":
    """Identify system for a ROM file via hash lookup.

    For CHD files, extracts the internal SHA1 hash (same as scanner.py).
    For other files, computes CRC32.

    Args:
        file_path: Path to the ROM file

    Returns:
        System object if identified, None otherwise.
    """
    from .chd import extract_chd_sha1, is_chd_file

    try:
        filename = os.path.basename(file_path)

        # CHD files need internal SHA1 (same pattern as scanner.py:1010-1018)
        if is_chd_file(filename):
            sha1 = extract_chd_sha1(file_path)
            if sha1:
                return identify_system_by_hash(sha1=sha1)
            logger.debug("Could not extract SHA1 from CHD: %s", filename)
            return None

        # Regular files use CRC32
        crc32 = compute_file_crc32(file_path)
        return identify_system_by_hash(crc32=crc32)
    except Exception as e:
        logger.warning("Failed to compute hash for %s: %s", file_path, e)
        return None


def detect_systems_from_archive(
    archive_path: str,
) -> list[tuple[str, "System", str]]:
    """Detect systems for all ROM files inside an archive.

    Examines each file in the archive and attempts to identify its system
    using extension matching first, then Hasheous CRC32 lookup as fallback.

    Args:
        archive_path: Path to the archive file (.zip or .7z)

    Returns:
        List of (path_in_archive, system, crc32) for each identified ROM.
        Files that cannot be identified are not included.
    """
    from .models import System

    try:
        contents = list_archive_contents(archive_path)
    except Exception as e:
        logger.warning("Failed to read archive %s: %s", archive_path, e)
        return []

    # Build exclusive extension map once
    all_systems = list(System.objects.all())
    exclusive_map = build_exclusive_extension_map(all_systems)

    identified: list[tuple[str, System, str]] = []

    for item in contents:
        filename = Path(item.name).name
        ext = get_full_extension(filename)

        # Skip non-ROM files
        if is_non_rom_extension(ext):
            continue

        # Skip nested archives
        if is_archive_file(filename):
            continue

        system = None
        crc32 = item.crc32 or ""

        # Try exclusive extension first
        if ext in exclusive_map:
            system = exclusive_map[ext]
            logger.debug(
                "Identified %s in archive via extension '%s' -> %s",
                filename,
                ext,
                system.slug,
            )
        else:
            # Try Hasheous CRC32 lookup
            if crc32:
                system = identify_system_by_hash(crc32=crc32)
                if system:
                    logger.debug(
                        "Identified %s in archive via CRC32=%s -> %s",
                        filename,
                        crc32[:8],
                        system.slug,
                    )

        if system:
            identified.append((item.name, system, crc32))

    return identified
