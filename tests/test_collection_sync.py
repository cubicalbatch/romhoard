"""Tests for collection source URL tracking and sync functionality."""

import pytest
from unittest.mock import patch

from romhoard.url_fetch import URLFetchError


class TestSourceUrlTracking:
    """Tests for saving source_url on URL import."""

    @pytest.mark.django_db
    def test_source_url_saved_on_url_import(self, client):
        """Test that source_url is saved when importing via URL."""
        from romcollections.models import Collection

        valid_collection = {
            "romhoard_collection": {"version": "1.0"},
            "collection": {
                "slug": "source-test",
                "name": "Source Test Collection",
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

            # Confirm import
            preview_url = response.url
            client.post(preview_url, {"action": "confirm"}, follow=True)

        collection = Collection.objects.get(slug="source-test")
        assert collection.source_url == "https://example.com/collection.json"
        assert collection.last_synced_at is not None

    @pytest.mark.django_db
    def test_source_url_not_set_on_file_upload(self, client):
        """Test that source_url is NOT set when importing via file upload."""
        import json
        from io import BytesIO

        from romcollections.models import Collection

        valid_collection = {
            "romhoard_collection": {"version": "1.0"},
            "collection": {
                "slug": "file-upload-test",
                "name": "File Upload Test",
            },
            "entries": [],
        }

        # Create a mock file upload
        json_content = json.dumps(valid_collection).encode("utf-8")
        file_obj = BytesIO(json_content)
        file_obj.name = "collection.json"

        response = client.post(
            "/collections/import/",
            {"file": file_obj},
            follow=True,
        )

        assert response.status_code == 200
        collection = Collection.objects.get(slug="file-upload-test")
        assert collection.source_url is None
        assert collection.last_synced_at is None


class TestSyncView:
    """Tests for sync_collection_from_source view."""

    @pytest.mark.django_db
    def test_sync_requires_post(self, client):
        """Test that sync view requires POST method."""
        from romcollections.models import Collection

        Collection.objects.create(
            slug="sync-get-test",
            name="Sync GET Test",
            creator="test",
            source_url="https://example.com/collection.json",
        )

        response = client.get("/collections/test/sync-get-test/sync/")
        assert response.status_code == 405  # Method Not Allowed

    @pytest.mark.django_db
    def test_sync_updates_collection_entries(self, client):
        """Test that sync updates collection entries from source."""
        from romcollections.models import Collection, CollectionEntry

        collection = Collection.objects.create(
            slug="sync-update-test",
            name="Sync Update Test",
            creator="test",
            source_url="https://example.com/collection.json",
        )
        CollectionEntry.objects.create(
            collection=collection,
            game_name="Old Game",
            system_slug="gba",
            position=0,
        )

        updated_collection = {
            "romhoard_collection": {"version": "1.0"},
            "collection": {
                "slug": "sync-update-test",
                "name": "Sync Update Test Updated",
                "creator": "test",  # Must match creator for overwrite
            },
            "entries": [
                {"game_name": "New Game 1", "system_slug": "nes"},
                {"game_name": "New Game 2", "system_slug": "snes"},
            ],
        }

        # Patch in the url_fetch module
        with patch(
            "romhoard.url_fetch.fetch_json_from_url"
        ) as mock_fetch:
            mock_fetch.return_value = updated_collection

            response = client.post(
                "/collections/test/sync-update-test/sync/",
                follow=True,
            )

        assert response.status_code == 200
        collection.refresh_from_db()

        # Old entry should be deleted, new ones created
        entries = list(collection.entries.all())
        assert len(entries) == 2
        entry_names = {e.game_name for e in entries}
        assert entry_names == {"New Game 1", "New Game 2"}
        assert "Old Game" not in entry_names

    @pytest.mark.django_db
    def test_sync_fails_gracefully_if_url_unreachable(self, client):
        """Test that sync fails gracefully when URL is unreachable."""
        from django.contrib.messages import get_messages

        from romcollections.models import Collection

        Collection.objects.create(
            slug="sync-fail-test",
            name="Sync Fail Test",
            creator="test",
            source_url="https://example.com/unreachable.json",
        )

        with patch(
            "romhoard.url_fetch.fetch_json_from_url"
        ) as mock_fetch:
            mock_fetch.side_effect = URLFetchError("Connection failed")

            response = client.post(
                "/collections/test/sync-fail-test/sync/",
                follow=True,
            )

        assert response.status_code == 200
        # Check Django messages
        messages = list(get_messages(response.wsgi_request))
        message_texts = [str(m) for m in messages]
        assert any("Failed to fetch source" in msg for msg in message_texts)

    @pytest.mark.django_db
    def test_sync_fails_if_no_source_url(self, client):
        """Test that sync fails if collection has no source URL."""
        from django.contrib.messages import get_messages

        from romcollections.models import Collection

        Collection.objects.create(
            slug="no-source-test",
            name="No Source Test",
            creator="test",
            source_url=None,
        )

        response = client.post(
            "/collections/test/no-source-test/sync/",
            follow=True,
        )

        assert response.status_code == 200
        # Check Django messages
        messages = list(get_messages(response.wsgi_request))
        message_texts = [str(m) for m in messages]
        assert any("no source URL" in msg for msg in message_texts)

    @pytest.mark.django_db
    def test_last_synced_at_updated_after_sync(self, client):
        """Test that last_synced_at is updated after successful sync."""
        import time

        from django.utils import timezone

        from romcollections.models import Collection

        old_time = timezone.now()
        time.sleep(0.01)  # Ensure some time passes

        collection = Collection.objects.create(
            slug="sync-time-test",
            name="Sync Time Test",
            creator="test",
            source_url="https://example.com/collection.json",
            last_synced_at=old_time,
        )

        updated_collection = {
            "romhoard_collection": {"version": "1.0"},
            "collection": {
                "slug": "sync-time-test",
                "name": "Sync Time Test",
                "creator": "test",
            },
            "entries": [],
        }

        with patch(
            "romhoard.url_fetch.fetch_json_from_url"
        ) as mock_fetch:
            mock_fetch.return_value = updated_collection

            client.post("/collections/test/sync-time-test/sync/", follow=True)

        collection.refresh_from_db()
        assert collection.last_synced_at is not None
        assert collection.last_synced_at > old_time


class TestSerializerSourceUrl:
    """Tests for serializer source_url parameter."""

    @pytest.mark.django_db
    def test_import_collection_with_source_url(self):
        """Test that import_collection saves source_url."""
        from romcollections.serializers import import_collection

        data = {
            "romhoard_collection": {"version": "1.0"},
            "collection": {
                "slug": "serializer-test",
                "name": "Serializer Test",
            },
            "entries": [],
        }

        result = import_collection(
            data,
            source_url="https://example.com/test.json",
        )

        collection = result["collection"]
        assert collection.source_url == "https://example.com/test.json"
        assert collection.last_synced_at is not None

    @pytest.mark.django_db
    def test_import_collection_without_source_url(self):
        """Test that import_collection without source_url leaves it null."""
        from romcollections.serializers import import_collection

        data = {
            "romhoard_collection": {"version": "1.0"},
            "collection": {
                "slug": "no-url-test",
                "name": "No URL Test",
            },
            "entries": [],
        }

        result = import_collection(data)

        collection = result["collection"]
        assert collection.source_url is None
        assert collection.last_synced_at is None
