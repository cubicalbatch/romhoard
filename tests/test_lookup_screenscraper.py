"""Tests for ScreenScraperLookupService."""

import library.lookup.screenscraper as ssmod
import pytest
import requests
from unittest.mock import MagicMock, patch

from library.lookup.screenscraper import (
    ScreenScraperLookupService,
    _extract_romnom,
    _extract_numbers,
    _check_cache,
    _save_to_cache,
    normalize_name,
    calculate_match_score,
    _candidate_score,
    _rank_candidates,
    _romnom_title_matches,
    _candidate_omits_identity_words,
    _subtitle_guard,
    _is_notgame_placeholder,
    expand_system_ids,
    get_all_family_system_ids,
)
from library.lookup.base import LookupResult
from library.metadata.screenscraper import ScreenScraperClient


@pytest.fixture
def mock_system():
    """Create a mock System object for testing."""
    system = MagicMock()
    system.slug = "gba"
    system.archive_as_rom = False
    system.all_screenscraper_ids = [12]
    return system


@pytest.fixture
def mock_arcade_system():
    """Create a mock arcade System object for testing."""
    system = MagicMock()
    system.slug = "arcade"
    system.archive_as_rom = True
    system.all_screenscraper_ids = [75, 142]
    return system


class TestExtractRomnom:
    """Tests for _extract_romnom function."""

    def test_loose_file(self):
        """Loose file returns just the filename."""
        result = _extract_romnom("/roms/gba/Advance Wars.gba", archive_as_rom=False)
        assert result == "Advance Wars.gba"

    def test_archive_as_rom_uses_archive_name(self):
        """For archive_as_rom systems, use the archive filename."""
        result = _extract_romnom("/roms/arcade/pacman.zip", archive_as_rom=True)
        assert result == "pacman.zip"

    def test_archived_rom_uses_inner_filename(self):
        """For regular archives, use the inner ROM filename."""
        result = _extract_romnom(
            "/roms/gba/collection.zip!Advance Wars.gba", archive_as_rom=False
        )
        assert result == "Advance Wars.gba"

    def test_archived_rom_with_nested_path(self):
        """Handle nested paths inside archives."""
        result = _extract_romnom(
            "/roms/gba/collection.zip!Games/USA/Advance Wars.gba", archive_as_rom=False
        )
        assert result == "Advance Wars.gba"

    def test_archive_as_rom_ignores_internal_path(self):
        """For arcade, even if there's internal structure, use archive name."""
        result = _extract_romnom(
            "/roms/arcade/pacman.zip!pacman/game.rom", archive_as_rom=True
        )
        assert result == "pacman.zip"


@pytest.mark.django_db
class TestScreenScraperLookupService:
    """Tests for ScreenScraperLookupService."""

    def test_lookup_returns_none_without_credentials(self, mock_system):
        """Lookup returns None when credentials aren't configured."""
        service = ScreenScraperLookupService()
        mock_client = MagicMock()
        mock_client.has_credentials.return_value = False

        with patch.object(service, "_get_client", return_value=mock_client):
            result = service.lookup(
                system=mock_system, crc32="12345678", file_path="/roms/test.gba"
            )

        assert result is None

    def test_lookup_tries_crc_first_for_regular_system(self, mock_system):
        """For non-arcade systems, CRC lookup is tried first."""
        service = ScreenScraperLookupService()
        mock_client = MagicMock()
        mock_client.has_credentials.return_value = True
        mock_client.search_by_crc.return_value = {
            "id": 123,
            "name": "Advance Wars",
            "system_id": 12,
        }

        with patch.object(service, "_get_client", return_value=mock_client):
            result = service.lookup(
                system=mock_system, crc32="12345678", file_path="/roms/test.gba"
            )

        assert result is not None
        assert result.name == "Advance Wars"
        assert result.screenscraper_id == 123
        assert result.match_type == "crc32"
        assert result.matched_system_id == 12
        assert result.source == "screenscraper"
        mock_client.search_by_crc.assert_called_once_with("12345678", 12)

    def test_lookup_falls_back_to_romnom(self, mock_system):
        """When CRC fails, romnom lookup is tried."""
        service = ScreenScraperLookupService()
        mock_client = MagicMock()
        mock_client.has_credentials.return_value = True
        mock_client.search_by_crc.return_value = None
        mock_client.search_by_romnom.return_value = {
            "id": 456,
            "name": "Fire Emblem",
            "system_id": 12,
        }

        with patch.object(service, "_get_client", return_value=mock_client):
            result = service.lookup(
                system=mock_system, crc32="12345678", file_path="/roms/Fire Emblem.gba"
            )

        assert result is not None
        assert result.name == "Fire Emblem"
        assert result.screenscraper_id == 456
        assert result.match_type == "romnom"
        assert result.matched_system_id == 12
        mock_client.search_by_romnom.assert_called_once_with("Fire Emblem.gba", 12)

    def test_arcade_skips_crc_goes_straight_to_romnom(self, mock_arcade_system):
        """Arcade systems skip CRC and use romnom directly."""
        service = ScreenScraperLookupService()
        mock_client = MagicMock()
        mock_client.has_credentials.return_value = True
        mock_client.search_by_romnom.return_value = {
            "id": 789,
            "name": "Pac-Man",
            "system_id": 75,
        }

        with patch.object(service, "_get_client", return_value=mock_client):
            result = service.lookup(
                system=mock_arcade_system,
                crc32="12345678",
                file_path="/roms/arcade/pacman.zip",
            )

        assert result is not None
        assert result.name == "Pac-Man"
        assert result.screenscraper_id == 789
        # CRC should NOT be called for arcade
        mock_client.search_by_crc.assert_not_called()
        mock_client.search_by_romnom.assert_called()

    def test_arcade_tries_multiple_system_ids(self, mock_arcade_system):
        """Arcade tries multiple system IDs until a match is found."""
        service = ScreenScraperLookupService()
        mock_client = MagicMock()
        mock_client.has_credentials.return_value = True
        # First system ID fails, second succeeds
        mock_client.search_by_romnom.side_effect = [
            None,  # First system ID (75)
            {"id": 999, "name": "Neo Geo Game", "system_id": 142},  # Second (142)
        ]

        with patch.object(service, "_get_client", return_value=mock_client):
            result = service.lookup(
                system=mock_arcade_system,
                crc32="",
                file_path="/roms/arcade/neogame.zip",
            )

        assert result is not None
        assert result.screenscraper_id == 999
        # Should have tried both system IDs
        assert mock_client.search_by_romnom.call_count == 2

    def test_is_available_requires_screenscraper_ids(self, mock_system):
        """Service is only available if system has ScreenScraper IDs."""
        service = ScreenScraperLookupService()

        # Has IDs
        assert service.is_available(mock_system) is True

        # No IDs
        mock_system.all_screenscraper_ids = []
        assert service.is_available(mock_system) is False


@pytest.mark.django_db
class TestScreenScraperLookupCache:
    """Tests for ScreenScraper cache functions."""

    def test_cache_miss_returns_not_found(self):
        """Cache check returns (False, None) for uncached lookups."""
        found, result = _check_cache("crc", "abc12345", 12)
        assert found is False
        assert result is None

    def test_cache_stores_and_retrieves_match(self):
        """Cache stores and retrieves successful matches."""
        _save_to_cache(
            "crc",
            "ABC12345",
            12,
            {"id": 123, "name": "Test Game"},
        )

        found, result = _check_cache("crc", "abc12345", 12)
        assert found is True
        assert result is not None
        assert result["id"] == 123
        assert result["name"] == "Test Game"

    def test_cache_stores_and_retrieves_no_match(self):
        """Cache stores and retrieves known no-matches."""
        _save_to_cache("romnom", "unknown.zip", 75, None)

        found, result = _check_cache("romnom", "unknown.zip", 75)
        assert found is True
        assert result is None

    def test_cache_is_case_insensitive(self):
        """Cache lookups are case-insensitive for values."""
        _save_to_cache(
            "crc",
            "DEADBEEF",
            12,
            {"id": 999, "name": "Case Test"},
        )

        # Should find with different case
        found, result = _check_cache("crc", "deadbeef", 12)
        assert found is True
        assert result["id"] == 999

    def test_cache_differentiates_by_system_id(self):
        """Same CRC/romnom can have different results per system."""
        # Save match for system 12
        _save_to_cache("crc", "aabbccdd", 12, {"id": 100, "name": "GBA Game"})
        # Save no-match for system 75
        _save_to_cache("crc", "aabbccdd", 75, None)

        # System 12 should have match
        found, result = _check_cache("crc", "aabbccdd", 12)
        assert found is True
        assert result is not None
        assert result["id"] == 100

        # System 75 should have no-match
        found, result = _check_cache("crc", "aabbccdd", 75)
        assert found is True
        assert result is None


@pytest.mark.django_db
class TestLookupResultScreenscraperId:
    """Tests for screenscraper_id field on LookupResult."""

    def test_lookup_result_has_screenscraper_id(self):
        """LookupResult can store screenscraper_id."""
        result = LookupResult(
            name="Test Game",
            region="USA",
            revision="Rev 1",
            tags=[],
            source="screenscraper",
            confidence=0.85,
            raw_name="Test Game",
            screenscraper_id=12345,
        )
        assert result.screenscraper_id == 12345

    def test_lookup_result_screenscraper_id_defaults_to_none(self):
        """LookupResult screenscraper_id defaults to None."""
        result = LookupResult(
            name="Test Game",
            region="",
            revision="",
            tags=[],
            source="hasheous",
            confidence=0.9,
            raw_name="Test Game",
        )
        assert result.screenscraper_id is None


class TestNormalizeName:
    """Tests for normalize_name function."""

    def test_converts_to_lowercase(self):
        """Converts name to lowercase."""
        assert normalize_name("Super Mario World") == "super mario world"

    def test_removes_leading_article_the(self):
        """Removes leading 'The ' article."""
        assert normalize_name("The Legend of Zelda") == "legend of zelda"

    def test_removes_leading_article_a(self):
        """Removes leading 'A ' article."""
        assert normalize_name("A Link to the Past") == "link to the past"

    def test_removes_punctuation(self):
        """Removes punctuation and special characters."""
        assert (
            normalize_name("Pac-Man: Championship Edition!")
            == "pac man championship edition"
        )

    def test_pac_man_vs_pac_man_normalization(self):
        """Hyphens and punctuation converted to spaces so Pac-Man matches Pac Man."""
        assert normalize_name("Pac-Man") == "pac man"
        assert normalize_name("Pac Man") == "pac man"
        assert normalize_name("Pac-Man") == normalize_name("Pac Man")

    def test_10_yard_fight_normalization(self):
        """Hyphenated words like 10-Yard Fight do not merge into 10yard."""
        assert normalize_name("10-Yard Fight") == "10 yard fight"
        assert normalize_name("10 Yard Fight") == "10 yard fight"

    def test_roman_numeral_normalization(self):
        """Standalone Roman numerals II-X are converted to Western numbers 2-10."""
        assert normalize_name("Mega Man II") == "mega man 2"
        assert normalize_name("Mega Man 2") == "mega man 2"
        assert normalize_name("Final Fantasy III") == "final fantasy 3"
        assert normalize_name("Final Fantasy 3") == "final fantasy 3"
        assert normalize_name("Street Fighter IV") == "street fighter 4"
        assert normalize_name("Street Fighter V") == "street fighter 5"
        assert normalize_name("Resident Evil VI") == "resident evil 6"
        assert normalize_name("Dragon Quest VII") == "dragon quest 7"
        assert normalize_name("Dragon Quest VIII") == "dragon quest 8"
        assert normalize_name("Dragon Quest IX") == "dragon quest 9"
        assert normalize_name("Mega Man X") == "mega man 10"
        # Non-standalone should not be converted
        assert normalize_name("Mega Man X2") == "mega man x2"
        assert normalize_name("Vega") == "vega"

    def test_normalizes_unicode(self):
        """Converts accented characters to ASCII."""
        assert normalize_name("Pokémon") == "pokemon"

    def test_normalizes_whitespace(self):
        """Normalizes multiple spaces to single space."""
        assert normalize_name("Super  Mario   World") == "super mario world"


class TestCalculateMatchScore:
    """Tests for calculate_match_score function."""

    def test_exact_match_returns_one(self):
        """Exact match returns 1.0."""
        assert calculate_match_score("Super Mario World", "Super Mario World") == 1.0

    def test_case_insensitive_match(self):
        """Case differences don't affect exact match."""
        assert calculate_match_score("SUPER MARIO WORLD", "super mario world") == 1.0

    def test_substring_match_returns_high_score(self):
        """Substring match with 2+ meaningful words returns 0.85."""
        score = calculate_match_score("Super Mario", "Super Mario World")
        assert score == 0.85

    def test_single_word_short_substring_does_not_score_high(self):
        """Single-word substring constituting < 40% of length does not score 0.85."""
        score = calculate_match_score("Mario", "Super Mario World")
        assert score < 0.60

    def test_partial_word_overlap(self):
        """Partial word overlap calculates Jaccard similarity."""
        score = calculate_match_score("Super Mario Bros", "Super Mario World")
        # "super", "mario" overlap, "bros" vs "world" don't
        # Overlap: 2, Total: 4, Score: 0.5
        assert 0.4 <= score <= 0.6

    def test_no_match_returns_zero(self):
        """Completely different names return low score."""
        score = calculate_match_score("Tetris", "Pac-Man")
        assert score == 0.0

    def test_pac_man_vs_pac_man_score(self):
        """Pac-Man vs Pac Man scores 1.0."""
        assert calculate_match_score("Pac-Man", "Pac Man") == 1.0

    def test_10_yard_fight_score(self):
        """10-Yard Fight vs 10 Yard Fight scores 1.0."""
        assert calculate_match_score("10-Yard Fight", "10 Yard Fight") == 1.0

    def test_mega_man_2_vs_ii_score(self):
        """Mega Man 2 vs Mega Man II scores 1.0."""
        assert calculate_match_score("Mega Man 2", "Mega Man II") == 1.0

    def test_final_fantasy_3_vs_iii_score(self):
        """Final Fantasy 3 vs Final Fantasy III scores 1.0."""
        assert calculate_match_score("Final Fantasy 3", "Final Fantasy III") == 1.0

    def test_shorter_title_words_contained_in_longer(self):
        """When all words of shorter title are in longer, score is at least 0.85."""
        assert calculate_match_score("Aladdin", "Disney's Aladdin") >= 0.85
        assert calculate_match_score("Disney's Aladdin", "Aladdin") >= 0.85
        assert calculate_match_score("Aladdin 2", "Disney's Aladdin: Part 2") >= 0.85

    def test_anti_false_positive_required_cases(self):
        """Specific anti-false-positive cases requested for accuracy filter."""
        # 1. Console term overlap only -> score 0.0
        assert calculate_match_score("32X Color by mic", "Doom 32X Resurrection") == 0.0

        # 2. Sequel number inconsistency -> score 0.0
        assert calculate_match_score("Sonic The Hedgehog 32X Pure Port", "Sonic Robo Blast 2") == 0.0
        assert calculate_match_score("Metal Slug", "Metal Slug 3") == 0.0
        assert calculate_match_score("Mega Man", "Mega Man 2") == 0.0

        # 3. Exact and Roman numeral matches
        assert calculate_match_score("Mega Man 2", "Mega Man 2") == 1.0
        assert calculate_match_score("Mega Man II", "Mega Man 2") == 1.0

        # 4. Substring and casing matches
        assert calculate_match_score("Aladdin", "Disney's Aladdin") >= 0.85
        assert calculate_match_score("ActRaiser", "Actraiser") == 1.0


class TestRankCandidates:
    """Tests for _rank_candidates aggregate acceptance."""

    def test_empty_pool_returns_none(self):
        assert _rank_candidates([], {"12"}, {"12"}) is None

    def test_exact_beats_substring_candidate(self):
        """The 1.0 candidate wins even when listed after a 0.85 one."""
        pool = [
            {"id": 1, "name": "Target Game Deluxe", "system_id": 12, "confidence": 0.85},
            {"id": 2, "name": "Target Game", "system_id": 12, "confidence": 1.0},
        ]
        assert _rank_candidates(pool, {"12"}, {"12"})["id"] == 2

    def test_margin_rejects_close_runner_up(self):
        """0.75 runner-up is within the 0.15 margin of a 0.85 top."""
        pool = [
            {"id": 1, "name": "A", "system_id": 12, "confidence": 0.85},
            {"id": 2, "name": "B", "system_id": 12, "confidence": 0.75},
        ]
        assert _rank_candidates(pool, {"12"}, {"12"}) is None

    def test_margin_accepts_distant_runner_up(self):
        pool = [
            {"id": 1, "name": "A", "system_id": 12, "confidence": 0.85},
            {"id": 2, "name": "B", "system_id": 12, "confidence": 0.6},
        ]
        assert _rank_candidates(pool, {"12"}, {"12"})["id"] == 1

    def test_tie_between_different_game_ids_rejected(self):
        pool = [
            {"id": 1, "name": "A", "system_id": 12, "confidence": 0.85},
            {"id": 2, "name": "B", "system_id": 12, "confidence": 0.85},
        ]
        assert _rank_candidates(pool, {"12"}, {"12"}) is None
        assert _rank_candidates(pool[::-1], {"12"}, {"12"}) is None

    def test_exact_tie_accepts_lowest_ss_id(self):
        """A 1.0 tie across ids is an SS duplicate entry: lowest id wins."""
        pool = [
            {"id": 117137, "name": "Wonderland", "system_id": 12, "confidence": 1.0},
            {"id": 117136, "name": "Wonderland", "system_id": 12, "confidence": 1.0},
        ]
        assert _rank_candidates(pool, {"12"}, {"12"})["id"] == 117136
        assert _rank_candidates(pool[::-1], {"12"}, {"12"})["id"] == 117136

    def test_same_game_id_runner_up_exempt_from_margin(self):
        pool = [
            {"id": 1, "name": "A", "system_id": 12, "confidence": 0.85},
            {"id": 1, "name": "A", "system_id": 13, "confidence": 0.85},
        ]
        assert _rank_candidates(pool, {"12"}, {"12", "13"})["id"] == 1

    def test_family_system_exact_accepted(self):
        """Rule (a): exact scores are accepted on family systems."""
        pool = [{"id": 5, "name": "Hack Game", "system_id": 278, "confidence": 1.0}]
        assert _rank_candidates(pool, {"3"}, {"3", "278"}) is not None

    def test_family_system_sub_exact_rejected(self):
        """Rule (c): sub-exact scores are mapped-only."""
        pool = [{"id": 5, "name": "Hack Game", "system_id": 278, "confidence": 0.7}]
        assert _rank_candidates(pool, {"3"}, {"3", "278"}) is None

    def test_family_system_substring_rule_b_accepted(self):
        """Rule (b): 0.85 scores are accepted on family systems."""
        pool = [
            {"id": 5, "name": "Hockey And Tennis", "system_id": 278, "confidence": 0.85}
        ]
        assert _rank_candidates(pool, {"3"}, {"3", "278"}) is not None


class TestCandidateScore:
    """Tests for _candidate_score."""

    def test_scores_all_names_and_takes_max(self):
        result = {"name": "No Match Here", "all_names": ["Super Mario World"]}
        assert _candidate_score("Super Mario World", "Totally Different", result) == 1.0

    def test_raw_query_name_participates(self):
        result = {"name": "Hockey And Tennis"}
        assert _candidate_score("Hockey", "Hockey Tennis", result) >= 0.85

    def test_no_names_scores_zero(self):
        assert _candidate_score("X", "Y", {"name": "", "all_names": []}) == 0.0


class TestExpandSystemIds:
    """Tests for the vendored parentid family closure."""

    def test_mapped_ids_plus_descendants(self):
        from types import SimpleNamespace

        system = SimpleNamespace(all_screenscraper_ids=[3])
        assert expand_system_ids(system) == [3, 106, 278]

    def test_snes_and_pce_families_include_companion_systems(self):
        from types import SimpleNamespace

        assert 202 in expand_system_ids(SimpleNamespace(all_screenscraper_ids=[4]))
        assert 114 in expand_system_ids(SimpleNamespace(all_screenscraper_ids=[31]))

    def test_unknown_system_unchanged(self):
        from types import SimpleNamespace

        assert expand_system_ids(SimpleNamespace(all_screenscraper_ids=[999])) == [999]


    def test_descendants_capped_at_four_lowest_ids(self, monkeypatch):
        """A parent with more than four children expands to only four."""
        from types import SimpleNamespace

        monkeypatch.setattr(
            ssmod,
            "_SYSTEM_FAMILIES",
            {"900": ["1005", "1003", "901", "1004", "902", "1002"]},
        )
        system = SimpleNamespace(all_screenscraper_ids=[900])
        assert expand_system_ids(system) == [900, 901, 902, 1002, 1003]

    def test_descendant_closure_capped_across_generations(self, monkeypatch):
        from types import SimpleNamespace

        monkeypatch.setattr(
            ssmod,
            "_SYSTEM_FAMILIES",
            {"800": ["801", "802"], "801": ["803"], "803": ["804"]},
        )
        system = SimpleNamespace(all_screenscraper_ids=[800])
        assert expand_system_ids(system) == [800, 801, 802, 803, 804]

    def test_arcade_family_fanout_is_bounded(self):
        """Real data: arcade parent 75 has ~68 descendants; expansion caps it."""
        from types import SimpleNamespace

        system = SimpleNamespace(all_screenscraper_ids=[75, 142])
        expanded = expand_system_ids(system)
        assert expanded[:2] == [75, 142]
        assert len(expanded) == 6

    def test_up_walk_small_family(self, monkeypatch):
        """P1: sufami (108) walks up to snes parent 4 and its siblings."""
        from types import SimpleNamespace

        monkeypatch.setattr(
            ssmod,
            "_load_system_families",
            lambda: {"4": ["107", "108", "110", "202"]},
        )
        system = SimpleNamespace(all_screenscraper_ids=[108])
        assert sorted(expand_system_ids(system)) == [4, 107, 108, 110, 202]

    def test_up_walk_skips_big_families(self, monkeypatch):
        """A mapped child of a 67-child parent (arcade 75) does not expand."""
        from types import SimpleNamespace

        monkeypatch.setattr(
            ssmod,
            "_load_system_families",
            lambda: {"75": [str(i) for i in range(100, 167)]},
        )
        system = SimpleNamespace(all_screenscraper_ids=[142])
        assert expand_system_ids(system) == [142]

    def test_get_all_family_ids_includes_up_walk(self, monkeypatch):
        """Acceptance covers the same up-walked ids the search probes."""
        from types import SimpleNamespace

        monkeypatch.setattr(
            ssmod,
            "_load_system_families",
            lambda: {"31": ["105", "114", "50"]},
        )
        system = SimpleNamespace(all_screenscraper_ids=[105])
        assert get_all_family_system_ids(system) == {"105", "31", "114", "50"}

    def test_real_families_up_walk_blast_radius(self):
        """Vendored tree: net-new searchable ids match the audit's measurement."""
        from types import SimpleNamespace

        cases = {
            109: {109, 2},  # sg1000 -> megadrive parent (sole child)
            108: {4, 107, 108, 110, 202},  # sufami
            114: {31, 105, 114, 50},  # pcecd
            105: {31, 105, 114, 50},  # sgfx
            10: {9, 10, 127, 128},  # gbc -> gameboy parent + siblings
            123: {135, 123, 136, 137, 290},  # scummvm
        }
        for mapped, expected in cases.items():
            expanded = set(expand_system_ids(SimpleNamespace(all_screenscraper_ids=[mapped])))
            assert expanded == expected, f"id {mapped}: {expanded} != {expected}"


class TestGetAllFamilySystemIds:
    """Tests for get_all_family_system_ids, which returns full descendant closure."""

    def test_arcade_includes_all_descendants_without_cap(self):
        from types import SimpleNamespace

        system = SimpleNamespace(all_screenscraper_ids=[75, 158])
        family_ids = get_all_family_system_ids(system)
        assert "75" in family_ids
        assert "158" in family_ids
        # Child systems under arcade
        assert "151" in family_ids  # Capcom Classics
        assert "148" in family_ids  # Irem Classics
        assert "142" in family_ids  # Neo Geo
        # Unlike expand_system_ids which is capped at 4 descendants,
        # get_all_family_system_ids returns all of them (60+)
        assert len(family_ids) > 20

    def test_foreign_systems_excluded(self):
        from types import SimpleNamespace

        system = SimpleNamespace(all_screenscraper_ids=[75, 158])
        family_ids = get_all_family_system_ids(system)
        assert "20" not in family_ids  # Sega CD
        assert "1" not in family_ids   # Mega Drive
        assert "3" not in family_ids   # NES

    def test_descendant_closure_across_generations(self, monkeypatch):
        from types import SimpleNamespace

        monkeypatch.setattr(
            ssmod,
            "_SYSTEM_FAMILIES",
            {"800": ["801", "802"], "801": ["803"], "803": ["804"]},
        )
        system = SimpleNamespace(all_screenscraper_ids=[800])
        assert get_all_family_system_ids(system) == {"800", "801", "802", "803", "804"}

    def test_unknown_system_returns_only_self(self):
        from types import SimpleNamespace

        system = SimpleNamespace(all_screenscraper_ids=[999])
        assert get_all_family_system_ids(system) == {"999"}

    def test_empty_system_returns_empty_set(self):
        from types import SimpleNamespace

        system = SimpleNamespace(all_screenscraper_ids=[])
        assert get_all_family_system_ids(system) == set()


class TestSubtitleGuardCase:
    """Regression: the subtitle guard compares case-insensitively."""

    def test_identical_title_with_subtitle_not_filtered(self):
        identity = "Classic Mario World 2 - The Great Alliance"
        assert not _subtitle_guard(identity, [normalize_name(identity)])

    def test_missing_subtitle_still_filtered(self):
        assert _subtitle_guard(
            "Bionic Commando: Elite Forces",
            [normalize_name("Bionic Commando")],
        )



@pytest.mark.django_db
class TestNameSearch:
    """Tests for name search functionality in ScreenScraperLookupService."""

    def test_lookup_falls_back_to_name_search(self, mock_system):
        """When CRC and romnom fail, name search is tried."""
        service = ScreenScraperLookupService()
        mock_client = MagicMock()
        mock_client.has_credentials.return_value = True
        mock_client.search_by_crc.return_value = None
        mock_client.search_by_romnom.return_value = None
        mock_client.search_game.return_value = [
            {"id": 789, "name": "Fire Emblem", "system_id": 12},
        ]

        with patch.object(service, "_get_client", return_value=mock_client):
            with patch(
                "library.metadata.screenscraper._get_search_variants",
                return_value=["Fire Emblem"],
            ):
                result = service.lookup(
                    system=mock_system,
                    crc32="12345678",
                    file_path="/roms/fire_emblem.gba",
                    game_name="Fire Emblem",
                )

        assert result is not None
        assert result.name == "Fire Emblem"
        assert result.screenscraper_id == 789
        mock_client.search_game.assert_called()

    def test_name_search_requires_minimum_confidence(self, mock_system):
        """With rescues disabled, sub-floor candidates stay unmatched
        (P3e alias-trust intentionally accepts the single-exact case when
        the flag is on)."""
        from library.models import Setting

        Setting.set("matching_rescue_enabled", False)
        service = ScreenScraperLookupService()
        mock_client = MagicMock()
        mock_client.has_credentials.return_value = True
        mock_client.search_by_crc.return_value = None
        mock_client.search_by_romnom.return_value = None
        # Return a game with very different name
        mock_client.search_game.return_value = [
            {"id": 789, "name": "Completely Different Game", "system_id": 12},
        ]

        with patch.object(service, "_get_client", return_value=mock_client):
            with patch(
                "library.metadata.screenscraper._get_search_variants",
                return_value=["Fire Emblem"],
            ):
                result = service.lookup(
                    system=mock_system,
                    crc32="",
                    file_path="",
                    game_name="Fire Emblem",
                )

        # Should not match due to low confidence
        assert result is None
        Setting.objects.filter(key="matching_rescue_enabled").delete()

    def test_name_search_uses_variants(self, mock_system):
        """Name search tries multiple search variants."""
        service = ScreenScraperLookupService()
        mock_client = MagicMock()
        mock_client.has_credentials.return_value = True
        mock_client.search_by_crc.return_value = None
        mock_client.search_by_romnom.return_value = None
        # First variant fails, second succeeds
        mock_client.search_game.side_effect = [
            [],  # "Legend of Zelda, The" returns nothing
            [
                {"id": 123, "name": "The Legend of Zelda", "system_id": 12}
            ],  # "Legend of Zelda"
        ]

        with (
            patch.object(service, "_get_client", return_value=mock_client),
            patch(
                "library.metadata.screenscraper._get_search_variants",
                return_value=["Legend of Zelda, The", "Legend of Zelda"],
            ),
            patch(
                "library.lookup.screenscraper.expand_system_ids",
                return_value=[12],
            ),
        ):
            result = service.lookup(
                system=mock_system,
                crc32="",
                file_path="",
                game_name="The Legend of Zelda",
            )

        assert result is not None
        assert result.screenscraper_id == 123
        # Should have tried both variants
        assert mock_client.search_game.call_count == 2

    def test_name_search_bypasses_app_cache(self, mock_system):
        """Name search is not served from the app-level cache: a seeded
        name-cache row is ignored and the client is queried."""
        _save_to_cache(
            "name",
            "advance wars",
            12,
            {"id": 456, "name": "Advance Wars", "confidence": 0.72},
        )

        service = ScreenScraperLookupService()
        mock_client = MagicMock()
        mock_client.has_credentials.return_value = True
        mock_client.search_game.return_value = [
            {"id": 456, "name": "Advance Wars", "system_id": 12},
        ]

        with (
            patch.object(service, "_get_client", return_value=mock_client),
            patch(
                "library.metadata.screenscraper._get_search_variants",
                return_value=["Advance Wars"],
            ),
            patch(
                "library.lookup.screenscraper.expand_system_ids",
                return_value=[12],
            ),
        ):
            result = service.lookup(system=mock_system, game_name="Advance Wars")

        assert result is not None
        assert result.screenscraper_id == 456
        assert result.confidence == 1.0
        assert result.match_type == "name"
        mock_client.search_game.assert_called_once_with("Advance Wars", 12)

    def test_name_search_cold_and_warm_choose_best_system(self):
        """Repeated searches keep choosing the same best system."""
        system = MagicMock()
        system.slug = "multi-system"
        system.archive_as_rom = False
        system.all_screenscraper_ids = [12, 13]

        service = ScreenScraperLookupService()
        mock_client = MagicMock()
        mock_client.has_credentials.return_value = True

        def search_game(_variant, system_id):
            if system_id == 12:
                return [{"id": 120, "name": "Target Game Deluxe", "system_id": 12}]
            return [{"id": 130, "name": "Target Game", "system_id": 13}]

        mock_client.search_game.side_effect = search_game
        with (
            patch.object(service, "_get_client", return_value=mock_client),
            patch(
                "library.metadata.screenscraper._get_search_variants",
                return_value=["Target Game"],
            ),
            patch(
                "library.lookup.screenscraper.expand_system_ids",
                return_value=[12, 13],
            ),
        ):
            cold = service.lookup(system=system, game_name="Target Game")

        assert cold is not None
        assert cold.screenscraper_id == 130
        assert cold.match_type == "name"
        assert cold.matched_system_id == 13
        assert cold.confidence == 1.0
        assert mock_client.search_game.call_count == 2

        mock_client.reset_mock()
        mock_client.has_credentials.return_value = True
        mock_client.search_game.side_effect = search_game
        with (
            patch.object(service, "_get_client", return_value=mock_client),
            patch(
                "library.metadata.screenscraper._get_search_variants",
                return_value=["Target Game"],
            ),
            patch(
                "library.lookup.screenscraper.expand_system_ids",
                return_value=[12, 13],
            ),
        ):
            warm = service.lookup(system=system, game_name="Target Game")

        assert warm is not None
        assert warm.screenscraper_id == cold.screenscraper_id
        assert warm.match_type == cold.match_type == "name"
        assert warm.matched_system_id == cold.matched_system_id == 13
        assert warm.confidence == cold.confidence == 1.0
        assert mock_client.search_game.call_count == 2

    def test_allow_name_search_false_limits_to_crc_and_romnom(self, mock_system):
        """allow_name_search=False runs the exact CRC/romnom phases only."""
        service = ScreenScraperLookupService()
        mock_client = MagicMock()
        mock_client.has_credentials.return_value = True
        mock_client.search_by_crc.return_value = None
        mock_client.search_game.return_value = [
            {"id": 999, "name": "Tetris", "system_id": 12},
        ]

        with patch.object(service, "_get_client", return_value=mock_client):
            assert (
                service.lookup(
                    system=mock_system,
                    game_name="Tetris",
                    allow_name_search=False,
                )
                is None
            )
            assert (
                service.lookup(
                    system=mock_system,
                    crc32="12345678",
                    game_name="Tetris",
                    allow_name_search=False,
                )
                is None
            )
            mock_client.search_game.assert_not_called()

            mock_client.search_by_crc.return_value = {
                "id": 123,
                "name": "Advance Wars",
                "system_id": 12,
            }
            crc_result = service.lookup(
                system=mock_system,
                crc32="87654321",
                game_name="Tetris",
                allow_name_search=False,
            )

        assert crc_result is not None
        assert crc_result.match_type == "crc32"

    def test_lookup_only_with_game_name(self, mock_system):
        """Lookup works with only game_name provided (no CRC or file_path)."""
        service = ScreenScraperLookupService()
        mock_client = MagicMock()
        mock_client.has_credentials.return_value = True
        mock_client.search_game.return_value = [
            {"id": 999, "name": "Tetris", "system_id": 12},
        ]

        with patch.object(service, "_get_client", return_value=mock_client):
            with patch(
                "library.metadata.screenscraper._get_search_variants",
                return_value=["Tetris"],
            ):
                result = service.lookup(
                    system=mock_system,
                    game_name="Tetris",
                )

        assert result is not None
        assert result.screenscraper_id == 999


@pytest.mark.django_db
class TestAggregateNameSearch:
    """Aggregate-and-rank acceptance across variants and system IDs."""

    def test_exact_on_later_variant_wins_over_early_substring(self, mock_system):
        """All variants run; a later exact match beats an early 0.85."""
        service = ScreenScraperLookupService()
        mock_client = MagicMock()
        mock_client.has_credentials.return_value = True

        def search_game(variant, system_id):
            if variant == "Target Game Deluxe":
                return [{"id": 1, "name": "Target Game Deluxe", "system_id": 12}]
            return [{"id": 2, "name": "Target Game", "system_id": 12}]

        mock_client.search_game.side_effect = search_game
        with (
            patch.object(service, "_get_client", return_value=mock_client),
            patch(
                "library.metadata.screenscraper._get_search_variants",
                return_value=["Target Game Deluxe", "Target Game"],
            ),
            patch(
                "library.lookup.screenscraper.expand_system_ids",
                return_value=[12],
            ),
        ):
            result = service.lookup(system=mock_system, game_name="Target Game")

        assert result is not None
        assert result.screenscraper_id == 2
        assert result.confidence == 1.0
        assert mock_client.search_game.call_count == 2

    def test_runner_up_margin_rejects_close_duplicate(self):
        """A 0.85 top with a 0.75 runner-up (within 0.15) is rejected."""
        system = MagicMock()
        system.slug = "multi"
        system.archive_as_rom = False
        system.all_screenscraper_ids = [12, 13]

        service = ScreenScraperLookupService()
        mock_client = MagicMock()
        mock_client.has_credentials.return_value = True

        def search_game(_variant, system_id):
            if system_id == 12:
                return [{"id": 1, "name": "Legend Quest Saga Deluxe", "system_id": 12}]
            return [
                {"id": 2, "name": "Quest Saga Legend Chronicles", "system_id": 13}
            ]

        mock_client.search_game.side_effect = search_game
        with (
            patch.object(service, "_get_client", return_value=mock_client),
            patch(
                "library.metadata.screenscraper._get_search_variants",
                return_value=["Legend Quest Saga"],
            ),
            patch(
                "library.lookup.screenscraper.expand_system_ids",
                return_value=[12, 13],
            ),
        ):
            result = service.lookup(system=system, game_name="Legend Quest Saga")

        assert result is None

    def test_sub_exact_tie_across_game_ids_rejected(self, mock_system):
        """Two different games tied below 1.0 are both rejected."""
        service = ScreenScraperLookupService()
        mock_client = MagicMock()
        mock_client.has_credentials.return_value = True
        mock_client.search_game.return_value = [
            {"id": 1, "name": "Target Game Deluxe", "system_id": 12},
            {"id": 2, "name": "Target Game Remix", "system_id": 12},
        ]
        with (
            patch.object(service, "_get_client", return_value=mock_client),
            patch(
                "library.metadata.screenscraper._get_search_variants",
                return_value=["Target Game"],
            ),
            patch(
                "library.lookup.screenscraper.expand_system_ids",
                return_value=[12],
            ),
        ):
            result = service.lookup(system=mock_system, game_name="Target Game")

        assert result is None

    def test_exact_tie_duplicate_entries_accept_lowest_id(self, mock_system):
        """Two SS duplicate entries at 1.0 resolve to the lowest SS id."""
        service = ScreenScraperLookupService()
        mock_client = MagicMock()
        mock_client.has_credentials.return_value = True
        mock_client.search_game.return_value = [
            {"id": 117137, "name": "Wonderland", "system_id": 12},
            {"id": 117136, "name": "Wonderland", "system_id": 12},
        ]
        with (
            patch.object(service, "_get_client", return_value=mock_client),
            patch(
                "library.metadata.screenscraper._get_search_variants",
                return_value=["Wonderland"],
            ),
            patch(
                "library.lookup.screenscraper.expand_system_ids",
                return_value=[12],
            ),
        ):
            result = service.lookup(system=mock_system, game_name="Wonderland")

        assert result is not None
        assert result.screenscraper_id == 117136

    def test_family_id_exact_match_accepted(self, mock_system):
        """Rule (a): an exact hit on a family-only system is accepted."""
        service = ScreenScraperLookupService()
        mock_client = MagicMock()
        mock_client.has_credentials.return_value = True

        def search_game(_variant, system_id):
            if system_id == 278:
                return [{"id": 500, "name": "Target Game", "system_id": 278}]
            return []

        mock_client.search_game.side_effect = search_game
        with (
            patch.object(service, "_get_client", return_value=mock_client),
            patch(
                "library.metadata.screenscraper._get_search_variants",
                return_value=["Target Game"],
            ),
            patch(
                "library.lookup.screenscraper.expand_system_ids",
                return_value=[12, 278],
            ),
        ):
            result = service.lookup(system=mock_system, game_name="Target Game")

        assert result is not None
        assert result.screenscraper_id == 500
        assert result.matched_system_id == 278
        assert result.confidence == 1.0


    def test_notgame_placeholder_is_not_a_candidate(self, mock_system):
        """ZZZ(notgame) entries never win, even when the title fits."""
        service = ScreenScraperLookupService()
        mock_client = MagicMock()
        mock_client.has_credentials.return_value = True
        mock_client.search_game.return_value = [
            {"id": 175790, "name": "ZZZ(notgame):Target Game", "system_id": 12},
        ]
        with (
            patch.object(service, "_get_client", return_value=mock_client),
            patch(
                "library.metadata.screenscraper._get_search_variants",
                return_value=["Target Game"],
            ),
        ):
            result = service.lookup(system=mock_system, game_name="Target Game")

        assert result is None

    def test_exact_candidate_with_subtitle_not_guard_blocked(self, mock_system):
        """Regression: identical subtitles must not be dropped by case."""
        title = "Classic Mario World 2 - The Great Alliance"
        service = ScreenScraperLookupService()
        mock_client = MagicMock()
        mock_client.has_credentials.return_value = True
        mock_client.search_game.return_value = [
            {"id": 192745, "name": title, "system_id": 12},
        ]
        with (
            patch.object(service, "_get_client", return_value=mock_client),
            patch(
                "library.metadata.screenscraper._get_search_variants",
                return_value=[title],
            ),
        ):
            result = service.lookup(system=mock_system, game_name=title)

        assert result is not None
        assert result.screenscraper_id == 192745


class TestScreenScraperApiRobustness:
    """Tests for API robustness: empty/invalid JSON and error handling."""

    @pytest.fixture
    def client(self):
        """Create a client instance with mocked credentials."""
        import os
        env_vars = {
            "SCREENSCRAPER_USER": "testuser",
            "SCREENSCRAPER_PASSWORD": "testpass",
            "SCREENSCRAPER_DEVID": "testdevid",
            "SCREENSCRAPER_DEVPASSWORD": "testdevpass",
        }
        with (
            patch.dict(os.environ, env_vars),
            patch("library.metadata.screenscraper.get_pause_until", return_value=None),
            patch("library.metadata.screenscraper.Setting.get", return_value=None),
        ):
            yield ScreenScraperClient()

    def test_search_game_handles_empty_response(self, client):
        """search_game returns empty list when API returns empty dict."""
        with patch.object(client, "_make_request", return_value={}):
            results = client.search_game("Nonexistent", 12)
            assert results == []

    def test_search_game_propagates_connection_failure(self, client):
        """An outage must reach the worker retry handler, not become a miss."""
        with patch.object(
            client,
            "_make_request",
            side_effect=requests.exceptions.ConnectionError("Connection failed"),
        ):
            with pytest.raises(requests.exceptions.ConnectionError):
                client.search_game("Any Game", 12)


class TestMatchRecoveryRegressions:
    """Regressions for games findable on ScreenScraper but missed by the pipeline."""

    def test_romnom_inner_filename_with_exclamation_marks(self):
        """Only the first ! separates archive from content (Punch Out!! regression)."""
        path = "/roms/nes/Punch Out!! (U) [b1].zip!Punch Out!! (U) [b1].nes"
        assert _extract_romnom(path, archive_as_rom=False) == "Punch Out!! (U) [b1].nes"

    def test_romnom_nested_inner_path_with_exclamation_marks(self):
        """Nested inner path still resolves to the full inner filename."""
        path = "/roms/nes/pack.7z!Top 100/Sanrio World Smash Ball! (Japan).sfc"
        assert (
            _extract_romnom(path, archive_as_rom=False)
            == "Sanrio World Smash Ball! (Japan).sfc"
        )

    def test_make_request_treats_400_as_no_match(self):
        """ScreenScraper answers 400 for values it has no entry for; the lookup
        chain must fall through to romnom/name search, not abort."""
        import os

        env_vars = {
            "SCREENSCRAPER_USER": "testuser",
            "SCREENSCRAPER_PASSWORD": "testpass",
        }
        response = MagicMock()
        response.status_code = 400
        with (
            patch.dict(os.environ, env_vars),
            patch(
                "library.metadata.screenscraper.get_pause_until", return_value=None
            ),
            patch("library.metadata.screenscraper.Setting.get", return_value=None),
            patch("library.metadata.screenscraper.requests.get", return_value=response),
        ):
            client = ScreenScraperClient()
            assert client._make_request("jeuInfos", {"crc": "0E298455"}) == {}

    def test_search_by_crc_returns_none_on_400(self):
        """A 400 on the CRC phase must yield None (not raise) so romnom and
        name search still run for the same game."""
        import os

        env_vars = {
            "SCREENSCRAPER_USER": "testuser",
            "SCREENSCRAPER_PASSWORD": "testpass",
        }
        response = MagicMock()
        response.status_code = 400
        with (
            patch.dict(os.environ, env_vars),
            patch(
                "library.metadata.screenscraper.get_pause_until", return_value=None
            ),
            patch("library.metadata.screenscraper.Setting.get", return_value=None),
            patch("library.metadata.screenscraper.requests.get", return_value=response),
        ):
            client = ScreenScraperClient()
            assert client.search_by_crc("0E298455", 3) is None
            assert client.search_game("Classic Concentration", 3) == []

    def test_catalog_prefix_normalized_away(self):
        """Channel F catalog labels must not trip number-consistency checks."""
        assert (
            calculate_match_score("Alien Invasion", "Videocart-26 - Alien Invasion")
            == 1.0
        )
        assert calculate_match_score("Alien Invasion", "Alien Invasion 2") == 0.0

    def test_candidate_score_accepts_catalog_prefixed_name(self):
        """The Channel F 'Alien Invasion' -> 'Videocart-26 - Alien Invasion' match."""
        result = {
            "id": 46519,
            "name": "Videocart-26 - Alien Invasion",
            "all_names": ["Videocart-26: Alien Invasion"],
            "system_id": 80,
        }
        assert _candidate_score("Alien Invasion", "Alien Invasion", result) == 1.0

    def test_romnom_rejects_base_title_for_hack(self):
        """ScreenScraper's romnom endpoint can return Punch-Out!! for the
        distinct Punch-Out!! Kirby hack; it must remain unmatched."""
        assert not _romnom_title_matches(
            "Punch-Out!! Kirby (Hack).nes",
            {"name": "Punch-Out!!"},
        )

    def test_romnom_accepts_punctuation_and_tag_variants(self):
        """The strict guard still accepts the real Punch-Out!! ROM lookup."""
        assert _romnom_title_matches(
            "Punch Out!! (U) [b1].nes",
            {"name": "Punch-Out!!"},
        )

    def test_romnom_accepts_compound_spacing_and_rejects_notgame(self):
        """ScreenScraper's spelling does not change identity, but non-game
        placeholder entries must never be accepted."""
        assert _romnom_title_matches(
            "Megaman Xtreme 2 (USA).gbc",
            {"name": "Mega Man Xtreme 2"},
        )
        assert not _romnom_title_matches(
            "Hello, World! Demo (PD).vb",
            {"name": "ZZZ(notgame):Hello, World! Demo"},
        )
        assert not _romnom_title_matches(
            "Mom, My Ears are Bleeding! (E).nes",
            {"name": "ZZZ(notgame):#NONGAME"},
        )
        assert _is_notgame_placeholder(["ZZZ(notgame):#NONGAME"])
        assert not _is_notgame_placeholder(["Target Game"])

    def test_notgame_entry_with_clean_alias_scores_on_clean_names(self):
        """A ZZZ-prefixed alias beside clean names no longer kills the entry;
        the clean names drive scoring (P0-A inverse case, pcecd Snatcher).
        The 64DD BIOS romnom still rejects because its clean title does not
        match the ROM filename identity."""
        assert not _is_notgame_placeholder(
            ["ZZZ(notgame):Nintendo 64DD IPL", "Nintendo 64DD IPL"]
        )
        assert not _romnom_title_matches(
            "64DD IPL JPN DISK cartridge v1.1.n64",
            {"name": "ZZZ(notgame):Nintendo 64DD IPL", "all_names": ["Nintendo 64DD IPL"]},
        )


@pytest.mark.django_db
class TestPlaceholderGates:
    """P0-A: placeholder-named entries must never be accepted, and entries
    with clean names beside a ZZZ alias must still win on the clean names."""

    def test_try_crc_rejects_placeholder_named_result(self, mock_system):
        """A crc hit whose only name is a ZZZ(notgame) placeholder is not a match."""
        service = ScreenScraperLookupService()
        mock_client = MagicMock()
        mock_client.has_credentials.return_value = True
        mock_client.search_by_crc.return_value = {
            "id": 175790,
            "name": "ZZZ(notgame):Target Game",
            "system_id": 12,
        }
        with patch.object(service, "_get_client", return_value=mock_client):
            assert service._try_crc("deadbeef", 12) is None

    def test_check_cache_dead_for_cached_placeholder_match(self):
        """Cached crc/romnom rows whose game name is a placeholder are dead on
        read: the pipeline re-queries instead of resurrecting the placeholder."""
        _save_to_cache(
            "crc",
            "cafebabe",
            12,
            {"id": 175790, "name": "ZZZ(notgame):Target Game", "system_id": 12},
        )
        found, result = _check_cache("crc", "cafebabe", 12)
        assert found is False
        assert result is None

    def test_clean_alias_beside_zzz_alias_wins(self, mock_system):
        """pcecd Snatcher: a ZZZ-prefixed wor alias beside clean names must not
        kill the entry; the clean names score 1.0 and it is accepted."""
        service = ScreenScraperLookupService()
        mock_client = MagicMock()
        mock_client.has_credentials.return_value = True
        mock_client.search_game.return_value = [
            {
                "id": 42222,
                "name": "ZZZ(notgame):Snatcher",
                "all_names": ["ZZZ(notgame):Snatcher (wor)", "Snatcher"],
                "system_id": 12,
            },
        ]
        with (
            patch.object(service, "_get_client", return_value=mock_client),
            patch(
                "library.metadata.screenscraper._get_search_variants",
                return_value=["Snatcher"],
            ),
        ):
            result = service.lookup(system=mock_system, game_name="Snatcher")

        assert result is not None
        assert result.screenscraper_id == 42222
        assert result.confidence == 1.0

    def test_entry_with_only_placeholder_names_is_skipped(self, mock_system):
        """An entry whose every name carries the placeholder prefix is skipped."""
        service = ScreenScraperLookupService()
        mock_client = MagicMock()
        mock_client.has_credentials.return_value = True
        mock_client.search_game.return_value = [
            {
                "id": 42,
                "name": "ZZZ(notgame):Snatcher",
                "all_names": ["ZZZ(notgame):Snatcher (wor)"],
                "system_id": 12,
            },
        ]
        with (
            patch.object(service, "_get_client", return_value=mock_client),
            patch(
                "library.metadata.screenscraper._get_search_variants",
                return_value=["Snatcher"],
            ),
        ):
            result = service.lookup(system=mock_system, game_name="Snatcher")

        assert result is None


@pytest.mark.django_db
class TestForeignSystemCacheBug:
    """P0-B: cached matches must remember the system the match belongs to."""

    def test_cache_reconstruction_carries_matched_system_id(self):
        """A row cached under system 2 but matched on system 21 reconstructs
        with system_id 21 so _system_ok can reject it."""
        _save_to_cache(
            "crc",
            "feedface",
            2,
            {"id": 5, "name": "Foreign Game", "system_id": 21},
        )
        found, result = _check_cache("crc", "feedface", 2)
        assert found is True
        assert result is not None
        assert result["system_id"] == 21

    def test_cache_reconstruction_falls_back_to_requested_system(self):
        """Rows saved without a matched system id keep the requested system."""
        _save_to_cache("crc", "c0ffee00", 2, {"id": 6, "name": "Same Game"})
        found, result = _check_cache("crc", "c0ffee00", 2)
        assert found is True
        assert result is not None
        assert result["system_id"] == 2

    def test_identity_guard_rejects_base_game_for_extra_words(self):
        """A base game cannot consume an extra meaningful title word."""
        assert _candidate_omits_identity_words(
            "Punch-Out!! Kirby",
            ["Punch-Out!!"],
        )
        assert not _candidate_omits_identity_words(
            "Robot War/Torpedo Alley",
            ["Videocart-13 - Robot War, Torpedo Alley"],
        )


class TestPrefixSubtitleAcceptance:
    """P3a: candidate = identity + subtitle scores on the main title."""

    def test_identity_with_candidate_subtitle_accepts(self):
        assert (
            calculate_match_score("ArduGolf", "ArduGolf - 18-Hole Golf Simulation")
            == 0.85
        )
        assert (
            calculate_match_score("8 Bit Xmas", "8 Bit Xmas - Feat. Santa")
            == 0.85
        )
        assert calculate_match_score("Liberation", "Liberation - Maiden Quest") == 0.85

    def test_sequel_without_subtitle_still_rejected(self):
        """The subtitle rule needs a separator + non-empty subtitle."""
        assert calculate_match_score("Mega Man", "Mega Man 2") == 0.0


class TestGuardRefinements:
    """P3b: scorer/guard refinements."""

    def test_multicart_tokens_ignored_by_number_extraction(self):
        """'<n>-in-1' tokens must not enter the sequel-number sets."""
        assert _extract_numbers("4 in 1") == set()
        assert _extract_numbers("150 in 1") == set()
        assert _extract_numbers("mega man 2") == {2}

    def test_multicart_consistency_not_zeroed(self):
        assert calculate_match_score("4-in-1 Funplay", "4 in 1 Funplay Joust") > 0.6

    def test_parenthetical_alt_title_excluded_from_omits_guard(self):
        assert not _candidate_omits_identity_words(
            "Donkey Kong (Donkey Kong '94)", ["Donkey Kong"]
        )
        assert _candidate_omits_identity_words(
            "Donkey Kong Jungle", ["Donkey Kong"]
        )

    def test_grouping_suffixes_ignored(self):
        """naomi 'Initial D Arcade Stage Series' == 'Initial D Arcade Stage'."""
        assert calculate_match_score(
            "Initial D Arcade Stage Series", "Initial D Arcade Stage"
        ) == 1.0
        assert not _candidate_omits_identity_words(
            "Puzzle Collection", ["Puzzle"]
        )

    def test_roman_numeral_equivalence_ys3(self):
        """MSX Ys III: normalize_name maps iii -> 3 on both sides."""
        assert normalize_name("Ys III") == "ys 3"
        assert calculate_match_score("Ys III", "Ys 3") == 1.0


class TestRomanizationFolds:
    """P3c: JP-token folds raise scores, never lower them."""

    def test_uo_ou_folds(self):
        assert calculate_match_score("Madoh Monogatari", "Madou Monogatari") == 1.0

    def test_compound_squash(self):
        assert calculate_match_score("Daimakaimura", "Daimakai Mura") == 1.0

    def test_fuzzy_romanization_tokens(self):
        assert calculate_match_score("Gryps Senki", "Grips Senki") >= 0.85

    def test_folds_never_lower_exact(self):
        assert calculate_match_score("Metal Slug", "Metal Slug") == 1.0
        assert calculate_match_score("Mega Man", "Mega Man 2") == 0.0
        assert calculate_match_score("Tetris", "Pac-Man") == 0.0


@pytest.mark.django_db
class TestTieExpansion:
    """P3d: sub-1.0 ties expand when titles share a base [flagged]."""

    def test_version_sibling_tie_picks_lowest_id(self):
        pool = [
            {"id": 10, "name": "Melty Blood Ver.A", "system_id": 12, "confidence": 0.85},
            {"id": 9, "name": "Melty Blood Ver.B", "system_id": 12, "confidence": 0.85},
        ]
        assert _rank_candidates(pool, {"12"}, {"12"})["id"] == 9
        assert _rank_candidates(pool[::-1], {"12"}, {"12"})["id"] == 9

    def test_cross_mapped_tie_prefers_mapped_system(self):
        pool = [
            {"id": 5, "name": "Checkers", "system_id": 99, "confidence": 0.85},
            {"id": 7, "name": "Checkers", "system_id": 12, "confidence": 0.85},
        ]
        assert _rank_candidates(pool, {"12"}, {"12", "99"})["system_id"] == 12

    def test_tie_below_threshold_still_rejects(self):
        pool = [
            {"id": 1, "name": "Alpha", "system_id": 12, "confidence": 0.84},
            {"id": 2, "name": "Alpha", "system_id": 12, "confidence": 0.84},
        ]
        assert _rank_candidates(pool, {"12"}, {"12"}) is None

    def test_different_base_tie_still_rejects(self):
        pool = [
            {"id": 1, "name": "Target Game Deluxe", "system_id": 12, "confidence": 0.85},
            {"id": 2, "name": "Target Game Remix", "system_id": 12, "confidence": 0.85},
        ]
        assert _rank_candidates(pool, {"12"}, {"12"}) is None

    def test_flag_off_restores_old_rejection(self):
        from library.models import Setting

        Setting.set("matching_tie_expand_enabled", False)
        pool = [
            {"id": 10, "name": "Melty Blood Ver.A", "system_id": 12, "confidence": 0.85},
            {"id": 9, "name": "Melty Blood Ver.B", "system_id": 12, "confidence": 0.85},
        ]
        assert _rank_candidates(pool, {"12"}, {"12"}) is None
        Setting.objects.filter(key="matching_tie_expand_enabled").delete()


@pytest.mark.django_db
class TestRescueRules:
    """P3e: structural rescues, gated by matching_rescue_enabled."""

    def _service_with_results(self, results, variants):
        service = ScreenScraperLookupService()
        mock_client = MagicMock()
        mock_client.has_credentials.return_value = True

        def search_side_effect(name, system_id=None):
            if system_id is None:
                return []
            return results

        mock_client.search_game.side_effect = search_side_effect
        with (
            patch.object(service, "_get_client", return_value=mock_client),
            patch(
                "library.metadata.screenscraper._get_search_variants",
                return_value=variants,
            ),
            patch(
                "library.lookup.screenscraper.expand_system_ids",
                return_value=[12],
            ),
        ):
            yield service, mock_client

    def test_rare_token_rescue_accepts_unique_mapped_candidate(self, mock_system):
        """'Magical Quest Starring Mickey' -> SS 'Magical Quest': the omits
        guard blocks ranking, the rare shared tokens rescue it at 0.85."""
        for service, mock_client in self._service_with_results(
            [
                {
                    "id": 8837,
                    "name": "Magical Quest",
                    "all_names": [],
                    "system_id": 12,
                }
            ],
            variants=["Magical Quest Starring Mickey"],
        ):
            result = service.lookup(
                system=mock_system, game_name="Magical Quest Starring Mickey"
            )
        assert result is not None
        assert result.screenscraper_id == 8837
        assert result.confidence == 0.85

    def test_low_score_candidates_do_not_rescue(self, mock_system):
        """Rescues never fire below the 0.60 score floor."""
        for service, mock_client in self._service_with_results(
            [
                {
                    "id": 8837,
                    "name": "Akumajou Dracula X - Chi No Rondo",
                    "all_names": [],
                    "system_id": 12,
                }
            ],
            variants=["Rondo of Blood"],
        ):
            result = service.lookup(system=mock_system, game_name="Rondo of Blood")
        assert result is None

    def test_rescue_flag_off_rejects(self, mock_system):
        from library.models import Setting

        Setting.set("matching_rescue_enabled", False)
        for service, mock_client in self._service_with_results(
            [
                {
                    "id": 8837,
                    "name": "Magical Quest",
                    "all_names": [],
                    "system_id": 12,
                }
            ],
            variants=["Magical Quest Starring Mickey"],
        ):
            result = service.lookup(
                system=mock_system, game_name="Magical Quest Starring Mickey"
            )
        Setting.objects.filter(key="matching_rescue_enabled").delete()
        assert result is None

    def test_zero_score_alias_not_trusted(self, mock_system):
        """'Puyo Puyo 2' vs SS 'Puyo Puyo Tsuu' scores 0.0; alias-trust no
        longer accepts zero-score candidates."""
        for service, mock_client in self._service_with_results(
            [
                {
                    "id": 38514,
                    "name": "Puyo Puyo Tsuu",
                    "all_names": [],
                    "system_id": 12,
                }
            ],
            variants=["Puyo Puyo 2"],
        ):
            result = service.lookup(system=mock_system, game_name="Puyo Puyo 2")
        assert result is None

    def test_composite_title_split_rescue(self, mock_system):
        """cd32 Qwak lives inside 'Alien Breed Special Edition And Qwak'."""
        for service, mock_client in self._service_with_results(
            [
                {
                    "id": 106286,
                    "name": "Alien Breed Special Edition And Qwak",
                    "all_names": [],
                    "system_id": 12,
                }
            ],
            variants=["Qwak"],
        ):
            result = service.lookup(system=mock_system, game_name="Qwak")
        assert result is not None
        assert result.screenscraper_id == 106286
