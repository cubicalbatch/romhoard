"""Hasheous naming after scans: outage handling, identify_game, follow-up routing."""

import json
import threading
import time
from types import SimpleNamespace
from unittest.mock import Mock, patch

import pytest
import requests
from django.db import connection

from library.lookup import hasheous
from library.lookup.hasheous import HasheousLookupService, hasheous_available
from library.models import Game, HasheousCache, MetadataJob, ROM, ROMSet, System
from library.tasks import identify_game, queue_game_followup, run_hash_lookup

CONTEXT = SimpleNamespace(should_abort=lambda: False)


def _hasheous_body(name="Super Mario Bros.", source="NoIntros", platform=None):
    return {
        "name": name,
        "platform": {"name": platform or "Nintendo Entertainment System"},
        "signature": {
            "rom": {"name": f"{name} (World).nes", "signatureSource": source}
        },
    }


def _response(status=200, body=None):
    response = Mock()
    response.status_code = status
    response.text = ""
    if isinstance(body, Exception):
        response.json.side_effect = body
    else:
        response.json.return_value = body
    return response


@pytest.fixture(autouse=True)
def _hasheous_state(monkeypatch):
    """Fresh breaker per test and no politeness sleeps."""
    monkeypatch.setattr(hasheous, "_unavailable_until", 0.0)
    monkeypatch.setattr(hasheous, "REQUEST_DELAY", 0)
    monkeypatch.delenv("HASHEOUS_API_BASE", raising=False)


@pytest.fixture
def post():
    with patch("library.lookup.hasheous.requests.post") as mock_post:
        yield mock_post


@pytest.fixture
def nes(db):
    system, _ = System.objects.get_or_create(
        slug="nes",
        defaults={
            "name": "Nintendo Entertainment System",
            "extensions": [".nes"],
            "exclusive_extensions": [".nes"],
            "folder_names": ["NES"],
            "screenscraper_ids": [3],
        },
    )
    return system


def _game(system, name="Super Mario Bros (hack)", crcs=("aaaa0001",), **fields):
    game = Game.objects.create(name=name, system=system, **fields)
    rom_set = ROMSet.objects.create(game=game)
    for i, crc in enumerate(crcs):
        ROM.objects.create(
            rom_set=rom_set,
            file_path=f"/roms/{name}-{i}.nes",
            file_name=f"{name}-{i}.nes",
            file_size=1,
            crc32=crc,
        )
    return game


@pytest.mark.django_db
class TestOutageBreaker:
    @pytest.mark.parametrize(
        "failure",
        [
            requests.exceptions.ConnectTimeout(),
            requests.exceptions.ReadTimeout(),
            requests.exceptions.ConnectionError(),
        ],
    )
    def test_network_failure_pauses_hasheous(self, post, failure):
        post.side_effect = failure

        assert HasheousLookupService()._api_lookup(crc="aaaa0001") is None
        assert not hasheous_available()
        assert HasheousLookupService()._api_lookup(crc="aaaa0002") is None
        assert post.call_count == 1

    @pytest.mark.parametrize("status", [429, 500, 502, 503])
    def test_server_errors_pause_hasheous(self, post, status):
        post.return_value = _response(status)

        assert HasheousLookupService()._api_lookup(crc="aaaa0001") is None
        assert not hasheous_available()
        assert not HasheousCache.objects.exists()

    def test_invalid_json_pauses_hasheous(self, post):
        post.return_value = _response(200, ValueError("not json"))

        assert HasheousLookupService()._api_lookup(crc="aaaa0001") is None
        assert not hasheous_available()

    def test_not_found_is_cached_and_keeps_hasheous_available(self, post):
        post.return_value = _response(404)

        assert HasheousLookupService()._api_lookup(crc="aaaa0001") is None
        assert hasheous_available()
        assert HasheousCache.objects.get(hash_value="aaaa0001").matched is False

    def test_client_error_is_neither_cached_nor_an_outage(self, post):
        post.return_value = _response(400)

        assert HasheousLookupService()._api_lookup(crc="aaaa0001") is None
        assert hasheous_available()
        assert not HasheousCache.objects.exists()

    def test_non_object_payload_is_ignored(self, post):
        post.return_value = _response(200, ["unexpected"])

        assert HasheousLookupService()._api_lookup(crc="aaaa0001") is None
        assert not HasheousCache.objects.exists()

    def test_calls_resume_after_cooldown(self, post, monkeypatch):
        post.side_effect = requests.exceptions.ConnectionError()
        HasheousLookupService()._api_lookup(crc="aaaa0001")
        monkeypatch.setattr(hasheous, "_unavailable_until", 0.0)
        post.side_effect = None
        post.return_value = _response(404)

        HasheousLookupService()._api_lookup(crc="aaaa0002")

        assert post.call_count == 2

    def test_cached_answers_still_work_during_outage(self, post, nes):
        post.return_value = _response(200, _hasheous_body())
        HasheousLookupService().lookup(system=nes, crc32="aaaa0001")
        post.reset_mock()
        post.side_effect = requests.exceptions.ConnectionError()
        hasheous._mark_unavailable("test")

        result = HasheousLookupService().lookup(system=nes, crc32="aaaa0001")

        assert result.name == "Super Mario Bros."
        post.assert_not_called()

    def test_api_base_is_configurable(self, post, monkeypatch):
        monkeypatch.setenv("HASHEOUS_API_BASE", "http://127.0.0.1:8766/api/v1/")
        post.return_value = _response(404)

        HasheousLookupService()._api_lookup(crc="aaaa0001")

        assert post.call_args.args[0] == "http://127.0.0.1:8766/api/v1/Lookup/ByHash"

    def test_default_api_base(self, post):
        post.return_value = _response(404)

        HasheousLookupService()._api_lookup(crc="aaaa0001")

        assert post.call_args.args[0] == "https://hasheous.org/api/v1/Lookup/ByHash"

    def test_lookup_rom_falls_through_to_next_service_when_down(self, post, nes):
        from library.lookup import LookupResult, lookup_rom

        post.side_effect = requests.exceptions.ConnectionError()
        fallback = Mock()
        fallback.name = "screenscraper"
        fallback.is_available.return_value = True
        fallback.lookup.return_value = LookupResult(
            name="Mario",
            region="",
            revision="",
            tags=[],
            source="screenscraper",
            confidence=1.0,
            raw_name="Mario",
            screenscraper_id=7,
        )

        result = lookup_rom(
            nes, crc32="aaaa0001", services=[HasheousLookupService(), fallback]
        )

        assert result.screenscraper_id == 7

    def test_upload_system_detection_survives_outage(self, post):
        from library.upload import identify_system_by_hash

        post.side_effect = requests.exceptions.ReadTimeout()

        assert identify_system_by_hash(crc32="aaaa0001", sha1="b" * 40) is None
        assert post.call_count == 1


class TestRateLimiter:
    """Concurrent lookups claim spaced start slots instead of posting together."""

    def test_concurrent_calls_are_spaced(self, monkeypatch):
        delay = 0.15
        monkeypatch.setattr(hasheous, "REQUEST_DELAY", delay)
        monkeypatch.setattr(hasheous, "_last_request_time", 0.0)

        starts: list[float] = []
        barrier = threading.Barrier(4)

        def call():
            barrier.wait()
            hasheous._rate_limit()
            starts.append(time.monotonic())

        threads = [threading.Thread(target=call) for _ in range(4)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert len(starts) == 4
        starts.sort()
        # A caller holds the lock until its slot: consecutive returns are
        # REQUEST_DELAY apart, so no two requests can post simultaneously
        # and a burst cannot trip the 429 outage cooldown.
        for earlier, later in zip(starts, starts[1:]):
            assert later - earlier >= delay - 0.05
        assert starts[-1] - starts[0] >= 3 * delay - 0.05


@pytest.mark.django_db
class TestIdentifyGame:
    def _run(self, game, **kwargs):
        with (
            patch("library.tasks.queue_game_metadata") as queue,
            patch.object(identify_game, "configure") as configure,
        ):
            result = identify_game.func(CONTEXT, game.pk, **kwargs)
        self.retry = configure
        # Metadata is queued by the scan, never by identification
        queue.assert_not_called()
        return result

    def test_renames_from_hasheous(self, post, nes):
        post.return_value = _response(200, _hasheous_body())
        game = _game(nes)

        result = self._run(game)

        game.refresh_from_db()
        assert game.name == "Super Mario Bros."
        assert game.name_source == "NoIntros"
        assert result["status"] == "identified"

    def test_unknown_database_source_is_stored_as_hasheous(self, post, nes):
        post.return_value = _response(200, _hasheous_body(source="TOSEC-and-friends"))
        game = _game(nes)

        self._run(game)

        game.refresh_from_db()
        assert game.name_source == Game.SOURCE_HASHEOUS

    @pytest.mark.parametrize(
        "source", [Game.SOURCE_SCREENSCRAPER, Game.SOURCE_MANUAL, "NoIntros"]
    )
    def test_leaves_non_filename_names_alone(self, post, nes, source):
        game = _game(nes, name_source=source)

        result = self._run(game)

        post.assert_not_called()
        assert result["status"] == "skipped"

    def test_name_set_by_screenscraper_meanwhile_is_kept(self, post, nes):
        """Metadata runs in parallel: an exact match may rename first."""
        post.return_value = _response(200, _hasheous_body())
        game = _game(nes)
        original_lookup = HasheousLookupService.lookup

        def screenscraper_renames_during_lookup(service, **kwargs):
            Game.objects.filter(pk=game.pk).update(
                name="Super Mario Bros", name_source=Game.SOURCE_SCREENSCRAPER
            )
            return original_lookup(service, **kwargs)

        with patch.object(
            HasheousLookupService,
            "lookup",
            autospec=True,
            side_effect=screenscraper_renames_during_lookup,
        ):
            result = self._run(game)

        game.refresh_from_db()
        assert game.name == "Super Mario Bros"
        assert game.name_source == Game.SOURCE_SCREENSCRAPER
        assert result["status"] == "skipped"

    def test_outage_keeps_name_and_schedules_retry_after_cooldown(self, post, nes):
        post.side_effect = requests.exceptions.ReadTimeout()
        game = _game(nes, crcs=("aaaa0001", "aaaa0002", "aaaa0003"))

        result = self._run(game)

        game.refresh_from_db()
        assert game.name == "Super Mario Bros (hack)"
        assert result["status"] == "retry_scheduled"
        assert post.call_count == 1
        assert self.retry.call_args.kwargs["schedule_in"] == {
            "seconds": hasheous.OUTAGE_COOLDOWN
        }
        self.retry.return_value.defer.assert_called_once_with(
            game_id=game.pk, attempt=1
        )

    def test_retries_back_off_and_stop(self, post, nes):
        from library.tasks import MAX_HASHEOUS_RETRIES

        hasheous._mark_unavailable("test")
        game = _game(nes)

        self._run(game, attempt=2)
        assert self.retry.call_args.kwargs["schedule_in"] == {
            "seconds": hasheous.OUTAGE_COOLDOWN * 4
        }

        result = self._run(game, attempt=MAX_HASHEOUS_RETRIES)
        assert result["status"] == "hasheous_unavailable"
        self.retry.assert_not_called()

    def test_retry_after_outage_names_the_game(self, post, nes):
        post.return_value = _response(200, _hasheous_body())
        game = _game(nes)

        result = self._run(game, attempt=1)

        game.refresh_from_db()
        assert result["status"] == "identified"
        assert game.name == "Super Mario Bros."
        self.retry.assert_not_called()

    def test_paused_hasheous_makes_no_request(self, post, nes):
        hasheous._mark_unavailable("test")
        game = _game(nes)

        result = self._run(game)

        post.assert_not_called()
        assert result["status"] == "retry_scheduled"

    def test_unexpected_error_is_contained(self, nes):
        game = _game(nes)

        with patch.object(
            HasheousLookupService, "lookup", side_effect=RuntimeError("boom")
        ):
            result = self._run(game)

        assert result["status"] == "error"
        self.retry.assert_not_called()

    def test_no_match_tries_next_roms_up_to_the_cap(self, post, nes):
        post.return_value = _response(404)
        game = _game(nes, crcs=[f"aaaa000{i}" for i in range(8)])

        result = self._run(game)

        assert result["status"] == "no_match"
        assert post.call_count == 5

    def test_second_rom_can_identify(self, post, nes):
        post.side_effect = [_response(404), _response(200, _hasheous_body())]
        game = _game(nes, crcs=("aaaa0001", "aaaa0002"))

        result = self._run(game)

        assert result["status"] == "identified"

    def test_platform_mismatch_does_not_rename(self, post, nes):
        post.return_value = _response(200, _hasheous_body(platform="Nintendo Game Boy"))
        game = _game(nes)

        result = self._run(game)

        game.refresh_from_db()
        assert game.name == "Super Mario Bros (hack)"
        assert result["status"] == "no_match"

    def test_merges_into_existing_game_with_canonical_name(self, post, nes):
        post.return_value = _response(200, _hasheous_body())
        canonical = _game(nes, name="Super Mario Bros.", crcs=("bbbb0001",))
        duplicate = _game(nes)

        result = self._run(duplicate)

        assert not Game.objects.filter(pk=duplicate.pk).exists()
        assert ROM.objects.filter(rom_set__game=canonical).count() == 2
        assert result["game_id"] == canonical.pk

    def test_deleted_game_is_skipped(self, post):
        result = identify_game.func(CONTEXT, 999999)

        assert result["status"] == "skipped"
        post.assert_not_called()

    def test_makes_no_screenscraper_requests(self, post, nes):
        post.return_value = _response(404)
        game = _game(nes)

        with patch(
            "library.metadata.screenscraper.ScreenScraperClient._make_request"
        ) as ss:
            self._run(game)

        ss.assert_not_called()

    def test_manual_hash_lookup_merges_on_name_collision(self, post, nes):
        post.return_value = _response(200, _hasheous_body())
        canonical = _game(nes, name="Super Mario Bros.", crcs=("bbbb0001",))
        duplicate = _game(nes)

        with patch(
            "library.lookup.registry.DEFAULT_SERVICES", [HasheousLookupService()]
        ):
            result = run_hash_lookup.func(duplicate.pk)

        assert result["matched"] is True
        assert result["new_name"] == "Super Mario Bros."
        assert not Game.objects.filter(pk=duplicate.pk).exists()
        assert Game.objects.filter(pk=canonical.pk).exists()


@pytest.mark.django_db
class TestQueueGameFollowup:
    """Hasheous naming and ScreenScraper metadata are queued side by side."""

    @pytest.fixture
    def defer(self):
        with patch.object(identify_game, "configure") as configure:
            yield configure.return_value.defer

    @pytest.fixture
    def credentials(self):
        with (
            patch(
                "library.metadata.screenscraper.screenscraper_available",
                return_value=True,
            ),
            patch("library.tasks.run_metadata_job_for_game.configure"),
        ):
            yield

    def test_queues_hasheous_and_metadata_in_parallel(self, nes, defer, credentials):
        game = _game(nes)

        queued = queue_game_followup(game, use_hasheous=True, fetch_metadata=True)

        assert queued == (True, True)
        defer.assert_called_once_with(game_id=game.pk)
        assert MetadataJob.objects.filter(game=game).exists()

    def test_hasheous_without_metadata(self, nes, defer, credentials):
        game = _game(nes)

        queued = queue_game_followup(game, use_hasheous=True, fetch_metadata=False)

        assert queued == (True, False)
        assert not MetadataJob.objects.exists()

    def test_hasheous_runs_without_screenscraper_credentials(self, nes, defer):
        game = _game(nes)

        queued = queue_game_followup(game, use_hasheous=True, fetch_metadata=True)

        assert queued == (True, False)
        defer.assert_called_once_with(game_id=game.pk)

    def test_hasheous_disabled_queues_metadata_only(self, nes, defer, credentials):
        game = _game(nes)

        queued = queue_game_followup(game, use_hasheous=False, fetch_metadata=True)

        assert queued == (False, True)
        defer.assert_not_called()

    def test_arcade_skips_hasheous(self, defer, credentials):
        arcade = System.objects.create(
            name="Arcade",
            slug="arcade-test",
            extensions=[".zip"],
            folder_names=[],
            archive_as_rom=True,
            screenscraper_ids=[75],
        )
        game = _game(arcade, name="sf2")

        queued = queue_game_followup(game, use_hasheous=True, fetch_metadata=True)

        assert queued == (False, True)
        defer.assert_not_called()

    def test_already_named_game_skips_hasheous(self, nes, defer, credentials):
        game = _game(nes, name_source=Game.SOURCE_SCREENSCRAPER)

        queued = queue_game_followup(game, use_hasheous=True, fetch_metadata=True)

        assert queued == (False, True)
        defer.assert_not_called()


@pytest.mark.django_db
class TestScanQueuesIdentification:
    def test_scan_queues_one_identification_per_game(self, tmp_path, nes):
        from library.scanner import scan_directory

        folder = tmp_path / "NES"
        folder.mkdir()
        (folder / "Game A (USA).nes").write_bytes(b"\x00a")
        (folder / "Game A (Europe).nes").write_bytes(b"\x00b")
        (folder / "Game B (USA).nes").write_bytes(b"\x00c")

        with patch.object(identify_game, "configure") as configure:
            result = scan_directory(str(tmp_path), use_hasheous=True)

        assert result["added"] == 3
        assert result["identification_queued"] == 2
        game_ids = {
            call.kwargs["game_id"]
            for call in configure.return_value.defer.call_args_list
        }
        assert game_ids == set(Game.objects.values_list("pk", flat=True))

    def test_scan_without_hasheous_queues_no_identification(self, tmp_path, nes):
        from library.scanner import scan_directory

        (tmp_path / "Game A (USA).nes").write_bytes(b"\x00a")

        with patch.object(identify_game, "configure") as configure:
            result = scan_directory(str(tmp_path), use_hasheous=False)

        assert result["identification_queued"] == 0
        configure.assert_not_called()

    def test_rescan_queues_nothing(self, tmp_path, nes):
        from library.scanner import scan_directory

        (tmp_path / "Game A (USA).nes").write_bytes(b"\x00a")
        with patch.object(identify_game, "configure"):
            scan_directory(str(tmp_path), use_hasheous=True)

        with patch.object(identify_game, "configure") as configure:
            result = scan_directory(str(tmp_path), use_hasheous=True)

        assert result["identification_queued"] == 0
        configure.assert_not_called()


@pytest.mark.django_db
class TestUploadQueuesIdentification:
    def test_uploaded_rom_gets_hasheous_followup(self, tmp_path, nes, settings):
        from library.models import UploadJob
        from library.tasks import _process_uploaded_file

        temp = tmp_path / "upload.nes"
        temp.write_bytes(b"\x00rom")
        settings.ROM_LIBRARY_ROOT = str(tmp_path / "library")
        job = UploadJob.objects.create(task_id="t")

        with (
            patch(
                "library.upload.get_library_root", return_value=str(tmp_path / "lib")
            ),
            patch("library.tasks.queue_game_followup") as followup,
        ):
            result = _process_uploaded_file(
                str(temp),
                "Game C (USA).nes",
                nes,
                str(tmp_path / "lib"),
                job,
                fetch_metadata=False,
            )

        assert result["success"], result
        followup.assert_called_once()
        assert followup.call_args.kwargs == {
            "use_hasheous": True,
            "fetch_metadata": False,
        }


@pytest.mark.django_db
class TestConvertStuckIdentificationJobs:
    def _job(self, task_name, args, status="todo", queue="identification"):
        with connection.cursor() as cursor:
            cursor.execute(
                "INSERT INTO procrastinate_jobs (queue_name, task_name, args, status) "
                "VALUES (%s, %s, %s::jsonb, %s)",
                [queue, task_name, json.dumps(args), status],
            )

    def _jobs(self, task_name):
        with connection.cursor() as cursor:
            cursor.execute(
                "SELECT args, priority, queue_name FROM procrastinate_jobs "
                "WHERE task_name = %s ORDER BY id",
                [task_name],
            )
            return [
                (json.loads(args) if isinstance(args, str) else args, priority, queue)
                for args, priority, queue in cursor.fetchall()
            ]

    def test_converts_per_rom_jobs_into_one_job_per_unnamed_game(self, nes):
        from importlib import import_module

        migration = import_module("library.migrations.0006_convert_identify_rom_jobs")
        multi = _game(nes, name="Multi Disc", crcs=("c1", "c2"))
        named = _game(nes, name="Named", name_source=Game.SOURCE_SCREENSCRAPER)
        arcade = System.objects.create(
            name="Arcade",
            slug="arcade-mig",
            extensions=[".zip"],
            folder_names=[],
            archive_as_rom=True,
        )
        mame = _game(arcade, name="sf2")
        for game in (multi, named, mame):
            for rom in ROM.objects.filter(rom_set__game=game):
                self._job("library.tasks.identify_rom", {"rom_id": rom.pk})
        self._job("library.tasks.identify_rom", {"rom_id": 987654})  # ROM gone
        finished = ROM.objects.filter(rom_set__game=multi).first()
        self._job(
            "library.tasks.identify_rom", {"rom_id": finished.pk}, status="succeeded"
        )

        with connection.cursor() as cursor:
            cursor.execute(migration.CONVERT_JOBS)

        new_jobs = self._jobs("library.tasks.identify_game")
        assert new_jobs == [({"game_id": multi.pk}, 25, "identification")]
        remaining = self._jobs("library.tasks.identify_rom")
        assert len(remaining) == 1  # only the finished job's history is kept


@pytest.mark.django_db
def test_icon_auto_fetch_skips_without_credentials(nes):
    from library.tasks import auto_fetch_system_icons

    _game(nes)

    with patch("library.metadata.matcher.fetch_system_metadata_for_job") as fetch:
        result = auto_fetch_system_icons.func()

    assert result == {"status": "skipped", "reason": "no_credentials"}
    fetch.assert_not_called()


@pytest.mark.parametrize(
    "raw,expected",
    [
        ("Grinch, The", "The Grinch"),
        ("Bug's Life, A", "A Bug's Life"),
        ("0983 - Mummy, The", "The Mummy"),
        (
            "Legend of Zelda, The - A Link to the Past",
            "Legend of Zelda, The - A Link to the Past",
        ),
        ("Tetris", "Tetris"),
    ],
)
def test_hasheous_names_put_articles_first_like_filenames(raw, expected):
    assert HasheousLookupService()._clean_name(raw) == expected


@pytest.mark.django_db
class TestScreenScraperNamePrecedence:
    """An exact ScreenScraper match names the game whether or not Hasheous ran."""

    def _apply(self, game, match_type="crc32"):
        from library.metadata.matcher import apply_metadata_to_game

        apply_metadata_to_game(
            game,
            {
                "id": 4242,
                "_match_type": match_type,
                "_screenscraper_name": "Grinch (USA)",
            },
        )
        game.refresh_from_db()
        return game

    @pytest.mark.parametrize("source", ["filename", "hasheous", "NoIntros", "Redump"])
    def test_exact_match_renames_filename_and_hasheous_names(self, nes, source):
        game = self._apply(_game(nes, name="Grinch, The (hack)", name_source=source))

        assert game.name == "Grinch"
        assert game.name_source == Game.SOURCE_SCREENSCRAPER

    @pytest.mark.parametrize("source", ["manual", "collection", "screenscraper"])
    def test_exact_match_keeps_chosen_names(self, nes, source):
        game = self._apply(_game(nes, name="My Grinch", name_source=source))

        assert game.name == "My Grinch"

    def test_name_search_match_keeps_hasheous_name(self, nes):
        game = self._apply(
            _game(nes, name="The Grinch", name_source="NoIntros"), match_type="name"
        )

        assert game.name == "The Grinch"
        assert game.name_source == "NoIntros"

    def test_metadata_keeps_name_hasheous_wrote_meanwhile(self, nes):
        """The metadata job's game was loaded before Hasheous renamed it."""
        from library.metadata.matcher import apply_metadata_to_game

        stale = _game(nes, name="Grinch (hack)")
        Game.objects.filter(pk=stale.pk).update(
            name="The Grinch", name_source="NoIntros"
        )

        apply_metadata_to_game(stale, {"id": 4242, "_match_type": "name"})

        game = Game.objects.get(pk=stale.pk)
        assert game.name == "The Grinch"
        assert game.name_source == "NoIntros"
        assert game.screenscraper_id == 4242
