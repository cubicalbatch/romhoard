"""Tests for ScreenScraper fallback behavior and credential management."""

import os

import pytest
import requests
from unittest.mock import MagicMock, patch

from library.metadata.screenscraper import (
    ScreenScraperClient,
    screenscraper_available,
)
from library.models import Setting


class TestScreenScraperFallback:
    """Test fallback behavior when primary API call fails."""

    @pytest.fixture
    def client(self):
        """Create a client instance with mocked credentials."""
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

    def test_fallback_on_timeout(self, client):
        """Test that get_game_info falls back to search on timeout."""
        game_id = "2147"
        game_name = "Super Metroid"
        system_id = 4

        # Mock response for jeuRecherche
        mock_search_response = {
            "response": {
                "jeux": [
                    {
                        "id": "2147",
                        "noms": [{"region": "ss", "text": "Super Metroid"}],
                        "synopsis": [{"langue": "en", "text": "Samus returns..."}],
                        "medias": [],
                    }
                ]
            }
        }

        with patch("requests.get") as mock_get:

            def side_effect(url, **kwargs):
                if "jeuInfos" in url:
                    raise requests.exceptions.ReadTimeout("Mocked timeout")
                elif "jeuRecherche" in url:
                    mock_resp = MagicMock()
                    mock_resp.status_code = 200
                    mock_resp.json.return_value = mock_search_response
                    return mock_resp
                return MagicMock()

            mock_get.side_effect = side_effect

            # Execute
            result = client.get_game_info(
                game_id, game_name=game_name, system_id=system_id
            )

            # Verify
            assert result["id"] == "2147"
            assert result["name"] == "Super Metroid"
            assert result["description"] == "Samus returns..."

            # Verify calls
            assert mock_get.call_count == 2
            calls = mock_get.call_args_list
            assert "jeuInfos" in calls[0][0][0]
            assert "jeuRecherche" in calls[1][0][0]
            # Check params in search url
            assert (
                f"recherche={game_name.replace(' ', '+')}" in calls[1][0][0]
                or f"recherche={game_name.replace(' ', '%20')}" in calls[1][0][0]
            )

    def test_no_fallback_without_params(self, client):
        """Test that exception is raised if fallback params are missing."""
        game_id = "2147"

        with patch("requests.get") as mock_get:
            mock_get.side_effect = requests.exceptions.ReadTimeout("Mocked timeout")

            # Should raise ReadTimeout because we didn't provide name/system
            with pytest.raises(requests.exceptions.ReadTimeout):
                client.get_game_info(game_id)


class TestScreenScraperSuccessPath:
    """Test normal success path without fallback."""

    @pytest.fixture
    def client(self):
        """Create a client instance with mocked credentials."""
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

    def test_jeuinfos_success_no_fallback(self, client):
        """Test that successful jeuInfos request does not trigger fallback."""
        game_id = "2147"

        mock_jeuinfos_response = {
            "response": {
                "jeu": {
                    "id": "2147",
                    "noms": [{"region": "ss", "text": "Super Metroid"}],
                    "synopsis": [{"langue": "en", "text": "A great game."}],
                    "medias": [],
                }
            }
        }

        with patch("requests.get") as mock_get:
            mock_resp = MagicMock()
            mock_resp.status_code = 200
            mock_resp.json.return_value = mock_jeuinfos_response
            mock_get.return_value = mock_resp

            # Execute - provide fallback params but they shouldn't be used
            result = client.get_game_info(
                game_id, game_name="Super Metroid", system_id=4
            )

            # Verify
            assert result["id"] == "2147"
            assert result["name"] == "Super Metroid"
            assert result["description"] == "A great game."

            # Verify only one call was made (no fallback)
            assert mock_get.call_count == 1
            assert "jeuInfos" in mock_get.call_args[0][0]

    def test_jeuinfos_without_fallback_params(self, client):
        """Test jeuInfos works when no fallback params provided."""
        game_id = "2147"

        mock_jeuinfos_response = {
            "response": {
                "jeu": {
                    "id": "2147",
                    "noms": [{"region": "ss", "text": "Zelda"}],
                    "synopsis": [{"langue": "en", "text": "Epic adventure."}],
                    "medias": [],
                }
            }
        }

        with patch("requests.get") as mock_get:
            mock_resp = MagicMock()
            mock_resp.status_code = 200
            mock_resp.json.return_value = mock_jeuinfos_response
            mock_get.return_value = mock_resp

            # Execute without fallback params
            result = client.get_game_info(game_id)

            # Verify
            assert result["id"] == "2147"
            assert result["name"] == "Zelda"
            assert mock_get.call_count == 1

    def test_jeuinfos_with_media_types(self, client):
        """Test that media_types parameter is respected."""
        game_id = "2147"

        mock_response = {
            "response": {
                "jeu": {
                    "id": "2147",
                    "noms": [{"region": "ss", "text": "Test Game"}],
                    "synopsis": [],
                    "medias": [
                        {"type": "box-2D", "url": "http://example.com/box.png"},
                        {"type": "ss", "url": "http://example.com/screenshot.png"},
                    ],
                }
            }
        }

        with patch("requests.get") as mock_get:
            mock_resp = MagicMock()
            mock_resp.status_code = 200
            mock_resp.json.return_value = mock_response
            mock_get.return_value = mock_resp

            # Execute with specific media types
            result = client.get_game_info(game_id, media_types={"box-2D"})

            # Should succeed
            assert result["id"] == "2147"
            assert mock_get.call_count == 1


class TestScreenScraperConnectionErrors:
    """Test handling of various connection errors."""

    @pytest.fixture
    def client(self):
        """Create a client instance with mocked credentials."""
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

    def test_fallback_on_connection_error(self, client):
        """Test that connection errors also trigger fallback."""
        game_id = "2147"
        game_name = "Test Game"
        system_id = 4

        mock_search_response = {
            "response": {
                "jeux": [
                    {
                        "id": "2147",
                        "noms": [{"region": "ss", "text": "Test Game"}],
                        "synopsis": [{"langue": "en", "text": "Description"}],
                        "medias": [],
                    }
                ]
            }
        }

        with patch("requests.get") as mock_get:

            def side_effect(url, **kwargs):
                if "jeuInfos" in url:
                    raise requests.exceptions.ConnectionError("Connection refused")
                elif "jeuRecherche" in url:
                    mock_resp = MagicMock()
                    mock_resp.status_code = 200
                    mock_resp.json.return_value = mock_search_response
                    return mock_resp
                return MagicMock()

            mock_get.side_effect = side_effect

            result = client.get_game_info(
                game_id, game_name=game_name, system_id=system_id
            )

            assert result["id"] == "2147"
            assert mock_get.call_count == 2

    def test_connection_error_without_fallback_params(self, client):
        """Test connection error raises when no fallback params."""
        game_id = "2147"

        with patch("requests.get") as mock_get:
            mock_get.side_effect = requests.exceptions.ConnectionError(
                "Connection refused"
            )

            with pytest.raises(requests.exceptions.ConnectionError):
                client.get_game_info(game_id)


class TestScreenScraperEmptyResponses:
    """Test handling of empty or malformed responses."""

    @pytest.fixture
    def client(self):
        """Create a client instance with mocked credentials."""
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

    def test_empty_search_results_in_fallback(self, client):
        """Test that empty fallback search re-raises original exception."""
        game_id = "2147"
        game_name = "Nonexistent Game"
        system_id = 4

        # jeuInfos times out, fallback search returns empty (no matching game ID)
        mock_search_response = {"response": {"jeux": []}}

        with patch("requests.get") as mock_get:

            def side_effect(url, **kwargs):
                if "jeuInfos" in url:
                    raise requests.exceptions.ReadTimeout("Mocked timeout")
                elif "jeuRecherche" in url:
                    mock_resp = MagicMock()
                    mock_resp.status_code = 200
                    mock_resp.json.return_value = mock_search_response
                    return mock_resp
                return MagicMock()

            mock_get.side_effect = side_effect

            # When fallback search doesn't find the game ID, original exception is re-raised
            with pytest.raises(requests.exceptions.ReadTimeout):
                client.get_game_info(game_id, game_name=game_name, system_id=system_id)

    def test_missing_response_key(self, client):
        """Test handling of response missing expected keys returns empty dict."""
        game_id = "2147"

        mock_response = {"unexpected": "structure"}

        with patch("requests.get") as mock_get:
            mock_resp = MagicMock()
            mock_resp.status_code = 200
            mock_resp.json.return_value = mock_response
            mock_get.return_value = mock_resp

            result = client.get_game_info(game_id)

            # When response structure is unexpected, returns empty dict
            assert result == {}


@pytest.mark.django_db
class TestScreenScraperCredentialPriority:
    """Test credential loading priority: DB > env vars > none."""

    def test_db_credentials_take_priority_over_env(self):
        """Test that database credentials are used over environment variables."""
        # Set up DB credentials
        Setting.set("screenscraper_username", "db_user")
        Setting.set("screenscraper_password", "db_pass")

        env_vars = {
            "SCREENSCRAPER_USER": "env_user",
            "SCREENSCRAPER_PASSWORD": "env_pass",
        }

        with (
            patch.dict(os.environ, env_vars),
            patch("library.metadata.screenscraper.get_pause_until", return_value=None),
        ):
            client = ScreenScraperClient()

            # DB credentials should be used, not env vars
            assert client.username == "db_user"
            assert client.password == "db_pass"

        # Clean up
        Setting.objects.filter(
            key__in=["screenscraper_username", "screenscraper_password"]
        ).delete()

    def test_env_fallback_when_db_empty(self):
        """Test that environment variables are used when DB has no credentials."""
        # Ensure no DB credentials
        Setting.objects.filter(
            key__in=["screenscraper_username", "screenscraper_password"]
        ).delete()

        env_vars = {
            "SCREENSCRAPER_USER": "env_user",
            "SCREENSCRAPER_PASSWORD": "env_pass",
        }

        with (
            patch.dict(os.environ, env_vars),
            patch("library.metadata.screenscraper.get_pause_until", return_value=None),
        ):
            client = ScreenScraperClient()

            # Should fall back to env vars
            assert client.username == "env_user"
            assert client.password == "env_pass"

    def test_no_credentials_when_both_empty(self):
        """Test that no credentials are set when both sources are empty."""
        # Ensure no DB credentials
        Setting.objects.filter(
            key__in=["screenscraper_username", "screenscraper_password"]
        ).delete()

        # Clear env vars
        env_vars = {}

        with (
            patch.dict(os.environ, env_vars, clear=True),
            patch("library.metadata.screenscraper.get_pause_until", return_value=None),
        ):
            client = ScreenScraperClient()

            # Both should be empty/None
            assert not client.username
            assert not client.password


@pytest.mark.django_db
class TestScreenScraperAvailable:
    """Test the screenscraper_available() helper function."""

    def test_available_with_db_credentials(self):
        """Test that screenscraper_available returns True with DB credentials."""
        Setting.set("screenscraper_username", "test_user")
        Setting.set("screenscraper_password", "test_pass")

        # Clear env vars to ensure we're testing DB
        with patch.dict(os.environ, {}, clear=True):
            assert screenscraper_available() is True

        # Clean up
        Setting.objects.filter(
            key__in=["screenscraper_username", "screenscraper_password"]
        ).delete()

    def test_available_with_env_credentials(self):
        """Test that screenscraper_available returns True with env credentials."""
        # Ensure no DB credentials
        Setting.objects.filter(
            key__in=["screenscraper_username", "screenscraper_password"]
        ).delete()

        env_vars = {
            "SCREENSCRAPER_USER": "env_user",
            "SCREENSCRAPER_PASSWORD": "env_pass",
        }

        with patch.dict(os.environ, env_vars):
            assert screenscraper_available() is True

    def test_not_available_with_no_credentials(self):
        """Test that screenscraper_available returns False with no credentials."""
        # Ensure no DB credentials
        Setting.objects.filter(
            key__in=["screenscraper_username", "screenscraper_password"]
        ).delete()

        # Clear env vars
        with patch.dict(os.environ, {}, clear=True):
            assert screenscraper_available() is False

    def test_not_available_with_partial_credentials(self):
        """Test that screenscraper_available returns False with only username."""
        # Ensure no DB credentials
        Setting.objects.filter(
            key__in=["screenscraper_username", "screenscraper_password"]
        ).delete()

        # Only set username, no password
        env_vars = {
            "SCREENSCRAPER_USER": "env_user",
        }

        with patch.dict(os.environ, env_vars, clear=True):
            assert screenscraper_available() is False

    def test_db_overrides_partial_env(self):
        """Test that complete DB credentials work even with partial env vars."""
        Setting.set("screenscraper_username", "db_user")
        Setting.set("screenscraper_password", "db_pass")

        # Partial env vars (only username)
        env_vars = {
            "SCREENSCRAPER_USER": "env_user",
        }

        with patch.dict(os.environ, env_vars, clear=True):
            assert screenscraper_available() is True

        # Clean up
        Setting.objects.filter(
            key__in=["screenscraper_username", "screenscraper_password"]
        ).delete()
