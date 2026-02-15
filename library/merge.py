"""
Game deduplication and merge utilities.

Provides functions for:
- Finding existing games by hash, screenscraper_id, or case-insensitive name
- Selecting the canonical game when duplicates are found
- Merging duplicate games into a canonical one
"""

import logging

from django.db import transaction
from django.db.models import Count

from library.models import Game, ROM, System

logger = logging.getLogger(__name__)


# Name source priority (higher = more authoritative)
NAME_SOURCE_PRIORITY = {
    "NoIntros": 100,
    "Redump": 90,
    Game.SOURCE_HASHEOUS: 80,
    Game.SOURCE_SCREENSCRAPER: 70,
    Game.SOURCE_COLLECTION: 60,
    Game.SOURCE_FILENAME: 50,
    Game.SOURCE_MANUAL: 40,
}


def _romsets_compatible_for_merge(romset1, romset2) -> bool:
    """Check if two ROMSets can be merged based on content types.

    ROMSets should NOT be merged if:
    - One contains only base game ROMs and the other contains only update/DLC ROMs

    Args:
        romset1: First ROMSet to compare
        romset2: Second ROMSet to compare

    Returns:
        True if ROMSets can be merged, False otherwise
    """
    # Get content types from both ROMSets
    types1 = set(romset1.roms.values_list("content_type", flat=True).distinct())
    types2 = set(romset2.roms.values_list("content_type", flat=True).distinct())

    # Remove empty strings (no content type info)
    types1.discard("")
    types2.discard("")

    # If no content type info on either, assume compatible
    if not types1 or not types2:
        return True

    # Check if one is base-only and other is update/dlc-only
    has_base_1 = "base" in types1
    has_base_2 = "base" in types2
    has_update_or_dlc_1 = bool(types1.intersection({"update", "dlc"}))
    has_update_or_dlc_2 = bool(types2.intersection({"update", "dlc"}))

    # Incompatible if one is base-only and other is update/dlc-only
    if (has_base_1 and not has_update_or_dlc_1 and not has_base_2 and has_update_or_dlc_2):
        return False
    if (has_base_2 and not has_update_or_dlc_2 and not has_base_1 and has_update_or_dlc_1):
        return False

    return True


def find_existing_game(
    name: str,
    system: System,
    crc32: str = "",
    sha1: str = "",
    screenscraper_id: int | None = None,
) -> Game | None:
    """
    Find an existing game by multiple criteria.

    Check order (returns first match):
    1. Hash match: Find ROM with same CRC32 or SHA1, return its game
    2. ScreenScraper ID match: Find game with same screenscraper_id + system
    3. Case-insensitive name match: Find game with same name (iexact) + system

    Args:
        name: Game name to search for
        system: System to search within
        crc32: Optional CRC32 hash to match
        sha1: Optional SHA1 hash to match
        screenscraper_id: Optional ScreenScraper game ID to match

    Returns:
        Existing Game or None if no match found
    """
    # 1. Hash match - most reliable
    if crc32:
        rom = ROM.objects.filter(crc32=crc32, rom_set__game__system=system).first()
        if rom:
            logger.debug(f"Found game by CRC32 {crc32}: {rom.game}")
            return rom.game

    if sha1:
        rom = ROM.objects.filter(sha1=sha1, rom_set__game__system=system).first()
        if rom:
            logger.debug(f"Found game by SHA1 {sha1}: {rom.game}")
            return rom.game

    # 2. ScreenScraper ID match
    if screenscraper_id:
        game = Game.objects.filter(
            screenscraper_id=screenscraper_id, system=system
        ).first()
        if game:
            logger.debug(f"Found game by ScreenScraper ID {screenscraper_id}: {game}")
            return game

    # 3. Case-insensitive name match
    game = Game.objects.filter(name__iexact=name, system=system).first()
    if game:
        logger.debug(f"Found game by case-insensitive name '{name}': {game}")
        return game

    return None


def select_canonical_game(games: list[Game]) -> Game:
    """
    Select the best game to keep when merging duplicates.

    Priority (in order):
    1. Has screenscraper_id set
    2. Has metadata_updated_at set
    3. Has more images
    4. Higher-authority name_source
    5. Lower pk (older record)

    Args:
        games: List of duplicate Game objects

    Returns:
        The game to keep as canonical
    """
    if len(games) == 1:
        return games[0]

    def score_game(game: Game) -> tuple:
        """Higher score = better candidate for canonical."""
        has_ss_id = 1 if game.screenscraper_id else 0
        has_metadata = 1 if game.metadata_updated_at else 0
        image_count = game.images.count()
        name_priority = NAME_SOURCE_PRIORITY.get(game.name_source, 0)
        # Negate pk so lower pk = higher score
        pk_score = -game.pk

        return (has_ss_id, has_metadata, image_count, name_priority, pk_score)

    return max(games, key=score_game)


@transaction.atomic
def merge_games(canonical: Game, duplicate: Game) -> dict:
    """
    Merge a duplicate game into the canonical game.

    Moves all related objects from duplicate to canonical:
    - ROMSets (and their ROMs)
    - GameImages
    - MetadataJobs

    Args:
        canonical: The game to keep
        duplicate: The game to merge and delete

    Returns:
        Summary dict with counts of merged objects
    """
    if canonical.pk == duplicate.pk:
        raise ValueError("Cannot merge a game with itself")

    if canonical.system_id != duplicate.system_id:
        raise ValueError("Cannot merge games from different systems")

    summary = {
        "romsets_moved": 0,
        "roms_moved": 0,
        "images_moved": 0,
        "images_deleted": 0,
        "metadata_jobs_moved": 0,
    }

    logger.info(
        f"Merging game '{duplicate}' (pk={duplicate.pk}) into '{canonical}' (pk={canonical.pk})"
    )

    # 1. Move ROMSets (handle region/revision conflicts)
    for dup_romset in duplicate.rom_sets.all():
        # Check if canonical already has a ROMSet with same region/revision
        existing_romset = canonical.rom_sets.filter(
            region=dup_romset.region, revision=dup_romset.revision
        ).first()

        if existing_romset and _romsets_compatible_for_merge(existing_romset, dup_romset):
            # Move ROMs to existing ROMSet
            roms_count = dup_romset.roms.count()
            dup_romset.roms.update(rom_set=existing_romset)
            summary["roms_moved"] += roms_count
            logger.debug(
                f"Moved {roms_count} ROMs from duplicate ROMSet to existing "
                f"'{existing_romset.region}' '{existing_romset.revision}'"
            )
            # Delete empty ROMSet
            dup_romset.delete()
        elif existing_romset:
            # ROMSets have same region/revision but are incompatible (e.g., base vs update)
            # Keep them separate by using a unique revision based on content type
            content_types = set(dup_romset.roms.values_list("content_type", flat=True))
            content_types.discard("")
            content_label = "-".join(sorted(content_types)) if content_types else "merged"
            dup_romset.game = canonical
            dup_romset.revision = f"({content_label})"
            dup_romset.save()
            summary["romsets_moved"] += 1
            summary["roms_moved"] += dup_romset.roms.count()
            logger.debug(
                f"Moved ROMSet '{dup_romset.region}' to canonical with unique revision"
            )
        else:
            # Move entire ROMSet to canonical
            dup_romset.game = canonical
            dup_romset.save()
            summary["romsets_moved"] += 1
            summary["roms_moved"] += dup_romset.roms.count()
            logger.debug(
                f"Moved ROMSet '{dup_romset.region}' '{dup_romset.revision}' to canonical"
            )

    # 2. Move GameImages (delete duplicates by type)
    canonical_image_types = set(canonical.images.values_list("image_type", flat=True))

    for dup_image in duplicate.images.all():
        if dup_image.image_type in canonical_image_types:
            # Delete duplicate image (keep canonical's)
            dup_image.delete()
            summary["images_deleted"] += 1
            logger.debug(f"Deleted duplicate image type '{dup_image.image_type}'")
        else:
            # Move image to canonical
            dup_image.game = canonical
            dup_image.save()
            summary["images_moved"] += 1
            canonical_image_types.add(dup_image.image_type)
            logger.debug(f"Moved image type '{dup_image.image_type}' to canonical")

    # 3. Move MetadataJobs
    jobs_count = duplicate.metadata_jobs.count()
    duplicate.metadata_jobs.update(game=canonical)
    summary["metadata_jobs_moved"] = jobs_count

    # 4. Update canonical with duplicate's metadata if canonical is missing it
    if duplicate.screenscraper_id and not canonical.screenscraper_id:
        canonical.screenscraper_id = duplicate.screenscraper_id
        logger.debug(
            f"Copied screenscraper_id {duplicate.screenscraper_id} to canonical"
        )

    # Copy individual metadata fields if canonical is missing them
    metadata_fields = [
        "description",
        "release_date",
        "developer",
        "publisher",
        "players",
        "rating",
        "rating_source",
        "metadata_updated_at",
    ]
    copied_fields = []
    for field in metadata_fields:
        dup_value = getattr(duplicate, field)
        can_value = getattr(canonical, field)
        # Copy if duplicate has value and canonical doesn't
        if dup_value and not can_value:
            setattr(canonical, field, dup_value)
            copied_fields.append(field)

    # Handle genres M2M separately
    if not canonical.genres.exists() and duplicate.genres.exists():
        canonical.genres.set(duplicate.genres.all())
        copied_fields.append("genres")

    if copied_fields:
        logger.debug(
            f"Copied metadata fields from duplicate to canonical: {copied_fields}"
        )

    # Recalculate default ROMSet now that all ROMSets have been moved
    from .romset_scoring import get_best_romset
    canonical.default_rom_set = get_best_romset(canonical)
    canonical.save()

    # 5. Delete the duplicate game
    duplicate_name = str(duplicate)
    duplicate.delete()
    logger.info(f"Deleted duplicate game '{duplicate_name}'")

    return summary


def find_duplicate_groups_by_screenscraper_id(
    system_slug: str | None = None,
) -> list[list[Game]]:
    """
    Find groups of games with the same screenscraper_id within the same system.

    Args:
        system_slug: Optional system to filter by

    Returns:
        List of lists, each containing games that share the same screenscraper_id
    """
    queryset = Game.objects.exclude(screenscraper_id__isnull=True)

    if system_slug:
        queryset = queryset.filter(system__slug=system_slug)

    # Find screenscraper_ids that appear more than once per system
    duplicates = (
        queryset.values("screenscraper_id", "system")
        .annotate(count=Count("id"))
        .filter(count__gt=1)
    )

    groups = []
    for dup in duplicates:
        games = list(
            Game.objects.filter(
                screenscraper_id=dup["screenscraper_id"], system_id=dup["system"]
            ).order_by("pk")
        )
        groups.append(games)

    return groups


def find_duplicate_groups_by_name_case(
    system_slug: str | None = None,
) -> list[list[Game]]:
    """
    Find groups of games that differ only in case (e.g., 'galaga' vs 'Galaga').

    Args:
        system_slug: Optional system to filter by

    Returns:
        List of lists, each containing games with case-insensitive matching names
    """
    from django.db.models.functions import Lower

    queryset = Game.objects.all()

    if system_slug:
        queryset = queryset.filter(system__slug=system_slug)

    # Find names that appear more than once per system (case-insensitive)
    duplicates = (
        queryset.annotate(name_lower=Lower("name"))
        .values("name_lower", "system")
        .annotate(count=Count("id"))
        .filter(count__gt=1)
    )

    groups = []
    for dup in duplicates:
        games = list(
            Game.objects.filter(
                name__iexact=dup["name_lower"], system_id=dup["system"]
            ).order_by("pk")
        )
        groups.append(games)

    return groups


def find_duplicate_groups_by_hash(system_slug: str | None = None) -> list[list[Game]]:
    """
    Find groups of games whose ROMs share the same hash (CRC32).

    This finds different Game records that have ROMs with identical hashes,
    indicating they are the same game.

    Args:
        system_slug: Optional system to filter by

    Returns:
        List of lists, each containing games whose ROMs share a hash
    """
    queryset = ROM.objects.exclude(crc32="")

    if system_slug:
        queryset = queryset.filter(rom_set__game__system__slug=system_slug)

    # Find CRC32 values that appear in ROMs of different games
    duplicates = (
        queryset.values("crc32", "rom_set__game__system")
        .annotate(game_count=Count("rom_set__game", distinct=True))
        .filter(game_count__gt=1)
    )

    groups = []
    seen_game_sets = set()

    for dup in duplicates:
        game_ids = (
            ROM.objects.filter(
                crc32=dup["crc32"],
                rom_set__game__system_id=dup["rom_set__game__system"],
            )
            .values_list("rom_set__game_id", flat=True)
            .distinct()
        )

        game_ids_tuple = tuple(sorted(game_ids))
        if game_ids_tuple in seen_game_sets:
            continue
        seen_game_sets.add(game_ids_tuple)

        games = list(Game.objects.filter(id__in=game_ids).order_by("pk"))
        if len(games) > 1:
            groups.append(games)

    return groups


def merge_duplicate_group(games: list[Game]) -> dict | None:
    """
    Merge a group of duplicate games into the canonical one.

    Args:
        games: List of duplicate Game objects

    Returns:
        Summary dict with merge results, or None if no merge needed
    """
    if len(games) < 2:
        return None

    canonical = select_canonical_game(games)
    duplicates = [g for g in games if g.pk != canonical.pk]

    total_summary = {
        "canonical": canonical,
        "merged_count": 0,
        "romsets_moved": 0,
        "roms_moved": 0,
        "images_moved": 0,
        "images_deleted": 0,
        "metadata_jobs_moved": 0,
    }

    for duplicate in duplicates:
        summary = merge_games(canonical, duplicate)
        total_summary["merged_count"] += 1
        total_summary["romsets_moved"] += summary["romsets_moved"]
        total_summary["roms_moved"] += summary["roms_moved"]
        total_summary["images_moved"] += summary["images_moved"]
        total_summary["images_deleted"] += summary["images_deleted"]
        total_summary["metadata_jobs_moved"] += summary["metadata_jobs_moved"]

    return total_summary
