"""Image processing utilities for device exports and game image uploads."""

import io
import logging
import uuid
from pathlib import Path
from typing import TYPE_CHECKING

from django.conf import settings
from PIL import Image

if TYPE_CHECKING:
    from django.core.files.uploadedfile import UploadedFile

    from library.models import Game, GameImage

logger = logging.getLogger(__name__)


def validate_metadata_path(path: str | None) -> tuple[bool, str]:
    """Validate that a metadata storage path is usable.

    Args:
        path: The path to validate

    Returns:
        Tuple of (is_valid, status_message)
    """
    if not path:
        return False, "No path configured"

    path_obj = Path(path)

    if path_obj.exists():
        if not path_obj.is_dir():
            return False, "Path exists but is not a directory"
        # Test writability
        try:
            test_file = path_obj / ".romhoard_write_test"
            test_file.touch()
            test_file.unlink()
            return True, "Path exists and is writable"
        except OSError:
            return False, "Path exists but is not writable"

    # Path doesn't exist - check if parent is writable
    parent = path_obj.parent
    if not parent.exists():
        return False, "Parent directory does not exist"

    try:
        path_obj.mkdir(parents=False, exist_ok=True)
        return True, "Created directory successfully"
    except OSError as e:
        return False, f"Cannot create directory: {e}"


# Max dimensions for uploaded images
COVER_MAX_WIDTH = 1200
COVER_MAX_HEIGHT = 1200
SCREENSHOT_MAX_WIDTH = 1920
SCREENSHOT_MAX_HEIGHT = 1080


def _get_images_dir() -> Path:
    """Get computed default directory for storing images (no DB check)."""
    # Check dedicated image storage path first
    image_path = getattr(settings, "IMAGE_STORAGE_PATH", None)
    if image_path:
        return Path(image_path)
    # Fallback to ROM library root
    library_root = getattr(settings, "ROM_LIBRARY_ROOT", None)
    if library_root:
        return Path(library_root) / "images"
    if settings.MEDIA_ROOT:
        return Path(settings.MEDIA_ROOT) / "images"
    # Default to data/images/ in the project directory
    return settings.BASE_DIR / "data" / "images"


def get_image_storage_path() -> Path:
    """Get image storage path from DB setting, env var, or computed default.

    This is the single source of truth for where metadata images are stored.
    Checks in order:
    1. Database setting (metadata_image_path)
    2. Environment variable (IMAGE_STORAGE_PATH)
    3. Computed default from _get_images_dir()

    Returns:
        Path to the image storage directory
    """
    from library.models import Setting

    # Check database setting first
    setting = Setting.objects.filter(key="metadata_image_path").first()
    if setting and setting.value:
        return Path(setting.value)

    # Fall back to computed default (checks env var internally)
    return _get_images_dir()


def get_game_images_dir(game: "Game") -> Path:
    """Get the storage directory for a game's uploaded images.

    Creates the directory if it doesn't exist.

    Args:
        game: Game instance

    Returns:
        Path to the game's images directory
    """
    images_dir = _get_images_dir() / "games" / game.system.slug / str(game.pk)
    images_dir.mkdir(parents=True, exist_ok=True)
    return images_dir


def save_uploaded_image(
    game: "Game",
    uploaded_file: "UploadedFile",
    image_type: str,
    max_width: int | None = None,
    max_height: int | None = None,
) -> "GameImage":
    """Save an uploaded image file for a game.

    Processes the image (resize if needed), saves to disk, and creates
    a GameImage record.

    Args:
        game: Game instance
        uploaded_file: Django UploadedFile object
        image_type: Type of image ("cover", "screenshot", etc.)
        max_width: Maximum width (defaults based on image_type)
        max_height: Maximum height (defaults based on image_type)

    Returns:
        Created GameImage instance

    Raises:
        ValueError: If image processing fails
    """
    from library.models import GameImage

    # Set defaults based on image type
    if max_width is None:
        max_width = COVER_MAX_WIDTH if image_type == "cover" else SCREENSHOT_MAX_WIDTH
    if max_height is None:
        max_height = (
            COVER_MAX_HEIGHT if image_type == "cover" else SCREENSHOT_MAX_HEIGHT
        )

    # Generate unique filename
    ext = Path(uploaded_file.name).suffix.lower()
    if ext not in (".png", ".jpg", ".jpeg", ".gif", ".webp"):
        ext = ".png"
    unique_name = f"{image_type}_{uuid.uuid4().hex[:8]}{ext}"

    # Get save directory
    save_dir = get_game_images_dir(game)
    save_path = save_dir / unique_name

    try:
        # Process and save image
        with Image.open(uploaded_file) as img:
            # Convert to RGB if needed (for JPEG output)
            if img.mode == "RGBA" and ext in (".jpg", ".jpeg"):
                background = Image.new("RGB", img.size, (255, 255, 255))
                background.paste(img, mask=img.split()[3])
                img = background
            elif img.mode not in ("RGB", "RGBA"):
                img = img.convert("RGB")

            # Resize if needed (maintain aspect ratio)
            original_width, original_height = img.size
            if original_width > max_width or original_height > max_height:
                ratio = min(max_width / original_width, max_height / original_height)
                new_width = int(original_width * ratio)
                new_height = int(original_height * ratio)
                img = img.resize((new_width, new_height), Image.Resampling.LANCZOS)
                logger.debug(
                    f"Resized uploaded image from {original_width}x{original_height} "
                    f"to {new_width}x{new_height}"
                )

            # Save to disk
            if ext in (".jpg", ".jpeg"):
                img.save(save_path, format="JPEG", quality=90)
            else:
                img.save(save_path, format="PNG")

        # Get file size
        file_size = save_path.stat().st_size

        # Create GameImage record
        game_image = GameImage.objects.create(
            game=game,
            file_path=str(save_path),
            file_name=unique_name,
            file_size=file_size,
            image_type=image_type,
            source="uploaded",
        )

        logger.info(
            f"Saved uploaded {image_type} image for game {game.pk}: {save_path}"
        )
        return game_image

    except Exception as e:
        # Clean up file if it was created
        if save_path.exists():
            save_path.unlink()
        logger.error(f"Failed to save uploaded image: {e}")
        raise ValueError(f"Failed to process image: {e}") from e


def delete_game_image(game_image: "GameImage", delete_file: bool = True) -> bool:
    """Delete a GameImage and optionally its file.

    Args:
        game_image: GameImage instance to delete
        delete_file: Whether to delete the file from disk

    Returns:
        True if successful
    """
    file_path = Path(game_image.file_path)

    # Delete the database record first
    game_image.delete()

    # Delete file if requested and it exists
    if delete_file and file_path.exists():
        try:
            file_path.unlink()
            logger.info(f"Deleted image file: {file_path}")
        except Exception as e:
            logger.warning(f"Failed to delete image file {file_path}: {e}")

    return True


def get_game_image(game: "Game", image_type: str = "cover") -> "GameImage | None":
    """Get the preferred image for a game.

    Tries to get the specified image type first, then falls back to other types.

    Args:
        game: Game instance
        image_type: Preferred image type ("cover", "screenshot", "mix")

    Returns:
        GameImage instance or None if no image available
    """
    # Try preferred type first
    image = game.images.filter(image_type=image_type).first()
    if image:
        return image

    # Fallback order: cover -> mix -> screenshot
    fallback_order = ["cover", "mix", "screenshot"]
    for fallback_type in fallback_order:
        if fallback_type != image_type:
            image = game.images.filter(image_type=fallback_type).first()
            if image:
                return image

    return None


def resize_image_to_width(
    source_path: str,
    max_width: int | None,
    output_format: str = "PNG",
) -> io.BytesIO:
    """Resize an image to max width, maintaining aspect ratio.

    Args:
        source_path: Path to source image file
        max_width: Maximum width in pixels (None = no resize)
        output_format: Output format (PNG, JPEG)

    Returns:
        BytesIO containing the (possibly resized) image data

    Raises:
        FileNotFoundError: If source file doesn't exist
        IOError: If image processing fails
    """
    output = io.BytesIO()

    with Image.open(source_path) as img:
        original_width = img.width
        original_height = img.height

        if max_width and img.width > max_width:
            # Calculate new height maintaining aspect ratio
            ratio = max_width / img.width
            new_height = int(img.height * ratio)

            # Resize with high-quality resampling
            img = img.resize((max_width, new_height), Image.Resampling.LANCZOS)
            logger.debug(
                f"Resized image from {original_width}x{original_height} "
                f"to {max_width}x{new_height}"
            )

        # Convert RGBA to RGB for JPEG output
        if output_format.upper() == "JPEG" and img.mode == "RGBA":
            # Create white background
            background = Image.new("RGB", img.size, (255, 255, 255))
            background.paste(img, mask=img.split()[3])  # Use alpha as mask
            img = background

        # Save to BytesIO
        save_kwargs = {"quality": 95} if output_format.upper() == "JPEG" else {}
        img.save(output, format=output_format, **save_kwargs)

    output.seek(0)
    return output


def prepare_image_for_device(
    game: "Game",
    image_type: str,
    max_width: int | None,
) -> tuple[io.BytesIO, str] | None:
    """Prepare a game image for device transfer.

    Gets the preferred image for the game, resizes if needed, and returns
    the image data as a BytesIO object.

    Args:
        game: Game instance
        image_type: Preferred image type ("cover", "screenshot")
        max_width: Maximum width for resize (None = no resize)

    Returns:
        Tuple of (image_data BytesIO, original_extension) or None if no image
    """
    image = get_game_image(game, image_type)
    if not image:
        return None

    source_path = image.file_path
    if not Path(source_path).exists():
        logger.warning(f"Image file not found: {source_path}")
        return None

    # Determine output format from source
    ext = Path(source_path).suffix.lower()
    if ext in (".jpg", ".jpeg"):
        output_format = "JPEG"
    else:
        output_format = "PNG"

    try:
        image_data = resize_image_to_width(source_path, max_width, output_format)
        return (image_data, ext if ext else ".png")
    except Exception as e:
        logger.error(f"Failed to process image {source_path}: {e}")
        return None


def humanize_bytes(size_bytes: int) -> str:
    """Convert bytes to human-readable string.

    Args:
        size_bytes: Size in bytes

    Returns:
        Human-readable string like "1.5 GB"
    """
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if abs(size_bytes) < 1024:
            return f"{size_bytes:.1f} {unit}"
        size_bytes /= 1024
    return f"{size_bytes:.1f} PB"


def get_downloaded_images_stats() -> dict:
    """Get statistics about downloaded images (from metadata service).

    Returns:
        Dictionary with:
            - count: Number of downloaded images
            - size_bytes: Total size in bytes
            - size_human: Human-readable size string
            - system_icons_count: Number of system icons
            - system_icons_size: Size of system icons in bytes
    """
    from library.models import GameImage, System

    # Get all downloaded GameImages
    downloaded_images = GameImage.objects.filter(source="downloaded")

    count = 0
    total_size = 0

    for image in downloaded_images:
        count += 1
        file_path = Path(image.file_path)
        if file_path.exists():
            try:
                total_size += file_path.stat().st_size
            except OSError:
                pass

    # Get system icons
    systems_with_icons = System.objects.exclude(icon_path="")
    icon_count = 0
    icon_size = 0

    for system in systems_with_icons:
        if system.icon_path:
            icon_path = Path(system.icon_path)
            if icon_path.exists():
                icon_count += 1
                try:
                    icon_size += icon_path.stat().st_size
                except OSError:
                    pass

    total_count = count + icon_count
    total_bytes = total_size + icon_size

    return {
        "count": total_count,
        "size_bytes": total_bytes,
        "size_human": humanize_bytes(total_bytes),
        "game_images_count": count,
        "game_images_size": total_size,
        "system_icons_count": icon_count,
        "system_icons_size": icon_size,
    }


def move_downloaded_image(
    old_path: str | Path,
    old_base: str | Path,
    new_base: str | Path,
) -> tuple[Path | None, str | None]:
    """Move a single downloaded image from old base to new base.

    Preserves the directory structure relative to the base path.
    Skips if destination already exists.

    Args:
        old_path: Current absolute path of the image
        old_base: Base path to strip from old_path
        new_base: New base path to prepend

    Returns:
        Tuple of (new_path, error_message). new_path is None on skip/error.
        error_message is None on success, "skipped" if file already exists.
    """
    import shutil

    old_path = Path(old_path)
    old_base = Path(old_base)
    new_base = Path(new_base)

    if not old_path.exists():
        return None, f"Source file not found: {old_path}"

    try:
        # Calculate relative path from old base
        relative_path = old_path.relative_to(old_base)
        new_path = new_base / relative_path

        # Skip if destination already exists
        if new_path.exists():
            return None, "skipped"

        # Create destination directory
        new_path.parent.mkdir(parents=True, exist_ok=True)

        # Move the file
        shutil.move(str(old_path), str(new_path))
        logger.debug(f"Moved image: {old_path} -> {new_path}")
        return new_path, None

    except ValueError:
        # old_path is not relative to old_base
        return None, f"Path {old_path} is not under base {old_base}"
    except Exception as e:
        return None, str(e)


def delete_downloaded_image(file_path: str | Path) -> str | None:
    """Delete a downloaded image file from disk.

    Args:
        file_path: Path to the image file

    Returns:
        None on success, error message on failure
    """
    file_path = Path(file_path)

    if not file_path.exists():
        return None  # Already gone, not an error

    try:
        file_path.unlink()
        logger.debug(f"Deleted image: {file_path}")

        # Try to remove empty parent directories up to a point
        _cleanup_empty_dirs(file_path.parent)

        return None
    except Exception as e:
        return str(e)


def _cleanup_empty_dirs(directory: Path, max_depth: int = 3) -> None:
    """Remove empty directories up the tree.

    Args:
        directory: Starting directory
        max_depth: Maximum number of parent levels to clean
    """
    current = directory
    for _ in range(max_depth):
        if not current.exists():
            current = current.parent
            continue

        try:
            # Only remove if empty
            if not any(current.iterdir()):
                current.rmdir()
                logger.debug(f"Removed empty directory: {current}")
                current = current.parent
            else:
                break
        except OSError:
            break
