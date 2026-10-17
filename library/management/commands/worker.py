"""Custom worker command with orphaned job cleanup and periodic maintenance."""

import logging
from datetime import timedelta

from django.core.management import call_command
from django.core.management.base import BaseCommand
from django.utils import timezone

logger = logging.getLogger(__name__)


class Command(BaseCommand):
    help = "Start Procrastinate worker with orphaned job cleanup"

    def add_arguments(self, parser):
        # Accept all arguments that procrastinate worker accepts
        parser.add_argument("--concurrency", type=int, default=1)
        parser.add_argument("--queues", type=str, default="")
        parser.add_argument("--name", type=str, default="")

    def handle(self, *args, **options):
        # Cleanup orphaned jobs from crashed worker
        cleaned = self.cleanup_orphaned_jobs()
        if cleaned:
            logger.info(
                f"Marked {cleaned} orphaned job(s) as FAILED from previous worker crash"
            )

        # Schedule periodic cleanup tasks
        self.schedule_cleanup_tasks()

        # Build args for procrastinate worker
        worker_args = ["worker"]
        if options["concurrency"]:
            worker_args.extend(["--concurrency", str(options["concurrency"])])
        if options["queues"]:
            worker_args.extend(["--queues", options["queues"]])
        if options["name"]:
            worker_args.extend(["--name", options["name"]])

        # Delegate to procrastinate worker
        call_command("procrastinate", *worker_args)

    def schedule_cleanup_tasks(self) -> None:
        """Schedule periodic cleanup tasks to run at startup.

        Tasks will reschedule themselves after each run.
        """
        from romcollections.tasks import cleanup_expired_exports, cleanup_old_cover_jobs

        try:
            # Schedule cleanup tasks to run soon (within 1 minute)
            # They will reschedule themselves after execution
            run_at = timezone.now() + timedelta(minutes=1)

            # Cleanup expired exports (runs hourly, reschedules itself)
            cleanup_expired_exports.configure(schedule_at=run_at).defer()
            logger.info("Scheduled cleanup_expired_exports task")

            # Cleanup old cover jobs (runs daily, reschedules itself)
            cleanup_old_cover_jobs.configure(schedule_at=run_at).defer()
            logger.info("Scheduled cleanup_old_cover_jobs task")

        except Exception as e:
            # Don't fail worker startup if scheduling fails
            logger.warning(f"Failed to schedule cleanup tasks: {e}")

    def cleanup_orphaned_jobs(self) -> int:
        """Mark RUNNING jobs whose worker is gone as FAILED."""
        from library.job_cleanup import mark_orphaned_jobs

        return mark_orphaned_jobs()
