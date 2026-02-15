"""Tests for collection cover image functionality."""

import io
from unittest.mock import MagicMock

import pytest
from PIL import Image

from library.models import Game, GameImage, ROMSet, System
from romcollections.cover_utils import (
    COVER_MAX_HEIGHT,
    COVER_MAX_WIDTH,
    create_collage_cover,
    get_collection_cover_dir,
    get_collection_cover_path,
    get_sample_game_images,
    resize_cover_image,
)
from romcollections.models import Collection, CollectionEntry, CoverJob


@pytest.fixture
def collection(db):
    """Create a test collection."""
    return Collection.objects.create(
        slug="test-covers",
        name="Test Cover Collection",
        description="Testing cover functionality",
        creator="test-creator",
    )


@pytest.fixture
def system(db):
    """Get or create a test system."""
    system, _ = System.objects.get_or_create(
        slug="snes",
        defaults={
            "name": "Super Nintendo",
            "extensions": [".sfc", ".smc"],
            "folder_names": ["SNES", "snes"],
        },
    )
    return system


@pytest.fixture
def game_with_image(db, system, tmp_path):
    """Create a game with an image file."""
    game = Game.objects.create(name="Super Mario World", system=system)
    ROMSet.objects.create(game=game, region="USA")

    # Create a test image file
    image_path = tmp_path / "mario_cover.png"
    img = Image.new("RGB", (200, 300), color="red")
    img.save(image_path, "PNG")

    GameImage.objects.create(
        game=game,
        file_path=str(image_path),
        file_name="mario_cover.png",
        file_size=image_path.stat().st_size,
        image_type="cover",
        source="test",
    )
    return game


@pytest.fixture
def sample_image_path(tmp_path):
    """Create a sample image file for testing."""
    image_path = tmp_path / "test_image.png"
    img = Image.new("RGB", (800, 1000), color="blue")
    img.save(image_path, "PNG")
    return image_path


class TestCollectionCoverFields:
    """Test Collection model cover-related fields and methods."""

    def test_has_cover_default_false(self, collection):
        """Test has_cover defaults to False."""
        assert collection.cover_image_path == ""
        assert collection.has_cover is False

    def test_set_cover_updates_fields(self, collection, tmp_path):
        """Test set_cover method updates all cover-related fields."""
        cover_path = tmp_path / "cover.png"
        img = Image.new("RGB", (100, 100), color="green")
        img.save(cover_path, "PNG")

        collection.set_cover(
            path=str(cover_path),
            source=Collection.COVER_SOURCE_UPLOADED,
        )

        assert collection.has_cover is True
        assert collection.cover_image_path == str(cover_path)
        assert collection.cover_source == Collection.COVER_SOURCE_UPLOADED

    def test_set_cover_with_generation_type(self, collection, tmp_path):
        """Test set_cover method with generation type."""
        cover_path = tmp_path / "cover.png"
        img = Image.new("RGB", (100, 100), color="green")
        img.save(cover_path, "PNG")

        collection.set_cover(
            path=str(cover_path),
            source=Collection.COVER_SOURCE_GENERATED,
            generation_type=Collection.COVER_TYPE_SCREENSHOT,
        )

        assert collection.has_cover is True
        assert collection.cover_source == Collection.COVER_SOURCE_GENERATED
        assert collection.cover_generation_type == Collection.COVER_TYPE_SCREENSHOT

    def test_delete_cover_removes_file(self, collection, tmp_path):
        """Test delete_cover removes the file from disk."""
        cover_path = tmp_path / "cover.png"
        img = Image.new("RGB", (100, 100), color="green")
        img.save(cover_path, "PNG")

        collection.cover_image_path = str(cover_path)
        collection.has_cover = True
        collection.cover_source = Collection.COVER_SOURCE_UPLOADED
        collection.save()

        assert cover_path.exists()
        collection.delete_cover()

        assert not cover_path.exists()
        assert collection.cover_image_path == ""
        assert collection.has_cover is False
        assert collection.cover_source == Collection.COVER_SOURCE_NONE

    def test_delete_cover_handles_missing_file(self, collection, tmp_path):
        """Test delete_cover handles missing file gracefully."""
        collection.cover_image_path = str(tmp_path / "nonexistent.png")
        collection.has_cover = True
        collection.cover_source = Collection.COVER_SOURCE_GENERATED
        collection.save()

        # Should not raise
        collection.delete_cover()

        assert collection.cover_image_path == ""
        assert collection.has_cover is False
        assert collection.cover_source == Collection.COVER_SOURCE_NONE

    def test_cover_source_choices(self, collection):
        """Test cover source field accepts valid choices."""
        collection.cover_source = Collection.COVER_SOURCE_UPLOADED
        collection.full_clean(exclude=["tags"])
        collection.save()

        collection.cover_source = Collection.COVER_SOURCE_GENERATED
        collection.full_clean(exclude=["tags"])
        collection.save()

    def test_cover_generation_type_choices(self, collection):
        """Test cover generation type accepts valid choices."""
        for choice, _ in Collection.COVER_TYPE_CHOICES:
            collection.cover_generation_type = choice
            collection.full_clean(exclude=["tags"])


class TestCoverJob:
    """Test CoverJob model."""

    def test_create_upload_job(self, collection):
        """Test creating a cover upload job."""
        job = CoverJob.objects.create(
            collection=collection,
            task_id="test-upload-123",
            job_type=CoverJob.JOB_TYPE_UPLOAD,
            upload_path="/tmp/test.png",
        )
        assert job.pk is not None
        assert job.status == CoverJob.STATUS_PENDING
        assert job.job_type == CoverJob.JOB_TYPE_UPLOAD

    def test_create_generate_job(self, collection):
        """Test creating a cover generation job."""
        job = CoverJob.objects.create(
            collection=collection,
            task_id="test-generate-456",
            job_type=CoverJob.JOB_TYPE_GENERATE,
            image_type=Collection.COVER_TYPE_SCREENSHOT,
        )
        assert job.pk is not None
        assert job.job_type == CoverJob.JOB_TYPE_GENERATE
        assert job.image_type == Collection.COVER_TYPE_SCREENSHOT

    def test_cover_job_str(self, collection):
        """Test CoverJob string representation."""
        job = CoverJob.objects.create(
            collection=collection,
            task_id="test-job-789",
            job_type=CoverJob.JOB_TYPE_GENERATE,
        )
        assert "generate" in str(job).lower()
        assert collection.name in str(job)

    def test_cover_job_status_transitions(self, collection):
        """Test CoverJob status can be updated."""
        job = CoverJob.objects.create(
            collection=collection,
            task_id="test-status-job",
            job_type=CoverJob.JOB_TYPE_UPLOAD,
        )
        assert job.status == CoverJob.STATUS_PENDING

        job.status = CoverJob.STATUS_RUNNING
        job.save()
        job.refresh_from_db()
        assert job.status == CoverJob.STATUS_RUNNING

        job.status = CoverJob.STATUS_COMPLETED
        job.save()
        job.refresh_from_db()
        assert job.status == CoverJob.STATUS_COMPLETED


class TestCoverUtils:
    """Test cover utility functions."""

    def test_get_collection_cover_dir_creates_directory(self, tmp_path, settings):
        """Test get_collection_cover_dir creates the directory."""
        settings.ROM_LIBRARY_ROOT = str(tmp_path)

        cover_dir = get_collection_cover_dir("test-collection")

        assert cover_dir.exists()
        assert cover_dir.is_dir()
        assert "collections" in str(cover_dir)
        assert "test-collection" in str(cover_dir)

    def test_get_collection_cover_path(self, tmp_path, settings):
        """Test get_collection_cover_path returns correct path."""
        settings.ROM_LIBRARY_ROOT = str(tmp_path)

        cover_path = get_collection_cover_path("my-collection")

        assert str(cover_path).endswith("cover.png")
        assert "collections" in str(cover_path)
        assert "my-collection" in str(cover_path)

    def test_resize_cover_image_downscales_large_image(self, sample_image_path):
        """Test resize_cover_image downscales images larger than max dimensions."""
        result = resize_cover_image(sample_image_path)

        # Parse the result as an image
        img = Image.open(io.BytesIO(result))
        assert img.width <= COVER_MAX_WIDTH
        assert img.height <= COVER_MAX_HEIGHT

    def test_resize_cover_image_preserves_aspect_ratio(self, tmp_path):
        """Test resize_cover_image maintains aspect ratio."""
        # Create a wide image (wider than max)
        image_path = tmp_path / "wide.png"
        img = Image.new("RGB", (1600, 400), color="blue")
        img.save(image_path, "PNG")

        result = resize_cover_image(image_path)
        result_img = Image.open(io.BytesIO(result))

        # Should be scaled to fit width (800), height proportional (200)
        assert result_img.width == COVER_MAX_WIDTH
        assert result_img.height == int(400 * (COVER_MAX_WIDTH / 1600))

    def test_resize_cover_image_no_upscale(self, tmp_path):
        """Test resize_cover_image doesn't upscale small images."""
        # Create a small image
        image_path = tmp_path / "small.png"
        img = Image.new("RGB", (100, 150), color="green")
        img.save(image_path, "PNG")

        result = resize_cover_image(image_path)
        result_img = Image.open(io.BytesIO(result))

        # Should remain the same size
        assert result_img.width == 100
        assert result_img.height == 150

    def test_resize_cover_image_returns_png_bytes(self, sample_image_path):
        """Test resize_cover_image returns PNG format bytes."""
        result = resize_cover_image(sample_image_path)

        assert isinstance(result, bytes)
        # PNG magic bytes
        assert result[:8] == b"\x89PNG\r\n\x1a\n"

    def test_resize_cover_image_file_not_found(self, tmp_path):
        """Test resize_cover_image raises for missing file."""
        with pytest.raises(FileNotFoundError):
            resize_cover_image(tmp_path / "nonexistent.png")

    def test_resize_cover_image_preserves_transparency(self, tmp_path):
        """Test resize_cover_image preserves alpha channel for transparent images."""
        # Create an RGBA image with transparency
        image_path = tmp_path / "transparent.png"
        img = Image.new("RGBA", (400, 400), color=(0, 0, 0, 0))  # Fully transparent
        # Add a visible red rectangle
        from PIL import ImageDraw

        draw = ImageDraw.Draw(img)
        draw.rectangle([100, 100, 300, 300], fill=(255, 0, 0, 255))  # Red, opaque
        img.save(image_path, "PNG")

        result = resize_cover_image(image_path)
        result_img = Image.open(io.BytesIO(result))

        # Should be RGBA mode to preserve transparency
        assert result_img.mode == "RGBA"

        # Check that transparent areas are still transparent
        # Corner should be transparent (alpha=0)
        corner_pixel = result_img.getpixel((0, 0))
        assert corner_pixel[3] == 0  # Alpha channel should be 0

        # Center should be opaque red
        center_pixel = result_img.getpixel((200, 200))
        assert center_pixel[3] == 255  # Alpha channel should be 255
        assert center_pixel[:3] == (255, 0, 0)  # Red color


class TestCreateCollageCover:
    """Test collage cover generation."""

    def test_create_collage_single_image(self, tmp_path):
        """Test collage with single image."""
        # Create a test image
        image_path = tmp_path / "test.png"
        img = Image.new("RGB", (200, 300), color="red")
        img.save(image_path, "PNG")

        mock_image = MagicMock()
        mock_image.file_path = str(image_path)

        result = create_collage_cover([mock_image])

        assert isinstance(result, bytes)
        result_img = Image.open(io.BytesIO(result))
        # Banner format: 800 width, height cropped to content
        assert result_img.width == 800
        assert result_img.height <= 300

    def test_create_collage_multiple_images(self, tmp_path):
        """Test collage with multiple images."""
        mock_images = []
        for i in range(5):
            image_path = tmp_path / f"test_{i}.png"
            img = Image.new("RGB", (200, 300), color=(i * 50, 0, 0))
            img.save(image_path, "PNG")

            mock_image = MagicMock()
            mock_image.file_path = str(image_path)
            mock_images.append(mock_image)

        result = create_collage_cover(mock_images)

        assert isinstance(result, bytes)
        result_img = Image.open(io.BytesIO(result))
        # Banner format: 800 width, height cropped to content
        assert result_img.width == 800
        assert result_img.height <= 300

    def test_create_collage_no_valid_images(self):
        """Test collage raises error with no valid images."""
        mock_image = MagicMock()
        mock_image.file_path = "/nonexistent/path.png"

        with pytest.raises(ValueError, match="No valid images"):
            create_collage_cover([mock_image])

    def test_create_collage_empty_list(self):
        """Test collage raises error with empty list."""
        with pytest.raises(ValueError, match="No valid images"):
            create_collage_cover([])

    def test_create_collage_limits_to_five(self, tmp_path):
        """Test collage uses at most 5 images."""
        mock_images = []
        for i in range(10):
            image_path = tmp_path / f"test_{i}.png"
            img = Image.new("RGB", (200, 300), color=(i * 25, 0, 0))
            img.save(image_path, "PNG")

            mock_image = MagicMock()
            mock_image.file_path = str(image_path)
            mock_images.append(mock_image)

        # Should not raise, should just use first 5
        result = create_collage_cover(mock_images)
        assert isinstance(result, bytes)

    def test_create_collage_all_images_fail_to_process(self, tmp_path):
        """Test collage raises error when all images fail to open.

        Regression test: Even if files exist, if PIL can't open them,
        we should raise an error rather than return an empty canvas.
        """
        # Create files that exist but aren't valid images
        mock_images = []
        for i in range(3):
            image_path = tmp_path / f"corrupt_{i}.png"
            image_path.write_text("not an image")

            mock_image = MagicMock()
            mock_image.file_path = str(image_path)
            mock_images.append(mock_image)

        # Should raise because no images could be processed
        with pytest.raises(ValueError, match="Failed to process any images"):
            create_collage_cover(mock_images)

    def test_create_collage_crops_empty_space(self, tmp_path):
        """Test collage crops empty transparent space from canvas.

        When using landscape screenshots, the images don't fill the full
        300px canvas height. The result should be cropped to remove
        transparent space above and below.
        """
        # Create landscape (screenshot-style) images that are wider than tall
        mock_images = []
        for i in range(3):
            image_path = tmp_path / f"landscape_{i}.png"
            # Create 16:9 landscape image (like a screenshot)
            img = Image.new("RGB", (320, 180), color=(i * 80, 100, 150))
            img.save(image_path, "PNG")

            mock_image = MagicMock()
            mock_image.file_path = str(image_path)
            mock_images.append(mock_image)

        result = create_collage_cover(mock_images)
        result_img = Image.open(io.BytesIO(result))

        # Width should remain 800 (banner format)
        assert result_img.width == 800
        # Height should be less than 300 (cropped to remove empty space)
        assert result_img.height < 300
        # Height should still be reasonable (not too small)
        assert result_img.height > 100


class TestGetSampleGameImages:
    """Test get_sample_game_images function."""

    def test_get_sample_images_basic(self, collection, game_with_image):
        """Test getting sample images from collection."""
        CollectionEntry.objects.create(
            collection=collection,
            game_name=game_with_image.name,
            system_slug=game_with_image.system.slug,
            position=0,
        )

        images = get_sample_game_images(collection, image_type="cover")

        assert len(images) == 1
        assert images[0].game == game_with_image

    def test_get_sample_images_respects_limit(self, collection, system, tmp_path, db):
        """Test get_sample_game_images respects limit parameter."""
        # Create multiple games with images
        for i in range(10):
            game = Game.objects.create(name=f"Game {i}", system=system)
            ROMSet.objects.create(game=game, region="USA")

            image_path = tmp_path / f"game_{i}.png"
            img = Image.new("RGB", (100, 150), color="blue")
            img.save(image_path, "PNG")

            GameImage.objects.create(
                game=game,
                file_path=str(image_path),
                file_name=f"game_{i}.png",
                file_size=100,
                image_type="cover",
                source="test",
            )

            CollectionEntry.objects.create(
                collection=collection,
                game_name=game.name,
                system_slug=system.slug,
                position=i,
            )

        images = get_sample_game_images(collection, image_type="cover", limit=5)

        assert len(images) == 5

    def test_get_sample_images_fallback(self, collection, system, tmp_path, db):
        """Test get_sample_game_images falls back to other image types."""
        game = Game.objects.create(name="Screenshot Only Game", system=system)
        ROMSet.objects.create(game=game, region="USA")

        image_path = tmp_path / "screenshot.png"
        img = Image.new("RGB", (320, 240), color="green")
        img.save(image_path, "PNG")

        GameImage.objects.create(
            game=game,
            file_path=str(image_path),
            file_name="screenshot.png",
            file_size=100,
            image_type="screenshot",
            source="test",
        )

        CollectionEntry.objects.create(
            collection=collection,
            game_name=game.name,
            system_slug=system.slug,
            position=0,
        )

        # Request cover but game only has screenshot
        images = get_sample_game_images(collection, image_type="cover", limit=5)

        # Should fall back to screenshot
        assert len(images) == 1
        assert images[0].image_type == "screenshot"

    def test_get_sample_images_empty_collection(self, collection):
        """Test get_sample_game_images with empty collection."""
        images = get_sample_game_images(collection, image_type="cover")
        assert len(images) == 0

    def test_get_sample_images_no_matched_games(self, collection, system):
        """Test get_sample_game_images with no matched games."""
        CollectionEntry.objects.create(
            collection=collection,
            game_name="Nonexistent Game",
            system_slug="snes",
            position=0,
        )

        images = get_sample_game_images(collection, image_type="cover")
        assert len(images) == 0


class TestCoverSignal:
    """Test the pre_delete signal for cover cleanup."""

    def test_cover_file_deleted_on_collection_delete(self, collection, tmp_path):
        """Test cover file is deleted when collection is deleted."""
        cover_path = tmp_path / "cover.png"
        img = Image.new("RGB", (100, 100), color="purple")
        img.save(cover_path, "PNG")

        collection.cover_image_path = str(cover_path)
        collection.has_cover = True
        collection.cover_source = Collection.COVER_SOURCE_UPLOADED
        collection.save()

        assert cover_path.exists()

        collection.delete()

        assert not cover_path.exists()

    def test_collection_delete_handles_missing_cover(self, collection, tmp_path):
        """Test collection deletion handles missing cover file gracefully."""
        collection.cover_image_path = str(tmp_path / "nonexistent.png")
        collection.has_cover = True
        collection.save()

        # Should not raise
        collection.delete()

    def test_collection_delete_no_cover(self, collection):
        """Test collection deletion with no cover path set."""
        # Should not raise
        collection.delete()
