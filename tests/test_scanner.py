"""Tests for the ROM scanner."""

from pathlib import Path
from unittest.mock import MagicMock

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


@pytest.mark.django_db(transaction=True)
def test_scan_queues_metadata_after_rom_creation(tmp_path, gba_system, monkeypatch):
    """A metadata merge cannot delete the ROMSet before its ROM is inserted."""
    from library.merge import merge_games
    from library.models import Game, ROM, ROMSet
    from library.scanner import scan_directory

    canonical = Game.objects.create(name="Canonical Game", system=gba_system)
    canonical_set = ROMSet.objects.create(game=canonical)
    rom_path = tmp_path / "Duplicate Game.gba"
    rom_path.write_bytes(b"rom")

    def merge_immediately(game):
        merge_games(canonical, game)
        return True

    monkeypatch.setattr("library.tasks.queue_game_metadata", merge_immediately)

    result = scan_directory(str(tmp_path), fetch_metadata=True)

    assert result["added"] == 1
    assert ROM.objects.get(file_path=str(rom_path)).rom_set_id == canonical_set.pk


@pytest.mark.django_db
def test_scan_accepts_long_revision(tmp_path, gba_system):
    """TOSEC revision tags longer than 50 characters must still scan."""
    from library.models import ROM
    from library.scanner import scan_directory

    revision = f"Rev {'A' * 60}"
    rom_path = tmp_path / f"Long Revision ({revision}).gba"
    rom_path.write_bytes(b"rom")

    result = scan_directory(
        str(tmp_path), use_hasheous=False, fetch_metadata=False
    )

    assert result["added"] == 1
    assert ROM.objects.get(file_path=str(rom_path)).rom_set.revision == revision


@pytest.mark.django_db(transaction=True)
def test_scan_continues_after_file_database_error(tmp_path, gba_system, monkeypatch):
    """One invalid ROM must not abort the remaining directory scan."""
    from library import scanner
    from library.models import Game, ROM

    bad_path = tmp_path / "Bad.gba"
    good_path = tmp_path / "Good.gba"
    bad_path.write_bytes(b"bad")
    good_path.write_bytes(b"good")
    parse_rom_filename = scanner.parse_rom_filename

    def parse_with_invalid_revision(filename, arcade=False):
        parsed = parse_rom_filename(filename, arcade=arcade)
        if filename == bad_path.name:
            parsed["revision"] = "A" * 101
        return parsed

    monkeypatch.setattr(scanner, "parse_rom_filename", parse_with_invalid_revision)

    result = scanner.scan_directory(str(tmp_path), fetch_metadata=False)

    assert result["added"] == 1
    assert ROM.objects.filter(file_path=str(good_path)).exists()
    assert not Game.objects.filter(name="Bad").exists()
    assert any(str(bad_path) in error for error in result["errors"])


@pytest.mark.django_db(transaction=True)
def test_scan_survives_concurrent_merge_of_selected_game(
    tmp_path, gba_system, monkeypatch
):
    """A Game merged away between game selection and ROMSet creation must not fail the import."""
    import threading
    import zipfile

    from django.db import connection

    from library import scanner
    from library.merge import merge_games
    from library.models import Game, ROM
    from library.scanner import scan_directory

    canonical = Game.objects.create(name="Canonical Game", system=gba_system)
    duplicate = Game.objects.create(name="Duplicate Game", system=gba_system)
    zip_path = tmp_path / "Duplicate Game.zip"
    with zipfile.ZipFile(zip_path, "w") as zf:
        zf.writestr("Duplicate Game.gba", b"rom data")

    selected = threading.Event()
    merged = threading.Event()
    merge_errors: list[BaseException] = []

    def merge_in_other_connection():
        """Merge away the selected Game on a separate database connection."""
        try:
            if not selected.wait(timeout=30):
                raise RuntimeError("scan never selected the duplicate game")
            merge_games(canonical, duplicate)
        except BaseException as exc:  # captured below so failures cannot pass silently
            merge_errors.append(exc)
        finally:
            merged.set()
            connection.close()

    real_find_existing_game = scanner.find_existing_game

    def find_then_pause(*args, **kwargs):
        """Pause after the duplicate is selected, before the scanner uses it."""
        game = real_find_existing_game(*args, **kwargs)
        if game is not None and game.pk == duplicate.pk:
            selected.set()
            merged.wait(timeout=30)
        return game

    monkeypatch.setattr(scanner, "find_existing_game", find_then_pause)

    merger = threading.Thread(target=merge_in_other_connection, name="game-merger")
    merger.start()
    try:
        result = scan_directory(str(tmp_path), use_hasheous=False, fetch_metadata=False)
    finally:
        # Never deadlock: release the merge thread if the scan died before selecting.
        selected.set()
        merger.join(timeout=30)

    assert merge_errors == []
    assert not merger.is_alive()

    assert result["added"] == 1
    assert result["errors"] == []
    rom = ROM.objects.get(archive_path=str(zip_path))
    assert rom.path_in_archive == "Duplicate Game.gba"


@pytest.mark.django_db(transaction=True)
def test_scan_holds_lock_on_reresolved_canonical_game(
    tmp_path, gba_system, monkeypatch
):
    """A Game re-resolved after the selected one was merged away must be row-locked.

    The initially selected Game is merged into a canonical Game while the scan is
    paused after selection, so re-resolution finds the canonical Game by CRC32. A
    second merge then targets that re-resolved Game before ROMSet creation: if the
    scanner holds its row lock, the merge blocks until the scan's ROM commits and
    then moves both ROMSets onward; if it uses the re-resolved Game unlocked, the
    merge lands first and the import breaks (or the merge sees only one ROMSet).
    """
    import threading
    import zipfile
    import zlib

    from django.db import connection

    from library import scanner
    from library.merge import merge_games
    from library.models import Game, ROM, ROMSet
    from library.scanner import scan_directory

    canonical = Game.objects.create(name="Canonical Game", system=gba_system)
    duplicate = Game.objects.create(name="Duplicate Game", system=gba_system)
    third = Game.objects.create(name="Third Game", system=gba_system)

    inner_bytes = b"re-resolve lock test rom data"
    inner_crc = f"{zlib.crc32(inner_bytes) & 0xFFFFFFFF:08x}"
    zip_path = tmp_path / "Duplicate Game.zip"
    with zipfile.ZipFile(zip_path, "w") as zf:
        zf.writestr("Duplicate Game.gba", inner_bytes)

    # Seed ROM with the archive's CRC so the initial selection resolves to the
    # duplicate by hash and, after it is merged away, re-resolution resolves to
    # the canonical Game that inherited the seeded ROMSet.
    seed_romset = ROMSet.objects.create(game=duplicate, region="Seed")
    ROM.objects.create(
        rom_set=seed_romset,
        file_path="/seed/Duplicate Game.gba",
        file_name="Duplicate Game.gba",
        file_size=len(inner_bytes),
        crc32=inner_crc,
        path_in_archive="seed/Duplicate Game.gba",
    )

    selected = threading.Event()
    merged_once = threading.Event()
    re_resolved = threading.Event()
    merger_errors: list[BaseException] = []
    second_merge_summary: dict = {}

    def merge_selected_away():
        """Merge the initially selected Game into canonical on its own connection."""
        try:
            if not selected.wait(timeout=30):
                raise RuntimeError("scan never selected the duplicate game")
            merge_games(canonical, duplicate)
        except BaseException as exc:  # captured so failures cannot pass silently
            merger_errors.append(exc)
        finally:
            merged_once.set()
            connection.close()

    def merge_canonical_away():
        """Merge the re-resolved canonical Game into third once re-resolution ran."""
        try:
            if not re_resolved.wait(timeout=30):
                raise RuntimeError("scan never re-resolved to the canonical game")
            # Blocks on the scanner's row lock until the scan's ROM commits;
            # romsets_moved then counts both the seeded and the scanned ROMSet.
            second_merge_summary.update(merge_games(third, canonical))
        except BaseException as exc:  # captured so failures cannot pass silently
            merger_errors.append(exc)
        finally:
            re_resolved.set()
            connection.close()

    real_find_existing_game = scanner.find_existing_game

    def find_with_handshakes(*args, **kwargs):
        """Signal the mergers at selection and re-resolution of the same hash."""
        game = real_find_existing_game(*args, **kwargs)
        if game is not None and game.pk == duplicate.pk:
            selected.set()
            merged_once.wait(timeout=30)
        elif game is not None and game.pk == canonical.pk:
            re_resolved.set()
        return game

    monkeypatch.setattr(scanner, "find_existing_game", find_with_handshakes)

    merger1 = threading.Thread(target=merge_selected_away, name="game-merger-1")
    merger2 = threading.Thread(target=merge_canonical_away, name="game-merger-2")
    merger1.start()
    merger2.start()
    try:
        result = scan_directory(str(tmp_path), use_hasheous=False, fetch_metadata=False)
    finally:
        # Never deadlock: release the merge threads if the scan died early.
        selected.set()
        re_resolved.set()
        merger1.join(timeout=30)
        merger2.join(timeout=30)

    assert merger_errors == []
    assert not merger1.is_alive()
    assert not merger2.is_alive()

    assert result["added"] == 1
    assert result["errors"] == []

    # The second merge saw the scan's committed ROMSet (seeded + scanned = 2):
    # it could only proceed past the row lock after the scanner committed.
    assert second_merge_summary["romsets_moved"] == 2

    # The archive imported exactly once and survived both merges (moved onward).
    assert ROM.objects.filter(archive_path=str(zip_path)).count() == 1
    rom = ROM.objects.get(archive_path=str(zip_path))
    assert rom.path_in_archive == "Duplicate Game.gba"
    assert rom.rom_set.game.name == "Third Game"
    assert not Game.objects.filter(
        name__in=["Duplicate Game", "Canonical Game"], system=gba_system
    ).exists()
