"""Multi-game download bundling service.

This module provides functions to create ZIP bundles containing multiple games.
Designed to be reusable by the Collections feature (Phase 2).
"""

import tempfile
import zipfile
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Generator

from .download import get_rom_file
from .image_utils import prepare_image_for_device
from .models import Game, ROMSet


@dataclass
class BundleProgress:
    """Progress tracking for bundle creation."""

    total_games: int
    games_processed: int = 0
    current_game: str = ""
    bytes_written: int = 0
    images_added: int = 0


def get_default_romset(game: Game) -> ROMSet | None:
    """Get the ROMSet to use for a game download.

    Uses scoring-based selection to pick the best available ROMSet,
    considering region priority and archive type (standalone preferred).

    Args:
        game: Game instance

    Returns:
        ROMSet to download, or None if no available ROMs
    """
    from .romset_scoring import get_best_romset

    return get_best_romset(game)


def iter_game_files(
    game: Game,
) -> Generator[tuple[str, str], None, None]:
    """Yield (filename_in_zip, temp_or_real_file_path) tuples for a game.

    Handles multi-disc games by adding disc numbers to filenames.

    Args:
        game: Game instance to get files for

    Yields:
        Tuple of (filename_for_zip, file_path_to_add)
    """
    rom_set = get_default_romset(game)
    if not rom_set:
        return

    for rom in rom_set.roms.all():
        with get_rom_file(rom) as (file_path, filename):
            # For multi-disc, include disc number
            if rom.disc is not None:
                name_stem = Path(filename).stem
                extension = Path(filename).suffix
                filename = f"{name_stem} (Disc {rom.disc}){extension}"

            yield (filename, file_path)


def _add_game_image_to_zip(
    zipf: zipfile.ZipFile,
    game: Game,
    device,
    rom_filename: str,
) -> bool:
    """Add game image to ZIP archive.

    Args:
        zipf: Open ZipFile to write to
        game: Game to get image for
        device: Device with image configuration
        rom_filename: ROM filename (used for image path building)

    Returns:
        True if image was added, False otherwise
    """
    if not device or not device.include_images:
        return False

    # Get image path from device configuration
    image_path = device.get_image_path(game.system.slug, rom_filename)
    if not image_path:
        return False

    # Prepare image data
    result = prepare_image_for_device(
        game=game,
        image_type=device.image_type,
        max_width=device.image_max_width,
    )
    if not result:
        return False

    image_data, ext = result

    # Add to ZIP
    try:
        zipf.writestr(image_path, image_data.read())
        return True
    except Exception:
        return False


def create_multi_game_bundle(
    games: list[Game],
    bundle_name: str = "download",
    progress_callback: Callable[[BundleProgress], None] | None = None,
    device_id: int | None = None,
) -> tuple[str, str]:
    """Create ZIP bundle containing multiple games.

    Games are organized into folders by game name. Multi-disc games have their
    ROMs added directly to the game folder (not nested archives).

    Handles missing files gracefully by skipping them and adding a
    "missing_files.txt" log to the ZIP.

    Args:
        games: List of Game instances to bundle
        bundle_name: Base name for the ZIP file
        progress_callback: Optional callback called as games are processed
        device_id: Optional Device ID for device-specific path configuration

    Returns:
        Tuple of (temp_zip_path, suggested_filename)

    Raises:
        ValueError: If no games provided or all games have no available ROMs
    """
    if not games:
        raise ValueError("No games provided for bundling")

    # Filter to games with available ROMs
    games_with_roms = [g for g in games if get_default_romset(g) is not None]
    if not games_with_roms:
        raise ValueError("No games have available ROMs")

    # Get device config if specified
    device = None
    if device_id:
        from devices.models import Device

        device = Device.objects.filter(pk=device_id).first()

    progress = BundleProgress(total_games=len(games_with_roms))

    # Create temp ZIP file
    with tempfile.NamedTemporaryFile(suffix=".zip", delete=False) as temp_file:
        zip_path = temp_file.name

    try:
        missing_files = []
        added_files = 0

        with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as zipf:
            for game in games_with_roms:
                progress.current_game = game.name

                # Create game folder path - sanitize name for filesystem
                game_folder = _sanitize_filename(game.name)

                try:
                    first_rom_filename = None
                    for filename, file_path in iter_game_files(game):
                        # Track first ROM filename for image path building
                        if first_rom_filename is None:
                            first_rom_filename = filename

                        # Full path in ZIP
                        if device:
                            # Use device-specific path configuration
                            zip_internal_path = device.get_rom_path(
                                system_slug=game.system.slug,
                                game_name=game_folder,
                                filename=filename,
                            )
                        else:
                            # Default: GameName/filename.ext
                            zip_internal_path = f"{game_folder}/{filename}"

                        zipf.write(file_path, zip_internal_path)
                        progress.bytes_written += Path(file_path).stat().st_size
                        added_files += 1

                    # Add image for this game (after all ROMs added)
                    if first_rom_filename and device and device.include_images:
                        if _add_game_image_to_zip(
                            zipf, game, device, first_rom_filename
                        ):
                            progress.images_added += 1

                except (FileNotFoundError, IOError, OSError) as e:
                    # Log the missing file and continue with next game
                    missing_files.append(f"{game.name}: {e}")

                progress.games_processed += 1
                if progress_callback:
                    progress_callback(progress)

            # Add missing files log if any files were skipped
            if missing_files:
                missing_content = "The following games could not be included:\n\n"
                missing_content += "\n".join(missing_files)
                zipf.writestr("missing_files.txt", missing_content)

        # Raise error only if ALL files failed
        if added_files == 0 and missing_files:
            if Path(zip_path).exists():
                Path(zip_path).unlink()
            raise ValueError(f"All game files are missing: {', '.join(missing_files)}")

        # Generate filename
        suggested_filename = f"{bundle_name}.zip"
        return (zip_path, suggested_filename)

    except Exception:
        # Clean up temp file on error
        if Path(zip_path).exists():
            Path(zip_path).unlink()
        raise


def _sanitize_filename(name: str) -> str:
    """Sanitize a string for use as a filename/folder name.

    Removes/replaces characters that are problematic in filenames.
    """
    # Replace problematic characters
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

    # Trim whitespace and dots from ends
    return name.strip(". ")


def cleanup_expired_downloads() -> int:
    """Delete expired download bundles and their database records.

    Should be called periodically (e.g., when worker starts).

    Returns:
        Number of downloads cleaned up
    """
    from django.utils import timezone

    from .models import DownloadJob

    expired = DownloadJob.objects.filter(expires_at__lt=timezone.now())

    count = 0
    for job in expired:
        if job.file_path and Path(job.file_path).exists():
            try:
                Path(job.file_path).unlink()
            except (OSError, IOError):
                pass  # File might already be deleted
        job.delete()
        count += 1

    return count
