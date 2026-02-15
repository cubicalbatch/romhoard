"""Shared fixtures for romcollections tests."""

import pytest

from library.models import Game, ROMSet, System


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
def game(db, system):
    """Create a test game with a ROMSet."""
    game = Game.objects.create(name="Super Mario World", system=system)
    ROMSet.objects.create(game=game, region="USA")
    return game
