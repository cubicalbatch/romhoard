"""Shared collection search utilities.

This module provides collection search functionality that can be used by both
the romcollections app and the library app (for global search).
"""

from collections import defaultdict

from django.db import connection
from django.db.models import (
    Case,
    Count,
    Exists,
    F,
    IntegerField,
    OuterRef,
    Prefetch,
    Q,
    Value,
    When,
)

from library.models import Game, GameImage, System

from .models import Collection, CollectionEntry

# Search relevance weights (higher = more relevant)
WEIGHT_NAME = 1000  # Title match is most important
WEIGHT_TAGS = 750  # Tags are explicit categorization
WEIGHT_GENRE = 600  # Genre match (games with matching genre)
WEIGHT_DESCRIPTION = 500  # Description provides context
WEIGHT_SYSTEM = 400  # System/platform match
WEIGHT_CREATOR = 250  # Creator name match
WEIGHT_GAME = 100  # Per matching game in collection


def _build_relevance_annotations(query, matching_system_slugs):
    """Build annotations for search relevance scoring.

    Returns a dict of annotations that compute a relevance score based on
    where the query matches (name, tags, description, etc.).
    """
    annotations = {
        "score_name": Case(
            When(name__icontains=query, then=Value(WEIGHT_NAME)),
            default=Value(0),
            output_field=IntegerField(),
        ),
        "score_tags": Case(
            When(tags__icontains=query, then=Value(WEIGHT_TAGS)),
            default=Value(0),
            output_field=IntegerField(),
        ),
        "score_description": Case(
            When(description__icontains=query, then=Value(WEIGHT_DESCRIPTION)),
            default=Value(0),
            output_field=IntegerField(),
        ),
        "score_creator": Case(
            When(creator__icontains=query, then=Value(WEIGHT_CREATOR)),
            default=Value(0),
            output_field=IntegerField(),
        ),
        # Count matching games - each match adds WEIGHT_GAME points
        "game_match_count": Count(
            "entries", filter=Q(entries__game_name__icontains=query)
        ),
    }

    # System match scoring (if query matches any system)
    if matching_system_slugs:
        annotations["score_system"] = Case(
            When(
                Exists(
                    CollectionEntry.objects.filter(
                        collection=OuterRef("pk"),
                        system_slug__in=matching_system_slugs,
                    )
                ),
                then=Value(WEIGHT_SYSTEM),
            ),
            default=Value(0),
            output_field=IntegerField(),
        )
    else:
        annotations["score_system"] = Value(0, output_field=IntegerField())

    # Genre match scoring - check if any game in the collection has a matching genre
    # Uses nested subquery: CollectionEntry -> Game (by name+system) -> Genre
    annotations["score_genre"] = Case(
        When(
            Exists(
                CollectionEntry.objects.filter(
                    collection=OuterRef("pk"),
                ).filter(
                    Exists(
                        Game.objects.filter(
                            name__iexact=OuterRef("game_name"),
                            system__slug=OuterRef("system_slug"),
                            genres__name__icontains=query,
                        )
                    )
                )
            ),
            then=Value(WEIGHT_GENRE),
        ),
        default=Value(0),
        output_field=IntegerField(),
    )

    return annotations


def _annotate_collection_counts(queryset):
    """Annotate collections with entry_count in an efficient query."""
    return queryset.annotate(entry_count_annotated=Count("entries"))


def _compute_matched_counts_bulk(collections):
    """Compute matched_count for multiple collections efficiently.

    Uses a JOIN-based query instead of building OR conditions, which is
    orders of magnitude faster for large numbers of entries.
    """
    if not collections:
        return

    collection_ids = [c.pk for c in collections]

    # Use a raw SQL query with JOIN instead of building thousands of OR conditions
    # This is ~150x faster than the ORM approach with Q() objects
    with connection.cursor() as cursor:
        cursor.execute(
            """
            SELECT ce.collection_id, COUNT(DISTINCT (g.name, s.slug))
            FROM romcollections_collectionentry ce
            JOIN library_game g ON LOWER(g.name) = LOWER(ce.game_name)
            JOIN library_system s ON g.system_id = s.id AND s.slug = ce.system_slug
            JOIN library_romset rs ON g.id = rs.game_id
            WHERE ce.collection_id = ANY(%s)
            GROUP BY ce.collection_id
            """,
            [collection_ids],
        )
        results = {row[0]: row[1] for row in cursor.fetchall()}

    # Attach to collection objects
    for c in collections:
        c.matched_count_annotated = results.get(c.pk, 0)


def _attach_sample_covers_bulk(collections, limit=5):
    """Bulk-fetch sample covers for multiple collections efficiently.

    Uses a JOIN-based query to find matching game IDs, then fetches
    games with prefetched images using the ORM.
    """
    if not collections:
        return

    collection_ids = [c.pk for c in collections]

    # Use raw SQL to find matching game IDs with their collection mapping
    # This avoids building thousands of OR conditions
    with connection.cursor() as cursor:
        cursor.execute(
            """
            SELECT DISTINCT ce.collection_id, g.id as game_id, g.name
            FROM romcollections_collectionentry ce
            JOIN library_game g ON LOWER(g.name) = LOWER(ce.game_name)
            JOIN library_system s ON g.system_id = s.id AND s.slug = ce.system_slug
            WHERE ce.collection_id = ANY(%s)
            ORDER BY ce.collection_id, g.name
            """,
            [collection_ids],
        )
        rows = cursor.fetchall()

    if not rows:
        for c in collections:
            c.sample_covers = []
        return

    # Build mapping: collection_id -> list of game_ids
    collection_game_ids = defaultdict(list)
    all_game_ids = set()
    for collection_id, game_id, _name in rows:
        if len(collection_game_ids[collection_id]) < limit:
            collection_game_ids[collection_id].append(game_id)
            all_game_ids.add(game_id)

    # Fetch all matching games with their images in one query
    games = (
        Game.objects.filter(id__in=all_game_ids)
        .prefetch_related(
            Prefetch(
                "images",
                queryset=GameImage.objects.filter(
                    image_type__in=["cover", "mix", "screenshot", "wheel"]
                ).order_by("image_type"),
            )
        )
        .select_related("system")
    )

    # Build game lookup by ID
    game_lookup = {game.id: game for game in games}

    # Assign sample covers to each collection
    collection_covers = defaultdict(list)
    for collection_id, game_ids in collection_game_ids.items():
        for game_id in game_ids:
            game = game_lookup.get(game_id)
            if not game:
                continue

            # Find best image for this game
            image = None
            images_by_type = {img.image_type: img for img in game.images.all()}
            for image_type in ["cover", "mix", "screenshot", "wheel"]:
                if image_type in images_by_type:
                    image = images_by_type[image_type]
                    break

            if image and len(collection_covers[collection_id]) < limit:
                collection_covers[collection_id].append({"game": game, "image": image})

    # Attach to collection objects
    for c in collections:
        c.sample_covers = collection_covers.get(c.pk, [])


def search_collections(query, limit=6):
    """Search collections by query, return scored/annotated results.

    This function is used by the library's global search to include collection
    results. It uses the same scoring system as the collection search page.

    Args:
        query: Search query string (must be non-empty)
        limit: Maximum collections to return (default 6 for preview)

    Returns:
        List of Collection objects with:
        - entry_count_annotated: Total entries in collection
        - matched_count_annotated: Entries with matching games in library
        - sample_covers: List of dicts with 'game' and 'image' keys
        - relevance: Search relevance score
    """
    if not query:
        return []

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

    # Search by genre - find collections with games that have matching genres
    entry_genre_match = CollectionEntry.objects.filter(
        collection=OuterRef("pk"),
    ).filter(
        Exists(
            Game.objects.filter(
                name__iexact=OuterRef("game_name"),
                system__slug=OuterRef("system_slug"),
                genres__name__icontains=query,
            )
        )
    )
    search_filter |= Exists(entry_genre_match)

    # Build relevance scoring annotations
    relevance_annotations = _build_relevance_annotations(query, matching_system_slugs)

    # Query collections with filter, annotations, and relevance ordering
    collections = (
        Collection.objects.filter(search_filter)
        .annotate(
            **relevance_annotations,
            relevance=(
                F("score_name")
                + F("score_tags")
                + F("score_genre")
                + F("score_description")
                + F("score_creator")
                + F("score_system")
                + (F("game_match_count") * WEIGHT_GAME)
            ),
            entry_count_annotated=Count("entries"),
        )
        .order_by("-relevance", "name")[:limit]
    )

    # Convert to list and attach bulk data
    collections_list = list(collections)
    _compute_matched_counts_bulk(collections_list)
    _attach_sample_covers_bulk(collections_list, limit=5)

    return collections_list
