"""Metadata management views."""

import os

from django.contrib import messages
from django.db.models import Count
from django.http import HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.views.decorators.http import require_POST
from procrastinate.contrib.django import app as procrastinate_app

from ..models import (
    Game,
    ImageMigrationJob,
    MetadataBatch,
    MetadataJob,
    Setting,
    System,
    SystemMetadataJob,
)


def metadata_page(request):
    """Metadata management page with settings and job history."""
    from ..image_utils import get_image_storage_path, validate_metadata_path
    from ..metadata.screenscraper import (
        CREDENTIALS_VALID_KEY,
        get_credentials_valid,
        get_pause_until,
        screenscraper_available,
        set_credentials_valid,
        validate_credentials,
    )

    # Clear collection context when viewing metadata page
    request.session.pop("from_collection", None)

    # Handle ScreenScraper credentials form submission
    if request.method == "POST" and "save_screenscraper_credentials" in request.POST:
        username = request.POST.get("screenscraper_username", "").strip()
        password = request.POST.get("screenscraper_password", "").strip()

        if username and password:
            Setting.set("screenscraper_username", username)
            Setting.set("screenscraper_password", password)

            # Validate credentials immediately
            is_valid, error = validate_credentials()
            set_credentials_valid(is_valid)

            if not is_valid:
                messages.error(
                    request, f"ScreenScraper credentials appear invalid: {error}"
                )
            else:
                messages.success(
                    request, "ScreenScraper credentials saved and verified."
                )

        elif not username and not password:
            # Clear DB credentials + validation status if both are empty (allows fallback to env)
            Setting.objects.filter(
                key__in=[
                    "screenscraper_username",
                    "screenscraper_password",
                    CREDENTIALS_VALID_KEY,
                ]
            ).delete()

        return redirect("library:metadata")

    # Get ScreenScraper credentials (DB first, then env fallback)
    screenscraper_username = Setting.get("screenscraper_username") or os.environ.get(
        "SCREENSCRAPER_USER", ""
    )
    screenscraper_configured = screenscraper_available()
    # Check if credentials are from DB (to show appropriate UI)
    screenscraper_from_db = bool(Setting.get("screenscraper_username"))
    # Get validation status for showing in UI
    screenscraper_credentials_valid = (
        get_credentials_valid() if screenscraper_configured else None
    )

    # Get current settings
    from ..image_utils import get_image_storage_path

    # Get raw DB setting value for form and comparison
    try:
        image_path_setting = Setting.objects.get(key="metadata_image_path")
        image_path = image_path_setting.value
    except Setting.DoesNotExist:
        image_path = ""

    # Get effective path (with fallbacks) for display and validation
    effective_metadata_path = str(get_image_storage_path())
    metadata_path_valid, metadata_path_status = validate_metadata_path(
        effective_metadata_path
    )

    try:
        library_root_setting = Setting.objects.get(key="library_root")
        library_root = library_root_setting.value
    except Setting.DoesNotExist:
        library_root = ""

    # Handle Image Storage Path form submission
    if request.method == "POST" and "save_image_settings" in request.POST:
        from ..image_utils import get_downloaded_images_stats
        from ..tasks import run_image_migration

        new_image_path = request.POST.get("image_path", "").strip()

        # Check if image storage path has changed
        if new_image_path != image_path:
            # Path changed - check if there are any downloaded images to handle
            stats = get_downloaded_images_stats()

            if stats["count"] > 0:
                # Has existing images - check for user action
                image_action = request.POST.get("image_action", "")

                if not image_action:
                    # First submit - show confirmation modal
                    # Build context and render with modal open
                    # Pass new_image_path as image_path so the form submits the new value on confirm
                    return _render_metadata_page_with_modal(
                        request,
                        image_path=new_image_path,
                        library_root=library_root,
                        screenscraper_username=screenscraper_username,
                        screenscraper_configured=screenscraper_configured,
                        screenscraper_from_db=screenscraper_from_db,
                        show_migration_modal=True,
                        migration_stats=stats,
                        migration_old_path=image_path,
                        migration_new_path=new_image_path,
                    )

                if image_action == "cancel":
                    # User cancelled - don't change anything
                    return redirect("library:metadata")

                # User confirmed an action - create migration job
                job = ImageMigrationJob.objects.create(
                    action=image_action,
                    old_path=image_path,
                    new_path=new_image_path,
                    total_images=stats["count"],
                )

                # Update setting immediately
                Setting.objects.update_or_create(
                    key="metadata_image_path", defaults={"value": new_image_path}
                )

                # Queue background task
                task_id = run_image_migration.defer(migration_job_id=job.pk)
                job.task_id = str(task_id)
                job.save()

                return redirect("library:metadata")
            else:
                # No existing images - just save the new path
                Setting.objects.update_or_create(
                    key="metadata_image_path", defaults={"value": new_image_path}
                )
        else:
            # Path unchanged - just save (in case form submitted without changes)
            Setting.objects.update_or_create(
                key="metadata_image_path", defaults={"value": new_image_path}
            )

        return redirect("library:metadata")

    # Handle Library settings form submission
    if request.method == "POST" and "save_library_settings" in request.POST:
        new_library_root = request.POST.get("library_root", "").strip()

        Setting.objects.update_or_create(
            key="library_root", defaults={"value": new_library_root}
        )

        return redirect("library:metadata")

    # Get active batches (pending or running)
    active_batches = MetadataBatch.objects.filter(
        status__in=[MetadataBatch.STATUS_PENDING, MetadataBatch.STATUS_RUNNING]
    )

    # Track which systems have active batches (for button state)
    active_batch_slugs = set(active_batches.values_list("system_slug", flat=True))
    fetching_all = "" in active_batch_slugs  # Empty slug = "all systems" batch

    # Get recent completed batches
    recent_batches = MetadataBatch.objects.filter(
        status__in=[MetadataBatch.STATUS_COMPLETED, MetadataBatch.STATUS_CANCELLED]
    )[:5]

    # Check if ScreenScraper is paused due to rate limiting
    pause_until = get_pause_until()

    total_games = Game.objects.count()
    games_with_metadata = Game.objects.filter(metadata_updated_at__isnull=False).count()
    # Games that we tried to match but failed (red state)
    games_match_failed = Game.objects.filter(
        metadata_updated_at__isnull=True, metadata_match_failed=True
    ).count()
    # Games we haven't tried yet (white state)
    games_not_tried = Game.objects.filter(
        metadata_updated_at__isnull=True, metadata_match_failed=False
    ).count()

    # Calculate coverage percentages for the three-state bar
    if total_games > 0:
        coverage_percentage = int((games_with_metadata / total_games * 100))
        failed_percentage = int((games_match_failed / total_games * 100))
        # Ensure no visual gap when all games have been tried
        if games_not_tried == 0 and coverage_percentage + failed_percentage < 100:
            failed_percentage = 100 - coverage_percentage
    else:
        coverage_percentage = 100
        failed_percentage = 0

    # Build per-system stats with coverage info
    systems = (
        System.objects.exclude(screenscraper_ids=[])
        .annotate(game_count=Count("games"))
        .filter(game_count__gt=0)
        .order_by("name")
    )

    systems_with_stats = []
    for system in systems:
        total = Game.objects.filter(system=system).count()
        with_metadata = Game.objects.filter(
            system=system, metadata_updated_at__isnull=False
        ).count()
        match_failed = Game.objects.filter(
            system=system, metadata_updated_at__isnull=True, metadata_match_failed=True
        ).count()
        not_tried = Game.objects.filter(
            system=system, metadata_updated_at__isnull=True, metadata_match_failed=False
        ).count()

        # Calculate percentages for three-state bar
        if total > 0:
            percentage = int((with_metadata / total * 100))
            failed_pct = int((match_failed / total * 100))
        else:
            percentage = 100
            failed_pct = 0

        systems_with_stats.append(
            {
                "system": system,
                "total": total,
                "with_metadata": with_metadata,
                "match_failed": match_failed,
                "not_tried": not_tried,
                "percentage": percentage,
                "failed_percentage": failed_pct,
                "is_complete": not_tried == 0 and match_failed == 0,
                "has_active_batch": system.slug in active_batch_slugs,
            }
        )

    # Sort: incomplete systems first, then by not_tried count descending
    systems_with_stats.sort(
        key=lambda x: (x["is_complete"], -(x["not_tried"] + x["match_failed"]))
    )

    # Get active image migration job (if any)
    active_migration_job = ImageMigrationJob.objects.filter(
        status__in=[ImageMigrationJob.STATUS_PENDING, ImageMigrationJob.STATUS_RUNNING]
    ).first()

    # Get recent completed migration job for showing results
    recent_migration_job = ImageMigrationJob.objects.filter(
        status__in=[ImageMigrationJob.STATUS_COMPLETED, ImageMigrationJob.STATUS_FAILED]
    ).first()

    context = {
        "screenscraper_username": screenscraper_username,
        "screenscraper_configured": screenscraper_configured,
        "screenscraper_from_db": screenscraper_from_db,
        "screenscraper_credentials_valid": screenscraper_credentials_valid,
        "image_path": image_path,
        "effective_metadata_path": effective_metadata_path,
        "metadata_path_valid": metadata_path_valid,
        "metadata_path_status": metadata_path_status,
        "library_root": library_root,
        "total_games": total_games,
        "games_with_metadata": games_with_metadata,
        "games_match_failed": games_match_failed,
        "games_not_tried": games_not_tried,
        "coverage_percentage": coverage_percentage,
        "failed_percentage": failed_percentage,
        "systems_with_stats": systems_with_stats,
        "active_batches": active_batches,
        "recent_batches": recent_batches,
        "pause_until": pause_until,
        "fetching_all": fetching_all,
        "active_migration_job": active_migration_job,
        "recent_migration_job": recent_migration_job,
    }
    return render(request, "library/metadata.html", context)


def _render_metadata_page_with_modal(
    request,
    image_path,
    library_root,
    screenscraper_username,
    screenscraper_configured,
    screenscraper_from_db=False,
    show_migration_modal=False,
    migration_stats=None,
    migration_old_path="",
    migration_new_path="",
):
    """Helper to render metadata page with migration modal open.

    This builds the full context needed for the metadata page and adds
    the modal-specific context variables.
    """
    from ..image_utils import get_image_storage_path, validate_metadata_path
    from ..metadata.screenscraper import get_pause_until

    # Validate metadata path - use shared function for effective path
    effective_metadata_path = str(get_image_storage_path())
    metadata_path_valid, metadata_path_status = validate_metadata_path(
        effective_metadata_path
    )

    # Get active batches
    active_batches = MetadataBatch.objects.filter(
        status__in=[MetadataBatch.STATUS_PENDING, MetadataBatch.STATUS_RUNNING]
    )
    active_batch_slugs = set(active_batches.values_list("system_slug", flat=True))
    fetching_all = "" in active_batch_slugs

    # Get recent completed batches
    recent_batches = MetadataBatch.objects.filter(
        status__in=[MetadataBatch.STATUS_COMPLETED, MetadataBatch.STATUS_CANCELLED]
    )[:5]

    pause_until = get_pause_until()

    total_games = Game.objects.count()
    games_with_metadata = Game.objects.filter(metadata_updated_at__isnull=False).count()
    games_match_failed = Game.objects.filter(
        metadata_updated_at__isnull=True, metadata_match_failed=True
    ).count()
    games_not_tried = Game.objects.filter(
        metadata_updated_at__isnull=True, metadata_match_failed=False
    ).count()

    if total_games > 0:
        coverage_percentage = int((games_with_metadata / total_games * 100))
        failed_percentage = int((games_match_failed / total_games * 100))
    else:
        coverage_percentage = 100
        failed_percentage = 0

    # Build per-system stats
    systems = (
        System.objects.exclude(screenscraper_ids=[])
        .annotate(game_count=Count("games"))
        .filter(game_count__gt=0)
        .order_by("name")
    )

    systems_with_stats = []
    for system in systems:
        total = Game.objects.filter(system=system).count()
        with_metadata = Game.objects.filter(
            system=system, metadata_updated_at__isnull=False
        ).count()
        match_failed = Game.objects.filter(
            system=system, metadata_updated_at__isnull=True, metadata_match_failed=True
        ).count()
        not_tried = Game.objects.filter(
            system=system, metadata_updated_at__isnull=True, metadata_match_failed=False
        ).count()

        if total > 0:
            percentage = int((with_metadata / total * 100))
            failed_pct = int((match_failed / total * 100))
        else:
            percentage = 100
            failed_pct = 0

        systems_with_stats.append(
            {
                "system": system,
                "total": total,
                "with_metadata": with_metadata,
                "match_failed": match_failed,
                "not_tried": not_tried,
                "percentage": percentage,
                "failed_percentage": failed_pct,
                "is_complete": not_tried == 0 and match_failed == 0,
                "has_active_batch": system.slug in active_batch_slugs,
            }
        )

    systems_with_stats.sort(
        key=lambda x: (x["is_complete"], -(x["not_tried"] + x["match_failed"]))
    )

    context = {
        "screenscraper_username": screenscraper_username,
        "screenscraper_configured": screenscraper_configured,
        "screenscraper_from_db": screenscraper_from_db,
        "image_path": image_path,
        "effective_metadata_path": effective_metadata_path,
        "metadata_path_valid": metadata_path_valid,
        "metadata_path_status": metadata_path_status,
        "library_root": library_root,
        "total_games": total_games,
        "games_with_metadata": games_with_metadata,
        "games_match_failed": games_match_failed,
        "games_not_tried": games_not_tried,
        "coverage_percentage": coverage_percentage,
        "failed_percentage": failed_percentage,
        "systems_with_stats": systems_with_stats,
        "active_batches": active_batches,
        "recent_batches": recent_batches,
        "pause_until": pause_until,
        "fetching_all": fetching_all,
        # Modal-specific context
        "show_migration_modal": show_migration_modal,
        "migration_stats": migration_stats,
        "migration_old_path": migration_old_path,
        "migration_new_path": migration_new_path,
    }
    return render(request, "library/metadata.html", context)


def image_migration_status(request):
    """HTMX endpoint to poll image migration job status."""
    active_job = ImageMigrationJob.objects.filter(
        status__in=[ImageMigrationJob.STATUS_PENDING, ImageMigrationJob.STATUS_RUNNING]
    ).first()

    recent_job = None
    if not active_job:
        recent_job = ImageMigrationJob.objects.filter(
            status__in=[
                ImageMigrationJob.STATUS_COMPLETED,
                ImageMigrationJob.STATUS_FAILED,
            ]
        ).first()

    return render(
        request,
        "library/_image_migration_status.html",
        {
            "active_migration_job": active_job,
            "recent_migration_job": recent_job,
        },
    )


@require_POST
def start_metadata_job(request):
    """Start metadata fetch jobs for games without metadata."""
    from ..queues import PRIORITY_HIGH
    from ..tasks import queue_metadata_batch

    system_slug = request.POST.get("system_slug", "").strip()
    force_failed = request.POST.get("force_failed") == "1"

    # Create batch in queuing state - the task will populate jobs
    batch = MetadataBatch.objects.create(
        system_slug=system_slug,
        status=MetadataBatch.STATUS_QUEUING,
    )

    # Defer the queuing work to background (runs fast, high priority)
    queue_metadata_batch.configure(priority=PRIORITY_HIGH).defer(
        batch_id=batch.pk, force_failed=force_failed
    )

    return redirect("library:metadata")


def metadata_status(request):
    """HTMX endpoint to poll metadata batch status.

    Query parameters:
        type: 'active', 'recent', or omit for both (backward compatibility)
    """
    status_type = request.GET.get("type")

    active_batches = MetadataBatch.objects.filter(
        status__in=[
            MetadataBatch.STATUS_QUEUING,
            MetadataBatch.STATUS_PENDING,
            MetadataBatch.STATUS_RUNNING,
        ]
    )
    recent_batches = MetadataBatch.objects.filter(
        status__in=[MetadataBatch.STATUS_COMPLETED, MetadataBatch.STATUS_CANCELLED]
    )[:5]

    if status_type == "active":
        context = {"active_batches": active_batches}
        return render(request, "library/_active_batches_status.html", context)
    elif status_type == "recent":
        context = {"recent_batches": recent_batches}
        return render(request, "library/_recent_batches_status.html", context)

    # Default: return both (backward compatibility)
    context = {
        "active_batches": active_batches,
        "recent_batches": recent_batches,
    }
    return render(request, "library/_metadata_status.html", context)


@require_POST
def clear_screenscraper_pause(request):
    """Clear the ScreenScraper rate limit pause to resume API calls early."""
    from ..metadata.screenscraper import clear_pause

    clear_pause()
    messages.success(request, "ScreenScraper API calls resumed.")
    return redirect("library:metadata")


@require_POST
def revalidate_screenscraper(request):
    """Re-validate ScreenScraper credentials."""
    from ..metadata.screenscraper import (
        screenscraper_available,
        set_credentials_valid,
        validate_credentials,
    )

    if not screenscraper_available():
        messages.warning(request, "No ScreenScraper credentials configured.")
        return redirect("library:metadata")

    is_valid, error = validate_credentials()
    set_credentials_valid(is_valid)

    if is_valid:
        messages.success(request, "ScreenScraper credentials verified successfully.")
    else:
        messages.error(request, f"ScreenScraper credentials invalid: {error}")

    return redirect("library:metadata")


@require_POST
def cancel_metadata_batch(request, batch_id):
    """Cancel all pending jobs in a metadata batch."""
    batch = get_object_or_404(MetadataBatch, pk=batch_id)

    # Cancel pending jobs in Procrastinate and mark in Django
    pending_jobs = batch.jobs.filter(status=MetadataJob.STATUS_PENDING)
    for job in pending_jobs:
        try:
            procrastinate_app.job_manager.cancel_job_by_id(int(job.task_id), abort=True)
        except (ValueError, Exception):
            pass  # Job may have already started or finished

    # Mark all pending jobs as cancelled in Django
    pending_jobs.update(
        status=MetadataJob.STATUS_CANCELLED,
        completed_at=timezone.now(),
    )

    # Also try to abort any running jobs
    running_jobs = batch.jobs.filter(status=MetadataJob.STATUS_RUNNING)
    for job in running_jobs:
        try:
            procrastinate_app.job_manager.cancel_job_by_id(int(job.task_id), abort=True)
        except (ValueError, Exception):
            pass  # Job may have already finished

    # Update batch status
    batch.status = MetadataBatch.STATUS_CANCELLED
    batch.completed_at = timezone.now()
    batch.save()

    return redirect("library:metadata")


@require_POST
def fetch_system_metadata(request):
    """Start background system metadata fetch job."""
    from ..queues import PRIORITY_NORMAL
    from ..tasks import run_system_metadata_fetch

    # Create job
    job = SystemMetadataJob.objects.create(task_id="pending")

    # Enqueue task
    job_id = run_system_metadata_fetch.configure(priority=PRIORITY_NORMAL).defer(
        job_id=job.pk
    )
    job.task_id = str(job_id)
    job.save()

    return redirect("library:metadata")


def system_metadata_status(request):
    """HTMX endpoint to poll system metadata job status."""
    active_jobs = SystemMetadataJob.objects.filter(
        status__in=[SystemMetadataJob.STATUS_PENDING, SystemMetadataJob.STATUS_RUNNING]
    )
    recent_jobs = SystemMetadataJob.objects.filter(
        status__in=[
            SystemMetadataJob.STATUS_COMPLETED,
            SystemMetadataJob.STATUS_FAILED,
            SystemMetadataJob.STATUS_CANCELLED,
        ]
    ).order_by("-completed_at")[:3]

    context = {"active_jobs": active_jobs, "recent_jobs": recent_jobs}
    return render(request, "library/_system_metadata_status.html", context)


@require_POST
def cancel_system_metadata_job(request, job_id):
    """Cancel a system metadata fetch job."""
    job = get_object_or_404(SystemMetadataJob, pk=job_id)

    if job.status in [
        SystemMetadataJob.STATUS_PENDING,
        SystemMetadataJob.STATUS_RUNNING,
    ]:
        # Cancel in Procrastinate
        try:
            procrastinate_app.job_manager.cancel_job_by_id(int(job.task_id), abort=True)
        except (ValueError, Exception):
            pass  # Job may have already finished

        # Update Django model
        job.status = SystemMetadataJob.STATUS_CANCELLED
        job.completed_at = timezone.now()
        job.save()

    return redirect("library:metadata")


@require_POST
def fetch_game_metadata(request, pk):
    """Fetch metadata for a single game."""
    from ..queues import PRIORITY_HIGH
    from ..tasks import run_metadata_job_for_game

    game = get_object_or_404(Game, pk=pk)

    # Create job for single game (no batch)
    job = MetadataJob.objects.create(task_id="pending", game=game)

    # Enqueue task with high priority (user is viewing this game)
    job_id = run_metadata_job_for_game.configure(priority=PRIORITY_HIGH).defer(
        metadata_job_id=job.pk
    )
    job.task_id = str(job_id)
    job.save()

    # Return partial for HTMX requests
    if request.headers.get("HX-Request"):
        return render(
            request, "library/_game_fetch_status.html", {"game": game, "job": job}
        )

    # Redirect to game detail page for regular form submissions
    return redirect("library:game_detail", pk=pk)


def games_missing_metadata(request, system_slug):
    """HTMX endpoint to load games missing metadata for a system."""
    system = get_object_or_404(System, slug=system_slug)
    games = Game.objects.filter(
        system=system, metadata_updated_at__isnull=True
    ).order_by("name")[:20]
    total_missing = Game.objects.filter(
        system=system, metadata_updated_at__isnull=True
    ).count()
    return render(
        request,
        "library/_games_missing_list.html",
        {
            "games": games,
            "system": system,
            "total_missing": total_missing,
            "showing": min(20, total_missing),
        },
    )


def hash_lookup(request, pk):
    """Trigger hash-based ROM identification for a single game."""
    from ..queues import PRIORITY_HIGH
    from ..tasks import run_hash_lookup

    game = get_object_or_404(Game, pk=pk)

    # Enqueue task with high priority (user is viewing this game)
    run_hash_lookup.configure(priority=PRIORITY_HIGH).defer(game_id=game.pk)

    # Redirect to game detail page
    return redirect("library:game_detail", pk=pk)


@require_POST
def set_screenscraper_id(request, pk):
    """Set the ScreenScraper ID for a game manually.

    POST params:
        screenscraper_id: The ScreenScraper game ID (integer or empty to clear)
        fetch_metadata: Optional, if "1" triggers immediate metadata fetch
    """
    from ..queues import PRIORITY_HIGH
    from ..tasks import run_metadata_job_for_game

    game = get_object_or_404(Game, pk=pk)

    screenscraper_id = request.POST.get("screenscraper_id", "").strip()
    fetch_metadata = request.POST.get("fetch_metadata") == "1"

    if not screenscraper_id:
        # Clear the ID if empty
        game.screenscraper_id = None
        game.save()
    else:
        try:
            game.screenscraper_id = int(screenscraper_id)
            game.save()
        except ValueError:
            # Invalid ID, return error for HTMX
            if request.headers.get("HX-Request"):
                return HttpResponse(
                    '<span class="text-[var(--color-danger)]">Invalid ID (must be numeric)</span>',
                    status=400,
                )
            return redirect("library:game_detail", pk=pk)

    # Optionally trigger metadata fetch
    if fetch_metadata and game.screenscraper_id:
        job = MetadataJob.objects.create(task_id="pending", game=game)
        job_id = run_metadata_job_for_game.configure(priority=PRIORITY_HIGH).defer(
            metadata_job_id=job.pk
        )
        job.task_id = str(job_id)
        job.save()

    # Return partial for HTMX requests
    if request.headers.get("HX-Request"):
        return render(
            request,
            "library/_screenscraper_id_input.html",
            {
                "game": game,
                "saved": True,
                "fetch_triggered": fetch_metadata and game.screenscraper_id,
            },
        )

    return redirect("library:game_detail", pk=pk)
