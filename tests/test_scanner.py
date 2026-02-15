"""Tests for the ROM scanner."""

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from library.scanner import (
    detect_image_type,
    is_bios_file,
    match_by_folder,
    normalize_name_for_matching,
    should_expand_archive,
    to_absolute_path,
    to_storage_path,
)
from library.archive import ArchiveInfo


# -----------------------------------------------------------------------------
# Tests for is_bios_file
# -----------------------------------------------------------------------------


class TestIsBiosFile:
    """Tests for is_bios_file function."""

    @pytest.mark.parametrize(
        "filename,path,expected",
        [
            # Filename starts with "bios" (various cases)
            ("bios.bin", "/roms/snes/bios.bin", True),
            ("BIOS.bin", "/roms/snes/BIOS.bin", True),
            ("Bios.bin", "/roms/snes/Bios.bin", True),
            ("bios_ps2.bin", "/roms/ps2/bios_ps2.bin", True),
            # Path contains "bios" directory (various cases)
            ("system.dat", "/roms/snes/bios/system.dat", True),
            ("system.dat", "/roms/snes/BIOS/system.dat", True),
            ("system.dat", "/roms/snes/Bios/system.dat", True),
            ("scph1001.bin", "/home/user/roms/ps1/bios/scph1001.bin", True),
            # Normal game files (not BIOS)
            ("Super Mario World.sfc", "/roms/snes/Super Mario World.sfc", False),
            ("symbios.gba", "/roms/gba/symbios.gba", False),  # bios in middle
            ("game.rom", "/roms/mybiosfiles/game.rom", False),  # not exact match
        ],
    )
    def test_bios_detection(self, filename, path, expected):
        """Test BIOS file detection with various inputs."""
        assert is_bios_file(filename, path) is expected

    def test_empty_inputs(self):
        """Empty inputs should not match as BIOS."""
        assert is_bios_file("", "") is False
        assert is_bios_file("game.bin", "") is False


# -----------------------------------------------------------------------------
# Tests for detect_image_type
# -----------------------------------------------------------------------------


class TestDetectImageType:
    """Tests for detect_image_type function."""

    @pytest.mark.parametrize(
        "path,expected",
        [
            # Explicit patterns
            ("/roms/gba/mix/AdvanceWars.png", "mix"),
            ("/roms/gba/box/AdvanceWars.png", "cover"),
            ("/roms/gba/cover/AdvanceWars.png", "cover"),
            ("/roms/gba/screenshot/AdvanceWars.png", "screenshot"),
            # Combined patterns -> mix
            ("/roms/gba/box_screenshot/AdvanceWars.png", "mix"),
            # Case insensitivity
            ("/roms/gba/BOX/AdvanceWars.png", "cover"),
            ("/roms/gba/Screenshot/AdvanceWars.png", "screenshot"),
            ("/roms/gba/MIX/AdvanceWars.png", "mix"),
            # Pattern in filename
            ("/roms/gba/AdvanceWars_box.png", "cover"),
            # No match -> empty string
            ("/roms/gba/AdvanceWars.png", ""),
        ],
    )
    def test_image_type_detection(self, path, expected):
        """Test image type detection from path patterns."""
        assert detect_image_type(path) == expected

    def test_priority_mix_takes_precedence(self):
        """Test that 'mix' takes precedence over other patterns."""
        assert detect_image_type("/roms/gba/mix_box_screenshot/game.png") == "mix"

    def test_empty_path(self):
        """Empty path returns empty string."""
        assert detect_image_type("") == ""


# -----------------------------------------------------------------------------
# Tests for normalize_name_for_matching
# -----------------------------------------------------------------------------


class TestNormalizeNameForMatching:
    """Tests for normalize_name_for_matching function."""

    @pytest.mark.parametrize(
        "input_name,expected",
        [
            # Lowercase conversion
            ("Super Mario World", "super mario world"),
            ("ZELDA", "zelda"),
            # Underscore to space
            ("Super_Mario_World", "super mario world"),
            # Punctuation removal
            ("Zelda: A Link to the Past", "zelda a link to the past"),
            ("Pac-Man", "pacman"),
            ("Rock 'n' Roll Racing", "rock n roll racing"),
            # Whitespace normalization
            ("Super  Mario   World", "super mario world"),
            ("  Game Name  ", "game name"),
            # Combined
            (
                "The_Legend_of_Zelda:_Ocarina_of_Time",
                "the legend of zelda ocarina of time",
            ),
        ],
    )
    def test_name_normalization(self, input_name, expected):
        """Test name normalization for matching."""
        assert normalize_name_for_matching(input_name) == expected

    def test_empty_string(self):
        """Empty string normalizes to empty string."""
        assert normalize_name_for_matching("") == ""

    def test_whitespace_only(self):
        """Whitespace-only string normalizes to empty string."""
        assert normalize_name_for_matching("   ") == ""


# -----------------------------------------------------------------------------
# Tests for to_storage_path / to_absolute_path
# -----------------------------------------------------------------------------


class TestPathConversion:
    """Tests for path conversion functions."""

    def test_to_storage_path_with_library_root(self, settings):
        """Converts absolute path to relative when library root is set."""
        settings.ROM_LIBRARY_ROOT = "/roms"
        assert to_storage_path("/roms/gba/game.gba") == "gba/game.gba"

    def test_to_storage_path_without_library_root(self, settings):
        """Returns absolute path unchanged when no library root."""
        settings.ROM_LIBRARY_ROOT = ""
        assert to_storage_path("/roms/gba/game.gba") == "/roms/gba/game.gba"

    def test_to_storage_path_outside_library_root(self, settings):
        """Returns path unchanged if not under library root."""
        settings.ROM_LIBRARY_ROOT = "/roms"
        assert to_storage_path("/other/path/game.gba") == "/other/path/game.gba"

    def test_to_absolute_path_with_library_root(self, settings):
        """Converts relative path to absolute when library root is set."""
        settings.ROM_LIBRARY_ROOT = "/roms"
        assert to_absolute_path("gba/game.gba") == "/roms/gba/game.gba"

    def test_to_absolute_path_without_library_root(self, settings):
        """Returns path unchanged when no library root."""
        settings.ROM_LIBRARY_ROOT = ""
        assert to_absolute_path("gba/game.gba") == "gba/game.gba"

    def test_to_absolute_path_already_absolute(self, settings):
        """Returns absolute paths unchanged."""
        settings.ROM_LIBRARY_ROOT = "/roms"
        assert to_absolute_path("/roms/gba/game.gba") == "/roms/gba/game.gba"


# -----------------------------------------------------------------------------
# Tests for match_by_folder
# -----------------------------------------------------------------------------


class TestMatchByFolder:
    """Tests for match_by_folder function."""

    def test_matches_folder_name(self):
        """Matches system by folder name in path."""
        mock_system = MagicMock()
        mock_system.folder_names = ["GBA", "GameBoyAdvance"]

        path = Path("/roms/GBA/game.gba")
        result = match_by_folder(path, [mock_system])

        assert result == mock_system

    def test_case_insensitive_match(self):
        """Folder matching is case insensitive."""
        mock_system = MagicMock()
        mock_system.folder_names = ["GBA"]

        path = Path("/roms/gba/game.gba")
        result = match_by_folder(path, [mock_system])

        assert result == mock_system

    def test_nested_folder_match(self):
        """Matches folder names in nested paths."""
        mock_system = MagicMock()
        mock_system.folder_names = ["GBA"]

        path = Path("/home/user/roms/GBA/USA/game.gba")
        result = match_by_folder(path, [mock_system])

        assert result == mock_system

    def test_no_match_returns_none(self):
        """Returns None when no folder matches."""
        mock_system = MagicMock()
        mock_system.folder_names = ["GBA"]

        path = Path("/roms/snes/game.sfc")
        result = match_by_folder(path, [mock_system])

        assert result is None

    def test_first_matching_system_wins(self):
        """Returns first matching system when multiple could match."""
        system1 = MagicMock()
        system1.folder_names = ["roms"]

        system2 = MagicMock()
        system2.folder_names = ["GBA"]

        path = Path("/roms/GBA/game.gba")
        result = match_by_folder(path, [system1, system2])

        # First system's folder name "roms" matches first
        assert result == system1


# -----------------------------------------------------------------------------
# Tests for should_expand_archive
# -----------------------------------------------------------------------------


class TestShouldExpandArchive:
    """Tests for should_expand_archive function."""

    def test_single_file_no_expand(self):
        """Single file archives should not be expanded."""
        rom_files = [ArchiveInfo("game.gba", 1000)]
        assert should_expand_archive(rom_files) is False

    def test_empty_archive_no_expand(self):
        """Empty archives should not be expanded."""
        assert should_expand_archive([]) is False

    def test_same_game_multiple_discs_no_expand(self):
        """Multi-disc games with same name should not be expanded."""
        rom_files = [
            ArchiveInfo("Final Fantasy VII (Disc 1).bin", 1000),
            ArchiveInfo("Final Fantasy VII (Disc 2).bin", 1000),
            ArchiveInfo("Final Fantasy VII (Disc 3).bin", 1000),
        ]
        assert should_expand_archive(rom_files) is False

    def test_different_games_should_expand(self):
        """Different games in archive should trigger expansion."""
        rom_files = [
            ArchiveInfo("Super Mario World.sfc", 1000),
            ArchiveInfo("Zelda - A Link to the Past.sfc", 1000),
        ]
        assert should_expand_archive(rom_files) is True

    def test_case_insensitive_game_name_comparison(self):
        """Game name comparison is case insensitive."""
        rom_files = [
            ArchiveInfo("GAME.gba", 1000),
            ArchiveInfo("game.gba", 1000),  # Same name, different case
        ]
        # Same game name (case insensitive) -> don't expand
        assert should_expand_archive(rom_files) is False

    def test_nested_paths_use_filename_only(self):
        """Uses only filename, not path, for game name comparison."""
        rom_files = [
            ArchiveInfo("USA/Super Mario.gba", 1000),
            ArchiveInfo("EUR/Super Mario.gba", 1000),
        ]
        # Same game name from different folders -> don't expand
        assert should_expand_archive(rom_files) is False
