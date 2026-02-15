"""Game CRUD views."""

from pathlib import Path

from django.http import HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.views.decorators.http import require_POST

from romcollections.models import CollectionEntry

from ..merge import merge_games
from ..models import Game, GameImage, System


@require_POST
def delete_game(request, pk):
    """Delete a game from the library.

    Deletes the game and its metadata images from disk.
    ROM files are preserved (user manages ROM directories separately).
    """
    game = get_object_or_404(Game, pk=pk)
    system_slug = game.system.slug

    # Delete image files from disk
    for image in game.images.all():
        try:
            image_path = Path(image.file_path)
            if image_path.exists():
                image_path.unlink()
        except OSError:
            pass  # File may already be deleted or inaccessible

    # Delete game (cascades to ROMSets, ROMs, GameImages, MetadataJobs)
    game.delete()

    # Redirect to game list for this system
    response = HttpResponse()
    response["HX-Redirect"] = reverse("library:game_list", args=[system_slug])
    return response


@require_POST
def rename_game(request, pk):
    """Rename a game.

    POST params:
        name: The new game name
    """
    game = get_object_or_404(Game, pk=pk)
    old_name = game.name

    new_name = request.POST.get("name", "").strip()

    if not new_name:
        if request.headers.get("HX-Request"):
            return HttpResponse(
                '<span class="text-[var(--color-danger)]">Name cannot be empty</span>',
                status=400,
            )
        return redirect("library:game_detail", pk=pk)

    # Check for duplicate (same name + system)
    if (
        new_name != old_name
        and Game.objects.filter(name=new_name, system=game.system)
        .exclude(pk=pk)
        .exists()
    ):
        if request.headers.get("HX-Request"):
            return HttpResponse(
                '<span class="text-[var(--color-danger)]">A game with this name already exists</span>',
                status=400,
            )
        return redirect("library:game_detail", pk=pk)

    # Update game name
    game.name = new_name
    game.name_source = Game.SOURCE_MANUAL
    game.save()

    # Update CollectionEntry references (they store game_name + system_slug)
    CollectionEntry.objects.filter(
        game_name=old_name, system_slug=game.system.slug
    ).update(game_name=new_name)

    # Return partial for HTMX requests
    if request.headers.get("HX-Request"):
        return render(
            request,
            "library/_game_rename_input.html",
            {
                "game": game,
                "saved": True,
            },
        )

    return redirect("library:game_detail", pk=pk)


def edit_game(request, pk):
    """Edit a game's name, description, and images.

    GET: Render the edit form (HTMX partial)
    POST: Save changes and return updated form
    """
    from ..image_utils import delete_game_image as delete_image, save_uploaded_image

    game = get_object_or_404(Game, pk=pk)

    if request.method == "POST":
        # Handle form submission
        old_name = game.name
        old_system = game.system
        new_name = request.POST.get("name", "").strip()
        new_description = request.POST.get("description", "").strip()
        new_system_slug = request.POST.get("system", "").strip()

        errors = []

        # Validate system
        new_system = old_system
        if new_system_slug and new_system_slug != old_system.slug:
            try:
                new_system = System.objects.get(slug=new_system_slug)
            except System.DoesNotExist:
                errors.append("Invalid system selected")

        # Validate name
        if not new_name:
            errors.append("Name cannot be empty")
        elif (
            (new_name != old_name or new_system != old_system)
            and Game.objects.filter(name=new_name, system=new_system)
            .exclude(pk=pk)
            .exists()
        ):
            errors.append("A game with this name already exists in the selected system")

        if errors:
            if request.headers.get("HX-Request"):
                return HttpResponse(
                    f'<div class="text-[var(--color-danger)] text-sm mb-4">{"; ".join(errors)}</div>',
                    status=400,
                )
            return redirect("library:game_detail", pk=pk)

        # Update game fields
        game.name = new_name
        game.description = new_description
        game.name_source = Game.SOURCE_MANUAL
        if new_system != old_system:
            game.system = new_system

        # Handle ScreenScraper ID
        screenscraper_id = request.POST.get("screenscraper_id", "").strip()
        fetch_metadata_on_save = request.POST.get("fetch_metadata_on_save") == "1"

        if screenscraper_id:
            try:
                game.screenscraper_id = int(screenscraper_id)
            except ValueError:
                errors.append("ScreenScraper ID must be numeric")
        elif screenscraper_id == "":
            game.screenscraper_id = None

        game.save()

        # Trigger metadata fetch if requested and SSID is set
        if fetch_metadata_on_save and game.screenscraper_id and not errors:
            from ..models import MetadataJob
            from ..queues import PRIORITY_HIGH
            from ..tasks import run_metadata_job_for_game

            job = MetadataJob.objects.create(task_id="pending", game=game)
            job_id = run_metadata_job_for_game.configure(priority=PRIORITY_HIGH).defer(
                metadata_job_id=job.pk
            )
            job.task_id = str(job_id)
            job.save()

        # Update CollectionEntry references if name or system changed
        if new_name != old_name or new_system != old_system:
            CollectionEntry.objects.filter(
                game_name=old_name, system_slug=old_system.slug
            ).update(game_name=new_name, system_slug=new_system.slug)

        # Handle cover image upload
        if "cover_image" in request.FILES:
            cover_file = request.FILES["cover_image"]
            try:
                save_uploaded_image(game, cover_file, "cover")
            except ValueError as e:
                errors.append(f"Cover upload failed: {e}")

        # Handle screenshot uploads (multiple)
        for screenshot_file in request.FILES.getlist("screenshots"):
            try:
                save_uploaded_image(game, screenshot_file, "screenshot")
            except ValueError as e:
                errors.append(f"Screenshot upload failed: {e}")

        # Handle image deletions
        delete_ids = request.POST.getlist("delete_images")
        if delete_ids:
            images_to_delete = GameImage.objects.filter(pk__in=delete_ids, game=game)
            for img in images_to_delete:
                delete_image(img, delete_file=True)

        # For HTMX, return the form with success state
        if request.headers.get("HX-Request"):
            # Refresh game with updated images
            game.refresh_from_db()
            images = list(game.images.all())
            cover_image = None
            screenshots = []
            for img in images:
                if img.image_type == "cover":
                    cover_image = img
                elif img.image_type in ("screenshot", "screenshot_title"):
                    screenshots.append(img)

            return render(
                request,
                "library/_game_edit_form.html",
                {
                    "game": game,
                    "cover_image": cover_image,
                    "screenshots": screenshots,
                    "systems": System.objects.all().order_by("name"),
                    "saved": True,
                    "errors": errors,
                },
            )

        return redirect("library:game_detail", pk=pk)

    # GET request - render the edit form
    images = list(game.images.all())
    cover_image = None
    screenshots = []
    for img in images:
        if img.image_type == "cover":
            cover_image = img
        elif img.image_type in ("screenshot", "screenshot_title"):
            screenshots.append(img)

    return render(
        request,
        "library/_game_edit_form.html",
        {
            "game": game,
            "cover_image": cover_image,
            "screenshots": screenshots,
            "systems": System.objects.all().order_by("name"),
        },
    )


@require_POST
def delete_game_image(request, pk):
    """Delete a game image.

    POST params:
        image_id: ID of the image to delete
    """
    from ..image_utils import delete_game_image as delete_image

    game = get_object_or_404(Game, pk=pk)
    image_id = request.POST.get("image_id")

    if image_id:
        try:
            image = GameImage.objects.get(pk=image_id, game=game)
            delete_image(image, delete_file=True)
        except GameImage.DoesNotExist:
            pass

    if request.headers.get("HX-Request"):
        return HttpResponse("")

    return redirect("library:game_detail", pk=pk)


def game_search_for_merge(request, pk):
    """Search for games to merge into (HTMX endpoint).

    GET params:
        q: Search query (minimum 2 characters)

    Returns HTML list of matching games from the same system.
    """
    game = get_object_or_404(Game, pk=pk)
    query = request.GET.get("q", "").strip()

    if len(query) < 2:
        return render(request, "library/_merge_search_results.html", {"games": []})

    # Search games in the same system, excluding the current game
    matching_games = (
        Game.objects.filter(system=game.system, name__icontains=query)
        .exclude(pk=pk)
        .select_related("system")
        .prefetch_related("rom_sets")
        .order_by("name")[:10]
    )

    return render(
        request,
        "library/_merge_search_results.html",
        {"games": matching_games},
    )


@require_POST
def merge_game(request, pk):
    """Merge current game into another game.

    POST params:
        target_game_id: ID of the game to merge into

    Moves all ROM sets from this game to the target game and deletes this game.
    """
    source_game = get_object_or_404(Game, pk=pk)
    target_game_id = request.POST.get("target_game_id")

    if not target_game_id:
        return HttpResponse("Target game is required", status=400)

    try:
        target_game = Game.objects.get(pk=target_game_id)
    except Game.DoesNotExist:
        return HttpResponse("Target game not found", status=404)

    # Validate same system
    if source_game.system_id != target_game.system_id:
        return HttpResponse("Cannot merge games from different systems", status=400)

    # Validate not same game
    if source_game.pk == target_game.pk:
        return HttpResponse("Cannot merge a game into itself", status=400)

    # Perform the merge (target is canonical, source is duplicate)
    merge_games(target_game, source_game)

    # Redirect to the target game
    response = HttpResponse()
    response["HX-Redirect"] = reverse("library:game_detail", args=[target_game.pk])
    return response
