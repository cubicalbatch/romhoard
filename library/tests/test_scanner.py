"""Integration tests for ROM scanner using fixture files."""

from pathlib import Path
from django.test import TestCase

from library.models import ROM, Game, System, ROMSet, GameImage
from library.scanner import scan_directory
from library.system_loader import sync_systems
from library.scanner import (
    build_extension_map,
    detect_system,
    detect_system_details,
    detect_system_for_archived_file,
    filter_rom_files_in_archive,
    get_full_extension,
    is_compound_rom_extension,
    should_expand_archive,
)
from library.extensions import load_non_rom_extensions


FIXTURES_PATH = (
    Path(__file__).parent.parent.parent / "tests" / "fixtures" / "rom_library"
)


class TestScannerIntegration(TestCase):
    """Integration tests using real fixture files."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        sync_systems()

    def setUp(self):
        # Clear ROMs between tests
        ROM.objects.all().delete()
        Game.objects.all().delete()
        ROMSet.objects.all().delete()
        GameImage.objects.all().delete()

    def test_scan_gba_folder(self):
        """Test scanning GBA folder detects ROMs correctly."""
        scan_directory(str(FIXTURES_PATH))

        # Find GBA ROMs
        gba_roms = ROM.objects.filter(rom_set__game__system__slug="gba")

        # Should find Super Mario, Pokemon, Castlevania
        assert gba_roms.count() >= 3

        # Check region parsing
        pokemon = ROM.objects.get(file_path__icontains="Pokemon")
        assert pokemon.rom_set.region == "USA"
        assert pokemon.rom_set.revision == "Rev 1"

    def test_bios_files_skipped(self):
        """Test BIOS files are not imported."""
        scan_directory(str(FIXTURES_PATH))

        bios_roms = ROM.objects.filter(file_path__icontains="bios")
        assert bios_roms.count() == 0

    def test_image_detection(self):
        """Test images are matched to games with correct types."""
        scan_directory(str(FIXTURES_PATH))

        # Check image types
        mix_images = GameImage.objects.filter(image_type="mix")
        cover_images = GameImage.objects.filter(image_type="cover")
        screenshot_images = GameImage.objects.filter(image_type="screenshot")

        assert mix_images.exists()
        assert cover_images.exists()
        assert screenshot_images.exists()

    def test_multi_game_archive_expands(self):
        """Test archive with different games creates multiple ROMs."""
        scan_directory(str(FIXTURES_PATH))

        # Collection.7z should expand to individual ROMs
        crash = ROM.objects.filter(file_path__icontains="Crash")
        spyro = ROM.objects.filter(file_path__icontains="Spyro")

        assert crash.exists()
        assert spyro.exists()

    def test_single_game_archive_single_rom(self):
        """Test archive with same-game multi-disc creates one ROM."""
        scan_directory(str(FIXTURES_PATH))

        # Final Fantasy VII should be one ROM (multi-disc)
        ff7 = Game.objects.filter(name__icontains="Final Fantasy VII")
        assert ff7.count() == 1

    def test_exclusive_extension_detection(self):
        """Test ROMs in non-matching folders detected via extension."""
        scan_directory(str(FIXTURES_PATH))

        # .gba in RandomFolder should be detected as GBA
        # .z64 in RandomFolder should be detected as N64
        random_gba = ROM.objects.filter(
            file_path__icontains="RandomFolder", rom_set__game__system__slug="gba"
        )
        random_n64 = ROM.objects.filter(
            file_path__icontains="RandomFolder", rom_set__game__system__slug="n64"
        )

        assert random_gba.exists()
        assert random_n64.exists()


class TestCompoundExtensions(TestCase):
    """Test compound extension handling in scanner."""

    def setUp(self):
        """Create test system data."""
        # Sync systems from config
        sync_systems()
        self.pico8_system = System.objects.get(slug="pico8")

    def test_get_full_extension_compound(self):
        """Test get_full_extension returns .p8.png for compound extension."""
        extension = get_full_extension("game.p8.png")
        self.assertEqual(extension, ".p8.png")

    def test_get_full_extension_regular(self):
        """Test get_full_extension returns .gba for regular extension."""
        extension = get_full_extension("game.gba")
        self.assertEqual(extension, ".gba")

    def test_is_compound_rom_extension_true(self):
        """Test is_compound_rom_extension detects .p8.png correctly."""
        self.assertTrue(is_compound_rom_extension("game.p8.png"))
        self.assertTrue(is_compound_rom_extension("GAME.P8.PNG"))  # Case insensitive

    def test_is_compound_rom_extension_false(self):
        """Test is_compound_rom_extension returns False for regular extensions."""
        self.assertFalse(is_compound_rom_extension("game.gba"))
        self.assertFalse(is_compound_rom_extension("game.png"))
        self.assertFalse(is_compound_rom_extension("game.p8.png.zip"))


class TestArchiveScanning(TestCase):
    """Test archive scanning functionality."""

    def setUp(self):
        """Create test system data."""
        # Sync systems from config
        sync_systems()
        self.gba_system = System.objects.get(slug="gba")
        self.n64_system = System.objects.get(slug="n64")

    def test_should_expand_archive_single_game(self):
        """Test that archives with same game name are NOT expanded."""
        from library import archive as archive_utils

        rom_files = [
            archive_utils.ArchiveInfo("Mario (USA) (Disc 1).gba", 1000),
            archive_utils.ArchiveInfo("Mario (USA) (Disc 2).gba", 1000),
        ]

        # Same game name (Mario) - should NOT expand
        self.assertFalse(should_expand_archive(rom_files))

    def test_should_expand_archive_multiple_games(self):
        """Test that archives with different games ARE expanded."""
        from library import archive as archive_utils

        rom_files = [
            archive_utils.ArchiveInfo("Mario.gba", 1000),
            archive_utils.ArchiveInfo("Zelda.gba", 2000),
        ]

        # Different game names - should expand
        self.assertTrue(should_expand_archive(rom_files))

    def test_should_expand_archive_single_file(self):
        """Test that archives with single ROM are NOT expanded."""
        from library import archive as archive_utils

        rom_files = [
            archive_utils.ArchiveInfo("Mario.gba", 1000),
        ]

        # Single file - should NOT expand
        self.assertFalse(should_expand_archive(rom_files))

    def test_filter_rom_files_skips_nested_archives(self):
        """Test that nested archives are skipped."""
        from library import archive as archive_utils

        contents = [
            archive_utils.ArchiveInfo("game.gba", 1000),
            archive_utils.ArchiveInfo("inner.zip", 5000),  # Nested archive
            archive_utils.ArchiveInfo("other.gba", 2000),
        ]

        filtered = filter_rom_files_in_archive(contents, self.gba_system)

        # Only .gba files, not nested archive
        self.assertEqual(len(filtered), 2)
        names = [f.name for f in filtered]
        self.assertIn("game.gba", names)
        self.assertIn("other.gba", names)
        self.assertNotIn("inner.zip", names)

    def test_filter_rom_files_only_valid_extensions(self):
        """Test that only valid extensions for system are included."""
        from library import archive as archive_utils

        contents = [
            archive_utils.ArchiveInfo("game.gba", 1000),
            archive_utils.ArchiveInfo("game.nes", 2000),  # Wrong system
            archive_utils.ArchiveInfo("readme.txt", 100),
        ]

        filtered = filter_rom_files_in_archive(contents, self.gba_system)

        # Only .gba file
        self.assertEqual(len(filtered), 1)
        self.assertEqual(filtered[0].name, "game.gba")

    def test_detect_system_for_archived_file_exclusive_extension(self):
        """Test system detection for archived file with exclusive extension."""
        systems_cache = [self.gba_system, self.n64_system]
        exclusive_map = {".gba": self.gba_system, ".z64": self.n64_system}

        # Archive in non-matching folder, but .gba is exclusive to GBA
        system = detect_system_for_archived_file(
            "/roms/EverDrive 64/game.zip", "Mario.gba", systems_cache, exclusive_map
        )
        self.assertEqual(system, self.gba_system)

    def test_detect_system_for_archived_file_internal_folder(self):
        """Test system detection with internal folder path takes precedence."""
        systems_cache = [self.gba_system, self.n64_system]
        exclusive_map = {".gba": self.gba_system, ".z64": self.n64_system}

        # Internal path has N64 folder, should match even if archive path doesn't
        system = detect_system_for_archived_file(
            "/roms/RandomFolder/collection.zip",
            "Roms/N64/Mario.z64",
            systems_cache,
            exclusive_map,
        )
        self.assertEqual(system, self.n64_system)

    def test_detect_system_for_archived_file_archive_fallback(self):
        """Test system detection falls back to archive path when internal doesn't match."""
        systems_cache = [self.gba_system, self.n64_system]
        exclusive_map = {".gba": self.gba_system, ".z64": self.n64_system}

        # Archive path has GBA folder, internal path has no folder
        system = detect_system_for_archived_file(
            "/roms/GBA/collection.zip", "Mario.gba", systems_cache, exclusive_map
        )
        self.assertEqual(system, self.gba_system)

    def test_detect_system_for_archived_file_no_match(self):
        """Test system detection returns None when no match possible."""
        systems_cache = [self.gba_system, self.n64_system]
        exclusive_map = {".gba": self.gba_system, ".z64": self.n64_system}

        # No folder matches and extension isn't exclusive
        system = detect_system_for_archived_file(
            "/roms/RandomFolder/collection.zip",
            "game.txt",  # Not a ROM extension
            systems_cache,
            exclusive_map,
        )
        self.assertIsNone(system)


class TestProgressCallback(TestCase):
    """Test progress callback functionality in scanner."""

    def setUp(self):
        """Create test system data."""
        # Sync systems from config
        sync_systems()
        self.gba_system = System.objects.get(slug="gba")

    def test_progress_callback_with_fixtures(self):
        """Test that progress callback works with real fixture files."""
        from unittest.mock import Mock

        # Create mock callback
        mock_callback = Mock()

        # Run scan with callback on fixtures
        result = scan_directory(str(FIXTURES_PATH), progress_callback=mock_callback)

        # Verify results
        self.assertGreater(result["added"], 0)

        # Verify callback was called
        self.assertTrue(mock_callback.called)

        # Get the calls to the callback
        calls = mock_callback.call_args_list

        # Should be called at least once
        self.assertGreaterEqual(len(calls), 1)

        # Check the structure of the callback data
        for call in calls:
            args, kwargs = call
            callback_data = args[0]

            # Verify the callback data has the expected keys
            self.assertIn("files_processed", callback_data)
            self.assertIn("roms_found", callback_data)
            self.assertIn("images_found", callback_data)
            self.assertIn("current_directory", callback_data)

            # Verify data types
            self.assertIsInstance(callback_data["files_processed"], int)
            self.assertIsInstance(callback_data["roms_found"], int)
            self.assertIsInstance(callback_data["images_found"], int)
            self.assertIsInstance(callback_data["current_directory"], str)

    def test_progress_callback_counts_archive_entries(self):
        """Test that progress callback counts files inside archives, not just the archive itself.

        When scanning a multi-game archive with N ROMs, the files_processed counter
        should increase by N (one for each ROM found), not just 1 (for the archive).
        """
        from unittest.mock import Mock
        import tempfile
        import shutil
        import zipfile

        # Create a temp directory with just one archive containing multiple games
        temp_dir = Path(tempfile.mkdtemp())
        gba_folder = temp_dir / "GBA"
        gba_folder.mkdir(parents=True, exist_ok=True)

        # Create a ZIP file with multiple ROMs (different game names = will be expanded)
        archive_path = gba_folder / "multi_game.zip"
        with zipfile.ZipFile(archive_path, "w") as zf:
            zf.writestr("Game A (USA).gba", b"fake rom data A")
            zf.writestr("Game B (USA).gba", b"fake rom data B")
            zf.writestr("Game C (USA).gba", b"fake rom data C")

        try:
            # Create a mock callback that tracks all calls
            mock_callback = Mock()

            # Run scan with callback
            result = scan_directory(str(temp_dir), progress_callback=mock_callback)

            # Verify ROMs were added (should expand to 3 ROMs)
            self.assertEqual(result["added"], 3)

            # Get the final callback call - should have counted all archive entries
            final_call = mock_callback.call_args_list[-1]
            final_data = final_call[0][0]

            # files_processed should be at least 3 (the archive entries)
            # It might be more if callback is called multiple times during processing
            self.assertGreaterEqual(
                final_data["files_processed"],
                3,
                f"Expected files_processed >= 3 (for 3 archive entries), "
                f"got {final_data['files_processed']}. Archive entries should be "
                f"counted individually, not just the archive file.",
            )

            # Also verify roms_found matches what was added
            self.assertEqual(final_data["roms_found"], 3)

        finally:
            shutil.rmtree(temp_dir, ignore_errors=True)


class TestLayeredDetection(TestCase):
    """Test layered detection strategy for ROMs."""

    def setUp(self):
        """Create test system data and temporary ROM library."""
        import tempfile

        sync_systems()
        self.gba_system = System.objects.get(slug="gba")
        self.n64_system = System.objects.get(slug="n64")

        # Create temporary directory for test ROMs
        self.temp_dir = Path(tempfile.mkdtemp())
        self.rom_library = self.temp_dir / "roms"
        self.rom_library.mkdir()

        # Clear ROMs between tests
        ROM.objects.all().delete()
        Game.objects.all().delete()
        ROMSet.objects.all().delete()

    def tearDown(self):
        """Clean up temporary directory."""
        import shutil

        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def test_non_rom_extension_skipped_in_system_folder(self):
        """Non-ROM extensions (.png, .txt) should be skipped in system folders."""
        # Create system folder with non-ROM files
        gba_folder = self.rom_library / "GBA"
        gba_folder.mkdir(parents=True, exist_ok=True)

        # Create non-ROM files that should be skipped
        (gba_folder / "screenshot.png").write_bytes(b"fake image")
        (gba_folder / "readme.txt").write_text("readme content")
        (gba_folder / "game.jpg").write_bytes(b"fake image")

        # Also create a valid ROM
        (gba_folder / "Mario.gba").write_bytes(b"fake rom")

        result = scan_directory(str(self.rom_library), fetch_metadata=False)

        # Should only add the valid ROM
        self.assertEqual(result["added"], 1)
        rom = ROM.objects.first()
        self.assertEqual(rom.file_name, "Mario.gba")

    def test_unknown_extension_skipped_in_system_folder(self):
        """Unknown extensions should be skipped in system folders."""
        # Create system folder with unknown extension files
        gba_folder = self.rom_library / "GBA"
        gba_folder.mkdir(parents=True, exist_ok=True)

        # Create files with unknown extensions
        (gba_folder / "game.xyz").write_bytes(b"unknown format")
        (gba_folder / "game.foo").write_bytes(b"unknown format")

        # Also create a valid ROM
        (gba_folder / "Zelda.gba").write_bytes(b"fake rom")

        result = scan_directory(str(self.rom_library), fetch_metadata=False)

        # Should only add the valid ROM
        self.assertEqual(result["added"], 1)
        rom = ROM.objects.first()
        self.assertEqual(rom.file_name, "Zelda.gba")

    def test_exclusive_extension_overrides_folder(self):
        """Exclusive extension should override folder-based detection."""
        # Create N64 folder with a GBA file
        n64_folder = self.rom_library / "N64"
        n64_folder.mkdir(parents=True, exist_ok=True)

        # .gba is exclusive to GBA, so even in N64 folder it should be GBA
        (n64_folder / "Mario.gba").write_bytes(b"fake rom")

        result = scan_directory(str(self.rom_library), fetch_metadata=False)

        self.assertEqual(result["added"], 1)
        rom = ROM.objects.first()
        self.assertEqual(rom.rom_set.game.system.slug, "gba")

    def test_archive_in_system_folder_is_acceptable(self):
        """Archives (.zip, .7z) in system folders should be opened and scanned."""
        import zipfile

        gba_folder = self.rom_library / "GBA"
        gba_folder.mkdir(parents=True, exist_ok=True)

        # Create a ZIP with GBA ROMs
        with zipfile.ZipFile(gba_folder / "collection.zip", "w") as zf:
            zf.writestr("Mario.gba", b"fake rom")
            zf.writestr("Zelda.gba", b"fake rom")

        result = scan_directory(str(self.rom_library), fetch_metadata=False)

        # Should expand and create 2 ROMs
        self.assertEqual(result["added"], 2)

    def test_shared_extension_requires_folder_match(self):
        """Shared extensions (like .bin) should require folder match."""
        # Create a random folder with .bin file (shared by Genesis, 32X, Atari, etc.)
        random_folder = self.rom_library / "RandomFolder"
        random_folder.mkdir(parents=True, exist_ok=True)

        # .bin is not exclusive to any system, so no folder match = no detection
        (random_folder / "game.bin").write_bytes(b"fake rom")

        result = scan_directory(str(self.rom_library), fetch_metadata=False)

        # Should NOT add because .bin is not exclusive and folder doesn't match
        self.assertEqual(result["added"], 0)


class TestArcadeROMs(TestCase):
    """Test arcade ROM (archive-as-ROM) handling."""

    def setUp(self):
        """Create test system data and temporary ROM library."""
        import tempfile

        sync_systems()
        self.arcade_system = System.objects.get(slug="arcade")
        self.neogeo_system = System.objects.get(slug="neogeo")
        self.gba_system = System.objects.get(slug="gba")

        # Create temporary directory for test ROMs
        self.temp_dir = Path(tempfile.mkdtemp())
        self.rom_library = self.temp_dir / "roms"
        self.rom_library.mkdir()

        # Clear ROMs between tests
        ROM.objects.all().delete()
        Game.objects.all().delete()
        ROMSet.objects.all().delete()

    def tearDown(self):
        """Clean up temporary directory."""
        import shutil

        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def test_arcade_zip_treated_as_rom(self):
        """Arcade ZIPs should be treated as ROMs without looking inside."""
        import zipfile

        # Create test arcade ZIP with extensionless chip dumps
        arcade_zip = self.rom_library / "ARCADE" / "zookeep.zip"
        arcade_zip.parent.mkdir(parents=True, exist_ok=True)

        with zipfile.ZipFile(arcade_zip, "w") as zf:
            zf.writestr("za10", b"chip dump 1")
            zf.writestr("za11", b"chip dump 2")

        result = scan_directory(str(self.rom_library), fetch_metadata=False)

        self.assertEqual(result["added"], 1)
        rom = ROM.objects.get(file_name="zookeep.zip")
        self.assertEqual(rom.rom_set.game.name, "zookeep")
        self.assertEqual(rom.rom_set.game.system.slug, "arcade")
        # Should NOT be archived (archive_path should be empty)
        self.assertEqual(rom.archive_path, "")
        self.assertEqual(rom.file_path, str(arcade_zip))

    def test_neogeo_zip_treated_as_rom(self):
        """Neo Geo ZIPs should also be treated as ROMs."""
        import zipfile

        # Create test Neo Geo ZIP with internal files
        neogeo_zip = self.rom_library / "NeoGeo" / "kof98.zip"
        neogeo_zip.parent.mkdir(parents=True, exist_ok=True)

        with zipfile.ZipFile(neogeo_zip, "w") as zf:
            zf.writestr("c1", b"character data 1")
            zf.writestr("s1", b"sound data 1")

        result = scan_directory(str(self.rom_library), fetch_metadata=False)

        self.assertEqual(result["added"], 1)
        rom = ROM.objects.get(file_name="kof98.zip")
        self.assertEqual(rom.rom_set.game.name, "kof98")
        self.assertEqual(rom.rom_set.game.system.slug, "neogeo")

    def test_non_arcade_zips_still_look_inside(self):
        """Non-arcade ZIPs should still be opened and scanned normally."""
        import zipfile

        # Create regular GBA archive
        gba_zip = self.rom_library / "GBA" / "collection.zip"
        gba_zip.parent.mkdir(parents=True, exist_ok=True)

        with zipfile.ZipFile(gba_zip, "w") as zf:
            zf.writestr("Mario.gba", b"fake rom data")
            zf.writestr("Zelda.gba", b"fake rom data")

        result = scan_directory(str(self.rom_library), fetch_metadata=False)

        # Should expand and create 2 ROMs
        self.assertEqual(result["added"], 2)
        mario = ROM.objects.get(file_name="Mario.gba")
        zelda = ROM.objects.get(file_name="Zelda.gba")
        self.assertEqual(mario.rom_set.game.system.slug, "gba")
        self.assertEqual(zelda.rom_set.game.system.slug, "gba")


class TestHasheousCache(TestCase):
    """Test Hasheous API caching functionality."""

    def setUp(self):
        """Clear cache before each test."""
        from library.models import HasheousCache

        HasheousCache.objects.all().delete()

    def test_cache_miss_returns_not_found(self):
        """Cache miss should return (False, None)."""
        from library.lookup.hasheous import _check_cache

        found, result = _check_cache("crc32", "12345678")
        self.assertFalse(found)
        self.assertIsNone(result)

    def test_cache_save_and_retrieve_match(self):
        """Saved match should be retrievable from cache."""
        from library.lookup.hasheous import _check_cache, _save_to_cache

        # Simulate an API response
        api_response = {
            "name": "0983 - Super Mario Advance (USA)",
            "platform": {"name": "Nintendo Game Boy Advance"},
            "signature": {"rom": {"signatureSource": "NoIntros"}},
        }

        # Save to cache
        _save_to_cache("crc32", "ABCD1234", api_response)

        # Retrieve from cache
        found, cached = _check_cache("crc32", "abcd1234")  # lowercase should match
        self.assertTrue(found)
        self.assertIsNotNone(cached)
        self.assertEqual(cached["name"], api_response["name"])
        self.assertEqual(cached["platform"]["name"], api_response["platform"]["name"])

    def test_cache_save_and_retrieve_no_match(self):
        """Saved no-match should return (True, None) from cache."""
        from library.lookup.hasheous import _check_cache, _save_to_cache

        # Save a no-match to cache
        _save_to_cache("sha1", "1234567890abcdef1234567890abcdef12345678", None)

        # Retrieve from cache
        found, cached = _check_cache(
            "sha1", "1234567890ABCDEF1234567890ABCDEF12345678"
        )  # uppercase should match
        self.assertTrue(found)
        self.assertIsNone(cached)  # No match found

    def test_cache_hash_type_separation(self):
        """Different hash types should be cached separately."""
        from library.lookup.hasheous import _check_cache, _save_to_cache

        # Save match for crc32
        _save_to_cache(
            "crc32",
            "12345678",
            {"name": "Game A", "platform": {"name": "Test"}, "signature": {}},
        )

        # sha1 with same prefix should not find it
        found, _ = _check_cache("sha1", "12345678")
        self.assertFalse(found)

        # crc32 should find it
        found, _ = _check_cache("crc32", "12345678")
        self.assertTrue(found)

    def test_cache_stores_parsed_info(self):
        """Cache should store parsed LookupResult info when provided."""
        from library.lookup.hasheous import _save_to_cache
        from library.lookup.base import LookupResult
        from library.models import HasheousCache

        api_response = {
            "name": "Test Game (USA)",
            "platform": {"name": "Test Platform"},
            "signature": {"rom": {"signatureSource": "NoIntros"}},
        }

        parsed = LookupResult(
            name="Test Game",
            region="USA",
            revision="",
            tags=[],
            source="NoIntros",
            confidence=0.9,
            raw_name="Test Game (USA)",
        )

        _save_to_cache("crc32", "deadbeef", api_response, parsed)

        # Check the database directly
        entry = HasheousCache.objects.get(hash_type="crc32", hash_value="deadbeef")
        self.assertTrue(entry.matched)
        self.assertEqual(entry.game_name, "Test Game")
        self.assertEqual(entry.region, "USA")
        self.assertEqual(entry.source, "NoIntros")

    def test_lookup_hasheous_cache_returns_result(self):
        """lookup_hasheous_cache should return LookupResult from cached data."""
        from library.lookup.hasheous import _save_to_cache, lookup_hasheous_cache
        from library.lookup.base import LookupResult
        from library.models import System

        # Get or create a test system
        system, _ = System.objects.get_or_create(
            slug="gba",
            defaults={
                "name": "Game Boy Advance",
                "extensions": ["gba"],
                "folder_names": ["GBA", "gba"],
            },
        )

        # Save a cached match with parsed info
        api_response = {
            "name": "Super Mario Advance (USA)",
            "platform": {"name": "Nintendo Game Boy Advance"},
            "signature": {"rom": {"signatureSource": "NoIntros"}},
        }
        parsed = LookupResult(
            name="Super Mario Advance",
            region="USA",
            revision="",
            tags=[],
            source="NoIntros",
            confidence=0.9,
            raw_name="Super Mario Advance (USA)",
        )
        _save_to_cache("crc32", "cafe1234", api_response, parsed)

        # lookup_hasheous_cache should find it
        result = lookup_hasheous_cache(system, crc32="CAFE1234")
        self.assertIsNotNone(result)
        self.assertEqual(result.name, "Super Mario Advance")
        self.assertEqual(result.region, "USA")
        self.assertEqual(result.source, "NoIntros")

    def test_lookup_rom_uses_cache_when_hasheous_disabled(self):
        """lookup_rom should check Hasheous cache even when use_hasheous=False.

        Note: If the cached result doesn't have a screenscraper_id, lookup_rom
        will continue to try other services (ScreenScraper) to get one.
        """
        from unittest.mock import MagicMock
        from library.lookup.hasheous import _save_to_cache
        from library.lookup.base import LookupResult, LookupService
        from library.lookup import lookup_rom
        from library.models import System

        # Get or create a test system
        system, _ = System.objects.get_or_create(
            slug="gba",
            defaults={
                "name": "Game Boy Advance",
                "extensions": ["gba"],
                "folder_names": ["GBA", "gba"],
            },
        )

        # Save a cached match (without screenscraper_id - Hasheous doesn't provide it)
        api_response = {
            "name": "Zelda Minish Cap (USA)",
            "platform": {"name": "Nintendo Game Boy Advance"},
            "signature": {"rom": {"signatureSource": "NoIntros"}},
        }
        parsed = LookupResult(
            name="Zelda Minish Cap",
            region="USA",
            revision="",
            tags=[],
            source="NoIntros",
            confidence=0.9,
            raw_name="Zelda Minish Cap (USA)",
        )
        _save_to_cache("crc32", "beef5678", api_response, parsed)

        # Create a mock service that returns a result with screenscraper_id
        mock_ss_result = LookupResult(
            name="Zelda Minish Cap",
            region="",
            revision="",
            tags=[],
            source="screenscraper",
            confidence=0.85,
            raw_name="Zelda Minish Cap",
            screenscraper_id=54321,
        )
        mock_service = MagicMock(spec=LookupService)
        mock_service.name = "mock_screenscraper"
        mock_service.is_available.return_value = True
        mock_service.lookup.return_value = mock_ss_result

        # lookup_rom with use_hasheous=False should check Hasheous cache,
        # then continue to mock service to get screenscraper_id
        result = lookup_rom(
            system, crc32="BEEF5678", use_hasheous=False, services=[mock_service]
        )
        self.assertIsNotNone(result)
        self.assertEqual(result.screenscraper_id, 54321)

    def test_lookup_rom_no_api_call_when_hasheous_disabled_and_cache_miss(self):
        """lookup_rom should not make API calls when use_hasheous=False."""
        from unittest.mock import patch
        from library.lookup import lookup_rom
        from library.models import System

        system, _ = System.objects.get_or_create(
            slug="gba",
            defaults={
                "name": "Game Boy Advance",
                "extensions": ["gba"],
                "folder_names": ["GBA", "gba"],
            },
        )

        # Patch the API request function to track calls
        with patch("library.lookup.hasheous.requests.post") as mock_post:
            # lookup_rom with use_hasheous=False and no cached result
            result = lookup_rom(system, crc32="nocache00", use_hasheous=False)

            # Should return None (no cache hit, no API call)
            self.assertIsNone(result)
            # API should NOT have been called
            mock_post.assert_not_called()


class TestSourcePathGrouping(TestCase):
    """Test ROMSet grouping by source_path."""

    def setUp(self):
        """Create test system data."""
        from library.system_loader import sync_systems

        sync_systems()
        self.gba_system = System.objects.get(slug="gba")

        # Clear database between tests
        ROM.objects.all().delete()
        Game.objects.all().delete()
        ROMSet.objects.all().delete()

    def test_get_source_path_for_archived_rom(self):
        """Test get_source_path returns archive path for archived ROMs."""
        from library.scanner import get_source_path

        # For archived ROMs, source_path should be the archive itself
        source = get_source_path(
            "/roms/gba/game.gba",
            archive_path="/roms/collection.7z",
        )
        self.assertEqual(source, "/roms/collection.7z")

    def test_get_source_path_for_loose_rom(self):
        """Test get_source_path returns parent directory for loose ROMs."""
        from library.scanner import get_source_path

        # For loose files, source_path should be the parent directory
        source = get_source_path("/roms/gba/game.gba", archive_path="")
        self.assertEqual(source, "/roms/gba")

    def test_get_or_create_rom_set_uses_source_path(self):
        """Test that get_or_create_rom_set creates ROMSets with source_path."""
        from library.scanner import get_or_create_rom_set

        # Create first ROMSet
        rom_set1, _, _, _ = get_or_create_rom_set(
            name="Test Game",
            system=self.gba_system,
            region="USA",
            revision="",
            source_path="/roms/collection1",
        )

        # Create second ROMSet with same game/region but different source
        rom_set2, _, _, _ = get_or_create_rom_set(
            name="Test Game",
            system=self.gba_system,
            region="USA",
            revision="",
            source_path="/roms/collection2",
        )

        # Should be different ROMSets
        self.assertNotEqual(rom_set1.pk, rom_set2.pk)
        self.assertEqual(rom_set1.source_path, "/roms/collection1")
        self.assertEqual(rom_set2.source_path, "/roms/collection2")

        # Both should belong to the same game
        self.assertEqual(rom_set1.game.pk, rom_set2.game.pk)

    def test_same_source_same_romset(self):
        """Test that ROMs from same source go to same ROMSet."""
        from library.scanner import get_or_create_rom_set

        # Create ROMSet
        rom_set1, _, _, _ = get_or_create_rom_set(
            name="Test Game",
            system=self.gba_system,
            region="USA",
            revision="",
            source_path="/roms/collection1",
        )

        # Get same ROMSet again (same source)
        rom_set2, _, _, _ = get_or_create_rom_set(
            name="Test Game",
            system=self.gba_system,
            region="USA",
            revision="",
            source_path="/roms/collection1",
        )

        # Should be the same ROMSet
        self.assertEqual(rom_set1.pk, rom_set2.pk)

    def test_romset_unique_constraint(self):
        """Test unique constraint on (game, region, revision, source_path)."""
        from django.db import IntegrityError

        # Create a game
        game = Game.objects.create(name="Test Game", system=self.gba_system)

        # Create first ROMSet
        ROMSet.objects.create(
            game=game, region="USA", revision="", source_path="/roms/collection1"
        )

        # Attempt to create duplicate should fail
        with self.assertRaises(IntegrityError):
            ROMSet.objects.create(
                game=game, region="USA", revision="", source_path="/roms/collection1"
            )


class TestIssue3FolderAliases(TestCase):
    """Issue #3: No-Intro style folder names must resolve via explicit aliases.

    The reported library uses top-level folders like "Sony - Playstation
    Portable", "SNK - Neo Geo", "Sega - Genesis - MegaDrive", "Atari - ST",
    "Commodore - C64" and "NEC - Super Grafx". These tests read the real
    systems config via sync_systems().
    """

    def setUp(self):
        sync_systems()
        self.systems_cache = list(System.objects.all())
        self.exclusive_map = build_extension_map(self.systems_cache)

    def _detect(self, path):
        return detect_system(path, self.systems_cache, self.exclusive_map)

    def _get(self, slug):
        return System.objects.get(slug=slug)

    def test_psp_no_intro_folder_alias(self):
        self.assertEqual(
            self._detect("/roms/Sony - Playstation Portable/Game.iso"), self._get("psp")
        )

    def test_neogeo_snk_folder_alias(self):
        self.assertEqual(
            self._detect("/roms/SNK - Neo Geo/mslug.zip"), self._get("neogeo")
        )

    def test_genesis_no_intro_folder_alias(self):
        self.assertEqual(
            self._detect("/roms/Sega - Genesis - MegaDrive/Cool Spot (J) [!].bin"),
            self._get("genesis"),
        )

    def test_atari_st_folder_alias(self):
        self.assertEqual(
            self._detect("/roms/Atari - ST/10th Frame (Europe).st"),
            self._get("atarist"),
        )

    def test_c64_no_intro_folder_alias(self):
        self.assertEqual(
            self._detect("/roms/Commodore - C64/COSMICR0.G64"), self._get("c64")
        )

    def test_c64_tap_and_d81_folder_extensions(self):
        """C64 tape (.tap) and disk (.d81) images import from the C64 folder."""
        c64 = self._get("c64")
        self.assertEqual(
            self._detect("/roms/Commodore - C64/Jupiter Lander (Tape).tap"), c64
        )
        self.assertEqual(
            self._detect("/roms/Commodore - C64/Summer Games (Disk 1).d81"), c64
        )

    def test_ngage_supported_app_folder(self):
        """N-Gage .app titles import from the N-Gage folder alias."""
        self.assertEqual(
            self._detect("/roms/Nokia - N-Gage/System Rush (Europe).app"),
            self._get("ngage"),
        )

    def test_sgfx_folder_alias(self):
        self.assertEqual(
            self._detect("/roms/NEC - Super Grafx/Battle Ace (Japan).pce"),
            self._get("sgfx"),
        )

    def test_spc_music_dump_is_non_rom(self):
        """SNES sound dumps are rejected as non-ROM instead of scan noise."""
        load_non_rom_extensions.cache_clear()
        system, reason = detect_system_details(
            "/roms/Nintendo - Super Nintendo Entertainment System - Super Famicom/"
            "Chrono Trigger.spc",
            self.systems_cache,
            self.exclusive_map,
        )
        self.assertIsNone(system)
        self.assertEqual(reason, "non_rom_extension")


class TestIssue3FolderContextOverExclusive(TestCase):
    """Issue #3: a folder that ACCEPTS a shared extension beats an exclusive
    extension claimed by a different system; the deepest folder component
    wins; genuine cross-folder exclusives still hold.

    Built with explicit System objects so the precedence rules are pinned
    independently of packaged config.
    """

    def setUp(self):
        self.amiga = System.objects.create(
            name="Commodore Amiga",
            slug="amiga-test",
            extensions=[".adf", ".hdf", ".ipf", ".zip"],
            exclusive_extensions=[".adf", ".hdf", ".ipf"],
            folder_names=["Commodore - Amiga", "Amiga"],
        )
        self.atarist = System.objects.create(
            name="Atari ST",
            slug="atarist-test",
            extensions=[".st", ".stx", ".msa", ".ipf", ".zip"],
            exclusive_extensions=[".st", ".stx", ".msa"],
            folder_names=["Atari - ST", "Atari ST", "ST"],
        )
        self.genesis = System.objects.create(
            name="Sega Genesis",
            slug="genesis-test",
            extensions=[".gen", ".smd", ".bin", ".md", ".zip"],
            exclusive_extensions=[".gen", ".smd", ".md"],
            folder_names=["Sega - Genesis - MegaDrive", "Genesis", "Mega Drive"],
        )
        self.sega32x = System.objects.create(
            name="Sega 32X",
            slug="32x-test",
            extensions=[".32x", ".bin"],
            exclusive_extensions=[".32x"],
            folder_names=["32X", "Sega 32X"],
        )
        self.psp = System.objects.create(
            name="PSP",
            slug="psp-test",
            extensions=[".iso", ".cso", ".chd"],
            exclusive_extensions=[".cso"],
            folder_names=["Sony - Playstation Portable", "PSP"],
        )
        self.ps2 = System.objects.create(
            name="PlayStation 2",
            slug="ps2-test",
            extensions=[".iso", ".cso", ".chd", ".bin"],
            exclusive_extensions=[],
            folder_names=["Sony - PlayStation 2", "PS2"],
        )
        self.xbox360 = System.objects.create(
            name="Microsoft Xbox 360",
            slug="xbox360-test",
            extensions=[".iso", ".xex"],
            exclusive_extensions=[".xex"],
            folder_names=["Microsoft Xbox 360", "XBOX360"],
        )
        # Cache order mirrors systems.json order: genesis precedes 32x.
        self.systems_cache = [
            self.genesis,
            self.sega32x,
            self.amiga,
            self.atarist,
            self.psp,
            self.ps2,
            self.xbox360,
        ]
        self.exclusive_map = build_extension_map(self.systems_cache)

    def _detect(self, path):
        return detect_system(path, self.systems_cache, self.exclusive_map)

    def _detect_member(self, archive_path, internal_path):
        return detect_system_for_archived_file(
            archive_path, internal_path, self.systems_cache, self.exclusive_map
        )

    def test_atari_st_ipf_member_is_atarist_not_amiga(self):
        """An .ipf member under Atari - ST is an Atari ST disk, not Amiga."""
        self.assertEqual(
            self._detect_member(
                "/roms/Atari - ST/10th Frame (Europe).zip", "10th Frame (Europe).ipf"
            ),
            self.atarist,
        )

    def test_atari_st_ipf_disk_is_atarist_not_amiga(self):
        self.assertEqual(
            self._detect("/roms/Atari - ST/10th Frame (Europe).ipf"), self.atarist
        )

    def test_deepest_folder_component_wins(self):
        """A .bin in the nested 32X folder must not be captured by the outer
        Genesis folder, regardless of system config order."""
        self.assertEqual(
            self._detect("/roms/Sega - Genesis - MegaDrive/32X/36 Great Holes.bin"),
            self.sega32x,
        )

    def test_internal_folder_beats_accepting_outer_folder(self):
        """An inner member folder that accepts the extension wins even when the
        outer archive folder would accept it too."""
        self.assertEqual(
            self._detect_member(
                "/roms/Sega - Genesis - MegaDrive/collection.zip", "32X/game.bin"
            ),
            self.sega32x,
        )

    def test_rejecting_internal_folder_blocks_outer_fallback(self):
        """An explicit inner folder that rejects the extension must not be
        silently overridden by the accepting outer archive folder."""
        self.assertIsNone(
            self._detect_member(
                "/roms/Sony - PlayStation 2/collection.zip", "XBOX360/game.bin"
            )
        )

    def test_genesis_md_member_in_genesis_archive(self):
        """.md is a Mega Drive ROM extension inside Genesis archives, not markdown."""
        self.assertEqual(
            self._detect_member(
                "/roms/Sega - Genesis - MegaDrive/AAAHH!!! Real Monsters (Europe).zip",
                "AAAHH!!! Real Monsters (Europe).md",
            ),
            self.genesis,
        )

    def test_md_elsewhere_stays_non_rom(self):
        """.md must stay blocked (markdown) outside a Genesis folder context."""
        self.assertIsNone(
            self._detect_member("/roms/Commodore - Amiga/Game.zip", "readme.md")
        )

    def test_disk_md_in_genesis_folder_is_genesis(self):
        """.md on disk inside the Genesis folder is a Mega Drive ROM, not markdown."""
        self.assertEqual(
            self._detect("/roms/Sega - Genesis - MegaDrive/Cool Spot (J) [!].md"),
            self.genesis,
        )

    def _write(self, relative, content):
        import tempfile

        root = Path(tempfile.mkdtemp())
        self.addCleanup(__import__("shutil").rmtree, root, True)
        path = root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(content)
        return str(path)

    def test_unscoped_disk_md_stays_non_rom(self):
        """Unscoped README.md must not import as Genesis despite the exclusive .md claim."""
        self.assertIsNone(self._detect(self._write("README.md", b"# Readme\n")))

    def test_disk_md_in_foreign_folder_stays_non_rom(self):
        """Markdown in a foreign system folder stays blocked."""
        self.assertIsNone(self._detect(self._write("Amiga/readme.md", b"Notes\n")))

    def test_binary_disk_md_outside_genesis_folder_is_genesis(self):
        """A real Mega Drive ROM in an unrecognized folder still imports.

        Markdown is text; ROM images are binary. Upgraders with .md ROMs in
        folders like "SEGA Genesis" must not lose them.
        """
        rom = b"\x00\xff\xfe\x00" * 64 + b"SEGA MEGA DRIVE " + b"\x00" * 64
        path = self._write("Curated/SEGA Genesis/04. Sonic (USA, Europe).md", rom)
        self.assertEqual(self._detect(path), self.genesis)

    def test_unreadable_disk_md_counts_as_rom(self):
        """A read failure must never make a stored ROM look like markdown."""
        self.assertEqual(self._detect("/nonexistent/Sonic (USA).md"), self.genesis)

    def test_custom_declared_non_rom_extension_stays_blocked(self):
        """A folder system merely listing a non-ROM extension (.txt) must not
        import it, on disk or inside archives; only the extension's configured
        exclusive claimant may override the blocklist."""
        reader = System.objects.create(
            name="Reader",
            slug="reader-test",
            extensions=[".txt", ".zip"],
            exclusive_extensions=[],
            folder_names=["Reader"],
        )
        cache = self.systems_cache + [reader]
        reader_map = build_extension_map(cache)
        self.assertIsNone(detect_system("/roms/Reader/notes.txt", cache, reader_map))
        self.assertIsNone(
            detect_system_for_archived_file(
                "/roms/Reader/bundle.zip", "notes.txt", cache, reader_map
            )
        )

    def test_cso_in_ps2_folder_overrides_psp_exclusive(self):
        """.cso is exclusively claimed by PSP but a PS2 folder context is compatible."""
        self.assertEqual(self._detect("/roms/Sony - PlayStation 2/Game.cso"), self.ps2)

    def test_xex_in_psp_folder_stays_xbox360(self):
        """Issue evidence: Fable II default.xex under the PSP folder is a genuine
        Xbox 360 executable; PSP does not accept .xex so the exclusive mapping
        must keep winning. Guards the preserved behavior."""
        self.assertEqual(
            self._detect_member(
                "/roms/Sony - Playstation Portable/Fable II - Game of the Year Edition (USA, Europe).7z",
                "Fable II - Game of the Year Edition (En,Zh,Ko,Pl,Cs,Hu)/default.xex",
            ),
            self.xbox360,
        )

    def test_gbc_in_gb_folder_still_gbc(self):
        """Regression guard: GB folder does not accept .gbc so the exclusive
        Game Boy Color mapping must still win."""
        gb = System.objects.create(
            name="Game Boy",
            slug="gb-test",
            extensions=[".gb"],
            exclusive_extensions=[".gb"],
            folder_names=["Nintendo - Game Boy", "Game Boy"],
        )
        gbc = System.objects.create(
            name="Game Boy Color",
            slug="gbc-test",
            extensions=[".gbc"],
            exclusive_extensions=[".gbc"],
            folder_names=["Game Boy Color"],
        )
        cache = [gb, gbc] + self.systems_cache
        self.assertEqual(
            detect_system(
                "/roms/Nintendo - Game Boy/Tetris.gbc",
                cache,
                build_extension_map(cache),
            ),
            gbc,
        )


class TestIssue3ScanIntegration(TestCase):
    """End-to-end scan_directory regressions for issue #3 folders."""

    def setUp(self):
        import tempfile

        sync_systems()
        self.temp_dir = Path(tempfile.mkdtemp())
        self.rom_library = self.temp_dir / "roms"
        self.rom_library.mkdir(parents=True)
        ROM.objects.all().delete()
        Game.objects.all().delete()
        ROMSet.objects.all().delete()

    def tearDown(self):
        import shutil

        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def test_scan_atari_st_zip_stores_atarist(self):
        """Atari - ST zip containing .ipf must become an Atari ST ROMSet, not Amiga."""
        import zipfile

        st_folder = self.rom_library / "Atari - ST"
        st_folder.mkdir()
        with zipfile.ZipFile(st_folder / "10th Frame (Europe).zip", "w") as zf:
            zf.writestr("10th Frame (Europe).ipf", b"fake ipf")

        result = scan_directory(str(self.rom_library), fetch_metadata=False)
        self.assertEqual(result["added"], 1)
        rom = ROM.objects.first()
        self.assertEqual(rom.rom_set.game.system.slug, "atarist")

    def test_scan_neogeo_zip_uses_archive_as_rom(self):
        """SNK - Neo Geo MAME-style zip must resolve to Neo Geo and become one
        archive-as-ROM ROMSet (previously: 0 added, all members skipped)."""
        import zipfile

        ng_folder = self.rom_library / "SNK - Neo Geo"
        ng_folder.mkdir()
        with zipfile.ZipFile(ng_folder / "mslug.zip", "w") as zf:
            zf.writestr("030-p1.rom", b"fake p1")
            zf.writestr("030-s1.rom", b"fake s1")
            zf.writestr("030-m1.rom", b"fake m1")

        result = scan_directory(str(self.rom_library), fetch_metadata=False)
        self.assertEqual(result["added"], 1)
        rom = ROM.objects.first()
        self.assertEqual(rom.rom_set.game.system.slug, "neogeo")

    def test_scan_genesis_no_intro_folder(self):
        """Genesis .bin in the No-Intro named folder must be detected."""
        gen_folder = self.rom_library / "Sega - Genesis - MegaDrive"
        gen_folder.mkdir()
        (gen_folder / "Cool Spot (J) [!].bin").write_bytes(b"fake genesis rom")

        result = scan_directory(str(self.rom_library), fetch_metadata=False)
        self.assertEqual(result["added"], 1)
        rom = ROM.objects.first()
        self.assertEqual(rom.rom_set.game.system.slug, "genesis")


class TestRescanReconciliation(TestCase):
    """Rescans bring stored records in line with the current detector."""

    def setUp(self):
        import tempfile

        sync_systems()
        self.temp_dir = Path(tempfile.mkdtemp())
        self.rom_library = self.temp_dir / "roms"
        self.rom_library.mkdir(parents=True)
        ROM.objects.all().delete()
        Game.objects.all().delete()
        ROMSet.objects.all().delete()

    def tearDown(self):
        import shutil

        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def _store(self, slug, name, file_path, **rom_fields):
        """Create a record as an older detector would have stored it."""
        game = Game.objects.create(name=name, system=System.objects.get(slug=slug))
        rom_set = ROMSet.objects.create(game=game, region="")
        return ROM.objects.create(
            rom_set=rom_set,
            file_path=file_path,
            file_name=Path(file_path.split("!")[-1]).name,
            file_size=1,
            **rom_fields,
        )

    def test_rescan_moves_wrong_system_archive_member(self):
        """An Atari ST .ipf stored as Amiga is re-imported as Atari ST."""
        import zipfile

        archive = self.rom_library / "Atari - ST" / "10th Frame (Europe).zip"
        archive.parent.mkdir()
        with zipfile.ZipFile(archive, "w") as zf:
            zf.writestr("10th Frame (Europe).ipf", b"fake ipf")
        member = "10th Frame (Europe).ipf"
        self._store(
            "amiga",
            "10th Frame",
            f"{archive}!{member}",
            archive_path=str(archive),
            path_in_archive=member,
        )

        result = scan_directory(str(self.rom_library), fetch_metadata=False)

        self.assertEqual(result["reclassified"], 1)
        rom = ROM.objects.get()
        self.assertEqual(rom.rom_set.game.system.slug, "atarist")
        self.assertFalse(Game.objects.filter(system__slug="amiga").exists())

    def test_rescan_moves_wrong_system_loose_file(self):
        """A SuperGrafx .pce stored as PC Engine is re-imported as SuperGrafx."""
        rom_file = self.rom_library / "NEC - Super Grafx" / "Daimakaimura (Japan).pce"
        rom_file.parent.mkdir()
        rom_file.write_bytes(b"fake sgx")
        self._store("pce", "Daimakaimura", str(rom_file))

        result = scan_directory(str(self.rom_library), fetch_metadata=False)

        self.assertEqual(result["reclassified"], 1)
        self.assertEqual(ROM.objects.get().rom_set.game.system.slug, "sgfx")

    def test_rescan_keeps_manual_system_choice(self):
        """A system the user picked by hand is never overridden."""
        rom_file = self.rom_library / "NEC - Super Grafx" / "Daimakaimura (Japan).pce"
        rom_file.parent.mkdir()
        rom_file.write_bytes(b"fake sgx")
        rom = self._store("pce", "Daimakaimura", str(rom_file))
        Game.objects.filter(pk=rom.rom_set.game_id).update(
            name_source=Game.SOURCE_MANUAL
        )

        result = scan_directory(str(self.rom_library), fetch_metadata=False)

        self.assertEqual(result["reclassified"], 0)
        self.assertEqual(ROM.objects.get().rom_set.game.system.slug, "pce")

    def test_rescan_removes_records_no_longer_recognized(self):
        """A stored file that is now a non-ROM type (.spc) is removed."""
        spc = self.rom_library / "SNES" / "Chrono Trigger - 01 Title.spc"
        spc.parent.mkdir()
        spc.write_bytes(b"sound dump")
        self._store("snes", "Chrono Trigger - 01 Title", str(spc))

        result = scan_directory(str(self.rom_library), fetch_metadata=False)

        self.assertEqual(result["deleted_roms"], 1)
        self.assertFalse(ROM.objects.exists())
        self.assertFalse(Game.objects.exists())

    def test_rescan_replaces_expanded_members_of_archive_as_rom_zip(self):
        """Members stored from a now archive-as-ROM zip give way to one ROM."""
        import zipfile

        archive = self.rom_library / "SNK - Neo Geo" / "garou.zip"
        archive.parent.mkdir()
        with zipfile.ZipFile(archive, "w") as zf:
            zf.writestr("253d-p1.rom", b"p1")
            zf.writestr("253d-s1.rom", b"s1")
        self._store(
            "neogeo",
            "253d-p1",
            f"{archive}!253d-p1.rom",
            archive_path=str(archive),
            path_in_archive="253d-p1.rom",
        )

        scan_directory(str(self.rom_library), fetch_metadata=False)

        rom = ROM.objects.get()
        self.assertEqual(rom.file_path, str(archive))
        self.assertEqual(rom.archive_path, "")

    def test_rescan_keeps_records_of_unreadable_archive(self):
        """A read failure is not evidence that the archive's ROMs are gone."""
        archive = self.rom_library / "GBA" / "Pack.zip"
        archive.parent.mkdir()
        archive.write_bytes(b"not a zip")
        self._store(
            "gba",
            "Mario",
            f"{archive}!Mario.gba",
            archive_path=str(archive),
            path_in_archive="Mario.gba",
        )

        result = scan_directory(str(self.rom_library), fetch_metadata=False)

        self.assertEqual(result["deleted_roms"], 0)
        self.assertTrue(ROM.objects.exists())
        self.assertTrue(result["errors"])

    def test_rescan_keeps_records_in_unreadable_directory(self):
        """A directory the scan cannot list keeps its stored ROMs."""
        import os

        folder = self.rom_library / "GBA"
        folder.mkdir()
        rom_file = folder / "Mario.gba"
        rom_file.write_bytes(b"fake gba")
        self._store("gba", "Mario", str(rom_file))
        os.chmod(folder, 0)
        try:
            if os.access(folder, os.R_OK):
                self.skipTest("running with privileges that bypass permissions")
            result = scan_directory(str(self.rom_library), fetch_metadata=False)
        finally:
            os.chmod(folder, 0o755)

        self.assertEqual(result["deleted_roms"], 0)
        self.assertTrue(ROM.objects.exists())
        self.assertTrue(result["errors"])

    def test_scan_leaves_sibling_folder_with_shared_prefix_alone(self):
        """Scanning /roms/GB must not touch ROMs stored under /roms/GBA."""
        gb = self.rom_library / "GB"
        gb.mkdir()
        gba_file = self.rom_library / "GBA" / "Mario.gba"
        gba_file.parent.mkdir()
        gba_file.write_bytes(b"fake gba")
        self._store("gba", "Mario", str(gba_file))

        result = scan_directory(str(gb), fetch_metadata=False)

        self.assertEqual(result["deleted_roms"], 0)
        self.assertTrue(ROM.objects.exists())

    def test_rescan_keeps_binary_md_rom_in_unrecognized_folder(self):
        """Upgrade safety: a stored .md ROM outside a Genesis alias survives,
        while a stored README.md next to it is removed."""
        folder = self.rom_library / "Curated" / "SEGA Genesis"
        folder.mkdir(parents=True)
        rom = folder / "04. Sonic The Hedgehog (USA, Europe).md"
        rom.write_bytes(b"\x00\xff" * 128 + b"SEGA MEGA DRIVE ")
        readme = folder / "README.md"
        readme.write_bytes(b"# My curated set\n")
        self._store("genesis", "Sonic The Hedgehog", str(rom))
        self._store("genesis", "README", str(readme))

        result = scan_directory(str(self.rom_library), fetch_metadata=False)

        self.assertEqual(result["deleted_roms"], 1)
        self.assertEqual(
            list(ROM.objects.values_list("file_path", flat=True)), [str(rom)]
        )

    def test_rescan_is_idempotent(self):
        """A second scan of an up-to-date library changes nothing."""
        rom_file = self.rom_library / "NEC - Super Grafx" / "Daimakaimura (Japan).pce"
        rom_file.parent.mkdir()
        rom_file.write_bytes(b"fake sgx")
        scan_directory(str(self.rom_library), fetch_metadata=False)
        rom_pk = ROM.objects.get().pk

        result = scan_directory(str(self.rom_library), fetch_metadata=False)

        self.assertEqual(result["reclassified"], 0)
        self.assertEqual(result["deleted_roms"], 0)
        self.assertEqual(result["skipped"], 1)
        self.assertEqual(ROM.objects.get().pk, rom_pk)


class TestArchiveAsRomContentGate(TestCase):
    """Archive-as-ROM folders skip archives that hold no game data."""

    def setUp(self):
        import tempfile

        sync_systems()
        self.temp_dir = Path(tempfile.mkdtemp())
        self.rom_library = self.temp_dir / "roms"
        self.rom_library.mkdir(parents=True)
        ROM.objects.all().delete()
        Game.objects.all().delete()
        ROMSet.objects.all().delete()

    def tearDown(self):
        import shutil

        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def _zip(self, relative, members):
        import zipfile

        path = self.rom_library / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        with zipfile.ZipFile(path, "w") as zf:
            for name in members:
                zf.writestr(name, b"data")
        return path

    def test_emulator_config_and_source_zips_are_not_games(self):
        """Kawaks INI packs and Nebula plugin sources are not Neo Geo games."""
        self._zip("SNK - Neo Geo/Kawaks/DefaultWinKawaksINI.zip", ["WinKawaks.ini"])
        self._zip(
            "SNK - Neo Geo/Nebula/Plugins/hq2xsrc.zip",
            ["hq2x.asm", "hq2x.c", "readme.txt", "Makefile"],
        )
        self._zip(
            "SNK - Neo Geo/Nebula/Plugins/PLUGSRC.ZIP",
            ["plug.c", "plug.h", "plug.rc", "plug.res", "Makefile"],
        )
        self._zip("SNK - Neo Geo/garou.zip", ["253d-p1.rom", "253d-s1.rom"])

        result = scan_directory(str(self.rom_library), fetch_metadata=False)

        self.assertEqual(result["added"], 1)
        self.assertEqual(ROM.objects.get().file_name, "garou.zip")

    def test_extensionless_chip_dumps_still_count(self):
        """MAME sets with extensionless chip dumps stay games."""
        self._zip("MAME/zookeep.zip", ["za10", "za11"])

        result = scan_directory(str(self.rom_library), fetch_metadata=False)

        self.assertEqual(result["added"], 1)

    def test_dos_game_with_only_com_executable_is_kept(self):
        """A DOS game shipped as a lone .COM file is still a game."""
        self._zip("DOS/digger.zip", ["DIGGER.COM", "README.TXT"])

        result = scan_directory(str(self.rom_library), fetch_metadata=False)

        self.assertEqual(result["added"], 1)
