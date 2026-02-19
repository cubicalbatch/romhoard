"""Background tasks using Procrastinate task queue."""

import logging
from datetime import datetime, timedelta
from pathlib import Path

from django.utils import timezone
from procrastinate import RetryStrategy, job_context
from procrastinate.contrib.django import app
from procrastinate.exceptions import JobAborted

from .metadata.screenscraper import ScreenScraperRateLimited
from .models import (
    DownloadJob,
    Game,
    GameImage,
    ImageMigrationJob,
    MetadataBatch,
    MetadataJob,
    ROM,
    ScanJob,
    ScanPath,
    System,
    SystemMetadataJob,
    UploadJob,
)
from .multidownload import create_multi_game_bundle
from .queues import (
    QUEUE_BACKGROUND,
    QUEUE_IDENTIFICATION,
    QUEUE_METADATA,
    QUEUE_USER_ACTIONS,
)
from .scanner import scan_directory

logger = logging.getLogger(__name__)

# Retry strategy for API calls (metadata fetching)
# Exponential backoff: 5s, 10s, 20s
api_retry = RetryStrategy(
    max_attempts=3,
    wait=5,
    exponential_wait=2,
)


def queue_game_metadata(game: Game) -> bool:
    """Queue metadata fetch for a single game immediately.

    This is called during scan/upload when a new game is created or a ROM
    is added to an existing game that lacks metadata.

    Returns True if a job was queued, False if skipped.

    Skips if:
    - ScreenScraper credentials are not configured
    - Game already has screenscraper_id
    - Game already has metadata_updated_at
    - Game has metadata_match_failed=True
    - Game's system has no screenscraper_id (can't query ScreenScraper)
    - A pending/running MetadataJob already exists for this game
    """
    from .metadata.screenscraper import screenscraper_available
    from .queues import PRIORITY_BULK

    # Skip if ScreenScraper credentials are not configured
    if not screenscraper_available():
        logger.debug(f"Skip metadata queue for {game}: no ScreenScraper credentials")
        return False

    # Skip if game already has metadata fetched
    if game.metadata_updated_at:
        logger.debug(f"Skip metadata queue for {game}: already has metadata")
        return False

    # Skip if metadata match already failed (user can force via batch)
    if game.metadata_match_failed:
        logger.debug(f"Skip metadata queue for {game}: previously failed")
        return False

    # Skip if system doesn't have ScreenScraper ID
    if not game.system.screenscraper_id:
        logger.debug(f"Skip metadata queue for {game}: system has no SS ID")
        return False

    # Check for existing pending/running job to avoid duplicates
    existing_job = MetadataJob.objects.filter(
        game=game,
        status__in=[MetadataJob.STATUS_PENDING, MetadataJob.STATUS_RUNNING],
    ).exists()
    if existing_job:
        logger.debug(f"Skip metadata queue for {game}: job already pending")
        return False

    # Create job without a batch (standalone auto-queued job)
    job = MetadataJob.objects.create(
        task_id="pending",
        batch=None,
        game=game,
    )

    # Enqueue the actual metadata fetch task
    # Use PRIORITY_BULK so scans take precedence over auto-queued metadata
    # Note: run_metadata_job_for_game is defined later in this file
    job_id = run_metadata_job_for_game.configure(priority=PRIORITY_BULK).defer(
        metadata_job_id=job.pk
    )
    job.task_id = str(job_id)
    job.save()

    logger.info(f"Auto-queued metadata fetch for {game} (job {job.pk})")
    return True


@app.task(queue=QUEUE_BACKGROUND, pass_context=True)
def run_scan(context: job_context.JobContext, scan_job_id: int) -> dict:
    """Background task to scan a ROM directory."""
    scan_job = ScanJob.objects.get(pk=scan_job_id)
    scan_job.status = ScanJob.STATUS_RUNNING
    scan_job.scan_started_at = timezone.now()
    scan_job.save()

    # Create a callback function that updates ScanJob in the database
    # and checks for abort requests
    def update_progress(data: dict) -> None:
        # Check for cancellation
        if context.should_abort():
            raise JobAborted()

        ScanJob.objects.filter(pk=scan_job_id).update(
            files_processed=data["files_processed"],
            roms_found=data["roms_found"],
            images_found=data["images_found"],
            current_directory=data.get("current_directory", "")[:500],  # Truncate
        )

    try:
        result = scan_directory(
            scan_job.path,
            progress_callback=update_progress,
            use_hasheous=scan_job.use_hasheous,
            fetch_metadata=scan_job.fetch_metadata,
        )

        scan_job.status = ScanJob.STATUS_COMPLETED
        scan_job.added = result.get("added", 0)
        scan_job.skipped = result.get("skipped", 0)
        scan_job.deleted_roms = result.get("deleted_roms", 0)
        scan_job.images_added = result.get("images_added", 0)
        scan_job.images_skipped = result.get("images_skipped", 0)
        scan_job.metadata_queued = result.get("metadata_queued", 0)
        scan_job.errors = result.get("errors", [])
        scan_job.completed_at = timezone.now()
        scan_job.save()

        # Save/update the scanned path for future rescans
        scan_path, _ = ScanPath.objects.get_or_create(path=scan_job.path)
        scan_path.last_scanned = timezone.now()
        scan_path.use_hasheous = scan_job.use_hasheous
        scan_path.fetch_metadata = scan_job.fetch_metadata
        scan_path.save()

        # Queue identification tasks for all added ROMs
        added_rom_ids = result.get("added_rom_ids", [])
        identification_queued = 0
        if added_rom_ids:
            for rom_id in added_rom_ids:
                if queue_rom_identification(rom_id):
                    identification_queued += 1
            logger.info(f"Queued {identification_queued} identification tasks for scan")

        # Auto-fetch system icons if any are missing
        auto_fetch_system_icons.defer()

        return result
    except JobAborted:
        # Task was cancelled - update job status
        scan_job.status = ScanJob.STATUS_CANCELLED
        scan_job.completed_at = timezone.now()
        scan_job.save()
        raise
    except Exception as e:
        scan_job.status = ScanJob.STATUS_FAILED
        scan_job.errors = [str(e)]
        scan_job.completed_at = timezone.now()
        scan_job.save()
        raise


@app.task(queue=QUEUE_USER_ACTIONS)
def create_download_bundle(download_job_id: int) -> dict:
    """Background task to create a multi-game download bundle."""
    job = DownloadJob.objects.get(pk=download_job_id)
    job.status = DownloadJob.STATUS_RUNNING
    job.save()

    def update_progress(progress) -> None:
        DownloadJob.objects.filter(pk=download_job_id).update(
            games_processed=progress.games_processed,
            current_game=progress.current_game[:255],
            bytes_written=progress.bytes_written,
        )

    try:
        # Get games from stored IDs
        games = list(Game.objects.filter(pk__in=job.game_ids))
        job.games_total = len(games)
        job.save()

        # Generate bundle name
        bundle_name = job.system_slug

        # Add device suffix if present
        if job.device_id:
            from devices.models import Device

            device = Device.objects.filter(pk=job.device_id).first()
            if device:
                # Use device slug (already globally unique)
                bundle_name = f"{job.system_slug}_{device.slug}"

        # Add timestamp to filename
        timestamp = datetime.now().strftime("%Y-%m-%d-%H-%M")
        bundle_name = f"{bundle_name}_{timestamp}"

        # Create the bundle
        zip_path, filename = create_multi_game_bundle(
            games=games,
            bundle_name=bundle_name,
            progress_callback=update_progress,
            device_id=job.device_id,
        )

        # Refresh job from database to get updated progress values
        job.refresh_from_db()

        # Update job with results
        job.status = DownloadJob.STATUS_COMPLETED
        job.file_path = zip_path
        job.file_name = filename
        job.file_size = Path(zip_path).stat().st_size
        job.games_included = job.games_processed
        job.games_failed = len(games) - job.games_included
        job.completed_at = timezone.now()
        job.expires_at = timezone.now() + timedelta(hours=1)  # Keep for 1 hour
        job.save()

        return {
            "games_included": job.games_included,
            "file_size": job.file_size,
        }

    except Exception as e:
        job.status = DownloadJob.STATUS_FAILED
        job.errors = [str(e)]
        job.completed_at = timezone.now()
        job.save()
        raise


@app.task(queue=QUEUE_USER_ACTIONS, pass_context=True)
def run_send_upload(context: job_context.JobContext, send_job_id: int) -> dict:
    """Background task to upload games or specific ROMs to device via FTP/SFTP."""
    from devices.models import Device

    from .models import Game, ROM, SendJob
    from .send import SendProgress, send_games_to_device

    job = SendJob.objects.get(pk=send_job_id)
    job.status = SendJob.STATUS_TESTING
    job.started_at = timezone.now()
    job.save()

    def update_progress(progress: SendProgress) -> None:
        """Update job progress in database and check for abortion."""
        if context.should_abort():
            raise JobAborted()

        SendJob.objects.filter(pk=send_job_id).update(
            files_uploaded=progress.files_uploaded,
            files_skipped=progress.files_skipped,
            files_failed=progress.files_failed,
            current_file=progress.current_file[:255],
            bytes_uploaded=progress.bytes_uploaded,
            bytes_total=progress.bytes_total,
        )

    try:
        device = Device.objects.get(pk=job.device_id)

        # Check if we're sending specific ROMs or all ROMs from games
        if job.rom_ids:
            roms = list(
                ROM.objects.filter(pk__in=job.rom_ids).select_related(
                    "rom_set__game", "rom_set__game__system"
                )
            )
            games = []
        else:
            games = list(Game.objects.filter(pk__in=job.game_ids))
            roms = None

        job.status = SendJob.STATUS_RUNNING
        job.save()

        uploaded, skipped, failed, image_results = send_games_to_device(
            games=games,
            device=device,
            progress_callback=update_progress,
            roms=roms,
        )

        job.status = SendJob.STATUS_COMPLETED

        # Store ROM results
        job.uploaded_files = [
            {
                "game_id": r.game_id,
                "filename": r.filename,
                "remote_path": r.remote_path,
                "bytes": r.bytes,
            }
            for r in uploaded
        ]
        job.skipped_files = [
            {
                "game_id": r.game_id,
                "filename": r.filename,
                "remote_path": r.remote_path,
                "reason": "same size",
            }
            for r in skipped
        ]
        job.failed_files = [
            {
                "game_id": r.game_id,
                "filename": r.filename,
                "remote_path": r.remote_path,
                "error": r.error,
            }
            for r in failed
        ]

        # Store image results
        job.uploaded_images = [
            {
                "game_id": r.game_id,
                "rom_filename": r.rom_filename,
                "remote_path": r.remote_path,
                "bytes": r.bytes,
            }
            for r in image_results
            if r.success and not r.skipped
        ]
        job.skipped_images = [
            {
                "game_id": r.game_id,
                "rom_filename": r.rom_filename,
                "remote_path": r.remote_path,
            }
            for r in image_results
            if r.skipped
        ]
        job.failed_images = [
            {
                "game_id": r.game_id,
                "rom_filename": r.rom_filename,
                "remote_path": r.remote_path,
                "error": r.error,
            }
            for r in image_results
            if not r.success and not r.skipped
        ]
        job.files_uploaded = len(uploaded)
        job.files_skipped = len(skipped)
        job.files_failed = len(failed)
        job.completed_at = timezone.now()
        job.save()

        return {
            "uploaded": len(uploaded),
            "skipped": len(skipped),
            "failed": len(failed),
        }

    except Exception as e:
        job.status = SendJob.STATUS_FAILED
        job.error = str(e)
        job.completed_at = timezone.now()
        job.save()
        raise


@app.task(queue=QUEUE_USER_ACTIONS)
def queue_metadata_batch(batch_id: int, force_failed: bool = False) -> dict:
    """Queue metadata jobs for all games in a batch.

    This task runs quickly and creates individual MetadataJob records
    for each game needing metadata, then defers the actual fetch tasks.

    Args:
        batch_id: ID of the MetadataBatch record
        force_failed: If True, also retry games that previously failed to match
    """
    from .metadata.screenscraper import screenscraper_available
    from .queues import PRIORITY_BULK

    batch = MetadataBatch.objects.get(pk=batch_id)

    # Skip if ScreenScraper credentials are not configured
    if not screenscraper_available():
        batch.status = MetadataBatch.STATUS_COMPLETED
        batch.completed_at = timezone.now()
        batch.save()
        return {"jobs_queued": 0, "skipped": "no credentials"}

    # Get games without metadata, filtered by system if specified
    games = Game.objects.filter(metadata_updated_at__isnull=True)
    if batch.system_slug:
        games = games.filter(system__slug=batch.system_slug)

    # By default, skip games that already tried and failed to match
    # Unless force_failed is True, in which case retry everything
    if not force_failed:
        games = games.filter(metadata_match_failed=False)

    job_count = 0
    for game in games:
        job = MetadataJob.objects.create(
            task_id="pending",
            batch=batch,
            game=game,
        )
        # Enqueue the actual metadata fetch task with bulk priority
        # (scans take precedence over batch metadata operations)
        job_id = run_metadata_job_for_game.configure(priority=PRIORITY_BULK).defer(
            metadata_job_id=job.pk
        )
        job.task_id = str(job_id)
        job.save()
        job_count += 1

    # Transition to pending (ready to run) or completed (if nothing to queue)
    if job_count > 0:
        batch.status = MetadataBatch.STATUS_PENDING
    else:
        batch.status = MetadataBatch.STATUS_COMPLETED
        batch.completed_at = timezone.now()
    batch.save()

    return {"jobs_queued": job_count}


@app.task(queue=QUEUE_METADATA, retry=api_retry, pass_context=True)
def run_metadata_job_for_game(
    context: job_context.JobContext, metadata_job_id: int
) -> dict:
    """Background task to fetch metadata for a single game."""
    from .metadata.matcher import (
        apply_metadata_to_game,
        download_images_for_game,
        fetch_metadata_for_game,
    )

    try:
        job = MetadataJob.objects.get(pk=metadata_job_id)
    except MetadataJob.DoesNotExist:
        # Job was deleted (parent game or batch was removed) - nothing to do
        logger.info(f"MetadataJob {metadata_job_id} no longer exists, skipping")
        return {"status": "skipped", "reason": "job_deleted"}

    # Check if already cancelled before starting
    if context.should_abort() or job.status == MetadataJob.STATUS_CANCELLED:
        job.status = MetadataJob.STATUS_CANCELLED
        job.completed_at = timezone.now()
        job.save()
        _check_batch_completion(job.batch)
        raise JobAborted()

    # Mark as running
    job.status = MetadataJob.STATUS_RUNNING
    job.started_at = timezone.now()
    job.save()

    # Update batch started_at if this is the first job
    if job.batch and not job.batch.started_at:
        job.batch.started_at = timezone.now()
        job.batch.status = MetadataBatch.STATUS_RUNNING
        job.batch.save()

    try:
        game = job.game

        # Try to match and fetch metadata
        metadata = fetch_metadata_for_game(game)

        if metadata:
            # Apply metadata to game
            if apply_metadata_to_game(game, metadata):
                # Refresh job from database in case apply_metadata_to_game triggered
                # a merge (which updates job.game in the DB but not in memory)
                job.refresh_from_db()
                game = job.game

                job.matched = True
                # Clear the failed flag if it was set before (e.g., from a force retry)
                if game.metadata_match_failed:
                    game.metadata_match_failed = False
                    game.save(update_fields=["metadata_match_failed"])

                # Download images if media is available
                media_list = metadata.get("media", [])
                if media_list:
                    job.images_downloaded = download_images_for_game(game, media_list)
        else:
            # No match found - mark the game as tried and failed
            game.metadata_match_failed = True
            game.save(update_fields=["metadata_match_failed"])

        job.status = MetadataJob.STATUS_COMPLETED
        job.completed_at = timezone.now()
        job.save()

        return {"matched": job.matched, "images": job.images_downloaded}

    except JobAborted:
        job.status = MetadataJob.STATUS_CANCELLED
        job.completed_at = timezone.now()
        job.save()
        raise
    except ScreenScraperRateLimited as e:
        # Rate limited - reschedule job to run after pause expires
        job.status = MetadataJob.STATUS_PENDING
        job.started_at = None
        job.save()

        delay_seconds = (e.retry_after - timezone.now()).total_seconds()
        delay_seconds = max(60, delay_seconds)  # At least 1 minute

        # Schedule new job for after pause expires
        new_task_id = run_metadata_job_for_game.configure(
            schedule_in={"seconds": int(delay_seconds)}
        ).defer(metadata_job_id=metadata_job_id)

        job.task_id = str(new_task_id)
        job.save()

        logger.info(
            f"Rescheduled metadata job {metadata_job_id} for {e.retry_after} "
            f"(in {int(delay_seconds)}s) due to rate limiting"
        )
        return {"status": "rescheduled", "retry_after": e.retry_after.isoformat()}
    except Exception as e:
        job.status = MetadataJob.STATUS_FAILED
        job.error = str(e)
        job.completed_at = timezone.now()
        job.save()
        raise
    finally:
        # Check if batch is complete
        _check_batch_completion(job.batch)
        _trigger_cover_for_collections_if_done(job.game)


def _check_batch_completion(batch):
    """Check if all jobs in batch are done and update batch status."""
    if not batch:
        return

    pending_or_running = batch.jobs.filter(
        status__in=[MetadataJob.STATUS_PENDING, MetadataJob.STATUS_RUNNING]
    ).exists()

    if not pending_or_running:
        # All jobs done - update batch
        batch.status = MetadataBatch.STATUS_COMPLETED
        batch.completed_at = timezone.now()
        batch.save()


def _trigger_cover_for_collections_if_done(game: Game) -> None:
    """Queue cover generation for collections containing this game, once all
    their pending metadata jobs have finished.

    Called after each metadata job reaches a terminal state. When the last
    pending job for any collection completes, this triggers cover generation
    so the collage can use images from all matched games.
    """
    try:
        from romcollections.models import Collection, CollectionEntry
        from romcollections.tasks import maybe_generate_cover

        # Find all collections that contain this game (by name+system match)
        collection_ids = list(
            CollectionEntry.objects.filter(
                game_name__iexact=game.name,
                system_slug=game.system.slug,
            )
            .values_list("collection_id", flat=True)
            .distinct()
        )

        for col_id in collection_ids:
            # Check if any game in this collection still has a pending/running job
            col_entries = CollectionEntry.objects.filter(collection_id=col_id)
            still_pending = False
            for ce in col_entries:
                matched = Game.objects.filter(
                    name__iexact=ce.game_name, system__slug=ce.system_slug
                ).first()
                if matched and MetadataJob.objects.filter(
                    game=matched,
                    status__in=[MetadataJob.STATUS_PENDING, MetadataJob.STATUS_RUNNING],
                ).exists():
                    still_pending = True
                    break

            if not still_pending:
                col = Collection.objects.get(pk=col_id)
                maybe_generate_cover(col)
    except Exception:
        logger.exception(f"Error checking cover generation for game {game}")


@app.task(queue=QUEUE_USER_ACTIONS)
def run_hash_lookup(game_id: int) -> dict:
    """Background task to identify game via hash lookup.

    Tries Hasheous.
    Only processes games identified by filename or manual entry.
    Updates game name and name_source if a hash match is found.
    """
    from .lookup import lookup_rom

    game = Game.objects.filter(pk=game_id).first()
    if not game:
        return {"matched": False, "error": "Game not found"}

    # Skip if already identified via hash-based source
    hash_sources = {
        Game.SOURCE_HASHEOUS,
        "NoIntros",
        "Redump",
    }
    if game.name_source in hash_sources:
        return {"matched": False, "skipped": "Already hash-based"}

    # Collect all ROMs from all ROMSets
    from .models import ROM

    roms = ROM.objects.filter(rom_set__game=game).select_related("rom_set").all()

    for rom in roms:
        if not rom.crc32 and not rom.sha1:
            continue

        # For archived ROMs, pass archive_path
        # For arcade ROMs (archive_as_rom), pass file_path even though archive_path is empty
        if rom.is_archived:
            file_path = rom.archive_path
        elif game.system.archive_as_rom:
            file_path = rom.file_path
        else:
            file_path = ""

        result = lookup_rom(
            system=game.system,
            crc32=rom.crc32,
            sha1=rom.sha1,
            file_path=file_path,
            use_hasheous=True,
        )

        if result:
            # Update game with matched info
            game.name = result.name
            game.name_source = result.source
            game.save()
            return {
                "matched": True,
                "source": result.source,
                "new_name": result.name,
                "old_name": game.name,
            }

    return {"matched": False, "error": "No hash match found"}


def queue_rom_identification(rom_id: int) -> bool:
    """Queue an identification task for a ROM.

    This is called during scan when a new ROM is created with identify_later=True.
    The identification task will look up the game name via hash and update it.

    Args:
        rom_id: ID of the ROM to identify

    Returns:
        True if task was queued, False otherwise
    """
    from .queues import PRIORITY_NORMAL

    # Defer the identification task
    job_id = identify_rom.configure(priority=PRIORITY_NORMAL).defer(rom_id=rom_id)
    logger.debug(f"Queued identification for ROM {rom_id} (job {job_id})")
    return True


@app.task(queue=QUEUE_IDENTIFICATION, retry=api_retry, pass_context=True)
def identify_rom(context: job_context.JobContext, rom_id: int) -> dict:
    """Background task to identify a ROM via hash lookup and update its game.

    This task performs hash-based lookup for a ROM that was created during
    a scan with identify_later=True. It updates the game name and source
    if a match is found, and optionally queues metadata fetch.

    Args:
        rom_id: ID of the ROM to identify

    Returns:
        Dict with identification results
    """
    from .lookup import lookup_rom

    # Check for cancellation before starting
    if context.should_abort():
        raise JobAborted()

    try:
        rom = ROM.objects.select_related(
            "rom_set", "rom_set__game", "rom_set__game__system"
        ).get(pk=rom_id)
    except ROM.DoesNotExist:
        # ROM was deleted since the scan - this is fine, just skip
        logger.info(f"ROM {rom_id} no longer exists, skipping identification")
        return {"identified": False, "rom_id": rom_id, "reason": "rom_deleted"}

    game = rom.rom_set.game
    system = game.system

    # Skip if already identified via hash-based source
    hash_sources = {
        Game.SOURCE_HASHEOUS,
        "NoIntros",
        "Redump",
    }
    if game.name_source in hash_sources:
        return {
            "identified": False,
            "rom_id": rom_id,
            "reason": "already_hash_based",
            "source": game.name_source,
        }

    # Skip if game already has screenscraper_id (already identified)
    if game.screenscraper_id:
        return {
            "identified": False,
            "rom_id": rom_id,
            "reason": "has_screenscraper_id",
        }

    # Determine which hash and file path to use
    crc32 = rom.crc32
    sha1 = rom.sha1

    # For archived ROMs, pass archive_path; for arcade ROMs, pass file_path
    if rom.is_archived:
        file_path = rom.archive_path
    elif system.archive_as_rom:
        file_path = rom.file_path
    else:
        file_path = ""

    # Check for cancellation before API call
    if context.should_abort():
        raise JobAborted()

    # Perform the lookup
    result = lookup_rom(
        system=system,
        crc32=crc32,
        sha1=sha1,
        file_path=file_path,
        use_hasheous=True,
    )

    if result:
        # Update game with matched info
        old_name = game.name
        game.name = result.name
        game.name_source = result.source
        if result.screenscraper_id:
            game.screenscraper_id = result.screenscraper_id
        game.save()

        logger.info(
            f"Identified ROM {rom_id}: '{old_name}' -> '{game.name}' "
            f"(source: {result.source})"
        )

        # Optionally queue metadata fetch if lookup succeeded
        # and game doesn't already have metadata
        metadata_queued = False
        if not game.metadata_updated_at and not game.metadata_match_failed:
            metadata_queued = queue_game_metadata(game)

        return {
            "identified": True,
            "rom_id": rom_id,
            "game_id": game.pk,
            "old_name": old_name,
            "new_name": game.name,
            "source": result.source,
            "metadata_queued": metadata_queued,
        }

    return {"identified": False, "rom_id": rom_id, "reason": "no_match_found"}


@app.task(queue=QUEUE_METADATA, retry=api_retry, pass_context=True)
def run_system_metadata_fetch(context: job_context.JobContext, job_id: int) -> dict:
    """Background task to fetch system metadata from ScreenScraper."""
    from .metadata.matcher import fetch_system_metadata_for_job

    job = SystemMetadataJob.objects.get(pk=job_id)
    job.status = SystemMetadataJob.STATUS_RUNNING
    job.started_at = timezone.now()
    job.save()

    def update_progress(data: dict) -> None:
        # Check for cancellation
        if context.should_abort():
            raise JobAborted()

        SystemMetadataJob.objects.filter(pk=job_id).update(
            systems_total=data.get("systems_total", 0),
            systems_processed=data.get("systems_processed", 0),
            current_system=data.get("current_system", "")[:100],
            systems_updated=data.get("systems_updated", 0),
            icons_downloaded=data.get("icons_downloaded", 0),
        )

    try:
        result = fetch_system_metadata_for_job(progress_callback=update_progress)

        job.refresh_from_db()
        job.status = SystemMetadataJob.STATUS_COMPLETED
        job.systems_updated = result.get("updated", 0)
        job.systems_skipped = result.get("skipped", 0)
        job.icons_downloaded = result.get("icons_downloaded", 0)
        job.completed_at = timezone.now()
        job.save()

        return result
    except JobAborted:
        job.status = SystemMetadataJob.STATUS_CANCELLED
        job.completed_at = timezone.now()
        job.save()
        raise
    except ScreenScraperRateLimited as e:
        # Rate limited - reschedule job to run after pause expires
        job.status = SystemMetadataJob.STATUS_PENDING
        job.started_at = None
        job.save()

        delay_seconds = (e.retry_after - timezone.now()).total_seconds()
        delay_seconds = max(60, delay_seconds)  # At least 1 minute

        # Schedule new job for after pause expires
        new_task_id = run_system_metadata_fetch.configure(
            schedule_in={"seconds": int(delay_seconds)}
        ).defer(job_id=job_id)

        job.task_id = str(new_task_id)
        job.save()

        logger.info(
            f"Rescheduled system metadata job {job_id} for {e.retry_after} "
            f"(in {int(delay_seconds)}s) due to rate limiting"
        )
        return {"status": "rescheduled", "retry_after": e.retry_after.isoformat()}
    except Exception as e:
        job.status = SystemMetadataJob.STATUS_FAILED
        job.error = str(e)
        job.completed_at = timezone.now()
        job.save()
        raise


@app.task(queue=QUEUE_METADATA, retry=api_retry)
def auto_fetch_system_icons() -> dict:
    """Auto-fetch system icons if any are missing. No job tracking.

    Called automatically after scans and uploads complete.
    Reuses the existing fetch_system_metadata_for_job() which handles
    all the icon downloading logic and skips systems that already have icons.
    """
    from .metadata.matcher import fetch_system_metadata_for_job

    # Quick check: any systems with games but no icon?
    systems_needing_icons = (
        System.objects.exclude(screenscraper_ids=[])
        .filter(
            games__isnull=False,
            icon_path="",
        )
        .distinct()
        .exists()
    )

    if not systems_needing_icons:
        logger.debug("No systems need icons, skipping auto-fetch")
        return {"status": "skipped", "reason": "no_systems_need_icons"}

    logger.info("Auto-fetching system icons")
    result = fetch_system_metadata_for_job()
    return {"status": "completed", **result}


@app.task(queue=QUEUE_BACKGROUND)
def recalculate_all_default_romsets() -> dict:
    """Recalculate default ROMSet for all games after region preference change."""
    from .romset_scoring import recalculate_default_romset

    games = Game.objects.prefetch_related("rom_sets__roms")
    total = games.count()
    changed = 0
    for game in games.iterator(chunk_size=100):
        if recalculate_default_romset(game):
            changed += 1
    logger.info(f"Recalculated default ROMSets: {changed}/{total} changed")
    return {"total": total, "changed": changed}


@app.task(queue=QUEUE_USER_ACTIONS, pass_context=True)
def process_upload_job(context: job_context.JobContext, upload_job_id: int) -> dict:
    """Process uploaded files - detect systems, handle duplicates, create DB records."""
    import os
    import shutil

    from .archive import compute_file_crc32, is_archive_file, list_archive_contents
    from .extensions import get_full_extension, is_archive_extension
    from .parser import parse_rom_filename
    from .scanner import (
        filter_rom_files_in_archive,
        get_or_create_rom_set,
        should_expand_archive,
    )
    from .upload import (
        check_duplicate,
        detect_system_from_extension,
        detect_systems_from_archive,
        ensure_destination_dir,
        get_library_root,
        get_unique_filepath,
        get_upload_temp_dir,
        identify_rom_by_hash,
    )

    job = UploadJob.objects.get(pk=upload_job_id)
    job.status = UploadJob.STATUS_PROCESSING
    job.save()

    library_root = get_library_root()
    temp_dir = os.path.join(get_upload_temp_dir(), str(job.pk))

    if not os.path.exists(temp_dir):
        job.status = UploadJob.STATUS_FAILED
        job.errors = ["Upload temp directory not found"]
        job.completed_at = timezone.now()
        job.save()
        return {"error": "temp directory not found"}

    unidentified = []

    try:
        for filename in os.listdir(temp_dir):
            # Check for cancellation
            if context.should_abort():
                raise JobAborted()

            temp_path = os.path.join(temp_dir, filename)
            if not os.path.isfile(temp_path):
                continue

            job.current_file = filename
            job.save()

            # Detect system from extension
            system = detect_system_from_extension(filename)

            # For archives without exclusive extension, look inside
            if not system and is_archive_file(filename):
                identified_roms = detect_systems_from_archive(temp_path)
                if identified_roms:
                    # Extract and process each ROM individually
                    for path_in_archive, rom_system, crc32 in identified_roms:
                        _process_extracted_rom(
                            temp_path,
                            path_in_archive,
                            rom_system,
                            library_root,
                            job,
                            crc32,
                            fetch_metadata=job.fetch_metadata,
                        )
                    # Remove the original archive after processing
                    os.remove(temp_path)
                    continue
                # If nothing identified in archive, fall through to unidentified

            # For regular files, try Hasheous CRC32 lookup
            if not system:
                system = identify_rom_by_hash(temp_path)

            if not system:
                # Still can't identify - queue for user input
                unidentified.append(
                    {
                        "temp_path": temp_path,
                        "filename": filename,
                    }
                )
                continue

            # Check for duplicate
            if check_duplicate(filename, system):
                job.games_skipped += 1
                os.remove(temp_path)
                job.save()
                continue

            # Handle archives vs regular files
            ext = get_full_extension(filename)
            if is_archive_extension(ext):
                result = _process_uploaded_archive(
                    temp_path,
                    filename,
                    system,
                    library_root,
                    job,
                    fetch_metadata=job.fetch_metadata,
                )
            else:
                result = _process_uploaded_file(
                    temp_path,
                    filename,
                    system,
                    library_root,
                    job,
                    fetch_metadata=job.fetch_metadata,
                )

            if result["success"]:
                job.games_added += result.get("added", 1)
            else:
                job.games_failed += 1
                if result.get("error"):
                    job.errors = job.errors + [result["error"]]

            job.save()

        # Handle unidentified files
        if unidentified:
            job.unidentified_files = unidentified
            job.status = UploadJob.STATUS_AWAITING_INPUT
        else:
            job.status = UploadJob.STATUS_COMPLETED
            # Cleanup temp directory
            shutil.rmtree(temp_dir, ignore_errors=True)

        job.current_file = ""
        job.completed_at = timezone.now()
        job.save()

        # Auto-fetch system icons if any games were added
        if job.games_added > 0:
            auto_fetch_system_icons.defer()

        return {
            "added": job.games_added,
            "skipped": job.games_skipped,
            "failed": job.games_failed,
            "unidentified": len(unidentified),
        }

    except JobAborted:
        job.status = UploadJob.STATUS_CANCELLED
        job.completed_at = timezone.now()
        job.save()
        raise
    except Exception as e:
        logger.exception(f"Upload job {upload_job_id} failed")
        job.status = UploadJob.STATUS_FAILED
        job.errors = job.errors + [str(e)]
        job.completed_at = timezone.now()
        job.save()
        raise


def _process_uploaded_file(
    temp_path: str,
    filename: str,
    system,
    library_root: str,
    job: UploadJob,
    fetch_metadata: bool = True,
) -> dict:
    """Process a single uploaded ROM file."""
    import os
    import shutil

    from .archive import compute_file_crc32
    from .parser import parse_rom_filename
    from .scanner import get_or_create_rom_set
    from .upload import ensure_destination_dir, get_unique_filepath

    try:
        # Ensure destination directory exists
        dest_dir = ensure_destination_dir(system.slug)
        dest_path = get_unique_filepath(dest_dir, filename)

        # Move file to final location
        shutil.move(temp_path, dest_path)

        # Parse filename and compute hash
        parsed = parse_rom_filename(filename)
        file_size = os.path.getsize(dest_path)
        crc32 = compute_file_crc32(dest_path)

        # Create DB records using existing scanner logic
        rom_set, _, _, _ = get_or_create_rom_set(
            name=parsed["name"],
            system=system,
            region=parsed["region"],
            revision=parsed["revision"],
            source_path=os.path.dirname(dest_path),
            crc32=crc32,
            file_path=dest_path,
            use_hasheous=True,
            fetch_metadata=fetch_metadata,
        )

        ROM.objects.create(
            rom_set=rom_set,
            file_path=dest_path,
            file_name=os.path.basename(dest_path),
            file_size=file_size,
            crc32=crc32,
            tags=parsed.get("tags", []),
            rom_number=parsed.get("rom_number", ""),
            disc=parsed.get("disc"),
        )

        # Recalculate default ROMSet now that a ROM exists
        from library.romset_scoring import recalculate_default_romset

        recalculate_default_romset(rom_set.game)

        return {"success": True, "added": 1}

    except Exception as e:
        logger.exception(f"Failed to process uploaded file: {filename}")
        return {"success": False, "error": str(e)}


def _process_uploaded_archive(
    temp_path: str,
    filename: str,
    system,
    library_root: str,
    job: UploadJob,
    fetch_metadata: bool = True,
) -> dict:
    """Process an uploaded archive file.

    If archive contains a single game, keep as-is.
    If archive contains multiple games, extract and process individually.
    """
    import os
    import shutil

    from .archive import compute_file_crc32, list_archive_contents, extract_from_archive
    from .parser import parse_rom_filename
    from .scanner import (
        get_or_create_rom_set,
        should_expand_archive,
        filter_rom_files_in_archive,
    )
    from .upload import ensure_destination_dir, get_unique_filepath, check_duplicate

    try:
        contents = list_archive_contents(temp_path)
        rom_files = filter_rom_files_in_archive(contents, system)

        if not rom_files:
            os.remove(temp_path)
            return {
                "success": False,
                "error": f"No valid ROM files in archive: {filename}",
            }

        # Check if archive contains multiple games
        if should_expand_archive(rom_files):
            # Multiple games - extract and process individually
            return _process_multi_game_archive(
                temp_path,
                filename,
                system,
                library_root,
                job,
                rom_files,
                fetch_metadata,
            )
        else:
            # Single game - keep archive intact
            return _process_single_game_archive(
                temp_path,
                filename,
                system,
                library_root,
                job,
                rom_files,
                fetch_metadata,
            )

    except Exception as e:
        logger.exception(f"Failed to process archive: {filename}")
        return {"success": False, "error": str(e)}


def _process_single_game_archive(
    temp_path: str,
    filename: str,
    system,
    library_root: str,
    job: UploadJob,
    rom_files: list,
    fetch_metadata: bool = True,
) -> dict:
    """Process archive containing a single game - keep archive intact."""
    import os
    import shutil

    from .archive import compute_file_crc32
    from .parser import parse_rom_filename
    from .scanner import get_or_create_rom_set
    from .upload import ensure_destination_dir, get_unique_filepath

    try:
        # Use the first ROM file's info for naming
        first_rom = rom_files[0]

        # Ensure destination directory exists
        dest_dir = ensure_destination_dir(system.slug)
        dest_path = get_unique_filepath(dest_dir, filename)

        # Move archive to final location
        shutil.move(temp_path, dest_path)

        # Parse filename and compute hash of archive
        parsed = parse_rom_filename(first_rom.name)
        file_size = os.path.getsize(dest_path)
        archive_crc = compute_file_crc32(dest_path)

        # Use CRC of first ROM file inside archive for lookup
        rom_crc = first_rom.crc32 if first_rom.crc32 else archive_crc

        # Create DB records
        rom_set, _, _, _ = get_or_create_rom_set(
            name=parsed["name"],
            system=system,
            region=parsed["region"],
            revision=parsed["revision"],
            source_path=dest_path,  # Archive itself is the source
            crc32=rom_crc,
            file_path=dest_path,
            use_hasheous=True,
            fetch_metadata=fetch_metadata,
        )

        # Create ROM entry for archive
        ROM.objects.create(
            rom_set=rom_set,
            file_path=dest_path,
            file_name=os.path.basename(dest_path),
            file_size=file_size,
            crc32=archive_crc,
            tags=parsed.get("tags", []),
            rom_number=parsed.get("rom_number", ""),
            disc=parsed.get("disc"),
        )

        # Recalculate default ROMSet now that a ROM exists
        from library.romset_scoring import recalculate_default_romset

        recalculate_default_romset(rom_set.game)

        return {"success": True, "added": 1}

    except Exception as e:
        logger.exception(f"Failed to process single-game archive: {filename}")
        return {"success": False, "error": str(e)}


def _process_multi_game_archive(
    temp_path: str,
    filename: str,
    system,
    library_root: str,
    job: UploadJob,
    rom_files: list,
    fetch_metadata: bool = True,
) -> dict:
    """Process archive containing multiple games - extract and process individually."""
    import os
    import tempfile
    import shutil

    from .archive import extract_from_archive
    from .upload import check_duplicate

    added = 0
    skipped = 0
    errors = []

    try:
        # Create temp directory for extraction
        extract_dir = tempfile.mkdtemp()

        try:
            for rom_info in rom_files:
                try:
                    # Extract individual file
                    extracted_path = os.path.join(
                        extract_dir, os.path.basename(rom_info.name)
                    )
                    extract_from_archive(temp_path, rom_info.name, extracted_path)

                    # Check for duplicate
                    extracted_filename = os.path.basename(rom_info.name)
                    if check_duplicate(extracted_filename, system):
                        os.remove(extracted_path)
                        skipped += 1
                        continue

                    # Process as regular file
                    result = _process_uploaded_file(
                        extracted_path,
                        extracted_filename,
                        system,
                        library_root,
                        job,
                        fetch_metadata=fetch_metadata,
                    )

                    if result["success"]:
                        added += result.get("added", 1)
                    else:
                        errors.append(result.get("error", "Unknown error"))

                except Exception as e:
                    errors.append(f"Failed to extract {rom_info.name}: {str(e)}")

        finally:
            # Clean up extraction directory
            shutil.rmtree(extract_dir, ignore_errors=True)

        # Remove original archive
        os.remove(temp_path)

        if errors:
            return {
                "success": added > 0,
                "added": added,
                "error": "; ".join(errors[:3]),
            }
        return {"success": True, "added": added}

    except Exception as e:
        logger.exception(f"Failed to process multi-game archive: {filename}")
        return {"success": False, "error": str(e)}


def _process_extracted_rom(
    archive_path: str,
    path_in_archive: str,
    system,
    library_root: str,
    job: UploadJob,
    crc32: str = "",
    fetch_metadata: bool = True,
) -> dict:
    """Process a ROM extracted from an archive with known system.

    This is used when we've identified the system for files inside an archive
    (via extension or hash lookup) and need to extract and process each file.

    Args:
        archive_path: Path to the archive file
        path_in_archive: Path of the ROM inside the archive
        system: The identified System object
        library_root: Library root path
        job: The UploadJob for tracking progress
        crc32: Pre-computed CRC32 from archive headers (optional)
        fetch_metadata: Auto-queue ScreenScraper metadata for new games

    Returns:
        Dict with success status and added count or error message.
    """
    import os
    import shutil
    import tempfile

    from .archive import extract_file_from_archive
    from .parser import parse_rom_filename
    from .scanner import get_or_create_rom_set
    from .upload import check_duplicate, ensure_destination_dir, get_unique_filepath

    try:
        filename = os.path.basename(path_in_archive)

        # Check for duplicate before extracting
        if check_duplicate(filename, system):
            job.games_skipped += 1
            job.save()
            return {"success": True, "added": 0, "skipped": 1}

        # Extract to temp file
        with tempfile.NamedTemporaryFile(delete=False, suffix=filename) as tmp:
            temp_path = tmp.name

        extract_file_from_archive(archive_path, path_in_archive, temp_path)

        # Ensure destination directory exists
        dest_dir = ensure_destination_dir(system.slug)
        dest_path = get_unique_filepath(dest_dir, filename)

        # Move to final location
        shutil.move(temp_path, dest_path)

        # Parse filename
        parsed = parse_rom_filename(filename)
        file_size = os.path.getsize(dest_path)

        # Use pre-computed CRC32 if available, otherwise compute
        if not crc32:
            from .archive import compute_file_crc32

            crc32 = compute_file_crc32(dest_path)

        # Create DB records
        rom_set, _, _, _ = get_or_create_rom_set(
            name=parsed["name"],
            system=system,
            region=parsed["region"],
            revision=parsed["revision"],
            source_path=os.path.dirname(dest_path),
            crc32=crc32,
            file_path=dest_path,
            use_hasheous=True,
            fetch_metadata=fetch_metadata,
        )

        ROM.objects.create(
            rom_set=rom_set,
            file_path=dest_path,
            file_name=os.path.basename(dest_path),
            file_size=file_size,
            crc32=crc32,
            tags=parsed.get("tags", []),
            rom_number=parsed.get("rom_number", ""),
            disc=parsed.get("disc"),
        )

        # Recalculate default ROMSet now that a ROM exists
        from library.romset_scoring import recalculate_default_romset

        recalculate_default_romset(rom_set.game)

        job.games_added += 1
        job.save()

        return {"success": True, "added": 1}

    except Exception as e:
        logger.exception(f"Failed to extract ROM from archive: {path_in_archive}")
        job.games_failed += 1
        job.save()
        return {"success": False, "error": str(e)}


@app.periodic(cron="*/10 * * * *")
@app.task(queue=QUEUE_BACKGROUND, queueing_lock="scan_scheduler")
def check_scheduled_scans(timestamp) -> dict:
    """Check for paths due for scheduled scanning and trigger scans.

    Runs every 10 minutes. Checks all scheduled paths and queues scans for those that are due.
    Uses a queueing lock to prevent multiple instances from running simultaneously.
    """
    from .queues import PRIORITY_LOW

    scheduled_paths = ScanPath.objects.filter(schedule_enabled=True)

    scans_triggered = 0
    for scan_path in scheduled_paths:
        if not scan_path.is_due_for_scan():
            continue

        # Check if there's already an active scan for this path
        active_scan = ScanJob.objects.filter(
            path=scan_path.path,
            status__in=[ScanJob.STATUS_PENDING, ScanJob.STATUS_RUNNING],
        ).exists()

        if active_scan:
            logger.debug(
                f"Skipping scheduled scan for {scan_path.path}: scan already active"
            )
            continue

        # Create ScanJob and enqueue task
        scan_job = ScanJob.objects.create(
            path=scan_path.path,
            use_hasheous=scan_path.use_hasheous,
            fetch_metadata=scan_path.fetch_metadata,
            task_id="pending",
        )

        # Use same priority as user-initiated scans
        job_id = run_scan.configure(priority=PRIORITY_LOW).defer(
            scan_job_id=scan_job.pk
        )
        scan_job.task_id = str(job_id)
        scan_job.save()

        logger.info(f"Triggered scheduled scan for {scan_path.path}")
        scans_triggered += 1

    return {"scans_triggered": scans_triggered}


@app.task(queue=QUEUE_BACKGROUND, pass_context=True)
def run_image_migration(context: job_context.JobContext, migration_job_id: int) -> dict:
    """Background task to migrate (move/delete) downloaded images.

    Handles three actions:
    - move: Move images from old path to new path, update DB records
    - delete: Delete images from disk, clear DB records
    - orphan: Just clear DB records, leave files on disk
    """
    from .image_utils import (
        delete_downloaded_image,
        move_downloaded_image,
    )

    job = ImageMigrationJob.objects.get(pk=migration_job_id)
    job.status = ImageMigrationJob.STATUS_RUNNING
    job.started_at = timezone.now()
    job.save()

    try:
        action = job.action
        old_path = job.old_path
        new_path = job.new_path

        # Get all downloaded images
        downloaded_images = list(
            GameImage.objects.filter(source="downloaded").values_list("pk", "file_path")
        )

        # Get system icons
        systems_with_icons = list(
            System.objects.exclude(icon_path="").values_list("pk", "icon_path")
        )

        job.total_images = len(downloaded_images) + len(systems_with_icons)
        job.save()

        processed = 0
        skipped = 0
        failed = 0
        errors = []

        if action == ImageMigrationJob.ACTION_MOVE:
            # Move game images
            for image_pk, file_path in downloaded_images:
                if context.should_abort():
                    raise JobAborted()

                new_file_path, error = move_downloaded_image(
                    file_path, old_path, new_path
                )

                if error == "skipped":
                    skipped += 1
                elif error:
                    failed += 1
                    errors.append(f"{Path(file_path).name}: {error}")
                else:
                    # Update DB record with new path
                    GameImage.objects.filter(pk=image_pk).update(
                        file_path=str(new_file_path)
                    )

                processed += 1
                if processed % 50 == 0:
                    ImageMigrationJob.objects.filter(pk=migration_job_id).update(
                        processed_images=processed,
                        skipped_images=skipped,
                        failed_images=failed,
                    )

            # Move system icons
            for system_pk, icon_path in systems_with_icons:
                if context.should_abort():
                    raise JobAborted()

                new_icon_path, error = move_downloaded_image(
                    icon_path, old_path, new_path
                )

                if error == "skipped":
                    skipped += 1
                elif error:
                    failed += 1
                    errors.append(f"System icon {Path(icon_path).name}: {error}")
                else:
                    # Update system record with new path
                    System.objects.filter(pk=system_pk).update(
                        icon_path=str(new_icon_path)
                    )

                processed += 1

        elif action == ImageMigrationJob.ACTION_DELETE:
            # Delete game images from disk
            for image_pk, file_path in downloaded_images:
                if context.should_abort():
                    raise JobAborted()

                error = delete_downloaded_image(file_path)
                if error:
                    failed += 1
                    errors.append(f"{Path(file_path).name}: {error}")

                processed += 1
                if processed % 50 == 0:
                    ImageMigrationJob.objects.filter(pk=migration_job_id).update(
                        processed_images=processed,
                        failed_images=failed,
                    )

            # Delete system icons from disk
            for system_pk, icon_path in systems_with_icons:
                if context.should_abort():
                    raise JobAborted()

                error = delete_downloaded_image(icon_path)
                if error:
                    failed += 1
                    errors.append(f"System icon {Path(icon_path).name}: {error}")

                processed += 1

            # Now clear DB records
            # Get game IDs BEFORE deleting images
            game_ids_with_downloads = list(
                GameImage.objects.filter(source="downloaded")
                .values_list("game_id", flat=True)
                .distinct()
            )
            GameImage.objects.filter(source="downloaded").delete()
            Game.objects.filter(pk__in=game_ids_with_downloads).update(
                metadata_updated_at=None
            )
            System.objects.update(icon_path="", metadata_updated_at=None)

        elif action == ImageMigrationJob.ACTION_ORPHAN:
            # Just clear DB records, leave files on disk
            # Get game IDs BEFORE deleting images
            game_ids_with_downloads = list(
                GameImage.objects.filter(source="downloaded")
                .values_list("game_id", flat=True)
                .distinct()
            )
            GameImage.objects.filter(source="downloaded").delete()
            Game.objects.filter(pk__in=game_ids_with_downloads).update(
                metadata_updated_at=None
            )
            System.objects.update(icon_path="", metadata_updated_at=None)
            processed = job.total_images  # Mark all as processed

        # Update final status
        job.processed_images = processed
        job.skipped_images = skipped
        job.failed_images = failed
        job.errors = errors[:50]  # Limit stored errors
        job.status = ImageMigrationJob.STATUS_COMPLETED
        job.completed_at = timezone.now()
        job.save()

        return {
            "action": action,
            "processed": processed,
            "skipped": skipped,
            "failed": failed,
        }

    except JobAborted:
        job.status = ImageMigrationJob.STATUS_FAILED
        job.error_message = "Job was cancelled"
        job.completed_at = timezone.now()
        job.save()
        raise
    except Exception as e:
        logger.exception(f"Image migration job {migration_job_id} failed")
        job.status = ImageMigrationJob.STATUS_FAILED
        job.error_message = str(e)
        job.completed_at = timezone.now()
        job.save()
        raise
