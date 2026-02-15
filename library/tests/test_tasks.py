"""Tests for background scanning tasks and ScanJob model."""

import tempfile
from pathlib import Path
from unittest.mock import patch, MagicMock

from django.test import TestCase

from library.models import ScanJob, System
from library.tasks import run_scan


def create_mock_context(should_abort=False):
    """Create a mock JobContext for testing tasks that use pass_context=True."""
    mock_context = MagicMock()
    mock_context.should_abort.return_value = should_abort
    return mock_context


class TestScanJobModel(TestCase):
    """Test the ScanJob model."""

    def setUp(self):
        """Set up test data."""
        self.scan_job = ScanJob.objects.create(
            path="/test/path", task_id="test-task-id"
        )

    def test_scan_job_creation(self):
        """Test ScanJob creation with default values."""
        self.assertEqual(self.scan_job.path, "/test/path")
        self.assertEqual(self.scan_job.task_id, "test-task-id")
        self.assertEqual(self.scan_job.status, ScanJob.STATUS_PENDING)
        self.assertEqual(self.scan_job.added, 0)
        self.assertEqual(self.scan_job.skipped, 0)
        self.assertEqual(self.scan_job.deleted_roms, 0)
        self.assertEqual(self.scan_job.images_added, 0)
        self.assertEqual(self.scan_job.images_skipped, 0)
        self.assertEqual(self.scan_job.errors, [])
        self.assertIsNotNone(self.scan_job.started_at)
        self.assertIsNone(self.scan_job.completed_at)

    def test_scan_job_ordering(self):
        """Test that ScanJobs are ordered by started_at descending."""
        # Create jobs with different timestamps
        job1 = ScanJob.objects.create(path="/path1", task_id="task1")
        job2 = ScanJob.objects.create(path="/path2", task_id="task2")

        # Query all jobs and check ordering
        jobs = list(ScanJob.objects.all())
        self.assertEqual(jobs[0], job2)  # Most recent first
        self.assertEqual(jobs[1], job1)
        self.assertEqual(jobs[2], self.scan_job)  # Original job

    def test_status_choices(self):
        """Test all status choices are valid."""
        valid_statuses = [
            ScanJob.STATUS_PENDING,
            ScanJob.STATUS_RUNNING,
            ScanJob.STATUS_COMPLETED,
            ScanJob.STATUS_FAILED,
        ]

        for status in valid_statuses:
            job = ScanJob.objects.create(
                path=f"/test/{status}", task_id=f"task-{status}"
            )
            job.status = status
            job.save()
            self.assertEqual(job.status, status)


class TestRunScanTask(TestCase):
    """Test the run_scan background task."""

    def setUp(self):
        """Set up test data."""
        self.scan_job = ScanJob.objects.create(
            path="/test/path", task_id="test-task-id"
        )

        # Create a test system for scanning
        self.system = System.objects.create(
            name="Test System", slug="test", extensions=[".test"], folder_names=["test"]
        )

    @patch("library.tasks.scan_directory")
    def test_run_scan_success(self, mock_scan):
        """Test successful scan execution."""
        # Mock scan_directory result
        mock_result = {
            "added": 5,
            "skipped": 2,
            "deleted_roms": 1,
            "images_added": 3,
            "images_skipped": 1,
            "errors": [],
        }
        mock_scan.return_value = mock_result

        # Execute the task function directly (not through the task queue)
        mock_context = create_mock_context()
        result = run_scan.func(mock_context, self.scan_job.pk)

        # Verify the scan was called with correct path and progress callback
        self.assertEqual(mock_scan.call_count, 1)
        call_args = mock_scan.call_args
        self.assertEqual(call_args[0][0], "/test/path")
        self.assertIn("progress_callback", call_args[1])

        # Verify the job was updated correctly
        self.scan_job.refresh_from_db()
        self.assertEqual(self.scan_job.status, ScanJob.STATUS_COMPLETED)
        self.assertEqual(self.scan_job.added, 5)
        self.assertEqual(self.scan_job.skipped, 2)
        self.assertEqual(self.scan_job.deleted_roms, 1)
        self.assertEqual(self.scan_job.images_added, 3)
        self.assertEqual(self.scan_job.images_skipped, 1)
        self.assertEqual(self.scan_job.errors, [])
        self.assertIsNotNone(self.scan_job.completed_at)

        # Verify the task returned the scan result
        self.assertEqual(result, mock_result)

    @patch("library.tasks.scan_directory")
    def test_run_scan_with_errors(self, mock_scan):
        """Test scan execution with errors in result."""
        # Mock scan_directory result with errors
        mock_result = {
            "added": 1,
            "skipped": 0,
            "deleted_roms": 0,
            "images_added": 0,
            "images_skipped": 0,
            "errors": ["Test error 1", "Test error 2"],
        }
        mock_scan.return_value = mock_result

        # Execute the task function directly (not through the task queue)
        mock_context = create_mock_context()
        result = run_scan.func(mock_context, self.scan_job.pk)

        # Verify the job was updated with errors
        self.scan_job.refresh_from_db()
        self.assertEqual(self.scan_job.status, ScanJob.STATUS_COMPLETED)
        self.assertEqual(self.scan_job.errors, ["Test error 1", "Test error 2"])

        # Verify the task returned the scan result
        self.assertEqual(result, mock_result)

    @patch("library.tasks.scan_directory")
    def test_run_scan_exception(self, mock_scan):
        """Test scan execution when scan_directory raises exception."""
        # Mock scan_directory to raise exception
        mock_scan.side_effect = Exception("Test exception")

        # Execute the task function directly (not through the task queue)
        mock_context = create_mock_context()
        with self.assertRaises(Exception, msg="Test exception"):
            run_scan.func(mock_context, self.scan_job.pk)

        # Verify the job was marked as failed
        self.scan_job.refresh_from_db()
        self.assertEqual(self.scan_job.status, ScanJob.STATUS_FAILED)
        self.assertEqual(self.scan_job.errors, ["Test exception"])
        self.assertIsNotNone(self.scan_job.completed_at)

    def test_run_scan_job_not_found(self):
        """Test task execution when ScanJob doesn't exist."""
        mock_context = create_mock_context()
        with self.assertRaises(ScanJob.DoesNotExist):
            run_scan.func(mock_context, 99999)  # Non-existent ID

    @patch("library.tasks.scan_directory")
    def test_run_scan_updates_status_to_running(self, mock_scan):
        """Test that job status is updated to running before scan starts."""

        # Mock scan_directory to check status during execution
        def check_status_during_scan(
            path, progress_callback=None, use_hasheous=None, fetch_metadata=None
        ):
            # Check that status was updated to running
            self.scan_job.refresh_from_db()
            self.assertEqual(self.scan_job.status, ScanJob.STATUS_RUNNING)
            return {
                "added": 0,
                "skipped": 0,
                "deleted_roms": 0,
                "images_added": 0,
                "images_skipped": 0,
                "errors": [],
            }

        mock_scan.side_effect = check_status_during_scan

        # Execute the task function directly (not through the task queue)
        mock_context = create_mock_context()
        run_scan.func(mock_context, self.scan_job.pk)

        # Verify final status is completed
        self.scan_job.refresh_from_db()
        self.assertEqual(self.scan_job.status, ScanJob.STATUS_COMPLETED)


class TestScanJobIntegration(TestCase):
    """Integration tests for ScanJob with actual file system."""

    def setUp(self):
        """Set up test data with temporary directory."""
        self.temp_dir = tempfile.mkdtemp()
        self.test_system = System.objects.create(
            name="Test System",
            slug="test",
            extensions=[".test"],
            exclusive_extensions=[".test"],
            folder_names=["test"],
        )

    def test_scan_job_with_real_directory(self):
        """Test ScanJob with actual directory scanning."""
        # Create a test ROM file
        test_file = Path(self.temp_dir) / "test_rom.test"
        test_file.write_text("test content")

        # Create ScanJob
        scan_job = ScanJob.objects.create(path=self.temp_dir, task_id="test-task")

        # Execute the task function directly (not through the task queue)
        mock_context = create_mock_context()
        result = run_scan.func(mock_context, scan_job.pk)

        # Verify results
        self.assertEqual(result["added"], 1)
        self.assertEqual(result["skipped"], 0)
        self.assertEqual(result["errors"], [])

        # Verify ScanJob was updated
        scan_job.refresh_from_db()
        self.assertEqual(scan_job.status, ScanJob.STATUS_COMPLETED)
        self.assertEqual(scan_job.added, 1)
        self.assertEqual(scan_job.errors, [])
