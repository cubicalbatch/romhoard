"""Service registry for ROM lookup."""

import logging
from typing import TYPE_CHECKING, Optional

from .base import LookupResult, LookupService
from .hasheous import HasheousLookupService, lookup_hasheous_cache
from .screenscraper import ScreenScraperLookupService

if TYPE_CHECKING:
    from library.models import System

logger = logging.getLogger(__name__)

# Ordered list of services to try (first to last)
DEFAULT_SERVICES = [
    HasheousLookupService(),  # Hash databases (No-Intro, Redump)
    ScreenScraperLookupService(),  # ScreenScraper CRC + romnom fallback
]


def lookup_rom(
    system: "System",
    crc32: str = "",
    sha1: str = "",
    md5: str = "",
    file_path: str = "",
    game_name: str = "",
    services: list[LookupService] | None = None,
    use_hasheous: bool = True,
) -> Optional[LookupResult]:
    """Look up ROM using registered services in order.

    Args:
        system: Target system
        crc32: CRC32 hash (8 hex chars)
        sha1: SHA1 hash (40 hex chars)
        md5: MD5 hash (32 hex chars)
        file_path: Path to ROM file (for arcade ROM handling)
        game_name: Game name for name-based search (last resort fallback)
        services: Override service list (for testing)
        use_hasheous: Enable/disable Hasheous API calls (cache always checked)

    Returns:
        First successful LookupResult, or None
    """
    # Always check Hasheous cache first (regardless of use_hasheous flag)
    # This ensures cached results from previous lookups are always used
    cached_result = lookup_hasheous_cache(system, crc32=crc32, sha1=sha1, md5=md5)
    if cached_result and cached_result.screenscraper_id:
        # Only return early if we have a screenscraper_id
        # Otherwise continue to services to try to get one
        return cached_result

    # Build service list, filtering Hasheous API if disabled
    if services is None:
        services = DEFAULT_SERVICES.copy()
        if not use_hasheous:
            services = [s for s in services if s.name != "hasheous"]

    for service in services:
        if not service.is_available(system):
            logger.debug(
                "Service %s not available for system %s", service.name, system.slug
            )
            continue

        try:
            result = service.lookup(
                system=system,
                crc32=crc32,
                sha1=sha1,
                md5=md5,
                file_path=file_path,
                game_name=game_name,
            )
            if result:
                logger.info(
                    "Lookup success via %s for %s: '%s' [%s]",
                    service.name,
                    system.slug,
                    result.name,
                    result.region or "no region",
                )
                return result
        except Exception as e:
            logger.warning("Service %s lookup failed: %s", service.name, e)
            continue

    return None
