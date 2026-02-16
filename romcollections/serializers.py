"""JSON serialization for collection import/export."""

import json
import logging
import shutil
import uuid
import zipfile
from datetime import date
from pathlib import Path
from typing import Any

from django.conf import settings
from django.utils import timezone

from library.merge import find_existing_game
from library.metadata.screenscraper import ScreenScraperClient
from library.models import Game, GameImage, MetadataJob, System
from library.tasks import run_metadata_job_for_game

from .models import Collection, CollectionEntry

logger = logging.getLogger(__name__)

EXPORT_VERSION = "1.0"


def _prefetch_matched_games_for_entries(
    entries: list[CollectionEntry],
) -> dict[tuple[str, str], Game]:
    """Batch-fetch matched games for a list of collection entries.

    This avoids N+1 queries by fetching all matching games in a single query.

    Args:
        entries: List of CollectionEntry objects

    Returns:
        Dict mapping (game_name_lower, system_slug) to Game object
    """
    from django.db.models import Q

    if not entries:
        return {}

    # Build a Q filter for all entries
    filters = Q()
    for entry in entries:
        filters |= Q(name__iexact=entry.game_name, system__slug=entry.system_slug)

    # Fetch all matching games in one query
    games = Game.objects.filter(filters).select_related("system")

    # Build lookup dict
    result: dict[tuple[str, str], Game] = {}
    for game in games:
        key = (game.name.lower(), game.system.slug)
        result[key] = game

    return result


def export_collection(collection: Collection) -> dict[str, Any]:
    """Export a collection to a portable JSON-serializable dict.

    Args:
        collection: Collection instance to export

    Returns:
        Dictionary ready for JSON serialization
    """
    # Get all entries
    entries_list = list(collection.entries.all().order_by("position"))

    # Batch-fetch matched games to avoid N+1 queries
    matched_games = _prefetch_matched_games_for_entries(entries_list)

    entries = []
    for entry in entries_list:
        entry_data = {
            "game_name": entry.game_name,
            "system_slug": entry.system_slug,
            "position": entry.position,
            "notes": entry.notes,
        }
        # Include screenscraper_id if matched game has one
        key = (entry.game_name.lower(), entry.system_slug)
        matched_game = matched_games.get(key)
        if matched_game and matched_game.screenscraper_id:
            entry_data["screenscraper_id"] = matched_game.screenscraper_id
        entries.append(entry_data)

    return {
        "romhoard_collection": {
            "version": EXPORT_VERSION,
            "exported_at": timezone.now().isoformat(),
        },
        "collection": {
            "slug": collection.slug,
            "name": collection.name,
            "description": collection.description,
            "creator": collection.creator,
            "is_public": collection.is_public,
            "is_community": collection.is_community,
            "tags": collection.tags,
            "created_at": collection.created_at.isoformat(),
            "updated_at": collection.updated_at.isoformat(),
            # Cover metadata
            "has_cover": collection.has_cover,
            "cover_source": collection.cover_source,
            "cover_generation_type": collection.cover_generation_type,
        },
        "entries": entries,
    }


class ImportError(Exception):
    """Raised when import validation fails."""

    pass


class ValidationResult:
    """Result of ZIP validation."""

    def __init__(
        self,
        is_valid: bool,
        errors: list[str] | None = None,
        warnings: list[str] | None = None,
        info: list[str] | None = None,
        compressed_size: int = 0,
        uncompressed_size: int = 0,
        has_collection_json: bool = False,
        has_cover: bool = False,
        game_count: int = 0,
        image_count: int = 0,
    ):
        self.is_valid = is_valid
        self.errors = errors or []
        self.warnings = warnings or []
        self.info = info or []
        self.compressed_size = compressed_size
        self.uncompressed_size = uncompressed_size
        self.has_collection_json = has_collection_json
        self.has_cover = has_cover
        self.game_count = game_count
        self.image_count = image_count

    def __bool__(self) -> bool:
        return self.is_valid


def validate_collection_zip(
    zip_path: str,
    max_size: int = 0,
    max_uncompressed: int = 0,
) -> ValidationResult:
    """Validate a collection ZIP file without extracting it.

    Performs safety checks to prevent zip bomb attacks and validate structure.

    Args:
        zip_path: Path to the ZIP file
        max_size: Maximum allowed file size (defaults to COLLECTION_IMPORT_MAX_SIZE)
        max_uncompressed: Maximum allowed uncompressed size (defaults to
                         COLLECTION_IMPORT_MAX_UNCOMPRESSED_SIZE)

    Returns:
        ValidationResult with validation status and details
    """
    max_size = max_size or getattr(
        settings, "COLLECTION_IMPORT_MAX_SIZE", 1024 * 1024 * 1024
    )
    max_uncompressed = max_uncompressed or getattr(
        settings, "COLLECTION_IMPORT_MAX_UNCOMPRESSED_SIZE", 2 * 1024 * 1024 * 1024
    )

    errors: list[str] = []
    warnings: list[str] = []
    info: list[str] = []

    # Check file exists and get size
    zip_file = Path(zip_path)
    if not zip_file.exists():
        return ValidationResult(is_valid=False, errors=["ZIP file not found"])

    compressed_size: int = zip_file.stat().st_size
    assert max_size is not None

    # Check file size limit
    if compressed_size > max_size:
        size_mb = compressed_size / (1024 * 1024)
        max_mb = max_size / (1024 * 1024)
        return ValidationResult(
            is_valid=False,
            errors=[f"File too large: {size_mb:.1f}MB (max {max_mb:.0f}MB)"],
            compressed_size=compressed_size,
        )

    # Try to open as ZIP
    try:
        with zipfile.ZipFile(zip_path, "r") as zipf:
            # Check for zip bomb - compare compressed vs uncompressed size
            try:
                total_uncompressed: int = sum(info.file_size for info in zipf.infolist())
            except (RuntimeError, OSError) as e:
                return ValidationResult(
                    is_valid=False,
                    errors=[f"Cannot read ZIP contents: {e}"],
                    compressed_size=compressed_size,
                )

            assert max_uncompressed is not None

            # Prevent zip bomb (high compression ratio)
            if total_uncompressed > max_uncompressed:
                return ValidationResult(
                    is_valid=False,
                    errors=[
                        f"ZIP would expand to {total_uncompressed / (1024 * 1024 * 1024):.1f}GB "
                        f"(max {max_uncompressed / (1024 * 1024 * 1024):.0f}GB). "
                        "This may be a zip bomb or contain too much data."
                    ],
                    compressed_size=compressed_size,
                    uncompressed_size=total_uncompressed,
                )

            # Check compression ratio (another zip bomb indicator)
            if compressed_size > 0 and total_uncompressed / compressed_size > 100:
                warnings.append(
                    "Very high compression ratio detected. "
                    "Import may take a while."
                )

            # Get file list
            try:
                namelist = zipf.namelist()
            except (RuntimeError, OSError) as e:
                return ValidationResult(
                    is_valid=False,
                    errors=[f"Cannot read ZIP file list: {e}"],
                    compressed_size=compressed_size,
                    uncompressed_size=total_uncompressed,
                )

            # Check for required collection.json
            if "collection.json" not in namelist:
                errors.append("Missing required file: collection.json")
                return ValidationResult(
                    is_valid=False,
                    errors=errors,
                    compressed_size=compressed_size,
                    uncompressed_size=total_uncompressed,
                )

            has_collection_json = True
            info.append(f"Found collection.json")

            # Initialize counters (in case validation fails early)
            game_count = 0

            # Validate collection.json content
            try:
                with zipf.open("collection.json") as f:
                    collection_content = f.read().decode("utf-8")
                    collection_data = json.loads(collection_content)
                    validate_import_data(collection_data)

                    # Count entries
                    entries = collection_data.get("entries", [])
                    game_count = len(entries)
                    info.append(f"Collection contains {game_count} game(s)")

                    # Check for collection metadata
                    coll_meta = collection_data.get("collection", {})
                    coll_name = coll_meta.get("name", "Unnamed")
                    info.append(f"Collection name: {coll_name}")
            except UnicodeDecodeError as e:
                errors.append(f"collection.json is not valid UTF-8: {e}")
            except json.JSONDecodeError as e:
                errors.append(f"collection.json is not valid JSON: {e}")
            except ImportError as e:
                errors.append(f"Invalid collection data: {e}")

            # Check for cover image
            cover_files = [n for n in namelist if n.startswith("cover.")]
            has_cover = len(cover_files) > 0
            if has_cover:
                ext = Path(cover_files[0]).suffix.lower()
                valid_cover_exts = {".png", ".jpg", ".jpeg"}
                if ext not in valid_cover_exts:
                    warnings.append(f"Cover image has unusual extension: {ext}")
                else:
                    info.append(f"Found cover image: {cover_files[0]}")

            # Check for games/ and images/ folders
            has_games_folder = any(n.startswith("games/") for n in namelist)
            has_images_folder = any(n.startswith("images/") for n in namelist)

            if has_games_folder:
                game_files = [n for n in namelist if n.startswith("games/") and n.endswith(".json")]
                if game_files:
                    info.append(f"Found {len(game_files)} game metadata file(s)")

            # Count images and validate extensions
            image_count = 0
            if has_images_folder:
                valid_image_exts = {".png", ".jpg", ".jpeg"}
                invalid_images = []

                for name in namelist:
                    if name.startswith("images/") and not name.endswith("/"):
                        ext = Path(name).suffix.lower()
                        if ext in valid_image_exts:
                            image_count += 1
                        else:
                            invalid_images.append(name)

                if image_count > 0:
                    info.append(f"Found {image_count} image(s)")

                if invalid_images:
                    warnings.append(
                        f"Found {len(invalid_images)} file(s) with non-standard image extensions"
                    )

            # Check for suspicious files (potential security issues)
            suspicious = [
                n for n in namelist
                if n.startswith(("../", "/", "..\\")) or ".." in Path(n).parts
            ]
            if suspicious:
                errors.append(f"ZIP contains suspicious paths: {suspicious}")

            # Build final result
            is_valid = len(errors) == 0

            return ValidationResult(
                is_valid=is_valid,
                errors=errors,
                warnings=warnings,
                info=info,
                compressed_size=compressed_size,
                uncompressed_size=total_uncompressed,
                has_collection_json=has_collection_json,
                has_cover=has_cover,
                game_count=game_count,
                image_count=image_count,
            )

    except zipfile.BadZipFile:
        return ValidationResult(
            is_valid=False,
            errors=["File is not a valid ZIP archive"],
            compressed_size=compressed_size,
        )
    except (IOError, OSError) as e:
        return ValidationResult(
            is_valid=False,
            errors=[f"Cannot read ZIP file: {e}"],
            compressed_size=compressed_size,
        )


def validate_import_data(data: dict[str, Any]) -> None:
    """Validate import data structure.

    Args:
        data: Dictionary from JSON import

    Raises:
        ImportError: If validation fails
    """
    if not isinstance(data, dict):
        raise ImportError("Invalid format: expected JSON object")

    if "romhoard_collection" not in data:
        raise ImportError("Invalid format: missing 'romhoard_collection' header")

    header = data["romhoard_collection"]
    if not isinstance(header, dict) or "version" not in header:
        raise ImportError("Invalid format: invalid header")

    if "collection" not in data:
        raise ImportError("Invalid format: missing 'collection' data")

    collection_data = data["collection"]
    required_fields = ["slug", "name"]
    for field in required_fields:
        if field not in collection_data:
            raise ImportError(f"Invalid format: missing required field '{field}'")

    if "entries" not in data:
        raise ImportError("Invalid format: missing 'entries' array")

    if not isinstance(data["entries"], list):
        raise ImportError("Invalid format: 'entries' must be an array")

    for i, entry in enumerate(data["entries"]):
        if not isinstance(entry, dict):
            raise ImportError(f"Invalid format: entry {i} is not an object")
        if "game_name" not in entry:
            raise ImportError(f"Invalid format: entry {i} missing 'game_name'")
        if "system_slug" not in entry:
            raise ImportError(f"Invalid format: entry {i} missing 'system_slug'")


def import_collection(
    data: dict[str, Any],
    overwrite: bool = False,
    creator_override: str | None = None,
    force_public: bool = False,
    force_community: bool = False,
    source_url: str | None = None,
) -> dict[str, Any]:
    """Import a collection from JSON data.

    Creates Game records for entries that don't match existing games.
    Queues metadata lookup for newly created games if ScreenScraper credentials
    are configured and no screenscraper_id is provided.

    Args:
        data: Dictionary from JSON import (should be validated first)
        overwrite: If True, overwrite existing collection with same slug
        creator_override: If provided, use this as the creator instead of JSON value
        force_public: If True, set is_public=True regardless of JSON value
        force_community: If True, set is_community=True regardless of JSON value
        source_url: If provided, save as the collection's source URL for syncing

    Returns:
        Dict with keys:
            - collection: Collection instance
            - entries_imported: number of entries imported
            - games_created: number of new Game records created
            - metadata_jobs_queued: number of metadata jobs queued
            - warnings: list of warning messages

    Raises:
        ImportError: If import fails
    """
    validate_import_data(data)

    collection_data = data["collection"]
    slug = collection_data["slug"]

    # Creator: use override if provided, otherwise fall back to JSON value or "local"
    creator = creator_override or collection_data.get("creator") or "local"

    # Check for existing collection with same creator+slug
    existing = Collection.objects.filter(creator=creator, slug=slug).first()
    if existing:
        if not overwrite:
            raise ImportError(
                f"Collection '{creator}/{slug}' already exists. "
                "Use overwrite=True to replace it."
            )
        existing.entries.all().delete()
        collection = existing
    else:
        collection = Collection(slug=slug)

    collection.name = collection_data["name"]
    collection.description = collection_data.get("description", "")
    collection.creator = creator
    collection.is_public = True if force_public else collection_data.get("is_public", True)
    # Handle is_community: force_community overrides, then is_personal, then JSON value
    if force_community:
        collection.is_community = True
    elif collection_data.get("is_personal", False):
        collection.is_community = False
    else:
        collection.is_community = collection_data.get("is_community", True)
    collection.tags = collection_data.get("tags", [])
    # Save source URL for syncing
    if source_url:
        collection.source_url = source_url
        collection.last_synced_at = timezone.now()
    collection.save()

    # Track stats
    entries_imported = 0
    games_created = 0
    metadata_jobs_queued = 0
    warnings = []
    invalid_systems = set()

    # Check credentials once for metadata lookup
    ss_client = ScreenScraperClient()
    has_credentials = ss_client.has_credentials()

    # Create entries and games
    entries_data = data["entries"]
    for entry_data in entries_data:
        entry = CollectionEntry.objects.create(
            collection=collection,
            game_name=entry_data["game_name"],
            system_slug=entry_data["system_slug"],
            position=entry_data.get("position", entries_imported),
            notes=entry_data.get("notes", ""),
        )
        entries_imported += 1

        # Check if game exists
        matched_game = entry.get_matched_game()
        ss_id = entry_data.get("screenscraper_id")

        if matched_game:
            # Update screenscraper_id if provided and not already set
            if ss_id and not matched_game.screenscraper_id:
                matched_game.screenscraper_id = ss_id
                matched_game.save(update_fields=["screenscraper_id"])
        else:
            # No matching game - check for existing game or create one if system exists
            system = System.objects.filter(slug=entry_data["system_slug"]).first()
            if system:
                # Check for existing game by name (case-insensitive) or screenscraper_id
                game = find_existing_game(
                    name=entry_data["game_name"],
                    system=system,
                    screenscraper_id=ss_id,
                )
                if game:
                    # Update screenscraper_id if provided and not already set
                    if ss_id and not game.screenscraper_id:
                        game.screenscraper_id = ss_id
                        game.save(update_fields=["screenscraper_id"])
                else:
                    # Create new game
                    game = Game.objects.create(
                        name=entry_data["game_name"],
                        system=system,
                        name_source=Game.SOURCE_COLLECTION,
                        screenscraper_id=ss_id,
                    )
                    games_created += 1
                    # Queue metadata lookup if no screenscraper_id and credentials available
                    if not ss_id and has_credentials and system.screenscraper_id:
                        # Use unique temp ID to avoid constraint conflicts
                        temp_id = f"pending-{uuid.uuid4().hex[:8]}"
                        job = MetadataJob.objects.create(task_id=temp_id, game=game)
                        job_id = run_metadata_job_for_game.defer(metadata_job_id=job.id)
                        job.task_id = str(job_id)
                        job.save(update_fields=["task_id"])
                        metadata_jobs_queued += 1
            else:
                invalid_systems.add(entry_data["system_slug"])

    # Build warnings
    if invalid_systems:
        warnings.append(
            f"Could not create games for unknown systems: {', '.join(sorted(invalid_systems))}"
        )

    return {
        "collection": collection,
        "entries_imported": entries_imported,
        "games_created": games_created,
        "metadata_jobs_queued": metadata_jobs_queued,
        "warnings": warnings,
    }


def _get_images_dir() -> Path:
    """Get the directory for storing game images."""
    # Use ROM_LIBRARY_ROOT if set, otherwise fall back to MEDIA_ROOT
    library_root = getattr(settings, "ROM_LIBRARY_ROOT", None)
    if library_root:
        return Path(library_root) / "images"
    return Path(settings.MEDIA_ROOT) / "images"


def _sanitize_filename(name: str) -> str:
    """Sanitize a string for use as a filename/folder name."""
    replacements = {
        "/": "-",
        "\\": "-",
        ":": " -",
        "*": "",
        "?": "",
        '"': "'",
        "<": "",
        ">": "",
        "|": "-",
    }
    for char, replacement in replacements.items():
        name = name.replace(char, replacement)
    return name.strip(". ")


def import_collection_with_images(
    zip_path: str,
    overwrite: bool = False,
    creator_override: str | None = None,
    force_public: bool = False,
    force_community: bool = False,
    source_url: str | None = None,
) -> dict[str, Any]:
    """Import a collection from a ZIP file with images.

    The ZIP should contain:
    - collection.json: The collection data
    - games/: Folder with metadata JSON for each game (optional)
    - images/: Folder with images organized by game name

    Args:
        zip_path: Path to the ZIP file
        overwrite: If True, overwrite existing collection with same slug
        creator_override: If provided, use this as the creator instead of JSON value
        force_public: If True, set is_public=True regardless of JSON value
        force_community: If True, set is_community=True regardless of JSON value
        source_url: Optional URL the collection was imported from

    Returns:
        Dict with keys:
            - collection: Collection instance
            - entries_imported: number of entries imported
            - games_created: number of new Game records created
            - images_imported: number of images imported
            - metadata_jobs_queued: number of metadata jobs queued
            - warnings: list of warning messages

    Raises:
        ImportError: If import fails
    """
    with zipfile.ZipFile(zip_path, "r") as zipf:
        # Find and read collection.json
        if "collection.json" not in zipf.namelist():
            raise ImportError("ZIP file does not contain collection.json")

        try:
            with zipf.open("collection.json") as f:
                collection_data = json.loads(f.read().decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as e:
            raise ImportError(f"Invalid collection.json in ZIP: {e}") from e

        # First, import the collection using existing logic
        result = import_collection(
            collection_data,
            overwrite=overwrite,
            creator_override=creator_override,
            force_public=force_public,
            force_community=force_community,
            source_url=source_url,
        )
        collection = result["collection"]

        # Import collection cover image if present
        cover_imported = False
        cover_files = [n for n in zipf.namelist() if n.startswith("cover.")]
        if cover_files:
            cover_zip_path = cover_files[0]
            tmp_cover_path = None
            try:
                import tempfile

                from .cover_utils import get_collection_cover_path, resize_cover_image

                # Extract to temp file for processing
                suffix = Path(cover_zip_path).suffix or ".png"
                with tempfile.NamedTemporaryFile(
                    delete=False, suffix=suffix
                ) as tmp_file:
                    with zipf.open(cover_zip_path) as src:
                        shutil.copyfileobj(src, tmp_file)
                    tmp_cover_path = tmp_file.name

                # Convert to PNG and resize
                cover_data = resize_cover_image(tmp_cover_path)
                cover_path = get_collection_cover_path(collection.slug)

                with open(cover_path, "wb") as dst:
                    dst.write(cover_data)

                # Preserve cover metadata from export if available
                cover_meta = collection_data.get("collection", {})
                cover_source = cover_meta.get(
                    "cover_source", Collection.COVER_SOURCE_UPLOADED
                )
                cover_gen_type = cover_meta.get("cover_generation_type")

                collection.set_cover(
                    path=str(cover_path),
                    source=cover_source or Collection.COVER_SOURCE_UPLOADED,
                    generation_type=cover_gen_type,
                )
                cover_imported = True
            except (IOError, OSError, ValueError) as e:
                logger.warning(f"Failed to import cover image: {e}")
            finally:
                # Clean up temp file
                if tmp_cover_path:
                    try:
                        Path(tmp_cover_path).unlink(missing_ok=True)
                    except OSError:
                        pass

        # Build index of game metadata files and images in ZIP
        game_metadata = {}  # safe_name -> metadata dict
        game_images = {}  # safe_name -> list of (image_type, zip_path)

        for name in zipf.namelist():
            if name.startswith("games/") and name.endswith(".json"):
                # Parse game metadata
                safe_name = Path(name).stem
                try:
                    with zipf.open(name) as f:
                        game_metadata[safe_name] = json.loads(f.read().decode("utf-8"))
                except (json.JSONDecodeError, UnicodeDecodeError):
                    pass  # Skip invalid metadata files

            elif name.startswith("images/") and not name.endswith("/"):
                # Parse image paths: images/GameName_system/image_type.ext
                parts = Path(name).parts
                if len(parts) >= 3:
                    safe_name = parts[1]  # GameName_system
                    image_filename = parts[2]
                    image_type = Path(image_filename).stem  # cover, screenshot, etc.
                    if image_type == "unknown":
                        image_type = ""
                    if safe_name not in game_images:
                        game_images[safe_name] = []
                    game_images[safe_name].append((image_type, name))

        images_imported = 0
        images_dir = _get_images_dir()
        allowed_image_types = {
            v for v, _ in GameImage._meta.get_field("image_type").choices
        }

        # Batch-fetch matched games to avoid N+1 queries
        entries_list = list(collection.entries.all())
        matched_games = _prefetch_matched_games_for_entries(entries_list)

        # Prefetch existing image types for all matched games
        game_ids = [g.id for g in matched_games.values()]
        existing_images_qs = (
            GameImage.objects.filter(game_id__in=game_ids)
            .values_list("game_id", "image_type")
        )
        existing_images_by_game: dict[int, set[str]] = {}
        for game_id, image_type in existing_images_qs:
            if game_id not in existing_images_by_game:
                existing_images_by_game[game_id] = set()
            existing_images_by_game[game_id].add(image_type)

        # Process each entry in the collection
        for entry in entries_list:
            key = (entry.game_name.lower(), entry.system_slug)
            game = matched_games.get(key)
            if not game:
                continue

            # Build the safe name to look up metadata and images
            safe_name = _sanitize_filename(f"{game.name}_{game.system.slug}")

            # Apply metadata if available and game doesn't have it
            if safe_name in game_metadata:
                metadata = game_metadata[safe_name]
                _apply_metadata_to_game(game, metadata)

            # Import images
            if safe_name in game_images:
                # Get existing image types for this game (from prefetched data)
                existing_types = existing_images_by_game.get(game.id, set())

                for image_type, zip_image_path in game_images[safe_name]:
                    # Normalize/validate type (don't persist unknown strings)
                    if image_type not in allowed_image_types:
                        image_type = ""

                    # Skip if game already has this image type
                    if image_type in existing_types:
                        continue

                    try:
                        # Extract image to disk
                        ext = Path(zip_image_path).suffix.lower()
                        dest_dir = images_dir / game.system.slug
                        dest_dir.mkdir(parents=True, exist_ok=True)

                        # Generate unique filename
                        base_name = _sanitize_filename(game.name)
                        dest_filename = f"{base_name}_{image_type}{ext}"
                        dest_path = dest_dir / dest_filename

                        # Handle duplicates
                        counter = 1
                        while dest_path.exists():
                            dest_filename = f"{base_name}_{image_type}_{counter}{ext}"
                            dest_path = dest_dir / dest_filename
                            counter += 1

                        # Extract the image
                        with zipf.open(zip_image_path) as src:
                            with open(dest_path, "wb") as dst:
                                shutil.copyfileobj(src, dst)

                        # Create GameImage record
                        GameImage.objects.create(
                            game=game,
                            file_path=str(dest_path),
                            file_name=dest_filename,
                            file_size=dest_path.stat().st_size,
                            image_type=image_type,
                            source="downloaded",
                        )
                        images_imported += 1
                        existing_types.add(image_type)

                    except (IOError, OSError):
                        pass  # Skip failed image imports

    result["images_imported"] = images_imported
    result["cover_imported"] = cover_imported
    return result


def _apply_metadata_to_game(game: Game, metadata: dict[str, Any]) -> None:
    """Apply metadata from import to a game if it's missing.

    Only updates fields that are currently empty on the game.
    """
    updated = False

    if not game.description and metadata.get("description"):
        game.description = metadata["description"]
        updated = True

    if not game.genres.exists() and metadata.get("genres"):
        from library.metadata.normalize import normalize_genres
        from library.metadata.genres import get_or_create_genre_with_parent

        for genre_name in normalize_genres(metadata["genres"]):
            if genre_name and isinstance(genre_name, str):
                genre = get_or_create_genre_with_parent(genre_name)
                game.genres.add(genre)
        updated = True

    if not game.release_date and metadata.get("release_date"):
        try:
            game.release_date = date.fromisoformat(metadata["release_date"])
            updated = True
        except (ValueError, TypeError):
            pass

    if not game.developer and metadata.get("developer"):
        game.developer = metadata["developer"]
        updated = True

    if not game.publisher and metadata.get("publisher"):
        game.publisher = metadata["publisher"]
        updated = True

    if not game.players and metadata.get("players"):
        game.players = metadata["players"]
        updated = True

    if game.rating is None and metadata.get("rating") is not None:
        game.rating = metadata["rating"]
        game.rating_source = metadata.get("rating_source", "imported")
        updated = True

    if not game.screenscraper_id and metadata.get("screenscraper_id"):
        game.screenscraper_id = metadata["screenscraper_id"]
        updated = True

    if updated:
        game.save()
