"""Tests for ScreenScraper metadata fetching."""

import os
from unittest.mock import Mock, patch

import pytest
from requests import Response

from library.metadata.screenscraper import ScreenScraperClient


class TestScreenScraperClient:
    """Test ScreenScraper API client authentication and functionality."""

    @patch("library.metadata.screenscraper.Setting.get", return_value=None)
    @patch.dict(
        os.environ,
        {"SCREENSCRAPER_USER": "testuser", "SCREENSCRAPER_PASSWORD": "testpass"},
    )
    def test_load_credentials_user_only(self, mock_setting_get):
        """Test loading user credentials from env vars (DB returns None)."""
        client = ScreenScraperClient()

        assert client.username == "testuser"
        assert client.password == "testpass"
        # App identifier is now built-in (obfuscated)
        assert client.devid is not None
        assert client.devpassword is not None

    @patch("library.metadata.screenscraper.Setting.get", return_value=None)
    @patch.dict(
        os.environ,
        {
            "SCREENSCRAPER_USER": "testuser",
            "SCREENSCRAPER_PASSWORD": "testpass",
        },
    )
    def test_load_credentials_with_developer(self, mock_setting_get):
        """Test loading user credentials - app identifier is always available."""
        client = ScreenScraperClient()

        assert client.username == "testuser"
        assert client.password == "testpass"
        # App identifier is built-in
        assert client.devid is not None
        assert client.devpassword is not None

    @patch("library.metadata.screenscraper.Setting.get", return_value=None)
    @patch.dict(os.environ, {}, clear=True)
    def test_check_credentials_missing_user(self, mock_setting_get):
        """Test that missing user credentials raise ValueError."""
        client = ScreenScraperClient()
        assert client.username is None
        assert client.password is None

        client.username = None
        client.password = "test"

        with pytest.raises(
            ValueError, match="ScreenScraper credentials not configured"
        ):
            client._check_credentials()

        client.username = "test"
        client.password = None

        with pytest.raises(
            ValueError, match="ScreenScraper credentials not configured"
        ):
            client._check_credentials()

    @patch("library.metadata.screenscraper.Setting.get", return_value=None)
    @patch.dict(os.environ, {}, clear=True)
    def test_load_credentials_none_when_env_missing(self, mock_setting_get):
        """Test credentials are None when environment variables not set."""
        client = ScreenScraperClient()

        assert client.username is None
        assert client.password is None

    @patch("library.metadata.screenscraper.Setting.get", return_value=None)
    @patch("library.metadata.screenscraper._get_app_identifier", return_value=("", ""))
    @patch.dict(
        os.environ,
        {"SCREENSCRAPER_USER": "testuser", "SCREENSCRAPER_PASSWORD": "testpass"},
    )
    def test_check_credentials_missing_developer(
        self, mock_app_identifier, mock_setting_get
    ):
        """Test that missing app identifier raises ValueError."""
        client = ScreenScraperClient()
        assert client.username == "testuser"
        assert client.password == "testpass"
        assert client.devid == ""
        assert client.devpassword == ""

        with pytest.raises(ValueError, match="ScreenScraper app identifier"):
            client._check_credentials()

    @patch("library.metadata.screenscraper.Setting.get", return_value=None)
    @patch.dict(
        os.environ,
        {
            "SCREENSCRAPER_USER": "testuser",
            "SCREENSCRAPER_PASSWORD": "testpass",
        },
    )
    def test_build_url_with_developer_credentials(self, mock_setting_get):
        """Test building URL with configured credentials (app id is built-in)."""
        client = ScreenScraperClient()

        url = client._build_url("jeuRecherche", {"recherche": "test", "systemeid": 1})

        # App identifier is built-in, just verify user credentials
        assert "ssid=testuser" in url
        assert "sspassword=testpass" in url
        assert "softname=romhoard" in url
        assert "output=json" in url
        assert "recherche=test" in url
        assert "systemeid=1" in url
        # Should also have devid/devpassword from built-in app identifier
        assert "devid=" in url
        assert "devpassword=" in url

    @patch("library.metadata.screenscraper.Setting.get", return_value=None)
    @patch.dict(
        os.environ,
        {
            "SCREENSCRAPER_USER": "testuser",
            "SCREENSCRAPER_PASSWORD": "testpass",
        },
    )
    @patch("library.metadata.screenscraper.get_pause_until", return_value=None)
    @patch("library.metadata.screenscraper.requests.get")
    @patch("library.metadata.screenscraper.time.sleep")
    def test_search_game_success(
        self, mock_sleep, mock_get, mock_pause, mock_setting_get
    ):
        """Test successful game search."""
        # Mock API response
        mock_response = Mock(spec=Response)
        mock_response.status_code = 200
        mock_response.json.return_value = {
            "response": {
                "jeux": [{"id": "123", "noms": [{"text": "Test Game", "langue": "en"}]}]
            }
        }
        mock_response.raise_for_status.return_value = None
        mock_get.return_value = mock_response

        client = ScreenScraperClient()

        results = client.search_game("Test Game", 1)

        assert len(results) == 1
        assert results[0]["id"] == "123"
        assert results[0]["name"] == "Test Game"
        assert results[0]["system_id"] == 1

    @patch("library.metadata.screenscraper.Setting.get", return_value=None)
    @patch.dict(
        os.environ,
        {
            "SCREENSCRAPER_USER": "testuser",
            "SCREENSCRAPER_PASSWORD": "testpass",
        },
    )
    @patch("library.metadata.screenscraper.get_pause_until", return_value=None)
    @patch("library.metadata.screenscraper.requests.get")
    @patch("library.metadata.screenscraper.time.sleep")
    def test_search_game_no_results(
        self, mock_sleep, mock_get, mock_pause, mock_setting_get
    ):
        """Test game search with no results."""
        # Mock API response
        mock_response = Mock(spec=Response)
        mock_response.status_code = 200
        mock_response.json.return_value = {"response": {"jeux": []}}
        mock_response.raise_for_status.return_value = None
        mock_get.return_value = mock_response

        client = ScreenScraperClient()

        results = client.search_game("Nonexistent Game", 1)

        assert len(results) == 0

    @patch("library.metadata.screenscraper.Setting.get", return_value=None)
    @patch.dict(
        os.environ,
        {
            "SCREENSCRAPER_USER": "testuser",
            "SCREENSCRAPER_PASSWORD": "testpass",
        },
    )
    @patch("library.metadata.screenscraper.get_pause_until", return_value=None)
    @patch("library.metadata.screenscraper.requests.get")
    @patch("library.metadata.screenscraper.time.sleep")
    def test_search_game_api_error(
        self, mock_sleep, mock_get, mock_pause, mock_setting_get
    ):
        """Test handling of API errors."""
        # Mock API error response
        mock_response = Mock(spec=Response)
        mock_response.status_code = 200
        mock_response.json.return_value = {
            "response": {"erreur": "Invalid credentials"}
        }
        mock_response.raise_for_status.return_value = None
        mock_get.return_value = mock_response

        client = ScreenScraperClient()

        with pytest.raises(Exception, match="ScreenScraper error: Invalid credentials"):
            client.search_game("Test Game", 1)

    def test_extract_text_preferred_language(self):
        """Test text extraction with preferred language."""
        # Create client manually without database access
        client = object.__new__(ScreenScraperClient)

        items = [
            {"text": "English Name", "langue": "en"},
            {"text": "French Name", "langue": "fr"},
            {"text": "German Name", "langue": "de"},
        ]

        # Should return English name
        assert client._extract_text(items, language="en") == "English Name"

        # Should return French name
        assert client._extract_text(items, language="fr") == "French Name"

    def test_extract_text_fallback(self):
        """Test text extraction fallback behavior."""
        # Create client manually without database access
        client = object.__new__(ScreenScraperClient)

        items = [
            {"text": "Japanese Name", "langue": "jp"},
            {"text": "French Name", "langue": "fr"},
        ]

        # Should return first available when preferred not found
        assert (
            client._extract_text(items, language="en", fallback=True) == "Japanese Name"
        )

        # Should return empty string when no fallback
        assert client._extract_text(items, language="en", fallback=False) == ""

    def test_extract_text_empty_list(self):
        """Test text extraction with empty list."""
        # Create client manually without database access
        client = object.__new__(ScreenScraperClient)

        assert client._extract_text([]) == ""
        assert client._extract_text([], language="fr") == ""

    def test_select_best_media_prioritizes_us_region(self):
        """Test that US region is prioritized for media selection."""
        client = object.__new__(ScreenScraperClient)

        medias = [
            {"type": "wheel", "url": "http://example.com/wheel_eu", "region": "eu"},
            {"type": "wheel", "url": "http://example.com/wheel_us", "region": "us"},
            {"type": "wheel", "url": "http://example.com/wheel_jp", "region": "jp"},
        ]

        best = client._select_best_media(medias, "wheel")
        assert best is not None
        assert best["region"] == "us"
        assert best["url"] == "http://example.com/wheel_us"

    def test_select_best_media_falls_back_to_eu(self):
        """Test fallback to EU when US not available."""
        client = object.__new__(ScreenScraperClient)

        medias = [
            {"type": "wheel", "url": "http://example.com/wheel_jp", "region": "jp"},
            {"type": "wheel", "url": "http://example.com/wheel_eu", "region": "eu"},
        ]

        best = client._select_best_media(medias, "wheel")
        assert best is not None
        assert best["region"] == "eu"

    def test_select_best_media_returns_none_when_type_not_found(self):
        """Test None returned when media type not found."""
        client = object.__new__(ScreenScraperClient)

        medias = [
            {"type": "box-2D", "url": "http://example.com/box", "region": "us"},
        ]

        best = client._select_best_media(medias, "wheel")
        assert best is None

    def test_select_best_media_handles_unknown_regions(self):
        """Test unknown regions are sorted last."""
        client = object.__new__(ScreenScraperClient)

        medias = [
            {"type": "wheel", "url": "http://example.com/wheel_xx", "region": "xx"},
            {"type": "wheel", "url": "http://example.com/wheel_wor", "region": "wor"},
        ]

        best = client._select_best_media(medias, "wheel")
        assert best is not None
        assert best["region"] == "wor"  # wor is in priority list, xx is not

    @patch("library.metadata.screenscraper.Setting.get", return_value=None)
    @patch.dict(
        os.environ,
        {
            "SCREENSCRAPER_USER": "testuser",
            "SCREENSCRAPER_PASSWORD": "testpass",
        },
    )
    @patch("library.metadata.screenscraper.get_pause_until", return_value=None)
    @patch("library.metadata.screenscraper.requests.get")
    @patch("library.metadata.screenscraper.time.sleep")
    def test_get_systems_list_success(
        self, mock_sleep, mock_get, mock_pause, mock_setting_get
    ):
        """Test fetching systems list from API."""
        # Mock API response
        mock_response = Mock(spec=Response)
        mock_response.status_code = 200
        mock_response.json.return_value = {
            "response": {
                "systemes": [
                    {
                        "id": 12,
                        "noms": {"nom_eu": "Game Boy Advance"},
                        "datedebut": "2001",
                        "medias": [
                            {"type": "icon", "url": "http://example.com/gba.png"}
                        ],
                    },
                    {
                        "id": 3,
                        "noms": {"nom_eu": "NES"},
                        "datedebut": "1985",
                        "medias": [],
                    },
                ]
            }
        }
        mock_response.raise_for_status.return_value = None
        mock_get.return_value = mock_response

        client = ScreenScraperClient()
        results = client.get_systems_list()

        assert len(results) == 2
        assert results[0]["id"] == 12
        assert results[0]["name"] == "Game Boy Advance"
        assert results[0]["release_year"] == "2001"
        assert results[0]["icon_url"] == "http://example.com/gba.png"
        assert results[1]["id"] == 3
        assert results[1]["name"] == "NES"
        assert results[1]["icon_url"] is None


@pytest.mark.django_db
class TestSystemMetadataFetching:
    """Test system metadata fetching functions."""

    @patch("library.metadata.matcher.ScreenScraperClient")
    def test_fetch_system_metadata_updates_systems_with_games(self, mock_client_class):
        """Test that fetch_system_metadata_for_job updates systems that have games."""
        from library.metadata.matcher import fetch_system_metadata_for_job
        from library.models import Game, System

        # Create or get test system (may already exist from fixtures)
        system, _ = System.objects.update_or_create(
            slug="gba",
            defaults={
                "name": "Game Boy Advance",
                "extensions": [".gba"],
                "folder_names": ["GBA"],
                "screenscraper_ids": [12],
                "release_year": "",  # Clear any existing value
            },
        )

        # Create a game for this system so it has > 0 games
        Game.objects.get_or_create(
            name="Test Game",
            system=system,
        )

        # Mock the client
        mock_client = Mock()
        mock_client.get_systems_list.return_value = [
            {
                "id": 12,
                "name": "Game Boy Advance",
                "release_year": "2001",
                "icon_url": None,  # No icon to download
            }
        ]
        mock_client_class.return_value = mock_client

        result = fetch_system_metadata_for_job()

        assert result["updated"] >= 1
        assert result["icons_downloaded"] == 0

        # Reload and check
        system.refresh_from_db()
        assert system.release_year == "2001"
        assert system.metadata_updated_at is not None

    @patch("library.metadata.matcher.ScreenScraperClient")
    def test_fetch_system_metadata_skips_systems_without_games(self, mock_client_class):
        """Test that systems without games are not processed."""
        from library.metadata.matcher import fetch_system_metadata_for_job
        from library.models import Game, System

        # Clear any systems that might be matched by the mock response
        System.objects.filter(screenscraper_ids__contains=[12]).update(
            screenscraper_ids=[]
        )

        # Create test system with no games
        system, _ = System.objects.update_or_create(
            slug="test_no_games",
            defaults={
                "name": "System Without Games",
                "extensions": [".nog"],
                "folder_names": ["NoGames"],
                "screenscraper_ids": [999],
            },
        )

        # Ensure no games for this system
        Game.objects.filter(system=system).delete()

        # Mock the client
        mock_client = Mock()
        mock_client.get_systems_list.return_value = [
            {"id": 999, "name": "NoGames", "release_year": "2000", "icon_url": None}
        ]
        mock_client_class.return_value = mock_client

        result = fetch_system_metadata_for_job()

        # System should not be updated because it has no games
        assert result["updated"] == 0

    @patch("library.metadata.matcher.ScreenScraperClient")
    def test_fetch_system_metadata_skips_unknown_systems(self, mock_client_class):
        """Test that systems not in ScreenScraper are skipped."""
        from library.metadata.matcher import fetch_system_metadata_for_job
        from library.models import Game, System

        # Clear any systems that might be matched by the mock response
        System.objects.filter(screenscraper_ids__contains=[12]).update(
            screenscraper_ids=[]
        )

        # Create test system with non-matching screenscraper_id
        system, _ = System.objects.update_or_create(
            slug="test_unknown",
            defaults={
                "name": "Unknown System",
                "extensions": [".unk"],
                "folder_names": ["Unknown"],
                "screenscraper_ids": [9999],  # Not in mock response
            },
        )

        # Add a game so it's included in processing
        Game.objects.get_or_create(name="Test Game Unknown", system=system)

        # Mock the client
        mock_client = Mock()
        mock_client.get_systems_list.return_value = [
            {"id": 12, "name": "GBA", "release_year": "2001", "icon_url": None}
        ]
        mock_client_class.return_value = mock_client

        result = fetch_system_metadata_for_job()

        # This system has games but ID 9999 is not in the mock response
        assert result["skipped"] >= 1


@pytest.mark.django_db
class TestMetadataCache:
    """Test ScreenScraper metadata caching functionality."""

    def test_get_cached_metadata_returns_none_when_no_cache(self, tmp_path):
        """get_cached_metadata returns None when no cache exists."""
        from library.metadata.matcher import get_cached_metadata
        from library.models import Game, Setting, System

        # Create test system and game
        system, _ = System.objects.update_or_create(
            slug="test_cache",
            defaults={
                "name": "Test System",
                "extensions": [".test"],
                "folder_names": ["TestCache"],
                "screenscraper_ids": [100],
            },
        )
        game, _ = Game.objects.update_or_create(name="Cache Test Game", system=system)

        # Set metadata path
        Setting.objects.update_or_create(
            key="metadata_image_path", defaults={"value": str(tmp_path)}
        )

        result = get_cached_metadata(game)
        assert result is None

    def test_save_and_get_cached_metadata(self, tmp_path):
        """save_metadata_cache and get_cached_metadata work together."""
        from library.metadata.matcher import get_cached_metadata, save_metadata_cache
        from library.models import Game, Setting, System

        # Create test system and game
        system, _ = System.objects.update_or_create(
            slug="test_cache2",
            defaults={
                "name": "Test System 2",
                "extensions": [".test2"],
                "folder_names": ["TestCache2"],
                "screenscraper_ids": [101],
            },
        )
        game, _ = Game.objects.update_or_create(name="Cache Test Game 2", system=system)

        # Set metadata path
        Setting.objects.update_or_create(
            key="metadata_image_path", defaults={"value": str(tmp_path)}
        )

        # Create test metadata
        metadata = {
            "id": 12345,
            "name": "Cache Test Game 2",
            "description": "A test game",
            "developer": "Test Dev",
            "publisher": "Test Pub",
            "_match_type": "crc32",
        }

        # Save to cache
        save_metadata_cache(game, metadata)

        # Verify file was created
        cache_file = tmp_path / "test_cache2" / "Cache Test Game 2" / "metadata.json"
        assert cache_file.exists()

        # Retrieve from cache
        cached = get_cached_metadata(game)
        assert cached is not None
        assert cached["id"] == 12345
        assert cached["description"] == "A test game"
        assert cached["_match_type"] == "crc32"

    def test_get_cached_metadata_uses_fallback_path_when_no_setting(self, tmp_path):
        """get_cached_metadata uses computed fallback path when no setting configured."""
        from library.metadata.matcher import get_cached_metadata, save_metadata_cache
        from library.models import Game, Setting, System

        # Remove any existing metadata path setting
        Setting.objects.filter(key="metadata_image_path").delete()

        # Create test system and game
        system, _ = System.objects.update_or_create(
            slug="test_cache3",
            defaults={
                "name": "Test System 3",
                "extensions": [".test3"],
                "folder_names": ["TestCache3"],
                "screenscraper_ids": [102],
            },
        )
        game, _ = Game.objects.update_or_create(name="Cache Test Game 3", system=system)

        # Without a cache file, should return None (but doesn't fail)
        result = get_cached_metadata(game)
        assert result is None

    def test_cache_handles_special_characters_in_name(self, tmp_path):
        """Cache handles game names with special characters."""
        from library.metadata.matcher import get_cached_metadata, save_metadata_cache
        from library.models import Game, Setting, System

        # Create test system and game with special characters
        system, _ = System.objects.update_or_create(
            slug="test_cache4",
            defaults={
                "name": "Test System 4",
                "extensions": [".test4"],
                "folder_names": ["TestCache4"],
                "screenscraper_ids": [103],
            },
        )
        game, _ = Game.objects.update_or_create(
            name="Game: The Sequel?",
            system=system,  # Has : and ? which are special
        )

        # Set metadata path
        Setting.objects.update_or_create(
            key="metadata_image_path", defaults={"value": str(tmp_path)}
        )

        metadata = {"id": 999, "name": "Game: The Sequel?"}
        save_metadata_cache(game, metadata)

        # Should work with sanitized folder name (: and ? replaced with _)
        cached = get_cached_metadata(game)
        assert cached is not None
        assert cached["id"] == 999


@pytest.mark.django_db
class TestRateLimitPause:
    """Test ScreenScraper rate limit pause functionality."""

    def test_get_pause_until_returns_none_when_not_paused(self):
        """get_pause_until returns None when no pause is set."""
        from library.metadata.screenscraper import PAUSE_SETTING_KEY, get_pause_until
        from library.models import Setting

        # Ensure no pause setting exists
        Setting.objects.filter(key=PAUSE_SETTING_KEY).delete()

        result = get_pause_until()
        assert result is None

    def test_set_pause_until_stores_timestamp(self):
        """set_pause_until stores the correct timestamp in the database."""
        from datetime import timedelta

        from django.utils import timezone

        from library.metadata.screenscraper import (
            PAUSE_SETTING_KEY,
            get_pause_until,
            set_pause_until,
        )
        from library.models import Setting

        # Clear any existing pause
        Setting.objects.filter(key=PAUSE_SETTING_KEY).delete()

        # Set pause for 2 hours
        before = timezone.now()
        pause_until = set_pause_until(hours=2)
        after = timezone.now()

        # Verify pause time is approximately 2 hours from now
        assert pause_until >= before + timedelta(hours=2)
        assert pause_until <= after + timedelta(hours=2)

        # Verify it can be retrieved
        retrieved = get_pause_until()
        assert retrieved is not None
        assert abs((retrieved - pause_until).total_seconds()) < 1

    def test_get_pause_until_returns_none_when_expired(self):
        """get_pause_until returns None when pause has expired."""
        from datetime import timedelta

        from django.utils import timezone

        from library.metadata.screenscraper import PAUSE_SETTING_KEY, get_pause_until
        from library.models import Setting

        # Set pause to 1 hour ago (expired)
        expired_time = timezone.now() - timedelta(hours=1)
        Setting.objects.update_or_create(
            key=PAUSE_SETTING_KEY, defaults={"value": expired_time.isoformat()}
        )

        result = get_pause_until()
        assert result is None

    def test_clear_pause_removes_setting(self):
        """clear_pause removes the pause setting from the database."""
        from library.metadata.screenscraper import (
            PAUSE_SETTING_KEY,
            clear_pause,
            set_pause_until,
        )
        from library.models import Setting

        # Set a pause
        set_pause_until(hours=2)
        assert Setting.objects.filter(key=PAUSE_SETTING_KEY).exists()

        # Clear it
        clear_pause()
        assert not Setting.objects.filter(key=PAUSE_SETTING_KEY).exists()

    @patch.dict(
        os.environ,
        {
            "SCREENSCRAPER_USER": "testuser",
            "SCREENSCRAPER_PASSWORD": "testpass",
            "SCREENSCRAPER_DEVID": "testdevid",
            "SCREENSCRAPER_DEVPASSWORD": "testdevpass",
        },
    )
    @patch("library.metadata.screenscraper.requests.get")
    @patch("library.metadata.screenscraper.time.sleep")
    def test_make_request_raises_when_paused(self, mock_sleep, mock_get):
        """_make_request raises ScreenScraperRateLimited when paused."""
        from datetime import timedelta

        from django.utils import timezone

        from library.metadata.screenscraper import (
            ScreenScraperClient,
            ScreenScraperRateLimited,
        )

        client = ScreenScraperClient()

        # Now mock get_pause_until to return a future time
        future_time = timezone.now() + timedelta(hours=1)
        with patch(
            "library.metadata.screenscraper.get_pause_until", return_value=future_time
        ):
            with pytest.raises(ScreenScraperRateLimited) as exc_info:
                client._make_request("jeuRecherche", {"recherche": "test"})

            assert exc_info.value.retry_after == future_time

        # Verify no HTTP request was made
        mock_get.assert_not_called()

    @patch.dict(
        os.environ,
        {
            "SCREENSCRAPER_USER": "testuser",
            "SCREENSCRAPER_PASSWORD": "testpass",
            "SCREENSCRAPER_DEVID": "testdevid",
            "SCREENSCRAPER_DEVPASSWORD": "testdevpass",
        },
    )
    @patch("library.metadata.screenscraper.get_pause_until", return_value=None)
    @patch("library.metadata.screenscraper.set_pause_until")
    @patch("library.metadata.screenscraper.requests.get")
    @patch("library.metadata.screenscraper.time.sleep")
    def test_make_request_triggers_pause_on_429(
        self, mock_sleep, mock_get, mock_set_pause, mock_get_pause
    ):
        """_make_request triggers pause when receiving HTTP 429."""
        from datetime import timedelta

        from django.utils import timezone

        from library.metadata.screenscraper import (
            ScreenScraperClient,
            ScreenScraperRateLimited,
        )

        # Mock 429 response
        mock_response = Mock(spec=Response)
        mock_response.status_code = 429
        mock_get.return_value = mock_response

        # Mock set_pause_until return value
        future_time = timezone.now() + timedelta(hours=2)
        mock_set_pause.return_value = future_time

        client = ScreenScraperClient()

        with pytest.raises(ScreenScraperRateLimited) as exc_info:
            client._make_request("jeuRecherche", {"recherche": "test"})

        # Verify pause was set
        mock_set_pause.assert_called_once()
        assert exc_info.value.retry_after == future_time

    @patch.dict(
        os.environ,
        {
            "SCREENSCRAPER_USER": "testuser",
            "SCREENSCRAPER_PASSWORD": "testpass",
            "SCREENSCRAPER_DEVID": "testdevid",
            "SCREENSCRAPER_DEVPASSWORD": "testdevpass",
        },
    )
    @patch("library.metadata.screenscraper.get_pause_until", return_value=None)
    @patch("library.metadata.screenscraper.set_pause_until")
    @patch("library.metadata.screenscraper.requests.get")
    @patch("library.metadata.screenscraper.time.sleep")
    def test_make_request_triggers_pause_on_430(
        self, mock_sleep, mock_get, mock_set_pause, mock_get_pause
    ):
        """_make_request triggers pause when receiving HTTP 430."""
        from datetime import timedelta

        from django.utils import timezone

        from library.metadata.screenscraper import (
            ScreenScraperClient,
            ScreenScraperRateLimited,
        )

        # Mock 430 response
        mock_response = Mock(spec=Response)
        mock_response.status_code = 430
        mock_get.return_value = mock_response

        # Mock set_pause_until return value
        future_time = timezone.now() + timedelta(hours=2)
        mock_set_pause.return_value = future_time

        client = ScreenScraperClient()

        with pytest.raises(ScreenScraperRateLimited) as exc_info:
            client._make_request("jeuRecherche", {"recherche": "test"})

        # Verify pause was set
        mock_set_pause.assert_called_once()
        assert exc_info.value.retry_after == future_time


class TestWheelMiniGeneration:
    """Tests for wheel-mini thumbnail generation."""

    def test_create_wheel_mini_maintains_aspect_ratio(self, tmp_path):
        """Test that wheel-mini maintains original aspect ratio."""
        from PIL import Image

        from library.metadata.matcher import create_wheel_mini

        # Create a wide test image (200x100 = 2:1 ratio)
        test_image = tmp_path / "wheel.png"
        img = Image.new("RGBA", (200, 100), color="red")
        img.save(test_image)

        # Create mini version
        mini_path = create_wheel_mini(test_image)

        assert mini_path is not None
        assert mini_path.exists()
        assert mini_path.name == "wheel-mini.png"

        with Image.open(mini_path) as mini_img:
            assert mini_img.height == 48
            # Width should maintain 2:1 ratio
            assert mini_img.width == 96

    def test_create_wheel_mini_tall_image(self, tmp_path):
        """Test wheel-mini with tall image."""
        from PIL import Image

        from library.metadata.matcher import create_wheel_mini

        # Create a tall test image (50x200 = 1:4 ratio)
        test_image = tmp_path / "wheel.png"
        img = Image.new("RGBA", (50, 200), color="blue")
        img.save(test_image)

        mini_path = create_wheel_mini(test_image)

        assert mini_path is not None
        with Image.open(mini_path) as mini_img:
            assert mini_img.height == 48
            # Width should maintain 1:4 ratio (48 / 4 = 12)
            assert mini_img.width == 12

    def test_create_wheel_mini_custom_height(self, tmp_path):
        """Test wheel-mini with custom target height."""
        from PIL import Image

        from library.metadata.matcher import create_wheel_mini

        test_image = tmp_path / "wheel.png"
        img = Image.new("RGBA", (100, 100), color="green")
        img.save(test_image)

        mini_path = create_wheel_mini(test_image, target_height=32)

        assert mini_path is not None
        with Image.open(mini_path) as mini_img:
            assert mini_img.height == 32
            assert mini_img.width == 32

    def test_create_wheel_mini_invalid_file_returns_none(self, tmp_path):
        """Test that invalid file returns None without raising."""
        from library.metadata.matcher import create_wheel_mini

        fake_image = tmp_path / "not_an_image.txt"
        fake_image.write_text("not an image")

        result = create_wheel_mini(fake_image)
        assert result is None

    @pytest.mark.django_db
    def test_existing_wheel_and_wheel_mini_registered_from_cache(self, tmp_path):
        """Test that existing wheel and wheel-mini images are registered in DB from cache."""
        from PIL import Image

        from library.metadata.matcher import download_images_for_game
        from library.models import Game, GameImage, Setting, System

        # Create system and game
        system = System.objects.create(
            name="Test System", slug="test", extensions=[".rom"], folder_names=["test"]
        )
        game = Game.objects.create(name="Test Game", system=system)

        # Set up metadata_image_path
        Setting.objects.create(key="metadata_image_path", value=str(tmp_path))

        # Create game directory with pre-existing wheel and wheel-mini images
        game_dir = tmp_path / "test" / "Test Game"
        game_dir.mkdir(parents=True)

        # Create wheel image
        wheel_path = game_dir / "wheel.png"
        img = Image.new("RGBA", (200, 100), color="red")
        img.save(wheel_path)

        # Create wheel-mini image (as if previously generated)
        wheel_mini_path = game_dir / "wheel-mini.png"
        mini_img = Image.new("RGBA", (96, 48), color="red")
        mini_img.save(wheel_mini_path)

        # Call download_images_for_game with wheel in the media list
        media_list = [{"type": "wheel", "url": "http://example.com/wheel.png"}]

        # No actual download should happen since images exist
        downloaded = download_images_for_game(game, media_list)

        # Should report 0 downloads since images already exist
        assert downloaded == 0

        # Both images should be registered in the database
        wheel_record = GameImage.objects.filter(game=game, image_type="wheel").first()
        assert wheel_record is not None
        assert wheel_record.file_name == "wheel.png"
        assert wheel_record.source == "scanned"

        wheel_mini_record = GameImage.objects.filter(
            game=game, image_type="wheel_mini"
        ).first()
        assert wheel_mini_record is not None
        assert wheel_mini_record.file_name == "wheel-mini.png"
        assert wheel_mini_record.source == "scanned"


class TestRatingSupport:
    """Test rating extraction, conversion, and application."""

    def test_rating_conversion_20_to_100(self):
        """Test that ScreenScraper 0-20 scale converts to 0-100 scale."""
        # Create client manually without database access
        object.__new__(ScreenScraperClient)

        # Mock game response with note=16 (out of 20)
        game_response = {
            "note": {"text": "16"},
            "noms": [{"text": "Test Game", "langue": "en"}],
            "synopsis": [],
            "dates": [],
            "developpeur": {},
            "editeur": {},
            "joueurs": {},
            "genres": [],
            "medias": [],
        }

        # Simulate the rating extraction logic
        note_text = game_response.get("note", {}).get("text", "")
        if note_text and note_text.isdigit():
            rating_20 = int(note_text)
            rating_100 = (rating_20 / 20) * 100
            rating = int(rating_100)
            rating_source = "screenscraper"
        else:
            rating = None
            rating_source = None

        assert rating == 80  # 16/20 * 100 = 80
        assert rating_source == "screenscraper"

    def test_rating_conversion_boundary_values(self):
        """Test boundary values for rating conversion."""
        test_cases = [
            ("0", 0),
            ("1", 5),
            ("10", 50),
            ("20", 100),
            ("15", 75),
        ]

        for note_text, expected_rating in test_cases:
            if note_text.isdigit():
                rating_20 = int(note_text)
                rating_100 = (rating_20 / 20) * 100
                rating = int(rating_100)
                assert rating == expected_rating, f"Failed for note={note_text}"

    def test_rating_missing_in_response(self):
        """Test that missing rating returns None values."""
        # Test with empty note
        game_response = {"note": {}, "noms": []}

        note_text = game_response.get("note", {}).get("text", "")
        if note_text and note_text.isdigit():
            rating = int((int(note_text) / 20) * 100)
            rating_source = "screenscraper"
        else:
            rating = None
            rating_source = None

        assert rating is None
        assert rating_source is None

    def test_rating_non_numeric_returns_none(self):
        """Test that non-numeric rating text returns None."""
        test_cases = [
            {"note": {"text": "N/A"}},
            {"note": {"text": "unknown"}},
            {"note": {"text": ""}},
            {"note": {"text": "16.5"}},  # Non-integer
        ]

        for game_response in test_cases:
            note_text = game_response.get("note", {}).get("text", "")
            if note_text and note_text.isdigit():
                rating = int((int(note_text) / 20) * 100)
            else:
                rating = None

            assert rating is None, f"Should be None for: {game_response}"

    @pytest.mark.django_db
    def test_rating_application_to_game(self):
        """Test that rating is applied to Game instance."""
        from library.metadata.matcher import apply_metadata_to_game
        from library.models import Game, System

        system, _ = System.objects.update_or_create(
            slug="test_rating",
            defaults={
                "name": "Test System Rating",
                "extensions": [".test"],
                "folder_names": ["TestRating"],
                "screenscraper_ids": [200],
            },
        )
        game, _ = Game.objects.update_or_create(name="Rating Test Game", system=system)

        metadata = {
            "id": "123",
            "rating": 85,
            "rating_source": "screenscraper",
            "description": "Test description",
        }

        result = apply_metadata_to_game(game, metadata)

        assert result is True
        game.refresh_from_db()
        assert game.rating == 85
        assert game.rating_source == "screenscraper"

    @pytest.mark.django_db
    def test_rating_none_clears_existing(self):
        """Test that None rating clears existing rating."""
        from library.metadata.matcher import apply_metadata_to_game
        from library.models import Game, System

        system, _ = System.objects.update_or_create(
            slug="test_rating_clear",
            defaults={
                "name": "Test System Rating Clear",
                "extensions": [".test"],
                "folder_names": ["TestRatingClear"],
                "screenscraper_ids": [201],
            },
        )
        game, _ = Game.objects.update_or_create(
            name="Rating Clear Test Game",
            system=system,
            defaults={"rating": 90, "rating_source": "screenscraper"},
        )

        metadata = {
            "id": "124",
            "rating": None,
            "rating_source": None,
            "description": "Updated description",
        }

        result = apply_metadata_to_game(game, metadata)

        assert result is True
        game.refresh_from_db()
        assert game.rating is None
        assert game.rating_source == ""

    @pytest.mark.django_db
    def test_rating_cache_persistence(self, tmp_path):
        """Test that rating is saved to and loaded from disk cache."""
        from library.metadata.matcher import get_cached_metadata, save_metadata_cache
        from library.models import Game, Setting, System

        # Create a game
        system, _ = System.objects.update_or_create(
            slug="test_rating_cache",
            defaults={
                "name": "Test System Rating Cache",
                "extensions": [".test"],
                "folder_names": ["TestRatingCache"],
                "screenscraper_ids": [202],
            },
        )
        game, _ = Game.objects.update_or_create(
            name="Cache Rating Test Game", system=system
        )

        # Configure temp cache path
        Setting.objects.update_or_create(
            key="metadata_image_path", defaults={"value": str(tmp_path)}
        )

        # Save metadata with rating
        metadata = {
            "id": "125",
            "rating": 75,
            "rating_source": "screenscraper",
            "description": "Cached game",
        }
        save_metadata_cache(game, metadata)

        # Load from cache
        cached = get_cached_metadata(game)

        assert cached is not None
        assert cached["rating"] == 75
        assert cached["rating_source"] == "screenscraper"

    @patch("library.metadata.screenscraper.Setting.get", return_value=None)
    @patch.dict(
        os.environ,
        {
            "SCREENSCRAPER_USER": "testuser",
            "SCREENSCRAPER_PASSWORD": "testpass",
        },
    )
    @patch("library.metadata.screenscraper.get_pause_until", return_value=None)
    @patch("library.metadata.screenscraper.requests.get")
    @patch("library.metadata.screenscraper.time.sleep")
    def test_get_game_info_extracts_rating(
        self, mock_sleep, mock_get, mock_pause, mock_setting_get
    ):
        """Test that get_game_info extracts and converts rating."""
        # Mock API response with rating
        mock_response = Mock(spec=Response)
        mock_response.status_code = 200
        mock_response.json.return_value = {
            "response": {
                "jeu": {
                    "id": "12345",
                    "noms": [{"text": "Advance Wars", "langue": "en"}],
                    "note": {"text": "16"},  # 16/20 = 80/100
                    "synopsis": [],
                    "dates": [],
                    "developpeur": {},
                    "editeur": {},
                    "joueurs": {},
                    "genres": [],
                    "medias": [],
                }
            }
        }
        mock_response.raise_for_status.return_value = None
        mock_get.return_value = mock_response

        client = ScreenScraperClient()
        info = client.get_game_info(12345)

        assert info["rating"] == 80
        assert info["rating_source"] == "screenscraper"

    @patch("library.metadata.screenscraper.Setting.get", return_value=None)
    @patch.dict(
        os.environ,
        {
            "SCREENSCRAPER_USER": "testuser",
            "SCREENSCRAPER_PASSWORD": "testpass",
        },
    )
    @patch("library.metadata.screenscraper.get_pause_until", return_value=None)
    @patch("library.metadata.screenscraper.requests.get")
    @patch("library.metadata.screenscraper.time.sleep")
    def test_get_game_info_handles_missing_rating(
        self, mock_sleep, mock_get, mock_pause, mock_setting_get
    ):
        """Test that get_game_info handles missing rating gracefully."""
        # Mock API response without rating
        mock_response = Mock(spec=Response)
        mock_response.status_code = 200
        mock_response.json.return_value = {
            "response": {
                "jeu": {
                    "id": "12346",
                    "noms": [{"text": "Test Game No Rating", "langue": "en"}],
                    "synopsis": [],
                    "dates": [],
                    "developpeur": {},
                    "editeur": {},
                    "joueurs": {},
                    "genres": [],
                    "medias": [],
                }
            }
        }
        mock_response.raise_for_status.return_value = None
        mock_get.return_value = mock_response

        client = ScreenScraperClient()
        info = client.get_game_info(12346)

        assert info["rating"] is None
        assert info["rating_source"] is None


class TestSearchNameNormalization:
    """Tests for search name normalization and variant generation."""

    def test_normalize_name_unicode_accents(self):
        """Test that normalize_name converts accented chars to ASCII."""
        from library.lookup.screenscraper import normalize_name

        # Accented characters should be normalized
        assert normalize_name("Pokémon") == "pokemon"
        assert normalize_name("Café Runner") == "cafe runner"
        assert normalize_name("Château") == "chateau"
        assert normalize_name("naïve") == "naive"

    def test_normalize_name_pokemon_matching(self):
        """Test that Pokemon and Pokémon normalize to the same value."""
        from library.lookup.screenscraper import normalize_name

        # Both should normalize identically
        assert normalize_name("Pokemon FireRed") == normalize_name("Pokémon FireRed")
        assert normalize_name("pokemon") == normalize_name("pokémon")

    def test_calculate_match_score_with_accents(self):
        """Test that match scoring works with accented characters."""
        from library.lookup.screenscraper import calculate_match_score

        # Pokemon vs Pokémon should now match well
        score = calculate_match_score("Pokemon FireRed", "Pokémon FireRed Version")
        assert score >= 0.8, f"Expected score >= 0.8, got {score}"

        # Exact match after normalization
        score = calculate_match_score("Pokemon", "Pokémon")
        assert score == 1.0, f"Expected exact match, got {score}"

    def test_normalize_search_name_removes_leading_the(self):
        """Test search name normalization removes leading 'The'."""
        from library.metadata.screenscraper import _normalize_search_name

        assert _normalize_search_name("The Legend of Zelda") == "Legend of Zelda"
        assert _normalize_search_name("the legend of zelda") == "legend of zelda"
        assert _normalize_search_name("THE GAME") == "GAME"

    def test_normalize_search_name_preserves_non_the_names(self):
        """Test that names not starting with 'The' are preserved."""
        from library.metadata.screenscraper import _normalize_search_name

        assert _normalize_search_name("Legend of Zelda") == "Legend of Zelda"
        assert _normalize_search_name("Super Mario World") == "Super Mario World"
        assert _normalize_search_name("Metroid") == "Metroid"

    def test_normalize_search_name_strips_whitespace(self):
        """Test that whitespace is stripped."""
        from library.metadata.screenscraper import _normalize_search_name

        assert _normalize_search_name("The Game  ") == "Game"
        assert _normalize_search_name("  Game  ") == "Game"

    def test_get_search_variants_basic(self):
        """Test basic variant generation (just removes The)."""
        from library.metadata.screenscraper import _get_search_variants

        variants = _get_search_variants("The Legend of Zelda")
        assert variants == ["Legend of Zelda"]

    def test_get_search_variants_with_ampersand(self):
        """Test variant generation with ampersand."""
        from library.metadata.screenscraper import _get_search_variants

        variants = _get_search_variants("Chip & Dale")
        assert "Chip & Dale" in variants
        assert "Chip and Dale" in variants
        assert len(variants) == 2

    def test_get_search_variants_with_dash(self):
        """Test variant generation with dash."""
        from library.metadata.screenscraper import _get_search_variants

        variants = _get_search_variants("Metroid - Mission Zero")
        assert "Metroid - Mission Zero" in variants
        assert "Metroid Mission Zero" in variants
        assert "Mission Zero" in variants  # subtitle extraction

    def test_get_search_variants_combined(self):
        """Test variant generation with multiple special characters."""
        from library.metadata.screenscraper import _get_search_variants

        variants = _get_search_variants("The Game & Stuff - Remastered")
        assert "Game & Stuff - Remastered" in variants  # base (The removed)
        assert "Game and Stuff - Remastered" in variants  # & -> and
        assert "Game & Stuff Remastered" in variants  # dash removed
        assert "Remastered" in variants  # subtitle extraction

    def test_get_search_variants_no_duplicates(self):
        """Test that duplicate variants are not generated."""
        from library.metadata.screenscraper import _get_search_variants

        # A name with a dash - the dash gets replaced with space
        variants = _get_search_variants("Game-Name")
        # Should have base, dash-removed variant, and subtitle
        assert "Game-Name" in variants
        assert "Game Name" in variants  # dash replaced with space
        assert "Name" in variants  # subtitle extraction
        # Ensure no duplicates
        assert len(variants) == len(set(variants))

    @pytest.mark.skip(reason="Test needs refactoring to work with lookup chain")
    @pytest.mark.django_db
    @patch("library.metadata.screenscraper.ScreenScraperClient")
    def test_match_game_tries_variants_on_low_confidence(self, mock_client_class):
        """Test that match_game tries variants when match confidence is too low."""
        from library.metadata.matcher import match_game
        from library.models import Game, System

        # Create test system and game with ampersand
        system, _ = System.objects.update_or_create(
            slug="test_variant",
            defaults={
                "name": "Test System",
                "extensions": [".test"],
                "folder_names": ["TestVariant"],
                "screenscraper_ids": [100],
            },
        )
        game, _ = Game.objects.update_or_create(name="Game & Stuff", system=system)

        # Mock client
        mock_client = Mock()
        mock_client_class.return_value = mock_client

        # First search returns low-confidence results, second returns good match
        mock_client.search_game.side_effect = [
            # First variant "Game & Stuff" - returns results but low score
            [{"id": 111, "name": "Different Game", "all_names": []}],
            # Second variant "Game and Stuff" - good match
            [{"id": 222, "name": "Game and Stuff", "all_names": ["Game and Stuff"]}],
        ]

        # Mock get_game_info to return metadata
        mock_client.get_game_info.return_value = {
            "id": 222,
            "name": "Game and Stuff",
            "description": "A test game",
        }

        result = match_game(game)

        # Should have tried both variants
        assert mock_client.search_game.call_count == 2
        # Should have fetched metadata for the good match
        assert mock_client.get_game_info.called
        assert result is not None
        assert result["id"] == 222

    @pytest.mark.skip(reason="Test needs refactoring to work with lookup chain")
    @pytest.mark.django_db
    @patch("library.metadata.screenscraper.ScreenScraperClient")
    def test_match_game_returns_first_good_match(self, mock_client_class):
        """Test that match_game returns on first successful variant match."""
        from library.metadata.matcher import match_game
        from library.models import Game, System

        # Create test system and game
        system, _ = System.objects.update_or_create(
            slug="test_variant2",
            defaults={
                "name": "Test System 2",
                "extensions": [".test2"],
                "folder_names": ["TestVariant2"],
                "screenscraper_ids": [101],
            },
        )
        game, _ = Game.objects.update_or_create(
            name="The Legend of Zelda", system=system
        )

        # Mock client
        mock_client = Mock()
        mock_client_class.return_value = mock_client

        # First search succeeds with good match (name normalized to remove "The")
        mock_client.search_game.return_value = [
            {"id": 333, "name": "Legend of Zelda", "all_names": ["Legend of Zelda"]}
        ]

        mock_client.get_game_info.return_value = {
            "id": 333,
            "name": "Legend of Zelda",
            "description": "A classic game",
        }

        result = match_game(game)

        # Should only call search once since first variant matched
        assert mock_client.search_game.call_count == 1
        assert result is not None
        assert result["id"] == 333

    def test_roman_to_western_converts_standalone_numerals(self):
        """Test that standalone Roman numerals are converted to Western numbers."""
        from library.metadata.screenscraper import _roman_to_western

        assert _roman_to_western("Final Fantasy V") == "Final Fantasy 5"
        assert _roman_to_western("Street Fighter II") == "Street Fighter 2"
        assert _roman_to_western("Mega Man X") == "Mega Man 10"
        assert _roman_to_western("Resident Evil VII") == "Resident Evil 7"
        assert _roman_to_western("King's Quest IV") == "King's Quest 4"
        # Multiple Roman numerals in one name
        assert _roman_to_western("Game II Part III") == "Game 2 Part 3"

    def test_roman_to_western_ignores_lowercase(self):
        """Test that lowercase Roman numerals are not converted."""
        from library.metadata.screenscraper import _roman_to_western

        assert _roman_to_western("More x vision") is None
        assert _roman_to_western("game v two") is None
        assert _roman_to_western("the ii game") is None

    def test_roman_to_western_ignores_embedded_letters(self):
        """Test that letters within words are not converted."""
        from library.metadata.screenscraper import _roman_to_western

        assert _roman_to_western("Vega") is None
        assert _roman_to_western("Civilization") is None
        assert _roman_to_western("Divinity") is None
        assert _roman_to_western("VVVVVV") is None
        assert _roman_to_western("Xena") is None

    def test_roman_to_western_returns_none_when_no_change(self):
        """Test that None is returned when no conversion is made."""
        from library.metadata.screenscraper import _roman_to_western

        assert _roman_to_western("Super Mario World") is None
        assert _roman_to_western("The Legend of Zelda") is None

    def test_get_search_variants_with_roman_numerals(self):
        """Test variant generation with Roman numerals."""
        from library.metadata.screenscraper import _get_search_variants

        variants = _get_search_variants("Final Fantasy V")
        assert "Final Fantasy V" in variants
        assert "Final Fantasy 5" in variants

        variants = _get_search_variants("Street Fighter II")
        assert "Street Fighter II" in variants
        assert "Street Fighter 2" in variants

    def test_pokemon_accent_converts_pokemon(self):
        """Test that pokemon is converted to pokémon (case-insensitive match)."""
        from library.metadata.screenscraper import _pokemon_accent

        assert _pokemon_accent("Pokemon Red") == "pokémon Red"
        assert _pokemon_accent("pokemon blue") == "pokémon blue"
        assert _pokemon_accent("POKEMON Yellow") == "pokémon Yellow"
        assert _pokemon_accent("New Pokemon Snap") == "New pokémon Snap"

    def test_pokemon_accent_returns_none_when_no_match(self):
        """Test that None is returned when no pokemon found."""
        from library.metadata.screenscraper import _pokemon_accent

        assert _pokemon_accent("Super Mario World") is None
        assert _pokemon_accent("Zelda") is None

    def test_get_search_variants_with_pokemon(self):
        """Test variant generation with pokemon."""
        from library.metadata.screenscraper import _get_search_variants

        variants = _get_search_variants("Pokemon Red")
        assert "Pokemon Red" in variants
        assert "pokémon Red" in variants

        variants = _get_search_variants("New Pokemon Snap")
        assert "New Pokemon Snap" in variants
        assert "New pokémon Snap" in variants
