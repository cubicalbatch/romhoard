"""Dashboard and game browsing views."""

from itertools import groupby
from operator import attrgetter

from django.core.paginator import Paginator
from django.db.models import Case, Count, F, IntegerField, Q, Value, When
from django.shortcuts import get_object_or_404, render

from romcollections.models import Collection, CollectionEntry
from romcollections.search import search_collections

from ..models import Game, Genre, ROMSet, System


def home(request):
    """Dashboard showing library stats and quick actions."""
    # Get systems with game counts
    systems_with_games = System.objects.annotate(game_count=Count("games")).filter(
        game_count__gt=0
    )

    context = {
        "total_systems": systems_with_games.count(),
        "total_games": Game.objects.count(),
        "total_roms": ROMSet.objects.count(),
    }
    return render(request, "library/home.html", context)


def system_list(request):
    """List all systems with game counts, or search results if q param provided."""
    from ..metadata.screenscraper import screenscraper_available, get_credentials_valid

    query = request.GET.get("q", "").strip()

    if query:
        # Delegate to global_search for full page render
        return global_search(request, full_page=True)

    systems = (
        System.objects.annotate(
            game_count=Count(
                "games", filter=Q(games__rom_sets__isnull=False), distinct=True
            ),
            rom_count=Count("games__rom_sets__roms", distinct=True),
        )
        .filter(game_count__gt=0)
        .order_by("name")
    )

    # Total stats (only count games with romsets)
    total_systems = systems.count()
    total_games = Game.objects.filter(rom_sets__isnull=False).distinct().count()
    total_romsets = ROMSet.objects.count()

    # Credential status for empty state messaging
    has_credentials = screenscraper_available()
    credentials_valid = get_credentials_valid() if has_credentials else None

    context = {
        "systems": systems,
        "total_systems": total_systems,
        "total_games": total_games,
        "total_romsets": total_romsets,
        "has_screenscraper_credentials": has_credentials,
        "screenscraper_credentials_valid": credentials_valid,
    }
    return render(request, "library/system_list.html", context)


def global_search(request, full_page=False):
    """HTMX endpoint for global system/game search with multi-faceted filters.

    Query params:
        q: Text search (name or genre)
        system: Comma-separated system slugs (OR logic)
        genre: Comma-separated genre slugs (OR logic)
        rating_op: Rating operator (gte, lte, eq, between)
        rating_min: Minimum rating value
        rating_max: Maximum rating (only for 'between')
    """
    query = request.GET.get("q", "").strip()
    system_slugs = [s for s in request.GET.get("system", "").split(",") if s]
    genre_slugs = [g for g in request.GET.get("genre", "").split(",") if g]
    rating_op = request.GET.get("rating_op", "").strip()
    rating_min = request.GET.get("rating_min", "").strip()
    rating_max = request.GET.get("rating_max", "").strip()

    # Check if any filters are active
    has_filters = bool(query or system_slugs or genre_slugs or rating_op)

    if not has_filters:
        # Empty query and no filters: restore default system grid
        systems = (
            System.objects.annotate(
                game_count=Count(
                    "games", filter=Q(games__rom_sets__isnull=False), distinct=True
                ),
                rom_count=Count("games__rom_sets__roms", distinct=True),
            )
            .filter(game_count__gt=0)
            .order_by("name")
        )
        if full_page:
            # Full page load - redirect to library without query
            return render(
                request,
                "library/system_list.html",
                {
                    "systems": systems,
                    "total_systems": systems.count(),
                    "total_games": Game.objects.filter(rom_sets__isnull=False)
                    .distinct()
                    .count(),
                    "total_romsets": ROMSet.objects.count(),
                },
            )
        return render(request, "library/_system_grid.html", {"systems": systems})

    # Build base game queryset (only games with romsets)
    games = (
        Game.objects.filter(rom_sets__isnull=False)
        .distinct()
        .select_related("system")
        .prefetch_related("images", "genres")
        .annotate(rom_count=Count("rom_sets", distinct=True))
    )

    # Apply text search filter (name OR genre name)
    if query:
        games = games.filter(
            Q(name__icontains=query) | Q(genres__name__icontains=query)
        ).distinct()

    # Apply system filter (OR logic within systems)
    if system_slugs:
        games = games.filter(system__slug__in=system_slugs)

    # Apply genre filter (OR logic within genres)
    if genre_slugs:
        games = games.filter(genres__slug__in=genre_slugs).distinct()

    # Apply rating filter
    if rating_op and rating_min:
        try:
            min_val = int(rating_min)
            if rating_op == "gte":
                games = games.filter(rating__gte=min_val)
            elif rating_op == "lte":
                games = games.filter(rating__lte=min_val)
            elif rating_op == "eq":
                games = games.filter(rating=min_val)
            elif rating_op == "between" and rating_max:
                max_val = int(rating_max)
                games = games.filter(rating__gte=min_val, rating__lte=max_val)
        except (ValueError, TypeError):
            pass  # Invalid rating values, ignore filter

    # Sort by system name, then game name
    games = games.order_by("system__name", "name")

    # Search systems only if text query is provided (not for filter-only searches)
    matched_systems = []
    if query:
        matched_systems = (
            System.objects.filter(Q(name__icontains=query) | Q(slug__icontains=query))
            .annotate(
                game_count=Count(
                    "games", filter=Q(games__rom_sets__isnull=False), distinct=True
                ),
                rom_count=Count("games__rom_sets__roms", distinct=True),
            )
            .filter(game_count__gt=0)
            .order_by("name")
        )

    # Pagination for games
    page_size = request.GET.get(
        "page_size", request.session.get("global_search_page_size", 25)
    )
    try:
        page_size = int(page_size)
        if page_size not in [25, 50, 100, 200]:
            page_size = 25
    except (ValueError, TypeError):
        page_size = 25
    request.session["global_search_page_size"] = page_size

    paginator = Paginator(games, page_size)
    page_number = request.GET.get("page", 1)
    page_obj = paginator.get_page(page_number)

    # Group current page's games by system
    matched_games_by_system = []
    for system, system_games in groupby(page_obj, key=attrgetter("system")):
        matched_games_by_system.append((system, list(system_games)))

    # Search collections only if text query is provided
    matched_collections = []
    collections_truncated = False
    if query:
        matched_collections = search_collections(query, limit=6)
        collections_truncated = len(matched_collections) >= 6

    # Build active filters context for displaying chips
    active_filters = []
    if system_slugs:
        systems_map = {
            s.slug: s.name for s in System.objects.filter(slug__in=system_slugs)
        }
        for slug in system_slugs:
            active_filters.append(
                {
                    "type": "system",
                    "slug": slug,
                    "name": systems_map.get(slug, slug),
                }
            )
    if genre_slugs:
        genres_map = {
            g.slug: g.name for g in Genre.objects.filter(slug__in=genre_slugs)
        }
        for slug in genre_slugs:
            active_filters.append(
                {
                    "type": "genre",
                    "slug": slug,
                    "name": genres_map.get(slug, slug),
                }
            )
    if rating_op and rating_min:
        op_display = {"gte": ">=", "lte": "<=", "eq": "=", "between": ""}.get(
            rating_op, ""
        )
        if rating_op == "between" and rating_max:
            active_filters.append(
                {
                    "type": "rating",
                    "name": f"Rating {rating_min}-{rating_max}",
                }
            )
        else:
            active_filters.append(
                {
                    "type": "rating",
                    "name": f"Rating {op_display} {rating_min}",
                }
            )

    context = {
        "query": query,
        "matched_systems": matched_systems,
        "matched_games_by_system": matched_games_by_system,
        "page_obj": page_obj,
        "current_page_size": page_size,
        "matched_collections": matched_collections,
        "collections_truncated": collections_truncated,
        # Filter state for persistence
        "active_filters": active_filters,
        "filter_systems": system_slugs,
        "filter_genres": genre_slugs,
        "filter_rating_op": rating_op,
        "filter_rating_min": rating_min,
        "filter_rating_max": rating_max,
    }

    # Full page load - render system_list template with search results
    if full_page:
        context["is_search_results"] = True
        return render(request, "library/system_list.html", context)

    return render(request, "library/_global_search_results.html", context)


def game_list(request, slug):
    """List games for a specific system (only games with romsets)."""
    system = get_object_or_404(System, slug=slug)

    # Sort params: GET updates session, then read from session
    if "sort" in request.GET:
        sort_field = request.GET["sort"]
        if sort_field in ("name", "rating"):
            request.session["games_sort"] = sort_field
    if "order" in request.GET:
        sort_order = request.GET["order"]
        if sort_order in ("asc", "desc"):
            request.session["games_order"] = sort_order
    sort_field = request.session.get("games_sort", "name")
    sort_order = request.session.get("games_order", "asc")

    games = (
        Game.objects.filter(system=system, rom_sets__isnull=False)
        .distinct()
        .annotate(rom_count=Count("rom_sets", distinct=True))
        .prefetch_related("images", "genres")
    )

    # Apply sorting
    if sort_field == "rating":
        if sort_order == "desc":
            games = games.order_by(F("rating").desc(nulls_last=True))
        else:
            games = games.order_by(F("rating").asc(nulls_last=True))
    else:  # name
        if sort_order == "desc":
            games = games.order_by("-name")
        else:
            games = games.order_by("name")

    page_size = request.GET.get("page_size", request.session.get("games_page_size", 50))
    try:
        page_size = int(page_size)
        if page_size not in [50, 100, 200]:
            page_size = 50
    except (ValueError, TypeError):
        page_size = 50
    request.session["games_page_size"] = page_size

    paginator = Paginator(games, page_size)
    page_number = request.GET.get("page", 1)
    page_obj = paginator.get_page(page_number)

    # Collection integration
    # Filter entries to only those matching games we have with romsets for this system
    game_names = (
        Game.objects.filter(system=system, rom_sets__isnull=False)
        .distinct()
        .values_list("name", flat=True)
    )

    total_in_collections = (
        CollectionEntry.objects.filter(
            system_slug=system.slug, game_name__in=game_names
        )
        .values("game_name")
        .distinct()
        .count()
    )

    system_collections = (
        Collection.objects.filter(
            entries__system_slug=system.slug, entries__game_name__in=game_names
        )
        .annotate(
            system_count=Count(
                "entries",
                filter=Q(
                    entries__system_slug=system.slug, entries__game_name__in=game_names
                ),
                distinct=True,
            )
        )
        .order_by("-system_count")
    )

    context = {
        "system": system,
        "games": page_obj,
        "page_obj": page_obj,
        "total_games": paginator.count,
        "total_in_collections": total_in_collections,
        "system_collections": system_collections,
        "current_page_size": page_size,
        "current_sort": sort_field,
        "current_order": sort_order,
    }
    return render(request, "library/game_list.html", context)


def game_search(request, slug):
    """HTMX endpoint for live game search (only games with romsets)."""
    system = get_object_or_404(System, slug=slug)
    query = request.GET.get("q", "").strip()

    # Sort params: GET updates session, then read from session
    if "sort" in request.GET:
        sort_field = request.GET["sort"]
        if sort_field in ("name", "rating"):
            request.session["games_sort"] = sort_field
    if "order" in request.GET:
        sort_order = request.GET["order"]
        if sort_order in ("asc", "desc"):
            request.session["games_order"] = sort_order
    sort_field = request.session.get("games_sort", "name")
    sort_order = request.session.get("games_order", "asc")

    games = (
        Game.objects.filter(system=system, rom_sets__isnull=False)
        .distinct()
        .annotate(rom_count=Count("rom_sets", distinct=True))
        .prefetch_related("images", "genres")
    )

    # Genre filter
    genre_param = request.GET.get("genre", "")
    if genre_param:
        genre_slugs = [s.strip() for s in genre_param.split(",") if s.strip()]
        if genre_slugs:
            games = games.filter(genres__slug__in=genre_slugs).distinct()

    # Rating filter
    rating_op = request.GET.get("rating_op", "")
    rating_min = request.GET.get("rating_min")
    rating_max = request.GET.get("rating_max")
    if rating_op and rating_min:
        try:
            min_val = int(rating_min)
            if rating_op == "gte":
                games = games.filter(rating__gte=min_val)
            elif rating_op == "lte":
                games = games.filter(rating__lte=min_val)
            elif rating_op == "eq":
                games = games.filter(rating=min_val)
            elif rating_op == "between" and rating_max:
                max_val = int(rating_max)
                games = games.filter(rating__gte=min_val, rating__lte=max_val)
        except (ValueError, TypeError):
            pass

    # Apply sorting
    if sort_field == "rating":
        if sort_order == "desc":
            games = games.order_by(F("rating").desc(nulls_last=True))
        else:
            games = games.order_by(F("rating").asc(nulls_last=True))
    else:  # name
        if sort_order == "desc":
            games = games.order_by("-name")
        else:
            games = games.order_by("name")

    # Apply name filter if query present
    if query:
        games = games.filter(name__icontains=query)

    # Determine if we're in search/filter mode (any filter active means no pagination)
    has_filters = query or genre_param or (rating_op and rating_min)

    if has_filters:
        # Search/filter mode: show all results without pagination
        context = {
            "games": games,
            "system": system,
            "search_mode": True,
            "current_sort": sort_field,
            "current_order": sort_order,
        }
    else:
        # No search/filters: return paginated results
        page_size = request.GET.get(
            "page_size", request.session.get("games_page_size", 50)
        )
        try:
            page_size = int(page_size)
            if page_size not in [50, 100, 200]:
                page_size = 50
        except (ValueError, TypeError):
            page_size = 50

        paginator = Paginator(games, page_size)
        page_number = request.GET.get("page", 1)
        page_obj = paginator.get_page(page_number)
        context = {
            "games": page_obj,
            "page_obj": page_obj,
            "system": system,
            "search_mode": False,
            "current_page_size": page_size,
            "current_sort": sort_field,
            "current_order": sort_order,
        }

    return render(request, "library/_game_list_content.html", context)


def game_detail(request, pk):
    """Show game details with all ROM variants and images."""
    from ..metadata.screenscraper import screenscraper_available

    game = get_object_or_404(Game, pk=pk)
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

    # Collection context is now passed via URL (/collections/{slug}/{game_pk}/)
    # not session-based tracking
    from_collection = None

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
        "from_collection": from_collection,
        "is_favorite": is_favorite,
        "screenscraper_configured": screenscraper_available(),
    }
    return render(request, "library/game_detail.html", context)
