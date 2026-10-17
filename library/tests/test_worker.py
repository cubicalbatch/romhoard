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


class TestOrphanDetectionRespectsLiveWorkers(TestCase):
    """A starting worker must not fail jobs that other live workers are running."""

    def _procrastinate_job(self, heartbeat_age_seconds: int | None) -> str:
        from django.db import connection

        with connection.cursor() as cursor:
            worker_id = None
            if heartbeat_age_seconds is not None:
                cursor.execute(
                    "INSERT INTO procrastinate_workers (last_heartbeat) "
                    "VALUES (now() - make_interval(secs => %s)) RETURNING id",
                    [heartbeat_age_seconds],
                )
                worker_id = cursor.fetchone()[0]
            cursor.execute(
                "INSERT INTO procrastinate_jobs (queue_name, task_name, status, worker_id) "
                "VALUES ('background', 'library.tasks.run_scan', 'doing', %s) RETURNING id",
                [worker_id],
            )
            return str(cursor.fetchone()[0])

    def _running_scan(self, task_id: str) -> ScanJob:
        return ScanJob.objects.create(
            path="/roms", task_id=task_id, status=ScanJob.STATUS_RUNNING
        )

    def test_job_held_by_live_worker_is_kept(self):
        scan = self._running_scan(self._procrastinate_job(heartbeat_age_seconds=5))

        cleaned = WorkerCommand().cleanup_orphaned_jobs()

        scan.refresh_from_db()
        self.assertEqual(cleaned, 0)
        self.assertEqual(scan.status, ScanJob.STATUS_RUNNING)

    def test_job_of_worker_with_stale_heartbeat_is_failed(self):
        scan = self._running_scan(self._procrastinate_job(heartbeat_age_seconds=600))

        WorkerCommand().cleanup_orphaned_jobs()

        scan.refresh_from_db()
        self.assertEqual(scan.status, ScanJob.STATUS_FAILED)

    def test_job_whose_worker_was_pruned_is_failed(self):
        scan = self._running_scan(self._procrastinate_job(heartbeat_age_seconds=None))

        WorkerCommand().cleanup_orphaned_jobs()

        scan.refresh_from_db()
        self.assertEqual(scan.status, ScanJob.STATUS_FAILED)

    def test_metadata_job_held_by_live_worker_is_kept(self):
        system = System.objects.create(
            name="Live", slug="live", extensions=[".x"], folder_names=[]
        )
        job = MetadataJob.objects.create(
            task_id=self._procrastinate_job(heartbeat_age_seconds=1),
            status=MetadataJob.STATUS_RUNNING,
            game=Game.objects.create(name="G", system=system),
        )

        WorkerCommand().cleanup_orphaned_jobs()

        job.refresh_from_db()
        self.assertEqual(job.status, MetadataJob.STATUS_RUNNING)

    def test_periodic_sweep_fails_orphans(self):
        from library.tasks import cleanup_orphaned_jobs

        scan = self._running_scan(self._procrastinate_job(heartbeat_age_seconds=600))

        result = cleanup_orphaned_jobs.func(0)

        scan.refresh_from_db()
        self.assertEqual(result, {"failed": 1})
        self.assertEqual(scan.status, ScanJob.STATUS_FAILED)

    def test_claim_landing_mid_cleanup_is_respected(self):
        """A claim committed while cleanup runs must not fail the job.

        The live-worker check has to happen inside the UPDATE statement: a
        snapshot taken before the update would miss this claim and falsely
        fail a job a worker just picked up.
        """
        from django.db import connection

        with connection.cursor() as cursor:
            cursor.execute(
                "INSERT INTO procrastinate_jobs (queue_name, task_name, status) "
                "VALUES ('background', 'library.tasks.run_scan', 'todo') RETURNING id"
            )
            job_id = cursor.fetchone()[0]
        scan = self._running_scan(str(job_id))

        intercepted: list[str] = []

        def claim_before_update(execute, sql, params, many, context):
            # Simulate a worker claiming the job after cleanup started: the
            # claim is committed just before cleanup's first UPDATE runs.
            if not intercepted and sql.lstrip().upper().startswith("UPDATE"):
                intercepted.append(sql)
                with connection.cursor() as cursor:
                    cursor.execute(
                        "INSERT INTO procrastinate_workers (last_heartbeat) "
                        "VALUES (now()) RETURNING id"
                    )
                    worker_id = cursor.fetchone()[0]
                    cursor.execute(
                        "UPDATE procrastinate_jobs "
                        "SET status = 'doing', worker_id = %s WHERE id = %s",
                        [worker_id, job_id],
                    )
            return execute(sql, params, many, context)

        with connection.execute_wrapper(claim_before_update):
            cleaned = WorkerCommand().cleanup_orphaned_jobs()

        self.assertTrue(intercepted)
        self.assertEqual(cleaned, 0)
        scan.refresh_from_db()
        self.assertEqual(scan.status, ScanJob.STATUS_RUNNING)
