"""ROM directory scanner.

Scans directories for ROM files and adds them to the database.
"""

import os
import re
from pathlib import Path
from typing import Callable, Optional

from django.conf import settings
from django.db import IntegrityError

from . import archive as archive_utils
from .chd import extract_chd_sha1, is_chd_file
from .extensions import (
    build_exclusive_extension_map,
    get_full_extension,
    is_acceptable_extension,
    is_compound_rom_extension,
    is_non_rom_extension,
    COMPRESSED_EXTENSIONS,
    IMAGE_EXTENSIONS,
)
from .lookup import lookup_rom, LookupResult
from .merge import find_existing_game
from .models import Game, GameImage, ROM, ROMSet, System
from .parser import (
    get_switch_content_info,
    parse_rom_filename,
)
import logging

logger = logging.getLogger(__name__)


def get_library_root() -> str:
    """Get the configured library root path."""
    return getattr(settings, "ROM_LIBRARY_ROOT", "")


def to_storage_path(absolute_path: str) -> str:
    """Convert an absolute path to a storage path.

    If ROM_LIBRARY_ROOT is set, returns a relative path.
    Otherwise returns the absolute path unchanged.
    """
    library_root = get_library_root()
    if not library_root:
        return absolute_path

    # Normalize the library root
    library_root = os.path.abspath(library_root)
    if not library_root.endswith(os.sep):
        library_root += os.sep

    # Convert to relative if under library root
    if absolute_path.startswith(library_root):
        return absolute_path[len(library_root) :]

    return absolute_path


def to_absolute_path(storage_path: str) -> str:
    """Convert a storage path to an absolute path.

    If ROM_LIBRARY_ROOT is set and path is relative, prepends the root.
    Otherwise returns the path unchanged.
    """
    library_root = get_library_root()
    if not library_root:
        return storage_path

    if os.path.isabs(storage_path):
        return storage_path

    return os.path.join(library_root, storage_path)


def is_bios_file(filename: str, file_path: str) -> bool:
    """
    Check if a file is a BIOS file based on filename or path.

    Args:
        filename: The file name (e.g., "bios.bin")
        file_path: The full file path (e.g., "/roms/snes/bios/bios.bin")

    Returns:
        True if the file is a BIOS file, False otherwise
    """
    # Check if filename starts with "bios" (case-insensitive)
    if filename.lower().startswith("bios"):
        return True

    # Check if any directory component in the path is "bios" (case-insensitive)
    path = Path(file_path)
    for part in path.parts:
        if part.lower() == "bios":
            return True

    return False


def detect_image_type(file_path: str) -> str:
    """Detect image type from filepath patterns."""
    path_lower = file_path.lower()

    if "mix" in path_lower:
        return "mix"

    has_cover = "box" in path_lower or "cover" in path_lower
    has_screenshot = "screenshot" in path_lower

    if has_cover and has_screenshot:
        return "mix"
    if has_cover:
        return "cover"
    if has_screenshot:
        return "screenshot"

    return ""


def build_extension_map(systems: list) -> dict:
    """Build map of exclusive extensions to systems.

    Uses explicit exclusive_extensions field from system config.

    Args:
        systems: List of System objects

    Returns:
        dict mapping extension to System for exclusive extensions only
    """
    return build_exclusive_extension_map(systems)


def match_by_folder(path: Path, systems: list) -> Optional[System]:
    """
    Match system by folder name in path.

    Args:
        path: Path object for the file
        systems: List of System objects

    Returns:
        System instance or None if no match
    """
    for system in systems:
        folder_names_lower = [f.lower() for f in system.folder_names]
        # Check each path component (excluding filename)
        for part in path.parts[:-1]:
            if part.lower() in folder_names_lower:
                return system
    return None


def detect_system_for_archived_file(
    archive_path: str,
    internal_path: str,
    systems_cache: list,
    exclusive_map: dict,
) -> Optional[System]:
    """
    Detect system for a file inside an archive.

    Layered detection strategy:
    1. Skip non-ROM extensions early (e.g., screenshots inside archive)
    2. Exclusive extension - definitive match without folder context
    3. Internal folder match + acceptable extension
    4. Archive folder match + acceptable extension

    Args:
        archive_path: Path to the archive file
        internal_path: Path to the file within the archive
        systems_cache: List of System objects
        exclusive_map: Map of exclusive extensions to systems

    Returns:
        System instance or None if no match
    """
    extension = get_full_extension(Path(internal_path).name)

    if not extension:
        return None

    # Skip non-ROM extensions early (e.g., screenshots, docs inside archives)
    if is_non_rom_extension(extension):
        return None

    # Layer 1: Exclusive extension (highest priority)
    if extension in exclusive_map:
        matched_system = exclusive_map[extension]
        logger.debug(
            "Matched %s!%s to system %s (exclusive extension %s)",
            Path(archive_path).name,
            internal_path,
            matched_system.name,
            extension,
        )
        return matched_system

    # Layer 2: Internal folder match + acceptable extension
    internal_system = match_by_folder(Path(internal_path), systems_cache)
    if internal_system:
        if is_acceptable_extension(extension, internal_system):
            logger.debug(
                "Matched %s!%s to system %s (internal folder + acceptable extension)",
                Path(archive_path).name,
                internal_path,
                internal_system.name,
            )
            return internal_system
        # Unknown extension - skip with debug log
        logger.debug(
            "Skipped %s!%s: unknown extension %s in %s folder",
            Path(archive_path).name,
            internal_path,
            extension,
            internal_system.name,
        )
        return None

    # Layer 3: Archive folder match + acceptable extension
    archive_system = match_by_folder(Path(archive_path), systems_cache)
    if archive_system:
        if is_acceptable_extension(extension, archive_system):
            logger.debug(
                "Matched %s!%s to system %s (archive folder + acceptable extension)",
                Path(archive_path).name,
                internal_path,
                archive_system.name,
            )
            return archive_system
        # Unknown extension - skip with debug log
        logger.debug(
            "Skipped %s!%s: unknown extension %s in %s folder",
            Path(archive_path).name,
            internal_path,
            extension,
            archive_system.name,
        )
        return None

    # Can't determine system
    return None


def detect_system(
    file_path: str,
    systems_cache: list,
    exclusive_map: dict,
) -> Optional[System]:
    """
    Detect the system for a ROM file.

    Layered detection strategy:
    1. Exclusive extension - definitive match without folder context
    2. Folder match + acceptable extension - match if extension is valid for system
    3. Folder match + non-ROM extension - skip (e.g., screenshots)
    4. Folder match + unknown extension - skip with debug log

    Args:
        file_path: Absolute path to the ROM file
        systems_cache: List of System objects
        exclusive_map: Map of exclusive extensions to systems

    Returns:
        System instance or None if no match
    """
    path = Path(file_path)
    extension = get_full_extension(path.name)

    if not extension:
        return None

    # Layer 1: Exclusive extension (highest priority after hash lookup)
    if extension in exclusive_map:
        matched_system = exclusive_map[extension]
        logger.debug(
            "Matched %s to system %s (exclusive extension %s)",
            file_path,
            matched_system.name,
            extension,
        )
        return matched_system

    # Layer 2: Folder match with extension validation
    folder_system = match_by_folder(path, systems_cache)
    if folder_system:
        # Check if extension is acceptable for this system
        if is_acceptable_extension(extension, folder_system):
            logger.debug(
                "Matched %s to system %s (folder + acceptable extension)",
                file_path,
                folder_system.name,
            )
            return folder_system

        # Check if it's a known non-ROM extension (skip silently)
        if is_non_rom_extension(extension):
            return None

        # Unknown extension in system folder - skip with debug log
        logger.debug(
            "Skipped %s: unknown extension %s in %s folder",
            file_path,
            extension,
            folder_system.name,
        )
        return None

    # No folder match and not exclusive extension -> can't identify
    return None


def normalize_name_for_matching(name: str) -> str:
    """Normalize a name for comparison by lowercasing and removing punctuation."""
    # Convert to lowercase and replace underscores with spaces
    name = name.lower().replace("_", " ")
    # Remove punctuation except spaces
    name = re.sub(r"[^\w\s]", "", name)
    # Normalize whitespace
    name = re.sub(r"\s+", " ", name).strip()
    return name


def match_image_to_game(image_name: str, system: System) -> Optional[Game]:
    """
    Find a game matching the image filename using smart matching.

    Matching strategy (in order of priority):
    1. Exact normalized match (case-insensitive)
    2. Game name starts with image name at word boundary
       - Allows "Zelda" to match "Zelda - A Link to the Past"
       - Still prevents "Mario" matching "Mario Kart" (see below)
    3. Image name starts with game name at word boundary

    The key insight is that image names are typically shortened versions of
    game names ("Zelda" for "Zelda - A Link to the Past"), not ambiguous
    prefixes. We allow these matches but verify it's the best match.

    Args:
        image_name: Image filename without extension (e.g., "Wario Land 4")
        system: System to search within

    Returns:
        Game instance or None if no match
    """
    normalized_image = normalize_name_for_matching(image_name)
    games = list(Game.objects.filter(system=system))

    # Strategy 1: Exact normalized match (highest priority)
    for game in games:
        if normalize_name_for_matching(game.name) == normalized_image:
            return game

    # Strategy 2 & 3: Prefix matching with scoring
    # Find all matches and pick the best one
    matches = []

    for game in games:
        game_norm = normalize_name_for_matching(game.name)

        # Check if game name starts with image name
        if game_norm.startswith(normalized_image):
            remainder = game_norm[len(normalized_image) :]
            # Must be word boundary (empty or space)
            if not remainder or remainder.startswith(" "):
                # Score based on how much of the game name is matched
                score = len(normalized_image) / len(game_norm)
                matches.append((game, score))

        # Check if image name starts with game name
        elif normalized_image.startswith(game_norm):
            remainder = normalized_image[len(game_norm) :]
            if not remainder or remainder.startswith(" "):
                score = len(game_norm) / len(normalized_image)
                matches.append((game, score))

    # Return the best match (highest score = closest match)
    if matches:
        matches.sort(key=lambda x: x[1], reverse=True)
        return matches[0][0]

    return None


def get_source_path(
    file_path: str, archive_path: str = "", path_in_archive: str = ""
) -> str:
    """Get source identifier for ROMSet grouping.

    For archived ROMs: archive_path + parent folder within archive
    For loose files: parent directory

    This ensures ROMs in different folders within an archive get separate ROMSets,
    while multi-disc games in the same folder stay grouped.
    """
    if archive_path:
        if path_in_archive:
            parent_in_archive = os.path.dirname(path_in_archive)
            if parent_in_archive:
                return f"{archive_path}/{parent_in_archive}"
        return archive_path
    return os.path.dirname(file_path)


def get_or_create_rom_set(
    name: str,
    system: System,
    region: str,
    revision: str,
    source_path: str = "",
    crc32: str = "",
    sha1: str = "",
    file_path: str = "",
    use_hasheous: bool = False,
    fetch_metadata: bool = True,
    identify_later: bool = True,
) -> tuple[ROMSet, LookupResult | None, bool, bool]:
    """
    Find or create a Game and ROMSet.

    Args:
        name: Game name (from parsed filename)
        system: System instance
        region: Region string (may be empty)
        revision: Revision string (may be empty)
        source_path: Source identifier for grouping (archive path or parent dir)
        crc32: Optional CRC32 hash for lookup
        sha1: Optional SHA1 hash for lookup (used for CHD files)
        file_path: Path to ROM file (for romnom lookup fallback)
        use_hasheous: Enable Hasheous API lookup as fallback
        fetch_metadata: Auto-queue ScreenScraper metadata fetch for new games
        identify_later: If True, skip hash lookup during scan and queue for later

    Returns:
        Tuple of (ROMSet, LookupResult or None if no match, metadata_queued bool,
                  identification_needed bool)
    """
    # Try hash-based lookup first if we have any hash or file_path
    # The lookup chain will try Hasheous first, then ScreenScraper (CRC + romnom)
    # Skip lookup if identify_later=True (for parallel identification after scan)
    name_source = Game.SOURCE_FILENAME
    result: LookupResult | None = None
    screenscraper_id: int | None = None
    identification_needed = False

    if not identify_later and (crc32 or sha1 or file_path):
        result = lookup_rom(
            system=system,
            crc32=crc32,
            sha1=sha1,
            file_path=file_path,
            use_hasheous=use_hasheous,
        )
        if result:
            original_name = name  # Preserve for logging
            name = result.name
            # Keep filename-parsed region/revision if lookup returns empty
            region = result.region or region
            revision = result.revision or revision
            # Use source from lookup result
            name_source = result.source
            # Capture screenscraper_id if present (from ScreenScraper lookup)
            screenscraper_id = result.screenscraper_id
            hash_info = f"SHA1: {sha1}" if sha1 else f"CRC32: {crc32}"
            logger.info(
                f"Lookup match ({result.source}) for {system.slug}: "
                f"'{original_name}' -> '{name}' [{region}] ({hash_info})"
            )
    elif identify_later and (crc32 or sha1 or file_path):
        # Mark for later identification
        identification_needed = True

    # Find existing game by hash, screenscraper_id, or case-insensitive name
    # This prevents duplicates like "galaga" vs "Galaga"
    game = find_existing_game(
        name=name,
        system=system,
        crc32=crc32,
        sha1=sha1,
        screenscraper_id=screenscraper_id,
    )

    # Handle game creation with retry logic for race conditions.
    # Multiple ROM files can map to the same game (same screenscraper_id or name),
    # causing IntegrityError on the unique constraints. Catch and retry.
    game_created = False
    try:
        if game:
            logger.debug(f"Found existing game: {game} (pk={game.pk})")
        else:
            # Create new game
            game = Game.objects.create(
                name=name, system=system, name_source=name_source
            )
            game_created = True
            logger.debug(f"Created new game: {game} (pk={game.pk})")

        # Update name_source if this is a new game or if we have a hash-based match
        # (anything except filename parsing, screenscraper, or manual entry)
        non_hash_sources = {
            Game.SOURCE_FILENAME,
            Game.SOURCE_SCREENSCRAPER,
            Game.SOURCE_MANUAL,
        }
        needs_save = False
        if not game_created and name_source not in non_hash_sources:
            # Existing game but we have a better name source - update it
            game.name_source = name_source
            needs_save = True

        # Update screenscraper_id if provided (from ScreenScraper lookup)
        if screenscraper_id and not game.screenscraper_id:
            game.screenscraper_id = screenscraper_id
            needs_save = True

        if needs_save:
            game.save()

    except IntegrityError:
        # Race condition: another ROM file created the game or set screenscraper_id
        # Re-query to find the existing game
        logger.debug(f"IntegrityError creating game '{name}' - finding existing game")
        game = find_existing_game(
            name=name, system=system, screenscraper_id=screenscraper_id
        )
        if not game and screenscraper_id:
            # Fallback: direct lookup by screenscraper_id (constraint violation source)
            game = Game.objects.filter(
                screenscraper_id=screenscraper_id, system=system
            ).first()
        if not game:
            # Last resort: exact name match (unique_together violation source)
            game = Game.objects.filter(name=name, system=system).first()
        if not game:
            # Should not happen, but raise if we still can't find it
            raise
        game_created = False

    # Find or create rom set for this region/revision/source
    rom_set, rom_set_created = ROMSet.objects.get_or_create(
        game=game,
        region=region,
        revision=revision,
        source_path=source_path,
    )

    # Note: default ROMSet recalculation happens AFTER ROM creation in the caller,
    # since the ROMSet needs at least one ROM for scoring to work.

    # Auto-queue metadata fetch for new games or existing games without metadata
    # The credential check is now consolidated in queue_game_metadata()
    metadata_queued = False
    if fetch_metadata:
        # Queue if: new game OR ROM added to existing game without screenscraper_id
        if game_created or (not game.screenscraper_id and rom_set_created):
            from library.tasks import queue_game_metadata

            metadata_queued = queue_game_metadata(game)

    return rom_set, result, metadata_queued, identification_needed


def filter_rom_files_in_archive(
    contents: list[archive_utils.ArchiveInfo], system: System
) -> list[archive_utils.ArchiveInfo]:
    """
    Filter archive contents to valid ROM files for a system.

    Args:
        contents: List of ArchiveInfo objects from archive
        system: System to filter for

    Returns:
        List of ArchiveInfo objects that are valid ROM files
    """
    valid = []
    for item in contents:
        # Skip nested archives - treat them as opaque files
        if archive_utils.is_nested_archive(item.name):
            continue

        # Check if extension is valid for this system
        ext = get_full_extension(item.name)
        if ext in system.extensions:
            valid.append(item)

    return valid


def should_expand_archive(rom_files: list[archive_utils.ArchiveInfo]) -> bool:
    """
    Determine if archive contains multiple different games.

    Compares parsed game names from filenames. If all ROMs share the same
    game name, they're treated as a single game (e.g., multi-disc).

    Args:
        rom_files: List of ArchiveInfo objects for ROM files

    Returns:
        True if archive should be expanded (multiple unique game names)
        False if single game (keep archive as one ROM)
    """
    if len(rom_files) <= 1:
        return False

    game_names = set()
    for item in rom_files:
        # Parse just the filename, not the full path within archive
        filename = Path(item.name).name
        parsed = parse_rom_filename(filename)
        game_names.add(parsed["name"].lower().strip())

    # Multiple unique names = expand archive
    return len(game_names) > 1


def create_rom_from_archive_as_rom(
    archive_path: str,
    system: System,
    seen_paths: set,
    use_hasheous: bool = False,
    fetch_metadata: bool = True,
) -> dict:
    """Create ROM entry for an archive that IS the ROM (e.g., MAME ZIPs)."""
    results = {"added": 0, "skipped": 0, "errors": [], "metadata_queued": 0, "added_rom_ids": []}

    seen_paths.add(archive_path)

    # Skip if already exists
    if ROM.objects.filter(file_path=archive_path, archive_path="").exists():
        logger.debug("Skipped existing archive-as-ROM: %s", archive_path)
        results["skipped"] = 1
        return results

    filename = Path(archive_path).name
    parsed = parse_rom_filename(filename)

    try:
        file_size = os.path.getsize(archive_path)
    except OSError as e:
        results["errors"].append(f"Cannot read size: {archive_path}: {e}")
        return results

    # Compute CRC32 for the archive file itself
    try:
        crc32 = archive_utils.compute_file_crc32(archive_path)
    except Exception:
        crc32 = ""

    # For arcade systems (archive_as_rom), the lookup chain will use romnom
    # to identify games via ScreenScraper. The ScreenScraperLookupService
    # handles this automatically when file_path is provided.
    rom_set, lookup_result, metadata_queued, _ = get_or_create_rom_set(
        parsed["name"],
        system,
        parsed["region"],
        parsed["revision"],
        source_path=archive_path,  # Archive itself is the source
        crc32=crc32,
        file_path=archive_path,
        use_hasheous=use_hasheous,
        fetch_metadata=fetch_metadata,
    )

    # Use matched internal CRC32 if available, otherwise use archive's CRC32
    rom_crc32 = crc32
    if lookup_result and lookup_result.matched_crc32:
        rom_crc32 = lookup_result.matched_crc32
        logger.debug(
            "Using matched internal CRC32: %s (instead of archive: %s)",
            rom_crc32,
            crc32,
        )

    # Extract Switch content type if applicable
    switch_title_id, content_type = (
        get_switch_content_info(filename) if system.slug == "switch" else ("", "")
    )

    rom = ROM.objects.create(
        rom_set=rom_set,
        file_path=archive_path,
        file_name=filename,
        file_size=file_size,
        crc32=rom_crc32,
        tags=parsed["tags"],
        rom_number=parsed["rom_number"],
        disc=parsed["disc"],
        content_type=content_type,
        switch_title_id=switch_title_id,
    )
    results["added_rom_ids"].append(rom.pk)

    # Recalculate default ROMSet now that a ROM exists
    from .romset_scoring import recalculate_default_romset

    recalculate_default_romset(rom_set.game)

    logger.debug(
        "Added archive-as-ROM: %s (system: %s, crc32: %s)",
        filename,
        system.name,
        rom_crc32 or "(none)",
    )
    results["added"] = 1
    if metadata_queued:
        results["metadata_queued"] = 1
    return results


def create_archived_rom(
    archive_path: str,
    path_in_archive: str,
    file_size: int,
    system: System,
    seen_paths: set,
    use_archive_as_filename: bool = False,
    display_filename: str | None = None,
    crc32: str = "",
    parsing_filename: str | None = None,
    use_hasheous: bool = False,
    fetch_metadata: bool = True,
) -> dict:
    """
    Create a ROM record for a file inside an archive.

    Args:
        archive_path: Path to the archive file
        path_in_archive: Path to ROM within archive
        file_size: Uncompressed size of the ROM
        system: System instance
        seen_paths: Set of paths seen in scan (for deduplication)
        use_archive_as_filename: If True, display archive name instead of internal filename
        display_filename: If provided, override computed filename with this value
        crc32: Pre-computed CRC32 hash (from archive header if available)
        parsing_filename: If provided, use this exact filename for parsing instead of deriving from path_in_archive

    Returns:
        dict with keys: added (0 or 1), skipped (0 or 1)
    """
    # Composite path for uniqueness checking
    composite_path = f"{archive_path}!{path_in_archive}"
    seen_paths.add(composite_path)

    # Skip if already exists
    if ROM.objects.filter(
        archive_path=archive_path, path_in_archive=path_in_archive
    ).exists():
        logger.debug("Skipped existing archived ROM: %s", composite_path)
        return {"added": 0, "skipped": 1, "metadata_queued": 0, "added_rom_ids": []}

    # Parse filename (use internal file's name for metadata)
    if parsing_filename:
        internal_filename = Path(parsing_filename).name
    else:
        internal_filename = Path(path_in_archive).name

    parsed = parse_rom_filename(internal_filename)

    # Determine display filename
    if display_filename:
        final_display_filename = display_filename
    elif use_archive_as_filename:
        final_display_filename = Path(archive_path).name
    else:
        final_display_filename = internal_filename

    # Compute CRC32 if not already provided
    if not crc32:
        crc32 = (
            archive_utils.compute_archived_file_crc32(archive_path, path_in_archive)
            or ""
        )

    # Find or create rom set (hash lookup happens here)
    rom_set, _, metadata_queued, _ = get_or_create_rom_set(
        parsed["name"],
        system,
        parsed["region"],
        parsed["revision"],
        source_path=get_source_path("", archive_path, path_in_archive),
        crc32=crc32,
        file_path=archive_path,
        use_hasheous=use_hasheous,
        fetch_metadata=fetch_metadata,
    )

    # Extract Switch content type if applicable
    switch_title_id, content_type = (
        get_switch_content_info(internal_filename) if system.slug == "switch" else ("", "")
    )

    # Create ROM record with archive info
    rom = ROM.objects.create(
        rom_set=rom_set,
        file_path=composite_path,  # Logical path for display
        file_name=final_display_filename,
        file_size=file_size,
        archive_path=archive_path,
        path_in_archive=path_in_archive,
        crc32=crc32,
        tags=parsed["tags"],
        rom_number=parsed["rom_number"],
        disc=parsed["disc"],
        content_type=content_type,
        switch_title_id=switch_title_id,
    )

    # Recalculate default ROMSet now that a ROM exists
    from .romset_scoring import recalculate_default_romset

    recalculate_default_romset(rom_set.game)

    logger.debug(
        "Added archived ROM: %s (game: %s, system: %s, region: %s, crc32: %s)",
        final_display_filename,
        rom_set.game.name,
        system.name,
        rom_set.region or "(none)",
        crc32 or "(none)",
    )

    return {"added": 1, "skipped": 0, "metadata_queued": 1 if metadata_queued else 0, "added_rom_ids": [rom.pk]}


def process_archive(
    archive_path: str,
    systems_cache: list,
    exclusive_map: dict,
    seen_paths: set,
    use_hasheous: bool = False,
    fetch_metadata: bool = True,
    progress_state: dict | None = None,
) -> dict:
    """
    Process an archive file, potentially extracting multiple ROMs.

    Strategy:
    1. Check if archive should be treated as ROM (e.g., Arcade/MAME)
    2. List archive contents first (always open archives now)
    3. Filter contents with per-file system detection (skip nested archives)
    4. If all ROMs are same game -> treat archive as single ROM
    5. If ROMs are different games -> import each individually

    Args:
        archive_path: Path to archive file
        systems_cache: List of all System objects
        exclusive_map: Map of exclusive extensions to systems
        seen_paths: Set of paths seen in scan
        use_hasheous: Enable Hasheous API lookup as fallback
        fetch_metadata: Auto-queue ScreenScraper metadata for new games
        progress_state: Optional dict for tracking progress with keys:
            - files_processed: current count (will be incremented)
            - callback: function to call with progress updates
            - roms_found: current ROMs found count
            - images_found: current images found count
            - current_directory: current directory being scanned

    Returns:
        dict with keys: added, skipped, errors, metadata_queued, added_rom_ids
    """
    results = {"added": 0, "skipped": 0, "errors": [], "metadata_queued": 0, "added_rom_ids": []}

    # Check if archive should be treated as ROM (e.g., Arcade/MAME)
    system = detect_system(archive_path, systems_cache, exclusive_map)
    if system and system.archive_as_rom:
        # Treat archive itself as the ROM, don't look inside
        return create_rom_from_archive_as_rom(
            archive_path=archive_path,
            system=system,
            seen_paths=seen_paths,
            use_hasheous=use_hasheous,
            fetch_metadata=fetch_metadata,
        )

    # List archive contents first (we always open archives now)
    try:
        contents = archive_utils.list_archive_contents(archive_path)
    except Exception as e:
        logger.error("Failed to read archive %s: %s", archive_path, e)
        results["errors"].append(f"Failed to read archive {archive_path}: {e}")
        return results

    # Filter to valid ROM files, skipping nested archives
    regular_files = []
    for item in contents:
        # Skip nested archives (zip-in-zip) - too expensive to extract
        if archive_utils.is_nested_archive(item.name):
            continue

        # Check if this is a valid ROM file
        system = detect_system_for_archived_file(
            archive_path, item.name, systems_cache, exclusive_map
        )
        if system:
            regular_files.append((item, system))

    # Process regular ROM files
    if not regular_files:
        return results

    # Extract just the ArchiveInfo objects for should_expand_archive check
    rom_files = [item for item, _ in regular_files]

    # Helper to update progress for each archive entry processed
    def update_archive_progress():
        if progress_state and progress_state.get("callback"):
            progress_state["files_processed"] += 1
            progress_state["callback"](
                {
                    "files_processed": progress_state["files_processed"],
                    "roms_found": progress_state.get("roms_found", 0),
                    "images_found": progress_state.get("images_found", 0),
                    "current_directory": progress_state.get("current_directory", ""),
                }
            )

    # Check if we should expand the archive (multiple games)
    if should_expand_archive(rom_files):
        # Multiple games - create individual ROM entries
        for item, system in regular_files:
            # Update progress for each archive entry
            update_archive_progress()

            result = create_archived_rom(
                archive_path=archive_path,
                path_in_archive=item.name,
                file_size=item.size,  # Uncompressed size
                system=system,
                seen_paths=seen_paths,
                crc32=item.crc32,  # Use CRC32 from archive header if available
                parsing_filename=item.name,
                use_hasheous=use_hasheous,
                fetch_metadata=fetch_metadata,
            )
            results["added"] += result["added"]
            results["skipped"] += result["skipped"]
            results["metadata_queued"] += result.get("metadata_queued", 0)
            results["added_rom_ids"].extend(result.get("added_rom_ids", []))

            # Update roms_found in progress_state for next callback
            if progress_state:
                progress_state["roms_found"] = (
                    progress_state.get("roms_found", 0) + result["added"]
                )
    else:
        # Single game - create one ROM entry using the first file
        # Still count all files in archive as processed for accurate progress
        if progress_state:
            # Count all entries but only create one ROM
            for _ in regular_files:
                update_archive_progress()

        first_item, first_system = regular_files[0]
        result = create_archived_rom(
            archive_path=archive_path,
            path_in_archive=first_item.name,
            file_size=first_item.size,  # Uncompressed size
            system=first_system,
            seen_paths=seen_paths,
            use_archive_as_filename=True,  # Display archive name instead of internal filename
            crc32=first_item.crc32,  # Use CRC32 from archive header if available
            parsing_filename=first_item.name,
            use_hasheous=use_hasheous,
            fetch_metadata=fetch_metadata,
        )
        results["added"] += result["added"]
        results["skipped"] += result["skipped"]
        results["metadata_queued"] += result.get("metadata_queued", 0)
        results["added_rom_ids"].extend(result.get("added_rom_ids", []))

        # Update roms_found in progress_state
        if progress_state:
            progress_state["roms_found"] = (
                progress_state.get("roms_found", 0) + result["added"]
            )

    return results


def scan_directory(
    base_path: str,
    progress_callback: Optional[Callable[[dict], None]] = None,
    use_hasheous: bool = False,
    fetch_metadata: bool = True,
) -> dict:
    """
    Scan a directory for ROMs and images, adding them to the database.

    ROMs that exist in the database but are no longer on disk are deleted,
    along with any orphaned ROMSets and Games.

    Args:
        base_path: Path to scan recursively
        progress_callback: Optional callback function to report progress
        use_hasheous: If True, use Hasheous API as fallback for ROM identification
        fetch_metadata: If True, auto-queue ScreenScraper metadata for new games

    Returns:
        dict with keys:
            - added: Number of new ROMs added
            - skipped: Number of existing ROMs skipped
            - deleted_roms: Number of ROMs deleted (no longer on disk)
            - images_added: Number of new images added
            - images_skipped: Number of existing images skipped
            - metadata_queued: Number of metadata jobs queued
            - added_rom_ids: List of IDs of ROMs that need identification
            - errors: List of error messages
    """
    base_path = os.path.abspath(base_path)
    logger.info("Starting scan of directory: %s", base_path)

    if not os.path.isdir(base_path):
        logger.error("Directory not found: %s", base_path)
        return {
            "added": 0,
            "skipped": 0,
            "marked_missing": 0,
            "images_added": 0,
            "images_skipped": 0,
            "metadata_queued": 0,
            "errors": [f"Directory not found: {base_path}"],
        }

    added = 0
    skipped = 0
    errors = []
    seen_paths = set()
    images_added = 0
    images_skipped = 0
    metadata_queued = 0
    added_rom_ids = []  # Track ROM IDs for parallel identification
    collected_images = []  # [(file_path, filename, system), ...]

    # Cache all systems and build extension map
    all_systems = list(System.objects.all())
    exclusive_map = build_extension_map(all_systems)

    # Mutable progress state for tracking across archive processing
    # This allows process_archive() to update counters and trigger callbacks
    progress_state = {
        "files_processed": 0,
        "roms_found": 0,
        "images_found": 0,
        "current_directory": "",
        "callback": progress_callback,
    }

    # Walk directory - process ALL files, let detect_system filter
    for root, _dirs, files in os.walk(base_path):
        current_dir = root  # Track current directory
        progress_state["current_directory"] = current_dir
        for filename in files:
            progress_state["files_processed"] += 1

            # Update progress frequently (every 10 files, and immediately on first file)
            if progress_callback and (
                progress_state["files_processed"] == 1
                or progress_state["files_processed"] % 10 == 0
            ):
                progress_callback(
                    {
                        "files_processed": progress_state["files_processed"],
                        "roms_found": added,
                        "images_found": len(collected_images),
                        "current_directory": current_dir,
                    }
                )
            file_path = os.path.join(root, filename)
            extension = Path(filename).suffix.lower()

            if not extension:
                continue

            if is_bios_file(filename, file_path):
                logger.debug("Skipped BIOS file: %s", file_path)
                continue

            # Check compound ROM extensions BEFORE image check
            if is_compound_rom_extension(filename):
                extension = get_full_extension(filename)
                # Fall through to normal ROM processing
            elif extension in IMAGE_EXTENSIONS:
                # Existing image handling
                system = match_by_folder(Path(file_path), all_systems)
                if system:
                    collected_images.append((file_path, filename, system))
                continue

            # Handle archives specially - look inside for ROMs
            if extension in COMPRESSED_EXTENSIONS:
                # Update progress_state with current totals before archive processing
                progress_state["roms_found"] = added
                progress_state["images_found"] = len(collected_images)

                archive_results = process_archive(
                    file_path,
                    all_systems,
                    exclusive_map,
                    seen_paths,
                    use_hasheous=use_hasheous,
                    fetch_metadata=fetch_metadata,
                    progress_state=progress_state,
                )
                added += archive_results["added"]
                skipped += archive_results["skipped"]
                metadata_queued += archive_results.get("metadata_queued", 0)
                added_rom_ids.extend(archive_results.get("added_rom_ids", []))
                errors.extend(archive_results["errors"])
                continue

            # Detect system (folder match + valid ext, or exclusive ext)
            system = detect_system(file_path, all_systems, exclusive_map)
            if not system:
                # Not a recognized ROM file, skip silently
                continue

            seen_paths.add(file_path)

            # Skip if already in database
            if ROM.objects.filter(file_path=file_path).exists():
                logger.debug("Skipped existing ROM: %s", file_path)
                skipped += 1
                continue

            # Parse filename
            try:
                parsed = parse_rom_filename(filename)
            except Exception as e:
                logger.error("Parse error for %s: %s", filename, e)
                errors.append(f"Parse error for {filename}: {e}")
                continue

            # Get file size
            try:
                file_size = os.path.getsize(file_path)
            except OSError as e:
                logger.error("Cannot read file size for %s: %s", file_path, e)
                errors.append(f"Cannot read file size for {file_path}: {e}")
                continue

            # Compute hashes for the ROM file
            crc32 = ""
            sha1 = ""

            # For CHD files, extract internal SHA1 (more useful than outer CRC32)
            if is_chd_file(filename):
                sha1 = extract_chd_sha1(file_path) or ""
                # CRC32 of CHD container is not useful for lookup, skip it
            else:
                try:
                    crc32 = archive_utils.compute_file_crc32(file_path)
                except Exception:
                    pass

            # Find or create rom set (also creates game if needed, hash lookup happens here)
            rom_set, _, rom_metadata_queued, _ = get_or_create_rom_set(
                parsed["name"],
                system,
                parsed["region"],
                parsed["revision"],
                source_path=os.path.dirname(file_path),  # Group by directory
                crc32=crc32,
                sha1=sha1,
                file_path=file_path,
                use_hasheous=use_hasheous,
                fetch_metadata=fetch_metadata,
            )

            # Extract Switch content type if applicable
            switch_title_id, content_type = (
                get_switch_content_info(filename) if system.slug == "switch" else ("", "")
            )

            # Create ROM record
            rom = ROM.objects.create(
                rom_set=rom_set,
                file_path=file_path,
                file_name=filename,
                file_size=file_size,
                crc32=crc32,
                sha1=sha1,
                tags=parsed["tags"],
                rom_number=parsed["rom_number"],
                disc=parsed["disc"],
                content_type=content_type,
                switch_title_id=switch_title_id,
            )
            added_rom_ids.append(rom.pk)

            # Recalculate default ROMSet now that a ROM exists
            from .romset_scoring import recalculate_default_romset

            recalculate_default_romset(rom_set.game)

            hash_info = f"sha1={sha1}" if sha1 else f"crc32={crc32 or '(none)'}"
            logger.debug(
                "Added ROM: %s (game: %s, system: %s, region: %s, %s)",
                filename,
                rom_set.game.name,
                system.name,
                rom_set.region or "(none)",
                hash_info,
            )
            added += 1
            if rom_metadata_queued:
                metadata_queued += 1

    # Process collected images - match to games
    for img_path, img_filename, img_system in collected_images:
        # Skip if already in database
        if GameImage.objects.filter(file_path=img_path).exists():
            images_skipped += 1
            continue

        # Get image name without extension for matching
        image_name = Path(img_filename).stem

        # Try to match to a game (fuzzy match)
        game = match_image_to_game(image_name, img_system)
        if not game:
            # No matching game found, skip silently
            images_skipped += 1
            continue

        # Get file size
        try:
            file_size = os.path.getsize(img_path)
        except OSError:
            file_size = 0

        # Create GameImage record
        GameImage.objects.create(
            game=game,
            file_path=img_path,
            file_name=img_filename,
            file_size=file_size,
            image_type=detect_image_type(img_path),
        )
        images_added += 1

    # Final progress update before deleting missing ROMs
    if progress_callback:
        progress_callback(
            {
                "files_processed": progress_state["files_processed"],
                "roms_found": added,
                "images_found": images_added,
                "current_directory": "(cleaning up)",
            }
        )

    # Delete ROMs that are in DB but no longer on disk
    deleted_roms = 0
    deleted_romsets = 0
    deleted_games = 0

    roms_under_path = ROM.objects.filter(
        file_path__startswith=base_path,
    ).select_related("rom_set", "rom_set__game")

    for rom in roms_under_path:
        # For archived ROMs, check if archive exists
        # For regular ROMs, check if file_path exists
        check_path = to_absolute_path(
            rom.archive_path if rom.is_archived else rom.file_path
        )

        if check_path not in seen_paths and not os.path.exists(check_path):
            rom_set = rom.rom_set
            game = rom_set.game

            rom.delete()
            deleted_roms += 1
            logger.debug("Deleted ROM: %s", rom.file_path)

            # Delete orphaned ROMSet (no ROMs left)
            if not rom_set.roms.exists():
                rom_set.delete()
                deleted_romsets += 1
                logger.debug("Deleted orphan ROMSet for: %s", game.name)

                # Delete orphaned Game (no ROMSets left)
                if not game.rom_sets.exists():
                    game.delete()
                    deleted_games += 1
                    logger.debug("Deleted orphan Game: %s", game.name)

    logger.info(
        "Scan complete: added=%d, skipped=%d, deleted_roms=%d (romsets=%d, games=%d), "
        "images_added=%d, images_skipped=%d, metadata_queued=%d, errors=%d",
        added,
        skipped,
        deleted_roms,
        deleted_romsets,
        deleted_games,
        images_added,
        images_skipped,
        metadata_queued,
        len(errors),
    )
    return {
        "added": added,
        "skipped": skipped,
        "deleted_roms": deleted_roms,
        "images_added": images_added,
        "images_skipped": images_skipped,
        "metadata_queued": metadata_queued,
        "added_rom_ids": added_rom_ids,
        "errors": errors,
    }
