from pathlib import Path

from django.core.validators import MaxLengthValidator
from django.db import models
from django.urls import reverse


class Collection(models.Model):
    """A curated list of games across multiple systems."""

    # Cover source choices
    COVER_SOURCE_NONE = ""
    COVER_SOURCE_UPLOADED = "uploaded"
    COVER_SOURCE_GENERATED = "generated"

    COVER_SOURCE_CHOICES = [
        (COVER_SOURCE_NONE, "None"),
        (COVER_SOURCE_UPLOADED, "Uploaded"),
        (COVER_SOURCE_GENERATED, "Generated"),
    ]

    # Cover generation type choices
    COVER_TYPE_COVER = "cover"
    COVER_TYPE_SCREENSHOT = "screenshot"
    COVER_TYPE_MIX = "mix"

    COVER_TYPE_CHOICES = [
        (COVER_TYPE_COVER, "Game Covers"),
        (COVER_TYPE_SCREENSHOT, "Screenshots"),
        (COVER_TYPE_MIX, "Mix Images"),
    ]

    slug = models.SlugField()  # Unique within creator namespace
    name = models.CharField(max_length=255)
    description = models.TextField(blank=True, validators=[MaxLengthValidator(1000)])
    creator = models.SlugField(max_length=255)  # Required, URL-safe
    is_public = models.BooleanField(default=True)
    is_community = models.BooleanField(default=False)
    is_favorites = models.BooleanField(default=False, db_index=True)
    tags = models.JSONField(default=list)

    # Cover image fields
    cover_image_path = models.CharField(max_length=1024, blank=True, default="")
    has_cover = models.BooleanField(default=False, db_index=True)
    cover_source = models.CharField(
        max_length=20,
        choices=COVER_SOURCE_CHOICES,
        default=COVER_SOURCE_NONE,
        blank=True,
    )
    cover_generation_type = models.CharField(
        max_length=20,
        choices=COVER_TYPE_CHOICES,
        default=COVER_TYPE_COVER,
        blank=True,
    )

    # Source tracking for URL imports
    source_url = models.URLField(max_length=2000, blank=True, null=True)
    last_synced_at = models.DateTimeField(blank=True, null=True)

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-is_favorites", "name"]  # Favorites always first
        unique_together = [("creator", "slug")]
        indexes = [
            models.Index(fields=["is_public", "-created_at"]),
        ]
        constraints = [
            models.UniqueConstraint(
                fields=["is_favorites"],
                condition=models.Q(is_favorites=True),
                name="unique_favorites_collection",
            )
        ]

    def __str__(self) -> str:
        return self.name

    def get_absolute_url(self) -> str:
        """Return the canonical URL for this collection."""
        return reverse(
            "romcollections:collection_detail",
            kwargs={"creator": self.creator, "slug": self.slug},
        )

    @property
    def entry_count(self) -> int:
        return self.entries.count()

    @property
    def matched_count(self) -> int:
        """Count entries that have a matching game with romsets in the library."""
        count = 0
        for entry in self.entries.all():
            game = entry.get_matched_game()
            if game is not None and game.rom_sets.exists():
                count += 1
        return count

    def get_sample_covers(self, limit: int = 5) -> list[dict]:
        """Get sample matched games that have images for preview.

        Args:
            limit: Maximum number of games to return

        Returns:
            List of dicts with 'game' and 'image' keys
        """
        games_with_covers = []
        for entry in self.entries.order_by("position"):
            if len(games_with_covers) >= limit:
                break
            game = entry.get_matched_game()
            if game:
                # Fallback order: cover -> mix -> screenshot -> wheel
                image = None
                for image_type in ["cover", "mix", "screenshot", "wheel"]:
                    image = game.images.filter(image_type=image_type).first()
                    if image:
                        break
                if image:
                    games_with_covers.append({"game": game, "image": image})
        return games_with_covers

    def set_cover(
        self,
        path: str,
        source: str,
        generation_type: str | None = None,
    ) -> None:
        """Set the cover image path and related fields.

        Args:
            path: Absolute path to the cover image file
            source: One of COVER_SOURCE_UPLOADED or COVER_SOURCE_GENERATED
            generation_type: For generated covers, the image type used
        """
        self.cover_image_path = path
        self.has_cover = True
        self.cover_source = source
        if generation_type:
            self.cover_generation_type = generation_type
        update_fields = ["cover_image_path", "has_cover", "cover_source"]
        if generation_type:
            update_fields.append("cover_generation_type")
        self.save(update_fields=update_fields)

    def delete_cover(self) -> None:
        """Delete cover image file and clear related fields."""
        if self.cover_image_path:
            cover_path = Path(self.cover_image_path)
            if cover_path.exists():
                cover_path.unlink(missing_ok=True)
        self.cover_image_path = ""
        self.has_cover = False
        self.cover_source = self.COVER_SOURCE_NONE
        self.save(update_fields=["cover_image_path", "has_cover", "cover_source"])

    def get_latest_export(self):
        """Get the most recent completed export for this collection.

        For persistent hub exports, we don't check expires_at since they
        remain valid until a new export is generated.
        """
        return (
            self.export_jobs.filter(status=ExportJob.STATUS_COMPLETED)
            .order_by("-completed_at")
            .first()
        )


class CollectionEntry(models.Model):
    """A game entry in a collection (declarative - stores name + system, not FK)."""

    collection = models.ForeignKey(
        Collection, on_delete=models.CASCADE, related_name="entries"
    )
    game_name = models.CharField(max_length=255)
    system_slug = models.CharField(max_length=50)
    position = models.PositiveIntegerField(default=0)
    notes = models.TextField(blank=True, validators=[MaxLengthValidator(1000)])

    added_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["position"]
        unique_together = ["collection", "game_name", "system_slug"]
        indexes = [
            models.Index(fields=["system_slug"]),
            models.Index(fields=["collection", "system_slug"]),
        ]

    def __str__(self) -> str:
        return f"{self.game_name} ({self.system_slug})"

    def get_matched_game(self):
        """Find matching Game in library using case-insensitive name match.

        Returns:
            Game instance if found, None otherwise
        """
        from library.models import Game

        return Game.objects.filter(
            name__iexact=self.game_name, system__slug=self.system_slug
        ).first()

    @property
    def is_matched(self) -> bool:
        """Check if this entry has a matching game in the library."""
        return self.get_matched_game() is not None


class ExportJob(models.Model):
    """Tracks background collection export jobs (ZIP with images)."""

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

    collection = models.ForeignKey(
        Collection, on_delete=models.CASCADE, related_name="export_jobs"
    )
    task_id = models.CharField(max_length=64, unique=True)
    status = models.CharField(
        max_length=20, choices=STATUS_CHOICES, default=STATUS_PENDING
    )

    # Progress tracking
    entries_total = models.IntegerField(default=0)
    entries_processed = models.IntegerField(default=0)
    images_total = models.IntegerField(default=0)
    images_processed = models.IntegerField(default=0)
    current_game = models.CharField(max_length=255, blank=True, default="")

    # Results
    file_path = models.CharField(max_length=1000, blank=True)
    file_name = models.CharField(max_length=255, blank=True)
    file_size = models.BigIntegerField(default=0)
    games_exported = models.IntegerField(default=0)
    images_exported = models.IntegerField(default=0)
    error = models.TextField(blank=True)

    # Timestamps
    created_at = models.DateTimeField(auto_now_add=True)
    completed_at = models.DateTimeField(null=True, blank=True)
    expires_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self) -> str:
        return f"Export {self.collection.name} ({self.status})"

    @property
    def progress_percent(self) -> int:
        """Return progress as percentage."""
        total = self.entries_total + self.images_total
        processed = self.entries_processed + self.images_processed
        if total == 0:
            return 0
        return int((processed / total) * 100)

    @property
    def is_expired(self) -> bool:
        """Check if the export file should be cleaned up."""
        if not self.expires_at:
            return False
        from django.utils import timezone

        return timezone.now() > self.expires_at


class CoverJob(models.Model):
    """Tracks background cover generation/upload jobs."""

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

    JOB_TYPE_UPLOAD = "upload"
    JOB_TYPE_GENERATE = "generate"

    JOB_TYPE_CHOICES = [
        (JOB_TYPE_UPLOAD, "Upload Processing"),
        (JOB_TYPE_GENERATE, "Auto-Generate"),
    ]

    collection = models.ForeignKey(
        Collection, on_delete=models.CASCADE, related_name="cover_jobs"
    )
    task_id = models.CharField(max_length=64, unique=True)
    status = models.CharField(
        max_length=20, choices=STATUS_CHOICES, default=STATUS_PENDING
    )
    job_type = models.CharField(max_length=20, choices=JOB_TYPE_CHOICES)
    image_type = models.CharField(
        max_length=20,
        choices=Collection.COVER_TYPE_CHOICES,
        default=Collection.COVER_TYPE_COVER,
    )

    # For upload jobs - temporary upload path
    upload_path = models.CharField(max_length=1024, blank=True, default="")

    # Result
    error = models.TextField(blank=True)

    # Timestamps
    created_at = models.DateTimeField(auto_now_add=True)
    completed_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["-created_at"]
        indexes = [
            models.Index(
                fields=["collection", "status"], name="romcollecti_collect_status_idx"
            ),
        ]

    def __str__(self) -> str:
        return f"Cover {self.job_type} for {self.collection.name} ({self.status})"
