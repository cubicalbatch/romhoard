"""Integration tests for ROM download functionality."""

from django.test import TestCase
from pathlib import Path

from library.download import (
    create_romset_bundle,
    get_rom_file,
    get_rom_file_as_stored,
    is_single_rom_archive,
)
from library.models import ROM, ROMSet, Game, GameImage
from library.scanner import scan_directory
from library.system_loader import sync_systems


FIXTURES_PATH = (
    Path(__file__).parent.parent.parent / "tests" / "fixtures" / "rom_library"
)


class TestDownloadIntegration(TestCase):
    """Integration tests using real fixture files."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        sync_systems()

    def setUp(self):
        # Clear any data from previous tests and scan fresh
        ROM.objects.all().delete()
        ROMSet.objects.all().delete()
        Game.objects.all().delete()
        GameImage.objects.all().delete()
        scan_directory(str(FIXTURES_PATH))

    def test_download_loose_rom(self):
        """Test downloading a loose ROM file."""
        rom = ROM.objects.filter(archive_path="").first()
        self.assertIsNotNone(rom, "Expected at least one loose ROM in fixtures")

        with get_rom_file(rom) as (file_path, filename):
            assert Path(file_path).exists()
            assert filename == rom.file_name

    def test_download_archived_rom(self):
        """Test downloading ROM from archive."""
        rom = ROM.objects.exclude(archive_path="").first()
        self.assertIsNotNone(rom, "Expected at least one archived ROM in fixtures")

        with get_rom_file(rom) as (file_path, filename):
            assert Path(file_path).exists()
            # Verify content was extracted (file has size > 0)
            assert Path(file_path).stat().st_size > 0

    def test_create_multi_disc_bundle(self):
        """Test creating ZIP bundle for multi-disc game."""
        # Find a multi-disc ROMSet (Final Fantasy VII has disc 1 and 2)
        multi_disc = ROMSet.objects.filter(roms__disc__isnull=False).distinct().first()
        self.assertIsNotNone(
            multi_disc, "Expected at least one multi-disc ROMSet in fixtures"
        )

        bundle_path, bundle_filename = create_romset_bundle(multi_disc)
        try:
            assert Path(bundle_path).exists()
            assert bundle_filename.endswith(".zip")
        finally:
            # Clean up
            Path(bundle_path).unlink(missing_ok=True)

    def test_create_single_rom_bundle(self):
        """Test creating bundle for single ROM."""
        # Find a single-ROM ROMSet (no disc number)
        single_rom = ROMSet.objects.filter(roms__disc__isnull=True).first()
        self.assertIsNotNone(
            single_rom, "Expected at least one single-ROM ROMSet in fixtures"
        )

        bundle_path, bundle_filename = create_romset_bundle(single_rom)
        try:
            assert Path(bundle_path).exists()
            assert bundle_filename.endswith(".zip")
        finally:
            # Clean up
            Path(bundle_path).unlink(missing_ok=True)


class TestGetROMSetBundleFilename(TestCase):
    """Test the get_romset_bundle_filename function."""

    def setUp(self):
        """Set up test data."""
        from library.models import System, Game

        self.system = System.objects.create(
            name="Test System", slug="test", extensions=[".test"], folder_names=["Test"]
        )
        self.game = Game.objects.create(name="Test Game", system=self.system)

    def test_filename_uses_game_name(self):
        """Test filename is always game name.zip."""
        from library.download import get_romset_bundle_filename

        rom_set = ROMSet.objects.create(game=self.game, region="USA", revision="Rev 1")
        filename = get_romset_bundle_filename(rom_set)
        self.assertEqual(filename, "Test Game.zip")


class TestDownloadViews(TestCase):
    """Test download-related view functions."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        sync_systems()

    def setUp(self):
        # Clear any data from previous tests and scan fresh
        ROM.objects.all().delete()
        ROMSet.objects.all().delete()
        Game.objects.all().delete()
        GameImage.objects.all().delete()
        scan_directory(str(FIXTURES_PATH))

    def test_download_loose_rom_view(self):
        """Test downloading a loose ROM file."""
        from library.views import download_rom

        rom = ROM.objects.filter(archive_path="").first()
        self.assertIsNotNone(rom, "Expected at least one loose ROM in fixtures")

        response = download_rom(None, rom.pk)

        self.assertEqual(response.status_code, 200)
        self.assertTrue(hasattr(response, "file_to_stream"))
        self.assertEqual(
            response["Content-Disposition"], f'attachment; filename="{rom.file_name}"'
        )

    def test_download_archived_rom_view(self):
        """Test downloading an archived ROM file."""
        from library.views import download_rom

        rom = ROM.objects.exclude(archive_path="").first()
        self.assertIsNotNone(rom, "Expected at least one archived ROM in fixtures")

        response = download_rom(None, rom.pk)

        self.assertEqual(response.status_code, 200)
        self.assertTrue(hasattr(response, "file_to_stream"))
        self.assertTrue(
            response["Content-Disposition"].startswith("attachment; filename=")
        )

    def test_download_romset_single_rom(self):
        """Test downloading ROMSet with single ROM."""
        from library.views import download_romset

        rom_set = ROMSet.objects.filter(roms__disc__isnull=True).first()
        self.assertIsNotNone(
            rom_set, "Expected at least one single-ROM ROMSet in fixtures"
        )

        response = download_romset(None, rom_set.pk)

        self.assertEqual(response.status_code, 200)
        self.assertTrue(hasattr(response, "file_to_stream"))

    def test_download_romset_multi_disc(self):
        """Test downloading ROMSet with multiple discs."""
        from library.views import download_romset

        rom_set = ROMSet.objects.filter(roms__disc__isnull=False).distinct().first()
        self.assertIsNotNone(
            rom_set, "Expected at least one multi-disc ROMSet in fixtures"
        )

        response = download_romset(None, rom_set.pk)

        self.assertEqual(response.status_code, 200)
        self.assertTrue(hasattr(response, "file_to_stream"))

    def test_download_game_uses_default_romset(self):
        """Test downloading game uses default ROMSet."""
        from library.views import download_game

        game = Game.objects.first()
        self.assertIsNotNone(game, "Expected at least one game in fixtures")
        self.assertTrue(game.rom_sets.exists(), "Expected game to have ROM sets")

        game.default_rom_set = game.rom_sets.first()
        game.save()

        response = download_game(None, game.pk)

        self.assertEqual(response.status_code, 200)
        self.assertTrue(hasattr(response, "file_to_stream"))

    def test_romset_download_picker_view(self):
        """Test the ROMSet download picker view."""
        from library.views import romset_download_picker
        from django.db.models import Count

        rom_set = (
            ROMSet.objects.annotate(rcount=Count("roms")).filter(rcount__gt=1).first()
        )
        if not rom_set:
            # Create a multi-rom set if none found in fixtures
            game = Game.objects.first()
            rom_set = ROMSet.objects.create(game=game, region="Test")
            ROM.objects.create(
                rom_set=rom_set,
                file_path="/tmp/test1.bin",
                file_name="test1.bin",
                file_size=1024,
            )
            ROM.objects.create(
                rom_set=rom_set,
                file_path="/tmp/test2.bin",
                file_name="test2.bin",
                file_size=1024,
            )

        response = romset_download_picker(None, rom_set.pk)

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, rom_set.game.name)
        self.assertContains(response, "Individual Files")
        self.assertContains(response, "Download All as ZIP")


class TestIsSingleRomArchive(TestCase):
    """Test the is_single_rom_archive function."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        sync_systems()

    def setUp(self):
        ROM.objects.all().delete()
        ROMSet.objects.all().delete()
        Game.objects.all().delete()
        GameImage.objects.all().delete()
        scan_directory(str(FIXTURES_PATH))

    def test_loose_rom_returns_false(self):
        """Loose ROMs are not single-ROM archives."""
        rom = ROM.objects.filter(archive_path="").first()
        self.assertIsNotNone(rom, "Expected at least one loose ROM in fixtures")

        self.assertFalse(is_single_rom_archive(rom))

    def test_single_rom_archive_returns_true(self):
        """Single-ROM archives should return True."""
        # Find an archived ROM where archive_path has only one ROM
        archived_roms = ROM.objects.exclude(archive_path="")
        for rom in archived_roms:
            count = ROM.objects.filter(archive_path=rom.archive_path).count()
            if count == 1:
                self.assertTrue(is_single_rom_archive(rom))
                return

        self.skipTest("No single-ROM archive found in fixtures")

    def test_multi_rom_archive_returns_false(self):
        """Multi-ROM archives should return False."""
        # Find an archived ROM where archive_path has multiple ROMs
        archived_roms = ROM.objects.exclude(archive_path="")
        for rom in archived_roms:
            count = ROM.objects.filter(archive_path=rom.archive_path).count()
            if count > 1:
                self.assertFalse(is_single_rom_archive(rom))
                return

        self.skipTest("No multi-ROM archive found in fixtures")


class TestGetRomFileAsStored(TestCase):
    """Test the get_rom_file_as_stored function."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        sync_systems()

    def setUp(self):
        ROM.objects.all().delete()
        ROMSet.objects.all().delete()
        Game.objects.all().delete()
        GameImage.objects.all().delete()
        scan_directory(str(FIXTURES_PATH))

    def test_loose_rom_returns_same_path(self):
        """Loose ROMs return the same path as get_rom_file."""
        rom = ROM.objects.filter(archive_path="").first()
        self.assertIsNotNone(rom, "Expected at least one loose ROM in fixtures")

        with get_rom_file_as_stored(rom) as (file_path, filename):
            self.assertEqual(file_path, rom.file_path)
            self.assertEqual(filename, rom.file_name)

    def test_single_rom_archive_returns_archive(self):
        """Single-ROM archives return the archive file itself."""
        # Find an archived ROM where archive_path has only one ROM
        archived_roms = ROM.objects.exclude(archive_path="")
        for rom in archived_roms:
            count = ROM.objects.filter(archive_path=rom.archive_path).count()
            if count == 1:
                with get_rom_file_as_stored(rom) as (file_path, filename):
                    self.assertEqual(file_path, rom.archive_path)
                    self.assertEqual(filename, Path(rom.archive_path).name)
                return

        self.skipTest("No single-ROM archive found in fixtures")


class TestDownloadViewsWithMode(TestCase):
    """Test download views with mode query parameter."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        sync_systems()

    def setUp(self):
        ROM.objects.all().delete()
        ROMSet.objects.all().delete()
        Game.objects.all().delete()
        GameImage.objects.all().delete()
        scan_directory(str(FIXTURES_PATH))

    def test_download_rom_default_mode(self):
        """Test downloading ROM with default (stored) mode."""
        from django.test import RequestFactory
        from library.views import download_rom

        # Find a single-ROM archive to test
        archived_roms = ROM.objects.exclude(archive_path="")
        rom = None
        for r in archived_roms:
            count = ROM.objects.filter(archive_path=r.archive_path).count()
            if count == 1:
                rom = r
                break

        if not rom:
            self.skipTest("No single-ROM archive found in fixtures")

        factory = RequestFactory()
        request = factory.get("/download/rom/")

        response = download_rom(request, rom.pk)

        self.assertEqual(response.status_code, 200)
        # For single-ROM archive in stored mode, should get archive filename
        self.assertIn(Path(rom.archive_path).name, response["Content-Disposition"])

    def test_download_rom_extract_mode(self):
        """Test downloading ROM with extract mode."""
        from django.test import RequestFactory
        from library.views import download_rom

        # Find a single-ROM archive to test
        archived_roms = ROM.objects.exclude(archive_path="")
        rom = None
        for r in archived_roms:
            count = ROM.objects.filter(archive_path=r.archive_path).count()
            if count == 1:
                rom = r
                break

        if not rom:
            self.skipTest("No single-ROM archive found in fixtures")

        factory = RequestFactory()
        request = factory.get("/download/rom/?mode=extract")

        response = download_rom(request, rom.pk)

        self.assertEqual(response.status_code, 200)
        # For extract mode, should get the extracted ROM filename
        expected_filename = Path(rom.path_in_archive).name
        self.assertIn(expected_filename, response["Content-Disposition"])
