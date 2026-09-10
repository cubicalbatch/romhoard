"""Matching must not convert service failures or fuzzy evidence into identity."""

from types import SimpleNamespace
from unittest.mock import patch

import pytest
import requests

from library.lookup.screenscraper import ScreenScraperLookupService
from library.metadata.matcher import fetch_metadata_for_game
from library.metadata.screenscraper import ScreenScraperRateLimited
from library.models import Game, ScreenScraperLookupCache, System


@pytest.mark.django_db
@pytest.mark.parametrize("status,body", [
    (200, b"Service unavailable"),
    (200, b'{"response":{"erreur":"Invalid credentials"}}'),
    (200, b'[]'),
    (503, b"Service unavailable"),
    (429, b"Too many threads"),
    (431, b"Negative request quota exceeded"),
])
def test_api_failure_does_not_become_cached_miss(status, body):
    system = System.objects.create(name="Failure test", slug="failure-test", screenscraper_ids=[3], extensions=[".nes"], folder_names=[])
    game = Game.objects.create(name="Unidentified Adventure", system=system)
    response = requests.Response()
    response.status_code = status
    response._content = body
    with (
        patch("library.metadata.screenscraper.Setting.get", return_value="test"),
        patch("library.metadata.screenscraper.get_pause_until", return_value=None),
        patch("library.metadata.matcher.get_cached_metadata", return_value=None),
        patch("requests.get", return_value=response),
    ):
        with pytest.raises((requests.RequestException, ScreenScraperRateLimited)):
            fetch_metadata_for_game(game)
    game.refresh_from_db()
    assert not game.metadata_match_failed
    assert game.screenscraper_id is None
    assert not ScreenScraperLookupCache.objects.filter(lookup_type="name").exists()


@pytest.mark.django_db
def test_empty_details_do_not_erase_existing_identity():
    system = System.objects.create(name="Details test", slug="details-test", screenscraper_ids=[3], extensions=[".nes"], folder_names=[])
    game = Game.objects.create(name="Existing Adventure", system=system, screenscraper_id=123)
    with (
        patch("library.metadata.matcher.ScreenScraperClient") as client,
        patch("library.metadata.matcher.save_metadata_cache") as save_cache,
    ):
        client.return_value.get_game_info.return_value = {}
        assert fetch_metadata_for_game(game) is None
        save_cache.assert_not_called()
    game.refresh_from_db()
    assert game.screenscraper_id == 123


@pytest.mark.django_db
def test_name_match_does_not_claim_hash_identity():
    service = ScreenScraperLookupService()
    with (
        patch.object(service, "_get_client") as get_client,
        patch("library.metadata.screenscraper._get_search_variants", return_value=["Unique Adventure"]),
    ):
        get_client.return_value.search_game.return_value = [
            {"id": 123, "name": "Unique Adventure", "all_names": ["Unique Adventure"], "system_id": 3}
        ]
        result = service._try_name_search("Unique Adventure", 3)
        assert result is not None
        assert result.match_type == "name"
        assert result.matched_system_id == 3
        cached = service._try_name_search("Unique Adventure", 3)
        assert cached is not None
        assert cached.match_type == "name"
        assert cached.confidence == result.confidence


@pytest.mark.django_db
@pytest.mark.parametrize("archived", [False, True])
@pytest.mark.parametrize("manual", [False, True])
def test_identification_recognizes_rom_filename_in_loose_and_archived_files(archived, manual):
    from library.models import ROM, ROMSet
    from library.tasks import identify_rom, run_hash_lookup

    system = System.objects.create(
        name="ROM test", slug="rom-test", extensions=[".nes"],
        folder_names=[], screenscraper_ids=[3],
    )
    game = Game.objects.create(name="Unknown", system=system)
    rom_set = ROMSet.objects.create(game=game)
    filename = "Unique Adventure (USA).nes"
    rom = ROM.objects.create(
        rom_set=rom_set, file_name=filename, file_size=16,
        file_path=f"/roms/pack.zip!{filename}" if archived else f"/roms/{filename}",
        archive_path="/roms/pack.zip" if archived else "",
        path_in_archive=filename if archived else "",
        crc32="deadbeef",
    )
    service = ScreenScraperLookupService()
    with (
        patch("library.lookup.registry.DEFAULT_SERVICES", [service]),
        patch.object(service, "_get_client") as get_client,
        patch("library.tasks.queue_game_metadata", return_value=False),
    ):
        get_client.return_value.has_credentials.return_value = True
        get_client.return_value.search_by_crc.return_value = None
        get_client.return_value.search_by_romnom.side_effect = lambda name, system_id: (
            {"id": 456, "name": "Unique Adventure", "system_id": system_id}
            if name == filename else None
        )
        get_client.return_value.search_game.return_value = []
        if manual:
            result = run_hash_lookup.func(game.pk)
            assert result["old_name"] == "Unknown"
        else:
            identify_rom.func(SimpleNamespace(should_abort=lambda: False), rom.pk)
    game.refresh_from_db()
    assert game.screenscraper_id == 456
    assert game.name == "Unique Adventure"


def test_ambiguous_live_name_candidates_are_not_selected_by_order():
    from library.lookup.screenscraper import _find_best_match

    # ScreenScraper's live "Mario" query returns both at equal confidence.
    candidates = [
        {"id": "1271", "name": "Mario Bros."},
        {"id": "1340", "name": "Dr. Mario"},
    ]
    assert _find_best_match("Mario", candidates) is None
    assert _find_best_match("Mario", candidates[::-1]) is None


@pytest.mark.django_db
def test_truncated_query_does_not_identify_an_unrelated_game():
    service = ScreenScraperLookupService()
    with (
        patch.object(service, "_get_client") as get_client,
        patch("library.metadata.screenscraper._get_search_variants", return_value=["Mario"]),
    ):
        get_client.return_value.search_game.return_value = [
            {"id": "1271", "name": "Mario Bros.", "all_names": ["Mario Bros."]}
        ]
        assert service._try_name_search("Mario Fishing Expedition", 3) is None


@pytest.mark.django_db
@pytest.mark.parametrize("title", [
    "2C04-03 Excitebike", "VS. Excitebike", "Excitebike (VS)",
])
def test_vs_hardware_identity_is_not_stripped_to_home_console_game(title):
    from library.lookup.screenscraper import calculate_match_score

    service = ScreenScraperLookupService()
    with patch.object(service, "_get_client") as get_client:
        get_client.return_value.search_game.return_value = [
            {"id": "1371", "name": "Excitebike", "system_id": 3}
        ]
        assert service._try_name_search(title, 3) is None
    assert calculate_match_score("Vs. Excitebike", "Excitebike") == 0.0
    assert calculate_match_score("Excitebike", "Vs. Excitebike") == 0.0
    assert calculate_match_score("Vs. Excitebike", "Vs. Excitebike") == 1.0




@pytest.mark.django_db
def test_tied_same_system_candidates_are_rejected_not_first_won():
    service = ScreenScraperLookupService()
    with (
        patch.object(service, "_get_client") as get_client,
        patch(
            "library.metadata.screenscraper._get_search_variants",
            return_value=["Aladdin"],
        ),
    ):
        get_client.return_value.search_game.return_value = [
            {"id": "2170", "name": "Disney's Aladdin", "all_names": ["Disney's Aladdin"], "system_id": 3},
            {"id": "268186", "name": "Aladdin 2000", "all_names": ["Aladdin 2000"], "system_id": 3},
        ]

        # Both score 0.85 against the cleanup variant; neither may win by order.
        assert service._try_name_search("Aladdin Trained", 3) is None

@pytest.mark.django_db
def test_dropped_subtitle_rejects_franchise_prefix_match():
    service = ScreenScraperLookupService()
    with (
        patch.object(service, "_get_client") as get_client,
        patch(
            "library.metadata.screenscraper._get_search_variants",
            return_value=["Bionic Commando Elite Forces"],
        ),
    ):
        # ScreenScraper has no Elite Forces entry on GB; only the base game.
        get_client.return_value.search_game.return_value = [
            {"id": "3120", "name": "Bionic Commando", "all_names": ["Bionic Commando"], "system_id": 9}
        ]
        assert service._try_name_search("Bionic Commando: Elite Forces", 9) is None

@pytest.mark.django_db
def test_expired_negative_hash_is_retried_but_positive_hash_is_retained():
    from datetime import timedelta
    from django.utils import timezone

    stale = ScreenScraperLookupCache.objects.create(
        lookup_type="crc", lookup_value="dd61ae6b", system_id=26, matched=False,
    )
    ScreenScraperLookupCache.objects.filter(pk=stale.pk).update(
        created_at=timezone.now() - timedelta(days=31),
    )
    service = ScreenScraperLookupService()
    with patch.object(service, "_get_client") as get_client:
        get_client.return_value.search_by_crc.return_value = {
            "id": "544232", "name": "Man Goes Down", "system_id": 26,
        }
        match = service._try_crc("dd61ae6b", 26)
        assert match is not None and match.screenscraper_id == "544232"
        ScreenScraperLookupCache.objects.filter(pk=stale.pk).update(
            created_at=timezone.now() - timedelta(days=31),
        )
        get_client.return_value.search_by_crc.side_effect = AssertionError("Positive hash should remain cached")
        cached = service._try_crc("dd61ae6b", 26)
        assert cached is not None and cached.screenscraper_id == 544232
