"""File serving and download views."""

import json
import tempfile
from pathlib import Path

from django.http import FileResponse, HttpResponse, JsonResponse
from django.shortcuts import get_object_or_404, render
from django.urls import reverse
from django.views.decorators.http import require_POST

from ._common import TempFileResponse
from ..download import (
    create_romset_bundle,
    get_rom_file,
    get_rom_file_as_stored,
)
from ..models import DownloadJob, Game, GameImage, ROM, ROMSet, System
from ..tasks import create_download_bundle


def serve_image(request, pk):
    """Serve a game image from disk."""
    image = get_object_or_404(GameImage, pk=pk)
    return FileResponse(open(image.file_path, "rb"))


def serve_system_icon(request, slug):
    """Serve a system icon from disk."""
    system = get_object_or_404(System, slug=slug)
    if not system.icon_path:
        return HttpResponse(status=404)
    icon_path = Path(system.icon_path)
    if not icon_path.exists():
        return HttpResponse(status=404)
    return FileResponse(open(icon_path, "rb"))


def _serve_rom_file(rom: ROM, context_manager) -> HttpResponse:
    """Serve a ROM file using the provided context manager.

    Args:
        rom: ROM instance to serve
        context_manager: Context manager that yields (file_path, filename)

    Returns:
        TempFileResponse or FileResponse for the file
    """
    try:
        with context_manager as (file_path, filename):
            # For loose files or archive files served directly, no temp cleanup needed
            if file_path == rom.file_path or (
                rom.is_archived and file_path == rom.archive_path
            ):
                return FileResponse(
                    open(file_path, "rb"), as_attachment=True, filename=filename
                )

            # For extracted files, copy to a new temp file and serve with cleanup
            with tempfile.NamedTemporaryFile(delete=False) as temp_file:
                temp_path = temp_file.name
                with open(file_path, "rb") as src:
                    temp_file.write(src.read())

            return TempFileResponse(
                open(temp_path, "rb"),
                as_attachment=True,
                filename=filename,
                temp_path=temp_path,
            )
    except FileNotFoundError:
        return HttpResponse("ROM file not found", status=404)


def download_rom(request, pk: int):
    """Download a single ROM file.

    Query params:
        mode: 'extract' to extract ROM from archive, default serves as-stored
    """
    rom = get_object_or_404(ROM, pk=pk)
    mode = request.GET.get("mode", "stored") if request else "stored"

    if mode == "extract":
        return _serve_rom_file(rom, get_rom_file(rom))
    return _serve_rom_file(rom, get_rom_file_as_stored(rom))


def download_romset(request, pk: int):
    """Download a ROMSet (single ROM or multi-disc bundle).

    Query params:
        mode: 'extract' to extract ROM from archive, default serves as-stored
    """
    rom_set = get_object_or_404(ROMSet, pk=pk)
    available_roms = rom_set.roms.all()

    if not available_roms.exists():
        return HttpResponse("No available ROMs", status=404)

    if available_roms.count() == 1:
        # Forward the mode parameter to download_rom
        return download_rom(request, available_roms.first().pk)

    # Multi-disc: create ZIP bundle (always extracts ROMs for bundling)
    try:
        temp_path, filename = create_romset_bundle(rom_set)
        return TempFileResponse(
            open(temp_path, "rb"),
            as_attachment=True,
            filename=filename,
            temp_path=temp_path,
        )
    except FileNotFoundError:
        return HttpResponse("No available ROMs", status=404)


def romset_download_picker(request, pk: int):
    """HTMX partial for ROMSet download options modal."""
    rom_set = get_object_or_404(ROMSet, pk=pk)
    available_roms = rom_set.roms.order_by("disc", "file_name")

    return render(
        request,
        "library/_romset_download_picker.html",
        {
            "rom_set": rom_set,
            "game": rom_set.game,
            "roms": available_roms,
            "rom_count": available_roms.count(),
        },
    )


@require_POST
def start_romset_download(request, pk: int):
    """Start an async ROMSet download job.

    Creates a background job to bundle the ROMSet files into a ZIP.
    Returns job_id for status polling.
    """
    from ..queues import PRIORITY_CRITICAL

    rom_set = get_object_or_404(ROMSet, pk=pk)
    game = rom_set.game
    available_roms = rom_set.roms.all()

    if not available_roms.exists():
        return HttpResponse("No available ROMs", status=404)

    # Single ROM: redirect directly (no bundling needed)
    if available_roms.count() == 1:
        return JsonResponse(
            {
                "redirect_url": reverse(
                    "library:download_rom", args=[available_roms.first().pk]
                )
            }
        )

    # Multiple ROMs: create background job
    job = DownloadJob.objects.create(
        game_ids=[game.pk],
        system_slug=game.system.slug,
        games_total=1,
        task_id="pending",
    )

    job_id = create_download_bundle.configure(priority=PRIORITY_CRITICAL).defer(
        download_job_id=job.pk
    )
    job.task_id = str(job_id)
    job.save()

    return JsonResponse({"job_id": job.pk})


def download_game(request, pk: int):
    """Download game's default ROMSet.

    Query params:
        mode: 'extract' to extract ROM from archive, default serves as-stored
    """
    game = get_object_or_404(Game, pk=pk)

    rom_set = game.default_rom_set
    if not rom_set:
        rom_set = game.rom_sets.filter(roms__isnull=False).distinct().first()

    if not rom_set:
        return HttpResponse("No available ROMs", status=404)

    # If the ROMSet has multiple files, we might want to show the picker
    # but only if requested via a parameter or if we're in a context that supports it.
    # To keep it simple and consistent with the plan, if it's multiple files
    # and we have 'picker=1' in the URL, we redirect to the picker.
    if request and request.GET.get("picker") == "1" and rom_set.rom_count > 1:
        return JsonResponse(
            {"picker_url": reverse("library:romset_download_picker", args=[rom_set.pk])}
        )

    # Forward the mode parameter to download_romset
    return download_romset(request, rom_set.pk)


@require_POST
def start_multi_download(request, slug):
    """Start a multi-game download job.

    POST body: { "game_ids": [1, 2, 3, ...], "device_id": 123 (optional) }
    Returns:
        - Single game: { "redirect_url": "/download/game/123/" }
        - Multiple games: { "job_id": 123 }
    """
    from ..queues import PRIORITY_CRITICAL

    try:
        data = json.loads(request.body)
        game_ids = data.get("game_ids", [])
        device_id = data.get("device_id")
    except json.JSONDecodeError:
        return HttpResponse("Invalid JSON", status=400)

    if not game_ids:
        return HttpResponse("No games selected", status=400)

    # Validate game IDs exist and belong to the system
    system = get_object_or_404(System, slug=slug)
    valid_games = Game.objects.filter(pk__in=game_ids, system=system)

    if not valid_games.exists():
        return HttpResponse("No valid games found", status=400)

    # Single game: redirect directly to download_game (no archive needed)
    if valid_games.count() == 1:
        game = valid_games.first()
        return JsonResponse(
            {"redirect_url": reverse("library:download_game", args=[game.pk])}
        )

    # Multiple games: create background job
    job = DownloadJob.objects.create(
        game_ids=list(valid_games.values_list("pk", flat=True)),
        system_slug=slug,
        games_total=valid_games.count(),
        task_id="pending",
        device_id=device_id,
    )

    # Enqueue background task with high priority (user is waiting)
    job_id = create_download_bundle.configure(priority=PRIORITY_CRITICAL).defer(
        download_job_id=job.pk
    )
    job.task_id = str(job_id)
    job.save()

    return JsonResponse({"job_id": job.pk})


def download_status(request, job_id):
    """HTMX endpoint to poll download job status.

    Returns partial HTML for status display.
    When complete, includes auto-download trigger.
    """
    job = get_object_or_404(DownloadJob, pk=job_id)
    context = {"job": job}
    return render(request, "library/_download_status.html", context)


@require_POST
def preview_games(request):
    """Return a list of games for download/send preview modal.

    Accepts POST with either:
    - collection_slug: fetch matched games from a collection
    - game_ids: list of game IDs (for multi-select)
    """
    try:
        data = json.loads(request.body)
    except json.JSONDecodeError:
        return HttpResponse("Invalid JSON", status=400)

    if "collection_slug" in data:
        from romcollections.models import Collection

        collection = get_object_or_404(Collection, slug=data["collection_slug"])
        games = []
        for entry in collection.entries.all():
            game = entry.get_matched_game()
            if game and game.rom_sets.exists():
                games.append(game)
    else:
        game_ids = data.get("game_ids", [])
        games = list(Game.objects.filter(pk__in=game_ids).select_related("system"))

    return render(request, "library/_games_preview.html", {"games": games})


def serve_download_bundle(request, job_id):
    """Serve the completed download bundle file."""
    job = get_object_or_404(DownloadJob, pk=job_id)

    if job.status != DownloadJob.STATUS_COMPLETED:
        return HttpResponse("Download not ready", status=400)

    if not job.file_path or not Path(job.file_path).exists():
        return HttpResponse("Download file not found", status=404)

    if job.is_expired:
        return HttpResponse("Download has expired", status=410)

    return FileResponse(
        open(job.file_path, "rb"),
        as_attachment=True,
        filename=job.file_name,
    )
