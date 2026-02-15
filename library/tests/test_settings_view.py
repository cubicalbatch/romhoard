"""Tests for the metadata settings view."""

import os
from unittest.mock import patch

import pytest
from django.urls import reverse
from django.utils import timezone

from library.models import Game, GameImage, Setting, System


@pytest.mark.django_db
class TestMetadataPageGet:
    """Test GET requests to the metadata settings page."""

    def test_settings_page_renders(self, client):
        """Test that settings page renders successfully."""
        response = client.get(reverse("library:metadata"))
        assert response.status_code == 200

    @patch.dict(
        os.environ,
        {"SCREENSCRAPER_USER": "testuser", "SCREENSCRAPER_PASSWORD": "testpass"},
    )
    def test_settings_page_shows_configured_credentials(self, client):
        """Test that page shows credentials are configured when env vars are set."""
        response = client.get(reverse("library:metadata"))
        assert response.status_code == 200
        assert b"testuser" in response.content
        assert b"Configured" in response.content

    @patch.dict(os.environ, {}, clear=True)
    def test_settings_page_shows_not_configured_warning(self, client):
        """Test that page shows warning when credentials are not configured."""
        response = client.get(reverse("library:metadata"))
        assert response.status_code == 200
        assert b"Not configured" in response.content
        assert b"SCREENSCRAPER_USER" in response.content

    def test_settings_page_shows_existing_image_path(self, client):
        """Test that existing image path is shown in form."""
        Setting.objects.create(key="metadata_image_path", value="/custom/path")
        response = client.get(reverse("library:metadata"))
        assert response.status_code == 200
        assert b"/custom/path" in response.content

    def test_settings_page_empty_when_no_settings(self, client):
        """Test that page renders without errors when no settings exist."""
        response = client.get(reverse("library:metadata"))
        assert response.status_code == 200


@pytest.mark.django_db
class TestImagePathChanges:
    """Test behavior when metadata image storage path changes."""

    def test_changing_image_path_shows_modal_when_images_exist(self, client):
        """Test that changing path shows confirmation modal when downloaded images exist."""
        Setting.objects.create(key="metadata_image_path", value="/old/path")

        system = System.objects.create(
            name="Test", slug="test", extensions=[], folder_names=[]
        )
        game = Game.objects.create(name="Test Game", system=system)
        GameImage.objects.create(
            game=game,
            file_path="/old/path/img.png",
            file_name="img.png",
            source="downloaded",
        )

        # POST without image_action - should show modal
        response = client.post(
            reverse("library:metadata"),
            {
                "save_image_settings": "1",
                "image_path": "/new/path",
            },
        )

        # Should render page with modal, not redirect
        assert response.status_code == 200
        assert b"Image Storage Path Changed" in response.content
        assert b"1 downloaded images" in response.content

    def test_changing_image_path_creates_migration_job(self, client):
        """Test that confirming image path change creates a migration job."""
        from library.models import ImageMigrationJob

        Setting.objects.create(key="metadata_image_path", value="/old/path")

        system = System.objects.create(
            name="Test", slug="test", extensions=[], folder_names=[]
        )
        game = Game.objects.create(name="Test Game", system=system)
        GameImage.objects.create(
            game=game,
            file_path="/old/path/img.png",
            file_name="img.png",
            source="downloaded",
        )

        # POST with image_action - should create migration job
        response = client.post(
            reverse("library:metadata"),
            {
                "save_image_settings": "1",
                "image_path": "/new/path",
                "image_action": "move",
            },
        )

        assert response.status_code == 302  # Redirects after creating job
        assert Setting.objects.get(key="metadata_image_path").value == "/new/path"

        # Migration job should be created
        job = ImageMigrationJob.objects.first()
        assert job is not None
        assert job.action == "move"
        assert job.old_path == "/old/path"
        assert job.new_path == "/new/path"
        assert job.total_images == 1

    def test_cancel_action_does_not_change_path(self, client):
        """Test that cancel action doesn't change the path setting."""
        Setting.objects.create(key="metadata_image_path", value="/old/path")

        system = System.objects.create(
            name="Test", slug="test", extensions=[], folder_names=[]
        )
        game = Game.objects.create(name="Test Game", system=system)
        GameImage.objects.create(
            game=game,
            file_path="/old/path/img.png",
            file_name="img.png",
            source="downloaded",
        )

        response = client.post(
            reverse("library:metadata"),
            {
                "save_image_settings": "1",
                "image_path": "/new/path",
                "image_action": "cancel",
            },
        )

        assert response.status_code == 302
        # Path should NOT have changed
        assert Setting.objects.get(key="metadata_image_path").value == "/old/path"

    def test_no_images_skips_modal(self, client):
        """Test that changing path without downloaded images skips modal."""
        Setting.objects.create(key="metadata_image_path", value="/old/path")

        response = client.post(
            reverse("library:metadata"),
            {
                "save_image_settings": "1",
                "image_path": "/new/path",
            },
        )

        # Should redirect directly (no modal)
        assert response.status_code == 302
        assert Setting.objects.get(key="metadata_image_path").value == "/new/path"

    def test_unchanged_image_path_preserves_metadata(self, client):
        """Test that saving settings without changing the path preserves metadata."""
        Setting.objects.create(key="metadata_image_path", value="/path")

        game = Game.objects.create(
            name="Test Game",
            system=System.objects.create(
                name="Sys", slug="sys", extensions=[], folder_names=[]
            ),
            metadata_updated_at=timezone.now(),
        )

        GameImage.objects.create(
            game=game,
            file_path="/path/img.png",
            file_name="img.png",
            source="downloaded",
        )

        client.post(
            reverse("library:metadata"),
            {
                "save_image_settings": "1",
                "image_path": "/path",  # SAME PATH
            },
        )

        game.refresh_from_db()
        assert game.metadata_updated_at is not None
        assert GameImage.objects.count() == 1

    def test_empty_image_path_can_be_saved(self, client):
        """Test that empty image path can be saved."""
        Setting.objects.create(key="metadata_image_path", value="/some/path")

        client.post(
            reverse("library:metadata"),
            {
                "save_image_settings": "1",
                "image_path": "",
            },
        )

        assert Setting.objects.get(key="metadata_image_path").value == ""

    def test_whitespace_stripped_from_image_path(self, client):
        """Test that whitespace is stripped from image path."""
        client.post(
            reverse("library:metadata"),
            {
                "save_image_settings": "1",
                "image_path": "  /my/path  ",
            },
        )

        assert Setting.objects.get(key="metadata_image_path").value == "/my/path"


@pytest.mark.django_db
class TestLibraryRootSettings:
    """Test saving library root settings."""

    def test_save_library_root(self, client):
        """Test saving library root path."""
        response = client.post(
            reverse("library:metadata"),
            {
                "save_library_settings": "1",
                "library_root": "/my/roms",
            },
        )
        assert response.status_code == 302
        assert Setting.objects.get(key="library_root").value == "/my/roms"

    def test_update_library_root(self, client):
        """Test updating existing library root."""
        Setting.objects.create(key="library_root", value="/old/path")

        client.post(
            reverse("library:metadata"),
            {
                "save_library_settings": "1",
                "library_root": "/new/path",
            },
        )

        assert Setting.objects.get(key="library_root").value == "/new/path"

    def test_clear_library_root(self, client):
        """Test clearing library root by submitting empty value."""
        Setting.objects.create(key="library_root", value="/some/path")

        client.post(
            reverse("library:metadata"),
            {
                "save_library_settings": "1",
                "library_root": "",
            },
        )

        assert Setting.objects.get(key="library_root").value == ""

    def test_whitespace_stripped_from_library_root(self, client):
        """Test that whitespace is stripped from library root."""
        client.post(
            reverse("library:metadata"),
            {
                "save_library_settings": "1",
                "library_root": "  /my/path  ",
            },
        )

        assert Setting.objects.get(key="library_root").value == "/my/path"


@pytest.mark.django_db
class TestImageMigrationTask:
    """Test the image migration background task."""

    def test_orphan_action_clears_db_records(self):
        """Test that orphan action clears DB records but not files."""
        from unittest.mock import MagicMock

        from library.models import ImageMigrationJob
        from library.tasks import run_image_migration

        system = System.objects.create(
            name="Test",
            slug="test",
            extensions=[],
            folder_names=[],
            icon_path="/old/path/systems/test/icon.png",
            metadata_updated_at=timezone.now(),
        )

        # Game with downloaded image - should be affected
        game_with_download = Game.objects.create(
            name="Game With Download",
            system=system,
            metadata_updated_at=timezone.now(),
        )
        image_downloaded = GameImage.objects.create(
            game=game_with_download,
            file_path="/old/img.png",
            file_name="img.png",
            source="downloaded",
        )

        # Game with scanned image only - should NOT be affected
        game_with_scan = Game.objects.create(
            name="Game With Scan",
            system=system,
            metadata_updated_at=timezone.now(),
        )
        GameImage.objects.create(
            game=game_with_scan,
            file_path="/roms/img.png",
            file_name="img.png",
            source="scanned",
        )

        # Create migration job
        job = ImageMigrationJob.objects.create(
            action="orphan",
            old_path="/old/path",
            new_path="/new/path",
            total_images=1,
        )

        # Run the task with a mock context
        mock_context = MagicMock()
        mock_context.should_abort.return_value = False
        run_image_migration(mock_context, job.pk)

        # Verify results
        job.refresh_from_db()
        assert job.status == ImageMigrationJob.STATUS_COMPLETED

        # Downloaded image record should be deleted
        assert not GameImage.objects.filter(pk=image_downloaded.pk).exists()

        # Scanned image should still exist
        assert GameImage.objects.filter(source="scanned").count() == 1

        # System icon should be cleared
        system.refresh_from_db()
        assert system.icon_path == ""
        assert system.metadata_updated_at is None

        # Game with downloaded image should have metadata cleared
        game_with_download.refresh_from_db()
        assert game_with_download.metadata_updated_at is None

        # Game with only scanned image should NOT be affected
        game_with_scan.refresh_from_db()
        assert game_with_scan.metadata_updated_at is not None
