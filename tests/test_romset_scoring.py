"""Tests for ROMSet priority scoring, including Switch content type handling."""

import pytest

from library.models import Game, ROM, ROMSet, System
from library.romset_scoring import (
    calculate_romset_score,
    get_content_type_penalty,
    NO_BASE_GAME_PENALTY,
)


@pytest.fixture
def switch_system(db):
    """Create a Switch system for testing."""
    return System.objects.create(
        name="Nintendo Switch",
        slug="switch",
        extensions=[".nsp", ".xci"],
        exclusive_extensions=[".nsp", ".xci"],
        folder_names=["switch", "Switch"],
    )


@pytest.fixture
def gba_system(db):
    """Create a GBA system for testing (non-Switch)."""
    return System.objects.create(
        name="Game Boy Advance",
        slug="gba",
        extensions=[".gba"],
        exclusive_extensions=[".gba"],
        folder_names=["gba", "GBA"],
    )


@pytest.fixture
def switch_game(switch_system):
    """Create a Switch game for testing."""
    return Game.objects.create(
        name="Super Mario Odyssey",
        system=switch_system,
    )


@pytest.fixture
def gba_game(gba_system):
    """Create a GBA game for testing."""
    return Game.objects.create(
        name="Advance Wars",
        system=gba_system,
    )


class TestGetContentTypePenalty:
    """Tests for get_content_type_penalty function."""

    def test_non_switch_system_no_penalty(self, gba_game):
        """Non-Switch systems should never get a penalty."""
        rom_set = ROMSet.objects.create(game=gba_game, region="USA")
        ROM.objects.create(
            rom_set=rom_set,
            file_path="/roms/gba/game.gba",
            file_name="game.gba",
            file_size=1000,
        )
        assert get_content_type_penalty(rom_set) == 0

    def test_switch_with_base_no_penalty(self, switch_game):
        """Switch ROMSet with base game should not be penalized."""
        rom_set = ROMSet.objects.create(game=switch_game, region="USA")
        ROM.objects.create(
            rom_set=rom_set,
            file_path="/roms/switch/game.nsp",
            file_name="game.nsp",
            file_size=1000,
            content_type="base",
            switch_title_id="0100000000010000",
        )
        assert get_content_type_penalty(rom_set) == 0

    def test_switch_update_only_penalized(self, switch_game):
        """Switch ROMSet with only update should be heavily penalized."""
        rom_set = ROMSet.objects.create(game=switch_game, region="USA")
        ROM.objects.create(
            rom_set=rom_set,
            file_path="/roms/switch/update.nsp",
            file_name="update.nsp",
            file_size=1000,
            content_type="update",
            switch_title_id="0100000000010800",
        )
        assert get_content_type_penalty(rom_set) == NO_BASE_GAME_PENALTY

    def test_switch_dlc_only_penalized(self, switch_game):
        """Switch ROMSet with only DLC should be heavily penalized."""
        rom_set = ROMSet.objects.create(game=switch_game, region="USA")
        ROM.objects.create(
            rom_set=rom_set,
            file_path="/roms/switch/dlc.nsp",
            file_name="dlc.nsp",
            file_size=1000,
            content_type="dlc",
            switch_title_id="0100000000010001",
        )
        assert get_content_type_penalty(rom_set) == NO_BASE_GAME_PENALTY

    def test_switch_no_content_type_no_penalty(self, switch_game):
        """Switch ROMSet without content_type info should not be penalized."""
        rom_set = ROMSet.objects.create(game=switch_game, region="USA")
        ROM.objects.create(
            rom_set=rom_set,
            file_path="/roms/switch/game.xci",
            file_name="game.xci",
            file_size=1000,
            # No content_type set
        )
        assert get_content_type_penalty(rom_set) == 0

    def test_switch_base_plus_update_no_penalty(self, switch_game):
        """Switch ROMSet with base + update should not be penalized."""
        rom_set = ROMSet.objects.create(game=switch_game, region="USA")
        ROM.objects.create(
            rom_set=rom_set,
            file_path="/roms/switch/base.nsp",
            file_name="base.nsp",
            file_size=1000,
            content_type="base",
            switch_title_id="0100000000010000",
        )
        ROM.objects.create(
            rom_set=rom_set,
            file_path="/roms/switch/update.nsp",
            file_name="update.nsp",
            file_size=500,
            content_type="update",
            switch_title_id="0100000000010800",
        )
        assert get_content_type_penalty(rom_set) == 0


class TestCalculateRomsetScoreWithContentType:
    """Tests for calculate_romset_score with content type integration."""

    def test_switch_base_scores_higher_than_update(self, switch_game):
        """Switch base game ROMSet should score much higher than update-only."""
        base_romset = ROMSet.objects.create(game=switch_game, region="USA")
        ROM.objects.create(
            rom_set=base_romset,
            file_path="/roms/switch/base.nsp",
            file_name="base.nsp",
            file_size=1000,
            content_type="base",
            switch_title_id="0100000000010000",
        )

        update_romset = ROMSet.objects.create(game=switch_game, region="USA", revision="v1.0")
        ROM.objects.create(
            rom_set=update_romset,
            file_path="/roms/switch/update.nsp",
            file_name="update.nsp",
            file_size=500,
            content_type="update",
            switch_title_id="0100000000010800",
        )

        base_score = calculate_romset_score(base_romset)
        update_score = calculate_romset_score(update_romset)

        # Base should score much higher
        assert base_score > update_score
        # The difference should be at least the penalty
        assert base_score - update_score >= abs(NO_BASE_GAME_PENALTY)

    def test_gba_unaffected_by_content_type_logic(self, gba_game):
        """GBA games should be completely unaffected by content type logic."""
        rom_set = ROMSet.objects.create(game=gba_game, region="USA")
        ROM.objects.create(
            rom_set=rom_set,
            file_path="/roms/gba/game.gba",
            file_name="game.gba",
            file_size=1000,
        )

        score = calculate_romset_score(rom_set)
        # Should just be region score (USA = 1000) + standalone bonus (100)
        assert score > 0  # Basic sanity check
