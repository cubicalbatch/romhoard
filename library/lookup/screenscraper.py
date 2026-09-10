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

    # Convert hyphens, en-dashes, em-dashes, slashes, colons, underscores into spaces
    # BEFORE removing other punctuation to avoid merging words (e.g. Pac-Man -> pac man)
    name = re.sub(r"[\-–—/:_]", " ", name)

    # Remove punctuation and special characters
    name = re.sub(r"[^\w\s]", "", name)

    # Convert standalone Roman numerals II-X to Western numbers 2-10 (case-insensitive)
    # Note: 'name' is already lowercased, so we match lowercase roman numerals
    roman_to_western = {
        "viii": "8",
        "vii": "7",
        "vi": "6",
        "iv": "4",
        "ix": "9",
        "iii": "3",
        "ii": "2",
        "v": "5",
        "x": "10",
    }
    name = re.sub(
        r"\b(viii|vii|vi|iv|ix|iii|ii|v|x)\b",
        lambda m: roman_to_western[m.group(0)],
        name,
    )

    # Normalize whitespace
    name = re.sub(r"\s+", " ", name).strip()

    return name


CONSOLE_TERMS = {
    "32x",
    "nes",
    "snes",
    "sfc",
    "sms",
    "gg",
    "genesis",
    "megadrive",
    "n64",
    "lynx",
    "gb",
    "gba",
    "gbc",
    "pce",
    "tg16",
    "atari",
    "sega",
    "nintendo",
    "sony",
    "playstation",
    "psx",
    "arcade",
    "mame",
}

STOPWORDS = {
    "the",
    "a",
    "an",
    "of",
    "and",
    "or",
    "vs",
    "to",
    "in",
    "on",
    "at",
    "by",
    "for",
    "with",
    "no",
    "ni",
    "de",
    "la",
    "le",
}


def _extract_numbers(norm_name: str) -> set[int]:
    """Extract standalone numbers (1-99) including normalized Roman numerals 1-10, excluding 4-digit years.

    Args:
        norm_name: Normalized game name string

    Returns:
        Set of integers found (1-99)
    """
    tokens = norm_name.split()
    numbers = set()
    for idx, token in enumerate(tokens):
        if token.isdigit():
            val = int(token)
            if 1 <= val <= 99:
                numbers.add(val)
        elif token == "i":
            # Standalone 'i' treated as Roman numeral 1 if at the end of title or after part/vol/volume/chapter/episode
            if (idx == len(tokens) - 1 and len(tokens) > 1) or (
                idx > 0
                and tokens[idx - 1] in {"part", "vol", "volume", "chapter", "episode"}
            ):
                numbers.add(1)
    return numbers


def _check_number_consistency(norm1: str, norm2: str) -> bool:
    """Check sequel / number consistency between two normalized names.

    Returns False if:
    - Both titles have numbers and they differ (e.g. 2 vs 3)
    - One title has a sequel number >= 2 and the other has NO numbers (e.g. "Mega Man" vs "Mega Man 2")
    """
    nums1 = _extract_numbers(norm1)
    nums2 = _extract_numbers(norm2)

    if nums1 and nums2:
        if nums1 != nums2:
            return False
    elif nums1 and not nums2:
        if any(n >= 2 for n in nums1):
            return False
    elif nums2 and not nums1:
        if any(n >= 2 for n in nums2):
            return False

    return True


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

    # Sequel / Number Consistency Guard
    if not _check_number_consistency(norm_game, norm_api):
        return 0.0

    game_words = set(norm_game.split())
    api_words = set(norm_api.split())

    if not game_words or not api_words:
        return 0.0

    overlap = game_words & api_words

    # Console Terms & Stopwords Guard:
    # If the word overlap consists ONLY of console terms and/or stopwords: score MUST BE 0.0
    meaningful_overlap = overlap - CONSOLE_TERMS - STOPWORDS
    if not meaningful_overlap:
        return 0.0

    # Substring match precision:
    # Substring match shorter in longer (on word boundaries) awards 0.85 ONLY IF:
    # - Shorter has at least 2 meaningful words, OR
    # - Shorter is at least 5 characters and constitutes at least 40% of the longer string's length,
    # AND shorter is not purely a console term or stopword.
    shorter, longer = (
        (norm_game, norm_api)
        if len(norm_game) <= len(norm_api)
        else (norm_api, norm_game)
    )
    shorter_words = shorter.split()
    meaningful_shorter = [
        w for w in shorter_words if w not in CONSOLE_TERMS and w not in STOPWORDS
    ]

    has_word_boundary_match = bool(
        re.search(rf"\b{re.escape(shorter)}\b", longer)
    )
    is_precise_shorter = bool(
        meaningful_shorter
        and (
            len(meaningful_shorter) >= 2
            or (len(shorter) >= 5 and (len(shorter) / len(longer)) >= 0.40)
        )
    )

    if has_word_boundary_match and is_precise_shorter:
        return 0.85

    # Word overlap scoring
    total = len(game_words | api_words)
    base_score = len(overlap) / total

    # If all words of the shorter title are contained in the longer title
    # (e.g., "Aladdin" in "Disney's Aladdin"), ensure the score is at least 0.85
    # only if shorter meets the precision criteria
    if (
        game_words.issubset(api_words) or api_words.issubset(game_words)
    ) and is_precise_shorter:
        base_score = max(base_score, 0.85)

    # Boost score when base overlap is very low but there's a significant match
    # This helps when names are completely different (e.g., Japanese vs English)
    # but share a distinctive word like "Rondo", "Gradius", "Spriggan"
    # Only boost when base_score < 0.3 (very different names)
    if base_score < 0.3 and len(overlap) >= 1:
        significant_overlap = {
            w
            for w in overlap
            if len(w) >= 5
            and w.lower() not in STOPWORDS
            and w.lower() not in CONSOLE_TERMS
        }
        if significant_overlap:
            # A significant word match is a strong signal - boost to 0.65
            base_score = 0.65

    return base_score


def _find_best_match(
    game_name: str, results: list[dict], search_variant: str = ""
) -> dict | None:
    """Find the best matching result from a list of API results.

    Args:
        game_name: The game name we're searching for
        results: List of result dicts from ScreenScraper API
        search_variant: Optional search variant used to query the API

    Returns:
        Best matching result dict with 'score' added, or None if no good match
    """
    best_match = None
    best_score = 0.0

    norm_game = normalize_name(game_name)

    # Check search_variant validity:
    # If search_variant is purely a console term or <= 3 chars or stopword, ignore it for candidate scoring
    valid_search_variant = bool(search_variant)
    if valid_search_variant:
        norm_v = normalize_name(search_variant)
        v_words = norm_v.split()
        if (
            len(norm_v) <= 3
            or norm_v in CONSOLE_TERMS
            or norm_v in STOPWORDS
            or not (set(v_words) - CONSOLE_TERMS - STOPWORDS)
        ):
            valid_search_variant = False

    for result in results:
        # Check against primary name
        names_to_check = {result.get("name", "")}
        # Add all known regional names
        if "all_names" in result:
            names_to_check.update(result["all_names"])

        # Find best score across all names for this candidate
        candidate_best_score = 0.0
        for name in names_to_check:
            if not name:
                continue

            # Ensure candidate passes the sequel/number consistency check against game_name
            norm_cand = normalize_name(name)
            if not _check_number_consistency(norm_game, norm_cand):
                continue

            score = calculate_match_score(game_name, name)
            if valid_search_variant:
                score = max(score, calculate_match_score(search_variant, name))
            if score > candidate_best_score:
                candidate_best_score = score

        if candidate_best_score > best_score and candidate_best_score > 0.0:
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


def _extract_rom_stem(file_path: str, archive_as_rom: bool = False) -> str:
    """Extract ROM filename stem cleaned of tags for fallback name search.

    Args:
        file_path: ROM path, may contain "!" for archive contents
        archive_as_rom: If True, use archive name; else use inner file name

    Returns:
        Cleaned stem (tags and extensions removed)
    """
    romnom = _extract_romnom(file_path, archive_as_rom)
    try:
        from library.parser import parse_rom_filename

        parsed = parse_rom_filename(romnom)
        stem = parsed.get("name", "").strip()
        if stem:
            return stem
    except Exception:
        pass

    # Fallback: strip tags from Path stem
    stem = Path(romnom).stem
    stem = re.sub(r"[\(\[][^\)\]]+[\)\]]", "", stem).strip()
    return re.sub(r"\s+", " ", stem).strip()



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

        # Phase 3b: ROM filename stem search (fallback when game_name fails or differs)
        if file_path:
            stem = _extract_rom_stem(file_path, system.archive_as_rom)
            if stem and (not game_name or stem.strip().lower() != game_name.strip().lower()):
                stem_best_result = None
                stem_best_confidence = 0.0

                for system_id in system.all_screenscraper_ids:
                    result = self._try_name_search(stem, system_id)
                    if result and result.confidence > stem_best_confidence:
                        stem_best_result = result
                        stem_best_confidence = result.confidence

                if stem_best_result:
                    return stem_best_result

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
                best = _find_best_match(game_name, results, search_variant=variant)
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
