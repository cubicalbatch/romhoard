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
