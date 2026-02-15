"""Base classes for ROM lookup services."""

from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import TYPE_CHECKING, Optional

if TYPE_CHECKING:
    from library.models import System


@dataclass
class LookupResult:
    """Standardized result from any lookup service."""

    name: str  # Game name (parsed, without region tags)
    region: str  # Region string
    revision: str  # Revision string
    tags: list[str]  # Additional tags
    source: str  # Service name (e.g., "nointro", "hasheous")
    confidence: float  # 0.0-1.0, how confident in match
    raw_name: str  # Original name from source (for debugging)
    matched_crc32: str = (
        ""  # CRC32 that matched (for arcade ROMs matched via internal file)
    )
    screenscraper_id: int | None = None  # ScreenScraper game ID (for SS matches)


class LookupService(ABC):
    """Abstract base for ROM lookup services."""

    name: str  # Service identifier

    @abstractmethod
    def lookup(
        self,
        system: "System",
        crc32: str = "",
        sha1: str = "",
        md5: str = "",
        file_path: str = "",
        game_name: str = "",
    ) -> Optional[LookupResult]:
        """Look up ROM by hash(es) for a given system.

        Args:
            system: Target system to search within
            crc32: CRC32 hash (8 hex chars)
            sha1: SHA1 hash (40 hex chars)
            md5: MD5 hash (32 hex chars)
            file_path: Path to ROM file (for arcade ROM handling)
            game_name: Game name for name-based search (last resort fallback)

        Returns:
            LookupResult if found, None otherwise
        """
        pass

    @abstractmethod
    def is_available(self, system: "System") -> bool:
        """Check if service is configured and available for a system."""
        pass
