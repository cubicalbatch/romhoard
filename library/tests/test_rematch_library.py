"""Unit tests for the rematch_library management command."""

from io import StringIO
from unittest.mock import MagicMock, call, patch

import pytest
import requests
from django.core.management import call_command
from django.core.management.base import CommandError
from django.utils import timezone

from library.lookup.base import LookupResult
from library.management.commands.rematch_library import Command, check_proxy_alive
from library.metadata.screenscraper import ScreenScraperClient
from library.models import Game, GameImage, ROM, ROMSet, System


@pytest.fixture
def test_setup(transactional_db, tmp_path, settings):
    """Set up test environment with a system, game, ROM, and GameImage."""
    settings.MEDIA_ROOT = str(tmp_path / "media")
    sys = System.objects.create(
        name="Channel F",
        slug="channelf",
        extensions=[".bin"],
        folder_names=["channelf"],
        screenscraper_ids=[80],
    )
    game = Game.objects.create(
        name="Alien Invasion",
        system=sys,
        screenscraper_id=46519,
        metadata_updated_at=timezone.now(),
        name_source=Game.SOURCE_SCREENSCRAPER,
    )
    rs = ROMSet.objects.create(game=game)
    rom = ROM.objects.create(
        rom_set=rs,
        file_name="Alien Invasion.bin",
        file_path="/roms/channelf/Alien Invasion.bin",
        file_size=2048,
        crc32="12345678",
    )
    img = GameImage.objects.create(
        game=game,
        file_path="/media/metadata/channelf/Alien Invasion/box-2D.png",
        file_name="box-2D.png",
        image_type="cover",
        source="downloaded",
    )
    return {"system": sys, "game": game, "rom": rom, "image": img}


def test_check_proxy_alive_success():
    """Verify check_proxy_alive returns True on 200/400/404."""
    with patch("requests.get") as mock_get:
        mock_get.return_value.status_code = 400
        assert check_proxy_alive("http://127.0.0.1:8765/api2/") is True


def test_check_proxy_alive_failure():
    """Verify check_proxy_alive returns False on network failure."""
    import requests

    with patch("requests.get", side_effect=requests.RequestException):
        assert check_proxy_alive("http://127.0.0.1:8765/api2/") is False


def test_rematch_aborts_if_proxy_down(test_setup):
    """Verify command raises CommandError if proxy is not reachable."""
    with patch("library.management.commands.rematch_library.check_proxy_alive", return_value=False):
        with pytest.raises(CommandError, match="ScreenScraper caching proxy is NOT reachable"):
            call_command("rematch_library")


def test_rematch_baseline_only(test_setup):
    """Verify --baseline-only displays stats and does not modify the database."""
    out = StringIO()
    with patch("library.management.commands.rematch_library.check_proxy_alive", return_value=True), \
         patch("library.management.commands.rematch_library.screenscraper_available", return_value=True):
        call_command("rematch_library", "--baseline-only", stdout=out)

    output = out.getvalue()
    assert "BASELINE MATCHING RATES" in output
    assert "channelf" in output
    assert "Alien Invasion" in Game.objects.get(pk=test_setup["game"].pk).name
    assert Game.objects.get(pk=test_setup["game"].pk).screenscraper_id == 46519


def test_rematch_dry_run(test_setup):
    """Verify --dry-run evaluates games without modifying DB state or images."""
    out = StringIO()
    fake_result = MagicMock(
        screenscraper_id=46519,
        name="Alien Invasion",
        match_type="name",
        matched_system_id=80,
    )
    with patch("library.management.commands.rematch_library.check_proxy_alive", return_value=True), \
         patch("library.management.commands.rematch_library.screenscraper_available", return_value=True), \
         patch("library.management.commands.rematch_library._identify_game", return_value=fake_result):
        call_command("rematch_library", "--system", "channelf", "--dry-run", stdout=out)

    output = out.getvalue()
    assert "[DRY RUN]" in output
    assert "DRY RUN EVALUATION REPORT" in output
    assert GameImage.objects.count() == 1


def test_rematch_unmatches_and_rematches(test_setup, tmp_path):
    """Verify rematch unmatches, preserves images, and rematches games."""
    out = StringIO()
    report_file = tmp_path / "report.md"

    fake_result = MagicMock(
        screenscraper_id=46519,
        name="Alien Invasion",
        match_type="name",
        matched_system_id=80,
        raw_name="Videocart-26 - Alien Invasion",
    )
    fake_info = {
        "id": "46519",
        "name": "Alien Invasion",
        "description": "Alien battle",
    }

    with patch("library.management.commands.rematch_library.check_proxy_alive", return_value=True), \
         patch("library.management.commands.rematch_library.screenscraper_available", return_value=True), \
         patch("library.management.commands.rematch_library._identify_game", return_value=fake_result), \
         patch.object(ScreenScraperClient, "get_game_info", return_value=fake_info):
        call_command("rematch_library", "--system", "channelf", "--report", str(report_file), stdout=out)

    game = Game.objects.get(pk=test_setup["game"].pk)
    assert game.screenscraper_id == 46519
    assert game.metadata_updated_at is not None
    assert game.metadata_match_failed is False
    # Image must be 100% preserved
    assert GameImage.objects.filter(game=game).count() == 1
    assert report_file.exists()
    report_text = report_file.read_text()
    assert "Library Rematch Report" in report_text
    assert "channelf" in report_text


def _mock_result(name: str, ss_id: str, system_id: int = 4):
    return LookupResult(
        name=name,
        region="",
        revision="",
        tags=[],
        source="screenscraper",
        confidence=0.9,
        raw_name=name,
        screenscraper_id=ss_id,
        matched_system_id=system_id,
        match_type="name",
    )


@pytest.mark.django_db
class TestRematchPendingOnly:
    """Tests for the --pending-only argument in rematch_library."""

    def test_pending_only_processes_only_unmatched_unfailed_games(self, test_setup):
        sys = test_setup["system"]
        # Game 1: already matched (test_setup["game"] has screenscraper_id=46519)
        g_matched = test_setup["game"]
        # Game 2: failed match
        g_failed = Game.objects.create(
            name="Unknown Game",
            system=sys,
            metadata_match_failed=True,
        )
        # Game 3: pending (screenscraper_id null, metadata_match_failed false)
        g_pending = Game.objects.create(
            name="Pending Game",
            system=sys,
            screenscraper_id=None,
            metadata_match_failed=False,
        )

        cmd = Command()
        with (
            patch("library.management.commands.rematch_library.check_proxy_alive", return_value=True),
            patch("library.management.commands.rematch_library.screenscraper_available", return_value=True),
            patch.object(cmd, "_unmatch_games") as mock_unmatch,
            patch.object(cmd, "_run_matching_pass") as mock_run_matching,
        ):
            mock_run_matching.return_value = {
                "processed": 1,
                "matched": 1,
                "failed": 0,
                "merged": 0,
                "match_types": {},
            }
            call_command(cmd, "--pending-only", "--system=channelf")

            # _unmatch_games must NOT be called in pending-only mode
            mock_unmatch.assert_not_called()

            # _run_matching_pass must receive only the pending game
            assert mock_run_matching.called
            qs_passed = mock_run_matching.call_args[0][0]
            pks = list(qs_passed.values_list("pk", flat=True))
            assert pks == [g_pending.pk]
            assert g_matched.pk not in pks
            assert g_failed.pk not in pks


@pytest.mark.django_db(transaction=True)
class TestRematchNetworkRetriesAndResilience:
    """Tests for network retries with backoff and ThreadPoolExecutor exception handling."""

    def test_transient_network_error_retries_and_succeeds(self, test_setup):
        sys = test_setup["system"]
        game = Game.objects.create(
            name="Pending Game",
            system=sys,
            screenscraper_id=None,
            metadata_match_failed=False,
        )

        cmd = Command()
        call_count = 0

        def flaky_identify(game, client):
            nonlocal call_count
            call_count += 1
            if call_count < 3:
                raise requests.exceptions.ConnectionError("Connection timed out")
            return _mock_result(game.name, "9999", system_id=80)

        with (
            patch("library.management.commands.rematch_library._identify_game", side_effect=flaky_identify),
            patch("library.management.commands.rematch_library.time.sleep") as mock_sleep,
            patch("library.management.commands.rematch_library.ScreenScraperClient") as mock_client_cls,
        ):
            mock_client = mock_client_cls.return_value
            mock_client.get_game_info.return_value = None

            qs = Game.objects.filter(pk=game.pk)
            stats = cmd._run_matching_pass(qs, workers=1, dry_run=False)

            assert call_count == 3
            assert mock_sleep.call_count == 2
            # Exponential backoff checks: 2^0 = 1s, 2^1 = 2s
            assert mock_sleep.call_args_list == [call(1), call(2)]
            assert stats["matched"] == 1
            game.refresh_from_db()
            assert game.screenscraper_id == 9999

    def test_exhausted_network_retries_raises_and_is_caught_by_executor(self, test_setup):
        sys = test_setup["system"]
        g1 = Game.objects.create(name="Game 1", system=sys)
        g2 = Game.objects.create(name="Game 2", system=sys)

        cmd = Command()

        def identify_side_effect(game, client):
            if game.name == "Game 1":
                raise requests.exceptions.RequestException("ScreenScraper API down")
            return _mock_result("Game 2", "5555", system_id=80)

        with (
            patch("library.management.commands.rematch_library._identify_game", side_effect=identify_side_effect),
            patch("library.management.commands.rematch_library.time.sleep"),
            patch("library.management.commands.rematch_library.logger.exception") as mock_log_exc,
            patch("library.management.commands.rematch_library.ScreenScraperClient") as mock_client_cls,
        ):
            mock_client = mock_client_cls.return_value
            mock_client.get_game_info.return_value = None

            qs = Game.objects.filter(pk__in=[g1.pk, g2.pk]).order_by("pk")
            # ThreadPoolExecutor should NOT crash despite Game 1 failing
            stats = cmd._run_matching_pass(qs, workers=2, dry_run=False)

            # Game 2 was processed and matched
            assert stats["matched"] == 1
            g2.refresh_from_db()
            assert g2.screenscraper_id == 5555

            # Game 1 raised after 3 retries and was caught in as_completed(futures)
            assert mock_log_exc.called
            assert any("Unexpected error processing game" in str(arg) for call_item in mock_log_exc.call_args_list for arg in call_item[0])

    def test_unexpected_exception_in_one_game_does_not_crash_executor(self, test_setup):
        sys = test_setup["system"]
        g1 = Game.objects.create(name="Game 1", system=sys)
        g2 = Game.objects.create(name="Game 2", system=sys)

        cmd = Command()

        def identify_side_effect(game, client):
            if game.name == "Game 1":
                raise RuntimeError("Unexpected boom!")
            return _mock_result("Game 2", "7777", system_id=80)

        with (
            patch("library.management.commands.rematch_library._identify_game", side_effect=identify_side_effect),
            patch("library.management.commands.rematch_library.logger.exception") as mock_log_exc,
            patch("library.management.commands.rematch_library.ScreenScraperClient") as mock_client_cls,
        ):
            mock_client = mock_client_cls.return_value
            mock_client.get_game_info.return_value = None

            qs = Game.objects.filter(pk__in=[g1.pk, g2.pk]).order_by("pk")
            # Executor should log error for Game 1 and allow Game 2 to finish
            stats = cmd._run_matching_pass(qs, workers=2, dry_run=False)

            assert stats["matched"] == 1
            g2.refresh_from_db()
            assert g2.screenscraper_id == 7777
            assert mock_log_exc.called


@pytest.mark.django_db(transaction=True)
class TestRematchIdentityFromRomFilename:
    """Rematch must identify games by the ROM filename, not the DB game name."""

    def test_poisoned_game_name_cannot_beat_rom_stem_identity(self, tmp_path, settings):
        """A junk DB name must not steer (or win) the rematch name search."""
        settings.MEDIA_ROOT = str(tmp_path / "media")
        system = System.objects.create(
            name="Super Nintendo",
            slug="snes-poison",
            extensions=[".sfc"],
            folder_names=["snes-poison"],
            screenscraper_ids=[12],
        )
        game = Game.objects.create(name="091 Some Junk Suffix", system=system)
        rom_set = ROMSet.objects.create(game=game)
        ROM.objects.create(
            rom_set=rom_set,
            file_name="Wonder Project J (Japan).sfc",
            file_path="/roms/snes/Wonder Project J (Japan).sfc",
            file_size=1024,
            crc32="dddddddd",
        )

        queries = []
        ss_client = MagicMock()
        ss_client.has_credentials.return_value = True

        def search_side_effect(name, system_id=None):
            queries.append(name)
            if name == "091 Some Junk Suffix":
                # Junk entry whose name exactly matches the poisoned DB name.
                return [
                    {
                        "id": 99999,
                        "name": "091 Some Junk Suffix",
                        "all_names": [],
                        "system_id": 12,
                    }
                ]
            if name == "Wonder Project J":
                return [
                    {
                        "id": 31234,
                        "name": "Wonder Project J",
                        "all_names": [],
                        "system_id": 12,
                    }
                ]
            return []

        ss_client.search_game.side_effect = search_side_effect
        ss_client.search_by_crc.return_value = None
        ss_client.search_by_romnom.return_value = None

        def get_game_info_side_effect(game_id, *args, **kwargs):
            return {"id": str(game_id), "name": f"Game {game_id}", "description": ""}

        with (
            patch(
                "library.management.commands.rematch_library.check_proxy_alive",
                return_value=True,
            ),
            patch(
                "library.management.commands.rematch_library.screenscraper_available",
                return_value=True,
            ),
            patch(
                "library.lookup.screenscraper.ScreenScraperLookupService._get_client",
                return_value=ss_client,
            ),
            patch.object(
                ScreenScraperClient, "get_game_info", side_effect=get_game_info_side_effect
            ),
        ):
            call_command("rematch_library", "--system", "snes-poison")

        game.refresh_from_db()
        assert game.screenscraper_id == 31234
        # The name search used the parsed ROM filename, never the poisoned name.
        assert "Wonder Project J" in queries
        assert "091 Some Junk Suffix" not in queries
