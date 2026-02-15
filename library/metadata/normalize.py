"""Genre normalization for consistent categorization.

This map handles only true synonyms and meaningful renames.
Hierarchy (e.g., "Action / Labyrinth") is preserved via the Genre.parent field.
"""

GENRE_NORMALIZATION_MAP = {
    # RPG abbreviations
    "Role Playing Game": "RPG",
    "Japanese RPG": "JRPG",
    "Dungeon Crawler RPG": "Dungeon Crawler",
    # Meaningful shooter renames (these become top-level genres)
    "Shooter / FPV": "FPS",
    "Shooter / TPV": "Third Person Shooter",
    "Shooter / Run and Gun": "Run and Gun",
    # Board games - spelling normalization
    "Asiatic board game": "Board Game",
    "Board game": "Board Game",
    # Adventure subgenres that are commonly known as standalone genres
    "Adventure / Point and Click": "Point and Click",
    "Adventure / Survival Horror": "Survival Horror",
    "Adventure / Visual Novel": "Visual Novel",
    "Adventure / Text": "Text Adventure",
    # Hunting/Fishing consolidation
    "Fishing": "Hunting and Fishing",
    "Hunting": "Hunting and Fishing",
    # Music -> Rhythm (common rename)
    "Music and Dancing": "Rhythm",
    # Various -> Misc
    "Various": "Misc",
    "Various / Electro- Mechanical": "Misc",
    "Various / Print Club": "Misc",
    "Various / Utilities": "Misc",
    # Horse racing goes to Racing
    "Horse racing": "Racing",
    # Racing format normalization (these don't have " / " so not hierarchical)
    "Racing, Driving": "Racing",
    "Racing FPV": "Racing",
    "Racing TPV": "Racing",
    "Motorcycle race FPV": "Racing",
    "Motorcycle race TPV": "Racing",
}


def normalize_genre(genre_name: str) -> str:
    """
    Normalize a genre name to its canonical form.

    Args:
        genre_name: Raw genre name from ScreenScraper

    Returns:
        Canonical genre name
    """
    return GENRE_NORMALIZATION_MAP.get(genre_name, genre_name)


def normalize_genres(genre_list: list[str]) -> list[str]:
    """
    Normalize a list of genre names, removing duplicates.

    Args:
        genre_list: List of raw genre names

    Returns:
        Deduplicated list of canonical genre names
    """
    seen = set()
    result = []
    for genre in genre_list:
        normalized = normalize_genre(genre)
        if normalized not in seen:
            seen.add(normalized)
            result.append(normalized)
    return result
