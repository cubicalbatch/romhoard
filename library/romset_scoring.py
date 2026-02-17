"""ROMSet priority scoring for intelligent default selection.

Assigns scores to ROMSets based on configurable factors like region,
archive type (standalone vs multi-ROM), and availability.

Higher score = better ROMSet for default selection.
"""

from .models import ROM, ROMSet, Setting

# Default region priorities (higher = better)
DEFAULT_REGION_PRIORITIES = {
    "USA": 1000,
    "Europe": 800,
    "Japan": 600,
    "World": 400,
}
DEFAULT_REGION_SCORE = 200

# Archive scoring (higher = better)
LOOSE_FILE_BONUS = 150              # Best: direct file access
SINGLE_ROM_ARCHIVE_BONUS = 100      # Good: small archive to extract
ARCHIVE_PENALTY_PER_ROM = 2         # Penalty multiplier for multi-ROM archives
MAX_ARCHIVE_PENALTY = 75            # Cap to avoid overwhelming region scores

# Legacy alias for backward compatibility
STANDALONE_ARCHIVE_BONUS = SINGLE_ROM_ARCHIVE_BONUS

# Switch content type penalty - disqualify update/DLC-only ROMSets
NO_BASE_GAME_PENALTY = -5000


def get_region_priorities() -> dict[str, int]:
    """Get region priorities - generates scores from ordered list or dict."""
    try:
        setting = Setting.objects.get(key="region_priorities")
        if isinstance(setting.value, list):
            # Generate scores: first=1000, second=900, etc.
            return {region: 1000 - (i * 100) for i, region in enumerate(setting.value)}
        return setting.value
    except Setting.DoesNotExist:
        return DEFAULT_REGION_PRIORITIES


def get_all_known_regions() -> list[str]:
    """Get all known regions: defaults + unique regions from database."""
    from .models import ROMSet

    default_regions = list(DEFAULT_REGION_PRIORITIES.keys())
    db_regions = (
        ROMSet.objects.exclude(region="").values_list("region", flat=True).distinct()
    )
    all_regions = default_regions[:]
    for region in db_regions:
        for r in region.split(","):
            r = r.strip()
            if r and r not in all_regions:
                all_regions.append(r)
    return all_regions


def get_region_score(region: str) -> int:
    """Get score for a region string.

    Handles multi-region strings like "USA, Europe" by taking the highest.
    """
    priorities = get_region_priorities()

    # Handle multi-region strings
    if "," in region:
        regions = [r.strip() for r in region.split(",")]
        return max(priorities.get(r, DEFAULT_REGION_SCORE) for r in regions)

    return priorities.get(region, DEFAULT_REGION_SCORE)


def get_content_type_penalty(rom_set: ROMSet) -> int:
    """Calculate content type penalty for Switch ROMSets.

    Only applies to Nintendo Switch system. ROMSets containing only
    update or DLC ROMs (no base game) receive a large penalty to
    prevent them from being selected as default.

    Args:
        rom_set: ROMSet to evaluate

    Returns:
        0 for non-Switch or ROMSets with base game, negative penalty otherwise
    """
    # Only applies to Switch
    if rom_set.game.system.slug != "switch":
        return 0

    # Single query to get distinct content types
    content_types = set(
        rom_set.roms.values_list("content_type", flat=True).distinct()
    )

    # No content type info available (old data or not NSP files)
    if not content_types or content_types == {""}:
        return 0

    # If we have content type info but no base game, penalize
    if "base" not in content_types:
        return NO_BASE_GAME_PENALTY

    return 0


def get_archive_score(rom_set: ROMSet) -> int:
    """Calculate archive-based score for a ROMSet.

    Tiers:
    - Loose files: +150 (no extraction needed)
    - Single-ROM archives: +100 (small archive)
    - Multi-ROM archives: penalty based on size (capped at -75)

    For multi-ROM ROMSets, returns the worst (minimum) score.
    """
    roms = rom_set.roms.all()
    if not roms.exists():
        return 0

    scores = []
    for rom in roms:
        if not rom.archive_path:
            scores.append(LOOSE_FILE_BONUS)
        else:
            archive_rom_count = ROM.objects.filter(
                archive_path=rom.archive_path
            ).count()
            if archive_rom_count == 1:
                scores.append(SINGLE_ROM_ARCHIVE_BONUS)
            else:
                penalty = min(
                    archive_rom_count * ARCHIVE_PENALTY_PER_ROM,
                    MAX_ARCHIVE_PENALTY
                )
                scores.append(-penalty)

    return min(scores)


def is_standalone_archive(rom_set: ROMSet) -> bool:
    """Check if ROMSet's ROMs are in standalone archives.

    Deprecated: Use get_archive_score() instead for more granular scoring.

    A standalone archive contains only this game's ROMs, making downloads
    faster and cleaner than extracting from multi-game collections.
    """
    return get_archive_score(rom_set) >= SINGLE_ROM_ARCHIVE_BONUS


def calculate_romset_score(rom_set: ROMSet) -> int:
    """Calculate priority score for a ROMSet.

    Higher score = better choice for default selection.
    """
    score = 0

    # Region score
    score += get_region_score(rom_set.region)

    # Archive score (replaces standalone bonus)
    if rom_set.roms.exists():
        score += get_archive_score(rom_set)

    # Switch content type penalty (update/DLC-only ROMSets)
    score += get_content_type_penalty(rom_set)

    return score


def get_best_romset(game) -> ROMSet | None:
    """Get the best ROMSet for a game based on scoring.

    Priority:
    1. Highest-scoring ROMSet with available ROMs
    """
    rom_sets = list(game.rom_sets.prefetch_related("roms"))
    if not rom_sets:
        return None

    # Score all ROMSets and pick the best
    scored = [(rs, calculate_romset_score(rs)) for rs in rom_sets]
    scored.sort(key=lambda x: x[1], reverse=True)

    # Return best scoring that has ROMs
    for rom_set, _ in scored:
        if rom_set.roms.exists():
            return rom_set

    return None


def recalculate_default_romset(game) -> bool:
    """Recalculate and update the default_rom_set for a game.

    Returns True if the default was changed.
    """
    best = get_best_romset(game)
    if best and best != game.default_rom_set:
        game.default_rom_set = best
        game.save(update_fields=["default_rom_set"])
        return True
    return False
