"""Tests for job cancellation functionality."""

from unittest.mock import MagicMock, patch

import pytest
from django.test import Client
from django.urls import reverse
from django.utils import timezone
from procrastinate.exceptions import JobAborted

from library.models import (
    Game,
    MetadataBatch,
    MetadataJob,
    ScanJob,
    System,
    SystemMetadataJob,
)


@pytest.fixture
def client():
    return Client()


@pytest.fixture
def system(db):
    return System.objects.create(
        name="Test System",
        slug="test",
        extensions=["rom"],
        folder_names=["test"],
        screenscraper_ids=[1],
    )


@pytest.fixture
def game(system):
    return Game.objects.create(name="Test Game", system=system)


class TestCancelScanJob:
    """Tests for scan job cancellation."""

    def test_cancel_pending_scan_job(self, client, db):
        """Test cancelling a pending scan job."""
        job = ScanJob.objects.create(
            path="/test/path", task_id="123", status=ScanJob.STATUS_PENDING
        )

        with patch(
            "library.views.scan.procrastinate_app.job_manager.cancel_job_by_id"
        ) as mock_cancel:
            response = client.post(reverse("library:cancel_scan_job", args=[job.pk]))

        assert response.status_code == 302
        assert response.url == reverse("library:scan")

        job.refresh_from_db()
        assert job.status == ScanJob.STATUS_CANCELLED
        assert job.completed_at is not None
        mock_cancel.assert_called_once_with(123, abort=True)

    def test_cancel_running_scan_job(self, client, db):
        """Test cancelling a running scan job."""
        job = ScanJob.objects.create(
            path="/test/path", task_id="456", status=ScanJob.STATUS_RUNNING
        )

        with patch(
            "library.views.scan.procrastinate_app.job_manager.cancel_job_by_id"
        ) as mock_cancel:
            response = client.post(reverse("library:cancel_scan_job", args=[job.pk]))

        assert response.status_code == 302

        job.refresh_from_db()
        assert job.status == ScanJob.STATUS_CANCELLED
        mock_cancel.assert_called_once_with(456, abort=True)

    def test_cancel_already_completed_job_no_change(self, client, db):
        """Test that cancelling an already completed job doesn't change it."""
        job = ScanJob.objects.create(
            path="/test/path",
            task_id="789",
            status=ScanJob.STATUS_COMPLETED,
            completed_at=timezone.now(),
        )

        with patch(
            "library.views.scan.procrastinate_app.job_manager.cancel_job_by_id"
        ) as mock_cancel:
            response = client.post(reverse("library:cancel_scan_job", args=[job.pk]))

        assert response.status_code == 302

        job.refresh_from_db()
        assert job.status == ScanJob.STATUS_COMPLETED  # Unchanged
        mock_cancel.assert_not_called()

    def test_cancel_handles_procrastinate_error(self, client, db):
        """Test that Procrastinate errors are handled gracefully."""
        job = ScanJob.objects.create(
            path="/test/path", task_id="123", status=ScanJob.STATUS_RUNNING
        )

        with patch(
            "library.views.scan.procrastinate_app.job_manager.cancel_job_by_id"
        ) as mock_cancel:
            mock_cancel.side_effect = Exception("Procrastinate error")
            response = client.post(reverse("library:cancel_scan_job", args=[job.pk]))

        assert response.status_code == 302

        job.refresh_from_db()
        assert job.status == ScanJob.STATUS_CANCELLED  # Still updated


class TestCancelSystemMetadataJob:
    """Tests for system metadata job cancellation."""

    def test_cancel_pending_system_metadata_job(self, client, db):
        """Test cancelling a pending system metadata job."""
        job = SystemMetadataJob.objects.create(
            task_id="123", status=SystemMetadataJob.STATUS_PENDING
        )

        with patch(
            "library.views.metadata.procrastinate_app.job_manager.cancel_job_by_id"
        ) as mock_cancel:
            response = client.post(
                reverse("library:cancel_system_metadata_job", args=[job.pk])
            )

        assert response.status_code == 302
        assert response.url == reverse("library:metadata")

        job.refresh_from_db()
        assert job.status == SystemMetadataJob.STATUS_CANCELLED
        assert job.completed_at is not None
        mock_cancel.assert_called_once_with(123, abort=True)

    def test_cancel_running_system_metadata_job(self, client, db):
        """Test cancelling a running system metadata job."""
        job = SystemMetadataJob.objects.create(
            task_id="456", status=SystemMetadataJob.STATUS_RUNNING
        )

        with patch(
            "library.views.metadata.procrastinate_app.job_manager.cancel_job_by_id"
        ) as mock_cancel:
            response = client.post(
                reverse("library:cancel_system_metadata_job", args=[job.pk])
            )

        assert response.status_code == 302

        job.refresh_from_db()
        assert job.status == SystemMetadataJob.STATUS_CANCELLED
        mock_cancel.assert_called_once_with(456, abort=True)


class TestCancelMetadataBatch:
    """Tests for metadata batch cancellation."""

    def test_cancel_batch_cancels_pending_jobs(self, client, system, game):
        """Test that cancelling a batch cancels all pending jobs."""
        batch = MetadataBatch.objects.create(
            status=MetadataBatch.STATUS_RUNNING, started_at=timezone.now()
        )
        job1 = MetadataJob.objects.create(
            task_id="1", game=game, batch=batch, status=MetadataJob.STATUS_PENDING
        )
        job2 = MetadataJob.objects.create(
            task_id="2", game=game, batch=batch, status=MetadataJob.STATUS_PENDING
        )
        job3 = MetadataJob.objects.create(
            task_id="3",
            game=game,
            batch=batch,
            status=MetadataJob.STATUS_COMPLETED,
            completed_at=timezone.now(),
        )

        with patch(
            "library.views.metadata.procrastinate_app.job_manager.cancel_job_by_id"
        ) as mock_cancel:
            response = client.post(
                reverse("library:cancel_metadata_batch", args=[batch.pk])
            )

        assert response.status_code == 302

        batch.refresh_from_db()
        assert batch.status == MetadataBatch.STATUS_CANCELLED

        job1.refresh_from_db()
        job2.refresh_from_db()
        job3.refresh_from_db()
        assert job1.status == MetadataJob.STATUS_CANCELLED
        assert job2.status == MetadataJob.STATUS_CANCELLED
        assert job3.status == MetadataJob.STATUS_COMPLETED  # Unchanged

        # Verify Procrastinate cancel was called for pending jobs
        assert mock_cancel.call_count == 2

    def test_cancel_batch_also_aborts_running_jobs(self, client, system, game):
        """Test that cancelling a batch also tries to abort running jobs."""
        batch = MetadataBatch.objects.create(
            status=MetadataBatch.STATUS_RUNNING, started_at=timezone.now()
        )
        MetadataJob.objects.create(
            task_id="1", game=game, batch=batch, status=MetadataJob.STATUS_RUNNING
        )

        with patch(
            "library.views.metadata.procrastinate_app.job_manager.cancel_job_by_id"
        ) as mock_cancel:
            response = client.post(
                reverse("library:cancel_metadata_batch", args=[batch.pk])
            )

        assert response.status_code == 302

        # Verify cancel was called for the running job
        mock_cancel.assert_called_with(1, abort=True)


class TestTaskAbortChecking:
    """Tests for task abort checking logic."""

    def test_run_scan_handles_abort_in_progress(self, db, tmp_path):
        """Test that run_scan properly handles JobAborted during progress update."""
        from library.tasks import run_scan

        # Create a real directory with a file to scan
        scan_dir = tmp_path / "roms"
        scan_dir.mkdir()
        (scan_dir / "test.rom").write_text("test")

        job = ScanJob.objects.create(
            path=str(scan_dir), task_id="123", status=ScanJob.STATUS_PENDING
        )

        # Create a mock context that signals abort when should_abort is checked
        mock_context = MagicMock()
        mock_context.should_abort.return_value = True

        # Mock scan_directory to call the progress callback
        with patch("library.tasks.scan_directory") as mock_scan:

            def call_progress_and_abort(
                path, progress_callback, use_hasheous, fetch_metadata=False
            ):
                # This will trigger the abort check in the callback
                progress_callback(
                    {"files_processed": 1, "roms_found": 0, "images_found": 0}
                )
                return {
                    "added": 0,
                    "skipped": 0,
                    "marked_missing": 0,
                    "images_added": 0,
                    "images_skipped": 0,
                }

            mock_scan.side_effect = call_progress_and_abort

            with pytest.raises(JobAborted):
                run_scan(mock_context, job.pk)

        job.refresh_from_db()
        assert job.status == ScanJob.STATUS_CANCELLED

    def test_run_metadata_job_checks_cancelled_status(self, db, system, game):
        """Test that run_metadata_job_for_game checks cancelled status at start."""
        from library.tasks import run_metadata_job_for_game

        job = MetadataJob.objects.create(
            task_id="123", game=game, status=MetadataJob.STATUS_CANCELLED
        )

        mock_context = MagicMock()
        mock_context.should_abort.return_value = False  # Not aborted by Procrastinate

        # The task should still raise JobAborted because Django status is cancelled
        with pytest.raises(JobAborted):
            run_metadata_job_for_game(mock_context, job.pk)

        job.refresh_from_db()
        assert job.status == MetadataJob.STATUS_CANCELLED

    def test_run_system_metadata_handles_abort(self, db):
        """Test that run_system_metadata_fetch properly handles JobAborted."""
        from library.tasks import run_system_metadata_fetch

        job = SystemMetadataJob.objects.create(
            task_id="123", status=SystemMetadataJob.STATUS_PENDING
        )

        # Create a mock context that signals abort when checked
        mock_context = MagicMock()
        mock_context.should_abort.return_value = True

        # Mock the metadata fetching function to call the progress callback
        with patch(
            "library.metadata.matcher.fetch_system_metadata_for_job"
        ) as mock_fetch:

            def call_callback(progress_callback):
                # This will trigger the abort check in the callback
                progress_callback({"systems_total": 1, "systems_processed": 0})
                return {"updated": 0, "skipped": 0, "icons_downloaded": 0}

            mock_fetch.side_effect = call_callback

            with pytest.raises(JobAborted):
                run_system_metadata_fetch(mock_context, job.pk)

        job.refresh_from_db()
        assert job.status == SystemMetadataJob.STATUS_CANCELLED


class TestScanStatusView:
    """Tests for scan status view with cancelled jobs."""

    def test_cancelled_jobs_appear_in_recent(self, client, db):
        """Test that cancelled jobs appear in recent jobs list."""
        ScanJob.objects.create(
            path="/test",
            task_id="123",
            status=ScanJob.STATUS_CANCELLED,
            completed_at=timezone.now(),
        )

        response = client.get(reverse("library:scan_status"))

        assert response.status_code == 200
        assert b"cancelled" in response.content.lower()


class TestSystemMetadataStatusView:
    """Tests for system metadata status view with cancelled jobs."""

    def test_cancelled_jobs_appear_in_recent(self, client, db):
        """Test that cancelled jobs appear in recent jobs list."""
        SystemMetadataJob.objects.create(
            task_id="123",
            status=SystemMetadataJob.STATUS_CANCELLED,
            completed_at=timezone.now(),
        )

        response = client.get(reverse("library:system_metadata_status"))

        assert response.status_code == 200
        assert b"cancelled" in response.content.lower()
