"""Upload workflow views."""

import json
import os
import shutil

from django.http import JsonResponse
from django.shortcuts import get_object_or_404, render
from django.utils import timezone
from django.views.decorators.http import require_POST

from ..models import ROM, System, UploadJob


def upload_page(request):
    """Render the upload page for adding games."""
    from ..upload import build_extension_map_for_frontend, get_library_root

    library_root = get_library_root()
    extension_map = build_extension_map_for_frontend()

    # Get all systems for unidentified resolution
    systems = System.objects.all().order_by("name")

    context = {
        "library_root": library_root,
        "extension_map": json.dumps(extension_map),
        "systems": systems,
    }
    return render(request, "library/upload.html", context)


@require_POST
def start_upload(request):
    """Create an upload job and return job ID."""
    from ..upload import get_library_root

    # Check library_root is configured
    if not get_library_root():
        return JsonResponse(
            {
                "error": "Upload destination not configured. Set Library Root Path in Metadata > Library Settings."
            },
            status=400,
        )

    file_count = int(request.POST.get("file_count", 0))
    total_size = int(request.POST.get("total_size", 0))
    fetch_metadata = request.POST.get("fetch_metadata") == "1"

    if file_count == 0:
        return JsonResponse({"error": "No files selected"}, status=400)

    # Create upload job
    job = UploadJob.objects.create(
        files_total=file_count,
        bytes_total=total_size,
        fetch_metadata=fetch_metadata,
    )

    return JsonResponse({"job_id": job.pk})


@require_POST
def check_duplicates(request):
    """Check if files already exist in library before upload.

    Accepts JSON body with list of {name, size} objects.
    Returns {duplicates: {filename: {game_id, game_name, system_slug, has_icon} | null}}.
    """
    try:
        files = json.loads(request.body)
    except json.JSONDecodeError:
        return JsonResponse({"error": "Invalid JSON"}, status=400)

    if not isinstance(files, list):
        return JsonResponse({"error": "Expected list of files"}, status=400)

    duplicates = {}
    for f in files:
        name = f.get("name")
        size = f.get("size")
        if name and size is not None:
            rom = (
                ROM.objects.filter(file_name=name, file_size=size)
                .select_related("rom_set__game__system")
                .first()
            )
            if rom:
                game = rom.rom_set.game
                system = game.system
                duplicates[name] = {
                    "game_id": game.pk,
                    "game_name": game.name,
                    "system_slug": system.slug,
                    "has_icon": bool(system.icon_path),
                }
            else:
                duplicates[name] = None

    return JsonResponse({"duplicates": duplicates})


@require_POST
def upload_file(request, job_id: int):
    """Handle individual file upload within a job."""
    from ..upload import get_upload_temp_dir

    job = get_object_or_404(UploadJob, pk=job_id)

    if job.status not in [UploadJob.STATUS_UPLOADING, UploadJob.STATUS_PROCESSING]:
        return JsonResponse({"error": "Job not accepting uploads"}, status=400)

    uploaded_file = request.FILES.get("file")
    if not uploaded_file:
        return JsonResponse({"error": "No file provided"}, status=400)

    # Save to temp location
    temp_dir = get_upload_temp_dir()
    job_temp_dir = os.path.join(temp_dir, str(job.pk))
    os.makedirs(job_temp_dir, exist_ok=True)

    # Sanitize filename to prevent path traversal attacks
    # os.path.basename strips any directory components like "../" or "foo/bar"
    safe_filename = os.path.basename(uploaded_file.name)
    if not safe_filename:
        return JsonResponse({"error": "Invalid filename"}, status=400)
    temp_path = os.path.join(job_temp_dir, safe_filename)

    # Write chunks to avoid loading large files into memory
    with open(temp_path, "wb+") as dest:
        for chunk in uploaded_file.chunks():
            dest.write(chunk)

    # Update progress
    job.files_uploaded += 1
    job.bytes_uploaded += uploaded_file.size
    job.current_file = safe_filename  # Use sanitized filename
    job.save()

    return JsonResponse(
        {
            "status": "ok",
            "files_uploaded": job.files_uploaded,
            "files_total": job.files_total,
        }
    )


@require_POST
def finalize_upload(request, job_id: int):
    """Called when all files uploaded - trigger processing."""
    from ..queues import PRIORITY_CRITICAL
    from ..tasks import process_upload_job

    job = get_object_or_404(UploadJob, pk=job_id)

    if job.files_uploaded == 0:
        job.status = UploadJob.STATUS_FAILED
        job.errors = ["No files were uploaded"]
        job.completed_at = timezone.now()
        job.save()
        return JsonResponse({"status": "failed", "error": "No files uploaded"})

    job.status = UploadJob.STATUS_PROCESSING
    job.upload_completed_at = timezone.now()
    job.save()

    # Enqueue background processing task
    task_id = process_upload_job.configure(priority=PRIORITY_CRITICAL).defer(
        upload_job_id=job.pk
    )
    job.task_id = str(task_id)
    job.save()

    return JsonResponse({"status": "processing", "job_id": job.pk})


def upload_status(request, job_id: int):
    """HTMX endpoint to poll upload job status."""
    job = get_object_or_404(UploadJob, pk=job_id)
    systems = System.objects.all().order_by("name")
    return render(
        request, "library/_upload_status.html", {"job": job, "systems": systems}
    )


@require_POST
def resolve_unidentified(request, job_id: int):
    """Handle user system selection for unidentified games."""
    from ..archive import compute_file_crc32
    from ..parser import parse_rom_filename
    from ..scanner import get_or_create_rom_set
    from ..upload import (
        check_duplicate,
        ensure_destination_dir,
        get_unique_filepath,
        get_upload_temp_dir,
    )

    job = get_object_or_404(UploadJob, pk=job_id)

    # Prevent double-processing from rapid button clicks
    if job.status != UploadJob.STATUS_AWAITING_INPUT:
        return JsonResponse(
            {"error": "Job is not awaiting input", "status": job.status}, status=400
        )

    # Mark as processing immediately to prevent race condition
    job.status = UploadJob.STATUS_PROCESSING
    job.save(update_fields=["status"])

    # Parse system assignments from POST
    data = json.loads(request.body)
    assignments = data.get("assignments", {})

    processed_count = 0
    for item in job.unidentified_files:
        temp_path = item.get("temp_path")
        filename = item.get("filename")

        if temp_path not in assignments:
            continue

        system_slug = assignments[temp_path]
        if system_slug == "skip":
            # Remove temp file and skip
            if os.path.exists(temp_path):
                os.remove(temp_path)
            job.games_skipped += 1
            processed_count += 1
            continue

        try:
            system = System.objects.get(slug=system_slug)

            # Check for duplicate
            if check_duplicate(filename, system):
                job.games_skipped += 1
                if os.path.exists(temp_path):
                    os.remove(temp_path)
                processed_count += 1
                continue

            # Ensure destination directory exists
            dest_dir = ensure_destination_dir(system_slug)
            dest_path = get_unique_filepath(dest_dir, filename)

            # Move file to final location
            shutil.move(temp_path, dest_path)

            # Parse filename and compute hash
            parsed = parse_rom_filename(filename)
            file_size = os.path.getsize(dest_path)
            crc32 = compute_file_crc32(dest_path)

            # Create DB records using existing scanner logic
            rom_set, _, _ = get_or_create_rom_set(
                name=parsed["name"],
                system=system,
                region=parsed["region"],
                revision=parsed["revision"],
                source_path=os.path.dirname(dest_path),
                crc32=crc32,
                file_path=dest_path,
                use_hasheous=True,
                fetch_metadata=job.fetch_metadata,
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
            processed_count += 1

        except System.DoesNotExist:
            job.games_failed += 1
            job.errors.append(f"Unknown system: {system_slug}")
            processed_count += 1
        except Exception as e:
            job.games_failed += 1
            job.errors.append(f"Failed to process {filename}: {str(e)}")
            processed_count += 1

    # Clear unidentified list and mark completed
    job.unidentified_files = []
    job.status = UploadJob.STATUS_COMPLETED
    job.completed_at = timezone.now()
    job.save()

    # Cleanup temp directory
    temp_dir = os.path.join(get_upload_temp_dir(), str(job.pk))
    if os.path.exists(temp_dir):
        shutil.rmtree(temp_dir, ignore_errors=True)

    return JsonResponse({"status": "completed", "processed": processed_count})
