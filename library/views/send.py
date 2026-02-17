"""FTP/SFTP send views."""

import json

from django.http import HttpResponse, JsonResponse
from django.shortcuts import get_object_or_404, render
from django.views.decorators.http import require_POST

from ..models import Game, ROM, SendJob, System


@require_POST
def start_send(request, slug):
    """Start sending selected games or specific ROMs to a device via FTP/SFTP.

    POST body: {
        "game_ids": [1, 2, 3, ...],  # Send default ROMs from these games
        "rom_ids": [1, 2, 3, ...],   # OR send specific ROMs (takes precedence)
        "device_id": 123,
        "transfer_type": "sftp", "transfer_host": "...", ...
    }
    Returns: { "job_id": 123 }
    """
    from devices.models import Device

    from ..queues import PRIORITY_CRITICAL
    from ..tasks import run_send_upload

    try:
        data = json.loads(request.body)
        game_ids = data.get("game_ids", [])
        rom_ids = data.get("rom_ids", [])
        device_id = data.get("device_id")
    except json.JSONDecodeError:
        return HttpResponse("Invalid JSON", status=400)

    if not device_id:
        return HttpResponse("Device not selected", status=400)

    # Validate device exists and has WiFi capability
    device = get_object_or_404(Device, pk=device_id)

    if not device.has_wifi:
        return JsonResponse({"error": "Device does not have WiFi"}, status=400)

    # Update transfer configuration if provided
    transfer_type = data.get("transfer_type")
    transfer_host = data.get("transfer_host")
    transfer_port = data.get("transfer_port")
    transfer_user = data.get("transfer_user")
    transfer_password = data.get("transfer_password")
    transfer_path_prefix = data.get("transfer_path_prefix")

    if (
        transfer_type
        or transfer_host
        or transfer_user
        or transfer_password
        or transfer_path_prefix is not None
    ):
        device.transfer_type = transfer_type or device.transfer_type
        device.transfer_host = transfer_host or device.transfer_host
        device.transfer_port = (
            int(transfer_port) if transfer_port else device.transfer_port
        )
        device.transfer_user = transfer_user or device.transfer_user
        device.transfer_password = transfer_password or device.transfer_password
        if transfer_path_prefix is not None:
            device.transfer_path_prefix = transfer_path_prefix
        device.save()

    if not device.has_transfer_config:
        return JsonResponse(
            {"error": "Device has no transfer configuration"}, status=400
        )

    system = get_object_or_404(System, slug=slug)

    from ..send import get_send_files

    # Calculate games and ROMs to send
    if rom_ids:
        # Validate ROMs exist and belong to the system
        valid_roms = list(
            ROM.objects.filter(pk__in=rom_ids, rom_set__game__system=system)
        )
        if not valid_roms:
            return JsonResponse({"error": "No valid ROMs selected"}, status=400)
        valid_games = None
    else:
        # Validate games exist and belong to the system
        valid_games = list(Game.objects.filter(pk__in=game_ids, system=system))
        if not valid_games:
            return HttpResponse("No valid games selected", status=400)
        valid_roms = None

    # Use the utility to collect all files (ROMs + optionally images) to count total
    all_send_items = get_send_files(
        games=valid_games,
        roms=valid_roms,
        include_images=device.include_images,
        device=device,
    )

    files_total = len(all_send_items)
    if device.include_images:
        # Each item in all_send_items is (game, rom, image_path)
        # We count the ROM itself, and if image_path is set, we count the image too
        files_total = len(all_send_items) + sum(
            1 for g, r, img in all_send_items if img
        )

    if files_total == 0:
        return HttpResponse("No ROM files to upload", status=400)

    # Prepare IDs for the job
    job_game_ids = [g.pk for g in valid_games] if valid_games else []
    job_rom_ids = [r.pk for r in valid_roms] if valid_roms else []

    # Create job
    job = SendJob.objects.create(
        game_ids=job_game_ids,
        rom_ids=job_rom_ids,
        device=device,
        files_total=files_total,
        task_id="pending",
    )

    # Enqueue with high priority (user is waiting)
    task_id = run_send_upload.configure(priority=PRIORITY_CRITICAL).defer(
        send_job_id=job.pk
    )
    job.task_id = str(task_id)
    job.save()

    return JsonResponse({"job_id": job.pk})


def send_status(request, job_id):
    """HTMX endpoint to poll send job status.

    Returns partial HTML for status display.
    """
    job = get_object_or_404(SendJob, pk=job_id)
    context = {"job": job}
    return render(request, "library/_send_status.html", context)
