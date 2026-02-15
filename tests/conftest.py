"""Pytest configuration and shared fixtures for Django tests."""

import io
import os
import tempfile
from pathlib import Path
from unittest.mock import MagicMock

import django
import pytest
from PIL import Image


def pytest_configure(config):
    """Configure Django settings before running tests."""
    os.environ.setdefault("DJANGO_SETTINGS_MODULE", "romhoard.settings")
    django.setup()


# -----------------------------------------------------------------------------
# Image test helpers
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
# System fixtures
# -----------------------------------------------------------------------------


@pytest.fixture
def gba_system(db):
    """Create a GBA system for testing."""
    from library.models import System

    system, _ = System.objects.get_or_create(
        slug="gba",
        defaults={
            "name": "Game Boy Advance",
            "exclusive_extensions": [".gba"],
            "extensions": [".gba"],
            "folder_names": ["GBA", "gba"],
        },
    )
    # Ensure proper extensions
    if ".gba" not in system.exclusive_extensions:
        system.exclusive_extensions = [".gba"]
        system.save()
    return system


@pytest.fixture
def nes_system(db):
    """Create an NES system for testing."""
    from library.models import System

    system, _ = System.objects.get_or_create(
        slug="nes",
        defaults={
            "name": "Nintendo Entertainment System",
            "exclusive_extensions": [".nes"],
            "extensions": [".nes"],
            "folder_names": ["NES", "nes"],
        },
    )
    if ".nes" not in system.exclusive_extensions:
        system.exclusive_extensions = [".nes"]
        system.save()
    return system


@pytest.fixture
def ps1_system(db):
    """Create a PS1 system for testing."""
    from library.models import System

    system, _ = System.objects.get_or_create(
        slug="ps1",
        defaults={
            "name": "PlayStation",
            "extensions": [".chd", ".bin", ".cue"],
            "folder_names": ["PS1", "PlayStation"],
        },
    )
    return system


@pytest.fixture
def test_system(db):
    """Create a generic test system."""
    from library.models import System

    return System.objects.create(
        name="Test System",
        slug="test-system",
        extensions=[".rom"],
        folder_names=["Test"],
    )


# -----------------------------------------------------------------------------
# Game fixtures
# -----------------------------------------------------------------------------


@pytest.fixture
def test_game(db, test_system):
    """Create a generic test game."""
    from library.models import Game

    return Game.objects.create(name="Test Game", system=test_system)


@pytest.fixture
def game_with_images(db):
    """Create a game with multiple image types."""
    from library.models import Game, GameImage, System

    system = System.objects.create(
        name="GBA", slug="gba-images", extensions=[".gba"], folder_names=["GBA"]
    )
    game = Game.objects.create(name="Test Game", system=system)

    # Create a temporary image file
    with tempfile.NamedTemporaryFile(suffix=".png", delete=False) as f:
        f.write(create_test_image(200, 200))
        cover_path = f.name

    GameImage.objects.create(
        game=game,
        file_path=cover_path,
        file_name="cover.png",
        image_type="cover",
    )

    yield game

    # Cleanup
    Path(cover_path).unlink(missing_ok=True)


@pytest.fixture
def game_with_screenshot_only(db):
    """Create a game with only a screenshot image."""
    from library.models import Game, GameImage, System

    system = System.objects.create(
        name="GBA", slug="gba-screenshot", extensions=[".gba"], folder_names=["GBA"]
    )
    game = Game.objects.create(name="Test Game Screenshot", system=system)

    with tempfile.NamedTemporaryFile(suffix=".png", delete=False) as f:
        f.write(create_test_image(200, 200))
        screenshot_path = f.name

    GameImage.objects.create(
        game=game,
        file_path=screenshot_path,
        file_name="screenshot.png",
        image_type="screenshot",
    )

    yield game

    Path(screenshot_path).unlink(missing_ok=True)


@pytest.fixture
def game_without_images(db):
    """Create a game with no images."""
    from library.models import Game, System

    system = System.objects.create(
        name="GBA", slug="gba-no-images", extensions=[".gba"], folder_names=["GBA"]
    )
    game = Game.objects.create(name="Test Game No Images", system=system)

    return game


@pytest.fixture
def game_with_real_image(db):
    """Create a game with a real image file for testing."""
    from library.models import Game, GameImage, System

    system = System.objects.create(
        name="GBA", slug="gba-real-image", extensions=[".gba"], folder_names=["GBA"]
    )
    game = Game.objects.create(name="Test Game Real Image", system=system)

    # Create a larger test image
    with tempfile.NamedTemporaryFile(suffix=".png", delete=False) as f:
        f.write(create_test_image(500, 500))
        image_path = f.name

    GameImage.objects.create(
        game=game,
        file_path=image_path,
        file_name="cover.png",
        image_type="cover",
    )

    yield game, image_path

    Path(image_path).unlink(missing_ok=True)


@pytest.fixture
def game_with_missing_image_file(db):
    """Create a game with an image record but no actual file."""
    from library.models import Game, GameImage, System

    system = System.objects.create(
        name="GBA", slug="gba-missing-file", extensions=[".gba"], folder_names=["GBA"]
    )
    game = Game.objects.create(name="Test Game Missing File", system=system)

    GameImage.objects.create(
        game=game,
        file_path="/nonexistent/path/cover.png",
        file_name="cover.png",
        image_type="cover",
    )

    return game


# -----------------------------------------------------------------------------
# ROM fixtures
# -----------------------------------------------------------------------------


@pytest.fixture
def test_rom(db, gba_system):
    """Create a test ROM with romset."""
    from library.models import Game, ROM, ROMSet

    game = Game.objects.create(name="Test ROM Game", system=gba_system)
    romset = ROMSet.objects.create(game=game, region="USA")
    rom = ROM.objects.create(
        rom_set=romset,
        file_path="/test/path.gba",
        file_name="test.gba",
        file_size=1024,
    )
    return rom


# -----------------------------------------------------------------------------
# Mock fixtures for external services
# -----------------------------------------------------------------------------


@pytest.fixture
def mock_system():
    """Create a mock System object for non-DB tests."""
    system = MagicMock()
    system.slug = "gba"
    system.archive_as_rom = False
    system.all_screenscraper_ids = [12]
    return system


@pytest.fixture
def mock_arcade_system():
    """Create a mock arcade System object for testing."""
    system = MagicMock()
    system.slug = "arcade"
    system.archive_as_rom = True
    system.all_screenscraper_ids = [75, 142]
    return system


# -----------------------------------------------------------------------------
# HTTP mock helpers
# -----------------------------------------------------------------------------


@pytest.fixture
def mock_http_response():
    """Factory for creating mock HTTP responses."""

    def _make_response(content, headers=None, status_code=200):
        mock_response = MagicMock()
        mock_response.headers = headers or {}
        mock_response.content = content
        mock_response.status_code = status_code
        mock_response.raise_for_status = MagicMock()
        return mock_response

    return _make_response
