"""Queue definitions and priority constants for Procrastinate task queue."""

# Queue names - tasks are routed to specific queues based on their nature
QUEUE_USER_ACTIONS = "user_actions"  # User-initiated, expecting quick response
QUEUE_BACKGROUND = "background"  # Background processing, can wait
QUEUE_METADATA = "metadata"  # ScreenScraper API calls (rate-limited)
QUEUE_IDENTIFICATION = "identification"  # ROM hash lookup (Hasheous/ScreenScraper)

# All queues for worker startup
ALL_QUEUES = [QUEUE_USER_ACTIONS, QUEUE_BACKGROUND, QUEUE_METADATA, QUEUE_IDENTIFICATION]

# Priority levels (higher number = processed first)
# Within a queue, jobs are ordered by priority DESC, then created_at ASC
PRIORITY_CRITICAL = 100  # User is actively waiting (downloads)
PRIORITY_HIGH = 75  # User-initiated but can wait briefly
PRIORITY_NORMAL = 50  # Standard background work
PRIORITY_LOW = 25  # Batch operations
PRIORITY_BULK = 10  # Large imports, lowest priority
