"""ScreenScraper quota and throttling: pause reasons, usage, automatic resume."""

import os
from datetime import datetime, timedelta
from unittest.mock import MagicMock, Mock, patch
from zoneinfo import ZoneInfo

import pytest
from django.utils import timezone
from requests import Response

from library.metadata import screenscraper as ss
from library.metadata.screenscraper import (
    ScreenScraperClient,
    ScreenScraperRateLimited,
    clear_pause,
    get_pause,
    get_pause_until,
    get_quota_usage,
    next_quota_reset,
    record_quota_usage,
    set_pause,
)
from library.models import Game, MetadataJob, ROM, ROMSet, Setting, System

PARIS = ZoneInfo("Europe/Paris")
CREDENTIALS = {
    "SCREENSCRAPER_USER": "testuser",
    "SCREENSCRAPER_PASSWORD": "testpass",
    "SCREENSCRAPER_DEVID": "testdevid",
    "SCREENSCRAPER_DEVPASSWORD": "testdevpass",
}


def _ssuser(**overrides) -> dict:
    """ScreenScraper `ssuser` block as returned with every API response."""
    data = {
        "requeststoday": "30",
        "maxrequestsperday": "100000",
        "requestskotoday": "5",
        "maxrequestskoperday": "10000",
    }
    data.update(overrides)
    return data


@pytest.fixture(autouse=True)
def _reset_quota_state():
    ss._quota_written_at = 0.0
    yield
    ss._quota_written_at = 0.0


class TestNextQuotaReset:
    def test_reset_is_next_paris_midnight_plus_grace(self):
        now = datetime(2026, 10, 6, 18, 24, tzinfo=PARIS)

        reset = next_quota_reset(now)

        assert reset == datetime(2026, 10, 7, 0, 5, tzinfo=PARIS)

    def test_just_after_midnight_waits_for_the_following_one(self):
        now = datetime(2026, 10, 7, 0, 1, tzinfo=PARIS)

        assert next_quota_reset(now) == datetime(2026, 10, 8, 0, 5, tzinfo=PARIS)

    def test_handles_daylight_saving_change(self):
        # Clocks go back on 2026-10-25 in Paris; midnight stays midnight.
        now = datetime(2026, 10, 24, 23, 0, tzinfo=PARIS)

        reset = next_quota_reset(now)

        assert reset.astimezone(PARIS).hour == 0
        assert reset.astimezone(PARIS).day == 25


@pytest.mark.django_db
class TestPauseState:
    def test_no_pause_by_default(self):
        assert get_pause() is None
        assert get_pause_until() is None

    @pytest.mark.parametrize("reason", ["daily_quota", "failed_quota"])
    def test_quota_pauses_last_until_reset(self, reason):
        until = set_pause(reason)

        pause = get_pause()
        assert pause.reason == reason
        assert pause.until == until
        assert pause.resumes_at_reset
        assert abs((until - next_quota_reset()).total_seconds()) < 5

    def test_rate_limit_pause_is_short(self):
        until = set_pause("rate_limit")

        assert until - timezone.now() <= timedelta(minutes=1)
        assert not get_pause().resumes_at_reset

    def test_unavailable_pause_is_medium(self):
        until = set_pause("unavailable")

        remaining = until - timezone.now()
        assert timedelta(minutes=5) < remaining <= timedelta(minutes=15)

    def test_expired_pause_is_ignored(self):
        Setting.objects.create(
            key=ss.PAUSE_SETTING_KEY,
            value={
                "until": (timezone.now() - timedelta(minutes=1)).isoformat(),
                "reason": "daily_quota",
            },
        )

        assert get_pause() is None

    def test_clear_pause(self):
        set_pause("daily_quota")

        clear_pause()

        assert get_pause() is None


@pytest.mark.django_db
@patch.dict(os.environ, CREDENTIALS)
@patch("library.metadata.screenscraper.requests.get")
class TestThrottleResponses:
    def _respond(self, mock_get, status):
        response = Mock(spec=Response)
        response.status_code = status
        mock_get.return_value = response

    @pytest.mark.parametrize(
        "status,reason",
        [
            (429, "rate_limit"),
            (430, "daily_quota"),
            (431, "failed_quota"),
            (401, "unavailable"),
            (423, "unavailable"),
        ],
    )
    def test_status_sets_matching_pause(self, mock_get, status, reason):
        self._respond(mock_get, status)

        with pytest.raises(ScreenScraperRateLimited) as exc_info:
            ScreenScraperClient()._make_request("jeuRecherche", {"recherche": "x"})

        pause = get_pause()
        assert pause.reason == reason
        assert exc_info.value.retry_after == pause.until

    def test_paused_client_makes_no_request(self, mock_get):
        set_pause("daily_quota")

        with pytest.raises(ScreenScraperRateLimited):
            ScreenScraperClient()._make_request("jeuRecherche", {"recherche": "x"})

        mock_get.assert_not_called()

    def test_successful_response_records_quota_usage(self, mock_get):
        response = Mock(spec=Response)
        response.status_code = 200
        response.json.return_value = {"response": {"ssuser": _ssuser()}}
        mock_get.return_value = response

        ScreenScraperClient()._make_request("jeuRecherche", {"recherche": "x"})

        usage = get_quota_usage()
        assert usage["requests_today"] == 30
        assert usage["failed_today"] == 5


@pytest.mark.django_db
class TestQuotaUsage:
    def test_exhausted_daily_quota_pauses_before_the_next_request(self):
        record_quota_usage(_ssuser(requeststoday="100000"))

        assert get_pause().reason == "daily_quota"

    def test_exhausted_failed_lookup_quota_pauses(self):
        record_quota_usage(_ssuser(requestskotoday="10000"))

        assert get_pause().reason == "failed_quota"

    def test_usage_writes_are_throttled(self):
        record_quota_usage(_ssuser(requeststoday="10"))
        record_quota_usage(_ssuser(requeststoday="11"))

        assert get_quota_usage()["requests_today"] == 10

    def test_usage_from_before_the_last_reset_is_hidden(self):
        record_quota_usage(_ssuser())
        stale = Setting.objects.get(key=ss.QUOTA_SETTING_KEY)
        stale.value["updated_at"] = (timezone.now() - timedelta(days=2)).isoformat()
        stale.save()

        assert get_quota_usage() is None

    def test_malformed_usage_is_ignored(self):
        record_quota_usage({"requeststoday": "n/a"})

        assert get_quota_usage() is None
        assert get_pause() is None


@pytest.mark.django_db
class TestPauseBanner:
    def test_banner_shows_on_every_page_while_paused(self, client):
        set_pause("daily_quota")

        html = client.get("/").content.decode()

        assert "ScreenScraper daily quota reached" in html
        assert "continue automatically" in html

    def test_failed_lookup_quota_banner(self, client):
        set_pause("failed_quota")

        html = client.get("/").content.decode()

        assert "unmatched lookups" in html

    def test_no_banner_when_not_paused(self, client):
        html = client.get("/").content.decode()

        assert "continue automatically" not in html

    @patch.dict(os.environ, CREDENTIALS)
    def test_metadata_page_shows_quota_usage(self, client):
        record_quota_usage(_ssuser(requeststoday="1234", requestskotoday="56"))

        html = client.get("/settings/").content.decode()

        assert "1,234" in html
        assert "100,000" in html
        assert "56" in html


def _game_with_rom(name: str = "Sonic") -> Game:
    system, _ = System.objects.get_or_create(
        slug="quota-test",
        defaults={
            "name": "Quota Test",
            "extensions": [".bin"],
            "folder_names": [],
            "screenscraper_ids": [1],
        },
    )
    game = Game.objects.create(name=name, system=system)
    ROM.objects.create(
        rom_set=ROMSet.objects.create(game=game),
        file_path=f"/roms/{name}.bin",
        file_name=f"{name}.bin",
        file_size=1,
    )
    return game


@pytest.mark.django_db
class TestIdentificationDuringPause:
    def test_identify_rom_reschedules_instead_of_failing(self):
        from library import tasks

        game = _game_with_rom()
        rom = ROM.objects.get(rom_set__game=game)
        until = set_pause("daily_quota")
        context = MagicMock()
        context.should_abort.return_value = False

        with (
            patch(
                "library.lookup.lookup_rom",
                side_effect=ScreenScraperRateLimited(until),
            ),
            patch.object(tasks.identify_rom, "configure") as configure,
        ):
            result = tasks.identify_rom.func(context, rom.pk)

        assert result["status"] == "rescheduled"
        configure.assert_called_once()
        assert configure.call_args.kwargs["schedule_at"] == until


@pytest.mark.django_db
class TestResumeStrandedMetadata:
    def _fail_job(self, game, n=1):
        for i in range(n):
            MetadataJob.objects.create(
                task_id=f"{game.pk}-{i}",
                game=game,
                status=MetadataJob.STATUS_FAILED,
                error="Connection reset",
            )

    def _sweep(self):
        from library import tasks

        with (
            patch("library.metadata.screenscraper.screenscraper_available", return_value=True),
            patch.object(tasks.run_metadata_job_for_game, "configure") as configure,
        ):
            configure.return_value.defer.return_value = 1
            result = tasks.resume_stranded_metadata.func(0)
        return result, configure

    def test_requeues_game_whose_last_job_failed(self):
        game = _game_with_rom()
        self._fail_job(game)

        result, configure = self._sweep()

        assert result["queued"] == 1
        assert MetadataJob.objects.filter(
            game=game, status=MetadataJob.STATUS_PENDING
        ).exists()

    def test_waits_while_paused(self):
        game = _game_with_rom()
        self._fail_job(game)
        set_pause("daily_quota")

        result, configure = self._sweep()

        assert result["queued"] == 0
        configure.assert_not_called()

    def test_gives_up_after_repeated_failures(self):
        game = _game_with_rom()
        self._fail_job(game, n=3)

        result, _ = self._sweep()

        assert result["queued"] == 0

    def test_respects_cancelled_jobs(self):
        game = _game_with_rom()
        self._fail_job(game)
        MetadataJob.objects.create(
            task_id="cancelled", game=game, status=MetadataJob.STATUS_CANCELLED
        )

        result, _ = self._sweep()

        assert result["queued"] == 0

    def test_skips_games_with_pending_job(self):
        game = _game_with_rom()
        self._fail_job(game)
        MetadataJob.objects.create(
            task_id="pending", game=game, status=MetadataJob.STATUS_PENDING
        )

        result, _ = self._sweep()

        assert result["queued"] == 0

    def test_skips_resolved_games(self):
        game = _game_with_rom()
        self._fail_job(game)
        Game.objects.filter(pk=game.pk).update(metadata_match_failed=True)

        result, _ = self._sweep()

        assert result["queued"] == 0


@pytest.mark.django_db
def test_legacy_pause_setting_is_removed_by_migration():
    from importlib import import_module

    from django.apps import apps

    Setting.objects.create(
        key="screenscraper_pause_until", value=timezone.now().isoformat()
    )
    migration = import_module("library.migrations.0005_remove_legacy_screenscraper_pause")

    migration.remove_legacy_pause(apps, None)

    assert not Setting.objects.filter(key="screenscraper_pause_until").exists()
    assert get_pause() is None
