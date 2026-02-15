"""Django signals for romcollections app."""

import uuid
from pathlib import Path

from django.db.models.signals import post_save, pre_delete
from django.dispatch import receiver

from library.queues import PRIORITY_LOW

from .models import Collection, ExportJob
from .tasks import create_collection_export


@receiver(pre_delete, sender=Collection)
def cleanup_collection_cover(sender, instance, **kwargs):
    """Delete cover image file when collection is deleted."""
    if instance.cover_image_path:
        cover_path = Path(instance.cover_image_path)
        if cover_path.exists():
            try:
                cover_path.unlink()
                # Also try to remove the parent directory if empty
                parent = cover_path.parent
                if parent.exists() and not any(parent.iterdir()):
                    parent.rmdir()
            except (OSError, IOError):
                pass  # Best effort cleanup


@receiver(post_save, sender=Collection)
def trigger_export_on_save(sender, instance, created, update_fields, **kwargs):
    """Trigger export generation when a public collection is saved.

    Only triggers on creation or when is_public becomes True, not on every save.
    """
    # Only trigger on newly created collections or when is_public just became True
    if not created:
        # Check if is_public was just changed to True
        if not instance.is_public:
            return
        # If update_fields is specified and doesn't include is_public, skip
        if update_fields and "is_public" not in update_fields:
            return
    elif not instance.is_public:
        return

    if not instance.entries.exists():
        return

    # Create export job and queue task
    task_id = str(uuid.uuid4())
    job = ExportJob.objects.create(
        collection=instance,
        task_id=task_id,
    )

    create_collection_export.configure(priority=PRIORITY_LOW).defer(
        export_job_id=job.pk
    )
