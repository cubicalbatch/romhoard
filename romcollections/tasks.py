"""Background tasks for romcollections using Procrastinate task queue."""

import json
import logging
import os
import shutil
import zipfile
from datetime import timedelta
from pathlib import Path

from django.conf import settings
from django.utils import timezone
from procrastinate.contrib.django import app

from library.queues import QUEUE_BACKGROUND, QUEUE_USER_ACTIONS

from .models import Collection, CoverJob, ExportJob
from .serializers import export_collection as serialize_export

logger = logging.getLogger(__name__)


def _get_exports_dir() -> Path:
    """Get directory for storing collection exports.

    Uses the same location as images but in an 'exports' subdirectory.
    Falls back to BASE_DIR/data/exports if no image storage is configured.

    Returns:
        Path to the exports directory
    """
    # Check dedicated image storage path first
    image_path = getattr(settings, "IMAGE_STORAGE_PATH", None)
    if image_path:
        return Path(image_path) / "exports"
    # Fallback to ROM library root
    library_root = getattr(settings, "ROM_LIBRARY_ROOT", None)
    if library_root:
        return Path(library_root) / "exports"
    if settings.MEDIA_ROOT:
        return Path(settings.MEDIA_ROOT) / "exports"
    # Default to data/exports/ in the project directory
    return settings.BASE_DIR / "data" / "exports"


def export_game_metadata(game) -> dict:
    """Export game metadata to a dictionary.

    Args:
        game: Game instance

    Returns:
        Dictionary with game metadata
    """
    return {
        "name": game.name,
        "system_slug": game.system.slug,
        "screenscraper_id": game.screenscraper_id,
        "description": game.description,
        "genres": list(game.genres.values_list("name", flat=True)),
        "release_date": game.release_date.isoformat() if game.release_date else None,
        "developer": game.developer,
        "publisher": game.publisher,
        "players": game.players,
        "rating": game.rating,
        "rating_source": game.rating_source,
    }


@app.task(queue=QUEUE_USER_ACTIONS)
def create_collection_export(export_job_id: int) -> dict:
    """Background task to create a collection export ZIP with images.

    Creates a ZIP file containing:
    - collection.json: The standard collection export
    - games/: Folder with metadata JSON for each matched game
    - images/: Folder with all game images organized by game name

    Args:
        export_job_id: ID of ExportJob to process

    Returns:
        Dict with export results
    """
    job = ExportJob.objects.get(pk=export_job_id)
    job.status = ExportJob.STATUS_RUNNING
    job.save()

    def update_progress():
        """Save current progress to database."""
        ExportJob.objects.filter(pk=export_job_id).update(
            entries_processed=job.entries_processed,
            images_processed=job.images_processed,
            current_game=job.current_game[:255],
        )

    try:
        collection = job.collection

        # Clean up old completed export jobs for this collection
        # (their files will be overwritten by the new export)
        ExportJob.objects.filter(
            collection=collection,
            status=ExportJob.STATUS_COMPLETED,
        ).exclude(pk=job.pk).delete()

        # Get matched games and count images
        matched_games = []
        total_images = 0
        for entry in collection.entries.all():
            game = entry.get_matched_game()
            if game:
                matched_games.append((entry, game))
                total_images += game.images.count()

        job.entries_total = len(matched_games)
        job.images_total = total_images
        job.save()

        # Create exports directory if it doesn't exist
        exports_dir = _get_exports_dir()
        exports_dir.mkdir(parents=True, exist_ok=True)

        # Create collection-specific export directory
        collection_export_dir = exports_dir / collection.slug
        collection_export_dir.mkdir(parents=True, exist_ok=True)

        # Final export path (persistent)
        final_zip_path = collection_export_dir / f"{collection.slug}_with_images.zip"

        # Create temp ZIP file first
        temp_zip_path = final_zip_path.with_suffix(".zip.tmp")

        try:
            with zipfile.ZipFile(temp_zip_path, "w", zipfile.ZIP_DEFLATED) as zipf:
                # Add collection.json
                collection_data = serialize_export(collection)
                zipf.writestr(
                    "collection.json",
                    json.dumps(collection_data, indent=2, ensure_ascii=False),
                )

                # Add collection cover image if exists
                if collection.has_cover and os.path.exists(collection.cover_image_path):
                    try:
                        ext = Path(collection.cover_image_path).suffix.lower() or ".png"
                        zipf.write(collection.cover_image_path, f"cover{ext}")
                    except (IOError, OSError) as e:
                        logger.warning(f"Failed to add cover image: {e}")

                # Process each matched game
                for entry, game in matched_games:
                    job.current_game = game.name
                    update_progress()

                    # Create safe folder name
                    safe_name = _sanitize_filename(f"{game.name}_{game.system.slug}")

                    # Add game metadata JSON
                    metadata = export_game_metadata(game)
                    zipf.writestr(
                        f"games/{safe_name}.json",
                        json.dumps(metadata, indent=2, ensure_ascii=False),
                    )

                    job.entries_processed += 1
                    job.games_exported += 1

                    # Add images for this game
                    for image in game.images.all():
                        if os.path.exists(image.file_path):
                            try:
                                # Determine extension from file
                                ext = Path(image.file_path).suffix.lower()
                                if not ext:
                                    ext = ".png"

                                # Create image path in ZIP
                                image_filename = (
                                    f"{image.image_type}{ext}"
                                    if image.image_type
                                    else f"unknown{ext}"
                                )
                                image_path = f"images/{safe_name}/{image_filename}"

                                zipf.write(image.file_path, image_path)
                                job.images_exported += 1
                            except (IOError, OSError) as e:
                                logger.warning(
                                    f"Failed to add image {image.file_path}: {e}"
                                )

                        job.images_processed += 1
                        update_progress()

            # Move temp file to final location
            if final_zip_path.exists():
                final_zip_path.unlink()
            shutil.move(str(temp_zip_path), str(final_zip_path))

            # Complete the job
            job.status = ExportJob.STATUS_COMPLETED
            job.file_path = str(final_zip_path)
            job.file_name = f"{collection.slug}_with_images.zip"
            job.file_size = final_zip_path.stat().st_size
            job.completed_at = timezone.now()
            # No expiration for hub exports - they persist until regenerated
            job.expires_at = None
            job.save()

            return {
                "games_exported": job.games_exported,
                "images_exported": job.images_exported,
                "file_size": job.file_size,
            }

        except Exception:
            # Clean up temp file on error
            if temp_zip_path.exists():
                temp_zip_path.unlink()
            raise

    except Exception as e:
        job.status = ExportJob.STATUS_FAILED
        job.error = str(e)
        job.completed_at = timezone.now()
        job.save()
        logger.exception(f"Export job {export_job_id} failed")
        raise


def _sanitize_filename(name: str) -> str:
    """Sanitize a string for use as a filename/folder name."""
    replacements = {
        "/": "-",
        "\\": "-",
        ":": " -",
        "*": "",
        "?": "",
        '"': "'",
        "<": "",
        ">": "",
        "|": "-",
    }
    for char, replacement in replacements.items():
        name = name.replace(char, replacement)
    return name.strip(". ")


@app.task(queue=QUEUE_BACKGROUND, queueing_lock="cleanup_expired_exports")
def cleanup_expired_exports() -> int:
    """Delete expired export bundles and their database records.

    This task runs periodically (every hour) to clean up export files
    that have passed their expiration time. It reschedules itself.

    Returns:
        Number of exports cleaned up
    """
    expired = ExportJob.objects.filter(expires_at__lt=timezone.now())

    count = 0
    for job in expired:
        if job.file_path and Path(job.file_path).exists():
            try:
                Path(job.file_path).unlink()
            except (OSError, IOError):
                pass
        job.delete()
        count += 1

    if count > 0:
        logger.info(f"Cleaned up {count} expired export jobs")

    # Reschedule to run again in 1 hour (queueing_lock prevents duplicates)
    try:
        cleanup_expired_exports.configure(
            schedule_at=timezone.now() + timedelta(hours=1)
        ).defer()
    except Exception as e:
        logger.warning(f"Failed to reschedule cleanup_expired_exports: {e}")

    return count


@app.task(queue=QUEUE_USER_ACTIONS)
def process_cover_upload(cover_job_id: int) -> dict:
    """Process an uploaded cover image.

    Reads from temporary upload path, resizes to max dimensions,
    saves to final location, and updates Collection model.

    Args:
        cover_job_id: ID of CoverJob to process

    Returns:
        Dict with processing results
    """
    from .cover_utils import get_collection_cover_path, resize_cover_image

    try:
        job = CoverJob.objects.get(pk=cover_job_id)
    except CoverJob.DoesNotExist:
        logger.warning(f"CoverJob {cover_job_id} not found (may have been deleted)")
        return {"error": "Job not found"}

    upload_path = job.upload_path  # Store for cleanup in finally block

    job.status = CoverJob.STATUS_RUNNING
    job.save()

    try:
        collection = job.collection

        # Verify upload file exists
        if not upload_path or not Path(upload_path).exists():
            raise FileNotFoundError(f"Upload file not found: {upload_path}")

        # Resize and save cover
        cover_data = resize_cover_image(upload_path)
        cover_path = get_collection_cover_path(collection.slug)

        with open(cover_path, "wb") as f:
            f.write(cover_data)

        # Update collection using set_cover method
        collection.set_cover(
            path=str(cover_path),
            source=Collection.COVER_SOURCE_UPLOADED,
        )

        # Mark job completed
        job.status = CoverJob.STATUS_COMPLETED
        job.completed_at = timezone.now()
        job.save()

        logger.info(f"Cover upload processed for collection {collection.slug}")

        return {
            "collection_slug": collection.slug,
            "cover_path": str(cover_path),
        }

    except Exception as e:
        job.status = CoverJob.STATUS_FAILED
        job.error = str(e)
        job.completed_at = timezone.now()
        job.save()
        logger.exception(f"Cover upload job {cover_job_id} failed")
        raise

    finally:
        # Always clean up temp file, even on failure
        if upload_path:
            try:
                Path(upload_path).unlink(missing_ok=True)
            except OSError:
                pass


@app.task(queue=QUEUE_BACKGROUND)
def generate_collection_cover(cover_job_id: int) -> dict:
    """Generate a cover collage from game images.

    Fetches sample games with images, creates a collage using
    the specified image type, and saves to the collection.

    Args:
        cover_job_id: ID of CoverJob to process

    Returns:
        Dict with generation results
    """
    from .cover_utils import (
        create_collage_cover,
        get_collection_cover_path,
        get_sample_game_images,
    )

    try:
        job = CoverJob.objects.get(pk=cover_job_id)
    except CoverJob.DoesNotExist:
        logger.warning(f"CoverJob {cover_job_id} not found (may have been deleted)")
        return {"error": "Job not found"}

    job.status = CoverJob.STATUS_RUNNING
    job.save()

    try:
        collection = job.collection
        image_type = job.image_type or Collection.COVER_TYPE_COVER

        # Get sample game images
        game_images = get_sample_game_images(collection, image_type=image_type, limit=5)

        if not game_images:
            raise ValueError(f"No games with {image_type} images found in collection")

        # Create collage
        cover_data = create_collage_cover(game_images)
        cover_path = get_collection_cover_path(collection.slug)

        with open(cover_path, "wb") as f:
            f.write(cover_data)

        # Update collection using set_cover method
        collection.set_cover(
            path=str(cover_path),
            source=Collection.COVER_SOURCE_GENERATED,
            generation_type=image_type,
        )

        # Mark job completed
        job.status = CoverJob.STATUS_COMPLETED
        job.completed_at = timezone.now()
        job.save()

        logger.info(
            f"Cover generated for collection {collection.slug} "
            f"using {len(game_images)} {image_type} images"
        )

        return {
            "collection_slug": collection.slug,
            "cover_path": str(cover_path),
            "images_used": len(game_images),
            "image_type": image_type,
        }

    except Exception as e:
        job.status = CoverJob.STATUS_FAILED
        job.error = str(e)
        job.completed_at = timezone.now()
        job.save()
        logger.exception(f"Cover generation job {cover_job_id} failed")
        raise


def maybe_generate_cover(collection: "Collection") -> "CoverJob | None":
    """Trigger cover generation for a collection if it has matched games with images.

    This is a non-blocking operation that queues a background task.
    Should be called after importing a collection or adding entries.

    Uses transaction and select_for_update to prevent concurrent job creation.

    Args:
        collection: Collection to generate cover for

    Returns:
        CoverJob if generation was queued, None otherwise
    """
    import uuid

    from django.db import transaction

    from .cover_utils import get_sample_game_images

    # Skip if collection already has a cover
    if collection.has_cover:
        return None

    # Check if collection has any games with images (outside transaction for speed)
    sample_images = get_sample_game_images(collection, limit=1)
    if not sample_images:
        return None

    # Use transaction with select_for_update to prevent race condition
    with transaction.atomic():
        # Re-fetch collection with lock to prevent concurrent modifications
        locked_collection = Collection.objects.select_for_update().get(pk=collection.pk)

        # Double-check after acquiring lock
        if locked_collection.has_cover:
            return None

        # Check for existing pending/running cover job
        existing_job = locked_collection.cover_jobs.filter(
            status__in=[CoverJob.STATUS_PENDING, CoverJob.STATUS_RUNNING]
        ).first()
        if existing_job:
            return None

        # Create cover generation job
        job = CoverJob.objects.create(
            collection=locked_collection,
            task_id=f"pending-{uuid.uuid4().hex}",
            job_type=CoverJob.JOB_TYPE_GENERATE,
            image_type=Collection.COVER_TYPE_COVER,
        )

    # Queue task outside transaction (defer happens after commit)
    from library.queues import PRIORITY_LOW

    job_id = generate_collection_cover.configure(priority=PRIORITY_LOW).defer(
        cover_job_id=job.pk
    )
    job.task_id = str(job_id)
    job.save()

    return job


@app.task(queue=QUEUE_BACKGROUND, queueing_lock="cleanup_old_cover_jobs")
def cleanup_old_cover_jobs(max_age_days: int = 7) -> int:
    """Delete old completed/failed cover jobs.

    This task runs periodically (every 24 hours) to clean up
    old job records from the database. It reschedules itself.

    Args:
        max_age_days: Delete jobs older than this many days

    Returns:
        Number of jobs deleted
    """
    cutoff = timezone.now() - timedelta(days=max_age_days)
    old_jobs = CoverJob.objects.filter(
        status__in=[CoverJob.STATUS_COMPLETED, CoverJob.STATUS_FAILED],
        created_at__lt=cutoff,
    )

    count = old_jobs.count()
    old_jobs.delete()

    if count > 0:
        logger.info(f"Cleaned up {count} old cover jobs")

    # Reschedule to run again in 24 hours (queueing_lock prevents duplicates)
    try:
        cleanup_old_cover_jobs.configure(
            schedule_at=timezone.now() + timedelta(hours=24)
        ).defer(max_age_days=max_age_days)
    except Exception as e:
        logger.warning(f"Failed to reschedule cleanup_old_cover_jobs: {e}")

    return count
