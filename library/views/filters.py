"""Filter option endpoints for advanced search."""

from django.db.models import Count, Q
from django.shortcuts import render

from ..models import Genre, System


def filter_systems(request):
    """Return systems with game counts for filter dropdown.

    Query params:
        q: Optional search query to filter systems
        selected: Comma-separated slugs of currently selected systems
        genre: Comma-separated genre slugs to filter counts by
        rating_op: Rating operator (gte, lte, eq, between)
        rating_min: Minimum rating value
        rating_max: Maximum rating value (for between)
        search_query: Main search query to filter game counts by

    Returns HTMX partial with checkboxes.
    """
    query = request.GET.get("q", "").strip()
    selected_slugs = [s for s in request.GET.get("selected", "").split(",") if s]

    # Get active genre filter
    genre_slugs = [g for g in request.GET.get("genre", "").split(",") if g]

    # Get active rating filter
    rating_op = request.GET.get("rating_op", "")
    rating_min = request.GET.get("rating_min", "")
    rating_max = request.GET.get("rating_max", "")

    # Get main search query to filter game counts
    search_query = request.GET.get("search_query", "").strip()

    # Build filter conditions for game count
    count_filter = Q(games__rom_sets__isnull=False)

    # Apply search query filter to counts
    if search_query:
        count_filter &= Q(games__name__icontains=search_query) | Q(
            games__genres__name__icontains=search_query
        )

    # Apply genre filter to counts
    if genre_slugs:
        count_filter &= Q(games__genres__slug__in=genre_slugs)

    # Apply rating filter to counts
    if rating_op and rating_min:
        try:
            rating_min_val = int(rating_min)
            if rating_op == "gte":
                count_filter &= Q(games__screenscraper_rating__gte=rating_min_val)
            elif rating_op == "lte":
                count_filter &= Q(games__screenscraper_rating__lte=rating_min_val)
            elif rating_op == "eq":
                count_filter &= Q(games__screenscraper_rating=rating_min_val)
            elif rating_op == "between" and rating_max:
                rating_max_val = int(rating_max)
                count_filter &= Q(
                    games__screenscraper_rating__gte=rating_min_val,
                    games__screenscraper_rating__lte=rating_max_val,
                )
        except (ValueError, TypeError):
            pass

    systems = (
        System.objects.annotate(
            game_count=Count("games", filter=count_filter, distinct=True)
        )
        .filter(game_count__gt=0)
        .order_by("name")
    )

    if query:
        systems = systems.filter(Q(name__icontains=query) | Q(slug__icontains=query))

    context = {
        "systems": systems,
        "selected_slugs": selected_slugs,
    }
    return render(request, "library/_filter_systems_options.html", context)


def filter_genres(request):
    """Return genres with game counts for filter dropdown.

    Query params:
        q: Optional search query to filter genres
        selected: Comma-separated slugs of currently selected genres
        system: Comma-separated system slugs to filter counts by
        rating_op: Rating operator (gte, lte, eq, between)
        rating_min: Minimum rating value
        rating_max: Maximum rating value (for between)
        search_query: Main search query to filter game counts by

    Returns HTMX partial with hierarchical checkboxes.
    """
    query = request.GET.get("q", "").strip()
    selected_slugs = [g for g in request.GET.get("selected", "").split(",") if g]

    # Get active system filter
    system_slugs = [s for s in request.GET.get("system", "").split(",") if s]

    # Get active rating filter
    rating_op = request.GET.get("rating_op", "")
    rating_min = request.GET.get("rating_min", "")
    rating_max = request.GET.get("rating_max", "")

    # Get main search query to filter game counts
    search_query = request.GET.get("search_query", "").strip()

    # Build filter conditions for game count
    count_filter = Q(games__rom_sets__isnull=False)

    # Apply search query filter to counts
    if search_query:
        count_filter &= Q(games__name__icontains=search_query) | Q(
            games__genres__name__icontains=search_query
        )

    # Apply system filter to counts
    if system_slugs:
        count_filter &= Q(games__system__slug__in=system_slugs)

    # Apply rating filter to counts
    if rating_op and rating_min:
        try:
            rating_min_val = int(rating_min)
            if rating_op == "gte":
                count_filter &= Q(games__screenscraper_rating__gte=rating_min_val)
            elif rating_op == "lte":
                count_filter &= Q(games__screenscraper_rating__lte=rating_min_val)
            elif rating_op == "eq":
                count_filter &= Q(games__screenscraper_rating=rating_min_val)
            elif rating_op == "between" and rating_max:
                rating_max_val = int(rating_max)
                count_filter &= Q(
                    games__screenscraper_rating__gte=rating_min_val,
                    games__screenscraper_rating__lte=rating_max_val,
                )
        except (ValueError, TypeError):
            pass

    # Get genres that have at least one game matching the filter
    genres = (
        Genre.objects.annotate(
            game_count=Count("games", filter=count_filter, distinct=True)
        )
        .filter(game_count__gt=0)
        .select_related("parent")
        .order_by("name")
    )

    if query:
        genres = genres.filter(Q(name__icontains=query) | Q(slug__icontains=query))

    # Build hierarchical structure
    # First, get top-level genres (no parent) and children
    parent_genres = [g for g in genres if g.parent is None]
    child_genres = [g for g in genres if g.parent is not None]

    # Build parent -> children map
    children_map = {}
    for child in child_genres:
        if child.parent_id not in children_map:
            children_map[child.parent_id] = []
        children_map[child.parent_id].append(child)

    # Get IDs of parents that are in the result set
    parent_genre_ids = {p.pk for p in parent_genres}

    # Build hierarchical list with indentation info
    hierarchical_genres = []
    for parent in parent_genres:
        hierarchical_genres.append({"genre": parent, "level": 0})
        for child in children_map.get(parent.pk, []):
            hierarchical_genres.append({"genre": child, "level": 1})

    # Add orphan children (children whose parent didn't match the search query)
    # These are shown at top level since their parent isn't in the results
    for parent_id, children in children_map.items():
        if parent_id not in parent_genre_ids:
            for child in children:
                hierarchical_genres.append({"genre": child, "level": 0})

    context = {
        "genres": hierarchical_genres,
        "selected_slugs": selected_slugs,
    }
    return render(request, "library/_filter_genres_options.html", context)
