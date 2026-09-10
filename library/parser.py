"""ROM filename parser.

Parses ROM filenames into structured components (name, region, revision, tags).
"""

import json
import logging
import re
from pathlib import Path

logger = logging.getLogger(__name__)


def _load_region_aliases() -> dict[str, str]:
    """Load region aliases from JSON config file."""
    config_path = Path(__file__).parent / "regions.json"
    try:
        with open(config_path) as f:
            data = json.load(f)
            return data.get("region_aliases", {})
    except (FileNotFoundError, json.JSONDecodeError) as e:
        logger.warning(f"Could not load regions.json: {e}, using defaults")
        # Fallback to minimal defaults
        return {
            "usa": "USA",
            "us": "USA",
            "europe": "Europe",
            "eu": "Europe",
            "japan": "Japan",
            "jp": "Japan",
            "world": "World",
        }


# Known regions with their variations (case-insensitive matching)
# Loaded from regions.json config file
REGION_ALIASES = _load_region_aliases()

# Pattern to match revision strings
REVISION_PATTERN = re.compile(r"^(rev\s*[a-z0-9]+|v\d+(\.\d+)*)$", re.IGNORECASE)

# Pattern to match ROM number prefix at start of filename: "123 - GameName" or "123. GameName"
ROM_NUMBER_PATTERN = re.compile(r"^(\d+)\s*(?:[-.])\s+")

# Pattern to match disc/track numbers in parentheses
# Matches: (Disc 1), (Track 2), (Disc 1 of 2), etc.
DISC_PATTERN = re.compile(r"^(?:disc|track)\s*(\d+)", re.IGNORECASE)

# Pattern to match dash-separated disc/track indicators
# Matches: " - CD1", " - Disc 2", " - Track 1", etc.
DASH_DISC_PATTERN = re.compile(r"\s*-\s*(?:cd|disc|track)\s*(\d+)\s*$", re.IGNORECASE)

# Compound extensions that end with image suffix but are ROMs
COMPOUND_EXTENSIONS = {".p8.png"}

# Date prefix pattern: "1984-11-30 Excitebike"
DATE_PREFIX_PATTERN = re.compile(r"^(\d{4}-\d{2}-\d{2})\s+")

# Rank number prefix without dashes: "089 Ice Climber" (exempting "007 James Bond")
RANK_PREFIX_PATTERN = re.compile(r"^(?!007\s+(?:James|Bond)\b)(\d{2,3})\s+(?=[A-Za-z])")

# Hardware prefix pattern: "2C03 Pinball", "2C04-01 Gradius"
HARDWARE_PREFIX_PATTERN = re.compile(r"^(2C0\d(?:-\d+)?)\s+", re.IGNORECASE)

# VS arcade prefix pattern: "VS. Duck Hunt"
VS_PREFIX_PATTERN = re.compile(r"^(VS\.)\s+", re.IGNORECASE)

# Inverted article pattern at end of base name: "Berenstain Bears' Camping Adventure, The"
INVERTED_ARTICLE_PATTERN = re.compile(r"^(.*?),\s*(The|A|An)$", re.IGNORECASE)

# Retail patch and QoL mod suffix pattern outside brackets
PATCH_SUFFIX_PATTERN = re.compile(
    r"(?:\s*[-–—]\s*|\s+)("
    r"PAL-to-NTSC\s*(?:\(?60Hz\)?)?(?:\s+Patched)?"
    r"|60Hz\s+Patched"
    r"|Save\s*Patched"
    r"|Savepatch"
    r"|Save\s*Patch"
    r"|\+Trainer"
    r"|Trainer"
    r"|Trained"
    r"|Bug-Fixed"
    r"|Bugfix"
    r"|Fixed"
    r"|Fix"
    r"|Improvement"
    r"|Speed-Up"
    r"|Enhanced"
    r"|Uncensored"
    r"|PTBR\+Save(?:\+Bugfixes)?"
    r"|MSX2SMS\s+Hack"
    r"|NES2PCE"
    r")\s*$",
    re.IGNORECASE,
)

# Version and author suffix pattern outside brackets: "v0.1 Revo", "b1 nextvolume", "1.02 Final"
VERSION_AUTHOR_PATTERN = re.compile(
    r"(?:\s*[-–—]\s*|\s+)("
    r"(?:v\d+(?:\.\d+)*[a-z]?|b\d+|\d+(?:\.\d+)+[a-z]?)\s+(?:Final|[A-Za-z0-9_.-]+(?:\s+[A-Za-z0-9_.-]+)*)"
    r"|v\d+(?:\.\d+)+[a-z]"
    r")\s*$",
    re.IGNORECASE,
)

# Trailing Hack suffix pattern outside brackets: "Aero Fighters Hack"
HACK_SUFFIX_PATTERN = re.compile(
    r"(?:\s*[-–—]\s*|\s+)(Hacks?)\s*$",
    re.IGNORECASE,
)


def get_stem_and_extension(filename: str) -> tuple[str, str]:
    """Get filename stem and extension, handling compound extensions."""
    # Extract just the filename if a path was passed
    basename = Path(filename).name
    filename_lower = basename.lower()
    for compound in COMPOUND_EXTENSIONS:
        if filename_lower.endswith(compound):
            return basename[: -len(compound)], compound.lower()
    return Path(basename).stem, Path(basename).suffix.lower()


def parse_rom_filename(filename: str) -> dict:
    """
    Parse a ROM filename into components.

    Args:
        filename: The ROM filename (e.g., "Advance Wars (USA) (Rev 1).gba")

    Returns:
        dict with keys:
            - name: Base game name (str)
            - region: Normalized region name (str, may be empty)
            - revision: Revision string (str, may be empty)
            - tags: List of other tags (list of str)
            - extension: File extension, lowercased (str)
            - rom_number: ROM number if present (str, may be empty)
    """
    # Get extension and stem, handling compound extensions
    name_without_ext, extension = get_stem_and_extension(filename)

    # Check for ROM number prefix at start of filename
    rom_number = ""
    rom_match = ROM_NUMBER_PATTERN.match(name_without_ext)
    if rom_match:
        rom_number = rom_match.group(1)
        # Remove the ROM number prefix from the name
        name_without_ext = ROM_NUMBER_PATTERN.sub("", name_without_ext).strip()

    # Initialize disc variable
    disc = None

    # First check for dash-separated disc pattern at the end
    dash_disc_match = DASH_DISC_PATTERN.search(name_without_ext)
    if dash_disc_match:
        base_name = name_without_ext[: dash_disc_match.start()].strip()
        disc = int(dash_disc_match.group(1))
        # Remove the disc part from remainder processing
        name_without_ext = base_name
        remainder = ""
    else:
        # Extract base name
        match = re.search(r"[\(\[]", name_without_ext)
        if match:
            # Check if we have a name before the first tag
            prefix = name_without_ext[: match.start()].strip()
            remainder = name_without_ext[match.start() :]

            if prefix:
                base_name = prefix
            else:
                # Name starts with a tag (e.g. "[BIOS] Game Name")
                # Remove all tags to find the name
                # Matches (content) or [content]
                base_name = re.sub(
                    r"[\(\[][^\)\]]+[\)\]]", "", name_without_ext
                ).strip()

                # If removing tags leaves nothing, fallback to original name
                if not base_name:
                    base_name = name_without_ext
        else:
            base_name = name_without_ext.strip()
            remainder = ""

    # Find all parenthetical and bracketed content
    # Pattern matches (content) or [content]
    tag_pattern = re.compile(r"[\(\[]([^\)\]]+)[\)\]]")

    # Clean up base name (handle "The" articles, trim whitespace, etc.)
    base_name = base_name.replace("_", " ").strip(" -")

    extra_prefix_tags: list[str] = []
    extra_suffix_tags: list[str] = []
    extracted_revision = ""

    # a) Date prefixes: e.g. "1984-11-30 Excitebike"
    date_match = DATE_PREFIX_PATTERN.match(base_name)
    if date_match:
        candidate = base_name[date_match.end() :].strip(" -")
        if candidate:
            extra_prefix_tags.append(date_match.group(1))
            base_name = candidate

    # b) Rank prefixes without dashes: e.g. "089 Ice Climber"
    if not rom_number:
        rank_match = RANK_PREFIX_PATTERN.match(base_name)
        if rank_match:
            candidate = base_name[rank_match.end() :].strip(" -")
            if candidate:
                rom_number = rank_match.group(1)
                base_name = candidate

    # c) Hardware prefixes: e.g. "2C03 Pinball", "2C04-01 Gradius"
    hw_match = HARDWARE_PREFIX_PATTERN.match(base_name)
    if hw_match:
        candidate = base_name[hw_match.end() :].strip(" -")
        if candidate:
            extra_prefix_tags.append(hw_match.group(1))
            base_name = candidate

    # d) Leading VS. prefix: e.g. "VS. Duck Hunt"
    vs_match = VS_PREFIX_PATTERN.match(base_name)
    if vs_match:
        candidate = base_name[vs_match.end() :].strip(" -")
        if candidate:
            extra_prefix_tags.append(vs_match.group(1))
            base_name = candidate

    # Suffix stripping: patch/mod, version/author, and hack suffixes outside brackets
    while True:
        stripped_suffix = False

        # h) Trailing Hack suffix: "Aero Fighters Hack"
        hack_match = HACK_SUFFIX_PATTERN.search(base_name)
        if hack_match:
            candidate = base_name[: hack_match.start()].rstrip(" -–—").strip()
            if candidate:
                extra_suffix_tags.append(hack_match.group(1))
                base_name = candidate
                stripped_suffix = True
                continue

        # f) Retail patch/mod suffixes: "ActRaiser PAL-to-NTSC Patched"
        patch_match = PATCH_SUFFIX_PATTERN.search(base_name)
        if patch_match:
            candidate = base_name[: patch_match.start()].rstrip(" -–—").strip()
            if candidate:
                extra_suffix_tags.append(patch_match.group(1))
                base_name = candidate
                stripped_suffix = True
                continue

        # g) Version and author suffixes: "Batter Up v0.1 Revo", "Battletoads b1 nextvolume", "Alien 1.02 Final"
        ver_match = VERSION_AUTHOR_PATTERN.search(base_name)
        if ver_match:
            candidate = base_name[: ver_match.start()].rstrip(" -–—").strip()
            if candidate:
                ver_tag = ver_match.group(1).strip()
                extra_suffix_tags.append(ver_tag)
                if not extracted_revision:
                    extracted_revision = ver_tag
                base_name = candidate
                stripped_suffix = True
                continue

        if not stripped_suffix:
            break

    # e) Inverted articles at end of base name: "Berenstain Bears' Camping Adventure, The" -> "The Berenstain Bears' Camping Adventure"
    base_name = base_name.rstrip(" -–—").strip()
    inv_match = INVERTED_ARTICLE_PATTERN.match(base_name)
    if inv_match:
        article = inv_match.group(2).capitalize()
        base_name = f"{article} {inv_match.group(1).strip()}"

    # Extract all tags from parentheses and brackets
    raw_tags = tag_pattern.findall(remainder)

    # Classify tags
    regions = []
    revision = ""
    other_tags = []

    for tag in raw_tags:
        tag_stripped = tag.strip()

        # Check if it's a disc/track indicator first (only if not already found)
        if disc is None:
            disc_match = DISC_PATTERN.match(tag_stripped)
            if disc_match:
                disc = int(disc_match.group(1))
                continue

        # Check if it's a revision
        if REVISION_PATTERN.match(tag_stripped):
            revision = tag_stripped
            continue

        # Check if it's a region (or comma-separated regions)
        parts = [p.strip() for p in tag_stripped.split(",")]
        region_matches = []
        non_region_parts = []

        for part in parts:
            normalized = REGION_ALIASES.get(part.lower())
            if normalized:
                region_matches.append(normalized)
            else:
                non_region_parts.append(part)

        # Only treat as regions if ALL parts are regions
        # This prevents (En,Fr,De) from being split into regions + tags
        if region_matches and not non_region_parts:
            regions.extend(region_matches)
            continue

        # Otherwise it's a general tag (keep original parts, not normalized)
        other_tags.extend(parts)

    # Combine multiple regions into single string (take first, most common case)
    region = regions[0] if regions else ""

    # If revision wasn't set from parentheses/brackets, use revision extracted from suffix
    if not revision and extracted_revision:
        revision = extracted_revision

    # Combine tags: prefix tags, bracket tags, suffix tags (deduplicated while preserving order)
    combined_tags: list[str] = []
    for tag_item in extra_prefix_tags + other_tags + extra_suffix_tags:
        if tag_item not in combined_tags:
            combined_tags.append(tag_item)

    result = {
        "name": base_name,
        "region": region,
        "revision": revision,
        "tags": combined_tags,
        "extension": extension,
        "rom_number": rom_number,
        "disc": disc,
    }

    logger.debug(
        "Parsed '%s': name=%s, region=%s, revision=%s, tags=%s, rom_number=%s, disc=%s",
        filename,
        base_name,
        region or "(none)",
        revision or "(none)",
        other_tags or "(none)",
        rom_number or "(none)",
        disc or "(none)",
    )

    return result


# Nintendo Switch Title ID patterns
# Title IDs are 16 hex digits, typically in brackets: [0100000000010000]
SWITCH_TITLE_ID_PATTERN = re.compile(r"\[([0-9A-Fa-f]{16})\]")


def extract_switch_title_id(filename: str) -> str | None:
    """Extract Nintendo Switch Title ID from filename.

    Switch Title IDs are 16 hex digits, typically in brackets like [0100000000010000].

    Args:
        filename: ROM filename (e.g., "Game Name [0100000000010000].nsp")

    Returns:
        16-character uppercase hex string, or None if not found
    """
    match = SWITCH_TITLE_ID_PATTERN.search(filename)
    if match:
        return match.group(1).upper()
    return None


def detect_switch_content_type(title_id: str) -> str:
    """Determine content type from Switch Title ID.

    Title ID structure (16 hex digits):
    - Last 3 chars = "000" -> Base game
    - Last 3 chars = "800" -> Update
    - Everything else -> DLC (001-7FF, 801-FFF)

    Args:
        title_id: 16-character hex Title ID (uppercase)

    Returns:
        "base", "update", or "dlc"
    """
    if not title_id or len(title_id) != 16:
        return ""

    suffix = title_id[-3:].upper()

    if suffix == "000":
        return "base"
    elif suffix == "800":
        return "update"
    else:
        return "dlc"


def get_switch_content_info(filename: str) -> tuple[str, str]:
    """Extract Switch Title ID and content type from filename.

    Convenience function that combines extract_switch_title_id and
    detect_switch_content_type into a single call.

    Args:
        filename: ROM filename (e.g., "Game Name [0100000000010000].nsp")

    Returns:
        Tuple of (switch_title_id, content_type). Both are empty strings
        if no Title ID is found in the filename.
    """
    title_id = extract_switch_title_id(filename)
    if not title_id:
        return "", ""
    return title_id, detect_switch_content_type(title_id)
