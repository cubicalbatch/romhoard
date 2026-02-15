import json
import os
import uuid

from django.contrib import messages
from django.core.paginator import Paginator
from django.db.models import (
    Case,
    Count,
    Exists,
    F,
    IntegerField,
    Max,
    OuterRef,
    Q,
    Value,
    When,
)
from django.http import FileResponse, HttpResponse, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils.http import url_has_allowed_host_and_scheme
from django.utils.text import slugify
from django.views.decorators.http import require_GET, require_POST

from devices.models import Device
from django.conf import settings
from library.models import DownloadJob, Game, ROM, SendJob, System
from library.tasks import create_download_bundle, run_send_upload

from .models import Collection, CollectionEntry, CoverJob, ExportJob
from .search import (
    WEIGHT_GAME,
    _annotate_collection_counts,
    _attach_sample_covers_bulk,
    _build_relevance_annotations,
    _compute_matched_counts_bulk,
)
from .serializers import ImportError as SerializerImportError
from .serializers import ValidationResult, export_collection as serialize_export
from .serializers import import_collection as serialize_import
from .serializers import validate_collection_zip
from .serializers import import_collection_with_images
from .tasks import create_collection_export, generate_collection_cover

# Character limits for text fields
MAX_DESCRIPTION_LENGTH = 1000
MAX_NOTES_LENGTH = 1000


COLLECTION_PAGE_SIZE = 12


def maybe_generate_cover(collection: Collection) -> CoverJob | None:
    """Trigger cover generation for a collection if it has matched games with images.

    This is a non-blocking operation that queues a background task.
    Should be called after importing a collection or adding entries.

    Uses transaction and select_for_update to prevent concurrent job creation.

    Args:
        collection: Collection to generate cover for

    Returns:
        CoverJob if generation was queued, None otherwise
    """
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


def _paginate_collections(queryset, page_number, page_size=COLLECTION_PAGE_SIZE):
    """Paginate collections and attach sample covers efficiently.

    Key optimization: Paginate FIRST, then process only the displayed page.
    """
    # Ensure consistent ordering for pagination, but preserve existing ordering if set
    # (e.g., relevance-based ordering from search)
    # Use model's Meta ordering: Favorites first, then alphabetically by name
    if not queryset.query.order_by:
        queryset = queryset.order_by("-is_favorites", "name")

    # Annotate with entry_count before pagination
    queryset = _annotate_collection_counts(queryset)

    # Paginate first - only process what we need
    paginator = Paginator(queryset, page_size)
    page = paginator.get_page(page_number)

    # Bulk compute matched counts and sample covers only for items on this page
    page_collections = list(page.object_list)
    _compute_matched_counts_bulk(page_collections)
    _attach_sample_covers_bulk(page_collections, limit=5)

    return page


def _paginate_collections_by_matched_count(
    queryset, page_number, page_size=COLLECTION_PAGE_SIZE
):
    """Paginate collections ordered by matched count (descending).

    Unlike _paginate_collections(), this computes matched counts for ALL
    collections first to enable sorting by matched count before pagination.
    Used for community collections where "most useful" (most matched games) should appear first.
    """
    # Annotate with entry_count
    queryset = _annotate_collection_counts(queryset)

    # Fetch all collections
    collections = list(queryset)

    # Compute matched counts for all
    _compute_matched_counts_bulk(collections)

    # Sort by matched_count descending, then name ascending as tiebreaker
    collections.sort(key=lambda c: (-c.matched_count_annotated, c.name.lower()))

    # Manual pagination
    paginator = Paginator(collections, page_size)
    page = paginator.get_page(page_number)

    # Attach sample covers only for current page
    page_collections = list(page.object_list)
    _attach_sample_covers_bulk(page_collections, limit=5)

    return page


def collection_list(request):
    """List all collections split into personal and community."""
    personal = Collection.objects.filter(is_community=False)
    community = Collection.objects.filter(is_community=True)

    personal_page = request.GET.get("personal_page", 1)
    community_page = request.GET.get("community_page", 1)

    personal_page_obj = _paginate_collections(personal, personal_page)
    community_page_obj = _paginate_collections_by_matched_count(
        community, community_page
    )

    context = {
        "personal_collections": personal_page_obj,
        "community_collections": community_page_obj,
        "personal_page_obj": personal_page_obj,
        "community_page_obj": community_page_obj,
        "total_personal": personal.count(),
        "total_community": community.count(),
    }
    return render(request, "collections/collection_list.html", context)


def creator_page(request, creator: str):
    """Page showing all collections by a creator.

    Args:
        request: The HTTP request
        creator: The creator slug from the URL

    Returns:
        Rendered creator page template

    Raises:
        Http404: If no collections found for this creator
    """
    from django.http import Http404

    # Get all collections by this creator (no is_public filter for local instance)
    queryset = Collection.objects.filter(creator=creator)

    # If no collections found, return 404
    if not queryset.exists():
        raise Http404(f"No collections found for creator '{creator}'")

    # Pagination
    page_number = request.GET.get("page", 1)
    page_obj = _paginate_collections(queryset, page_number)

    # Stats
    total_collections = queryset.count()

    context = {
        "creator": creator,
        "collections": page_obj,
        "page_obj": page_obj,
        "total_collections": total_collections,
    }

    return render(request, "collections/creator.html", context)


def collection_search(request):
    """HTMX endpoint for searching/filtering collections.

    Searches across:
    - Collection name, description, tags, creator
    - Game names within collection entries
    - System/platform (by slug, name, or common aliases)
    """
    query = request.GET.get("q", "").strip()
    filter_type = request.GET.get("type", "all")  # all, personal, community

    personal = Collection.objects.filter(is_community=False)
    community = Collection.objects.filter(is_community=True)

    if query:
        # Basic collection field search
        search_filter = (
            Q(name__icontains=query)
            | Q(description__icontains=query)
            | Q(tags__icontains=query)
            | Q(creator__icontains=query)
        )

        # Search game names within collection entries
        entry_game_match = CollectionEntry.objects.filter(
            collection=OuterRef("pk"), game_name__icontains=query
        )
        search_filter |= Exists(entry_game_match)

        # Search by system - match slug, name, or folder_names
        matching_system_slugs = list(
            System.objects.filter(
                Q(slug__iexact=query)
                | Q(name__icontains=query)
                | Q(folder_names__icontains=query)
            ).values_list("slug", flat=True)
        )

        if matching_system_slugs:
            entry_system_match = CollectionEntry.objects.filter(
                collection=OuterRef("pk"), system_slug__in=matching_system_slugs
            )
            search_filter |= Exists(entry_system_match)

        # Build relevance scoring annotations
        relevance_annotations = _build_relevance_annotations(
            query, matching_system_slugs
        )

        # Apply filter, annotations, and order by relevance
        personal = (
            personal.filter(search_filter)
            .annotate(
                **relevance_annotations,
                relevance=(
                    F("score_name")
                    + F("score_tags")
                    + F("score_description")
                    + F("score_creator")
                    + F("score_system")
                    + (F("game_match_count") * WEIGHT_GAME)
                ),
            )
            .order_by("-relevance", "name")
        )
        community = (
            community.filter(search_filter)
            .annotate(
                **relevance_annotations,
                relevance=(
                    F("score_name")
                    + F("score_tags")
                    + F("score_description")
                    + F("score_creator")
                    + F("score_system")
                    + (F("game_match_count") * WEIGHT_GAME)
                ),
            )
            .order_by("-relevance", "name")
        )

    personal_page = request.GET.get("personal_page", 1)
    community_page = request.GET.get("community_page", 1)

    # Apply type filter for what sections to show
    show_personal = filter_type in ("all", "personal")
    show_community = filter_type in ("all", "community")

    personal_page_obj = (
        _paginate_collections(personal, personal_page) if show_personal else None
    )
    # Community collections: use matched count ordering when no search query,
    # otherwise preserve relevance-based ordering from search
    if show_community:
        if query:
            community_page_obj = _paginate_collections(community, community_page)
        else:
            community_page_obj = _paginate_collections_by_matched_count(
                community, community_page
            )
    else:
        community_page_obj = None

    context = {
        "personal_collections": personal_page_obj,
        "community_collections": community_page_obj,
        "personal_page_obj": personal_page_obj,
        "community_page_obj": community_page_obj,
        "total_personal": personal.count() if show_personal else 0,
        "total_community": community.count() if show_community else 0,
        "show_personal": show_personal,
        "show_community": show_community,
        "query": query,
        "filter_type": filter_type,
    }
    # Return partial for HTMX requests, full page for direct browser access
    if request.headers.get("HX-Request"):
        return render(request, "collections/_collection_search_results.html", context)
    return render(request, "collections/collection_list.html", context)


@require_POST
def adopt_collection(request, creator, slug):
    """Convert a community collection to personal."""
    collection = get_object_or_404(
        Collection, creator=creator, slug=slug, is_community=True
    )
    collection.is_community = False
    collection.save(update_fields=["is_community"])
    next_url = request.POST.get("next") or request.GET.get("next")
    # Security: Validate redirect URL to prevent open redirect attacks
    if next_url and url_has_allowed_host_and_scheme(next_url, allowed_hosts=None):
        return redirect(next_url)
    return redirect("romcollections:collection_list")


@require_POST
def unadopt_collection(request, creator, slug):
    """Convert a personal collection back to community."""
    collection = get_object_or_404(
        Collection, creator=creator, slug=slug, is_community=False
    )
    collection.is_community = True
    collection.save(update_fields=["is_community"])
    next_url = request.POST.get("next") or request.GET.get("next")
    # Security: Validate redirect URL to prevent open redirect attacks
    if next_url and url_has_allowed_host_and_scheme(next_url, allowed_hosts=None):
        return redirect(next_url)
    return redirect("romcollections:collection_list")


@require_POST
def toggle_favorite(request, game_pk):
    """Toggle a game's presence in the Favorites collection."""
    game = get_object_or_404(Game, pk=game_pk)
    favorites = get_object_or_404(Collection, is_favorites=True)

    entry = CollectionEntry.objects.filter(
        collection=favorites,
        game_name__iexact=game.name,
        system_slug=game.system.slug,
    ).first()

    if entry:
        entry.delete()
        is_favorite = False
    else:
        max_position = (
            favorites.entries.aggregate(Max("position"))["position__max"] or 0
        )
        CollectionEntry.objects.create(
            collection=favorites,
            game_name=game.name,
            system_slug=game.system.slug,
            position=max_position + 1,
        )
        is_favorite = True

    return JsonResponse({"is_favorite": is_favorite})


def collection_game_detail(request, creator, slug, game_pk):
    """Show game detail with collection context for breadcrumb navigation.

    This view renders the standard game_detail template but with collection
    context for the breadcrumb: Collections > {Collection} > {Game}
    """
    from library.views.browse import game_detail as library_game_detail

    collection = get_object_or_404(Collection, creator=creator, slug=slug)
    game = get_object_or_404(Game, pk=game_pk)

    # Call the library game_detail view with from_collection in context
    # We need to render the same template but with our collection context
    from library.metadata.screenscraper import screenscraper_available

    # Order romsets with default first
    rom_sets = (
        game.rom_sets.prefetch_related("roms")
        .annotate(
            is_default=Case(
                When(pk=game.default_rom_set_id, then=Value(0)),
                default=Value(1),
                output_field=IntegerField(),
            )
        )
        .order_by("is_default", "region")
    )
    images = list(game.images.all())

    # Get collections this game is in
    collection_entries = (
        CollectionEntry.objects.filter(
            game_name=game.name, system_slug=game.system.slug
        )
        .select_related("collection")
        .order_by("collection__name")
    )

    # Check if game is in Favorites collection
    favorites = Collection.objects.filter(is_favorites=True).first()
    is_favorite = False
    if favorites:
        is_favorite = CollectionEntry.objects.filter(
            collection=favorites,
            game_name__iexact=game.name,
            system_slug=game.system.slug,
        ).exists()

    # Organize images by type for the redesigned template
    hero_image = None
    wheel_image = None
    cover_image = None
    screenshots = []

    for img in images:
        if img.image_type == "mix" and not hero_image:
            hero_image = img
        elif img.image_type == "cover":
            if not cover_image:
                cover_image = img
            if not hero_image:
                hero_image = img
        elif img.image_type == "screenshot":
            screenshots.append(img)
        elif img.image_type == "wheel":
            wheel_image = img

    # If no mix or cover, use first screenshot as hero
    if not hero_image and screenshots:
        hero_image = screenshots[0]

    context = {
        "game": game,
        "rom_sets": rom_sets,
        "images": images,
        "hero_image": hero_image,
        "cover_image": cover_image,
        "wheel_image": wheel_image,
        "screenshots": screenshots,
        "collection_entries": collection_entries,
        "from_collection": collection,  # This is the key context for breadcrumb
        "is_favorite": is_favorite,
        "screenscraper_configured": screenscraper_available(),
    }
    return render(request, "library/game_detail.html", context)


def collection_detail(request, creator, slug):
    """Show collection with entries and match status."""
    collection = get_object_or_404(Collection, creator=creator, slug=slug)

    # Note: Collection context for breadcrumb is now handled via URL
    # /collections/{slug}/{game_pk}/ instead of session-based tracking

    # Get sort parameters
    sort = request.GET.get("sort", "position")
    order = request.GET.get("order", "asc")

    entries = collection.entries.all()

    # Prefetch systems for efficiency
    system_map = {s.slug: s for s in System.objects.all()}

    entries_with_match = []
    for entry in entries:
        matched_game = entry.get_matched_game()
        system = system_map.get(entry.system_slug)
        has_roms = matched_game.rom_sets.exists() if matched_game else False
        entries_with_match.append(
            {
                "entry": entry,
                "matched_game": matched_game,
                "is_matched": matched_game is not None,
                "has_roms": has_roms,
                "system": system,
            }
        )

    matched_count = sum(1 for e in entries_with_match if e["has_roms"])

    # Sort the list based on parameters
    if sort == "name":
        entries_with_match.sort(
            key=lambda x: x["entry"].game_name.lower(), reverse=(order == "desc")
        )
    elif sort == "system":
        entries_with_match.sort(
            key=lambda x: x["entry"].system_slug.lower(), reverse=(order == "desc")
        )
    elif sort == "rating":
        # Nulls last for rating (use -1 for missing ratings so they sort to end in DESC)
        entries_with_match.sort(
            key=lambda x: (
                x["matched_game"].rating
                if x["matched_game"] and x["matched_game"].rating
                else -1
            ),
            reverse=(order == "desc"),
        )
    elif sort == "status":
        entries_with_match.sort(key=lambda x: x["has_roms"], reverse=(order == "desc"))
    else:  # position (default)
        entries_with_match.sort(key=lambda x: x["entry"].position)

    # Pagination
    page_size = request.GET.get(
        "page_size", request.session.get("collection_page_size", 25)
    )
    try:
        page_size = int(page_size)
        if page_size not in [25, 50, 100, 200]:
            page_size = 25
    except (ValueError, TypeError):
        page_size = 25

    request.session["collection_page_size"] = page_size

    paginator = Paginator(entries_with_match, page_size)
    page_number = request.GET.get("page", 1)
    page_obj = paginator.get_page(page_number)

    context = {
        "collection": collection,
        "entries": page_obj,
        "page_obj": page_obj,
        "current_page_size": page_size,
        "matched_count": matched_count,
        "total_count": len(entries_with_match),
        "current_sort": sort,
        "current_order": order,
    }
    return render(request, "collections/collection_detail.html", context)


def collection_entry_search(request, creator, slug):
    """HTMX endpoint for searching/filtering collection entries.

    Query params:
        q: Text search (game name)
        system: Comma-separated system slugs (OR logic)
        genre: Comma-separated genre slugs (OR logic)
        rating_op: Rating operator (gte, lte, eq, between)
        rating_min: Minimum rating value
        rating_max: Maximum rating (only for 'between')
        status: Match status filter ('all', 'in_library', 'not_in_library')
        sort: Sort field (position, name, system, rating, status)
        order: Sort order (asc, desc)
    """
    collection = get_object_or_404(Collection, creator=creator, slug=slug)

    # Parse query parameters
    query = request.GET.get("q", "").strip()
    system_slugs = [s for s in request.GET.get("system", "").split(",") if s]
    genre_slugs = [g for g in request.GET.get("genre", "").split(",") if g]
    rating_op = request.GET.get("rating_op", "").strip()
    rating_min = request.GET.get("rating_min", "").strip()
    rating_max = request.GET.get("rating_max", "").strip()
    status_filter = request.GET.get("status", "all").strip()
    sort = request.GET.get("sort", "position")
    order = request.GET.get("order", "asc")

    # Get all entries
    entries = collection.entries.all()

    # Prefetch systems for efficiency
    system_map = {s.slug: s for s in System.objects.all()}

    # Build entries with match info
    entries_with_match = []
    for entry in entries:
        matched_game = entry.get_matched_game()
        system = system_map.get(entry.system_slug)
        has_roms = matched_game.rom_sets.exists() if matched_game else False
        entries_with_match.append(
            {
                "entry": entry,
                "matched_game": matched_game,
                "is_matched": matched_game is not None,
                "has_roms": has_roms,
                "system": system,
            }
        )

    # Apply text search filter (game name)
    if query:
        entries_with_match = [
            e
            for e in entries_with_match
            if query.lower() in e["entry"].game_name.lower()
        ]

    # Apply system filter
    if system_slugs:
        entries_with_match = [
            e for e in entries_with_match if e["entry"].system_slug in system_slugs
        ]

    # Apply genre filter (requires matched game with genres)
    if genre_slugs:
        entries_with_match = [
            e
            for e in entries_with_match
            if e["matched_game"]
            and any(g.slug in genre_slugs for g in e["matched_game"].genres.all())
        ]

    # Apply rating filter (requires matched game with rating)
    if rating_op and rating_min:
        try:
            min_val = int(rating_min)
            filtered = []
            for e in entries_with_match:
                if not e["matched_game"] or not e["matched_game"].rating:
                    continue
                rating = e["matched_game"].rating
                if rating_op == "gte" and rating >= min_val:
                    filtered.append(e)
                elif rating_op == "lte" and rating <= min_val:
                    filtered.append(e)
                elif rating_op == "eq" and rating == min_val:
                    filtered.append(e)
                elif rating_op == "between" and rating_max:
                    max_val = int(rating_max)
                    if min_val <= rating <= max_val:
                        filtered.append(e)
            entries_with_match = filtered
        except (ValueError, TypeError):
            pass

    # Apply status filter
    if status_filter == "in_library":
        entries_with_match = [e for e in entries_with_match if e["has_roms"]]
    elif status_filter == "not_in_library":
        entries_with_match = [e for e in entries_with_match if not e["has_roms"]]

    # Calculate matched count for context
    matched_count = sum(1 for e in entries_with_match if e["has_roms"])

    # Sort the list
    if sort == "name":
        entries_with_match.sort(
            key=lambda x: x["entry"].game_name.lower(), reverse=(order == "desc")
        )
    elif sort == "system":
        entries_with_match.sort(
            key=lambda x: x["entry"].system_slug.lower(), reverse=(order == "desc")
        )
    elif sort == "rating":
        entries_with_match.sort(
            key=lambda x: (
                x["matched_game"].rating
                if x["matched_game"] and x["matched_game"].rating
                else -1
            ),
            reverse=(order == "desc"),
        )
    elif sort == "status":
        entries_with_match.sort(key=lambda x: x["has_roms"], reverse=(order == "desc"))
    else:  # position (default)
        entries_with_match.sort(key=lambda x: x["entry"].position)

    # Pagination
    page_size = request.GET.get(
        "page_size", request.session.get("collection_page_size", 25)
    )
    try:
        page_size = int(page_size)
        if page_size not in [25, 50, 100, 200]:
            page_size = 25
    except (ValueError, TypeError):
        page_size = 25

    request.session["collection_page_size"] = page_size

    paginator = Paginator(entries_with_match, page_size)
    page_number = request.GET.get("page", 1)
    page_obj = paginator.get_page(page_number)

    context = {
        "collection": collection,
        "entries": page_obj,
        "page_obj": page_obj,
        "current_page_size": page_size,
        "matched_count": matched_count,
        "total_count": len(entries_with_match),
        "current_sort": sort,
        "current_order": order,
        # Filter state for preserving in pagination
        "filter_query": query,
        "filter_systems": system_slugs,
        "filter_genres": genre_slugs,
        "filter_rating_op": rating_op,
        "filter_rating_min": rating_min,
        "filter_rating_max": rating_max,
        "filter_status": status_filter,
    }
    return render(request, "collections/_collection_entries_content.html", context)


def collection_filter_systems(request, creator, slug):
    """Return systems in a collection with entry counts for filter dropdown.

    Query params:
        q: Optional search query to filter systems
        selected: Comma-separated slugs of currently selected systems
        Other filter params are used to compute accurate counts
    """
    collection = get_object_or_404(Collection, creator=creator, slug=slug)

    query = request.GET.get("q", "").strip()

    # Get system slugs with their entry counts from this collection
    # CollectionEntry.system_slug is a CharField, not a FK, so we aggregate directly
    system_slug_counts = (
        collection.entries.values("system_slug")
        .annotate(entry_count=Count("id"))
        .order_by()
    )
    slug_to_count = {
        item["system_slug"]: item["entry_count"] for item in system_slug_counts
    }

    # Get system objects for these slugs
    systems = System.objects.filter(slug__in=slug_to_count.keys()).order_by("name")

    if query:
        systems = systems.filter(Q(name__icontains=query) | Q(slug__icontains=query))

    # Attach entry counts to each system
    systems_with_counts = []
    for system in systems:
        system.entry_count = slug_to_count.get(system.slug, 0)
        systems_with_counts.append(system)

    context = {
        "systems": systems_with_counts,
        "collection_creator": creator,
        "collection_slug": slug,
    }
    return render(
        request, "collections/_collection_filter_systems_options.html", context
    )


def collection_filter_genres(request, creator, slug):
    """Return genres from matched games in collection for filter dropdown.

    Query params:
        q: Optional search query to filter genres
        selected: Comma-separated slugs of currently selected genres
        system: Comma-separated system slugs to filter counts by
    """
    from library.models import Genre

    collection = get_object_or_404(Collection, creator=creator, slug=slug)

    query = request.GET.get("q", "").strip()
    system_slugs = [s for s in request.GET.get("system", "").split(",") if s]

    # Get matched games for entries in this collection
    entries = collection.entries.all()
    if system_slugs:
        entries = entries.filter(system_slug__in=system_slugs)

    matched_game_ids = []
    for entry in entries:
        matched_game = entry.get_matched_game()
        if matched_game:
            matched_game_ids.append(matched_game.pk)

    # Get genres from matched games with counts
    genres = (
        Genre.objects.filter(games__pk__in=matched_game_ids)
        .annotate(
            game_count=Count(
                "games", filter=Q(games__pk__in=matched_game_ids), distinct=True
            )
        )
        .filter(game_count__gt=0)
        .select_related("parent")
        .order_by("name")
    )

    if query:
        genres = genres.filter(Q(name__icontains=query) | Q(slug__icontains=query))

    # Build hierarchical structure
    parent_genres = [g for g in genres if g.parent is None]
    child_genres = [g for g in genres if g.parent is not None]

    children_map = {}
    for child in child_genres:
        if child.parent_id not in children_map:
            children_map[child.parent_id] = []
        children_map[child.parent_id].append(child)

    parent_genre_ids = {p.pk for p in parent_genres}

    hierarchical_genres = []
    for parent in parent_genres:
        hierarchical_genres.append({"genre": parent, "level": 0})
        for child in children_map.get(parent.pk, []):
            hierarchical_genres.append({"genre": child, "level": 1})

    # Add orphan children
    for parent_id, children in children_map.items():
        if parent_id not in parent_genre_ids:
            for child in children:
                hierarchical_genres.append({"genre": child, "level": 0})

    context = {
        "genres": hierarchical_genres,
    }
    # Reuse library's genre filter template
    return render(request, "library/_filter_genres_options.html", context)


def collection_create(request):
    """Create a new collection."""
    if request.method == "POST":
        name = request.POST.get("name", "").strip()
        # Normalize line endings (\r\n -> \n) to match frontend character count
        description = request.POST.get("description", "").replace("\r\n", "\n").strip()
        creator_input = request.POST.get("creator", "").strip()
        tags_str = request.POST.get("tags", "").strip()

        if not name:
            context = {"error": "Name is required", "is_create": True}
            return render(request, "collections/collection_form.html", context)

        if not creator_input:
            context = {"error": "Creator is required", "is_create": True}
            return render(request, "collections/collection_form.html", context)

        if len(description) > MAX_DESCRIPTION_LENGTH:
            context = {
                "error": f"Description must be {MAX_DESCRIPTION_LENGTH} characters or less",
                "is_create": True,
            }
            return render(request, "collections/collection_form.html", context)

        # Slugify creator for URL-safe value
        creator = slugify(creator_input)
        if not creator:
            context = {
                "error": "Creator must contain URL-safe characters",
                "is_create": True,
            }
            return render(request, "collections/collection_form.html", context)

        slug = slugify(name)
        base_slug = slug
        counter = 1
        # Check uniqueness within creator namespace
        while Collection.objects.filter(creator=creator, slug=slug).exists():
            slug = f"{base_slug}-{counter}"
            counter += 1

        tags = [t.strip() for t in tags_str.split(",") if t.strip()] if tags_str else []

        collection = Collection.objects.create(
            slug=slug,
            name=name,
            description=description,
            creator=creator,
            tags=tags,
        )
        return redirect(
            "romcollections:collection_detail",
            creator=collection.creator,
            slug=collection.slug,
        )

    return render(request, "collections/collection_form.html", {"is_create": True})


def collection_edit(request, creator, slug):
    """Edit an existing collection."""
    collection = get_object_or_404(Collection, creator=creator, slug=slug)

    if request.method == "POST":
        name = request.POST.get("name", "").strip()
        # Normalize line endings (\r\n -> \n) to match frontend character count
        description = request.POST.get("description", "").replace("\r\n", "\n").strip()
        # Creator cannot be changed after creation (it's part of the URL)
        tags_str = request.POST.get("tags", "").strip()

        if not name:
            context = {"collection": collection, "error": "Name is required"}
            return render(request, "collections/collection_form.html", context)

        if len(description) > MAX_DESCRIPTION_LENGTH:
            context = {
                "collection": collection,
                "error": f"Description must be {MAX_DESCRIPTION_LENGTH} characters or less",
                "tags_str": tags_str,
            }
            return render(request, "collections/collection_form.html", context)

        collection.name = name
        collection.description = description
        # Note: creator is not editable - it's part of the URL identity
        collection.tags = (
            [t.strip() for t in tags_str.split(",") if t.strip()] if tags_str else []
        )
        collection.save()

        return redirect(
            "romcollections:collection_detail",
            creator=collection.creator,
            slug=collection.slug,
        )

    context = {
        "collection": collection,
        "is_create": False,
        "tags_str": ", ".join(collection.tags) if collection.tags else "",
    }
    return render(request, "collections/collection_form.html", context)


@require_POST
def collection_delete(request, creator, slug):
    """Delete a collection."""
    collection = get_object_or_404(Collection, creator=creator, slug=slug)

    if collection.is_favorites:
        return HttpResponse("Cannot delete the Favorites collection", status=400)

    collection.delete()

    if request.headers.get("HX-Request"):
        response = HttpResponse()
        response["HX-Redirect"] = reverse("romcollections:collection_list")
        return response

    return redirect("romcollections:collection_list")


@require_POST
def add_entry(request, creator, slug):
    """Add an entry to a collection (HTMX/JSON)."""
    collection = get_object_or_404(Collection, creator=creator, slug=slug)

    game_name = request.POST.get("game_name", "").strip()
    system_slug = request.POST.get("system_slug", "").strip()
    # Normalize line endings (\r\n -> \n) to match frontend character count
    notes = request.POST.get("notes", "").replace("\r\n", "\n").strip()

    if not game_name or not system_slug:
        return HttpResponse("Game name and system are required", status=400)

    if len(notes) > MAX_NOTES_LENGTH:
        return HttpResponse(
            f"Notes must be {MAX_NOTES_LENGTH} characters or less", status=400
        )

    if CollectionEntry.objects.filter(
        collection=collection, game_name__iexact=game_name, system_slug=system_slug
    ).exists():
        return HttpResponse("Entry already exists in collection", status=400)

    max_position = collection.entries.aggregate(Max("position"))["position__max"]
    position = (max_position or 0) + 1

    CollectionEntry.objects.create(
        collection=collection,
        game_name=game_name,
        system_slug=system_slug,
        position=position,
        notes=notes,
    )

    # Return JSON success for AJAX calls
    return JsonResponse(
        {"success": True, "collection_name": collection.name, "game_name": game_name}
    )


@require_POST
def remove_entry(request, creator, slug, pk):
    """Remove an entry from a collection (HTMX)."""
    collection = get_object_or_404(Collection, creator=creator, slug=slug)
    entry = get_object_or_404(CollectionEntry, pk=pk, collection=collection)
    entry.delete()
    return HttpResponse("")


@require_POST
def bulk_remove_entries(request, creator, slug):
    """Remove multiple entries from a collection."""
    collection = get_object_or_404(Collection, creator=creator, slug=slug)

    try:
        data = json.loads(request.body)
        entry_ids = data.get("entry_ids", [])
    except json.JSONDecodeError:
        return JsonResponse({"error": "Invalid JSON"}, status=400)

    if not entry_ids:
        return JsonResponse({"error": "No entries specified"}, status=400)

    deleted_count = CollectionEntry.objects.filter(
        collection=collection, pk__in=entry_ids
    ).delete()[0]

    return JsonResponse({"deleted": deleted_count})


@require_POST
def reorder_entries(request, creator, slug):
    """Reorder entries in a collection (JSON)."""
    collection = get_object_or_404(Collection, creator=creator, slug=slug)

    try:
        data = json.loads(request.body)
        order = data.get("order", [])
    except json.JSONDecodeError:
        return JsonResponse({"error": "Invalid JSON"}, status=400)

    for position, entry_id in enumerate(order):
        CollectionEntry.objects.filter(pk=entry_id, collection=collection).update(
            position=position
        )

    return JsonResponse({"success": True})


@require_POST
def update_entry_notes(request, creator, slug, pk):
    """Update notes for a collection entry (HTMX)."""
    collection = get_object_or_404(Collection, creator=creator, slug=slug)
    entry = get_object_or_404(CollectionEntry, pk=pk, collection=collection)

    # Update notes from modal form
    # Normalize line endings (\r\n -> \n) to match frontend character count
    notes = request.POST.get("notes", "").replace("\r\n", "\n").strip()

    if len(notes) > MAX_NOTES_LENGTH:
        return HttpResponse(
            f"Notes must be {MAX_NOTES_LENGTH} characters or less", status=400
        )

    entry.notes = notes
    entry.save(update_fields=["notes"])

    # Get matched game info for rendering
    matched_game = entry.get_matched_game()
    system = System.objects.filter(slug=entry.system_slug).first()

    context = {
        "entry": entry,
        "matched_game": matched_game,
        "is_matched": matched_game is not None,
        "system": system,
        "collection": collection,
    }

    return render(request, "collections/_entry_card.html", context)


def collection_picker(request):
    """HTMX partial for collection picker dropdown."""
    # Get collections sorted by name, split into personal and community
    # Include Favorites - it's the most common collection to add games to
    all_collections = Collection.objects.all().order_by("-is_favorites", "name")
    personal_collections = [c for c in all_collections if not c.is_community]
    community_collections = [c for c in all_collections if c.is_community]

    # Check for single game
    game_name = request.GET.get("game_name")
    system_slug = request.GET.get("system_slug")

    # Check for bulk games (JSON in query param)
    games_json = request.GET.get("games")

    existing_slugs = set()

    if game_name and system_slug:
        # Single game mode - find collections containing this game
        existing_slugs = set(
            CollectionEntry.objects.filter(
                game_name__iexact=game_name, system_slug=system_slug
            ).values_list("collection__slug", flat=True)
        )
    elif games_json:
        # Bulk mode - find collections containing ALL selected games
        try:
            games = json.loads(games_json)
            if games:
                # Start with all collection slugs
                all_slugs = set(Collection.objects.values_list("slug", flat=True))
                # For each game, get collections it's in, then intersect
                for game in games:
                    game_collections = set(
                        CollectionEntry.objects.filter(
                            game_name__iexact=game.get("game_name", ""),
                            system_slug=game.get("system_slug", ""),
                        ).values_list("collection__slug", flat=True)
                    )
                    all_slugs &= game_collections
                existing_slugs = all_slugs
        except json.JSONDecodeError:
            pass

    # Get favorites collection for default selection
    favorites_collection = Collection.objects.filter(is_favorites=True).first()

    return render(
        request,
        "collections/_collection_picker.html",
        {
            "personal_collections": personal_collections,
            "community_collections": community_collections,
            "existing_collection_slugs": existing_slugs,
            "favorites_collection": favorites_collection,
        },
    )


@require_POST
def bulk_add_entries(request, creator, slug):
    """Add multiple games to a collection (JSON API)."""
    collection = get_object_or_404(Collection, creator=creator, slug=slug)

    try:
        data = json.loads(request.body)
        games = data.get("games", [])
    except json.JSONDecodeError:
        return JsonResponse({"error": "Invalid JSON"}, status=400)

    if not games:
        return JsonResponse({"error": "No games specified"}, status=400)

    added_count = 0
    skipped_count = 0

    max_position = collection.entries.aggregate(Max("position"))["position__max"] or 0

    for game_data in games:
        game_name = game_data.get("game_name", "").strip()
        system_slug = game_data.get("system_slug", "").strip()

        if not game_name or not system_slug:
            continue

        # Skip if already exists
        if CollectionEntry.objects.filter(
            collection=collection, game_name__iexact=game_name, system_slug=system_slug
        ).exists():
            skipped_count += 1
            continue

        max_position += 1
        CollectionEntry.objects.create(
            collection=collection,
            game_name=game_name,
            system_slug=system_slug,
            position=max_position,
        )
        added_count += 1

    return JsonResponse(
        {
            "success": True,
            "added": added_count,
            "skipped": skipped_count,
            "collection_name": collection.name,
        }
    )


def export_collection(request, creator, slug):
    """Export collection as JSON file download."""
    collection = get_object_or_404(Collection, creator=creator, slug=slug)
    data = serialize_export(collection)

    response = JsonResponse(data, json_dumps_params={"indent": 2})
    response["Content-Disposition"] = f'attachment; filename="{collection.slug}.json"'
    return response


@require_POST
def start_export_with_images(request, creator, slug):
    """Start background export with images (returns job ID for polling)."""
    from library.queues import PRIORITY_CRITICAL

    collection = get_object_or_404(Collection, creator=creator, slug=slug)

    # Create export job
    job = ExportJob.objects.create(
        collection=collection,
        task_id=f"pending-{uuid.uuid4().hex}",
    )

    # Queue background task
    job_id = create_collection_export.configure(priority=PRIORITY_CRITICAL).defer(
        export_job_id=job.pk
    )
    job.task_id = str(job_id)
    job.save()

    return JsonResponse({"job_id": job.pk})


def export_status(request, creator, slug):
    """HTMX endpoint to poll export status."""
    job_id = request.GET.get("job_id")
    if not job_id:
        return HttpResponse("Missing job_id", status=400)

    job = get_object_or_404(
        ExportJob, pk=job_id, collection__creator=creator, collection__slug=slug
    )

    context = {"job": job, "collection": job.collection}
    return render(request, "collections/_export_status.html", context)


def download_export(request, creator, slug, job_id):
    """Serve completed export file."""
    from django.http import FileResponse

    job = get_object_or_404(
        ExportJob, pk=job_id, collection__creator=creator, collection__slug=slug
    )

    if job.status != ExportJob.STATUS_COMPLETED:
        return HttpResponse("Export not ready", status=400)

    if not job.file_path or not os.path.exists(job.file_path):
        return HttpResponse("Export file not found", status=404)

    response = FileResponse(
        open(job.file_path, "rb"),
        as_attachment=True,
        filename=job.file_name,
    )
    return response


def import_collection(request):
    """Import collection from JSON file upload or URL.

    For URL imports, redirects to a preview page before committing.
    For file uploads, imports directly (existing behavior).
    """
    import tempfile
    import zipfile

    from romhoard.url_fetch import URLFetchError, fetch_json_from_url

    from .serializers import validate_import_data

    if request.method == "POST":
        url = request.POST.get("url", "").strip()
        uploaded_file = request.FILES.get("file")
        overwrite = request.POST.get("overwrite") == "on"

        # URL import - fetch and show preview
        if url:
            try:
                data = fetch_json_from_url(url)
                validate_import_data(data)

                # Store in session for preview
                token = uuid.uuid4().hex
                request.session[f"import_collection_{token}"] = {
                    "data": data,
                    "url": url,
                    "overwrite": overwrite,
                }

                return redirect("romcollections:import_collection_preview", token=token)

            except URLFetchError as e:
                context = {"error": f"Failed to fetch URL: {e}"}
                return render(request, "collections/import.html", context)
            except SerializerImportError as e:
                context = {"error": str(e)}
                return render(request, "collections/import.html", context)

        # File upload - import directly (existing behavior)
        if not uploaded_file:
            context = {"error": "Please provide a file or URL"}
            return render(request, "collections/import.html", context)

        filename = uploaded_file.name.lower()

        # Handle ZIP files
        if filename.endswith(".zip"):
            # Check file size first (prevent uploading obviously too-large files)
            max_size = getattr(
                settings, "COLLECTION_IMPORT_MAX_SIZE", 1024 * 1024 * 1024
            )
            if uploaded_file.size > max_size:
                size_mb = uploaded_file.size / (1024 * 1024)
                max_mb = max_size / (1024 * 1024)
                context = {
                    "error": f"File too large: {size_mb:.1f}MB (max {max_mb:.0f}MB)"
                }
                return render(request, "collections/import.html", context)

            try:
                # Save to temp file for zipfile to read
                with tempfile.NamedTemporaryFile(delete=False, suffix=".zip") as tmp:
                    for chunk in uploaded_file.chunks():
                        tmp.write(chunk)
                    tmp_path = tmp.name

                try:
                    # Validate ZIP before importing
                    validation = validate_collection_zip(tmp_path)
                    if not validation.is_valid:
                        error_msg = "Import failed: " + "; ".join(validation.errors)
                        context = {"error": error_msg}
                        return render(request, "collections/import.html", context)

                    # Show warnings from validation
                    for warning in validation.warnings:
                        messages.warning(request, warning)

                    # Show info about what's being imported
                    for info in validation.info:
                        messages.info(request, info)

                    result = import_collection_with_images(
                        tmp_path, overwrite=overwrite
                    )
                    collection = result["collection"]

                    # Show warnings
                    for warning in result.get("warnings", []):
                        messages.warning(request, warning)

                    # Show info about imports
                    games_created = result.get("games_created", 0)
                    images_imported = result.get("images_imported", 0)
                    metadata_jobs = result.get("metadata_jobs_queued", 0)

                    info_parts = []
                    if games_created > 0:
                        info_parts.append(f"Created {games_created} new game(s)")
                    if images_imported > 0:
                        info_parts.append(f"imported {images_imported} image(s)")
                    if metadata_jobs > 0:
                        info_parts.append(f"queued {metadata_jobs} metadata lookup(s)")

                    if info_parts:
                        messages.info(request, ". ".join(info_parts) + ".")

                    # Auto-generate cover if collection has matched games with images
                    # and no cover was imported
                    if not collection.has_cover:
                        maybe_generate_cover(collection)

                    return redirect(
                        "romcollections:collection_detail",
                        creator=collection.creator,
                        slug=collection.slug,
                    )
                finally:
                    # Clean up temp file
                    if os.path.exists(tmp_path):
                        os.unlink(tmp_path)

            except zipfile.BadZipFile:
                context = {"error": "Invalid ZIP file"}
                return render(request, "collections/import.html", context)
            except SerializerImportError as e:
                context = {"error": str(e)}
                return render(request, "collections/import.html", context)

        # Handle JSON files
        else:
            try:
                content = uploaded_file.read().decode("utf-8")
                data = json.loads(content)
            except (UnicodeDecodeError, json.JSONDecodeError) as e:
                context = {"error": f"Invalid JSON file: {e}"}
                return render(request, "collections/import.html", context)

            try:
                result = serialize_import(data, overwrite=overwrite)
                collection = result["collection"]

                # Show warnings for invalid systems
                for warning in result.get("warnings", []):
                    messages.warning(request, warning)

                # Show info about games created
                games_created = result.get("games_created", 0)
                metadata_jobs = result.get("metadata_jobs_queued", 0)
                if games_created > 0:
                    if metadata_jobs > 0:
                        messages.info(
                            request,
                            f"Created {games_created} new game(s). "
                            f"Queued {metadata_jobs} metadata lookup(s).",
                        )
                    else:
                        messages.info(request, f"Created {games_created} new game(s).")

                # Auto-generate cover if collection has matched games with images
                maybe_generate_cover(collection)

                return redirect(
                    "romcollections:collection_detail",
                    creator=collection.creator,
                    slug=collection.slug,
                )
            except SerializerImportError as e:
                context = {"error": str(e)}
                return render(request, "collections/import.html", context)

    return render(request, "collections/import.html")


def import_collection_preview(request, token):
    """Preview and confirm URL import for a collection."""
    session_key = f"import_collection_{token}"
    session_data = request.session.get(session_key)

    if not session_data:
        messages.error(request, "Import session expired. Please try again.")
        return redirect("romcollections:import_collection")

    data = session_data["data"]
    url = session_data["url"]
    overwrite = session_data["overwrite"]

    collection_data = data.get("collection", {})
    entries = data.get("entries", [])
    slug = collection_data.get("slug", "")

    # Check if collection already exists
    existing = Collection.objects.filter(slug=slug).first() if slug else None

    if request.method == "POST":
        action = request.POST.get("action")

        if action == "cancel":
            # Clean up session and return to import page
            del request.session[session_key]
            return redirect("romcollections:import_collection")

        if action == "confirm":
            # Check if overwrite was selected on this page
            if request.POST.get("overwrite"):
                overwrite = True

            # Perform the import
            try:
                result = serialize_import(data, overwrite=overwrite)
                collection = result["collection"]

                # Clean up session
                del request.session[session_key]

                # Show warnings for invalid systems
                for warning in result.get("warnings", []):
                    messages.warning(request, warning)

                # Show info about games created
                games_created = result.get("games_created", 0)
                metadata_jobs = result.get("metadata_jobs_queued", 0)
                if games_created > 0:
                    if metadata_jobs > 0:
                        messages.info(
                            request,
                            f"Created {games_created} new game(s). "
                            f"Queued {metadata_jobs} metadata lookup(s).",
                        )
                    else:
                        messages.info(request, f"Created {games_created} new game(s).")

                # Auto-generate cover if collection has matched games with images
                maybe_generate_cover(collection)

                messages.success(
                    request, f"Successfully imported collection '{collection.name}'"
                )
                return redirect(
                    "romcollections:collection_detail",
                    creator=collection.creator,
                    slug=collection.slug,
                )
            except SerializerImportError as e:
                # Clean up session on error too
                del request.session[session_key]
                messages.error(request, str(e))
                return redirect("romcollections:import_collection")

    context = {
        "token": token,
        "url": url,
        "overwrite": overwrite,
        "collection_data": collection_data,
        "entry_count": len(entries),
        "existing_collection": existing,
    }
    return render(request, "collections/import_preview.html", context)


@require_POST
def download_collection(request, creator, slug):
    """Start downloading all matched games in a collection."""
    collection = get_object_or_404(Collection, creator=creator, slug=slug)

    # Get device_id from request if provided
    device_id = None
    try:
        data = json.loads(request.body)
        device_id = data.get("device_id")
    except (json.JSONDecodeError, ValueError):
        pass  # If no valid JSON body, device_id stays None

    matched_games = []
    for entry in collection.entries.all():
        game = entry.get_matched_game()
        if game:
            matched_games.append(game)

    if not matched_games:
        return HttpResponse("No matched games to download", status=400)

    if len(matched_games) == 1:
        return JsonResponse(
            {
                "redirect_url": reverse(
                    "library:download_game", args=[matched_games[0].pk]
                )
            }
        )

    game_ids = [g.pk for g in matched_games]
    job = DownloadJob.objects.create(
        game_ids=game_ids,
        system_slug=collection.slug,
        games_total=len(game_ids),
        task_id="pending",
        device_id=device_id,
    )

    # Enqueue background task with high priority (user is waiting)
    from library.queues import PRIORITY_CRITICAL

    job_id = create_download_bundle.configure(priority=PRIORITY_CRITICAL).defer(
        download_job_id=job.pk
    )
    job.task_id = str(job_id)
    job.save()

    return JsonResponse({"job_id": job.pk})


def download_status(request, creator, slug):
    """HTMX endpoint to poll download status for collection."""
    job_id = request.GET.get("job_id")
    if not job_id:
        return HttpResponse("Missing job_id", status=400)

    job = get_object_or_404(DownloadJob, pk=job_id)
    context = {"job": job}
    return render(request, "library/_download_status.html", context)


@require_POST
def send_collection(request, creator, slug):
    """Start sending all matched games in a collection to a device."""
    collection = get_object_or_404(Collection, creator=creator, slug=slug)

    try:
        data = json.loads(request.body)
        device_id = data.get("device_id")
    except json.JSONDecodeError:
        return HttpResponse("Invalid JSON", status=400)

    if not device_id:
        return HttpResponse("Device not selected", status=400)

    device = get_object_or_404(Device, pk=device_id)

    if not device.has_wifi:
        return HttpResponse("Device does not have WiFi", status=400)

    # Update transfer configuration if provided
    transfer_type = data.get("transfer_type")
    transfer_host = data.get("transfer_host")
    transfer_port = data.get("transfer_port")
    transfer_user = data.get("transfer_user")
    transfer_password = data.get("transfer_password")

    if transfer_type or transfer_host or transfer_user or transfer_password:
        device.transfer_type = transfer_type or device.transfer_type
        device.transfer_host = transfer_host or device.transfer_host
        device.transfer_port = (
            int(transfer_port) if transfer_port else device.transfer_port
        )
        device.transfer_user = transfer_user or device.transfer_user
        device.transfer_password = transfer_password or device.transfer_password
        device.save()

    if not device.has_transfer_config:
        return HttpResponse("Device has no transfer configuration", status=400)

    # Get matched games
    matched_games = []
    for entry in collection.entries.all():
        game = entry.get_matched_game()
        if game:
            matched_games.append(game)

    if not matched_games:
        return HttpResponse("No matched games to send", status=400)

    # Create SendJob
    game_ids = [g.pk for g in matched_games]

    # Count total ROM files to upload
    files_total = ROM.objects.filter(rom_set__game__in=matched_games).count()

    if files_total == 0:
        return HttpResponse("No ROM files to upload", status=400)

    job = SendJob.objects.create(
        game_ids=game_ids,
        device=device,
        files_total=files_total,
        task_id="pending",
    )

    # Enqueue with high priority
    from library.queues import PRIORITY_CRITICAL

    task_id = run_send_upload.configure(priority=PRIORITY_CRITICAL).defer(
        send_job_id=job.pk
    )
    job.task_id = str(task_id)
    job.save()

    return JsonResponse({"job_id": job.pk})


# ============================================================================
# Cover Image Endpoints
# ============================================================================


def serve_cover(request, creator, slug):
    """Serve collection cover image."""
    collection = get_object_or_404(Collection, creator=creator, slug=slug)

    if not collection.has_cover:
        return HttpResponse("No cover image", status=404)

    # Handle race condition where file may be deleted between check and open
    try:
        return FileResponse(
            open(collection.cover_image_path, "rb"),
            content_type="image/png",
        )
    except FileNotFoundError:
        # File was deleted between has_cover check and open
        # Update the DB to reflect reality
        collection.has_cover = False
        collection.cover_image_path = ""
        collection.cover_source = Collection.COVER_SOURCE_NONE
        collection.save(update_fields=["has_cover", "cover_image_path", "cover_source"])
        return HttpResponse("Cover image not found", status=404)


@require_POST
def upload_cover(request, creator, slug):
    """Handle cover image upload, start processing job."""
    import tempfile

    from library.queues import PRIORITY_CRITICAL

    from .tasks import process_cover_upload

    collection = get_object_or_404(Collection, creator=creator, slug=slug)

    uploaded_file = request.FILES.get("cover_image")
    if not uploaded_file:
        return JsonResponse({"error": "No file uploaded"}, status=400)

    # Validate file size (10MB max)
    max_size = 10 * 1024 * 1024
    if uploaded_file.size > max_size:
        return JsonResponse({"error": "File too large (max 10MB)"}, status=400)

    # Validate file type
    content_type = uploaded_file.content_type
    if not content_type or not content_type.startswith("image/"):
        return JsonResponse({"error": "File must be an image"}, status=400)

    # Save to temp file
    suffix = (
        "." + uploaded_file.name.rsplit(".", 1)[-1]
        if "." in uploaded_file.name
        else ".png"
    )
    with tempfile.NamedTemporaryFile(delete=False, suffix=suffix) as tmp:
        for chunk in uploaded_file.chunks():
            tmp.write(chunk)
        tmp_path = tmp.name

    # Check for existing pending/running cover job
    existing_job = collection.cover_jobs.filter(
        status__in=[CoverJob.STATUS_PENDING, CoverJob.STATUS_RUNNING]
    ).first()
    if existing_job:
        # Cancel existing job
        existing_job.status = CoverJob.STATUS_FAILED
        existing_job.error = "Cancelled by new upload"
        existing_job.save()

    # Create cover job
    job = CoverJob.objects.create(
        collection=collection,
        task_id=f"pending-{uuid.uuid4().hex}",
        job_type=CoverJob.JOB_TYPE_UPLOAD,
        upload_path=tmp_path,
    )

    # Queue background task
    job_id = process_cover_upload.configure(priority=PRIORITY_CRITICAL).defer(
        cover_job_id=job.pk
    )
    job.task_id = str(job_id)
    job.save()

    return JsonResponse({"job_id": job.pk})


@require_POST
def generate_cover(request, creator, slug):
    """Start cover generation job."""
    from library.queues import PRIORITY_NORMAL

    from .tasks import generate_collection_cover

    collection = get_object_or_404(Collection, creator=creator, slug=slug)

    # Get image type from request
    try:
        data = json.loads(request.body) if request.body else {}
    except json.JSONDecodeError:
        data = {}

    image_type = (
        data.get("image_type")
        or request.POST.get("image_type")
        or Collection.COVER_TYPE_COVER
    )

    # Validate image type
    valid_types = [choice[0] for choice in Collection.COVER_TYPE_CHOICES]
    if image_type not in valid_types:
        return JsonResponse(
            {"error": f"Invalid image type. Must be one of: {', '.join(valid_types)}"},
            status=400,
        )

    # Check for existing pending/running cover job
    existing_job = collection.cover_jobs.filter(
        status__in=[CoverJob.STATUS_PENDING, CoverJob.STATUS_RUNNING]
    ).first()
    if existing_job:
        return JsonResponse(
            {"error": "Cover job already in progress", "job_id": existing_job.pk},
            status=409,
        )

    # Create cover job
    job = CoverJob.objects.create(
        collection=collection,
        task_id=f"pending-{uuid.uuid4().hex}",
        job_type=CoverJob.JOB_TYPE_GENERATE,
        image_type=image_type,
    )

    # Queue background task
    job_id = generate_collection_cover.configure(priority=PRIORITY_NORMAL).defer(
        cover_job_id=job.pk
    )
    job.task_id = str(job_id)
    job.save()

    return JsonResponse({"job_id": job.pk})


@require_POST
def remove_cover(request, creator, slug):
    """Delete collection cover."""
    collection = get_object_or_404(Collection, creator=creator, slug=slug)

    collection.delete_cover()

    # Check if HTMX request
    if request.headers.get("HX-Request"):
        return render(
            request,
            "collections/_cover_section.html",
            {
                "collection": collection,
            },
        )

    return redirect("romcollections:collection_edit", creator=creator, slug=slug)


@require_GET
def cover_status(request, creator, slug):
    """HTMX endpoint to poll cover job status."""
    job_id = request.GET.get("job_id")
    if not job_id:
        return HttpResponse("Missing job_id", status=400)

    job = get_object_or_404(
        CoverJob, pk=job_id, collection__creator=creator, collection__slug=slug
    )

    context = {"job": job, "collection": job.collection}
    return render(request, "collections/_cover_status.html", context)
