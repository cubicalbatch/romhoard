"""Genre creation utilities for maintaining parent-child relationships."""

from django.utils.text import slugify

from library.models import Genre


def get_or_create_genre_with_parent(genre_name: str) -> Genre:
    """Get or create a genre, setting its parent if it's a subgenre.

    For genres with " / " in the name (e.g., "Action / Labyrinth"),
    this ensures the parent genre exists and links them.

    Args:
        genre_name: The full genre name (e.g., "Action / Labyrinth")

    Returns:
        The Genre instance (created or existing)
    """
    genre, created = Genre.objects.get_or_create(
        name=genre_name, defaults={"slug": slugify(genre_name)}
    )

    # If it's a subgenre (contains " / "), ensure parent exists and is linked
    if " / " in genre_name and genre.parent is None:
        parent_name = genre_name.rsplit(" / ", 1)[0]
        parent, _ = Genre.objects.get_or_create(
            name=parent_name, defaults={"slug": slugify(parent_name)}
        )
        genre.parent = parent
        genre.save(update_fields=["parent"])

    return genre
