"""Tests for URL fetch utility."""

import pytest
from unittest.mock import MagicMock, patch

import httpx

from romhoard.url_fetch import (
    URLFetchError,
    fetch_json_from_url,
    validate_url,
    MAX_SIZE_BYTES,
)


class TestValidateUrl:
    """Tests for URL validation."""

    def test_accepts_http_url(self):
        """Test that http URLs are accepted."""
        url = validate_url("http://example.com/data.json")
        assert url == "http://example.com/data.json"

    def test_accepts_https_url(self):
        """Test that https URLs are accepted."""
        url = validate_url("https://example.com/data.json")
        assert url == "https://example.com/data.json"

    def test_rejects_empty_url(self):
        """Test that empty URLs are rejected."""
        with pytest.raises(URLFetchError, match="URL is required"):
            validate_url("")

    def test_rejects_whitespace_url(self):
        """Test that whitespace-only URLs are rejected."""
        with pytest.raises(URLFetchError, match="URL is required"):
            validate_url("   ")

    def test_rejects_file_scheme(self):
        """Test that file:// URLs are rejected."""
        with pytest.raises(URLFetchError, match="http or https"):
            validate_url("file:///etc/passwd")

    def test_rejects_ftp_scheme(self):
        """Test that ftp:// URLs are rejected."""
        with pytest.raises(URLFetchError, match="http or https"):
            validate_url("ftp://example.com/data.json")

    def test_rejects_missing_hostname(self):
        """Test that URLs without hostnames are rejected."""
        with pytest.raises(URLFetchError, match="hostname"):
            validate_url("http:///path/to/file")

    def test_strips_whitespace(self):
        """Test that leading/trailing whitespace is stripped."""
        url = validate_url("  https://example.com/data.json  ")
        assert url == "https://example.com/data.json"


class TestFetchJsonFromUrl:
    """Tests for fetching JSON from URLs."""

    def test_fetches_valid_json(self):
        """Test successful JSON fetch."""
        mock_response = MagicMock()
        mock_response.headers = {"content-type": "application/json"}
        mock_response.content = b'{"key": "value"}'
        mock_response.raise_for_status = MagicMock()

        with patch("romhoard.url_fetch.httpx.Client") as mock_client:
            mock_client.return_value.__enter__.return_value.get.return_value = (
                mock_response
            )
            result = fetch_json_from_url("https://example.com/data.json")

        assert result == {"key": "value"}

    def test_rejects_html_response(self):
        """Test that HTML responses are rejected."""
        mock_response = MagicMock()
        mock_response.headers = {"content-type": "text/html; charset=utf-8"}
        mock_response.content = b"<html></html>"
        mock_response.raise_for_status = MagicMock()

        with patch("romhoard.url_fetch.httpx.Client") as mock_client:
            mock_client.return_value.__enter__.return_value.get.return_value = (
                mock_response
            )
            with pytest.raises(URLFetchError, match="HTML instead of JSON"):
                fetch_json_from_url("https://example.com/page.html")

    def test_rejects_oversized_content_by_header(self):
        """Test that oversized content is rejected via content-length header."""
        mock_response = MagicMock()
        mock_response.headers = {"content-length": str(MAX_SIZE_BYTES + 1)}
        mock_response.raise_for_status = MagicMock()

        with patch("romhoard.url_fetch.httpx.Client") as mock_client:
            mock_client.return_value.__enter__.return_value.get.return_value = (
                mock_response
            )
            with pytest.raises(URLFetchError, match="too large"):
                fetch_json_from_url("https://example.com/large.json")

    def test_rejects_oversized_content_by_actual_size(self):
        """Test that oversized content is rejected after download."""
        mock_response = MagicMock()
        mock_response.headers = {}  # No content-length header
        mock_response.content = b"x" * (MAX_SIZE_BYTES + 1)
        mock_response.raise_for_status = MagicMock()

        with patch("romhoard.url_fetch.httpx.Client") as mock_client:
            mock_client.return_value.__enter__.return_value.get.return_value = (
                mock_response
            )
            with pytest.raises(URLFetchError, match="too large"):
                fetch_json_from_url("https://example.com/large.json")

    def test_handles_invalid_json(self):
        """Test that invalid JSON is rejected."""
        mock_response = MagicMock()
        mock_response.headers = {"content-type": "application/json"}
        mock_response.content = b"not valid json"
        mock_response.raise_for_status = MagicMock()

        with patch("romhoard.url_fetch.httpx.Client") as mock_client:
            mock_client.return_value.__enter__.return_value.get.return_value = (
                mock_response
            )
            with pytest.raises(URLFetchError, match="Invalid JSON"):
                fetch_json_from_url("https://example.com/invalid.json")

    def test_handles_timeout(self):
        """Test that timeouts are handled."""
        with patch("romhoard.url_fetch.httpx.Client") as mock_client:
            mock_client.return_value.__enter__.return_value.get.side_effect = (
                httpx.TimeoutException("Connection timed out")
            )
            with pytest.raises(URLFetchError, match="timed out"):
                fetch_json_from_url("https://example.com/slow.json")

    def test_handles_too_many_redirects(self):
        """Test that too many redirects are handled."""
        with patch("romhoard.url_fetch.httpx.Client") as mock_client:
            mock_client.return_value.__enter__.return_value.get.side_effect = (
                httpx.TooManyRedirects("Exceeded max redirects")
            )
            with pytest.raises(URLFetchError, match="redirects"):
                fetch_json_from_url("https://example.com/loop.json")

    def test_handles_http_errors(self):
        """Test that HTTP errors are handled."""
        mock_response = MagicMock()
        mock_response.status_code = 404

        with patch("romhoard.url_fetch.httpx.Client") as mock_client:
            mock_client.return_value.__enter__.return_value.get.side_effect = (
                httpx.HTTPStatusError("Not found", request=None, response=mock_response)
            )
            with pytest.raises(URLFetchError, match="HTTP error 404"):
                fetch_json_from_url("https://example.com/missing.json")

    def test_handles_connection_errors(self):
        """Test that connection errors are handled."""
        with patch("romhoard.url_fetch.httpx.Client") as mock_client:
            mock_client.return_value.__enter__.return_value.get.side_effect = (
                httpx.ConnectError("Connection refused")
            )
            with pytest.raises(URLFetchError, match="Request failed"):
                fetch_json_from_url("https://example.com/down.json")


class TestCollectionUrlImport:
    """Tests for collection URL import views."""

    @pytest.mark.django_db
    def test_url_import_fetches_and_shows_preview(self, client):
        """Test that URL import fetches JSON and redirects to preview."""
        valid_collection = {
            "romhoard_collection": {"version": "1.0"},
            "collection": {"slug": "test-collection", "name": "Test Collection"},
            "entries": [],
        }

        with patch("romhoard.url_fetch.fetch_json_from_url") as mock_fetch:
            mock_fetch.return_value = valid_collection

            response = client.post(
                "/collections/import/",
                {"url": "https://example.com/collection.json"},
            )

        assert response.status_code == 302
        assert "/collections/import/preview/" in response.url

    @pytest.mark.django_db
    def test_url_import_shows_error_for_fetch_failure(self, client):
        """Test that URL import shows error when fetch fails."""
        with patch("romhoard.url_fetch.fetch_json_from_url") as mock_fetch:
            mock_fetch.side_effect = URLFetchError("Connection failed")

            response = client.post(
                "/collections/import/",
                {"url": "https://example.com/bad.json"},
            )

        assert response.status_code == 200
        assert b"Failed to fetch URL" in response.content

    @pytest.mark.django_db
    def test_url_import_shows_error_for_invalid_format(self, client):
        """Test that URL import shows error for invalid collection format."""
        invalid_data = {"not": "a collection"}

        with patch("romhoard.url_fetch.fetch_json_from_url") as mock_fetch:
            mock_fetch.return_value = invalid_data

            response = client.post(
                "/collections/import/",
                {"url": "https://example.com/invalid.json"},
            )

        assert response.status_code == 200
        assert b"Invalid format" in response.content

    @pytest.mark.django_db
    def test_preview_page_shows_collection_details(self, client):
        """Test that preview page displays collection details."""
        valid_collection = {
            "romhoard_collection": {"version": "1.0"},
            "collection": {
                "slug": "preview-test",
                "name": "Preview Test Collection",
                "description": "A test description",
            },
            "entries": [
                {"game_name": "Game 1", "system_slug": "gba"},
                {"game_name": "Game 2", "system_slug": "nes"},
            ],
        }

        with patch("romhoard.url_fetch.fetch_json_from_url") as mock_fetch:
            mock_fetch.return_value = valid_collection

            # First, submit the URL to get the preview token
            response = client.post(
                "/collections/import/",
                {"url": "https://example.com/collection.json"},
                follow=True,
            )

        assert response.status_code == 200
        assert b"Preview Test Collection" in response.content
        assert b"2 game" in response.content

    @pytest.mark.django_db
    def test_preview_confirm_imports_collection(self, client):
        """Test that confirming preview imports the collection."""
        from romcollections.models import Collection

        valid_collection = {
            "romhoard_collection": {"version": "1.0"},
            "collection": {
                "slug": "confirm-test",
                "name": "Confirm Test Collection",
            },
            "entries": [],
        }

        with patch("romhoard.url_fetch.fetch_json_from_url") as mock_fetch:
            mock_fetch.return_value = valid_collection

            # Submit URL
            response = client.post(
                "/collections/import/",
                {"url": "https://example.com/collection.json"},
            )

            # Get the preview token from redirect URL
            preview_url = response.url

            # Confirm import
            response = client.post(preview_url, {"action": "confirm"}, follow=True)

        assert response.status_code == 200
        assert Collection.objects.filter(slug="confirm-test").exists()

    @pytest.mark.django_db
    def test_preview_cancel_returns_to_import(self, client):
        """Test that canceling preview returns to import page."""
        from romcollections.models import Collection

        valid_collection = {
            "romhoard_collection": {"version": "1.0"},
            "collection": {
                "slug": "cancel-test",
                "name": "Cancel Test Collection",
            },
            "entries": [],
        }

        with patch("romhoard.url_fetch.fetch_json_from_url") as mock_fetch:
            mock_fetch.return_value = valid_collection

            # Submit URL
            response = client.post(
                "/collections/import/",
                {"url": "https://example.com/collection.json"},
            )

            # Cancel
            response = client.post(response.url, {"action": "cancel"}, follow=True)

        assert response.status_code == 200
        assert b"Import Collection" in response.content
        assert not Collection.objects.filter(slug="cancel-test").exists()


class TestDeviceUrlImport:
    """Tests for device URL import views."""

    @pytest.mark.django_db
    def test_url_import_fetches_and_shows_preview(self, client):
        """Test that URL import fetches JSON and redirects to preview."""
        valid_device = {
            "romhoard_device": {"version": "3.0"},
            "device": {
                "slug": "test-device",
                "name": "Test Device",
                "root_path": "Roms/",
                "system_paths": {},
            },
        }

        with patch("romhoard.url_fetch.fetch_json_from_url") as mock_fetch:
            mock_fetch.return_value = valid_device

            response = client.post(
                "/devices/import/",
                {"url": "https://example.com/device.json"},
            )

        assert response.status_code == 302
        assert "/devices/import/preview/" in response.url

    @pytest.mark.django_db
    def test_preview_confirm_imports_device(self, client):
        """Test that confirming preview imports the device."""
        from devices.models import Device

        valid_device = {
            "romhoard_device": {"version": "3.0"},
            "device": {
                "slug": "confirm-device",
                "name": "Confirm Device",
                "root_path": "Roms/",
                "system_paths": {"gba": "GBA"},
            },
        }

        with patch("romhoard.url_fetch.fetch_json_from_url") as mock_fetch:
            mock_fetch.return_value = valid_device

            # Submit URL
            response = client.post(
                "/devices/import/",
                {"url": "https://example.com/device.json"},
            )

            # Confirm import
            response = client.post(response.url, {"action": "confirm"}, follow=True)

        assert response.status_code == 200
        assert Device.objects.filter(slug="confirm-device").exists()
        device = Device.objects.get(slug="confirm-device")
        assert device.name == "Confirm Device"
        assert device.system_paths == {"gba": "GBA"}
