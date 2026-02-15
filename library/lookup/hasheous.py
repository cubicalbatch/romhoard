"""Hasheous.org API lookup service."""

import logging
import re
import time
from typing import TYPE_CHECKING, Any, Optional

import requests

from library.parser import parse_rom_filename

from .base import LookupResult, LookupService

if TYPE_CHECKING:
    from library.models import System

logger = logging.getLogger(__name__)

# API configuration
API_BASE = "https://hasheous.org/api/v1/Lookup/ByHash"
API_TIMEOUT = 30  # seconds
API_RETRIES = 1  # number of retries on timeout
REQUEST_DELAY = 0.5  # seconds between requests to be polite

# Last request timestamp for rate limiting
_last_request_time = 0.0

# Mapping from Hasheous platform names to our system slugs
PLATFORM_TO_SLUG = {
    # Nintendo
    "Nintendo Super Nintendo Entertainment System": "snes",
    "Nintendo Game Boy": "gb",
    "Nintendo Game Boy Color": "gbc",
    "Nintendo Game Boy Advance": "gba",
    "Nintendo Entertainment System": "nes",
    "Super Nintendo Entertainment System": "snes",
    "Nintendo 64": "n64",
    "Nintendo DS": "nds",
    "Nintendo 3DS": "3ds",
    "Nintendo GameCube": "gc",
    "Nintendo Wii": "wii",
    "Nintendo Wii U": "wiiu",
    "Nintendo Switch": "switch",
    "Nintendo Virtual Boy": "vb",
    "Pokemon Mini": "pokemini",
    # Sega
    "Sega Master System": "sms",
    "Sega Genesis": "genesis",
    "Sega Mega Drive": "genesis",
    "Sega Game Gear": "gg",
    "Sega CD": "segacd",
    "Sega 32X": "32x",
    "Sega Saturn": "saturn",
    "Sega Dreamcast": "dreamcast",
    "Sega SG-1000": "sg1000",
    # Sony
    "Sony PlayStation": "ps1",
    "Sony PlayStation 2": "ps2",
    "Sony PlayStation 3": "ps3",
    "Sony PlayStation Portable": "psp",
    "Sony PlayStation Vita": "psvita",
    # Atari
    "Atari 2600": "2600",
    "Atari 5200": "5200",
    "Atari 7800": "7800",
    "Atari Lynx": "lynx",
    "Atari Jaguar": "jaguar",
    "Atari ST": "atarist",
    # NEC
    "NEC PC Engine": "pce",
    "TurboGrafx-16": "pce",
    "NEC PC Engine SuperGrafx": "sgfx",
    "NEC PC-FX": "pcfx",
    # SNK
    "Neo Geo Pocket": "ngp",
    "Neo Geo Pocket Color": "ngpc",
    "Neo Geo CD": "neogeocd",
    "Neo Geo": "neogeo",
    # Other
    "Arcade": "arcade",
    "MSX": "msx",
    "MSX2": "msx",
    "ColecoVision": "coleco",
    "Intellivision": "intv",
    "Vectrex": "vectrex",
    "WonderSwan": "ws",
    "WonderSwan Color": "wsc",
    "Bandai WonderSwan": "ws",
    "Bandai WonderSwan Color": "wsc",
    "Watara Supervision": "supervision",
    "Tiger Game.com": "gamecom",
    "Commodore 64": "c64",
    "Commodore Amiga": "amiga",
    "3DO": "3do",
    "Philips CD-i": "cdi",
}


def _check_cache(hash_type: str, hash_value: str) -> tuple[bool, dict | None]:
    """Check cache for a hash lookup.

    Args:
        hash_type: Type of hash ("crc32", "sha1", "md5")
        hash_value: The hash value to look up

    Returns:
        Tuple of (found_in_cache, cached_result).
        If found_in_cache is True but cached_result is None, it means
        the hash was previously looked up and had no match.
    """
    from library.models import HasheousCache

    try:
        cache_entry = HasheousCache.objects.get(
            hash_type=hash_type, hash_value=hash_value.lower()
        )

        if not cache_entry.matched:
            # Known no-match
            logger.debug(
                "Hasheous cache hit (no match): %s=%s", hash_type, hash_value[:8]
            )
            return True, None

        # Reconstruct the API response format
        cached_result = {
            "name": cache_entry.raw_name,
            "platform": {"name": cache_entry.platform_name},
            "signature": {
                "rom": {"signatureSource": cache_entry.source},
            },
            # Store parsed data for convenience
            "_cached": True,
            "_game_name": cache_entry.game_name,
            "_region": cache_entry.region,
            "_revision": cache_entry.revision,
            "_tags": cache_entry.tags,
        }
        logger.debug(
            "Hasheous cache hit (matched): %s=%s -> %s",
            hash_type,
            hash_value[:8],
            cache_entry.game_name,
        )
        return True, cached_result

    except HasheousCache.DoesNotExist:
        return False, None


def _save_to_cache(
    hash_type: str,
    hash_value: str,
    api_response: dict | None,
    parsed: "LookupResult | None" = None,
) -> None:
    """Save lookup result to cache.

    Args:
        hash_type: Type of hash ("crc32", "sha1", "md5")
        hash_value: The hash value
        api_response: Raw API response dict, or None if no match
        parsed: Parsed LookupResult (if available) for storing game info
    """
    from library.models import HasheousCache

    try:
        if api_response is None:
            # Cache a no-match
            HasheousCache.objects.update_or_create(
                hash_type=hash_type,
                hash_value=hash_value.lower(),
                defaults={"matched": False},
            )
            logger.debug(
                "Hasheous cache saved (no match): %s=%s", hash_type, hash_value[:8]
            )
        else:
            # Cache a match
            platform_name = api_response.get("platform", {}).get("name", "")
            source = (
                api_response.get("signature", {}).get("rom", {}).get("signatureSource")
                or "hasheous"
            )

            defaults = {
                "matched": True,
                "raw_name": api_response.get("name", ""),
                "platform_name": platform_name,
                "source": source,
            }

            # Add parsed info if available
            if parsed:
                defaults.update(
                    {
                        "game_name": parsed.name,
                        "region": parsed.region,
                        "revision": parsed.revision,
                        "tags": parsed.tags,
                    }
                )

            HasheousCache.objects.update_or_create(
                hash_type=hash_type,
                hash_value=hash_value.lower(),
                defaults=defaults,
            )
            logger.debug(
                "Hasheous cache saved (matched): %s=%s -> %s",
                hash_type,
                hash_value[:8],
                defaults.get("game_name", api_response.get("name", "")),
            )

    except Exception as e:
        # Don't fail the lookup if caching fails
        logger.warning("Failed to save Hasheous cache: %s", e)


def _rate_limit():
    """Enforce rate limiting between API requests."""
    global _last_request_time
    elapsed = time.time() - _last_request_time
    if elapsed < REQUEST_DELAY:
        time.sleep(REQUEST_DELAY - elapsed)
    _last_request_time = time.time()


def lookup_hasheous_cache(
    system: "System",
    crc32: str = "",
    sha1: str = "",
    md5: str = "",
) -> Optional[LookupResult]:
    """Check Hasheous cache for a match without making API calls.

    This function is called regardless of the use_hasheous toggle to ensure
    cached results from previous lookups are always used.

    Args:
        system: Target system for validation
        crc32: CRC32 hash to look up
        sha1: SHA1 hash to look up
        md5: MD5 hash to look up

    Returns:
        LookupResult if cached match found, None otherwise
    """
    # Try each hash type in order of preference (SHA1 > MD5 > CRC32)
    for hash_type, hash_value in [("sha1", sha1), ("md5", md5), ("crc32", crc32)]:
        if not hash_value:
            continue

        found_in_cache, cached_result = _check_cache(hash_type, hash_value)

        if not found_in_cache:
            continue

        if cached_result is None:
            # Known no-match cached - don't return, try other hashes
            continue

        # We have a cached match - convert to LookupResult
        # The cached result has _game_name, _region, etc. from previous parsing
        if cached_result.get("_cached"):
            result = LookupResult(
                name=cached_result.get("_game_name", ""),
                region=cached_result.get("_region", ""),
                revision=cached_result.get("_revision", ""),
                tags=cached_result.get("_tags", []),
                source=cached_result.get("signature", {})
                .get("rom", {})
                .get("signatureSource", "hasheous"),
                confidence=0.9,
                raw_name=cached_result.get("name", ""),
            )
            if result.name:
                logger.info(
                    "Hasheous cache lookup hit for %s: '%s' [%s]",
                    system.slug,
                    result.name,
                    result.region or "no region",
                )
                return result

    return None


class HasheousLookupService(LookupService):
    """Hasheous.org API lookup service."""

    name = "hasheous"

    def lookup(  # noqa: ARG002
        self,
        system: "System",
        crc32: str = "",
        sha1: str = "",
        md5: str = "",
        file_path: str = "",
        game_name: str = "",
    ) -> Optional[LookupResult]:
        """Look up ROM by hash using Hasheous API.

        Note: For arcade ROMs (archive_as_rom systems), this service is skipped.
        Arcade identification uses ScreenScraper's romnom lookup instead, which
        matches the MAME short name (ZIP filename) directly to games.

        Note: game_name is not used by Hasheous (hash-only lookup), but is
        accepted for interface compatibility.
        """
        # Skip arcade systems - they use ScreenScraper romnom lookup instead
        # Individual file CRC lookups don't work for arcade because many ROM chips
        # (palette PROMs, timing circuits) are shared across different games
        if system.archive_as_rom:
            return None

        # Regular lookup with provided hashes
        # Prefer SHA1 > MD5 > CRC32 (more reliable matches)
        if sha1:
            result = self._api_lookup(sha1=sha1)
            if result:
                parsed = self._parse_response(result, system)
                if parsed:
                    # Update cache with parsed info
                    _save_to_cache("sha1", sha1, result, parsed)
                return parsed

        if md5:
            result = self._api_lookup(md5=md5)
            if result:
                parsed = self._parse_response(result, system)
                if parsed:
                    _save_to_cache("md5", md5, result, parsed)
                return parsed

        if crc32:
            result = self._api_lookup(crc=crc32)
            if result:
                parsed = self._parse_response(result, system)
                if parsed:
                    _save_to_cache("crc32", crc32, result, parsed)
                return parsed

        return None

    def is_available(self, system: "System") -> bool:
        """Hasheous is always available (external service)."""
        return True

    def _api_lookup(
        self,
        crc: str = "",
        sha1: str = "",
        md5: str = "",
    ) -> Optional[dict[str, Any]]:
        """Make API request to Hasheous with caching.

        Checks the cache first before making an API call. Caches both
        successful matches and known misses to avoid repeated API calls.

        Args:
            crc: CRC32 hash
            sha1: SHA1 hash
            md5: MD5 hash

        Returns:
            Response dict if found, None otherwise
        """
        # Determine which hash to use (prefer SHA1 > MD5 > CRC32)
        if sha1:
            hash_type, hash_value = "sha1", sha1.lower()
        elif md5:
            hash_type, hash_value = "md5", md5.lower()
        elif crc:
            hash_type, hash_value = "crc32", crc.lower()
        else:
            return None

        # Check cache first
        found_in_cache, cached_result = _check_cache(hash_type, hash_value)
        if found_in_cache:
            return cached_result  # Could be dict or None (no-match)

        # Rate limit only for actual API calls
        _rate_limit()

        # Build request body
        body = {}
        if crc:
            body["crc"] = crc.lower()
        if sha1:
            body["shA1"] = sha1.lower()
        if md5:
            body["mD5"] = md5.lower()

        # Retry loop for timeout errors
        for attempt in range(API_RETRIES + 1):
            try:
                response = requests.post(
                    API_BASE,
                    json=body,
                    timeout=API_TIMEOUT,
                    headers={"Content-Type": "application/json"},
                )

                if response.status_code == 404:
                    # Not found - cache the miss
                    _save_to_cache(hash_type, hash_value, None)
                    return None

                if response.status_code != 200:
                    logger.warning(
                        "Hasheous API error: status=%d, body=%s",
                        response.status_code,
                        response.text[:200],
                    )
                    # Don't cache errors - might be transient
                    return None

                result = response.json()
                # Cache the successful result (parsed info added later in _parse_response)
                _save_to_cache(hash_type, hash_value, result)
                return result

            except requests.exceptions.Timeout:
                if attempt < API_RETRIES:
                    logger.info(
                        "Hasheous API timeout, retrying (%d/%d)...",
                        attempt + 1,
                        API_RETRIES,
                    )
                    _rate_limit()  # Be polite before retry
                    continue
                logger.warning(
                    "Hasheous API request timed out after %d retries", API_RETRIES
                )
                return None
            except requests.RequestException as e:
                logger.warning("Hasheous API request failed: %s", e)
                return None
            except ValueError as e:
                logger.warning("Hasheous API response parse error: %s", e)
                return None

        return None

    def _platform_matches(self, platform_name: str, system: "System") -> bool:
        """Check if Hasheous platform matches our target system."""
        slug = PLATFORM_TO_SLUG.get(platform_name)
        if slug is None:
            logger.warning(
                "Hasheous returned unknown platform '%s' - add to PLATFORM_TO_SLUG",
                platform_name,
            )
            return False
        return slug == system.slug

    def _parse_response(
        self, response: dict[str, Any], system: "System"
    ) -> Optional[LookupResult]:
        """Parse Hasheous response into LookupResult.

        Args:
            response: Raw API response dict
            system: Target system for validation

        Returns:
            LookupResult if valid, None if platform mismatch
        """
        # Get the game name from top-level "name" field (authoritative database name)
        # This is the clean game name without region tags
        game_name = response.get("name", "")
        if not game_name:
            game_name = response.get("signature", {}).get("game", {}).get("name", "")

        if not game_name:
            return None

        # Validate platform matches our system (skip for arcade which is flexible)
        platform_name = response.get("platform", {}).get("name", "")
        if platform_name and platform_name.lower() != "arcade":
            if not self._platform_matches(platform_name, system):
                logger.debug(
                    "Hasheous platform mismatch: got '%s', expected system '%s'",
                    platform_name,
                    system.slug,
                )
                return None

        # Extract signature source to determine which database matched
        signature_source = (
            response.get("signature", {}).get("rom", {}).get("signatureSource", "")
        )

        # Use signatureSource directly, or "hasheous" if missing
        source = signature_source if signature_source else "hasheous"

        # Clean up the game name - remove number prefixes like "0983 - "
        cleaned_game_name = self._clean_name(game_name)

        # Get region/revision from signature.rom.name which includes tags like "(USA)"
        # This is the full ROM filename, not the game name
        rom_name = response.get("signature", {}).get("rom", {}).get("name", "")
        region = ""
        revision = ""
        tags = []

        if rom_name:
            # Parse the ROM filename to extract region/revision
            parsed = parse_rom_filename(rom_name)
            region = parsed["region"]
            revision = parsed["revision"]
            tags = parsed["tags"]

        logger.debug(
            "Hasheous match: '%s' -> name='%s', region='%s', source='%s'",
            game_name,
            cleaned_game_name,
            region or "(none)",
            signature_source or "unknown",
        )

        return LookupResult(
            name=cleaned_game_name,
            region=region,
            revision=revision,
            tags=tags,
            source=source,
            confidence=0.9,  # Slightly less than DAT (external service)
            raw_name=game_name,
        )

    def _clean_name(self, name: str) -> str:
        """Clean up Hasheous name by removing catalog number prefixes.

        Hasheous names often include prefixes like "0983 - " from No-Intro numbering.
        """
        # Remove leading number prefixes like "0983 - " or "1234 - "
        cleaned = re.sub(r"^\d{3,4}\s*-\s*", "", name)
        return cleaned.strip()
