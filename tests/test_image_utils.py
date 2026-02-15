"""Tests for library/image_utils.py."""

import io
import tempfile
from pathlib import Path

import pytest
from PIL import Image

from library.image_utils import (
    get_game_image,
    humanize_bytes,
    prepare_image_for_device,
    resize_image_to_width,
    validate_metadata_path,
)


# -----------------------------------------------------------------------------
# Helper functions
# -----------------------------------------------------------------------------


def create_test_image(width: int, height: int, format: str = "PNG") -> bytes:
    """Create a test image in memory."""
    img = Image.new("RGB", (width, height), color="red")
    buffer = io.BytesIO()
    img.save(buffer, format=format)
    return buffer.getvalue()


def create_rgba_test_image(width: int, height: int) -> bytes:
    """Create a test RGBA image with transparency."""
    img = Image.new("RGBA", (width, height), color=(255, 0, 0, 128))
    buffer = io.BytesIO()
    img.save(buffer, format="PNG")
    return buffer.getvalue()


# -----------------------------------------------------------------------------
# Tests for validate_metadata_path
# -----------------------------------------------------------------------------


class TestValidateMetadataPath:
    """Tests for validate_metadata_path function."""

    def test_empty_path_returns_false(self):
        """Empty path should return False."""
        is_valid, status = validate_metadata_path("")
        assert is_valid is False
        assert "No path configured" in status

    def test_none_path_returns_false(self):
        """None path should return False."""
        is_valid, status = validate_metadata_path(None)
        assert is_valid is False
        assert "No path configured" in status

    def test_existing_writable_directory(self):
        """Existing writable directory should return True."""
        with tempfile.TemporaryDirectory() as tmpdir:
            is_valid, status = validate_metadata_path(tmpdir)
            assert is_valid is True
            assert "writable" in status

    def test_existing_file_returns_false(self):
        """Path that exists but is a file should return False."""
        with tempfile.NamedTemporaryFile(delete=False) as f:
            file_path = f.name

        try:
            is_valid, status = validate_metadata_path(file_path)
            assert is_valid is False
            assert "not a directory" in status
        finally:
            Path(file_path).unlink(missing_ok=True)

    def test_parent_does_not_exist(self):
        """Path with non-existent parent should return False."""
        is_valid, status = validate_metadata_path("/nonexistent/parent/path/metadata")
        assert is_valid is False
        assert "Parent directory does not exist" in status

    def test_can_create_directory(self):
        """Path that can be created should return True."""
        with tempfile.TemporaryDirectory() as tmpdir:
            new_path = Path(tmpdir) / "metadata"
            is_valid, status = validate_metadata_path(str(new_path))
            assert is_valid is True
            assert "Created directory" in status
            assert new_path.exists()


# -----------------------------------------------------------------------------
# Tests for resize_image_to_width
# -----------------------------------------------------------------------------


class TestResizeImageToWidth:
    """Tests for resize_image_to_width function."""

    def test_no_resize_when_smaller_than_max(self, tmp_path):
        """Image smaller than max_width should not be resized."""
        image_path = tmp_path / "test.png"
        image_path.write_bytes(create_test_image(100, 100))

        result = resize_image_to_width(str(image_path), max_width=200)

        output_img = Image.open(result)
        assert output_img.width == 100
        assert output_img.height == 100

    def test_resize_when_larger_than_max(self, tmp_path):
        """Image larger than max_width should be resized maintaining aspect ratio."""
        image_path = tmp_path / "test.png"
        # Create 500x250 image (2:1 ratio)
        image_path.write_bytes(create_test_image(500, 250))

        result = resize_image_to_width(str(image_path), max_width=200)

        output_img = Image.open(result)
        assert output_img.width == 200
        assert output_img.height == 100  # Maintains 2:1 ratio

    def test_no_resize_when_max_width_none(self, tmp_path):
        """Image should not be resized when max_width is None."""
        image_path = tmp_path / "test.png"
        image_path.write_bytes(create_test_image(500, 500))

        result = resize_image_to_width(str(image_path), max_width=None)

        output_img = Image.open(result)
        assert output_img.width == 500
        assert output_img.height == 500

    def test_jpeg_output_format(self, tmp_path):
        """Should produce valid JPEG output."""
        image_path = tmp_path / "test.jpg"
        image_path.write_bytes(create_test_image(100, 100, "JPEG"))

        result = resize_image_to_width(
            str(image_path), max_width=None, output_format="JPEG"
        )

        output_img = Image.open(result)
        assert output_img.format == "JPEG"

    def test_rgba_to_rgb_conversion_for_jpeg(self, tmp_path):
        """RGBA images should be converted to RGB for JPEG output."""
        image_path = tmp_path / "test.png"
        image_path.write_bytes(create_rgba_test_image(100, 100))

        result = resize_image_to_width(
            str(image_path), max_width=None, output_format="JPEG"
        )

        output_img = Image.open(result)
        assert output_img.mode == "RGB"

    def test_file_not_found(self):
        """Should raise FileNotFoundError for missing files."""
        with pytest.raises(FileNotFoundError):
            resize_image_to_width("/nonexistent/path/image.png", max_width=100)


# -----------------------------------------------------------------------------
# Tests for get_game_image
# -----------------------------------------------------------------------------


@pytest.mark.django_db
class TestGetGameImage:
    """Tests for get_game_image function."""

    def test_get_preferred_type(self, game_with_images):
        """Should return image of preferred type if available."""
        result = get_game_image(game_with_images, image_type="cover")

        assert result is not None
        assert result.image_type == "cover"

    def test_fallback_to_cover(self, game_with_screenshot_only):
        """Should fall back to screenshot if cover not available."""
        result = get_game_image(game_with_screenshot_only, image_type="cover")

        assert result is not None
        assert result.image_type == "screenshot"

    def test_no_image_returns_none(self, game_without_images):
        """Should return None if no images available."""
        result = get_game_image(game_without_images, image_type="cover")

        assert result is None


# -----------------------------------------------------------------------------
# Tests for prepare_image_for_device
# -----------------------------------------------------------------------------


@pytest.mark.django_db
class TestPrepareImageForDevice:
    """Tests for prepare_image_for_device function."""

    def test_prepare_with_resize(self, game_with_real_image):
        """Should resize image if max_width specified."""
        game, image_path = game_with_real_image

        result = prepare_image_for_device(game, image_type="cover", max_width=100)

        assert result is not None
        image_data, ext = result

        output_img = Image.open(image_data)
        assert output_img.width <= 100

    def test_prepare_without_resize(self, game_with_real_image):
        """Should not resize when max_width is None."""
        game, image_path = game_with_real_image

        result = prepare_image_for_device(game, image_type="cover", max_width=None)

        assert result is not None
        image_data, ext = result
        assert ext in (".png", ".jpg", ".jpeg")

    def test_prepare_missing_image_returns_none(self, game_without_images):
        """Should return None if game has no images."""
        result = prepare_image_for_device(
            game_without_images, image_type="cover", max_width=100
        )

        assert result is None

    def test_prepare_missing_file_returns_none(self, game_with_missing_image_file):
        """Should return None if image file doesn't exist on disk."""
        result = prepare_image_for_device(
            game_with_missing_image_file, image_type="cover", max_width=100
        )

        assert result is None


# -----------------------------------------------------------------------------
# Tests for humanize_bytes
# -----------------------------------------------------------------------------


class TestHumanizeBytes:
    """Tests for humanize_bytes function."""

    @pytest.mark.parametrize(
        "input_bytes,expected",
        [
            (0, "0.0 B"),
            (1, "1.0 B"),
            (1023, "1023.0 B"),
            (1024, "1.0 KB"),
            (1024 * 1024, "1.0 MB"),
            (1024 * 1024 * 1024, "1.0 GB"),
            (1024 * 1024 * 1024 * 1024, "1.0 TB"),
            # Fractional values
            (1536, "1.5 KB"),
            (1536 * 1024, "1.5 MB"),
            # Large values
            (5 * 1024 * 1024 * 1024, "5.0 GB"),
        ],
    )
    def test_humanize_bytes(self, input_bytes, expected):
        """Test byte to human-readable conversion."""
        assert humanize_bytes(input_bytes) == expected

    def test_humanize_negative_bytes(self):
        """Test handling of negative values."""
        # Function should handle negatives gracefully
        result = humanize_bytes(-1024)
        assert "KB" in result or "B" in result
