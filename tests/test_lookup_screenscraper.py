"""Tests for ScreenScraperLookupService."""

import pytest
from unittest.mock import MagicMock, patch

from library.lookup.screenscraper import (
    ScreenScraperLookupService,
    _extract_romnom,
    _check_cache,
    _save_to_cache,
    normalize_name,
    calculate_match_score,
    _find_best_match,
)
from library.lookup.base import LookupResult


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
            normalize_name("Pac-Man: Championship Edition")
            == "pacman championship edition"
        )

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
        """Substring match returns 0.85."""
        score = calculate_match_score("Mario", "Super Mario World")
        assert score == 0.85

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


class TestFindBestMatch:
    """Tests for _find_best_match function."""

    def test_finds_exact_match(self):
        """Finds exact match in results."""
        results = [
            {"id": 1, "name": "Super Mario World"},
            {"id": 2, "name": "Super Mario Bros"},
        ]
        best = _find_best_match("Super Mario World", results)
        assert best is not None
        assert best["id"] == 1
        assert best["score"] == 1.0

    def test_considers_all_names(self):
        """Considers all_names list in results."""
        results = [
            {
                "id": 1,
                "name": "Legend of Zelda, The",
                "all_names": ["The Legend of Zelda", "Zelda no Densetsu"],
            },
        ]
        best = _find_best_match("The Legend of Zelda", results)
        assert best is not None
        assert best["id"] == 1
        assert best["score"] == 1.0

    def test_returns_best_of_multiple_candidates(self):
        """Returns the best match among multiple candidates."""
        results = [
            {"id": 1, "name": "Mega Man X"},
            {"id": 2, "name": "Mega Man 2"},
            {"id": 3, "name": "Mega Man X2"},
        ]
        best = _find_best_match("Mega Man X2", results)
        assert best is not None
        assert best["id"] == 3

    def test_returns_none_for_empty_results(self):
        """Returns None for empty results list."""
        best = _find_best_match("Test Game", [])
        assert best is None


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
        """Name search only matches if confidence >= 0.6."""
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

        with patch.object(service, "_get_client", return_value=mock_client):
            with patch(
                "library.metadata.screenscraper._get_search_variants",
                return_value=["Legend of Zelda, The", "Legend of Zelda"],
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

    def test_name_search_caches_results(self, mock_system):
        """Name search results are cached."""
        # Save a cached name search result
        _save_to_cache("name", "advance wars", 12, {"id": 456, "name": "Advance Wars"})

        service = ScreenScraperLookupService()
        mock_client = MagicMock()
        mock_client.has_credentials.return_value = True

        with patch.object(service, "_get_client", return_value=mock_client):
            result = service._try_name_search("Advance Wars", 12)

        assert result is not None
        assert result.screenscraper_id == 456
        # Client should not be called - result came from cache
        mock_client.search_game.assert_not_called()

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
