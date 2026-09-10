"""Tests for the ROM filename parser."""

import pytest

from library.parser import (
    detect_switch_content_type,
    extract_switch_title_id,
    get_stem_and_extension,
    get_switch_content_info,
    parse_rom_filename,
)


# -----------------------------------------------------------------------------
# Tests for parse_rom_filename - Basic functionality
# -----------------------------------------------------------------------------


class TestParseRomFilename:
    """Tests for parse_rom_filename function."""

    @pytest.mark.parametrize(
        "filename,expected_name,expected_region,expected_revision,expected_tags,expected_ext",
        [
            # Basic region parsing
            ("Super Mario World (USA).sfc", "Super Mario World", "USA", "", [], ".sfc"),
            ("Game (Europe).gba", "Game", "Europe", "", [], ".gba"),
            ("Tetris (World).gb", "Tetris", "World", "", [], ".gb"),
            ("Game (Japan).sfc", "Game", "Japan", "", [], ".sfc"),
            # Single letter region codes
            ("Dragon Quest (J).sfc", "Dragon Quest", "Japan", "", [], ".sfc"),
            ("Super Mario (E).nes", "Super Mario", "Europe", "", [], ".nes"),
            ("Zelda (U).gba", "Zelda", "USA", "", [], ".gba"),
            ("Game (j).sfc", "Game", "Japan", "", [], ".sfc"),
            ("Game (e).nes", "Game", "Europe", "", [], ".nes"),
            ("Game (u).gba", "Game", "USA", "", [], ".gba"),
            # Case-insensitive regions
            ("Game (usa).gba", "Game", "USA", "", [], ".gba"),
            ("Game (EUR).gba", "Game", "Europe", "", [], ".gba"),
            # Revisions
            ("Pokemon (USA) (Rev 1).gba", "Pokemon", "USA", "Rev 1", [], ".gba"),
            ("Game (Europe) (Rev A).gba", "Game", "Europe", "Rev A", [], ".gba"),
            ("Game (USA) (v1.0).gba", "Game", "USA", "v1.0", [], ".gba"),
            # Tags
            ("Castlevania (USA) [!].gba", "Castlevania", "USA", "", ["!"], ".gba"),
            ("Game (USA) (Beta).gba", "Game", "USA", "", ["Beta"], ".gba"),
            ("Game (USA) (Proto).gba", "Game", "USA", "", ["Proto"], ".gba"),
            (
                "Game (Japan) (En,Fr,De).gba",
                "Game",
                "Japan",
                "",
                ["En", "Fr", "De"],
                ".gba",
            ),
            # Multiple regions
            (
                "Pokemon (USA, Europe) (Rev 1).gba",
                "Pokemon",
                "USA",  # First region wins
                "Rev 1",
                [],
                ".gba",
            ),
            # No metadata
            ("Game Name.gba", "Game Name", "", "", [], ".gba"),
            # Name with article
            (
                "Legend of Zelda, The - A Link to the Past (USA).sfc",
                "Legend of Zelda, The - A Link to the Past",
                "USA",
                "",
                [],
                ".sfc",
            ),
        ],
    )
    def test_parse_rom_filename(
        self,
        filename,
        expected_name,
        expected_region,
        expected_revision,
        expected_tags,
        expected_ext,
    ):
        """Test parsing various ROM filename formats."""
        result = parse_rom_filename(filename)
        assert result["name"] == expected_name
        assert result["region"] == expected_region
        assert result["revision"] == expected_revision
        assert result["tags"] == expected_tags
        assert result["extension"] == expected_ext


class TestParseDiscNumbers:
    """Tests for disc/track number parsing."""

    @pytest.mark.parametrize(
        "filename,expected_name,expected_disc",
        [
            # Dash-separated disc patterns
            ("Final Fantasy VII - CD1.bin", "Final Fantasy VII", 1),
            ("Final Fantasy VII - CD2.bin", "Final Fantasy VII", 2),
            ("Metal Gear Solid - Disc 1.bin", "Metal Gear Solid", 1),
            ("Resident Evil 2 - Track 1.cue", "Resident Evil 2", 1),
            ("Game - cd1.bin", "Game", 1),
            ("Game - DISC 1.bin", "Game", 1),
            # Parentheses disc patterns
            ("Final Fantasy VII (Disc 1).bin", "Final Fantasy VII", 1),
            # Disc with version in name
            ("Metal Gear Solid v1.0 - CD1.iso", "Metal Gear Solid v1.0", 1),
        ],
    )
    def test_disc_parsing(self, filename, expected_name, expected_disc):
        """Test disc number extraction from filenames."""
        result = parse_rom_filename(filename)
        assert result["name"] == expected_name
        assert result["disc"] == expected_disc

    def test_disc_is_none_when_not_present(self):
        """Disc should be None when not in filename."""
        result = parse_rom_filename("Super Mario World (USA).sfc")
        assert result["disc"] is None


class TestParseRomNumber:
    """Tests for ROM number prefix parsing."""

    @pytest.mark.parametrize(
        "filename,expected_rom_number,expected_name",
        [
            ("123 - Super Mario.gba", "123", "Super Mario"),
            ("0001 - Game Name.nes", "0001", "Game Name"),
            ("42. Pokemon.gb", "42", "Pokemon"),
        ],
    )
    def test_rom_number_extraction(self, filename, expected_rom_number, expected_name):
        """Test ROM number prefix extraction."""
        result = parse_rom_filename(filename)
        assert result["rom_number"] == expected_rom_number
        assert result["name"] == expected_name

    def test_rom_number_empty_when_not_present(self):
        """ROM number should be empty when not in filename."""
        result = parse_rom_filename("Super Mario World (USA).sfc")
        assert result["rom_number"] == ""


class TestParseExtensions:
    """Tests for extension handling."""

    @pytest.mark.parametrize(
        "filename,expected_ext",
        [
            ("Game.gba", ".gba"),
            ("Game.GBA", ".gba"),  # Uppercase -> lowercase
            ("Game.NES", ".nes"),
            ("Game.nds", ".nds"),
            ("Game.p8.png", ".p8.png"),  # Compound extension
        ],
    )
    def test_extension_parsing(self, filename, expected_ext):
        """Test extension extraction and normalization."""
        result = parse_rom_filename(filename)
        assert result["extension"] == expected_ext


class TestParseEdgeCases:
    """Tests for edge cases and error handling."""

    def test_empty_string(self):
        """Empty string should return empty values."""
        result = parse_rom_filename("")
        assert result["name"] == ""
        assert result["extension"] == ""
        assert result["region"] == ""
        assert result["revision"] == ""
        assert result["tags"] == []

    def test_extension_only(self):
        """Just an extension is treated as hidden file with no extension."""
        result = parse_rom_filename(".gba")
        # Parser treats ".gba" as a hidden file (Unix convention)
        assert result["name"] == ".gba"
        assert result["extension"] == ""

    def test_path_input(self):
        """Full paths should extract just the filename."""
        result = parse_rom_filename("/roms/gba/Game (USA).gba")
        assert result["name"] == "Game"
        assert result["region"] == "USA"
        assert result["extension"] == ".gba"

    def test_unbalanced_parentheses(self):
        """Unbalanced parentheses should not crash."""
        result = parse_rom_filename("Game (USA.gba")
        # Should gracefully handle
        assert result["extension"] == ".gba"
        assert result["name"]  # Name should be extracted

    def test_special_characters_in_name(self):
        """Special characters in name should be preserved."""
        result = parse_rom_filename("Ys I & II (USA).gba")
        assert result["name"] == "Ys I & II"
        assert result["region"] == "USA"


# -----------------------------------------------------------------------------
# Tests for get_stem_and_extension
# -----------------------------------------------------------------------------


class TestGetStemAndExtension:
    """Tests for get_stem_and_extension function."""

    @pytest.mark.parametrize(
        "filename,expected_stem,expected_ext",
        [
            ("game.gba", "game", ".gba"),
            ("game.GBA", "game", ".gba"),
            ("my.game.gba", "my.game", ".gba"),
            ("game", "game", ""),
            # Compound extensions
            ("game.p8.png", "game", ".p8.png"),
            ("GAME.P8.PNG", "GAME", ".p8.png"),
        ],
    )
    def test_stem_and_extension(self, filename, expected_stem, expected_ext):
        """Test stem and extension extraction."""
        stem, ext = get_stem_and_extension(filename)
        assert stem == expected_stem
        assert ext == expected_ext

    def test_path_extracts_basename(self):
        """Full paths should extract just the basename."""
        stem, ext = get_stem_and_extension("/path/to/game.gba")
        assert stem == "game"
        assert ext == ".gba"


# -----------------------------------------------------------------------------
# Tests for Switch Title ID functions
# -----------------------------------------------------------------------------


class TestExtractSwitchTitleId:
    """Tests for extract_switch_title_id function."""

    @pytest.mark.parametrize(
        "filename,expected_id",
        [
            # Standard NSP filenames
            ("Super Mario Odyssey [0100000000010000].nsp", "0100000000010000"),
            ("Game [0100123456789ABC].nsp", "0100123456789ABC"),
            # Lowercase hex
            ("Game [0100abcdef012345].nsp", "0100ABCDEF012345"),
            # With other tags
            ("Game [v1.0.0] [0100000000010000].nsp", "0100000000010000"),
            ("Game (USA) [0100000000010000].nsp", "0100000000010000"),
            # No Title ID
            ("Super Mario World (USA).sfc", None),
            ("Game.nsp", None),
            # Invalid (too short or too long)
            ("Game [01000000000100].nsp", None),  # 14 chars
            ("Game [010000000001000000].nsp", None),  # 18 chars
        ],
    )
    def test_extract_title_id(self, filename, expected_id):
        """Test Title ID extraction from filenames."""
        result = extract_switch_title_id(filename)
        assert result == expected_id


class TestDetectSwitchContentType:
    """Tests for detect_switch_content_type function."""

    @pytest.mark.parametrize(
        "title_id,expected_type",
        [
            # Base games (end in 000)
            ("0100000000010000", "base"),
            ("0100F8F0000A2000", "base"),
            # Updates (end in 800)
            ("0100000000010800", "update"),
            ("0100F8F0000A2800", "update"),
            # DLC (001-7FF)
            ("0100000000010001", "dlc"),
            ("0100000000010002", "dlc"),
            ("01000000000107FF", "dlc"),
            # DLC (801-FFF)
            ("0100000000010801", "dlc"),
            ("0100000000010FFF", "dlc"),
            # Invalid/empty
            ("", ""),
            ("0100", ""),
            (None, ""),
        ],
    )
    def test_detect_content_type(self, title_id, expected_type):
        """Test content type detection from Title ID."""
        result = detect_switch_content_type(title_id)
        assert result == expected_type


class TestGetSwitchContentInfo:
    """Tests for get_switch_content_info convenience function."""

    @pytest.mark.parametrize(
        "filename,expected_id,expected_type",
        [
            # Base game
            ("Super Mario Odyssey [0100000000010000].nsp", "0100000000010000", "base"),
            # Update
            ("Game Update [0100000000010800].nsp", "0100000000010800", "update"),
            # DLC
            ("Game DLC [0100000000010001].nsp", "0100000000010001", "dlc"),
            # No Title ID
            ("Super Mario World (USA).sfc", "", ""),
            ("Game.nsp", "", ""),
        ],
    )
    def test_get_switch_content_info(self, filename, expected_id, expected_type):
        """Test combined Title ID and content type extraction."""
        title_id, content_type = get_switch_content_info(filename)
        assert title_id == expected_id
        assert content_type == expected_type


# -----------------------------------------------------------------------------
# Tests for Enhanced ROM Filename Parser (Improvement 5)
# -----------------------------------------------------------------------------


class TestEnhancedRomFilenameParser:
    """Tests for enhanced ROM filename parsing (Improvement 5).

    Covers:
    a) Date prefixes (e.g. 1984-11-30 Excitebike)
    b) Rank prefixes without dashes (e.g. 089 Ice Climber)
    c) Hardware prefixes (e.g. 2C03 Pinball, 2C04-01 Gradius)
    d) VS prefixes (e.g. VS. Duck Hunt)
    e) Inverted articles (e.g. Berenstain Bears' Camping Adventure, The)
    f) Retail patch/mod suffixes outside brackets (e.g. ActRaiser PAL-to-NTSC Patched, Aladdin Trained)
    g) Version and author suffixes outside brackets (e.g. Batter Up v0.1 Revo, Battletoads b1 nextvolume, Alien 1.02 Final)
    h) Trailing Hack suffix (e.g. Aero Fighters Hack)
    """

    def test_date_prefix_excitebike(self):
        """Date prefix stripped from base_name and preserved in tags."""
        result = parse_rom_filename("1984-11-30 Excitebike (JU).nes")
        assert result["name"] == "Excitebike"
        assert "1984-11-30" in result["tags"]

    def test_rank_prefix_ice_climber(self):
        """Rank prefix without dash extracted as rom_number and stripped from name."""
        result = parse_rom_filename("089 Ice Climber (U).nes")
        assert result["name"] == "Ice Climber"
        assert result["rom_number"] == "089"

    def test_hardware_prefix_pinball(self):
        """Hardware revisions preserve VS game identity and original tags."""
        result = parse_rom_filename("2C03 Pinball (VS).nes")
        assert result["name"] == "Vs. Pinball"
        assert "2C03" in result["tags"]

    def test_hardware_prefix_gradius_subrevision(self):
        """A VS PPU subrevision must not become the home-console game."""
        result = parse_rom_filename("2C04-01 Gradius.nes")
        assert result["name"] == "Vs. Gradius"
        assert "2C04-01" in result["tags"]

    def test_vs_prefix_duck_hunt(self):
        """VS identity survives filename cleanup."""
        result = parse_rom_filename("VS. Duck Hunt (VS).nes")
        assert result["name"] == "Vs. Duck Hunt"
        assert any(t in result["tags"] for t in ("VS.", "VS"))

    def test_inverted_article_berenstain_bears(self):
        """Inverted articles at end of base name normalized to leading articles."""
        result = parse_rom_filename(
            "Berenstain Bears' Camping Adventure, The (USA).sms"
        )
        assert result["name"] == "The Berenstain Bears' Camping Adventure"

    def test_inverted_article_a(self):
        """Inverted article ', A' normalized to leading 'A '."""
        result = parse_rom_filename("Boy and His Blob, A (USA).nes")
        assert result["name"] == "A Boy and His Blob"

    def test_inverted_article_an(self):
        """Inverted article ', An' normalized to leading 'An '."""
        result = parse_rom_filename("Awesome Game, An (USA).nes")
        assert result["name"] == "An Awesome Game"

    def test_retail_patch_actraiser(self):
        """Retail patch suffix stripped from name and added to tags."""
        result = parse_rom_filename("ActRaiser PAL-to-NTSC Patched (Europe).sfc")
        assert result["name"] == "ActRaiser"
        assert "PAL-to-NTSC Patched" in result["tags"]

    def test_retail_patch_aladdin_trained(self):
        """Trainer suffix stripped from name and added to tags."""
        result = parse_rom_filename("Aladdin Trained (Europe).sfc")
        assert result["name"] == "Aladdin"
        assert "Trained" in result["tags"]

    def test_retail_patch_bubble_bobble_improvement(self):
        """Improvement suffix stripped from name and added to tags."""
        result = parse_rom_filename("Bubble Bobble Improvement.nes")
        assert result["name"] == "Bubble Bobble"
        assert "Improvement" in result["tags"]

    def test_retail_patch_secret_commando_speedup(self):
        """Speed-Up suffix stripped from name and added to tags."""
        result = parse_rom_filename("Secret Commando Speed-Up.sms")
        assert result["name"] == "Secret Commando"
        assert "Speed-Up" in result["tags"]

    def test_version_author_batter_up(self):
        """Version and author suffix stripped from name and added to tags and revision."""
        result = parse_rom_filename("Batter Up v0.1 Revo.sms")
        assert result["name"] == "Batter Up"
        assert "v0.1 Revo" in result["tags"]
        assert result["revision"] == "v0.1 Revo"

    def test_version_author_battletoads(self):
        """Beta version and author suffix stripped from name and added to tags and revision."""
        result = parse_rom_filename("Battletoads b1 nextvolume.sms")
        assert result["name"] == "Battletoads"
        assert "b1 nextvolume" in result["tags"]
        assert result["revision"] == "b1 nextvolume"

    def test_version_author_alien(self):
        """Version and Final suffix stripped from name and added to tags and revision."""
        result = parse_rom_filename("Alien 1.02 Final.lyx")
        assert result["name"] == "Alien"
        assert "1.02 Final" in result["tags"]
        assert result["revision"] == "1.02 Final"

    def test_trailing_hack_aero_fighters(self):
        """Trailing Hack suffix stripped from name and added to tags."""
        result = parse_rom_filename("Aero Fighters Hack.sfc")
        assert result["name"] == "Aero Fighters"
        assert "Hack" in result["tags"]

    def test_combined_prefix_and_suffix(self):
        """Filename with both prefix and suffix handled correctly."""
        result = parse_rom_filename("VS. Duck Hunt Hack.nes")
        assert result["name"] == "Vs. Duck Hunt"
        assert "VS." in result["tags"]
        assert "Hack" in result["tags"]
