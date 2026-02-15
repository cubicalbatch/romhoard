"""ScreenScraper-based ROM identification service.

This lookup service uses ScreenScraper's API to identify ROMs when Hasheous
fails to find a match. It tries CRC lookup first (for regular ROMs), then
falls back to romnom (filename) lookup, and finally name search as last resort.
"""

import logging
import re
import unicodedata
from pathlib import Path
from typing import TYPE_CHECKING, Optional

from .base import LookupResult, LookupService

if TYPE_CHECKING:
    from library.models import System

logger = logging.getLogger(__name__)


def normalize_name(name: str) -> str:
    """Normalize game name for fuzzy matching.

    Args:
        name: Original game name

    Returns:
        Normalized name (lowercase, no punctuation, no articles, ASCII only)
    """
    # Convert to lowercase
    name = name.lower()

    # Unicode normalize - convert accented chars to base form (é -> e)
    name = unicodedata.normalize("NFKD", name)
    name = "".join(c for c in name if not unicodedata.combining(c))

    # Remove common articles
    articles = ["the ", "a ", "an "]
    for article in articles:
        if name.startswith(article):
            name = name[len(article) :]

    # Remove punctuation and special characters
    name = re.sub(r"[^\w\s]", "", name)

    # Normalize whitespace
    name = re.sub(r"\s+", " ", name).strip()

    return name


def calculate_match_score(game_name: str, api_name: str) -> float:
    """Calculate similarity score between two game names.

    Args:
        game_name: Name from our database
        api_name: Name from API response

    Returns:
        Match score (0.0 to 1.0)
    """
    norm_game = normalize_name(game_name)
    norm_api = normalize_name(api_name)

    # Exact match
    if norm_game == norm_api:
        return 1.0

    # Substring match
    if norm_game in norm_api or norm_api in norm_game:
        return 0.85

    # Word overlap scoring
    game_words = set(norm_game.split())
    api_words = set(norm_api.split())

    if not game_words or not api_words:
        return 0.0

    overlap = game_words & api_words
    total = len(game_words | api_words)

    base_score = len(overlap) / total

    # Boost score when base overlap is very low but there's a significant match
    # This helps when names are completely different (e.g., Japanese vs English)
    # but share a distinctive word like "Rondo", "Gradius", "Spriggan"
    # Only boost when base_score < 0.3 (very different names)
    if base_score < 0.3 and len(overlap) >= 1:
        stopwords = {"the", "a", "an", "of", "and", "or", "vs", "no", "to", "ni"}
        significant_overlap = {
            w for w in overlap if len(w) >= 5 and w.lower() not in stopwords
        }
        if significant_overlap:
            # A significant word match is a strong signal - boost to 0.65
            base_score = 0.65

    return base_score


def _find_best_match(game_name: str, results: list[dict]) -> dict | None:
    """Find the best matching result from a list of API results.

    Args:
        game_name: The game name we're searching for
        results: List of result dicts from ScreenScraper API

    Returns:
        Best matching result dict with 'score' added, or None if no good match
    """
    best_match = None
    best_score = 0.0

    for result in results:
        # Check against primary name
        names_to_check = {result.get("name", "")}
        # Add all known regional names
        if "all_names" in result:
            names_to_check.update(result["all_names"])

        # Find best score across all names for this candidate
        candidate_best_score = 0.0
        for name in names_to_check:
            score = calculate_match_score(game_name, name)
            if score > candidate_best_score:
                candidate_best_score = score

        if candidate_best_score > best_score:
            best_score = candidate_best_score
            best_match = result.copy()
            best_match["score"] = candidate_best_score

    return best_match


def _extract_romnom(file_path: str, archive_as_rom: bool) -> str:
    """Extract romnom (filename WITH extension) for ScreenScraper search.

    The romnom is the filename that ScreenScraper uses for matching. Different
    handling is needed based on system type:

    - archive_as_rom systems (arcade): use archive filename → "pacman.zip"
    - Regular systems with archives: use ROM filename inside archive → "Advance Wars.gba"
    - Loose files: use the filename → "Super Mario World.smc"

    This mirrors how CRC works (hash of actual ROM, not archive).

    Args:
        file_path: ROM path, may contain "!" for archive contents
        archive_as_rom: If True, use archive name; else use inner file name
    """
    if archive_as_rom:
        # For arcade systems, always use the archive filename
        # "/roms/arcade/pacman.zip" → "pacman.zip"
        # "/roms/arcade/pacman.zip!pacman/game.rom" → "pacman.zip"
        if "!" in file_path:
            archive_path = file_path.split("!")[0]
            return Path(archive_path).name
        return Path(file_path).name
    elif "!" in file_path:
        # For regular systems with archives, use the inner ROM filename
        # "Advance Wars.zip!Advance Wars.gba" → "Advance Wars.gba"
        return file_path.split("!")[-1].split("/")[-1]
    else:
        # Loose file
        # "Super Mario World.smc" → "Super Mario World.smc"
        return Path(file_path).name


def _check_cache(
    lookup_type: str, lookup_value: str, system_id: int
) -> tuple[bool, dict | None]:
    """Check cache for a ScreenScraper lookup.

    Args:
        lookup_type: Type of lookup ("crc" or "romnom")
        lookup_value: The value to look up
        system_id: ScreenScraper system ID

    Returns:
        Tuple of (found_in_cache, cached_result).
        If found_in_cache is True but cached_result is None, it means
        the lookup was previously done and had no match.
    """
    from library.models import ScreenScraperLookupCache

    try:
        cache_entry = ScreenScraperLookupCache.objects.get(
            lookup_type=lookup_type,
            lookup_value=lookup_value.lower(),
            system_id=system_id,
        )

        if not cache_entry.matched:
            # Known no-match
            logger.debug(
                "ScreenScraper cache hit (no match): %s=%s (system %d)",
                lookup_type,
                lookup_value[:20],
                system_id,
            )
            return True, None

        # Reconstruct result dict
        cached_result = {
            "id": cache_entry.screenscraper_id,
            "name": cache_entry.game_name,
            "system_id": system_id,
        }
        logger.debug(
            "ScreenScraper cache hit (matched): %s=%s -> %s (ID: %s)",
            lookup_type,
            lookup_value[:20],
            cache_entry.game_name,
            cache_entry.screenscraper_id,
        )
        return True, cached_result

    except ScreenScraperLookupCache.DoesNotExist:
        return False, None


def _save_to_cache(
    lookup_type: str,
    lookup_value: str,
    system_id: int,
    result: dict | None,
) -> None:
    """Save lookup result to cache.

    Args:
        lookup_type: Type of lookup ("crc" or "romnom")
        lookup_value: The lookup value
        system_id: ScreenScraper system ID
        result: Result dict with 'id', 'name', or None if no match
    """
    from library.models import ScreenScraperLookupCache

    try:
        # Treat result with no ID as a no-match
        if result is None or not result.get("id"):
            # Cache a no-match
            ScreenScraperLookupCache.objects.update_or_create(
                lookup_type=lookup_type,
                lookup_value=lookup_value.lower(),
                system_id=system_id,
                defaults={"matched": False, "screenscraper_id": None, "game_name": ""},
            )
            logger.debug(
                "ScreenScraper cache saved (no match): %s=%s (system %d)",
                lookup_type,
                lookup_value[:20],
                system_id,
            )
        else:
            # Cache a match
            ScreenScraperLookupCache.objects.update_or_create(
                lookup_type=lookup_type,
                lookup_value=lookup_value.lower(),
                system_id=system_id,
                defaults={
                    "matched": True,
                    "screenscraper_id": result.get("id"),
                    "game_name": result.get("name", ""),
                },
            )
            logger.debug(
                "ScreenScraper cache saved (matched): %s=%s -> %s (ID: %s)",
                lookup_type,
                lookup_value[:20],
                result.get("name", ""),
                result.get("id"),
            )

    except Exception as e:
        # Don't fail the lookup if caching fails
        logger.warning("Failed to save ScreenScraper cache: %s", e)


class ScreenScraperLookupService(LookupService):
    """ScreenScraper-based ROM identification.

    Tries CRC lookup first (for regular ROMs), then romnom (filename), and finally
    name search as last resort. Works for all systems, providing a universal
    fallback when Hasheous fails.
    """

    name = "screenscraper"

    def __init__(self):
        self._client = None

    def _get_client(self):
        """Lazy-load the ScreenScraper client."""
        if self._client is None:
            from library.metadata.screenscraper import ScreenScraperClient

            self._client = ScreenScraperClient()
        return self._client

    def lookup(  # noqa: ARG002
        self,
        system: "System",
        crc32: str = "",
        sha1: str = "",
        md5: str = "",
        file_path: str = "",
        game_name: str = "",
    ) -> Optional[LookupResult]:
        """Look up ROM using ScreenScraper CRC, romnom, or name search.

        For regular systems, tries CRC first, then romnom, then name search.
        For arcade systems (archive_as_rom), skips CRC and goes straight to romnom.

        Args:
            system: Target system
            crc32: CRC32 hash (8 hex chars)
            sha1: SHA1 hash (not used - ScreenScraper uses CRC)
            md5: MD5 hash (not used - ScreenScraper uses CRC)
            file_path: Path to ROM file (for romnom extraction)
            game_name: Game name for name-based search (last resort fallback)

        Returns:
            LookupResult if found, None otherwise
        """
        # Need at least one of CRC, file_path, or game_name
        if not crc32 and not file_path and not game_name:
            return None

        # Check if ScreenScraper credentials are configured
        client = self._get_client()
        if not client.has_credentials():
            logger.debug("ScreenScraper credentials not configured, skipping lookup")
            return None

        # Try all system IDs in order for exact matches (CRC, romnom)
        for system_id in system.all_screenscraper_ids:
            # Phase 1: CRC lookup (skip for arcade systems)
            if crc32 and not system.archive_as_rom:
                result = self._try_crc(crc32, system_id)
                if result:
                    return result

            # Phase 2: Romnom lookup (filename-based fallback)
            if file_path:
                romnom = _extract_romnom(file_path, system.archive_as_rom)
                result = self._try_romnom(romnom, system_id)
                if result:
                    return result

        # Phase 3: Name search (fuzzy matching, last resort)
        # For name search, collect best match across ALL system IDs
        # since fuzzy matching may find better matches on alternate systems
        if game_name:
            best_result = None
            best_confidence = 0.0

            for system_id in system.all_screenscraper_ids:
                result = self._try_name_search(game_name, system_id)
                if result and result.confidence > best_confidence:
                    best_result = result
                    best_confidence = result.confidence

            if best_result:
                return best_result

        return None

    def _try_crc(self, crc32: str, system_id: int) -> Optional[LookupResult]:
        """Try CRC lookup with caching.

        Args:
            crc32: CRC32 hash
            system_id: ScreenScraper system ID

        Returns:
            LookupResult if found, None otherwise
        """
        # Check cache first
        found_in_cache, cached_result = _check_cache("crc", crc32, system_id)
        if found_in_cache:
            if cached_result is None:
                return None
            return self._result_from_dict(cached_result)

        # Make API call
        try:
            client = self._get_client()
            result = client.search_by_crc(crc32, system_id)

            # Cache result
            _save_to_cache("crc", crc32, system_id, result)

            if result:
                return self._result_from_dict(result)
            return None

        except Exception as e:
            logger.debug("ScreenScraper CRC lookup failed for %s: %s", crc32, e)
            return None

    def _try_romnom(self, romnom: str, system_id: int) -> Optional[LookupResult]:
        """Try romnom (filename) lookup with caching.

        Args:
            romnom: Filename for lookup (with extension)
            system_id: ScreenScraper system ID

        Returns:
            LookupResult if found, None otherwise
        """
        # Check cache first
        found_in_cache, cached_result = _check_cache("romnom", romnom, system_id)
        if found_in_cache:
            if cached_result is None:
                return None
            return self._result_from_dict(cached_result)

        # Make API call
        try:
            client = self._get_client()
            result = client.search_by_romnom(romnom, system_id)

            # Cache result
            _save_to_cache("romnom", romnom, system_id, result)

            if result:
                return self._result_from_dict(result)
            return None

        except Exception as e:
            logger.debug("ScreenScraper romnom lookup failed for '%s': %s", romnom, e)
            return None

    def _try_name_search(
        self, game_name: str, system_id: int
    ) -> Optional[LookupResult]:
        """Try name-based search with fuzzy matching.

        Uses ScreenScraper's search API and fuzzy matching to find the best
        match for a game name. Results are cached.

        Args:
            game_name: Game name to search for
            system_id: ScreenScraper system ID

        Returns:
            LookupResult if found with sufficient confidence, None otherwise
        """
        from library.metadata.screenscraper import _get_search_variants

        # Check cache first
        found_in_cache, cached_result = _check_cache("name", game_name, system_id)
        if found_in_cache:
            if cached_result is None:
                return None
            return self._result_from_dict(cached_result)

        # Try searching with variants
        try:
            client = self._get_client()
            variants = _get_search_variants(game_name)

            for variant in variants:
                results = client.search_game(variant, system_id)
                if not results:
                    continue

                # Find best match above threshold
                best = _find_best_match(game_name, results)
                if best and best.get("score", 0) >= 0.6:
                    result_dict = {
                        "id": best["id"],
                        "name": best["name"],
                        "system_id": system_id,
                    }
                    _save_to_cache("name", game_name, system_id, result_dict)
                    logger.info(
                        "ScreenScraper name search matched '%s' to '%s' "
                        "(ID: %s, score: %.2f)",
                        game_name,
                        best["name"],
                        best["id"],
                        best["score"],
                    )
                    return self._result_from_dict_with_confidence(
                        result_dict, best["score"]
                    )

            # Cache the miss
            _save_to_cache("name", game_name, system_id, None)
            logger.debug(
                "ScreenScraper name search found no match for '%s' on system %d",
                game_name,
                system_id,
            )
            return None

        except Exception as e:
            logger.debug("ScreenScraper name search failed for '%s': %s", game_name, e)
            return None

    def _result_from_dict_with_confidence(
        self, result: dict, confidence: float
    ) -> LookupResult:
        """Convert ScreenScraper result dict to LookupResult with custom confidence.

        Args:
            result: Dict with 'id', 'name', 'system_id' keys
            confidence: Match confidence score (0.0-1.0)

        Returns:
            LookupResult with screenscraper_id set
        """
        return LookupResult(
            name=result.get("name", ""),
            region="",  # ScreenScraper lookup doesn't provide region
            revision="",  # ScreenScraper lookup doesn't provide revision
            tags=[],
            source="screenscraper",
            confidence=confidence,
            raw_name=result.get("name", ""),
            screenscraper_id=result.get("id"),
        )

    def _result_from_dict(self, result: dict) -> LookupResult:
        """Convert ScreenScraper result dict to LookupResult.

        Args:
            result: Dict with 'id', 'name', 'system_id' keys

        Returns:
            LookupResult with screenscraper_id set
        """
        return LookupResult(
            name=result.get("name", ""),
            region="",  # ScreenScraper lookup doesn't provide region
            revision="",  # ScreenScraper lookup doesn't provide revision
            tags=[],
            source="screenscraper",
            confidence=0.85,  # Slightly less than Hasheous
            raw_name=result.get("name", ""),
            screenscraper_id=result.get("id"),
        )

    def is_available(self, system: "System") -> bool:
        """Check if ScreenScraper is available for this system.

        Returns True if the system has ScreenScraper IDs configured.
        """
        return bool(system.all_screenscraper_ids)
