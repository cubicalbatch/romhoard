"""Tests for multi-game download functionality."""

import json
import zipfile
from pathlib import Path

from django.test import Client, TestCase
from django.urls import reverse

from library.models import DownloadJob, Game, GameImage, ROM, ROMSet, System
from library.multidownload import (
    _sanitize_filename,
    create_multi_game_bundle,
    get_default_romset,
)
from library.scanner import scan_directory
from library.system_loader import sync_systems


FIXTURES_PATH = (
    Path(__file__).parent.parent.parent / "tests" / "fixtures" / "rom_library"
)


class TestMultiDownloadCore(TestCase):
    """Test core multidownload module functions."""

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

    def test_create_bundle_with_multiple_games(self):
        """Test creating ZIP with multiple games."""
        games = list(Game.objects.all()[:3])
        self.assertGreaterEqual(len(games), 1, "Need at least 1 game in fixtures")

        zip_path, filename = create_multi_game_bundle(games, "test_bundle")
        try:
            self.assertTrue(Path(zip_path).exists())
            self.assertEqual(filename, "test_bundle.zip")

            # Verify ZIP contents
            with zipfile.ZipFile(zip_path, "r") as zf:
                names = zf.namelist()
                # Each game should have a folder
                folders = set(n.split("/")[0] for n in names if "/" in n)
                self.assertGreaterEqual(len(folders), 1)
        finally:
            Path(zip_path).unlink(missing_ok=True)

    def test_create_bundle_empty_games_raises(self):
        """Test that empty games list raises ValueError."""
        with self.assertRaises(ValueError) as context:
            create_multi_game_bundle([], "empty")
        self.assertIn("No games provided", str(context.exception))

    def test_create_bundle_no_roms_raises(self):
        """Test that games with no available ROMs raises ValueError."""
        # Create a game with no ROMs
        system = System.objects.first()
        game = Game.objects.create(name="Empty Game", system=system)

        with self.assertRaises(ValueError) as context:
            create_multi_game_bundle([game], "empty")
        self.assertIn("No games have available ROMs", str(context.exception))

    def test_get_default_romset_uses_explicit_default(self):
        """Test that explicit default_rom_set is respected."""
        game = Game.objects.filter(rom_sets__roms__isnull=False).distinct().first()
        if not game or not game.rom_sets.exists():
            self.skipTest("No games with ROM sets in fixtures")

        rom_set = game.rom_sets.first()
        game.default_rom_set = rom_set
        game.save()

        result = get_default_romset(game)
        self.assertEqual(result, rom_set)

    def test_get_default_romset_falls_back_to_first(self):
        """Test that get_default_romset falls back to first available ROMSet."""
        game = Game.objects.filter(rom_sets__roms__isnull=False).distinct().first()
        if not game:
            self.skipTest("No games with ROM sets in fixtures")

        game.default_rom_set = None
        game.save()

        result = get_default_romset(game)
        self.assertIsNotNone(result)
        self.assertTrue(result.roms.exists())

    def test_sanitize_filename(self):
        """Test filename sanitization."""
        self.assertEqual(
            _sanitize_filename("Game: Special Edition"), "Game - Special Edition"
        )
        self.assertEqual(_sanitize_filename("Game/Sequel"), "Game-Sequel")
        self.assertEqual(_sanitize_filename("  Test  "), "Test")
        self.assertEqual(_sanitize_filename("Game*With?Symbols"), "GameWithSymbols")
        self.assertEqual(_sanitize_filename("...Test..."), "Test")

    def test_bundle_progress_callback(self):
        """Test that progress callback is called during bundling."""
        games = list(Game.objects.all()[:2])
        if len(games) < 1:
            self.skipTest("Need at least 1 game in fixtures")

        progress_updates = []

        def track_progress(progress):
            progress_updates.append(
                {
                    "games_processed": progress.games_processed,
                    "current_game": progress.current_game,
                }
            )

        zip_path, _ = create_multi_game_bundle(
            games, "test", progress_callback=track_progress
        )
        try:
            # Should have at least one progress update
            self.assertGreater(len(progress_updates), 0)
            # Last update should have all games processed
            self.assertEqual(progress_updates[-1]["games_processed"], len(games))
        finally:
            Path(zip_path).unlink(missing_ok=True)


class TestMultiDownloadViews(TestCase):
    """Test multi-download view functions."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        sync_systems()

    def setUp(self):
        ROM.objects.all().delete()
        ROMSet.objects.all().delete()
        Game.objects.all().delete()
        GameImage.objects.all().delete()
        DownloadJob.objects.all().delete()
        scan_directory(str(FIXTURES_PATH))

    def test_start_multi_download_creates_job(self):
        """Test that starting a download creates a job for multiple games."""
        client = Client()

        system = System.objects.filter(games__isnull=False).first()
        if not system:
            self.skipTest("No systems with games in fixtures")

        games = Game.objects.filter(system=system)[:2]
        if games.count() < 2:
            self.skipTest("Need at least 2 games for this test")

        game_ids = list(games.values_list("pk", flat=True))

        url = reverse("library:start_multi_download", kwargs={"slug": system.slug})
        response = client.post(
            url,
            data=json.dumps({"game_ids": game_ids}),
            content_type="application/json",
        )

        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertIn("job_id", data)

        job = DownloadJob.objects.get(pk=data["job_id"])
        self.assertEqual(job.status, DownloadJob.STATUS_PENDING)
        self.assertEqual(sorted(job.game_ids), sorted(game_ids))
        self.assertEqual(job.system_slug, system.slug)

    def test_start_multi_download_single_game_redirects(self):
        """Test that single game selection redirects to download_game."""
        client = Client()

        system = System.objects.filter(games__isnull=False).first()
        if not system:
            self.skipTest("No systems with games in fixtures")

        game = Game.objects.filter(system=system).first()
        game_ids = [game.pk]

        url = reverse("library:start_multi_download", kwargs={"slug": system.slug})
        response = client.post(
            url,
            data=json.dumps({"game_ids": game_ids}),
            content_type="application/json",
        )

        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertIn("redirect_url", data)
        expected_url = reverse("library:download_game", kwargs={"pk": game.pk})
        self.assertIn(expected_url, data["redirect_url"])
        # Should NOT create a DownloadJob
        self.assertEqual(DownloadJob.objects.count(), 0)

    def test_start_multi_download_invalid_json(self):
        """Test error handling for invalid JSON."""
        client = Client()

        system = System.objects.first()
        url = reverse("library:start_multi_download", kwargs={"slug": system.slug})
        response = client.post(
            url,
            data="not valid json",
            content_type="application/json",
        )

        self.assertEqual(response.status_code, 400)

    def test_start_multi_download_no_games(self):
        """Test error handling for empty game list."""
        client = Client()

        system = System.objects.first()
        url = reverse("library:start_multi_download", kwargs={"slug": system.slug})
        response = client.post(
            url,
            data=json.dumps({"game_ids": []}),
            content_type="application/json",
        )

        self.assertEqual(response.status_code, 400)

    def test_start_multi_download_invalid_games(self):
        """Test error handling for invalid game IDs."""
        client = Client()

        system = System.objects.first()
        url = reverse("library:start_multi_download", kwargs={"slug": system.slug})
        response = client.post(
            url,
            data=json.dumps({"game_ids": [999999]}),
            content_type="application/json",
        )

        self.assertEqual(response.status_code, 400)

    def test_download_status_pending(self):
        """Test download status view for pending job."""
        client = Client()

        system = System.objects.filter(games__isnull=False).first()
        job = DownloadJob.objects.create(
            task_id="test-pending",
            status=DownloadJob.STATUS_PENDING,
            game_ids=[1, 2],
            system_slug=system.slug,
        )

        response = client.get(f"/download/status/{job.pk}/")

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Preparing download")

    def test_download_status_running(self):
        """Test download status view for running job."""
        client = Client()

        system = System.objects.filter(games__isnull=False).first()
        job = DownloadJob.objects.create(
            task_id="test-running",
            status=DownloadJob.STATUS_RUNNING,
            game_ids=[1, 2],
            system_slug=system.slug,
            games_total=2,
            games_processed=1,
            current_game="Test Game",
        )

        response = client.get(f"/download/status/{job.pk}/")

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Creating bundle")
        self.assertContains(response, "1 of 2 games")
        self.assertContains(response, "Test Game")

    def test_download_status_completed(self):
        """Test download status view for completed job."""
        import tempfile

        client = Client()

        # Create temp file to simulate completed bundle
        with tempfile.NamedTemporaryFile(suffix=".zip", delete=False) as f:
            f.write(b"test content")
            temp_path = f.name

        try:
            system = System.objects.filter(games__isnull=False).first()
            job = DownloadJob.objects.create(
                task_id="test-completed",
                status=DownloadJob.STATUS_COMPLETED,
                game_ids=[1, 2],
                system_slug=system.slug,
                games_included=2,
                file_path=temp_path,
                file_name="test.zip",
                file_size=12,
            )

            response = client.get(f"/download/status/{job.pk}/")

            self.assertEqual(response.status_code, 200)
            self.assertContains(response, "Download Ready")
            self.assertContains(response, "2 games")
        finally:
            Path(temp_path).unlink(missing_ok=True)

    def test_download_status_failed(self):
        """Test download status view for failed job."""
        client = Client()

        system = System.objects.filter(games__isnull=False).first()
        job = DownloadJob.objects.create(
            task_id="test-failed",
            status=DownloadJob.STATUS_FAILED,
            game_ids=[1, 2],
            system_slug=system.slug,
            errors=["Something went wrong"],
        )

        response = client.get(f"/download/status/{job.pk}/")

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Download Failed")
        self.assertContains(response, "Something went wrong")

    def test_serve_download_bundle(self):
        """Test serving completed download bundle."""
        import tempfile

        client = Client()

        # Create temp file to simulate completed bundle
        with tempfile.NamedTemporaryFile(suffix=".zip", delete=False) as f:
            f.write(b"test zip content")
            temp_path = f.name

        try:
            system = System.objects.filter(games__isnull=False).first()
            job = DownloadJob.objects.create(
                task_id="test-serve",
                status=DownloadJob.STATUS_COMPLETED,
                game_ids=[1, 2],
                system_slug=system.slug,
                file_path=temp_path,
                file_name="test_bundle.zip",
            )

            response = client.get(f"/download/bundle/{job.pk}/")

            self.assertEqual(response.status_code, 200)
            self.assertEqual(
                response["Content-Disposition"],
                'attachment; filename="test_bundle.zip"',
            )
        finally:
            Path(temp_path).unlink(missing_ok=True)

    def test_serve_download_bundle_not_ready(self):
        """Test error when trying to download incomplete bundle."""
        client = Client()

        system = System.objects.filter(games__isnull=False).first()
        job = DownloadJob.objects.create(
            task_id="test-not-ready",
            status=DownloadJob.STATUS_RUNNING,
            game_ids=[1, 2],
            system_slug=system.slug,
        )

        response = client.get(f"/download/bundle/{job.pk}/")

        self.assertEqual(response.status_code, 400)


class TestDownloadJobModel(TestCase):
    """Test DownloadJob model properties."""

    def test_progress_percent_zero_total(self):
        """Test progress_percent when games_total is 0."""
        job = DownloadJob(games_total=0, games_processed=0)
        self.assertEqual(job.progress_percent, 0)

    def test_progress_percent_partial(self):
        """Test progress_percent with partial progress."""
        job = DownloadJob(games_total=10, games_processed=5)
        self.assertEqual(job.progress_percent, 50)

    def test_progress_percent_complete(self):
        """Test progress_percent when complete."""
        job = DownloadJob(games_total=5, games_processed=5)
        self.assertEqual(job.progress_percent, 100)

    def test_is_expired_no_expiry(self):
        """Test is_expired when expires_at is None."""
        job = DownloadJob(expires_at=None)
        self.assertFalse(job.is_expired)

    def test_is_expired_future(self):
        """Test is_expired when expires_at is in the future."""
        from datetime import timedelta

        from django.utils import timezone

        job = DownloadJob(expires_at=timezone.now() + timedelta(hours=1))
        self.assertFalse(job.is_expired)

    def test_is_expired_past(self):
        """Test is_expired when expires_at is in the past."""
        from datetime import timedelta

        from django.utils import timezone

        job = DownloadJob(expires_at=timezone.now() - timedelta(hours=1))
        self.assertTrue(job.is_expired)

    def test_str(self):
        """Test string representation."""
        job = DownloadJob(game_ids=[1, 2, 3], status=DownloadJob.STATUS_PENDING)
        self.assertEqual(str(job), "Download 3 games (pending)")


class TestCreateDownloadBundleTask(TestCase):
    """Test the background task for creating download bundles."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        sync_systems()

    def setUp(self):
        ROM.objects.all().delete()
        ROMSet.objects.all().delete()
        Game.objects.all().delete()
        GameImage.objects.all().delete()
        DownloadJob.objects.all().delete()
        scan_directory(str(FIXTURES_PATH))

    def test_task_creates_bundle(self):
        """Test that the task creates a bundle successfully."""
        from library.tasks import create_download_bundle

        system = System.objects.filter(games__isnull=False).first()
        games = Game.objects.filter(system=system)[:2]
        game_ids = list(games.values_list("pk", flat=True))

        job = DownloadJob.objects.create(
            task_id="test-task",
            status=DownloadJob.STATUS_PENDING,
            game_ids=game_ids,
            system_slug=system.slug,
        )

        # Call the task function directly
        create_download_bundle.func(job.pk)

        # Refresh from database
        job.refresh_from_db()

        try:
            self.assertEqual(job.status, DownloadJob.STATUS_COMPLETED)
            self.assertTrue(Path(job.file_path).exists())
            self.assertIsNotNone(job.expires_at)
            self.assertGreater(job.file_size, 0)
            # Filename should include timestamp: {system_slug}_yyyy-mm-dd-hh-mm.zip
            self.assertRegex(
                job.file_name,
                rf"{system.slug}_\d{{4}}-\d{{2}}-\d{{2}}-\d{{2}}-\d{{2}}\.zip",
            )
        finally:
            # Clean up
            if job.file_path and Path(job.file_path).exists():
                Path(job.file_path).unlink()

    def test_task_handles_error(self):
        """Test that the task handles errors gracefully."""
        from library.tasks import create_download_bundle

        # Create job with invalid game IDs
        job = DownloadJob.objects.create(
            task_id="test-error",
            status=DownloadJob.STATUS_PENDING,
            game_ids=[999999],  # Non-existent game
            system_slug="test",
        )

        # Call the task function directly
        try:
            create_download_bundle.func(job.pk)
        except Exception:
            pass  # Expected to raise

        # Refresh from database
        job.refresh_from_db()

        self.assertEqual(job.status, DownloadJob.STATUS_FAILED)
        self.assertGreater(len(job.errors), 0)
