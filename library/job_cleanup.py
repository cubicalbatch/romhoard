"""Fail job records whose worker died mid-run."""

import json
import logging
from datetime import timedelta

from django.db import DatabaseError, connection
from django.utils import timezone

from .models import DownloadJob, MetadataJob, ScanJob, SystemMetadataJob

logger = logging.getLogger(__name__)

ORPHAN_ERROR = "Worker crashed during execution"

# A worker is alive while its heartbeat is recent. Procrastinate beats every
# 10 s and prunes workers silent for 30 s; 60 s leaves room for slow beats.
LIVE_WORKER_WINDOW = timedelta(seconds=60)


def mark_orphaned_jobs() -> int:
    """Mark RUNNING job records as FAILED unless a live worker runs them.

    Several workers share the database (one per queue group), so a starting
    worker must leave jobs of its live siblings alone. A record is orphaned
    when its Procrastinate job is not being run by a worker with a recent
    heartbeat. A worker that just crashed still looks alive for up to
    LIVE_WORKER_WINDOW; the periodic `cleanup_orphaned_jobs` task catches it.

    Each model is updated by a single statement whose live-worker check runs
    inside the UPDATE, so a job claimed by a starting worker is never judged
    against a stale snapshot and falsely failed.

    Returns:
        Number of records marked FAILED.
    """
    orphan_errors = json.dumps([ORPHAN_ERROR])
    cleaned = 0
    now = timezone.now()
    try:
        with connection.cursor() as cursor:
            for Model, set_error_sql, error_param in (
                (ScanJob, "errors = %s::jsonb", orphan_errors),
                (DownloadJob, "errors = %s::jsonb", orphan_errors),
                (MetadataJob, "error = %s", ORPHAN_ERROR),
                (SystemMetadataJob, "error = %s", ORPHAN_ERROR),
            ):
                cursor.execute(
                    f"UPDATE {connection.ops.quote_name(Model._meta.db_table)} "
                    "SET status = %s, completed_at = %s, "
                    f"{set_error_sql} "
                    "WHERE status = %s AND task_id::text NOT IN ("
                    "    SELECT j.id::text FROM procrastinate_jobs j"
                    "    JOIN procrastinate_workers w ON w.id = j.worker_id"
                    "    WHERE j.status = 'doing'"
                    "      AND w.last_heartbeat > now() - %s)",
                    [
                        Model.STATUS_FAILED,
                        now,
                        error_param,
                        Model.STATUS_RUNNING,
                        LIVE_WORKER_WINDOW,
                    ],
                )
                count = cursor.rowcount
                if count:
                    logger.info(
                        f"Marked {count} orphaned {Model.__name__}(s) as FAILED"
                    )
                cleaned += count
    except DatabaseError:
        logger.debug("Skipping orphan cleanup - tables not yet created")
        return 0
    return cleaned
