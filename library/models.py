from django.db import models
from django.utils.text import slugify


class Genre(models.Model):
    """Game genre for filtering and categorization."""

    name = models.CharField(max_length=100, unique=True)
    slug = models.SlugField(max_length=100, unique=True)
    parent = models.ForeignKey(
        "self",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="children",
    )

    class Meta:
        ordering = ["name"]

    def __str__(self):
        return self.name

    @property
    def short_name(self):
        """Return just the subgenre part for display (after last ' / ')."""
        if " / " in self.name:
            return self.name.rsplit(" / ", 1)[-1]
        return self.name

    def save(self, *args, **kwargs):
        if not self.slug:
            self.slug = slugify(self.name)
        super().save(*args, **kwargs)


class System(models.Model):
    """Static reference data for gaming systems. Seeded via migration."""

    name = models.CharField(max_length=100)  # "Game Boy Advance"
    slug = models.SlugField(unique=True)  # "gba"
    extensions = models.JSONField()  # [".gba"]
    exclusive_extensions = models.JSONField(
        default=list
    )  # [".gba"] - unique to this system
    folder_names = models.JSONField()  # ["GBA", "gba", "Game Boy Advance"]
    archive_as_rom = models.BooleanField(
        default=False
    )  # Treat archives as ROMs (e.g., MAME)
    screenscraper_ids = models.JSONField(
        default=list, blank=True
    )  # [primary_id, alt1, alt2, ...] ScreenScraper system IDs

    # Metadata fields (from ScreenScraper)
    release_year = models.CharField(max_length=10, blank=True)  # "2001"
    icon_path = models.CharField(max_length=500, blank=True)  # Path to 32x32 icon
    metadata_updated_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["name"]

    def __str__(self) -> str:
        return self.name

    @property
    def screenscraper_id(self) -> int | None:
        """Primary ScreenScraper ID (for system metadata fetching)."""
        return self.screenscraper_ids[0] if self.screenscraper_ids else None

    @property
    def all_screenscraper_ids(self) -> list[int]:
        """All ScreenScraper IDs for game matching (primary + alternates)."""
        return self.screenscraper_ids or []


class Game(models.Model):
    """Represents a canonical game title. Multiple ROMs can belong to one Game."""

    # Source tracking choices
    SOURCE_FILENAME = "filename"
    SOURCE_SCREENSCRAPER = "screenscraper"
    SOURCE_MANUAL = "manual"
    SOURCE_HASHEOUS = "hasheous"  # Hasheous (unknown source)
    # Hasheous signature sources (used directly from API):
    # "NoIntros", "Redump", etc.

    SOURCE_COLLECTION = "collection"

    SOURCE_CHOICES = [
        ("NoIntros", "No-Intro (Hasheous)"),
        ("Redump", "Redump (Hasheous)"),
        (SOURCE_HASHEOUS, "Hasheous"),
        (SOURCE_FILENAME, "Filename parsing"),
        (SOURCE_SCREENSCRAPER, "ScreenScraper"),
        (SOURCE_MANUAL, "Manual entry"),
        (SOURCE_COLLECTION, "Collection import"),
    ]

    name = models.CharField(max_length=255)  # "Advance Wars"
    system = models.ForeignKey(System, on_delete=models.CASCADE, related_name="games")

    # Name source tracking
    name_source = models.CharField(
        max_length=20,
        choices=SOURCE_CHOICES,
        default=SOURCE_FILENAME,
        blank=True,
    )

    # Default ROMSet selection for exports
    default_rom_set = models.ForeignKey(
        "ROMSet",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="default_for_game_set",
    )

    # Metadata fields (from ScreenScraper)
    screenscraper_id = models.IntegerField(null=True, blank=True, db_index=True)
    description = models.TextField(blank=True)
    genres = models.ManyToManyField("Genre", related_name="games", blank=True)
    release_date = models.DateField(null=True, blank=True)
    developer = models.CharField(max_length=200, blank=True)
    publisher = models.CharField(max_length=200, blank=True)
    players = models.CharField(max_length=50, blank=True)
    rating = models.IntegerField(
        null=True,
        blank=True,
        help_text="Rating out of 100 (converted from source scale)",
    )
    rating_source = models.CharField(
        max_length=50,
        blank=True,
        help_text="Source of rating (e.g., 'screenscraper', 'metacritic')",
    )
    metadata_updated_at = models.DateTimeField(null=True, blank=True)
    metadata_match_failed = models.BooleanField(
        default=False,
        help_text="Set when metadata fetch was attempted but no match was found",
    )

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        unique_together = ["name", "system"]
        ordering = ["name"]

    def save(self, *args, **kwargs):
        if not self.name or not self.name.strip():
            raise ValueError("Game name cannot be empty")
        super().save(*args, **kwargs)

    def __str__(self) -> str:
        return f"{self.name} ({self.system.slug})"

    @property
    def wheel_image(self):
        """Return the wheel image for display, or None if not available."""
        images = list(self.images.all())  # Works with prefetched
        for img in images:
            if img.image_type == "wheel":
                return img
        return None

    @property
    def wheel_mini_image(self):
        """Return the wheel-mini thumbnail for menus, or None if not available."""
        images = list(self.images.all())  # Works with prefetched
        for img in images:
            if img.image_type == "wheel_mini":
                return img
        return None

    @property
    def screenshot_title_image(self):
        """Return the screenshot title image, or None if not available."""
        images = list(self.images.all())  # Works with prefetched
        for img in images:
            if img.image_type == "screenshot_title":
                return img
        return None

    @property
    def cover_image(self):
        """Return the cover/box art image, or None if not available."""
        images = list(self.images.all())  # Works with prefetched
        for img in images:
            if img.image_type == "cover":
                return img
        return None

    def display_genres(self):
        """Return genres excluding parents when their children are also present.

        When a game has both "Action" and "Action / Labyrinth", this returns
        only "Action / Labyrinth" since the more specific genre is present.
        """
        all_genres = list(self.genres.all())
        parent_ids = {g.parent_id for g in all_genres if g.parent_id}
        return [g for g in all_genres if g.pk not in parent_ids]


class ROMSet(models.Model):
    """A complete playable version of a game (1 or more ROMs)."""

    game = models.ForeignKey(Game, on_delete=models.CASCADE, related_name="rom_sets")
    region = models.CharField(max_length=100, blank=True)
    revision = models.CharField(max_length=50, blank=True)
    source_path = models.CharField(
        max_length=1000, blank=True
    )  # Archive path or parent dir for grouping

    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        unique_together = ["game", "region", "revision", "source_path"]
        ordering = ["region", "revision", "source_path"]

    def __str__(self) -> str:
        parts = [self.game.name]
        if self.region:
            parts.append(f"({self.region})")
        if self.revision:
            parts.append(f"({self.revision})")
        return " ".join(parts)

    @property
    def rom_count(self):
        return self.roms.count()

    @property
    def is_multi_disc(self):
        return self.roms.filter(disc__isnull=False).count() > 1


class ROM(models.Model):
    """A specific ROM file on disk. References a ROMSet."""

    rom_set = models.ForeignKey(
        ROMSet,
        on_delete=models.CASCADE,
        related_name="roms",
    )

    # File info
    file_path = models.CharField(max_length=1000)  # Absolute path
    file_name = models.CharField(max_length=255)  # Just the filename
    file_size = models.BigIntegerField()

    # Archive support
    archive_path = models.CharField(
        max_length=1000, blank=True
    )  # e.g., "/roms/gba/collection.7z"
    path_in_archive = models.CharField(
        max_length=500, blank=True
    )  # e.g., "Games/Mario.gba"

    # Hash fields for verification and matching
    crc32 = models.CharField(max_length=8, blank=True, db_index=True)  # 8 hex chars
    sha1 = models.CharField(
        max_length=40, blank=True, db_index=True
    )  # 40 hex chars (CHD internal hash)

    # Parsed metadata
    tags = models.JSONField(default=list)  # ["[!]", "(Beta)"]
    rom_number = models.CharField(max_length=20, blank=True)  # "237", "0001", etc.
    disc = models.PositiveSmallIntegerField(null=True, blank=True)  # Disc/track number

    # Nintendo Switch content type detection
    content_type = models.CharField(
        max_length=10, blank=True
    )  # "base", "update", "dlc" (Switch only)
    switch_title_id = models.CharField(
        max_length=16, blank=True, db_index=True
    )  # 16-hex-digit Title ID

    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["disc", "file_name"]

    def __str__(self) -> str:
        return self.file_name

    @property
    def game(self):
        """Convenience property to access game."""
        return self.rom_set.game

    @property
    def is_archived(self) -> bool:
        """Check if ROM lives inside an archive."""
        return bool(self.archive_path)


class GameImage(models.Model):
    """An image file associated with a game (box art, screenshot, etc.)."""

    game = models.ForeignKey(Game, on_delete=models.CASCADE, related_name="images")

    # File info
    file_path = models.CharField(max_length=1024, unique=True)  # Absolute path
    file_name = models.CharField(max_length=255)  # Just the filename
    file_size = models.BigIntegerField(default=0)

    image_type = models.CharField(
        max_length=20,
        choices=[
            ("", "Unknown"),
            ("cover", "Cover"),
            ("screenshot", "Screenshot"),
            ("screenshot_title", "Screenshot Title"),
            ("mix", "Mix"),
            ("wheel", "Wheel"),
            ("wheel_mini", "Wheel Mini"),
        ],
        default="",
        blank=True,
    )

    source = models.CharField(
        max_length=20,
        choices=[
            ("scanned", "Scanned from disk"),
            ("downloaded", "Downloaded from metadata service"),
            ("uploaded", "Uploaded by user"),
        ],
        default="scanned",
    )

    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["image_type", "file_name"]

    def __str__(self) -> str:
        return self.file_name


class Setting(models.Model):
    """Simple key-value store for user preferences."""

    key = models.CharField(max_length=100, unique=True)
    value = models.JSONField()

    def __str__(self) -> str:
        return self.key

    @classmethod
    def get(cls, key: str, default=None):
        """Get a setting value by key, returning default if not found."""
        from .crypto import decrypt_value, is_sensitive_key

        try:
            value = cls.objects.get(key=key).value
        except cls.DoesNotExist:
            return default

        if is_sensitive_key(key) and isinstance(value, str):
            decrypted = decrypt_value(value)
            if decrypted is None:
                # Decryption failed (SECRET_KEY changed) - clear invalid credential
                cls.objects.filter(key=key).delete()
                # Also invalidate credential validation status
                cls.objects.filter(key="screenscraper_credentials_valid").delete()
                return default
            return decrypted
        return value

    @classmethod
    def set(cls, key: str, value) -> "Setting":
        """Set a setting value, creating or updating as needed."""
        from .crypto import encrypt_value, is_sensitive_key

        if is_sensitive_key(key) and isinstance(value, str):
            value = encrypt_value(value)

        setting, _ = cls.objects.update_or_create(key=key, defaults={"value": value})
        return setting


class ScanPath(models.Model):
    """Saved directory paths for ROM scanning."""

    # Schedule interval choices
    SCHEDULE_HOURLY = "hourly"
    SCHEDULE_DAILY = "daily"
    SCHEDULE_WEEKLY = "weekly"
    SCHEDULE_MONTHLY = "monthly"

    SCHEDULE_CHOICES = [
        (SCHEDULE_HOURLY, "Every hour"),
        (SCHEDULE_DAILY, "Every day"),
        (SCHEDULE_WEEKLY, "Every week"),
        (SCHEDULE_MONTHLY, "Every month"),
    ]

    path = models.CharField(max_length=500, unique=True)
    last_scanned = models.DateTimeField(null=True, blank=True)
    use_hasheous = models.BooleanField(default=True)
    fetch_metadata = models.BooleanField(default=True)  # Auto-queue metadata fetch
    created_at = models.DateTimeField(auto_now_add=True)

    # Scheduled scanning
    schedule_enabled = models.BooleanField(default=False)
    schedule_interval = models.CharField(
        max_length=20, choices=SCHEDULE_CHOICES, default=SCHEDULE_DAILY
    )

    class Meta:
        ordering = ["-last_scanned", "-created_at"]

    def __str__(self) -> str:
        return self.path

    def get_next_scan_time(self):
        """Returns datetime of next scheduled scan, or None if not scheduled."""
        from datetime import timedelta

        if not self.schedule_enabled:
            return None

        if not self.last_scanned:
            # Never scanned - due now
            return None

        interval_deltas = {
            self.SCHEDULE_HOURLY: timedelta(hours=1),
            self.SCHEDULE_DAILY: timedelta(days=1),
            self.SCHEDULE_WEEKLY: timedelta(weeks=1),
            self.SCHEDULE_MONTHLY: timedelta(days=30),
        }

        delta = interval_deltas.get(self.schedule_interval, timedelta(days=1))
        return self.last_scanned + delta

    def is_due_for_scan(self) -> bool:
        """Returns True if path is due for scheduled scanning."""
        from django.utils import timezone

        if not self.schedule_enabled:
            return False

        next_scan = self.get_next_scan_time()
        if next_scan is None:
            # Never scanned - due now
            return True

        return timezone.now() >= next_scan


class ScanJob(models.Model):
    """Tracks background scan jobs."""

    STATUS_PENDING = "pending"
    STATUS_RUNNING = "running"
    STATUS_COMPLETED = "completed"
    STATUS_FAILED = "failed"
    STATUS_CANCELLED = "cancelled"

    STATUS_CHOICES = [
        (STATUS_PENDING, "Pending"),
        (STATUS_RUNNING, "Running"),
        (STATUS_COMPLETED, "Completed"),
        (STATUS_FAILED, "Failed"),
        (STATUS_CANCELLED, "Cancelled"),
    ]

    path = models.CharField(max_length=500)
    task_id = models.CharField(max_length=64, unique=True)  # Django task ID
    status = models.CharField(
        max_length=20, choices=STATUS_CHOICES, default=STATUS_PENDING
    )

    # Progress tracking (updated during scan)
    files_processed = models.IntegerField(default=0)
    roms_found = models.IntegerField(default=0)
    images_found = models.IntegerField(default=0)
    current_directory = models.CharField(max_length=500, blank=True, default="")

    # Options
    use_hasheous = models.BooleanField(default=True)  # Enable Hasheous API lookup
    fetch_metadata = models.BooleanField(default=True)  # Auto-queue metadata fetch

    # Results (populated on completion)
    added = models.IntegerField(default=0)
    skipped = models.IntegerField(default=0)
    deleted_roms = models.IntegerField(default=0)
    images_added = models.IntegerField(default=0)
    images_skipped = models.IntegerField(default=0)
    metadata_queued = models.IntegerField(default=0)  # Actual metadata jobs queued
    errors = models.JSONField(default=list)

    # Timestamps
    started_at = models.DateTimeField(auto_now_add=True)
    scan_started_at = models.DateTimeField(
        null=True, blank=True
    )  # When actual scanning begins
    completed_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["-started_at"]

    @property
    def running_duration(self):
        """Return duration since scan actually started (for running jobs)."""
        if self.scan_started_at and self.status == self.STATUS_RUNNING:
            from django.utils import timezone

            return timezone.now() - self.scan_started_at
        return None

    @property
    def total_duration(self):
        """Return total execution duration (for completed jobs)."""
        if self.scan_started_at and self.completed_at:
            return self.completed_at - self.scan_started_at
        return None


class DownloadJob(models.Model):
    """Tracks background download bundle jobs."""

    STATUS_PENDING = "pending"
    STATUS_RUNNING = "running"
    STATUS_COMPLETED = "completed"
    STATUS_FAILED = "failed"

    STATUS_CHOICES = [
        (STATUS_PENDING, "Pending"),
        (STATUS_RUNNING, "Running"),
        (STATUS_COMPLETED, "Completed"),
        (STATUS_FAILED, "Failed"),
    ]

    task_id = models.CharField(max_length=64, unique=True)
    status = models.CharField(
        max_length=20, choices=STATUS_CHOICES, default=STATUS_PENDING
    )

    # What to download
    game_ids = models.JSONField()  # [1, 2, 3, ...]
    system_slug = models.CharField(max_length=50)

    # Device configuration (optional)
    device_id = models.IntegerField(null=True, blank=True)

    # Progress tracking
    games_processed = models.IntegerField(default=0)
    games_total = models.IntegerField(default=0)
    current_game = models.CharField(max_length=255, blank=True, default="")
    bytes_written = models.BigIntegerField(default=0)

    # Results
    file_path = models.CharField(max_length=1000, blank=True)
    file_name = models.CharField(max_length=255, blank=True)
    file_size = models.BigIntegerField(default=0)
    games_included = models.IntegerField(default=0)
    games_failed = models.IntegerField(default=0)
    errors = models.JSONField(default=list)

    # Timestamps
    created_at = models.DateTimeField(auto_now_add=True)
    completed_at = models.DateTimeField(null=True, blank=True)
    expires_at = models.DateTimeField(null=True, blank=True)  # For cleanup

    class Meta:
        ordering = ["-created_at"]

    def __str__(self) -> str:
        return f"Download {len(self.game_ids)} games ({self.status})"

    @property
    def progress_percent(self) -> int:
        """Return progress as percentage."""
        if self.games_total == 0:
            return 0
        return int((self.games_processed / self.games_total) * 100)

    @property
    def is_expired(self) -> bool:
        """Check if the download file should be cleaned up."""
        if not self.expires_at:
            return False
        from django.utils import timezone

        return timezone.now() > self.expires_at


class SendJob(models.Model):
    """Tracks FTP/SFTP upload jobs for sending ROMs to devices."""

    STATUS_PENDING = "pending"
    STATUS_TESTING = "testing"
    STATUS_RUNNING = "running"
    STATUS_COMPLETED = "completed"
    STATUS_FAILED = "failed"

    STATUS_CHOICES = [
        (STATUS_PENDING, "Pending"),
        (STATUS_TESTING, "Testing Connection"),
        (STATUS_RUNNING, "Uploading"),
        (STATUS_COMPLETED, "Completed"),
        (STATUS_FAILED, "Failed"),
    ]

    task_id = models.CharField(max_length=64, unique=True)
    status = models.CharField(
        max_length=20, choices=STATUS_CHOICES, default=STATUS_PENDING
    )

    # Configuration
    device = models.ForeignKey(
        "devices.Device",
        on_delete=models.SET_NULL,
        null=True,
        related_name="send_jobs",
    )
    game_ids = models.JSONField(default=list)
    rom_ids = models.JSONField(
        default=list
    )  # For sending specific ROMs instead of games

    # Progress tracking
    files_total = models.IntegerField(default=0)
    files_uploaded = models.IntegerField(default=0)
    files_skipped = models.IntegerField(default=0)  # Skipped due to same size
    files_failed = models.IntegerField(default=0)
    current_file = models.CharField(max_length=255, blank=True, default="")
    bytes_uploaded = models.BigIntegerField(default=0)
    bytes_total = models.BigIntegerField(default=0)

    # Results - ROMs
    uploaded_files = models.JSONField(
        default=list
    )  # [{game_id, filename, remote_path, bytes}]
    skipped_files = models.JSONField(default=list)  # [{game_id, filename, reason}]
    failed_files = models.JSONField(default=list)  # [{game_id, filename, error}]

    # Results - Images
    uploaded_images = models.JSONField(
        default=list
    )  # [{game_id, rom_filename, remote_path, bytes}]
    skipped_images = models.JSONField(
        default=list
    )  # [{game_id, rom_filename, remote_path}]
    failed_images = models.JSONField(
        default=list
    )  # [{game_id, rom_filename, remote_path, error}]

    error = models.TextField(blank=True, default="")  # Job-level error

    # Timestamps
    created_at = models.DateTimeField(auto_now_add=True)
    started_at = models.DateTimeField(null=True, blank=True)
    completed_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self) -> str:
        device_name = self.device.name if self.device else "Unknown"
        return f"Send to {device_name} ({self.status})"

    @property
    def progress_percent(self) -> int:
        """Return progress as percentage."""
        if self.files_total == 0:
            return 0
        return int((self.files_processed / self.files_total) * 100)

    @property
    def files_processed(self) -> int:
        """Total files processed (uploaded + skipped + failed)."""
        return self.files_uploaded + self.files_skipped + self.files_failed

    @property
    def running_duration(self):
        """Duration since job started."""
        if self.started_at:
            from django.utils import timezone

            end = self.completed_at or timezone.now()
            return end - self.started_at
        return None


class UploadJob(models.Model):
    """Tracks browser upload jobs for adding games to the library."""

    STATUS_UPLOADING = "uploading"  # Files being received from browser
    STATUS_PROCESSING = "processing"  # Processing uploaded files
    STATUS_AWAITING_INPUT = "awaiting"  # Waiting for user to resolve unidentified games
    STATUS_COMPLETED = "completed"
    STATUS_FAILED = "failed"
    STATUS_CANCELLED = "cancelled"

    STATUS_CHOICES = [
        (STATUS_UPLOADING, "Uploading"),
        (STATUS_PROCESSING, "Processing"),
        (STATUS_AWAITING_INPUT, "Awaiting Input"),
        (STATUS_COMPLETED, "Completed"),
        (STATUS_FAILED, "Failed"),
        (STATUS_CANCELLED, "Cancelled"),
    ]

    task_id = models.CharField(max_length=64, blank=True, default="")
    status = models.CharField(
        max_length=20, choices=STATUS_CHOICES, default=STATUS_UPLOADING
    )

    # Progress tracking (uploading phase)
    files_total = models.IntegerField(default=0)
    files_uploaded = models.IntegerField(default=0)
    bytes_total = models.BigIntegerField(default=0)
    bytes_uploaded = models.BigIntegerField(default=0)
    current_file = models.CharField(max_length=255, blank=True, default="")

    # Processing results
    games_added = models.IntegerField(default=0)
    games_skipped = models.IntegerField(default=0)  # Duplicates
    games_failed = models.IntegerField(default=0)

    # Unidentified games (system couldn't be detected)
    # Stored as list of dicts: [{temp_path, filename}, ...]
    unidentified_files = models.JSONField(default=list)

    # Options
    fetch_metadata = models.BooleanField(default=True)  # Auto-queue metadata fetch

    # Errors and warnings
    errors = models.JSONField(default=list)

    # Timestamps
    created_at = models.DateTimeField(auto_now_add=True)
    upload_completed_at = models.DateTimeField(null=True, blank=True)
    completed_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self) -> str:
        return f"Upload {self.files_total} files ({self.status})"

    @property
    def progress_percent(self) -> int:
        """Return progress as percentage based on current phase."""
        if self.status == self.STATUS_UPLOADING:
            if self.files_total == 0:
                return 0
            return int((self.files_uploaded / self.files_total) * 100)
        elif self.status == self.STATUS_PROCESSING:
            # During processing, show based on files processed
            total_to_process = self.files_total - len(self.unidentified_files)
            processed = self.games_added + self.games_skipped + self.games_failed
            if total_to_process == 0:
                return 100
            return int((processed / total_to_process) * 100)
        elif self.status == self.STATUS_COMPLETED:
            return 100
        return 0

    @property
    def files_processed(self) -> int:
        """Total files processed (added + skipped + failed)."""
        return self.games_added + self.games_skipped + self.games_failed


class MetadataBatch(models.Model):
    """Tracks a batch of metadata fetch jobs."""

    STATUS_QUEUING = "queuing"
    STATUS_PENDING = "pending"
    STATUS_RUNNING = "running"
    STATUS_COMPLETED = "completed"
    STATUS_CANCELLED = "cancelled"

    STATUS_CHOICES = [
        (STATUS_QUEUING, "Queuing"),
        (STATUS_PENDING, "Pending"),
        (STATUS_RUNNING, "Running"),
        (STATUS_COMPLETED, "Completed"),
        (STATUS_CANCELLED, "Cancelled"),
    ]

    system_slug = models.CharField(max_length=50, blank=True)  # Empty = all systems
    status = models.CharField(
        max_length=20, choices=STATUS_CHOICES, default=STATUS_PENDING
    )

    # Timestamps
    created_at = models.DateTimeField(auto_now_add=True)
    started_at = models.DateTimeField(null=True, blank=True)
    completed_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self) -> str:
        scope = self.system_slug or "all systems"
        return f"Metadata batch for {scope} ({self.status})"

    @property
    def running_duration(self):
        """Duration since batch started running."""
        if self.started_at:
            from django.utils import timezone

            end = self.completed_at or timezone.now()
            return end - self.started_at
        return None

    @property
    def pending_count(self):
        """Number of pending jobs in batch."""
        return self.jobs.filter(status=MetadataJob.STATUS_PENDING).count()

    @property
    def completed_count(self):
        """Number of completed jobs in batch."""
        return self.jobs.filter(status=MetadataJob.STATUS_COMPLETED).count()

    @property
    def failed_count(self):
        """Number of failed jobs in batch."""
        return self.jobs.filter(status=MetadataJob.STATUS_FAILED).count()

    @property
    def total_count(self):
        """Total number of jobs in batch."""
        return self.jobs.count()

    @property
    def matched_count(self):
        """Number of jobs that matched metadata."""
        return self.jobs.filter(
            status=MetadataJob.STATUS_COMPLETED, matched=True
        ).count()

    @property
    def images_downloaded(self):
        """Total images downloaded across all jobs."""
        from django.db.models import Sum

        return self.jobs.aggregate(total=Sum("images_downloaded"))["total"] or 0


class MetadataJob(models.Model):
    """Tracks a single metadata fetch job for one game."""

    STATUS_PENDING = "pending"
    STATUS_RUNNING = "running"
    STATUS_COMPLETED = "completed"
    STATUS_FAILED = "failed"
    STATUS_CANCELLED = "cancelled"

    STATUS_CHOICES = [
        (STATUS_PENDING, "Pending"),
        (STATUS_RUNNING, "Running"),
        (STATUS_COMPLETED, "Completed"),
        (STATUS_FAILED, "Failed"),
        (STATUS_CANCELLED, "Cancelled"),
    ]

    task_id = models.CharField(max_length=64, unique=True)
    status = models.CharField(
        max_length=20, choices=STATUS_CHOICES, default=STATUS_PENDING
    )

    # Link to batch (optional - single game fetches may not have a batch)
    batch = models.ForeignKey(
        MetadataBatch,
        on_delete=models.CASCADE,
        related_name="jobs",
        null=True,
        blank=True,
    )

    # Single game reference
    game = models.ForeignKey(
        Game, on_delete=models.CASCADE, related_name="metadata_jobs"
    )

    # Results
    matched = models.BooleanField(default=False)
    images_downloaded = models.IntegerField(default=0)
    error = models.TextField(blank=True)

    # Timestamps
    created_at = models.DateTimeField(auto_now_add=True)
    started_at = models.DateTimeField(null=True, blank=True)
    completed_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["-created_at"]
        indexes = [
            models.Index(fields=["batch", "status"]),
        ]

    def __str__(self) -> str:
        return f"Metadata job for {self.game.name} ({self.status})"


class SystemMetadataJob(models.Model):
    """Tracks background system metadata fetch jobs."""

    STATUS_PENDING = "pending"
    STATUS_RUNNING = "running"
    STATUS_COMPLETED = "completed"
    STATUS_FAILED = "failed"
    STATUS_CANCELLED = "cancelled"

    STATUS_CHOICES = [
        (STATUS_PENDING, "Pending"),
        (STATUS_RUNNING, "Running"),
        (STATUS_COMPLETED, "Completed"),
        (STATUS_FAILED, "Failed"),
        (STATUS_CANCELLED, "Cancelled"),
    ]

    task_id = models.CharField(max_length=64, unique=True)
    status = models.CharField(
        max_length=20, choices=STATUS_CHOICES, default=STATUS_PENDING
    )

    # Progress tracking
    systems_total = models.IntegerField(default=0)
    systems_processed = models.IntegerField(default=0)
    current_system = models.CharField(max_length=100, blank=True)

    # Results
    systems_updated = models.IntegerField(default=0)
    systems_skipped = models.IntegerField(default=0)
    icons_downloaded = models.IntegerField(default=0)
    error = models.TextField(blank=True)

    # Timestamps
    created_at = models.DateTimeField(auto_now_add=True)
    started_at = models.DateTimeField(null=True, blank=True)
    completed_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self) -> str:
        return f"System Metadata ({self.status})"

    @property
    def running_duration(self):
        """Calculate running duration for display."""
        from django.utils import timezone

        if not self.started_at:
            return None

        end_time = self.completed_at or timezone.now()
        delta = end_time - self.started_at
        total_seconds = int(delta.total_seconds())

        if total_seconds < 60:
            return f"{total_seconds}s"
        elif total_seconds < 3600:
            minutes = total_seconds // 60
            seconds = total_seconds % 60
            return f"{minutes}m {seconds}s"
        else:
            hours = total_seconds // 3600
            minutes = (total_seconds % 3600) // 60
            return f"{hours}h {minutes}m"

    @property
    def progress_percent(self) -> int:
        """Return progress as percentage."""
        if self.systems_total == 0:
            return 0
        return int((self.systems_processed / self.systems_total) * 100)


class ImageMigrationJob(models.Model):
    """Tracks background image migration jobs when image storage path changes."""

    STATUS_PENDING = "pending"
    STATUS_RUNNING = "running"
    STATUS_COMPLETED = "completed"
    STATUS_FAILED = "failed"

    STATUS_CHOICES = [
        (STATUS_PENDING, "Pending"),
        (STATUS_RUNNING, "Running"),
        (STATUS_COMPLETED, "Completed"),
        (STATUS_FAILED, "Failed"),
    ]

    ACTION_MOVE = "move"
    ACTION_DELETE = "delete"
    ACTION_ORPHAN = "orphan"

    ACTION_CHOICES = [
        (ACTION_MOVE, "Move to new path"),
        (ACTION_DELETE, "Delete from disk"),
        (ACTION_ORPHAN, "Leave in place (orphan)"),
    ]

    task_id = models.CharField(max_length=64, blank=True)
    status = models.CharField(
        max_length=20, choices=STATUS_CHOICES, default=STATUS_PENDING
    )
    action = models.CharField(max_length=20, choices=ACTION_CHOICES)

    # Paths
    old_path = models.CharField(max_length=1000)
    new_path = models.CharField(max_length=1000, blank=True)

    # Progress tracking
    total_images = models.IntegerField(default=0)
    processed_images = models.IntegerField(default=0)
    skipped_images = models.IntegerField(default=0)  # Already exists at destination
    failed_images = models.IntegerField(default=0)

    # Results
    error_message = models.TextField(blank=True)
    errors = models.JSONField(default=list)  # List of per-file errors

    # Timestamps
    created_at = models.DateTimeField(auto_now_add=True)
    started_at = models.DateTimeField(null=True, blank=True)
    completed_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self) -> str:
        return f"Image migration ({self.action}) - {self.status}"

    @property
    def progress_percent(self) -> int:
        """Return progress as percentage."""
        if self.total_images == 0:
            return 0
        return int((self.processed_images / self.total_images) * 100)

    @property
    def running_duration(self):
        """Duration since job started."""
        if self.started_at:
            from django.utils import timezone

            end = self.completed_at or timezone.now()
            return end - self.started_at
        return None


class HasheousCache(models.Model):
    """Cache for Hasheous API lookups to avoid repeated API calls.

    Stores both successful matches and known misses (matched=False) to
    avoid re-querying hashes that aren't in the Hasheous database.
    """

    # Hash lookup key
    hash_type = models.CharField(max_length=10)  # "crc32", "sha1", "md5"
    hash_value = models.CharField(max_length=64)

    # Match result
    matched = models.BooleanField(default=False)

    # Game info (populated when matched=True)
    game_name = models.CharField(max_length=500, blank=True)
    region = models.CharField(max_length=100, blank=True)
    revision = models.CharField(max_length=100, blank=True)
    tags = models.JSONField(default=list)
    source = models.CharField(max_length=50, blank=True)  # "NoIntros", "Redump", etc.
    raw_name = models.CharField(max_length=500, blank=True)
    platform_name = models.CharField(max_length=100, blank=True)

    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        unique_together = [("hash_type", "hash_value")]
        indexes = [
            models.Index(fields=["hash_type", "hash_value"]),
        ]

    def __str__(self) -> str:
        status = "matched" if self.matched else "no match"
        return f"{self.hash_type}:{self.hash_value[:8]}... ({status})"


class ScreenScraperLookupCache(models.Model):
    """Cache for ScreenScraper identification lookups.

    Caches both CRC and romnom (filename) lookups to avoid repeated API calls.
    Each entry is unique by (lookup_type, lookup_value, system_id) since the
    same CRC or filename may exist on different systems.
    """

    # Lookup key
    lookup_type = models.CharField(max_length=10)  # "crc" or "romnom"
    lookup_value = models.CharField(max_length=255)  # CRC or filename
    system_id = models.IntegerField()  # ScreenScraper system ID

    # Result
    matched = models.BooleanField(default=False)
    screenscraper_id = models.IntegerField(null=True, blank=True)
    game_name = models.CharField(max_length=500, blank=True)

    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        unique_together = [["lookup_type", "lookup_value", "system_id"]]
        indexes = [
            models.Index(fields=["lookup_type", "lookup_value", "system_id"]),
        ]

    def __str__(self) -> str:
        status = "matched" if self.matched else "no match"
        return f"{self.lookup_type}:{self.lookup_value[:20]}... ({status})"
