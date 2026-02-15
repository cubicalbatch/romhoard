"""ScreenScraper API client for game metadata fetching.

ScreenScraper API Authentication Requirements:
- User credentials: REQUIRED
  - Set via UI (stored in database) or environment variables (SCREENSCRAPER_USER, SCREENSCRAPER_PASSWORD)
  - These are your personal ScreenScraper.fr account credentials
  - Without these, metadata fetching is disabled

- App identifier: Built-in
  - Used by the application to identify itself to ScreenScraper
"""

import base64
import logging
import os
import re
import time
from datetime import datetime, timedelta
from typing import Any
from urllib.parse import urlencode

import requests
from django.utils import timezone

from library.models import Setting

logger = logging.getLogger(__name__)

# ScreenScraper API base URL
SCREENSCRAPER_API_BASE = "https://api.screenscraper.fr/api2/"

# Rate limiting: Max requests per second (be conservative)
RATE_LIMIT_DELAY = 0.0  # Seconds between requests

# Allowed media types for download
ALLOWED_MEDIA_TYPES = {"box-2D", "ss", "mixrbv1", "wheel", "sstitle"}

# Region priority for media selection (lower index = higher priority)
REGION_PRIORITY = ["wor", "us", "usa", "eu", "jp", "ss"]

# Rate limit pause configuration
PAUSE_DURATION_HOURS = 2
PAUSE_SETTING_KEY = "screenscraper_pause_until"

# Timeout configuration (connect_timeout, read_timeout)
# Popular games can take 60-90 seconds due to server-side processing
DEFAULT_TIMEOUT = (10, 180)  # 10s connect, 180s read


def _normalize_search_name(name: str) -> str:
    """Normalize name for initial search query.

    - Remove leading "The " (ScreenScraper stores as "Name, The")
    - Normalize whitespace
    """
    # Remove leading "The " (case-insensitive)
    if name.lower().startswith("the "):
        name = name[4:]
    return name.strip()


def _roman_to_western(name: str) -> str | None:
    """Convert standalone uppercase Roman numerals to Western numbers.

    Only converts Roman numerals II-X when they appear as standalone words.
    Returns None if no conversion was made.

    Examples:
        "Final Fantasy V" -> "Final Fantasy 5"
        "Street Fighter II" -> "Street Fighter 2"
        "Vega" -> None (V is not standalone)
        "More x vision" -> None (x is lowercase)
    """
    roman_to_western = {
        "II": "2",
        "III": "3",
        "IV": "4",
        "V": "5",
        "VI": "6",
        "VII": "7",
        "VIII": "8",
        "IX": "9",
        "X": "10",
    }
    result = name
    for roman, western in roman_to_western.items():
        # Match standalone uppercase Roman numeral with word boundaries
        pattern = rf"\b{roman}\b"
        result = re.sub(pattern, western, result)

    return result if result != name else None


def _pokemon_accent(name: str) -> str | None:
    """Replace 'pokemon' with 'pokémon' (case-insensitive match).

    Returns None if no replacement was made.

    Examples:
        "Pokemon Red" -> "pokémon Red"
        "POKEMON Blue" -> "pokémon Blue"
        "Super Mario" -> None
    """
    result = re.sub(r"pokemon", "pokémon", name, flags=re.IGNORECASE)
    return result if result != name else None


def _get_app_identifier() -> tuple[str, str]:
    """Get the app identifier for API requests."""
    _OBF_DEVID = "=YTZ6FGbi1WR"
    _OBF_DEVPW = "=g3dDVVQBt0YzEHS"
    devid = base64.b64decode(_OBF_DEVID[::-1]).decode()
    devpw = base64.b64decode(_OBF_DEVPW[::-1]).decode()
    return devid, devpw


def _get_search_variants(name: str) -> list[str]:
    """Generate search query variants for retry logic.

    Returns list of names to try in order:
    1. Normalized name (leading "The" removed)
    2. With & replaced by "and" (if applicable)
    3. With dashes removed (if applicable)
    4. With colons removed (if applicable)
    5. Main title only - text before first colon or dash (if applicable)
    6. Subtitle only - text after first colon or dash (if applicable)
    7. With apostrophes removed (if applicable)
    8. First significant word for long titles (if applicable)
    9. Roman numerals converted to Western numbers (if applicable)
    10. Pokemon -> Pokémon (if applicable)
    11. Content before parentheses (if applicable)
    12. Content inside parentheses - for alt names/Japanese (if applicable)
    13. Slash-separated parts (if applicable)
    """
    variants = []

    # Start with normalized name
    base = _normalize_search_name(name)
    variants.append(base)

    # Variant: replace & with "and"
    if "&" in base:
        variants.append(base.replace("&", "and"))

    # Variant: remove dashes (normalize spaces around them)
    if "-" in base:
        # "Metroid - Mission Zero" -> "Metroid Mission Zero"
        no_dash = re.sub(r"\s*-\s*", " ", base)
        no_dash = re.sub(r"\s+", " ", no_dash).strip()
        if no_dash not in variants:
            variants.append(no_dash)

    # Variant: replace colons with " - " (e.g., "Game: Subtitle" -> "Game - Subtitle")
    if ":" in base:
        colon_to_dash = base.replace(":", " -")
        colon_to_dash = re.sub(r"\s+", " ", colon_to_dash).strip()
        if colon_to_dash not in variants:
            variants.append(colon_to_dash)

    # Variant: remove colons (normalize spaces around them)
    if ":" in base:
        # "Castlevania: Symphony of the Night" -> "Castlevania Symphony of the Night"
        no_colon = re.sub(r"\s*:\s*", " ", base)
        no_colon = re.sub(r"\s+", " ", no_colon).strip()
        if no_colon not in variants:
            variants.append(no_colon)

    # Variant: main title only - search for text before first colon or dash
    # "Castlevania: Symphony of the Night" -> "Castlevania"
    # "SNK vs. Capcom: Card Fighters' Clash" -> "SNK vs. Capcom"
    separator_match = re.search(r"[:\-]", base)
    if separator_match:
        main_title = base[: separator_match.start()].strip()
        if main_title and len(main_title) >= 3 and main_title not in variants:
            variants.append(main_title)

    # Variant: subtitle only - search for text after first colon or dash
    # "Castlevania: Symphony of the Night" -> "Symphony of the Night"
    # "Metroid - Mission Zero" -> "Mission Zero"
    if separator_match:
        subtitle = base[separator_match.end() :].strip()
        if subtitle and subtitle not in variants:
            variants.append(subtitle)

    # Variant: remove apostrophes (possessives cause matching issues)
    # "SNK Gals' Fighters" -> "SNK Gals Fighters"
    if "'" in base:
        no_apostrophe = base.replace("'", "")
        no_apostrophe = re.sub(r"\s+", " ", no_apostrophe).strip()
        if no_apostrophe not in variants:
            variants.append(no_apostrophe)

    # Variant: first significant word(s) for long titles (4+ words)
    # "Sonic the Hedgehog Pocket Adventure" -> "Sonic"
    # Skip common words like "the", "of", "and"
    skip_words = {"the", "a", "an", "of", "and", "or", "vs", "vs."}
    words = base.split()
    if len(words) >= 4:
        # Get first word that isn't a common article/preposition
        first_significant = None
        for word in words:
            if word.lower() not in skip_words and len(word) >= 3:
                first_significant = word
                break
        if first_significant and first_significant not in variants:
            variants.append(first_significant)

    # Variant: first word from subtitles containing stopwords (3+ words)
    # "Rondo of Blood" -> "Rondo" (helps find "Akumajou Dracula X - Chi No Rondo")
    # Only if subtitle has 3+ words and contains stopwords
    if separator_match:
        subtitle = base[separator_match.end() :].strip()
        subtitle_words = subtitle.split()
        if len(subtitle_words) >= 3:
            has_stopword = any(w.lower() in skip_words for w in subtitle_words)
            if has_stopword:
                first_word = None
                for word in subtitle_words:
                    if word.lower() not in skip_words and len(word) >= 3:
                        first_word = word
                        break
                if first_word and first_word not in variants:
                    variants.append(first_word)

    # Variant: Roman numerals converted to Western numbers
    # "Final Fantasy V" -> "Final Fantasy 5"
    western_variant = _roman_to_western(base)
    if western_variant and western_variant not in variants:
        variants.append(western_variant)

    # Variant: Pokemon -> Pokémon
    # "Pokemon Red" -> "Pokémon Red"
    pokemon_variant = _pokemon_accent(base)
    if pokemon_variant and pokemon_variant not in variants:
        variants.append(pokemon_variant)

    # Variant: content before parentheses
    # "Donkey Kong (Donkey Kong '94)" -> "Donkey Kong"
    # "Kid Icarus (Hikari Shinwa: Palutena no Kagami)" -> "Kid Icarus"
    paren_match = re.search(r"\s*\([^)]+\)", base)
    if paren_match:
        before_paren = base[: paren_match.start()].strip()
        if before_paren and len(before_paren) >= 3 and before_paren not in variants:
            variants.append(before_paren)

    # Variant: content inside parentheses (for alt names, Japanese titles)
    # "Kid Icarus (Hikari Shinwa: Palutena no Kagami)" -> "Hikari Shinwa"
    # Also handles nested colons by taking first part
    paren_content_match = re.search(r"\(([^)]+)\)", base)
    if paren_content_match:
        paren_content = paren_content_match.group(1).strip()
        # Remove year patterns like "94" or "'94" that might be in parens
        if not re.match(r"^'?\d{2,4}$", paren_content):
            # If content has separator, try the first part
            inner_sep = re.search(r"[:\-]", paren_content)
            if inner_sep:
                first_part = paren_content[: inner_sep.start()].strip()
                if first_part and len(first_part) >= 3 and first_part not in variants:
                    variants.append(first_part)
            # Also try the full parenthetical content
            if len(paren_content) >= 3 and paren_content not in variants:
                variants.append(paren_content)

    # Variant: split on slash for multi-version titles
    # "Pokemon Red/Blue" -> "Pokemon Red", "Pokemon Blue"
    if "/" in base:
        slash_parts = base.split("/")
        if len(slash_parts) == 2:
            left, right = slash_parts[0].strip(), slash_parts[1].strip()
            # Find the common prefix (words before the slash)
            # "Pokemon Red/Blue" -> prefix="Pokemon ", left_part="Red", right_part="Blue"
            left_words = left.rsplit(" ", 1)
            if len(left_words) == 2:
                prefix, left_part = left_words
                # Try "prefix left_part" (usually already in variants as base)
                full_left = f"{prefix} {left_part}"
                if full_left not in variants:
                    variants.append(full_left)
                # Try "prefix right_part"
                full_right = f"{prefix} {right}"
                if full_right not in variants:
                    variants.append(full_right)

    return variants


class ScreenScraperRateLimited(Exception):
    """Raised when ScreenScraper returns 429/430 rate limit error."""

    def __init__(self, retry_after: datetime):
        self.retry_after = retry_after
        super().__init__(f"ScreenScraper rate limited until {retry_after}")


def get_pause_until() -> datetime | None:
    """Get the pause-until timestamp, or None if not paused.

    Returns:
        datetime if currently paused and pause hasn't expired, None otherwise
    """
    try:
        setting = Setting.objects.get(key=PAUSE_SETTING_KEY)
        pause_until = setting.value
        if isinstance(pause_until, str):
            pause_until = datetime.fromisoformat(pause_until)
        # Make timezone-aware if needed
        if pause_until.tzinfo is None:
            pause_until = timezone.make_aware(pause_until)
        if pause_until > timezone.now():
            return pause_until
        return None
    except Setting.DoesNotExist:
        return None


def set_pause_until(hours: int = PAUSE_DURATION_HOURS) -> datetime:
    """Set the pause-until timestamp. Returns the pause-until time."""
    pause_until = timezone.now() + timedelta(hours=hours)
    Setting.objects.update_or_create(
        key=PAUSE_SETTING_KEY, defaults={"value": pause_until.isoformat()}
    )
    logger.warning(f"ScreenScraper paused until {pause_until} due to rate limiting")
    return pause_until


def clear_pause():
    """Clear the pause setting to resume API calls."""
    deleted, _ = Setting.objects.filter(key=PAUSE_SETTING_KEY).delete()
    if deleted:
        logger.info("ScreenScraper pause cleared, API calls resumed")


def screenscraper_available() -> bool:
    """Check if ScreenScraper user credentials are configured (DB or env).

    This is a lightweight check that doesn't require instantiating the client.
    Use this to conditionally show/hide UI elements that require ScreenScraper.

    Returns:
        True if username and password are configured, False otherwise
    """
    username = Setting.get("screenscraper_username") or os.environ.get(
        "SCREENSCRAPER_USER"
    )
    password = Setting.get("screenscraper_password") or os.environ.get(
        "SCREENSCRAPER_PASSWORD"
    )
    return bool(username and password)


CREDENTIALS_VALID_KEY = "screenscraper_credentials_valid"


def validate_credentials() -> tuple[bool, str | None]:
    """Validate ScreenScraper credentials by making a test API call.

    Uses the ssuserInfos endpoint to verify credentials work.

    Returns:
        Tuple of (is_valid, error_message)
    """
    if not screenscraper_available():
        return False, "No credentials configured"

    try:
        client = ScreenScraperClient()
        data = client._make_request("ssuserInfos", {})

        if "response" in data and "ssuser" in data["response"]:
            return True, None
        return False, "Unexpected API response"

    except ScreenScraperRateLimited:
        # Don't mark as invalid on rate limit - assume valid
        return True, None
    except Exception as e:
        error_str = str(e).lower()
        if (
            "login" in error_str
            or "identifiant" in error_str
            or "credentials" in error_str
        ):
            return False, "Invalid credentials"
        return False, str(e)


def get_credentials_valid() -> bool | None:
    """Get cached credential validation status.

    Returns:
        True if validated successfully, False if validation failed, None if never validated
    """
    value = Setting.get(CREDENTIALS_VALID_KEY)
    if value is None:
        return None
    return value is True or value == "true"


def set_credentials_valid(is_valid: bool):
    """Cache the credential validation status."""
    Setting.set(CREDENTIALS_VALID_KEY, is_valid)


def clear_credentials_valid():
    """Clear cached validation status (when credentials change)."""
    Setting.objects.filter(key=CREDENTIALS_VALID_KEY).delete()


def screenscraper_usable() -> bool:
    """Check if ScreenScraper can be used (credentials exist AND are valid).

    Use this to gate metadata functionality instead of screenscraper_available().
    """
    if not screenscraper_available():
        return False
    valid = get_credentials_valid()
    # If never validated, assume usable. If explicitly invalid, not usable.
    return valid is not False


class ScreenScraperClient:
    """Client for ScreenScraper API."""

    def __init__(self):
        """Initialize client with credentials from settings."""
        self.last_request_time = 0
        self._load_credentials()

    def _load_credentials(self):
        """Load credentials from database, falling back to environment variables."""
        # User credentials: DB first, then env fallback
        self.username = Setting.get("screenscraper_username") or os.environ.get(
            "SCREENSCRAPER_USER"
        )
        self.password = Setting.get("screenscraper_password") or os.environ.get(
            "SCREENSCRAPER_PASSWORD"
        )

        # App identifier (devid, devpassword) from obfuscated constants
        self.devid, self.devpassword = _get_app_identifier()

        if not self.username or not self.password:
            logger.debug("ScreenScraper user credentials not configured.")

    def _check_credentials(self):
        """Check if credentials are configured."""
        if not self.username or not self.password:
            raise ValueError(
                "ScreenScraper credentials not configured. "
                "Set credentials in Settings > Metadata or via environment variables."
            )
        if not self.devid or not self.devpassword:
            raise ValueError("ScreenScraper app identifier not available.")

    def _rate_limit(self):
        """Enforce rate limiting between requests."""
        elapsed = time.time() - self.last_request_time
        if elapsed < RATE_LIMIT_DELAY:
            time.sleep(RATE_LIMIT_DELAY - elapsed)
        self.last_request_time = time.time()

    def _build_url(self, endpoint: str, params: dict[str, Any]) -> str:
        """Build full API URL with authentication params."""
        self._check_credentials()

        # Add authentication params
        auth_params = {
            "devid": self.devid,
            "devpassword": self.devpassword,
            "softname": "romhoard",
            "ssid": self.username,
            "sspassword": self.password,
            "output": "json",  # Request JSON format instead of XML
        }

        # Merge with request params
        all_params = {**auth_params, **params}

        # Build URL
        return f"{SCREENSCRAPER_API_BASE}{endpoint}.php?{urlencode(all_params)}"

    def _make_request(self, endpoint: str, params: dict[str, Any]) -> dict:
        """Make API request with rate limiting and error handling."""
        # Check if paused before making request
        pause_until = get_pause_until()
        if pause_until:
            raise ScreenScraperRateLimited(pause_until)

        self._rate_limit()

        url = self._build_url(endpoint, params)
        logger.debug(f"ScreenScraper request: {endpoint} with params {params}")

        try:
            response = requests.get(url, timeout=DEFAULT_TIMEOUT)

            # Check for rate limiting BEFORE raise_for_status
            if response.status_code in (429, 430):
                pause_until = set_pause_until()
                raise ScreenScraperRateLimited(pause_until)

            response.raise_for_status()

            data = response.json()

            # Check for API-level errors
            if "response" in data and "erreur" in data["response"]:
                error_msg = data["response"]["erreur"]
                logger.error(f"ScreenScraper API error: {error_msg}")
                raise Exception(f"ScreenScraper error: {error_msg}")

            return data

        except requests.exceptions.RequestException as e:
            logger.error(f"ScreenScraper request failed: {e}")
            raise

    def _extract_text(
        self, items: list[dict], language: str = "en", fallback: bool = True
    ) -> str:
        """Extract text from multilingual API response.

        Args:
            items: List of {langue/region: X, text: Y} dicts
            language: Preferred language code (e.g., 'en', 'fr')
            fallback: If True, return first available if preferred not found

        Returns:
            Extracted text or empty string
        """
        if not items:
            return ""

        # Try to find preferred language
        for item in items:
            lang_code = item.get("langue") or item.get("region", "")
            if lang_code == language:
                return item.get("text", "")

        # Fallback to first available
        if fallback and items:
            return items[0].get("text", "")

        return ""

    def _select_best_media(
        self,
        medias: list[dict],
        media_type: str,
        region_priority: list[str] | None = None,
    ) -> dict | None:
        """Select the best media of a given type by region priority.

        Args:
            medias: List of media dicts from API response
            media_type: The type of media to select (e.g., "wheel", "box-2D")
            region_priority: Optional custom region priority list. If None, uses default.

        Returns:
            The best matching media dict, or None if not found
        """
        candidates = [m for m in medias if m.get("type") == media_type and m.get("url")]
        if not candidates:
            return None

        # Use custom region priority if provided, otherwise default
        priority = region_priority if region_priority is not None else REGION_PRIORITY

        def region_score(m: dict) -> int:
            region = m.get("region", "").lower()
            try:
                return priority.index(region)
            except ValueError:
                return len(priority)  # Unknown regions last

        candidates.sort(key=region_score)
        return candidates[0]

    def search_by_crc(self, crc32: str, system_id: int) -> dict | None:
        """Search for a game by CRC32 hash.

        This is more accurate than name-based matching as it identifies
        the exact ROM dump.

        Args:
            crc32: CRC32 hash as 8-character hex string
            system_id: ScreenScraper system ID

        Returns:
            Game dict if found, None otherwise
        """
        if not crc32:
            return None

        params = {
            "crc": crc32.upper(),  # ScreenScraper expects uppercase
            "systemeid": system_id,
        }

        try:
            data = self._make_request("jeuInfos", params)

            # Parse response
            if "response" not in data or "jeu" not in data["response"]:
                logger.debug(f"No CRC32 match for {crc32} on system {system_id}")
                return None

            game = data["response"]["jeu"]
            logger.info(f"Found game by CRC32 {crc32}: {game.get('id')}")

            return {
                "id": game.get("id"),
                "name": self._extract_text(game.get("noms", [])),
                "all_names": [
                    n.get("text", "") for n in game.get("noms", []) if n.get("text")
                ],
                "system_id": system_id,
            }

        except Exception as e:
            logger.debug(f"CRC32 lookup failed for {crc32}: {e}")
            return None

    def has_credentials(self) -> bool:
        """Check if ScreenScraper credentials are configured.

        Returns:
            True if both username and password are set, False otherwise
        """
        return bool(self.username and self.password)

    def search_by_romnom(self, romnom: str, system_id: int) -> dict | None:
        """Search for an arcade game by ROM name (MAME short name).

        This is the most accurate method for arcade ROMs where the ZIP filename
        (e.g., 'pacman', 'galaga') directly identifies the game. Uses the
        jeuInfos endpoint with the romnom parameter.

        Args:
            romnom: ROM name (filename without extension, e.g., 'pacman')
            system_id: ScreenScraper system ID (75 for MAME/Arcade)

        Returns:
            Dict with 'id', 'name', 'system_id' if found, None otherwise
        """
        if not romnom:
            return None

        params = {
            "romnom": romnom,
            "systemeid": system_id,
        }

        try:
            data = self._make_request("jeuInfos", params)

            if "response" not in data or "jeu" not in data["response"]:
                logger.debug(f"No romnom match for '{romnom}' on system {system_id}")
                return None

            game = data["response"]["jeu"]
            game_id = game.get("id")
            game_name = self._extract_text(game.get("noms", []))

            logger.info(
                f"Found arcade game by romnom '{romnom}': ID={game_id}, Name='{game_name}'"
            )

            return {
                "id": game_id,
                "name": game_name,
                "system_id": system_id,
            }

        except Exception as e:
            # Don't log credentials errors as warnings - they're expected when not configured
            if "credentials" in str(e).lower():
                logger.debug(
                    "ScreenScraper credentials not configured for romnom lookup"
                )
                return None
            logger.debug(f"romnom lookup failed for '{romnom}': {e}")
            return None

    def search_game(self, name: str, system_id: int) -> list[dict]:
        """Search for games by name and system.

        Args:
            name: Game name to search
            system_id: ScreenScraper system ID

        Returns:
            List of matching game dicts with basic info
        """
        params = {
            "recherche": name,
            "systemeid": system_id,
        }

        data = self._make_request("jeuRecherche", params)

        # Parse response
        if "response" not in data or "jeux" not in data["response"]:
            return []

        games = data["response"]["jeux"]
        if not isinstance(games, list):
            games = [games]

        results = []
        for game in games:
            # Skip results with no ID (invalid/empty responses from API)
            game_id = game.get("id")
            if not game_id:
                continue
            results.append(
                {
                    "id": game_id,
                    "name": self._extract_text(game.get("noms", [])),
                    "all_names": [
                        n.get("text", "") for n in game.get("noms", []) if n.get("text")
                    ],
                    "system_id": system_id,
                }
            )

        return results

    def get_game_info(
        self,
        game_id: int | str,
        media_types: set[str] | None = None,
        game_name: str | None = None,
        system_id: int | None = None,
    ) -> dict:
        """Get full game details by ScreenScraper game ID.

        Falls back to 'jeuRecherche' if 'jeuInfos' times out or fails,
        provided game_name and system_id are supplied.

        Args:
            game_id: ScreenScraper game ID
            media_types: Optional set of media types to include.
            game_name: Optional game name for fallback search
            system_id: Optional system ID for fallback search

        Returns:
            Dict with game details
        """
        # Use default allowed media types if none specified
        if media_types is None:
            media_types = ALLOWED_MEDIA_TYPES

        game = None
        params = {"gameid": game_id}

        try:
            data = self._make_request("jeuInfos", params)
            if "response" in data and "jeu" in data["response"]:
                game = data["response"]["jeu"]

        except (
            requests.exceptions.ReadTimeout,
            requests.exceptions.RequestException,
        ) as e:
            # If rate limited, bubble up immediately
            if isinstance(e, ScreenScraperRateLimited):
                raise

            # If no fallback info, re-raise
            if not game_name or not system_id:
                logger.warning(f"ScreenScraper jeuInfos failed for ID {game_id}: {e}")
                raise

            logger.warning(
                f"ScreenScraper jeuInfos timed out/failed for ID {game_id}, "
                f"attempting fallback search for '{game_name}'"
            )

            # Fallback: Try searching for the game to get details
            # jeuRecherche usually returns a list, we need to find our ID
            search_params = {
                "recherche": game_name,
                "systemeid": system_id,
            }

            try:
                search_data = self._make_request("jeuRecherche", search_params)
                if "response" in search_data and "jeux" in search_data["response"]:
                    games_list = search_data["response"]["jeux"]
                    if not isinstance(games_list, list):
                        games_list = [games_list]

                    # Find the game with matching ID
                    for g in games_list:
                        # IDs are strings in JSON, but ensure comparison works
                        if str(g.get("id")) == str(game_id):
                            game = g
                            logger.info(
                                f"Successfully retrieved metadata via fallback search for ID {game_id}"
                            )
                            break
            except Exception as search_e:
                logger.error(f"Fallback search also failed: {search_e}")
                # Raise the original exception if fallback fails
                raise e

            # If still no game found after fallback
            if not game:
                logger.warning(f"Fallback search found results but not ID {game_id}")
                raise e

        if not game:
            return {}

        # Extract metadata (common logic for both endpoints)
        info = {
            "id": game.get("id"),
            "name": self._extract_text(game.get("noms", [])),
            "description": self._extract_text(game.get("synopsis", [])),
            "release_date": self._extract_text(game.get("dates", [])),
            "developer": game.get("developpeur", {}).get("text", ""),
            "publisher": game.get("editeur", {}).get("text", ""),
            "players": game.get("joueurs", {}).get("text", ""),
            "genres": [],
            "media": [],
        }

        # Extract genres
        genres_list = game.get("genres", [])
        if not isinstance(genres_list, list):
            genres_list = [genres_list]

        for genre in genres_list:
            genre_names = genre.get("noms", [])
            genre_text = self._extract_text(genre_names)
            if genre_text:
                info["genres"].append(genre_text)

        # Extract rating (note field in French API response, scale 0-20 to 0-100)
        note_text = game.get("note", {}).get("text", "")
        if note_text and note_text.isdigit():
            rating_20 = int(note_text)
            # Convert 0-20 scale to 0-100 scale
            rating_100 = (rating_20 / 20) * 100
            info["rating"] = int(rating_100)
            info["rating_source"] = "screenscraper"
        else:
            info["rating"] = None
            info["rating_source"] = None

        # Extract media URLs (filtered by allowed types, best region for each type)
        medias = game.get("medias", [])
        if not isinstance(medias, list):
            medias = [medias]

        # Select best media for each allowed type by region priority
        for media_type in media_types:
            best = self._select_best_media(medias, media_type)
            if best:
                info["media"].append({"type": media_type, "url": best["url"]})

        return info

    def download_image(self, url: str, save_path: str) -> str | None:
        """Download an image from URL to local path.

        Args:
            url: Image URL
            save_path: Local path to save image

        Returns:
            The file extension (e.g., '.jpg') if successful, None otherwise
        """
        self._rate_limit()

        try:
            response = requests.get(url, timeout=DEFAULT_TIMEOUT, stream=True)
            response.raise_for_status()

            # Detect extension from Content-Type header
            content_type = response.headers.get("Content-Type", "")
            ext_map = {
                "image/jpeg": ".jpg",
                "image/png": ".png",
                "image/gif": ".gif",
                "image/webp": ".webp",
            }
            # Strip any parameters from content-type (e.g., "image/jpeg; charset=utf-8")
            ext = ext_map.get(content_type.split(";")[0].strip(), ".png")

            with open(save_path, "wb") as f:
                for chunk in response.iter_content(chunk_size=8192):
                    f.write(chunk)

            logger.info(f"Downloaded image to {save_path}")
            return ext

        except Exception as e:
            logger.error(f"Failed to download image from {url}: {e}")
            return None

    def get_systems_list(self) -> list[dict]:
        """Fetch all systems from ScreenScraper.

        Returns:
            List of system dicts with id, name, release_year, icon_url
        """
        data = self._make_request("systemesListe", {})
        systems_raw = data.get("response", {}).get("systemes", [])

        results = []
        for sys in systems_raw:
            # Find icon URL from medias
            icon_url = None
            for media in sys.get("medias", []):
                if media.get("type") == "icon":
                    icon_url = media.get("url")
                    break

            results.append(
                {
                    "id": sys.get("id"),
                    "name": sys.get("noms", {}).get("nom_eu", ""),
                    "release_year": sys.get("datedebut", ""),
                    "icon_url": icon_url,
                }
            )

        return results
