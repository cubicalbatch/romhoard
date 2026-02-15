"""Tests for extension utilities."""

from unittest.mock import MagicMock

from library.extensions import (
    build_exclusive_extension_map,
    get_full_extension,
    is_acceptable_extension,
    is_archive_extension,
    is_compound_rom_extension,
    is_image_extension,
    is_non_rom_extension,
    load_non_rom_extensions,
)


class TestLoadNonRomExtensions:
    """Tests for load_non_rom_extensions function."""

    def test_loads_extensions_from_config(self):
        """Should load extensions from non_rom_extensions.json."""
        extensions = load_non_rom_extensions()
        assert isinstance(extensions, set)
        assert len(extensions) > 0

    def test_includes_common_non_rom_extensions(self):
        """Should include common non-ROM extensions."""
        extensions = load_non_rom_extensions()
        assert ".png" in extensions
        assert ".jpg" in extensions
        assert ".txt" in extensions
        assert ".pdf" in extensions
        assert ".mp3" in extensions

    def test_extensions_are_lowercase(self):
        """All extensions should be lowercase."""
        extensions = load_non_rom_extensions()
        for ext in extensions:
            assert ext == ext.lower()


class TestIsNonRomExtension:
    """Tests for is_non_rom_extension function."""

    def test_image_extensions_are_non_rom(self):
        """Image extensions should be detected as non-ROM."""
        assert is_non_rom_extension(".png")
        assert is_non_rom_extension(".jpg")
        assert is_non_rom_extension(".jpeg")
        assert is_non_rom_extension(".gif")

    def test_audio_extensions_are_non_rom(self):
        """Audio extensions should be detected as non-ROM."""
        assert is_non_rom_extension(".mp3")
        assert is_non_rom_extension(".wav")
        assert is_non_rom_extension(".ogg")

    def test_document_extensions_are_non_rom(self):
        """Document extensions should be detected as non-ROM."""
        assert is_non_rom_extension(".txt")
        assert is_non_rom_extension(".pdf")
        assert is_non_rom_extension(".nfo")

    def test_case_insensitive(self):
        """Should be case-insensitive."""
        assert is_non_rom_extension(".PNG")
        assert is_non_rom_extension(".Txt")
        assert is_non_rom_extension(".MP3")

    def test_rom_extensions_are_not_blocked(self):
        """ROM extensions should not be blocked."""
        assert not is_non_rom_extension(".gba")
        assert not is_non_rom_extension(".nes")
        assert not is_non_rom_extension(".sfc")
        assert not is_non_rom_extension(".nds")

    def test_archive_extensions_are_not_blocked(self):
        """Archive extensions should not be blocked."""
        assert not is_non_rom_extension(".zip")
        assert not is_non_rom_extension(".7z")


class TestIsArchiveExtension:
    """Tests for is_archive_extension function."""

    def test_zip_is_archive(self):
        """ZIP should be recognized as archive."""
        assert is_archive_extension(".zip")
        assert is_archive_extension(".ZIP")

    def test_7z_is_archive(self):
        """7z should be recognized as archive."""
        assert is_archive_extension(".7z")
        assert is_archive_extension(".7Z")

    def test_non_archives(self):
        """Non-archive extensions should not be recognized."""
        assert not is_archive_extension(".gba")
        assert not is_archive_extension(".png")
        assert not is_archive_extension(".rar")  # Not supported


class TestIsImageExtension:
    """Tests for is_image_extension function."""

    def test_common_image_formats(self):
        """Common image formats should be recognized."""
        assert is_image_extension(".png")
        assert is_image_extension(".jpg")
        assert is_image_extension(".jpeg")
        assert is_image_extension(".gif")

    def test_case_insensitive(self):
        """Should be case-insensitive."""
        assert is_image_extension(".PNG")
        assert is_image_extension(".JPG")

    def test_non_images(self):
        """Non-image extensions should not be recognized."""
        assert not is_image_extension(".gba")
        assert not is_image_extension(".txt")


class TestIsCompoundRomExtension:
    """Tests for is_compound_rom_extension function."""

    def test_p8_png_is_compound(self):
        """PICO-8 .p8.png should be recognized as compound ROM."""
        assert is_compound_rom_extension("game.p8.png")
        assert is_compound_rom_extension("GAME.P8.PNG")

    def test_regular_png_is_not_compound(self):
        """Regular PNG should not be compound."""
        assert not is_compound_rom_extension("screenshot.png")

    def test_regular_rom_is_not_compound(self):
        """Regular ROM extension should not be compound."""
        assert not is_compound_rom_extension("game.gba")


class TestGetFullExtension:
    """Tests for get_full_extension function."""

    def test_simple_extension(self):
        """Should return simple extensions correctly."""
        assert get_full_extension("game.gba") == ".gba"
        assert get_full_extension("game.nes") == ".nes"
        assert get_full_extension("game.zip") == ".zip"

    def test_compound_extension(self):
        """Should return full compound extension."""
        assert get_full_extension("game.p8.png") == ".p8.png"

    def test_case_normalization(self):
        """Should normalize to lowercase."""
        assert get_full_extension("GAME.GBA") == ".gba"
        assert get_full_extension("GAME.P8.PNG") == ".p8.png"

    def test_no_extension(self):
        """Should return empty string for no extension."""
        assert get_full_extension("README") == ""


class TestIsAcceptableExtension:
    """Tests for is_acceptable_extension function."""

    def test_system_extension_is_acceptable(self):
        """Extension in system's list should be acceptable."""
        system = MagicMock()
        system.extensions = [".gba"]
        assert is_acceptable_extension(".gba", system)

    def test_archive_always_acceptable(self):
        """Archive extensions should always be acceptable."""
        system = MagicMock()
        system.extensions = [".gba"]
        assert is_acceptable_extension(".zip", system)
        assert is_acceptable_extension(".7z", system)

    def test_unrelated_extension_not_acceptable(self):
        """Unrelated extension should not be acceptable."""
        system = MagicMock()
        system.extensions = [".gba"]
        assert not is_acceptable_extension(".nes", system)
        assert not is_acceptable_extension(".png", system)

    def test_case_insensitive(self):
        """Should be case-insensitive."""
        system = MagicMock()
        system.extensions = [".gba"]
        assert is_acceptable_extension(".GBA", system)
        assert is_acceptable_extension(".ZIP", system)


class TestBuildExclusiveExtensionMap:
    """Tests for build_exclusive_extension_map function."""

    def test_builds_map_from_exclusive_extensions(self):
        """Should build map from exclusive_extensions field."""
        gba_system = MagicMock()
        gba_system.exclusive_extensions = [".gba"]

        nes_system = MagicMock()
        nes_system.exclusive_extensions = [".nes"]

        systems = [gba_system, nes_system]
        ext_map = build_exclusive_extension_map(systems)

        assert ext_map[".gba"] == gba_system
        assert ext_map[".nes"] == nes_system

    def test_excludes_compressed_extensions(self):
        """Should exclude compressed extensions even if listed."""
        system = MagicMock()
        system.exclusive_extensions = [".gba", ".zip", ".7z"]

        ext_map = build_exclusive_extension_map([system])

        assert ".gba" in ext_map
        assert ".zip" not in ext_map
        assert ".7z" not in ext_map

    def test_first_system_wins(self):
        """First system to claim an extension should win."""
        system1 = MagicMock()
        system1.exclusive_extensions = [".bin"]

        system2 = MagicMock()
        system2.exclusive_extensions = [".bin"]

        ext_map = build_exclusive_extension_map([system1, system2])

        assert ext_map[".bin"] == system1

    def test_handles_empty_exclusive_extensions(self):
        """Should handle systems with no exclusive extensions."""
        system = MagicMock()
        system.exclusive_extensions = []

        ext_map = build_exclusive_extension_map([system])
        assert len(ext_map) == 0

    def test_handles_none_exclusive_extensions(self):
        """Should handle systems where exclusive_extensions is None."""
        system = MagicMock()
        system.exclusive_extensions = None

        ext_map = build_exclusive_extension_map([system])
        assert len(ext_map) == 0

    def test_normalizes_to_lowercase(self):
        """Should normalize extensions to lowercase."""
        system = MagicMock()
        system.exclusive_extensions = [".GBA", ".NES"]

        ext_map = build_exclusive_extension_map([system])

        assert ".gba" in ext_map
        assert ".nes" in ext_map
        assert ".GBA" not in ext_map
