"""ROM lookup module - unified interface for multiple lookup services."""

from .base import LookupResult, LookupService
from .registry import lookup_rom
from .screenscraper import ScreenScraperLookupService

__all__ = ["LookupResult", "LookupService", "ScreenScraperLookupService", "lookup_rom"]
