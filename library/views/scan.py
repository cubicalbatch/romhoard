"""ROM scanning views."""

import os
from pathlib import Path

from django.conf import settings
from django.db.models import Q
from django.http import HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.views.decorators.http import require_POST
from procrastinate.contrib.django import app as procrastinate_app

from ..metadata.screenscraper import screenscraper_available
from ..models import Game, GameImage, ROM, ROMSet, ScanJob, ScanPath
from ..queues import PRIORITY_LOW
from ..tasks import run_scan


def _get_path_counts(scan_path):
    """Get game, ROM, and image counts for a scan path.

    Args:
        scan_path: ScanPath instance

    Returns:
        dict with game_count, rom_count, image_count
    """
    base_path = scan_path.path
    if not base_path.endswith(os.sep):
        base_path = base_path + os.sep

    # Get ROMs under this path
    roms = ROM.objects.filter(
        Q(file_path__startswith=base_path) | Q(archive_path__startswith=base_path)
    )

    # Count unique games from ROMs
    game_count = roms.values("rom_set__game").distinct().count() if roms.exists() else 0
    rom_count = roms.count()

    # Count images that were scanned/uploaded from this path
    image_count = GameImage.objects.filter(
        Q(file_path__startswith=base_path),
        Q(source="scanned") | Q(source="uploaded"),
    ).count()

    return {
        "game_count": game_count,
        "rom_count": rom_count,
        "image_count": image_count,
    }


def _annotate_saved_paths_with_counts(saved_paths):
    """Annotate ScanPath queryset with game/ROM/image counts.

    Args:
        saved_paths: QuerySet or list of ScanPath instances

    Returns:
        List of ScanPath instances with count attributes added
    """
    paths_with_counts = []
    for scan_path in saved_paths:
        counts = _get_path_counts(scan_path)
        scan_path.game_count = counts["game_count"]
        scan_path.rom_count = counts["rom_count"]
        scan_path.image_count = counts["image_count"]
        paths_with_counts.append(scan_path)
    return paths_with_counts


def scan_form(request):
    """Form to trigger a ROM scan."""
    context = {
        "saved_paths": _annotate_saved_paths_with_counts(ScanPath.objects.all()),
        "active_jobs": ScanJob.objects.filter(
            status__in=[ScanJob.STATUS_PENDING, ScanJob.STATUS_RUNNING]
        ),
        "recent_jobs": ScanJob.objects.filter(
            status__in=[ScanJob.STATUS_COMPLETED, ScanJob.STATUS_FAILED]
        )[:5],
        "default_path": getattr(settings, "ROM_LIBRARY_ROOT", ""),
        "screenscraper_configured": screenscraper_available(),
    }

    if request.method == "POST":
        path = request.POST.get("path", "").strip()
        use_hasheous = request.POST.get("use_hasheous") == "on"
        fetch_metadata = request.POST.get("fetch_metadata") == "on"
        schedule_enabled = request.POST.get("schedule_enabled") == "on"
        schedule_interval = request.POST.get(
            "schedule_interval", ScanPath.SCHEDULE_DAILY
        )
        if path:
            # Security: Normalize path to prevent traversal attacks
            path = os.path.normpath(os.path.abspath(path))
            # Validate path exists and is a directory
            if not os.path.isdir(path):
                context["error"] = f"Path does not exist or is not a directory: {path}"
                return render(request, "library/scan.html", context)

            # Save schedule settings to ScanPath (created/updated after scan completes,
            # but we store schedule now so user sees it immediately)
            if schedule_enabled:
                scan_path, _ = ScanPath.objects.get_or_create(path=path)
                scan_path.use_hasheous = use_hasheous
                scan_path.fetch_metadata = fetch_metadata
                scan_path.schedule_enabled = schedule_enabled
                scan_path.schedule_interval = schedule_interval
                scan_path.save()

            # Create ScanJob and enqueue task
            scan_job = ScanJob.objects.create(
                path=path,
                use_hasheous=use_hasheous,
                fetch_metadata=fetch_metadata,
                task_id="pending",
            )
            job_id = run_scan.configure(priority=PRIORITY_LOW).defer(
                scan_job_id=scan_job.pk
            )
            scan_job.task_id = str(job_id)
            scan_job.save()

            # Redirect to same page to show in-progress scan
            return redirect("library:scan")

    return render(request, "library/scan.html", context)


def rescan_path(request, pk):
    """Rescan a saved path."""
    if request.method == "POST":
        scan_path = get_object_or_404(ScanPath, pk=pk)

        # Create ScanJob and enqueue task (use saved settings)
        scan_job = ScanJob.objects.create(
            path=scan_path.path,
            use_hasheous=scan_path.use_hasheous,
            fetch_metadata=scan_path.fetch_metadata,
            task_id="pending",
        )
        job_id = run_scan.configure(priority=PRIORITY_LOW).defer(
            scan_job_id=scan_job.pk
        )
        scan_job.task_id = str(job_id)
        scan_job.save()

        # Redirect to same page to show in-progress scan
        return redirect("library:scan")

    return redirect("library:scan")


def _get_roms_under_path(base_path: str):
    """Get all ROMs under a scan path.

    Matches ROMs where file_path or archive_path starts with the base path.
    """
    # Ensure trailing separator for prefix matching
    if not base_path.endswith(os.sep):
        base_path = base_path + os.sep

    return ROM.objects.filter(
        Q(file_path__startswith=base_path) | Q(archive_path__startswith=base_path)
    )


def _count_orphans_for_deletion(roms_to_delete):
    """Count ROMSets and Games that would become orphaned if these ROMs are deleted.

    Returns:
        Tuple of (orphan_romset_count, orphan_game_count, orphan_game_pks)
    """
    # Materialize the affected ROMs once. Passing the lazy QuerySet into later
    # `pk__in` filters would re-run the (potentially expensive) path-prefix scan
    # on every iteration, and the old implementation ran an O(romsets^2) nest of
    # per-pair queries that hung scan paths with many ROMSets.
    rom_rows = list(roms_to_delete.values_list("pk", "rom_set_id"))
    if not rom_rows:
        return 0, 0, set()
    rom_pks = {rom_pk for rom_pk, _ in rom_rows}
    romset_ids = {romset_id for _, romset_id in rom_rows}

    # A ROMSet is orphaned if none of its ROMs survive the deletion.
    surviving_romset_ids = set(
        ROM.objects.filter(rom_set_id__in=romset_ids)
        .exclude(pk__in=rom_pks)
        .values_list("rom_set_id", flat=True)
    )
    orphan_romset_ids = romset_ids - surviving_romset_ids

    if not orphan_romset_ids:
        return len(orphan_romset_ids), 0, set()

    # A Game is orphaned only if every one of its ROMSets is orphaned, i.e. it
    # has no surviving ROM anywhere (including ROMSets untouched by this path).
    game_ids = set(
        ROMSet.objects.filter(pk__in=orphan_romset_ids).values_list(
            "game_id", flat=True
        )
    )
    surviving_game_ids = set(
        ROM.objects.filter(rom_set__game_id__in=game_ids)
        .exclude(pk__in=rom_pks)
        .values_list("rom_set__game_id", flat=True)
    )
    orphan_game_pks = game_ids - surviving_game_ids

    return len(orphan_romset_ids), len(orphan_game_pks), orphan_game_pks


def scan_path_delete_info(request, pk):
    """Return deletion counts for a scan path (for confirmation modal)."""
    scan_path = get_object_or_404(ScanPath, pk=pk)

    # Find ROMs under this path
    roms = _get_roms_under_path(scan_path.path)
    rom_count = roms.count()

    # Count orphans
    orphan_romsets, orphan_games, orphan_game_pks = _count_orphans_for_deletion(roms)

    # Count images for orphaned games
    from ..models import GameImage

    image_count = GameImage.objects.filter(game_id__in=orphan_game_pks).count()

    context = {
        "scan_path": scan_path,
        "rom_count": rom_count,
        "romset_count": orphan_romsets,
        "game_count": orphan_games,
        "image_count": image_count,
    }
    return render(request, "library/_scan_path_delete_confirm.html", context)


def delete_scan_path(request, pk):
    """Delete a saved scan path and cascade delete associated ROMs."""
    if request.method == "POST":
        scan_path = get_object_or_404(ScanPath, pk=pk)

        # Find ROMs under this path
        roms = _get_roms_under_path(scan_path.path)

        # Delete ROMs and clean up orphans
        deleted_roms = 0
        deleted_romsets = 0
        deleted_games = 0

        for rom in roms.select_related("rom_set", "rom_set__game"):
            rom_set = rom.rom_set
            game = rom_set.game
            rom.delete()
            deleted_roms += 1

            # Delete orphaned ROMSet (no ROMs left)
            if not rom_set.roms.exists():
                rom_set.delete()
                deleted_romsets += 1

                # Delete orphaned Game (no ROMSets left)
                if not game.rom_sets.exists():
                    # Delete image files from disk
                    for image in game.images.all():
                        Path(image.file_path).unlink(missing_ok=True)
                    game.delete()
                    deleted_games += 1

        # Delete the scan path itself
        scan_path.delete()

        context = {
            "saved_paths": _annotate_saved_paths_with_counts(ScanPath.objects.all())
        }
        return render(request, "library/_saved_paths.html", context)

    return redirect("library:scan")


def toggle_hasheous_path(request, pk):
    """Toggle Hasheous setting for a saved scan path."""
    if request.method == "POST":
        scan_path = get_object_or_404(ScanPath, pk=pk)
        scan_path.use_hasheous = not scan_path.use_hasheous
        scan_path.save()

        context = {
            "saved_paths": _annotate_saved_paths_with_counts(ScanPath.objects.all())
        }
        return render(request, "library/_saved_paths.html", context)

    return redirect("library:scan")


def toggle_fetch_metadata_path(request, pk):
    """Toggle fetch_metadata setting for a saved scan path."""
    if request.method == "POST":
        scan_path = get_object_or_404(ScanPath, pk=pk)
        scan_path.fetch_metadata = not scan_path.fetch_metadata
        scan_path.save()

        context = {
            "saved_paths": _annotate_saved_paths_with_counts(ScanPath.objects.all())
        }
        return render(request, "library/_saved_paths.html", context)

    return redirect("library:scan")


def update_scan_schedule(request, pk):
    """Update schedule settings for a saved scan path."""
    if request.method == "POST":
        scan_path = get_object_or_404(ScanPath, pk=pk)
        scan_path.schedule_enabled = request.POST.get("schedule_enabled") == "on"
        scan_path.schedule_interval = request.POST.get(
            "schedule_interval", ScanPath.SCHEDULE_DAILY
        )
        scan_path.save()

        context = {
            "saved_paths": _annotate_saved_paths_with_counts(ScanPath.objects.all())
        }
        return render(request, "library/_saved_paths.html", context)

    return redirect("library:scan")


def clear_library(request):
    """Delete all games and ROMs from the database."""
    if request.method == "POST":
        ROM.objects.all().delete()
        Game.objects.all().delete()

        response = HttpResponse()
        response["HX-Redirect"] = reverse("library:system_list")
        return response

    return redirect("library:scan")


def scan_status(request):
    """HTMX endpoint to poll scan status."""
    active_jobs = ScanJob.objects.filter(
        status__in=[ScanJob.STATUS_PENDING, ScanJob.STATUS_RUNNING]
    )
    recent_jobs = ScanJob.objects.filter(
        status__in=[
            ScanJob.STATUS_COMPLETED,
            ScanJob.STATUS_FAILED,
            ScanJob.STATUS_CANCELLED,
        ]
    )[:5]

    context = {
        "active_jobs": active_jobs,
        "recent_jobs": recent_jobs,
    }
    return render(request, "library/_scan_status.html", context)


@require_POST
def cancel_scan_job(request, job_id):
    """Cancel a scan job."""
    job = get_object_or_404(ScanJob, pk=job_id)

    if job.status in [ScanJob.STATUS_PENDING, ScanJob.STATUS_RUNNING]:
        # Cancel in Procrastinate
        try:
            procrastinate_app.job_manager.cancel_job_by_id(int(job.task_id), abort=True)
        except (ValueError, Exception):
            pass  # Job may have already finished

        # Update Django model
        job.status = ScanJob.STATUS_CANCELLED
        job.completed_at = timezone.now()
        job.save()

    return redirect("library:scan")
