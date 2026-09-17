"""Matching must not convert service failures or fuzzy evidence into identity."""

from types import SimpleNamespace
from unittest.mock import patch

import pytest
import requests

from library.lookup.screenscraper import ScreenScraperLookupService
from library.metadata.matcher import fetch_metadata_for_game
from library.metadata.screenscraper import ScreenScraperRateLimited
from library.models import Game, ScreenScraperLookupCache, System


def _system_stub(*ids: int) -> SimpleNamespace:
    """Bare system stand-in exposing only what the name search needs."""
    return SimpleNamespace(all_screenscraper_ids=list(ids), archive_as_rom=False)


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
            {"id": 123, "name": "Unique Adventure", "all_names": ["Unique Adventure"], "system_id": 3},
        ]
        result = service._aggregate_name_search(
            "Unique Adventure", _system_stub(3), set()
        )
        assert result is not None
        assert result.match_type == "name"
        assert result.matched_system_id == 3
        cached = service._aggregate_name_search(
            "Unique Adventure", _system_stub(3), set()
        )
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
    from library.lookup.screenscraper import _rank_candidates

    # ScreenScraper's live "Mario" query returns both at equal confidence.
    candidates = [
        {"id": "1271", "name": "Mario Bros.", "system_id": 3, "confidence": 0.85},
        {"id": "1340", "name": "Dr. Mario", "system_id": 3, "confidence": 0.85},
    ]
    assert _rank_candidates(candidates, {"3"}, {"3"}) is None
    assert _rank_candidates(candidates[::-1], {"3"}, {"3"}) is None


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
        assert service._aggregate_name_search("Mario Fishing Expedition", _system_stub(3), set()) is None


@pytest.mark.django_db
@pytest.mark.parametrize("title", [
    "VS. Excitebike", "Excitebike (VS)",
])
def test_vs_hardware_identity_is_not_stripped_to_home_console_game(title):
    from library.lookup.screenscraper import calculate_match_score

    service = ScreenScraperLookupService()
    with patch.object(service, "_get_client") as get_client:
        get_client.return_value.search_game.return_value = [
            {"id": "1371", "name": "Excitebike", "system_id": 3}
        ]
        assert service._aggregate_name_search(title, _system_stub(3), set()) is None
    assert calculate_match_score("Vs. Excitebike", "Excitebike") == 0.0
    assert calculate_match_score("Excitebike", "Vs. Excitebike") == 0.0
    assert calculate_match_score("Vs. Excitebike", "Vs. Excitebike") == 1.0


@pytest.mark.django_db
def test_hardware_prefix_off_arcade_is_home_console_game():
    """P0-D: without arcade context a 2C0x prefix is not a Vs identity."""
    from library.parser import parse_rom_filename

    assert parse_rom_filename("2C04-03 Excitebike.nes")["name"] == "Excitebike"
    service = ScreenScraperLookupService()
    with patch.object(service, "_get_client") as get_client:
        get_client.return_value.search_game.return_value = [
            {"id": "1371", "name": "Excitebike", "system_id": 3}
        ]
        result = service._aggregate_name_search(
            "2C04-03 Excitebike", _system_stub(3), set()
        )
    assert result is not None
    assert result.screenscraper_id == "1371"




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
        assert service._aggregate_name_search("Aladdin Trained", _system_stub(3), set()) is None

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
        assert service._aggregate_name_search("Bionic Commando: Elite Forces", _system_stub(9), set()) is None

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


@pytest.mark.django_db
@pytest.mark.parametrize("path", ["/roms/Hook (USA).zip"])
def test_crc_match_on_foreign_system_is_rejected(path):
    service = ScreenScraperLookupService()
    with patch.object(service, "_get_client") as get_client:
        get_client.return_value.has_credentials.return_value = True
        # SS answers the CRC with a Sega CD entry (system 20), not arcade.
        get_client.return_value.search_by_crc.return_value = {
            "id": "41707", "name": "Hook", "system_id": 20,
        }
        assert service.lookup(
            system=System(screenscraper_ids=[75, 158]),
            crc32="deadbeef",
            file_path=path,
        ) is None


@pytest.mark.django_db
def test_stem_fallback_rejects_different_game_identity():
    """With rescues disabled (rollback flag), a different-game identity
    sharing one token stays rejected; matching_rescue_enabled trades this
    protection for audit-verified rescues (e.g. Rondo of Blood)."""
    from library.models import Setting

    Setting.set("matching_rescue_enabled", False)
    service = ScreenScraperLookupService()
    with (
        patch.object(service, "_get_client") as get_client,
        patch(
            "library.metadata.screenscraper._get_search_variants",
            return_value=["Odd Mario"],
        ),
    ):
        # Stem search finds plain "Super Mario Bros." — a different game.
        get_client.return_value.search_game.return_value = [
            {"id": "1245", "name": "Super Mario Bros.", "all_names": ["Super Mario Bros."], "system_id": 3}
        ]
        assert service._aggregate_name_search("Odd Mario", _system_stub(3), set()) is None
        Setting.objects.filter(key="matching_rescue_enabled").delete()


@pytest.mark.django_db
def test_arcade_romnom_child_system_is_accepted():
    service = ScreenScraperLookupService()
    with patch.object(service, "_get_client") as get_client:
        get_client.return_value.has_credentials.return_value = True
        # 1942.zip is returned under Capcom Classics (system 151, child of arcade parent 75)
        get_client.return_value.search_by_romnom.return_value = {
            "id": "39874", "name": "1942", "system_id": 151,
        }
        result = service.lookup(
            system=System(screenscraper_ids=[75, 158], archive_as_rom=True),
            file_path="/roms/arcade/1942.zip",
        )
        assert result is not None
        assert result.screenscraper_id == "39874"
        assert result.match_type == "romnom"


@pytest.mark.django_db
def test_crc_child_system_is_accepted():
    service = ScreenScraperLookupService()
    with patch.object(service, "_get_client") as get_client:
        get_client.return_value.has_credentials.return_value = True
        # Hook is returned under Irem Classics (system 148, child of arcade parent 75)
        get_client.return_value.search_by_crc.return_value = {
            "id": "41707", "name": "Hook", "system_id": 148,
        }
        result = service.lookup(
            system=System(screenscraper_ids=[75, 158]),
            crc32="deadbeef",
            file_path="/roms/arcade/hook.zip",
        )
        assert result is not None
        assert result.screenscraper_id == "41707"
        assert result.match_type == "crc32"
        assert result.matched_system_id == 148


@pytest.mark.django_db
def test_romnom_match_on_foreign_system_is_rejected():
    service = ScreenScraperLookupService()
    with patch.object(service, "_get_client") as get_client:
        get_client.return_value.has_credentials.return_value = True
        # Match returned under Sega CD (system 20), which is foreign to arcade parent 75
        get_client.return_value.search_by_romnom.return_value = {
            "id": "99999", "name": "Foreign Game", "system_id": 20,
        }
        result = service.lookup(
            system=System(screenscraper_ids=[75, 158], archive_as_rom=True),
            file_path="/roms/arcade/foreign.zip",
        )
        assert result is None



@pytest.mark.django_db
def test_ambiguous_name_result_stays_rejected_on_repeat():
    """Caching must not turn an ambiguous cold miss into a warm match."""
    service = ScreenScraperLookupService()
    with (
        patch.object(service, "_get_client") as get_client,
        patch(
            "library.metadata.screenscraper._get_search_variants",
            return_value=["Target Game"],
        ),
    ):
        get_client.return_value.search_game.return_value = [
            {
                "id": 1,
                "name": "Target Game Deluxe",
                "all_names": [],
                "system_id": 3,
            },
            {
                "id": 2,
                "name": "Target Game Remix",
                "all_names": [],
                "system_id": 3,
            },
        ]
        assert service._aggregate_name_search("Target Game", _system_stub(3), set()) is None
        assert service._aggregate_name_search("Target Game", _system_stub(3), set()) is None


@pytest.mark.django_db
def test_composite_rescue_result_repeats():
    """Caching must not turn a cold structural rescue into a warm miss."""
    service = ScreenScraperLookupService()
    with (
        patch.object(service, "_get_client") as get_client,
        patch(
            "library.metadata.screenscraper._get_search_variants",
            return_value=["Qwak"],
        ),
    ):
        get_client.return_value.search_game.return_value = [
            {
                "id": 106286,
                "name": "Alien Breed Special Edition And Qwak",
                "all_names": [],
                "system_id": 3,
            },
        ]
        first = service._aggregate_name_search("Qwak", _system_stub(3), set())
        second = service._aggregate_name_search("Qwak", _system_stub(3), set())

    assert first is not None
    assert second is not None
    assert first.screenscraper_id == second.screenscraper_id == 106286

def test_subtitle_does_not_hide_sequel_number():
    """An alphabetic subtitle must not bypass sequel-number consistency."""
    from library.lookup.screenscraper import calculate_match_score

    assert calculate_match_score("Mega Man", "Mega Man 2 Deluxe") == 0.0


@pytest.mark.django_db
def test_single_unrelated_search_result_is_not_rescued():
    """A sole ScreenScraper result is not identity evidence at score zero."""
    service = ScreenScraperLookupService()
    with (
        patch.object(service, "_get_client") as get_client,
        patch(
            "library.metadata.screenscraper._get_search_variants",
            return_value=["Target Game"],
        ),
    ):
        get_client.return_value.search_game.return_value = [
            {
                "id": 9,
                "name": "Completely Different",
                "all_names": [],
                "system_id": 3,
            },
        ]
        result = service._aggregate_name_search("Target Game", _system_stub(3), set())

    assert result is None


@pytest.mark.django_db
def test_exact_primary_variant_skips_remaining_variants():
    """A decisive exact result does not spend calls on fallback variants."""
    service = ScreenScraperLookupService()
    with (
        patch.object(service, "_get_client") as get_client,
        patch(
            "library.metadata.screenscraper._get_search_variants",
            return_value=["Target Game", "Target"],
        ),
        patch(
            "library.lookup.screenscraper.expand_system_ids",
            return_value=[3],
        ),
    ):
        get_client.return_value.search_game.return_value = [
            {
                "id": 10,
                "name": "Target Game",
                "all_names": [],
                "system_id": 3,
            },
        ]
        result = service._aggregate_name_search("Target Game", _system_stub(3), set())

    assert result is not None
    assert result.screenscraper_id == 10
    get_client.return_value.search_game.assert_called_once_with("Target Game", 3)
