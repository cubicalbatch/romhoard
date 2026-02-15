"""Display Procrastinate queue status report."""

from django.core.management.base import BaseCommand
from django.db import connection


class Command(BaseCommand):
    help = "Display Procrastinate queue status"

    def add_arguments(self, parser):
        parser.add_argument(
            "--detail",
            "-d",
            action="store_true",
            help="Show per-queue breakdown",
        )
        parser.add_argument(
            "--watch",
            "-w",
            action="store_true",
            help="Watch queue activity (shows jobs/sec)",
        )
        parser.add_argument(
            "--fix-orphaned",
            action="store_true",
            help="Mark orphaned 'doing' jobs as failed",
        )

    def handle(self, *_args, **options):
        detail = options["detail"]
        watch = options["watch"]
        fix_orphaned = options["fix_orphaned"]

        # Fix orphaned jobs if requested
        if fix_orphaned:
            self._fix_orphaned_jobs()

        # Watch mode - show throughput
        if watch:
            self._watch_queue()
            return

        with connection.cursor() as cursor:
            # Get counts by queue and status
            cursor.execute("""
                SELECT queue_name, status, COUNT(*)
                FROM procrastinate_jobs
                GROUP BY queue_name, status
                ORDER BY queue_name, status
            """)
            rows = cursor.fetchall()

            # Check for orphaned jobs (in 'doing' status - these are likely stuck)
            # Jobs stay in 'doing' only while actively processing, so any 'doing'
            # job when no worker is running is orphaned
            cursor.execute("""
                SELECT COUNT(*)
                FROM procrastinate_jobs
                WHERE status = 'doing'
            """)
            orphaned_count = cursor.fetchone()[0]

        # Organize data
        queues: dict[str, dict[str, int]] = {}
        totals: dict[str, int] = {
            "todo": 0,
            "doing": 0,
            "succeeded": 0,
            "failed": 0,
            "aborted": 0,
        }

        for queue_name, status, count in rows:
            if queue_name not in queues:
                queues[queue_name] = {}
            queues[queue_name][status] = count
            if status in totals:
                totals[status] += count

        # Print summary
        self.stdout.write("")
        self.stdout.write(self.style.HTTP_INFO("=== Queue Summary ==="))
        self.stdout.write("")
        self.stdout.write(
            f"  Pending (todo):     {self.style.WARNING(str(totals['todo']).rjust(8))}"
        )
        self.stdout.write(
            f"  In Progress (doing):{self.style.HTTP_INFO(str(totals['doing']).rjust(8))}"
        )
        self.stdout.write(
            f"  Succeeded:          {self.style.SUCCESS(str(totals['succeeded']).rjust(8))}"
        )
        self.stdout.write(
            f"  Failed:             {self.style.ERROR(str(totals['failed']).rjust(8))}"
        )
        if totals["aborted"]:
            self.stdout.write(
                f"  Aborted:            {str(totals['aborted']).rjust(8)}"
            )
        self.stdout.write("")

        # Warn about potential orphaned jobs
        if orphaned_count:
            self.stdout.write(
                self.style.WARNING(
                    f"  ⚠ {orphaned_count} jobs in 'doing' status\n"
                    f"    If no worker is running, these are orphaned.\n"
                    f"    Run with --fix-orphaned to mark them as failed"
                )
            )
            self.stdout.write("")

        # Print per-queue breakdown if detail
        if detail and queues:
            self.stdout.write(self.style.HTTP_INFO("=== Per-Queue Breakdown ==="))
            self.stdout.write("")

            header = f"  {'Queue':<20} {'Todo':>8} {'Doing':>8} {'OK':>8} {'Failed':>8}"
            self.stdout.write(header)
            self.stdout.write("  " + "-" * 56)

            for queue_name in sorted(queues.keys()):
                q = queues[queue_name]
                todo = q.get("todo", 0)
                doing = q.get("doing", 0)
                succeeded = q.get("succeeded", 0)
                failed = q.get("failed", 0)

                # Color code based on status (apply color after padding)
                todo_str = (
                    self.style.WARNING(str(todo).rjust(8))
                    if todo
                    else str(todo).rjust(8)
                )
                doing_str = (
                    self.style.HTTP_INFO(str(doing).rjust(8))
                    if doing
                    else str(doing).rjust(8)
                )
                ok_str = (
                    self.style.SUCCESS(str(succeeded).rjust(8))
                    if succeeded
                    else str(succeeded).rjust(8)
                )
                failed_str = (
                    self.style.ERROR(str(failed).rjust(8))
                    if failed
                    else str(failed).rjust(8)
                )

                self.stdout.write(
                    f"  {queue_name:<20} {todo_str} {doing_str} {ok_str} {failed_str}"
                )

            self.stdout.write("")

    def _watch_queue(self) -> None:
        """Watch queue throughput in real-time."""
        import sys
        import time

        print("Watching queue (Ctrl+C to stop)...\n", flush=True)
        print(
            f"{'Time':<10} {'Todo':>8} {'Doing':>8} {'Done':>10} {'Jobs/sec':>10}",
            flush=True,
        )
        print("-" * 50, flush=True)

        last_succeeded = None
        last_time = None

        try:
            while True:
                with connection.cursor() as cursor:
                    cursor.execute("""
                        SELECT status, COUNT(*)
                        FROM procrastinate_jobs
                        GROUP BY status
                    """)
                    counts = {row[0]: row[1] for row in cursor.fetchall()}

                todo = counts.get("todo", 0)
                doing = counts.get("doing", 0)
                succeeded = counts.get("succeeded", 0)

                now = time.time()
                if last_succeeded is not None and last_time is not None:
                    elapsed = now - last_time
                    jobs_done = succeeded - last_succeeded
                    rate = jobs_done / elapsed if elapsed > 0 else 0
                    rate_str = f"{rate:.1f}"
                else:
                    rate_str = "-"

                timestamp = time.strftime("%H:%M:%S")
                print(
                    f"{timestamp:<10} {todo:>8} {doing:>8} {succeeded:>10} {rate_str:>10}",
                    flush=True,
                )

                last_succeeded = succeeded
                last_time = now
                time.sleep(10)

        except KeyboardInterrupt:
            print("\nStopped.", flush=True)
            sys.exit(0)

    def _fix_orphaned_jobs(self) -> None:
        """Mark all 'doing' jobs as failed (they're orphaned if no worker is running)."""
        with connection.cursor() as cursor:
            cursor.execute("""
                UPDATE procrastinate_jobs
                SET status = 'failed'
                WHERE status = 'doing'
            """)
            count = cursor.rowcount

        if count:
            self.stdout.write(
                self.style.SUCCESS(f"Marked {count} orphaned jobs as failed")
            )
        else:
            self.stdout.write("No orphaned jobs to fix")
