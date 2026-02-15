"""Cover image processing utilities for collections."""

import io
import logging
from pathlib import Path
from typing import TYPE_CHECKING

from django.conf import settings
from PIL import Image, ImageFilter

if TYPE_CHECKING:
    from library.models import GameImage

logger = logging.getLogger(__name__)

# Cover image dimensions (for uploaded images)
COVER_MAX_WIDTH = 800
COVER_MAX_HEIGHT = 300

# Collage settings - banner format for card display
# Images are sized large to fill the banner; card displays with object-cover
COLLAGE_CANVAS_WIDTH = 800
COLLAGE_CANVAS_HEIGHT = 300
COLLAGE_IMAGE_WIDTH = 220  # Wide enough for landscape screenshots
COLLAGE_IMAGE_HEIGHT = 280  # Tall enough to fill vertical space
COLLAGE_ROTATIONS = [-8, -4, 0, 4, 8]  # degrees for each image


def _get_images_dir() -> Path:
    """Get the directory for storing images."""
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


def get_collection_cover_dir(collection_slug: str) -> Path:
    """Get the storage directory for a collection's cover image.

    Creates the directory if it doesn't exist.

    Args:
        collection_slug: The collection's slug

    Returns:
        Path to the collection's cover directory
    """
    cover_dir = _get_images_dir() / "collections" / collection_slug
    cover_dir.mkdir(parents=True, exist_ok=True)
    return cover_dir


def get_collection_cover_path(collection_slug: str) -> Path:
    """Get the full path for a collection's cover image.

    Args:
        collection_slug: The collection's slug

    Returns:
        Path to the cover image file (may not exist)
    """
    return get_collection_cover_dir(collection_slug) / "cover.png"


def resize_cover_image(
    source_path: str | Path,
    max_width: int = COVER_MAX_WIDTH,
    max_height: int = COVER_MAX_HEIGHT,
) -> bytes:
    """Resize an uploaded image to fit within max dimensions.

    Maintains aspect ratio - the image will fit within the bounding box
    defined by max_width and max_height.

    Args:
        source_path: Path to source image file
        max_width: Maximum width in pixels
        max_height: Maximum height in pixels

    Returns:
        PNG image data as bytes

    Raises:
        FileNotFoundError: If source file doesn't exist
        IOError: If image processing fails
    """
    with Image.open(source_path) as img:
        # Convert to RGBA for consistent handling
        if img.mode != "RGBA":
            img = img.convert("RGBA")

        original_width, original_height = img.size

        # Calculate scale to fit within bounding box
        width_ratio = max_width / original_width
        height_ratio = max_height / original_height
        scale = min(width_ratio, height_ratio, 1.0)  # Don't upscale

        if scale < 1.0:
            new_width = int(original_width * scale)
            new_height = int(original_height * scale)
            img = img.resize((new_width, new_height), Image.Resampling.LANCZOS)
            logger.debug(
                f"Resized cover from {original_width}x{original_height} "
                f"to {new_width}x{new_height}"
            )

        # Save as RGBA PNG to preserve transparency
        output = io.BytesIO()
        img.save(output, format="PNG")
        return output.getvalue()


def create_collage_cover(
    game_images: list["GameImage"],
    canvas_width: int = COLLAGE_CANVAS_WIDTH,
    canvas_height: int = COLLAGE_CANVAS_HEIGHT,
) -> bytes:
    """Create a fan-style collage from multiple game images.

    Creates a composite image with up to 5 game images arranged in
    a "fan" pattern - overlapping and rotated like playing cards.
    Optimized for banner display in collection cards.

    Args:
        game_images: List of GameImage objects (max 5 used)
        canvas_width: Width of output canvas
        canvas_height: Height of output canvas

    Returns:
        PNG image data as bytes

    Raises:
        ValueError: If no valid images provided
    """
    # Filter to valid images and limit to 5
    valid_images = []
    for img in game_images[:5]:
        if img and img.file_path and Path(img.file_path).exists():
            valid_images.append(img)

    if not valid_images:
        raise ValueError("No valid images provided for collage")

    # Create canvas with transparent background
    canvas = Image.new("RGBA", (canvas_width, canvas_height), (0, 0, 0, 0))

    # Calculate layout based on number of images
    num_images = len(valid_images)

    # Adjust rotations based on count (smaller angles for banner format)
    if num_images == 1:
        rotations = [0]
    elif num_images == 2:
        rotations = [-4, 4]
    elif num_images == 3:
        rotations = [-6, 0, 6]
    elif num_images == 4:
        rotations = [-8, -3, 3, 8]
    else:
        rotations = COLLAGE_ROTATIONS

    # Center position - vertically centered in banner
    center_x = canvas_width // 2
    center_y = canvas_height // 2

    # Calculate spacing - spread images across the banner width
    # Higher = more overlap, lower = more spread. With larger images, use more overlap.
    overlap_factor = 0.50
    total_width = COLLAGE_IMAGE_WIDTH * (1 + (num_images - 1) * overlap_factor)
    start_x = center_x - total_width // 2

    images_placed = 0
    for i, game_image in enumerate(valid_images):
        try:
            with Image.open(game_image.file_path) as img:
                # Convert to RGBA
                if img.mode != "RGBA":
                    img = img.convert("RGBA")

                # Resize to collage image size maintaining aspect ratio
                img.thumbnail(
                    (COLLAGE_IMAGE_WIDTH, COLLAGE_IMAGE_HEIGHT),
                    Image.Resampling.LANCZOS,
                )

                # Use the image at its actual size (no padding to fixed canvas)
                # This respects the natural aspect ratio of covers vs screenshots

                # Add border directly to the resized image
                bordered = _add_border(img, color=(80, 80, 80), width=3)

                # Add drop shadow
                with_shadow = _add_drop_shadow(bordered, offset=(3, 3), blur=6)

                # Rotate
                rotation = rotations[i] if i < len(rotations) else 0
                rotated = with_shadow.rotate(
                    rotation, expand=True, resample=Image.Resampling.BICUBIC
                )

                # Calculate position with overlap
                x_offset = int(start_x + i * COLLAGE_IMAGE_WIDTH * overlap_factor)
                # Center the rotated image based on the standard width
                x_offset -= (rotated.width - COLLAGE_IMAGE_WIDTH) // 2
                y_offset = center_y - rotated.height // 2

                # Slight vertical variation for depth effect
                if num_images >= 3:
                    # Middle images slightly higher
                    middle_idx = num_images // 2
                    distance_from_middle = abs(i - middle_idx)
                    y_offset += distance_from_middle * 5

                # Paste with alpha composite
                canvas.paste(rotated, (x_offset, y_offset), rotated)
                images_placed += 1

        except Exception as e:
            logger.warning(f"Failed to process image {game_image.file_path}: {e}")
            continue

    if images_placed == 0:
        raise ValueError("Failed to process any images for collage")

    # Crop canvas to remove empty transparent space
    # Get bounding box of non-transparent pixels
    bbox = canvas.getbbox()
    if bbox:
        # bbox returns (left, upper, right, lower)
        left, upper, right, lower = bbox
        # Only crop height (upper/lower), keep full width for banner format
        # Use the full canvas width but crop to the content height
        canvas = canvas.crop((0, upper, canvas_width, lower))

    output = io.BytesIO()
    canvas.save(output, format="PNG")
    return output.getvalue()


def _add_border(
    img: Image.Image, color: tuple[int, int, int] = (60, 60, 60), width: int = 2
) -> Image.Image:
    """Add a border around an image.

    Args:
        img: PIL Image in RGBA mode
        color: Border color as RGB tuple
        width: Border width in pixels

    Returns:
        New image with border
    """
    bordered = Image.new(
        "RGBA",
        (img.width + width * 2, img.height + width * 2),
        (*color, 255),
    )
    bordered.paste(img, (width, width), img)
    return bordered


def _add_drop_shadow(
    img: Image.Image,
    offset: tuple[int, int] = (4, 4),
    blur: int = 8,
    shadow_color: tuple[int, int, int, int] = (0, 0, 0, 100),
) -> Image.Image:
    """Add a drop shadow to an image.

    Args:
        img: PIL Image in RGBA mode
        offset: Shadow offset (x, y)
        blur: Shadow blur radius
        shadow_color: Shadow color as RGBA tuple

    Returns:
        New image with shadow (larger to accommodate shadow)
    """
    # Calculate new size to fit shadow
    shadow_size = blur * 2 + max(abs(offset[0]), abs(offset[1]))
    new_width = img.width + shadow_size
    new_height = img.height + shadow_size

    # Create shadow layer
    shadow = Image.new("RGBA", (new_width, new_height), (0, 0, 0, 0))

    # Create shadow shape from alpha channel
    if img.mode == "RGBA":
        alpha = img.split()[3]
        shadow_shape = Image.new("RGBA", img.size, shadow_color)
        shadow_shape.putalpha(alpha)
    else:
        shadow_shape = Image.new("RGBA", img.size, shadow_color)

    # Paste shadow at offset
    shadow_x = shadow_size // 2 + offset[0]
    shadow_y = shadow_size // 2 + offset[1]
    shadow.paste(shadow_shape, (shadow_x, shadow_y))

    # Blur shadow
    shadow = shadow.filter(ImageFilter.GaussianBlur(blur))

    # Paste original image on top
    img_x = shadow_size // 2
    img_y = shadow_size // 2
    shadow.paste(img, (img_x, img_y), img)

    return shadow


def get_sample_game_images(
    collection, image_type: str = "cover", limit: int = 5
) -> list["GameImage"]:
    """Get sample game images from a collection for cover generation.

    Args:
        collection: Collection instance
        image_type: Type of image to prefer ("cover", "screenshot", "mix")
        limit: Maximum number of images to return

    Returns:
        List of GameImage objects
    """
    images = []
    seen_games = set()

    # Get matched games with images (ordered by collection position)
    for entry in collection.entries.order_by("position"):
        if len(images) >= limit:
            break

        game = entry.get_matched_game()
        if not game or game.pk in seen_games:
            continue
        seen_games.add(game.pk)

        # Try preferred type first, then fallback
        image = game.images.filter(image_type=image_type).first()
        if not image:
            # Fallback order depends on requested type
            if image_type == "cover":
                fallbacks = ["mix", "screenshot"]
            elif image_type == "screenshot":
                fallbacks = ["mix", "cover"]
            else:  # mix
                fallbacks = ["cover", "screenshot"]

            for fallback in fallbacks:
                image = game.images.filter(image_type=fallback).first()
                if image:
                    break

        if image and image.file_path and Path(image.file_path).exists():
            images.append(image)

    # Reverse so position #1 is rightmost (placed last, fully visible)
    return images[::-1]
