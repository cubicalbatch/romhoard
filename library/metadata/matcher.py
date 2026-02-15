"""Game matching and metadata application logic."""

import json
import logging
import re
from datetime import datetime
from pathlib import Path

from django.utils import timezone
from PIL import Image

from library.merge import merge_games
from library.models import Game, GameImage, System
from library.metadata.screenscraper import (
    ALLOWED_MEDIA_TYPES,
    ScreenScraperClient,
)
from library.parser import parse_rom_filename

logger = logging.getLogger(__name__)


def _get_image_storage_path() -> str:
    """Get image storage path. Wrapper around shared function for compatibility."""
    from library.image_utils import get_image_storage_path

    return str(get_image_storage_path())


def create_wheel_mini(source_path: Path, target_height: int = 48) -> Path | None:
    """Create a resized wheel-mini thumbnail maintaining aspect ratio.

    Args:
        source_path: Path to the original wheel image
        target_height: Target height in pixels (default 48)

    Returns:
        Path to the created thumbnail, or None on failure
    """
    try:
        with Image.open(source_path) as img:
            # Calculate new width maintaining aspect ratio
            ratio = target_height / img.height
            new_width = int(img.width * ratio)

            # Resize with high-quality resampling
            resized = img.resize((new_width, target_height), Image.Resampling.LANCZOS)

            # Save with same extension as original
            mini_path = source_path.with_name(f"wheel-mini{source_path.suffix}")
            resized.save(mini_path, quality=95)

            logger.info(
                f"Created wheel-mini: {mini_path} ({new_width}x{target_height})"
            )
            return mini_path
    except Exception as e:
        logger.error(f"Failed to create wheel-mini for {source_path}: {e}")
        return None


def parse_screenscraper_name(screenscraper_name: str) -> dict:
    """Parse ScreenScraper name into components.

    Reuses the same parsing logic as DAT/Hasheous lookups
    to ensure consistent game name handling.

    Args:
        screenscraper_name: Raw name from ScreenScraper API

    Returns:
        dict with 'name', 'region', 'revision', 'tags'

    Example:
        >>> parse_screenscraper_name("Super Mario World (USA)")
        {'name': 'Super Mario World', 'region': 'USA', ...}
    """
    # Add dummy extension since parser expects filename with extension
    parsed = parse_rom_filename(f"{screenscraper_name}.rom")
    return {
        "name": parsed["name"],
        "region": parsed["region"],
        "revision": parsed["revision"],
        "tags": parsed["tags"],
    }


def _get_game_cache_path(game: Game, base_path: str) -> Path:
    """Get path to metadata.json cache file for a game.

    Args:
        game: Game instance
        base_path: Base metadata image path

    Returns:
        Path to the metadata.json file
    """
    safe_game_name = re.sub(r'[<>:"/\\|?*]', "_", game.name)
    return Path(base_path) / game.system.slug / safe_game_name / "metadata.json"


def get_cached_metadata(game: Game) -> dict | None:
    """Load cached metadata from disk if it exists.

    Looks for metadata.json in the game's image directory.

    Args:
        game: Game instance to get cached metadata for

    Returns:
        Cached metadata dict if found, None otherwise
    """
    base_path = _get_image_storage_path()
    cache_path = _get_game_cache_path(game, base_path)

    if not cache_path.exists():
        return None

    try:
        with open(cache_path, "r", encoding="utf-8") as f:
            cached = json.load(f)
        logger.info(f"Using cached metadata for '{game.name}'")
        return cached
    except (json.JSONDecodeError, OSError) as e:
        logger.warning(f"Failed to read metadata cache for '{game.name}': {e}")
        return None


def save_metadata_cache(game: Game, metadata: dict) -> None:
    """Save metadata to disk cache.

    Saves metadata.json in the game's image directory.

    Args:
        game: Game instance
        metadata: Metadata dict from ScreenScraper API
    """
    base_path = _get_image_storage_path()
    cache_path = _get_game_cache_path(game, base_path)

    try:
        # Create directory if needed
        cache_path.parent.mkdir(parents=True, exist_ok=True)

        with open(cache_path, "w", encoding="utf-8") as f:
            json.dump(metadata, f, indent=2, ensure_ascii=False)
        logger.debug(f"Saved metadata cache for '{game.name}'")
    except OSError as e:
        logger.warning(f"Failed to save metadata cache for '{game.name}': {e}")


def _get_rom_for_lookup(game: Game):
    """Get the best ROM for lookup from a game's ROM sets.

    Prefers ROMs with CRC32 hashes, but will return any ROM if none have
    CRC32 (needed for CHD files which don't have CRC computed).

    Args:
        game: Game instance

    Returns:
        ROM instance or None if no ROMs found
    """
    from library.models import ROM

    # First try to find a ROM with CRC32
    rom_with_crc = (
        ROM.objects.filter(rom_set__game=game)
        .exclude(crc32="")
        .exclude(crc32__isnull=True)
        .first()
    )
    if rom_with_crc:
        return rom_with_crc

    # Fall back to any ROM (for CHD files without CRC)
    return ROM.objects.filter(rom_set__game=game).first()


def fetch_metadata_for_game(game: Game) -> dict | None:
    """Fetch metadata for a game using unified lookup chain.

    Uses the lookup module to identify the game if needed, then fetches
    full metadata from ScreenScraper.

    The identification chain (handled by lookup_rom):
    1. Hasheous (hash-based, cached)
    2. ScreenScraper CRC lookup (for non-arcade)
    3. ScreenScraper romnom lookup (filename-based)
    4. ScreenScraper name search (fuzzy matching)

    If game.screenscraper_id is already set (manual entry), uses that ID
    directly and always fetches fresh from API.

    Results are cached to metadata.json in the game's image directory.

    Args:
        game: Game instance to fetch metadata for

    Returns:
        Dict with metadata if found, None otherwise
    """
    from library.lookup import lookup_rom

    # Get all system IDs to try
    system_ids = game.system.all_screenscraper_ids
    if not system_ids:
        logger.warning(
            f"System {game.system.slug} has no ScreenScraper ID mappings, skipping"
        )
        return None

    client = ScreenScraperClient()

    try:
        # If game already has a ScreenScraper ID set (manual entry), use it directly
        # Skip cache check - always fetch fresh to honor manually set ID
        if game.screenscraper_id:
            logger.info(
                f"Using existing ScreenScraper ID {game.screenscraper_id} "
                f"for '{game.name}'"
            )
            metadata = client.get_game_info(
                game.screenscraper_id,
                media_types=ALLOWED_MEDIA_TYPES,
                game_name=game.name,
                system_id=system_ids[0],  # Use primary system ID for media
            )
            metadata["_match_type"] = "manual_id"
            save_metadata_cache(game, metadata)
            return metadata

        # Check cache for non-manual ID cases
        cached = get_cached_metadata(game)
        if cached:
            return cached

        # Try to identify the game via unified lookup chain
        rom = _get_rom_for_lookup(game)

        # Build file_path for romnom lookup
        file_path = ""
        if rom:
            if rom.is_archived:
                file_path = rom.archive_path
            elif game.system.archive_as_rom:
                file_path = rom.file_path
            else:
                file_path = rom.file_path

        result = lookup_rom(
            system=game.system,
            crc32=rom.crc32 if rom else "",
            file_path=file_path,
            game_name=game.name,  # For name-based fallback
            use_hasheous=False,  # Hasheous already tried during scan
        )

        if not result or not result.screenscraper_id:
            logger.info(f"No ScreenScraper match found for '{game.name}'")
            return None

        # Update game with screenscraper_id from lookup
        game.screenscraper_id = result.screenscraper_id
        game.save(update_fields=["screenscraper_id"])
        logger.info(
            f"Identified '{game.name}' as ScreenScraper ID {result.screenscraper_id} "
            f"via {result.source}"
        )

        # Determine match type based on lookup source
        # If source is screenscraper and confidence >= 0.85, it's CRC/romnom match
        if result.source == "screenscraper" and result.confidence >= 0.85:
            match_type = "crc32"  # Could be CRC or romnom, both are reliable
        else:
            match_type = "name"

        # Fetch full metadata using the identified ID
        metadata = client.get_game_info(
            result.screenscraper_id,
            media_types=ALLOWED_MEDIA_TYPES,
            game_name=result.name or game.name,
            system_id=system_ids[0],
        )

        # Track match type for apply_metadata_to_game renaming logic
        metadata["_match_type"] = match_type
        metadata["_matched_system_id"] = system_ids[0]
        if match_type in ("crc32", "romnom") and result.name:
            metadata["_screenscraper_name"] = result.raw_name

        # Cache the result
        save_metadata_cache(game, metadata)
        return metadata

    except Exception as e:
        logger.error(f"Error fetching metadata for game '{game.name}': {e}")
        return None


# Backward compatibility alias
match_game = fetch_metadata_for_game


def apply_metadata_to_game(game: Game, metadata: dict) -> bool:
    """Apply fetched metadata to a Game instance.

    If this was a CRC32 hash match and the game's name_source is 'filename',
    also renames the game using the ScreenScraper canonical name.

    Args:
        game: Game instance to update
        metadata: Metadata dict from ScreenScraper

    Returns:
        True if metadata was applied, False otherwise
    """
    if not metadata:
        return False

    try:
        # Check if we should rename the game
        # Only rename on CRC32/romnom matches when game was named via filename parsing
        match_type = metadata.get("_match_type")
        should_rename = (
            match_type in ("crc32", "romnom")
            and game.name_source == Game.SOURCE_FILENAME
            and metadata.get("_screenscraper_name")
        )

        if should_rename:
            parsed = parse_screenscraper_name(metadata["_screenscraper_name"])
            new_name = parsed["name"]

            if new_name and new_name != game.name:
                # Check for duplicate (same name + system already exists)
                existing = (
                    Game.objects.filter(name=new_name, system=game.system)
                    .exclude(pk=game.pk)
                    .first()
                )

                if existing:
                    logger.warning(
                        f"Cannot rename '{game.name}' to '{new_name}' - "
                        f"game with that name already exists (ID: {existing.pk})"
                    )
                else:
                    old_name = game.name
                    game.name = new_name
                    game.name_source = Game.SOURCE_SCREENSCRAPER
                    logger.info(
                        f"Renamed game '{old_name}' -> '{new_name}' "
                        f"(ScreenScraper CRC32 match)"
                    )

        # Check if another game already has this screenscraper_id
        new_ss_id = metadata.get("id")
        if new_ss_id:
            existing_with_ss_id = (
                Game.objects.filter(screenscraper_id=new_ss_id, system=game.system)
                .exclude(pk=game.pk)
                .first()
            )
            if existing_with_ss_id:
                # Merge current game into the one with matching screenscraper_id
                logger.info(
                    f"Found existing game with screenscraper_id {new_ss_id}: "
                    f"'{existing_with_ss_id.name}' (pk={existing_with_ss_id.pk}). "
                    f"Merging '{game.name}' (pk={game.pk}) into it."
                )
                merge_games(existing_with_ss_id, game)
                # Update the existing game's metadata instead
                game = existing_with_ss_id

        # Update basic fields
        game.screenscraper_id = new_ss_id
        game.description = metadata.get("description", "")
        game.developer = metadata.get("developer", "")
        game.publisher = metadata.get("publisher", "")
        game.players = metadata.get("players", "")

        # Update genres M2M
        from library.metadata.normalize import normalize_genres
        from library.metadata.genres import get_or_create_genre_with_parent

        game.genres.clear()
        raw_genres = metadata.get("genres", [])
        for genre_name in normalize_genres(raw_genres):
            if genre_name and isinstance(genre_name, str):
                genre = get_or_create_genre_with_parent(genre_name)
                game.genres.add(genre)

        # Update rating fields
        game.rating = metadata.get("rating")
        game.rating_source = metadata.get("rating_source") or ""

        # Parse release date
        release_str = metadata.get("release_date", "")
        if release_str:
            try:
                # Try to parse different date formats
                for fmt in ["%Y-%m-%d", "%Y", "%Y-%m"]:
                    try:
                        game.release_date = datetime.strptime(release_str, fmt).date()
                        break
                    except ValueError:
                        continue
            except Exception as e:
                logger.warning(f"Could not parse release date '{release_str}': {e}")

        game.metadata_updated_at = timezone.now()
        game.save()

        logger.info(f"Applied metadata to game '{game.name}'")
        return True

    except Exception as e:
        logger.error(f"Error applying metadata to game '{game.name}': {e}")
        return False


def _ensure_image_in_db(game: Game, image_path: Path, image_type: str) -> bool:
    """Ensure an existing image on disk is tracked in the database.

    Args:
        game: Game instance
        image_path: Path to existing image file
        image_type: One of "cover", "screenshot", "mix"

    Returns:
        True if record was created, False if already exists
    """
    file_path_str = str(image_path)

    # Check if already in database
    if GameImage.objects.filter(file_path=file_path_str).exists():
        return False

    # Create record for pre-existing image
    try:
        GameImage.objects.create(
            game=game,
            file_path=file_path_str,
            file_name=image_path.name,
            file_size=image_path.stat().st_size,
            image_type=image_type,
            source="scanned",  # Mark as scanned, not downloaded
        )
        logger.info(f"Added existing {image_type} image to database for '{game.name}'")
        return True
    except Exception as e:
        logger.error(f"Error creating GameImage record for existing file: {e}")
        return False


def download_images_for_game(game: Game, media_list: list[dict]) -> int:
    """Download and save images for a game.

    Args:
        game: Game instance
        media_list: List of media dicts from ScreenScraper API

    Returns:
        Number of images downloaded
    """
    if not media_list:
        return 0

    # Get image storage path from settings
    base_path = _get_image_storage_path()

    # Create directory structure: {base_path}/{system_slug}/{game_name}/
    safe_game_name = re.sub(r'[<>:"/\\|?*]', "_", game.name)
    game_dir = Path(base_path) / game.system.slug / safe_game_name
    game_dir.mkdir(parents=True, exist_ok=True)

    client = ScreenScraperClient()
    downloaded = 0

    for media in media_list:
        media_type = media.get("type", "")
        media_url = media.get("url", "")

        if not media_url:
            continue

        # Only process allowed media types
        if media_type not in {"box-2D", "ss", "mixrbv1", "wheel", "sstitle"}:
            logger.debug(f"Skipping unsupported media type: {media_type}")
            continue

        # Map ScreenScraper media types to our image types
        if media_type == "box-2D":
            image_type = "cover"
        elif media_type == "ss":
            image_type = "screenshot"
        elif media_type == "mixrbv1":
            image_type = "mix"
        elif media_type == "wheel":
            image_type = "wheel"
        elif media_type == "sstitle":
            image_type = "screenshot_title"
        else:
            continue  # Should not reach here due to filter above

        # Check if image already exists on disk (any extension)
        image_extensions = {".jpg", ".jpeg", ".png", ".gif", ".webp"}
        existing_images = [
            p
            for p in game_dir.glob(f"{media_type}.*")
            if p.suffix.lower() in image_extensions
        ]
        if existing_images:
            logger.info(
                f"Skipping {media_type} download for '{game.name}' - "
                f"image exists: {existing_images[0].name}"
            )
            # Ensure existing image is tracked in database
            _ensure_image_in_db(game, existing_images[0], image_type)

            # For wheel images, also check for and register wheel-mini
            if media_type == "wheel":
                existing_minis = [
                    p
                    for p in game_dir.glob("wheel-mini.*")
                    if p.suffix.lower() in image_extensions
                ]
                if existing_minis:
                    _ensure_image_in_db(game, existing_minis[0], "wheel_mini")
            continue

        # Download to temporary file first (to get actual extension from Content-Type)
        temp_path = game_dir / f"{media_type}.tmp"

        # Download image and get actual extension
        ext = client.download_image(media_url, str(temp_path))
        if ext:
            # Construct final path with correct extension
            filename = f"{media_type}{ext}"
            final_path = game_dir / filename

            # Skip if already exists (check before renaming)
            if final_path.exists():
                logger.debug(f"Image already exists: {final_path}")
                temp_path.unlink()  # Remove temp file
                continue

            # Rename temp file to final name
            temp_path.rename(final_path)

            # Create GameImage record
            try:
                GameImage.objects.create(
                    game=game,
                    file_path=str(final_path),
                    file_name=filename,
                    file_size=final_path.stat().st_size,
                    image_type=image_type,
                    source="downloaded",
                )
                downloaded += 1
                logger.info(f"Downloaded {media_type} image for '{game.name}'")

                # If this was a wheel image, create the mini version
                if media_type == "wheel":
                    mini_path = create_wheel_mini(final_path)
                    if mini_path:
                        GameImage.objects.create(
                            game=game,
                            file_path=str(mini_path),
                            file_name=mini_path.name,
                            file_size=mini_path.stat().st_size,
                            image_type="wheel_mini",
                            source="downloaded",
                        )
                        downloaded += 1
            except Exception as e:
                logger.error(f"Error creating GameImage record: {e}")

    return downloaded


def fetch_system_metadata_for_job(progress_callback=None) -> dict:
    """Fetch metadata for systems with games from ScreenScraper.

    Only fetches for systems that have at least 1 game in the library.

    Args:
        progress_callback: Optional callback for progress updates

    Returns:
        Dict with 'updated', 'skipped', and 'icons_downloaded' counts
    """
    from django.db.models import Count

    client = ScreenScraperClient()
    systems_data = client.get_systems_list()

    # Build lookup by ScreenScraper ID
    ss_systems = {s["id"]: s for s in systems_data}

    updated = 0
    skipped = 0
    icons_downloaded = 0
    processed = 0

    # Get image storage path
    base_path = Path(_get_image_storage_path())

    # Only fetch for systems with at least 1 game and at least 1 screenscraper_id
    systems = list(
        System.objects.exclude(screenscraper_ids=[])
        .annotate(game_count=Count("games"))
        .filter(game_count__gt=0)
    )

    total = len(systems)

    # Initial progress update
    if progress_callback:
        progress_callback(
            {
                "systems_total": total,
                "systems_processed": 0,
                "current_system": "",
                "systems_updated": 0,
                "icons_downloaded": 0,
            }
        )

    for system in systems:
        processed += 1

        # Update progress
        if progress_callback:
            progress_callback(
                {
                    "systems_total": total,
                    "systems_processed": processed,
                    "current_system": system.name,
                    "systems_updated": updated,
                    "icons_downloaded": icons_downloaded,
                }
            )

        ss_data = ss_systems.get(system.screenscraper_id)
        if not ss_data:
            logger.debug(
                f"System {system.slug} (ID {system.screenscraper_id}) "
                "not found in ScreenScraper"
            )
            skipped += 1
            continue

        # Update release year
        system.release_year = ss_data.get("release_year", "")

        # Download icon if available
        icon_url = ss_data.get("icon_url")
        if icon_url:
            icon_path = _download_system_icon(system, icon_url, base_path, client)
            if icon_path:
                system.icon_path = str(icon_path)
                icons_downloaded += 1

        system.metadata_updated_at = timezone.now()
        system.save()
        updated += 1
        logger.info(f"Updated metadata for system '{system.name}'")

    return {
        "updated": updated,
        "skipped": skipped,
        "icons_downloaded": icons_downloaded,
    }


def _download_system_icon(
    system: System, icon_url: str, base_path: Path, client: ScreenScraperClient
) -> Path | None:
    """Download icon for a system.

    Args:
        system: System instance
        icon_url: URL to download icon from
        base_path: Base path for storing metadata images
        client: ScreenScraper client for downloading

    Returns:
        Path to saved icon or None if failed
    """
    # Create directory: {base_path}/systems/{slug}/
    system_dir = base_path / "systems" / system.slug
    system_dir.mkdir(parents=True, exist_ok=True)

    # Check if icon already exists
    existing = list(system_dir.glob("icon.*"))
    if existing:
        logger.debug(f"Icon already exists for {system.slug}: {existing[0]}")
        return existing[0]

    # Download to temp file
    temp_path = system_dir / "icon.tmp"
    ext = client.download_image(icon_url, str(temp_path))

    if ext:
        final_path = system_dir / f"icon{ext}"
        temp_path.rename(final_path)
        logger.info(f"Downloaded icon for system '{system.name}'")
        return final_path

    # Cleanup temp file if download failed
    if temp_path.exists():
        temp_path.unlink()

    return None
