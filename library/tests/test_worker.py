"""Tests for worker command and orphaned job cleanup."""

from django.test import TestCase
from django.utils import timezone

from library.management.commands.worker import Command as WorkerCommand
from library.models import (
    DownloadJob,
    Game,
    MetadataJob,
    ScanJob,
    System,
    SystemMetadataJob,
)


class TestCleanupOrphanedJobs(TestCase):
    """Test the cleanup_orphaned_jobs functionality."""

    def setUp(self):
        """Set up test data."""
        self.command = WorkerCommand()

        # Create a test system and game for MetadataJob
        self.system = System.objects.create(
            name="Test System",
            slug="test",
            extensions=[".test"],
            folder_names=["test"],
        )
        self.game = Game.objects.create(
            name="Test Game",
            system=self.system,
        )

    def test_cleanup_running_scan_jobs(self):
        """Test that RUNNING ScanJobs are marked as FAILED."""
        job = ScanJob.objects.create(
            path="/test/path",
            task_id="scan-task-1",
            status=ScanJob.STATUS_RUNNING,
        )

        cleaned = self.command.cleanup_orphaned_jobs()

        job.refresh_from_db()
        self.assertEqual(cleaned, 1)
        self.assertEqual(job.status, ScanJob.STATUS_FAILED)
        self.assertEqual(job.errors, ["Worker crashed during execution"])
        self.assertIsNotNone(job.completed_at)

    def test_cleanup_running_download_jobs(self):
        """Test that RUNNING DownloadJobs are marked as FAILED."""
        job = DownloadJob.objects.create(
            task_id="download-task-1",
            status=DownloadJob.STATUS_RUNNING,
            game_ids=[1, 2, 3],
            system_slug="test",
        )

        cleaned = self.command.cleanup_orphaned_jobs()

        job.refresh_from_db()
        self.assertEqual(cleaned, 1)
        self.assertEqual(job.status, DownloadJob.STATUS_FAILED)
        self.assertEqual(job.errors, ["Worker crashed during execution"])
        self.assertIsNotNone(job.completed_at)

    def test_cleanup_running_metadata_jobs(self):
        """Test that RUNNING MetadataJobs are marked as FAILED."""
        job = MetadataJob.objects.create(
            task_id="metadata-task-1",
            status=MetadataJob.STATUS_RUNNING,
            game=self.game,
        )

        cleaned = self.command.cleanup_orphaned_jobs()

        job.refresh_from_db()
        self.assertEqual(cleaned, 1)
        self.assertEqual(job.status, MetadataJob.STATUS_FAILED)
        self.assertEqual(job.error, "Worker crashed during execution")
        self.assertIsNotNone(job.completed_at)

    def test_cleanup_running_system_metadata_jobs(self):
        """Test that RUNNING SystemMetadataJobs are marked as FAILED."""
        job = SystemMetadataJob.objects.create(
            task_id="sysmeta-task-1",
            status=SystemMetadataJob.STATUS_RUNNING,
        )

        cleaned = self.command.cleanup_orphaned_jobs()

        job.refresh_from_db()
        self.assertEqual(cleaned, 1)
        self.assertEqual(job.status, SystemMetadataJob.STATUS_FAILED)
        self.assertEqual(job.error, "Worker crashed during execution")
        self.assertIsNotNone(job.completed_at)

    def test_cleanup_does_not_affect_pending_jobs(self):
        """Test that PENDING jobs are not affected."""
        job = ScanJob.objects.create(
            path="/test/path",
            task_id="pending-task-1",
            status=ScanJob.STATUS_PENDING,
        )

        cleaned = self.command.cleanup_orphaned_jobs()

        job.refresh_from_db()
        self.assertEqual(cleaned, 0)
        self.assertEqual(job.status, ScanJob.STATUS_PENDING)

    def test_cleanup_does_not_affect_completed_jobs(self):
        """Test that COMPLETED jobs are not affected."""
        job = ScanJob.objects.create(
            path="/test/path",
            task_id="completed-task-1",
            status=ScanJob.STATUS_COMPLETED,
            completed_at=timezone.now(),
        )

        cleaned = self.command.cleanup_orphaned_jobs()

        job.refresh_from_db()
        self.assertEqual(cleaned, 0)
        self.assertEqual(job.status, ScanJob.STATUS_COMPLETED)

    def test_cleanup_does_not_affect_failed_jobs(self):
        """Test that already FAILED jobs are not affected."""
        job = ScanJob.objects.create(
            path="/test/path",
            task_id="failed-task-1",
            status=ScanJob.STATUS_FAILED,
            errors=["Original error"],
            completed_at=timezone.now(),
        )

        cleaned = self.command.cleanup_orphaned_jobs()

        job.refresh_from_db()
        self.assertEqual(cleaned, 0)
        self.assertEqual(job.status, ScanJob.STATUS_FAILED)
        self.assertEqual(job.errors, ["Original error"])

    def test_cleanup_multiple_job_types(self):
        """Test cleanup of multiple job types in one call."""
        scan_job = ScanJob.objects.create(
            path="/test/path",
            task_id="scan-multi-1",
            status=ScanJob.STATUS_RUNNING,
        )
        download_job = DownloadJob.objects.create(
            task_id="download-multi-1",
            status=DownloadJob.STATUS_RUNNING,
            game_ids=[1],
            system_slug="test",
        )
        metadata_job = MetadataJob.objects.create(
            task_id="metadata-multi-1",
            status=MetadataJob.STATUS_RUNNING,
            game=self.game,
        )

        cleaned = self.command.cleanup_orphaned_jobs()

        self.assertEqual(cleaned, 3)

        scan_job.refresh_from_db()
        download_job.refresh_from_db()
        metadata_job.refresh_from_db()

        self.assertEqual(scan_job.status, ScanJob.STATUS_FAILED)
        self.assertEqual(download_job.status, DownloadJob.STATUS_FAILED)
        self.assertEqual(metadata_job.status, MetadataJob.STATUS_FAILED)

    def test_cleanup_returns_zero_when_no_running_jobs(self):
        """Test that cleanup returns 0 when there are no running jobs."""
        # Create only completed jobs
        ScanJob.objects.create(
            path="/test/path",
            task_id="completed-1",
            status=ScanJob.STATUS_COMPLETED,
        )

        cleaned = self.command.cleanup_orphaned_jobs()

        self.assertEqual(cleaned, 0)
