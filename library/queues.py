"""Queue definitions and priority constants for Procrastinate task queue."""

# Queue names - tasks are routed to specific queues based on their nature
QUEUE_USER_ACTIONS = "user_actions"  # User-initiated, expecting quick response
QUEUE_BACKGROUND = "background"  # Background processing, can wait
QUEUE_METADATA = "metadata"  # ScreenScraper API calls (rate-limited)
QUEUE_IDENTIFICATION = "identification"  # ROM hash lookup (Hasheous/ScreenScraper)

# All queues for worker startup
ALL_QUEUES = [
    QUEUE_USER_ACTIONS,
    QUEUE_BACKGROUND,
    QUEUE_METADATA,
    QUEUE_IDENTIFICATION,
]

# Priority levels (higher number = processed first)
# Within a queue, jobs are ordered by priority DESC, then created_at ASC
PRIORITY_CRITICAL = 100  # User is actively waiting (downloads)
PRIORITY_HIGH = 75  # User-initiated but can wait briefly
PRIORITY_NORMAL = 50  # Standard background work
PRIORITY_LOW = 25  # Batch operations
PRIORITY_BULK = 10  # Large imports, lowest priority


def enqueue_job(job, task, **task_kwargs) -> int:
    """Publish a task and its owning job's task ID in one transaction.

    Procrastinate's Django connector shares the Django connection. Workers
    cannot claim the new task until its ID is saved on the application job.
    Updating only that field also preserves concurrent progress or edits.
    """
    from django.db import transaction

    with transaction.atomic():
        task_id = task.defer(**task_kwargs)
        type(job).objects.filter(pk=job.pk).update(task_id=str(task_id))
    job.task_id = str(task_id)
    return task_id
