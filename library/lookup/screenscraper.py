"""ScreenScraper-based ROM identification service.

This lookup service uses ScreenScraper's API to identify ROMs when Hasheous
fails to find a match. It tries CRC lookup first (for regular ROMs), then
falls back to romnom (filename) lookup, and finally name search as last resort.
"""

import json
import logging
import re
import unicodedata
from datetime import timedelta
from pathlib import Path
from typing import TYPE_CHECKING, Optional

import requests
from django.utils import timezone

from .base import LookupResult, LookupService

if TYPE_CHECKING:
    from library.models import System

logger = logging.getLogger(__name__)


def normalize_name(name: str) -> str:
    """Normalize game name for fuzzy matching.

    Args:
        name: Original game name

    Returns:
        Normalized name (lowercase, no punctuation, no articles, ASCII only)
    """
    # Convert to lowercase
    name = name.lower()

    # Strip catalog-number labels ScreenScraper prepends to some systems'
    # titles (Channel F: "Videocart-26 - Alien Invasion"). The label's number
    # is not a sequel number and must not trip number-consistency checks.
    name = re.sub(r"^(?:video)?\s*cart\s*[-#]?\s*\d+\s*[-:]\s+", "", name)

    # ScreenScraper marks non-game entries with this label; it is not title text.
    name = re.sub(r"^zzz\(notgame\):\s*", "", name)

    # Unicode normalize - convert accented chars to base form (é -> e)
    name = unicodedata.normalize("NFKD", name)
    name = "".join(c for c in name if not unicodedata.combining(c))

    # Remove common articles
    articles = ["the ", "a ", "an "]
    for article in articles:
        if name.startswith(article):
            name = name[len(article) :]

    # Convert hyphens, en-dashes, em-dashes, slashes, colons, underscores into spaces
    # BEFORE removing other punctuation to avoid merging words (e.g. Pac-Man -> pac man)
    name = re.sub(r"[\-–—/:_]", " ", name)

    # Remove punctuation and special characters
    name = re.sub(r"[^\w\s]", "", name)

    # Convert standalone Roman numerals II-X to Western numbers 2-10 (case-insensitive)
    # Note: 'name' is already lowercased, so we match lowercase roman numerals
    roman_to_western = {
        "viii": "8",
        "vii": "7",
        "vi": "6",
        "iv": "4",
        "ix": "9",
        "iii": "3",
        "ii": "2",
        "v": "5",
        "x": "10",
    }
    name = re.sub(
        r"\b(viii|vii|vi|iv|ix|iii|ii|v|x)\b",
        lambda m: roman_to_western[m.group(0)],
        name,
    )


    # P3b: grouping suffixes ("Series", "Collection", ...) do not change
    # game identity (naomi "Initial D Arcade Stage Series").
    name = re.sub(
        r"\s+(series|collection|anthology|compilation)$", "", name
    )

    # Normalize whitespace
    name = re.sub(r"\s+", " ", name).strip()

    return name


CONSOLE_TERMS = {
    "32x",
    "nes",
    "snes",
    "sfc",
    "sms",
    "gg",
    "genesis",
    "megadrive",
    "n64",
    "lynx",
    "gb",
    "gba",
    "gbc",
    "pce",
    "tg16",
    "atari",
    "sega",
    "nintendo",
    "sony",
    "playstation",
    "psx",
    "arcade",
    "mame",
}

STOPWORDS = {
    "the",
    "a",
    "an",
    "of",
    "and",
    "or",
    "vs",
    "to",
    "in",
    "on",
    "at",
    "by",
    "for",
    "with",
    "no",
    "ni",
    "de",
    "la",
    "le",
}


_JP_FOLD_PATTERN = re.compile(r"uu|ou|oh")


def _fold_jp(token: str) -> str:
    """Fold common Japanese romanization alternations (uu->u, ou/oh->o)."""
    return _JP_FOLD_PATTERN.sub(
        lambda m: "u" if m.group(0) == "uu" else "o", token
    )


def _levenshtein(a: str, b: str) -> int:
    """Levenshtein distance for short tokens, capped early."""
    if abs(len(a) - len(b)) > 2:
        return 3
    prev = list(range(len(b) + 1))
    for i, ca in enumerate(a, 1):
        cur = [i]
        for j, cb in enumerate(b, 1):
            cur.append(min(prev[j] + 1, cur[j - 1] + 1, prev[j - 1] + (ca != cb)))
        prev = cur
    return prev[-1]


def _jp_score(norm_game: str, norm_api: str) -> float:
    """Conservative JP-romanization comparison pass (P3c).

    Returns 1.0 for fold-equal or squash-equal (de-spaced) forms, else a
    Jaccard-style ratio counting token pairs within edit distance <= 2
    (>= 4 chars) as one shared token. Never lowers a score; callers apply
    it only after the consistency gates.
    """
    game_tokens = [_fold_jp(t) for t in norm_game.split()]
    api_tokens = [_fold_jp(t) for t in norm_api.split()]
    if not game_tokens or not api_tokens:
        return 0.0
    if game_tokens == api_tokens or "".join(game_tokens) == "".join(api_tokens):
        return 1.0
    matched = set()
    shared = 0
    for gt in game_tokens:
        for idx, at in enumerate(api_tokens):
            if idx in matched:
                continue
            if gt == at or (
                len(gt) >= 4 and len(at) >= 4 and _levenshtein(gt, at) <= 2
            ):
                shared += 1
                matched.add(idx)
                break
    if not shared:
        return 0.0
    return shared / (len(set(game_tokens)) + len(set(api_tokens)) - shared)


def _extract_numbers(norm_name: str) -> set[int]:
    """Extract standalone numbers (1-99) including normalized Roman numerals 1-10, excluding 4-digit years.

    Args:
        norm_name: Normalized game name string

    Returns:
        Set of integers found (1-99)
    """
    # P3b: multicart tokens ("4-in-1") are not sequel numbers.
    norm_name = re.sub(r"\b\d+\s+in\s+1\b", "", norm_name)
    tokens = norm_name.split()
    numbers = set()
    for idx, token in enumerate(tokens):
        if token.isdigit():
            val = int(token)
            if 1 <= val <= 99:
                numbers.add(val)
        elif token == "i":
            # Standalone 'i' treated as Roman numeral 1 if at the end of title or after part/vol/volume/chapter/episode
            if (idx == len(tokens) - 1 and len(tokens) > 1) or (
                idx > 0
                and tokens[idx - 1] in {"part", "vol", "volume", "chapter", "episode"}
            ):
                numbers.add(1)
    return numbers


def _check_number_consistency(norm1: str, norm2: str) -> bool:
    """Check sequel / number consistency between two normalized names.

    Returns False if:
    - Both titles have numbers and they differ (e.g. 2 vs 3)
    - One title has a sequel number >= 2 and the other has NO numbers (e.g. "Mega Man" vs "Mega Man 2")
    """
    nums1 = _extract_numbers(norm1)
    nums2 = _extract_numbers(norm2)

    if nums1 and nums2:
        if nums1 != nums2:
            return False
    elif nums1 and not nums2:
        if any(n >= 2 for n in nums1):
            return False
    elif nums2 and not nums1:
        if any(n >= 2 for n in nums2):
            return False

    return True


def _subtitle_guard(norm_identity: str, candidates: list[str]) -> bool:
    """True when a subtitle in the identity is missing from every candidate.

    A title like "Bionic Commando: Elite Forces" denotes a different game
    than the base "Bionic Commando"; if no candidate name contains the
    subtitle words, the match must not pass on a truncated prefix alone.
    """
    parts = re.split(r"\s*[:\-–—]\s*", norm_identity)
    if len(parts) < 2:
        return False
    subtitle_words = set(normalize_name(parts[-1]).split()) - STOPWORDS
    if not subtitle_words:
        return False
    candidate_words = set(" ".join(candidates).split())
    return not subtitle_words <= candidate_words


_NOTGAME_PREFIX = "ZZZ(notgame)"


def _clean_names(names: list[str]) -> list[str]:
    """Names without the ZZZ(notgame) placeholder prefix.

    ScreenScraper labels non-game placeholder entries with "ZZZ(notgame):",
    but some real entries also carry a placeholder-prefixed world alias
    beside clean regional names (pcecd Snatcher). The clean names decide.
    """
    return [n for n in names if n and not n.startswith(_NOTGAME_PREFIX)]


def _is_notgame_placeholder(names: list[str]) -> bool:
    """True when the ScreenScraper entry has no usable (clean) name."""
    return not _clean_names(names)


def _reject_placeholder(lookup_type: str, value: str, result: dict | None) -> dict | None:
    """Zero out a crc/romnom result whose only name is a placeholder."""
    names = [result.get("name", "")] if result else []
    if result and _is_notgame_placeholder(names):
        logger.info(
            "placeholder rejected type=%s value=%s name=%s",
            lookup_type,
            value[:30],
            result.get("name", ""),
        )
        return None
    return result


_SYSTEM_FAMILIES: dict[str, list[str]] | None = None


def _load_system_families() -> dict[str, list[str]]:
    """Load the vendored ScreenScraper parentid edges (lazy, once)."""
    global _SYSTEM_FAMILIES
    if _SYSTEM_FAMILIES is None:
        path = Path(__file__).resolve().parent.parent / "system_families.json"
        try:
            _SYSTEM_FAMILIES = json.loads(path.read_text())
        except OSError:
            _SYSTEM_FAMILIES = {}
    assert _SYSTEM_FAMILIES is not None
    return _SYSTEM_FAMILIES


def _child_to_parent(
    families: dict[str, list[str]],
) -> dict[str, str]:
    """Reverse index of the parentid tree: child SS id -> parent SS id."""
    return {
        child: parent
        for parent, children in families.items()
        for child in children
    }


def _up_walk_ids(
    mapped: list[str], families: dict[str, list[str]]
) -> list[str]:
    """P1: parent + siblings for each mapped id in a small (<= 5 children)
    ScreenScraper family. Big families (arcade 75 -> 67 children) are never
    up-walked — quota guard.
    """
    parents = _child_to_parent(families)
    up: list[str] = []
    seen = set(mapped)
    for sid in mapped:
        parent = parents.get(sid)
        if not parent:
            continue
        siblings = families.get(parent, [])
        if len(siblings) > 5:
            continue
        for family_id in (parent, *siblings):
            if family_id not in seen:
                seen.add(family_id)
                up.append(family_id)
    return up


def expand_system_ids(system: "System") -> list[int]:
    """System's mapped ScreenScraper IDs plus up/down family companions.

    ScreenScraper indexes hacks/bootlegs/homebrew under companion system IDs
    linked to the base hardware via ``parentid`` (library/system_families.json).
    Name search probes mapped IDs, their descendants (capped at the four
    lowest SS ids), and — P1 — the parent plus siblings of any mapped id
    whose family has at most five children. Acceptance still distinguishes
    mapped-only (rule c) from mapped+family (rules a/b).
    """
    families = _load_system_families()
    mapped = [str(sid) for sid in system.all_screenscraper_ids]
    seen = set(mapped)
    stack = list(mapped)
    descendants: list[str] = []
    while stack:
        for child in families.get(stack.pop(), []):
            if child not in seen:
                seen.add(child)
                descendants.append(child)
                stack.append(child)
    descendants = sorted(descendants, key=int)[:4]
    up = _up_walk_ids(mapped, families)
    ordered = list(dict.fromkeys(mapped + descendants + up))
    return [int(sid) for sid in ordered]


def get_all_family_system_ids(system: "System") -> set[str]:
    """Return mapped ScreenScraper IDs plus all family companion IDs.

    ScreenScraper indexes games under companion child system IDs linked via
    parentid (library/system_families.json), such as Capcom Classics (151)
    under Arcade (75). This returns the full transitive descendant closure
    of mapped IDs (uncapped) plus the P1 small-family up-walk, for verifying
    match acceptance.
    """
    families = _load_system_families()
    mapped = [str(sid) for sid in system.all_screenscraper_ids]
    allowed = set(mapped)
    stack = list(allowed)
    while stack:
        parent = stack.pop()
        for child in families.get(parent, []):
            if child not in allowed:
                allowed.add(child)
                stack.append(child)
    allowed.update(_up_walk_ids(mapped, families))
    return allowed


def calculate_match_score(game_name: str, api_name: str) -> float:
    """Calculate similarity score between two game names.

    Args:
        game_name: Name from our database
        api_name: Name from API response

    Returns:
        Match score (0.0 to 1.0)
    """
    norm_game = normalize_name(game_name)
    norm_api = normalize_name(api_name)

    # Exact match
    if norm_game and norm_game == norm_api:
        return 1.0

    # Hard identity gates before any subtitle/substring leniency: the "Vs."
    # hardware prefix and sequel numbers decide identity. Numbers inside a
    # separator-introduced subtitle ("ArduGolf - 18-Hole Golf Simulation")
    # are subtitle text, not sequel continuation — judge the main title in
    # that case; a bare continuation ("Mega Man 2 Deluxe") stays rejected.
    if norm_game.startswith("vs ") != norm_api.startswith("vs "):
        return 0.0

    if not _check_number_consistency(norm_game, norm_api):
        main_parts = re.split(r"\s*[:\-–—]\s*", api_name)
        main_api = normalize_name(main_parts[0]) if len(main_parts) > 1 else ""
        if not main_api or not _check_number_consistency(norm_game, main_api):
            return 0.0

    # P3a: the candidate is the identity plus a subtitle ("ArduGolf - 18-Hole
    # Golf Simulation"): the main titles are equal by construction, so judge
    # the main title only. A bare-number continuation ("Mega Man 2") is a
    # sequel, not a subtitle. (_subtitle_guard covers the opposite order.)
    if norm_game and norm_api.startswith(f"{norm_game} "):
        subtitle = norm_api[len(norm_game) + 1 :].strip()
        if subtitle and any(w.isalpha() for w in subtitle.split()):
            return 0.85

    # P3c: conservative JP-romanization fold pass (madoh/madou,
    # Daimakaimura/Daimakai Mura, gryps/grips).
    jp = _jp_score(norm_game, norm_api)
    game_words = set(norm_game.split())
    api_words = set(norm_api.split())

    if not game_words or not api_words:
        return 0.0

    overlap = game_words & api_words

    # Console Terms & Stopwords Guard:
    # If the word overlap consists ONLY of console terms and/or stopwords: score MUST BE 0.0
    # (unless the P3c fold pass found fold/squash equality, e.g. "Daimakai Mura").
    meaningful_overlap = overlap - CONSOLE_TERMS - STOPWORDS
    if not meaningful_overlap:
        return 1.0 if jp >= 1.0 else 0.0
    # Substring match precision:
    # Substring match shorter in longer (on word boundaries) awards 0.85 ONLY IF:
    # - Shorter has at least 2 meaningful words, OR
    # - Shorter is at least 5 characters and constitutes at least 40% of the longer string's length,
    # AND shorter is not purely a console term or stopword.
    shorter, longer = (
        (norm_game, norm_api)
        if len(norm_game) <= len(norm_api)
        else (norm_api, norm_game)
    )
    shorter_words = shorter.split()
    meaningful_shorter = [
        w for w in shorter_words if w not in CONSOLE_TERMS and w not in STOPWORDS
    ]

    has_word_boundary_match = bool(
        re.search(rf"\b{re.escape(shorter)}\b", longer)
    )
    is_precise_shorter = bool(
        meaningful_shorter
        and (
            len(meaningful_shorter) >= 2
            or (len(shorter) >= 5 and (len(shorter) / len(longer)) >= 0.40)
        )
    )

    if has_word_boundary_match and is_precise_shorter:
        return 0.85

    # Word overlap scoring
    total = len(game_words | api_words)
    base_score = len(overlap) / total

    # If all words of the shorter title are contained in the longer title
    # (e.g., "Aladdin" in "Disney's Aladdin"), ensure the score is at least 0.85
    # only if shorter meets the precision criteria
    if (
        game_words.issubset(api_words) or api_words.issubset(game_words)
    ) and is_precise_shorter:
        base_score = max(base_score, 0.85)

    return max(base_score, jp)


def _candidate_score(identity: str, alt_name: str, result: dict) -> float:
    """Best score of one API candidate against the identity or its raw name.

    The raw name is skipped when it disagrees with the parsed identity about a
    "Vs." prefix — the identity is the authoritative parse (a ROM named
    "Excitebike (VS)" must not match the home-console "Excitebike" through its
    trailing tag word).
    """
    names = _clean_names(
        [n for n in {result.get("name", ""), *result.get("all_names", [])} if n]
    )
    comparisons = [identity]
    if normalize_name(alt_name).startswith("vs ") == normalize_name(
        identity
    ).startswith("vs "):
        comparisons.append(alt_name)
    return max(
        (calculate_match_score(name, n) for name in comparisons for n in names),
        default=0.0,
    )


def _rank_candidates(
    candidates: list[dict],
    mapped_ids: set[str],
    searchable_ids: set[str],
) -> dict | None:
    """Pick the winner of a scored candidate pool, or None.

    Acceptance rules:
    - (a) exact 1.0: accepted on any mapped or family system;
    - (b) >= 0.85: accepted when the runner-up (best different SS game id) is
      at least 0.15 behind, mapped or family system;
    - (c) >= 0.60: accepted with a 0.25 margin, mapped systems only.
    A tie at the top between different SS game ids rejects everything, except
    an exact tie at 1.0 (SS duplicate entries: lowest id wins) and — P3d,
    flagged ``matching_tie_expand_enabled`` — ties >= 0.85 whose titles share
    a version-stripped base (mapped system preferred, then lowest id).
    """
    pool = sorted(candidates, key=lambda c: c["confidence"], reverse=True)
    if not pool:
        return None
    top = pool[0]
    tied = [
        c
        for c in pool[1:]
        if c["confidence"] == top["confidence"] and str(c["id"]) != str(top["id"])
    ]
    if tied:
        # P3d: sub-1.0 ties expand when every tied title shares a base
        # (equal after stripping version/revision tokens); prefer a
        # mapped-system candidate, then the lowest SS id. [flagged:
        # matching_tie_expand_enabled] Exact 1.0 ties keep the plain
        # lowest-id rule (no flag read, no mapped preference).
        if (
            0.85 <= top["confidence"] < 1.0
            and _ties_share_base([top["name"], *[t["name"] for t in tied]])
            and _flag_enabled("matching_tie_expand_enabled")
        ):
            top = min(
                [top, *tied],
                key=lambda c: (
                    str(c["system_id"]) not in mapped_ids,
                    int(c["id"]),
                ),
            )
            return top if str(top["system_id"]) in searchable_ids else None
        if top["confidence"] < 1.0:
            return None
        top = min([top, *tied], key=lambda c: int(c["id"]))
        return top if str(top["system_id"]) in searchable_ids else None
    runner_up = max(
        (c["confidence"] for c in pool[1:] if str(c["id"]) != str(top["id"])),
        default=0.0,
    )
    if top["confidence"] >= 1.0:
        return top if str(top["system_id"]) in searchable_ids else None
    if top["confidence"] >= 0.85:
        margin = 0.15
        system_ok = str(top["system_id"]) in searchable_ids
    else:
        margin = 0.25
        system_ok = str(top["system_id"]) in mapped_ids
    if not system_ok or runner_up > top["confidence"] - margin:
        return None
    return top


def _flag_enabled(key: str) -> bool:
    """Read a rollback flag (Setting row), defaulting to enabled."""
    from library.models import Setting

    return bool(Setting.get(key, True))


_VERSION_TOKEN_PATTERN = re.compile(
    r"^(?:v(?:er)?\.?\d*[a-z]?|version|rev(?:ision)?\.?\d*|r\d+)$"
)


def _title_base(name: str) -> str:
    """Normalized title with trailing version/revision tokens stripped."""
    tokens = name.lower().split()
    while len(tokens) > 1 and _VERSION_TOKEN_PATTERN.match(tokens[-1]):
        tokens.pop()
    return normalize_name(" ".join(tokens))


def _ties_share_base(names: list[str]) -> bool:
    """True when every name normalizes to the same version-stripped base."""
    bases = {_title_base(n) for n in names}
    return len(bases) == 1 and "" not in bases


def _candidate_omits_identity_words(identity: str, candidates: list[str]) -> bool:
    """True if every candidate omits a meaningful word from the identity.

    P3b: parenthetical alt-titles ("Donkey Kong (Donkey Kong '94)") do not
    belong to the word sets — they are not identity words the candidate
    must preserve.
    """

    def _words(text: str) -> set[str]:
        stripped = re.sub(r"\([^)]*\)|\[[^\]]*\]", " ", text)
        return set(normalize_name(stripped).split()) - STOPWORDS - CONSOLE_TERMS

    identity_words = _words(identity)
    if not identity_words:
        return False
    return all(not identity_words <= _words(candidate) for candidate in candidates)


def _extract_romnom(file_path: str, archive_as_rom: bool) -> str:
    """Extract romnom (filename WITH extension) for ScreenScraper search.

    The romnom is the filename that ScreenScraper uses for matching. Different
    handling is needed based on system type:

    - archive_as_rom systems (arcade): use archive filename → "pacman.zip"
    - Regular systems with archives: use ROM filename inside archive → "Advance Wars.gba"
    - Loose files: use the filename → "Super Mario World.smc"

    This mirrors how CRC works (hash of actual ROM, not archive).

    Args:
        file_path: ROM path, may contain "!" for archive contents
        archive_as_rom: If True, use archive name; else use inner file name
    """
    if archive_as_rom:
        # For arcade systems, always use the archive filename
        # "/roms/arcade/pacman.zip" → "pacman.zip"
        # "/roms/arcade/pacman.zip!pacman/game.rom" → "pacman.zip"
        if "!" in file_path:
            archive_path = file_path.split("!")[0]
            return Path(archive_path).name
        return Path(file_path).name
    elif re.search(r"\.\w{1,4}!", file_path):
        # For regular systems with archives, use the inner ROM filename.
        # The separator is an extension followed by "!" — NOT the first "!",
        # which is common inside filenames (e.g. "Punch Out!! (U) [b1].zip!
        # Punch Out!! (U) [b1].nes" → "Punch Out!! (U) [b1].nes").
        return re.split(r"\.\w{1,4}!", file_path, maxsplit=1)[-1].split("/")[-1]
    else:
        # Loose file
        # "Super Mario World.smc" → "Super Mario World.smc"
        return Path(file_path).name


def _extract_rom_stem(file_path: str, archive_as_rom: bool = False) -> str:
    """Extract ROM filename stem cleaned of tags for fallback name search.

    Args:
        file_path: ROM path, may contain "!" for archive contents
        archive_as_rom: If True, use archive name; else use inner file name

    Returns:
        Cleaned stem (tags and extensions removed)
    """
    romnom = _extract_romnom(file_path, archive_as_rom)
    try:
        from library.parser import parse_rom_filename

        parsed = parse_rom_filename(romnom, arcade=archive_as_rom)
        stem = parsed.get("name", "").strip()
        if stem:
            return stem
    except Exception:
        pass

    # Fallback: strip tags from Path stem
    stem = Path(romnom).stem
    stem = re.sub(r"[\(\[][^\)\]]+[\)\]]", "", stem).strip()
    return re.sub(r"\s+", " ", stem).strip()


def _romnom_title_matches(romnom: str, result: dict) -> bool:
    """Return whether a regular-system romnom result names the same title."""
    identity = _extract_rom_stem(romnom)
    normalized_identity = normalize_name(identity).replace(" ", "")
    names = _clean_names(
        [n for n in {result.get("name", ""), *result.get("all_names", [])} if n]
    )
    if not names:
        return False
    return any(
        calculate_match_score(identity, name) == 1.0
        or normalized_identity == normalize_name(name).replace(" ", "")
        for name in names
    )



def _check_cache(
    lookup_type: str, lookup_value: str, system_id: int
) -> tuple[bool, dict | None]:
    """Check cache for a ScreenScraper lookup.

    Args:
        lookup_type: Type of lookup ("crc" or "romnom")
        lookup_value: The value to look up
        system_id: ScreenScraper system ID

    Returns:
        Tuple of (found_in_cache, cached_result).
        If found_in_cache is True but cached_result is None, it means
        the lookup was previously done and had no match.
    """
    from library.models import ScreenScraperLookupCache

    try:
        cache_entry = ScreenScraperLookupCache.objects.get(
            lookup_type=lookup_type,
            lookup_value=lookup_value.lower(),
            system_id=system_id,
        )

        if not cache_entry.matched:
            # New dumps and catalog entries can turn a genuine miss into a hit.
            if cache_entry.created_at <= timezone.now() - timedelta(days=30):
                return False, None
            # Known no-match
            logger.debug(
                "ScreenScraper cache hit (no match): %s (system %d)",
                lookup_value[:20],
                system_id,
            )
            return True, None

        # P0-B: remember the system the match actually belongs to, so a
        # foreign-system hit cached before rejection cannot resurrect as a
        # same-system match on the next pass.
        cached_result = {
            "id": cache_entry.screenscraper_id,
            "name": cache_entry.game_name,
            "system_id": (
                cache_entry.matched_system_id
                if cache_entry.matched_system_id is not None
                else system_id
            ),
            "confidence": cache_entry.confidence,
        }
        # P0-A: a cached "match" whose only name is a placeholder is dead on
        # read; treat it as a miss so the pipeline re-evaluates the value.
        if _is_notgame_placeholder([cache_entry.game_name]):
            logger.info(
                "placeholder rejected type=%s value=%s name=%s",
                lookup_type,
                lookup_value[:30],
                cache_entry.game_name,
            )
            return False, None
        logger.debug(
            "ScreenScraper cache hit (matched): %s=%s -> %s (ID: %s)",
            lookup_type,
            lookup_value[:20],
            cache_entry.game_name,
            cache_entry.screenscraper_id,
        )
        return True, cached_result

    except ScreenScraperLookupCache.DoesNotExist:
        return False, None


def _save_to_cache(
    lookup_type: str,
    lookup_value: str,
    system_id: int,
    result: dict | None,
) -> None:
    """Save lookup result to cache.

    Args:
        lookup_type: Type of lookup ("crc", "romnom", or "name")
        lookup_value: The lookup value
        system_id: ScreenScraper system ID
        result: Result dict with 'id', 'name', and optional 'confidence', or
            None if no match
    """
    from library.models import ScreenScraperLookupCache

    try:
        # Treat result with no ID as a no-match
        if result is None or not result.get("id"):
            # Cache a no-match
            ScreenScraperLookupCache.objects.update_or_create(
                lookup_type=lookup_type,
                lookup_value=lookup_value.lower(),
                system_id=system_id,
                defaults={
                    "matched": False,
                    "screenscraper_id": None,
                    "game_name": "",
                    "confidence": None,
                    "created_at": timezone.now(),
                },
            )
            logger.debug(
                "ScreenScraper cache saved (no match): %s=%s (system %d)",
                lookup_type,
                lookup_value[:20],
                system_id,
            )
        else:
            # Cache a match
            ScreenScraperLookupCache.objects.update_or_create(
                lookup_type=lookup_type,
                lookup_value=lookup_value.lower(),
                system_id=system_id,
                defaults={
                    "matched": True,
                    "screenscraper_id": result.get("id"),
                    "game_name": result.get("name", ""),
                    "confidence": result.get("confidence"),
                    "matched_system_id": result.get("system_id"),
                },
            )
            logger.debug(
                "ScreenScraper cache saved (matched): %s=%s -> %s (ID: %s)",
                lookup_type,
                lookup_value[:20],
                result.get("name", ""),
                result.get("id"),
            )

    except Exception as e:
        # Don't fail the lookup if caching fails
        logger.warning("Failed to save ScreenScraper cache: %s", e)



class ScreenScraperLookupService(LookupService):
    """ScreenScraper-based ROM identification.

    Tries CRC lookup first (for regular ROMs), then romnom (filename), and finally
    name search as last resort. Works for all systems, providing a universal
    fallback when Hasheous fails.
    """

    name = "screenscraper"

    def __init__(self):
        self._client = None

    def _get_client(self):
        """Lazy-load the ScreenScraper client."""
        if self._client is None:
            from library.metadata.screenscraper import ScreenScraperClient

            self._client = ScreenScraperClient()
        return self._client

    def lookup(  # noqa: ARG002
        self,
        system: "System",
        crc32: str = "",
        sha1: str = "",
        md5: str = "",
        file_path: str = "",
        game_name: str = "",
        allow_name_search: bool = True,
    ) -> Optional[LookupResult]:
        """Look up ROM using ScreenScraper CRC, romnom, or name search.

        For regular systems, tries CRC first, then romnom, then name search.
        For arcade systems (archive_as_rom), skips CRC and goes straight to romnom.

        Args:
            system: Target system
            crc32: CRC32 hash (8 hex chars)
            sha1: SHA1 hash (not used - ScreenScraper uses CRC)
            md5: MD5 hash (not used - ScreenScraper uses CRC)
            file_path: Path to ROM file (for romnom extraction)
            game_name: Game name for name-based search (last resort fallback)
            allow_name_search: When False, run only the exact CRC/romnom
                phases and never the fuzzy name searches

        Returns:
            LookupResult if found, None otherwise
        """
        # Need at least one of CRC, file_path, or game_name
        if not crc32 and not file_path and not game_name:
            return None

        # Check if ScreenScraper credentials are configured
        client = self._get_client()
        if not client.has_credentials():
            logger.debug("ScreenScraper credentials not configured, skipping lookup")
            return None

        # Try all system IDs in order for exact matches (CRC, romnom)
        allowed_system_ids = get_all_family_system_ids(system)

        def _system_ok(result: LookupResult) -> bool:
            """Reject matches ScreenScraper attributed to a different system."""
            return (
                result.matched_system_id is None
                or str(result.matched_system_id) in allowed_system_ids
            )

        for system_id in system.all_screenscraper_ids:
            # Phase 1: CRC lookup (skip for arcade systems)
            if crc32 and not system.archive_as_rom:
                result = self._try_crc(crc32, system_id)
                if result and _system_ok(result):
                    return result
                if result:
                    logger.warning(
                        "CRC match %s for '%s' belongs to system %s, not %s; rejected",
                        result.screenscraper_id,
                        game_name or file_path,
                        result.matched_system_id,
                        sorted(allowed_system_ids),
                    )

            # Phase 2: Romnom lookup (filename-based fallback)
            if file_path:
                romnom = _extract_romnom(file_path, system.archive_as_rom)
                result = self._try_romnom(
                    romnom, system_id, verify_title=not system.archive_as_rom
                )
                if result and _system_ok(result):
                    return result
                if result:
                    logger.warning(
                        "Romnom match %s for '%s' belongs to system %s, not %s; rejected",
                        result.screenscraper_id,
                        game_name or file_path,
                        result.matched_system_id,
                        sorted(allowed_system_ids),
                    )

        if allow_name_search:
            # Phase 3: Name search (fuzzy matching, last resort).
            # Aggregate candidates across ALL query variants x ALL search
            # system IDs (mapped + family), then rank with margin-based
            # acceptance.
            issued: set[tuple[str, int]] = set()
            if game_name:
                best_result = self._aggregate_name_search(game_name, system, issued)
                if best_result:
                    return best_result

            # Phase 3b: ROM filename stem search (fallback when game_name
            # fails or differs)
            if file_path:
                stem = _extract_rom_stem(file_path, system.archive_as_rom)
                if stem and (
                    not game_name
                    or stem.strip().lower() != game_name.strip().lower()
                ):
                    stem_best_result = self._aggregate_name_search(
                        stem, system, issued
                    )
                    if stem_best_result and (
                        not game_name
                        or stem_best_result.confidence >= 0.85
                        or calculate_match_score(game_name, stem_best_result.name)
                        >= 0.6
                    ):
                        return stem_best_result

        return None

    def _try_crc(self, crc32: str, system_id: int) -> Optional[LookupResult]:
        """Try CRC lookup with caching."""
        from library.metadata.screenscraper import ScreenScraperRateLimited

        found_in_cache, cached_result = _check_cache("crc", crc32, system_id)
        if found_in_cache:
            return (
                None
                if cached_result is None
                else self._result_from_dict(cached_result, match_type="crc32")
            )

        try:
            result = _reject_placeholder("crc", crc32, self._get_client().search_by_crc(crc32, system_id))
            _save_to_cache("crc", crc32, system_id, result)
            return (
                self._result_from_dict(result, match_type="crc32") if result else None
            )
        except (requests.RequestException, ScreenScraperRateLimited):
            raise
        except Exception as e:
            logger.debug("ScreenScraper CRC lookup failed for %s: %s", crc32, e)
            raise

    def _try_romnom(
        self, romnom: str, system_id: int, *, verify_title: bool = True
    ) -> Optional[LookupResult]:
        """Try romnom lookup, rejecting title-prefix false positives."""
        from library.metadata.screenscraper import ScreenScraperRateLimited

        found_in_cache, result = _check_cache("romnom", romnom, system_id)
        try:
            if not found_in_cache:
                result = self._get_client().search_by_romnom(romnom, system_id)
                result = _reject_placeholder("romnom", romnom, result)

            rejected = result is not None and verify_title and not _romnom_title_matches(
                romnom, result
            )
            if rejected:
                assert result is not None
                logger.info(
                    "ScreenScraper romnom result rejected: '%s' -> '%s'",
                    romnom,
                    result.get("name"),
                )
                result = None

            # Also overwrite a cached false positive with a no-match.
            if not found_in_cache or rejected:
                _save_to_cache("romnom", romnom, system_id, result)

            return (
                self._result_from_dict(result, match_type="romnom") if result else None
            )
        except (requests.RequestException, ScreenScraperRateLimited):
            raise
        except Exception as e:
            logger.debug("ScreenScraper romnom lookup failed for '%s': %s", romnom, e)
            raise

    def _aggregate_name_search(
        self,
        query_name: str,
        system: "System",
        issued: set[tuple[str, int]],
    ) -> Optional[LookupResult]:
        """Search every system ID x every query variant, rank, and accept.

        Candidates are collected across all search system IDs (mapped + family,
        via ``expand_system_ids``) and all query variants, scored against the
        parsed identity and the raw query name, and filtered through the
        per-candidate guards. The ranked winner must pass the margin-based
        acceptance rules in ``_rank_candidates``.

        The first variant is queried across every searchable system. An exact
        aggregate winner returns immediately; otherwise remaining variants run.
        ``issued`` dedupes calls across the game-name and ROM-stem phases.
        """
        from library.metadata.screenscraper import ScreenScraperRateLimited
        from library.metadata.screenscraper import _get_search_variants
        from library.parser import parse_rom_filename

        search_ids = expand_system_ids(system)
        mapped_ids = {str(sid) for sid in system.all_screenscraper_ids}
        searchable_ids = {str(sid) for sid in search_ids}
        identity_name = parse_rom_filename(
            f"{query_name}.rom", arcade=system.archive_as_rom
        )["name"]
        variants = list(
            dict.fromkeys(
                _get_search_variants(identity_name) + _get_search_variants(query_name)
            )
        )

        try:
            candidates: list[dict] = []
            raw_pool: list[dict] = []  # P3e: every clean result, pre-guards

            def _search(variant: str, system_id: int) -> None:
                """Issue one (variant, system) query into the shared pools."""
                pair = (variant.lower(), system_id)
                if pair in issued:
                    return
                issued.add(pair)
                for result in self._get_client().search_game(variant, system_id):
                    if not result.get("id") or str(result.get("system_id")) != str(
                        system_id
                    ):
                        continue
                    names = _clean_names(
                        [
                            n
                            for n in {
                                result.get("name", ""),
                                *result.get("all_names", []),
                            }
                            if n
                        ]
                    )
                    if not names:
                        continue
                    score = _candidate_score(identity_name, query_name, result)
                    # P3e eligibility: rescues never override the hard
                    # "Vs." identity rule or a missing-subtitle verdict.
                    vs_agrees = normalize_name(
                        result.get("name", "")
                    ).startswith("vs ") == normalize_name(
                        identity_name
                    ).startswith("vs ")
                    subtitle_ok = not _subtitle_guard(
                        identity_name, [normalize_name(n) for n in names]
                    )
                    raw_pool.append(
                        {
                            "id": result["id"],
                            "name": result.get("name", ""),
                            "names": names,
                            "system_id": int(result["system_id"]),
                            "score": score,
                            "exact": variant.strip().lower()
                            == identity_name.strip().lower(),
                            "rescuable": vs_agrees and subtitle_ok,
                        }
                    )
                    if score < 0.6:
                        continue
                    # Preserve every meaningful word in the parsed identity.
                    # This blocks a base game from matching a distinct
                    # hack/subtitle solely through the 0.85 substring score.
                    if _subtitle_guard(
                        identity_name, [normalize_name(n) for n in names]
                    ) or _candidate_omits_identity_words(identity_name, names):
                        logger.debug(
                            "Identity guard rejected '%s' -> '%s'",
                            identity_name,
                            result.get("name"),
                        )
                        continue
                    candidates.append(
                        {
                            "id": result["id"],
                            "name": result.get("name", ""),
                            "system_id": int(result["system_id"]),
                            "confidence": score,
                        }
                    )

            def _finish(winner: dict | None) -> Optional[LookupResult]:
                if not winner:
                    logger.debug(
                        "ScreenScraper name search found no match for '%s'",
                        query_name,
                    )
                    return None
                logger.info(
                    "ScreenScraper name search matched '%s' to '%s' (ID: %s, score: %.2f)",
                    query_name,
                    winner["name"],
                    winner["id"],
                    winner["confidence"],
                )
                return self._result_from_dict(winner, match_type="name")

            # Stage 1: the first variant across every searchable system ID.
            for system_id in search_ids:
                for variant in variants[:1]:
                    _search(variant, system_id)
            winner = _rank_candidates(candidates, mapped_ids, searchable_ids)
            if winner and winner["confidence"] == 1.0:
                return _finish(winner)

            # Stage 2: remaining variants, then the full aggregate ranking.
            for variant in variants[1:]:
                for system_id in search_ids:
                    _search(variant, system_id)
            winner = _rank_candidates(candidates, mapped_ids, searchable_ids)
            if not winner:
                winner = self._rescue_candidates(
                    identity_name, query_name, raw_pool, mapped_ids, searchable_ids
                )
            return _finish(winner)

        except (requests.RequestException, ScreenScraperRateLimited):
            raise
        except Exception as e:
            logger.debug("ScreenScraper name search failed for '%s': %s", query_name, e)
            raise

    def _rescue_candidates(
        self,
        identity: str,
        query_name: str,
        raw_pool: list[dict],
        mapped_ids: set[str],
        searchable_ids: set[str],
    ) -> dict | None:
        """P3e: structural rescues when the normal rules reject the pool.

        Candidates must sit on mapped ∪ family systems and carry clean
        names. Rules, in order:
        - rare-token: exactly one candidate shares the query's rarest
          significant token, on a mapped system, runner-up margin >= 0.25,
          candidate score >= 0.60;
        - ss-alias-trust: the exact full-title query returned exactly one
          candidate, on a mapped system, with score >= 0.60;
        - composite-split: an `` And ``-separated part of the candidate
          title equals the identity exactly, on a mapped system.
        Flag: ``matching_rescue_enabled`` (default on).
        """
        if not _flag_enabled("matching_rescue_enabled"):
            return None

        # Dedupe by SS id, preferring copies returned by the exact query.
        dedup: dict[str, dict] = {}
        for cand in raw_pool:
            if str(cand["system_id"]) not in searchable_ids:
                continue
            if not cand.get("rescuable", False):
                continue
            key = str(cand["id"])
            if key not in dedup or (cand["exact"] and not dedup[key]["exact"]):
                dedup[key] = cand
        pool = list(dedup.values())
        if not pool:
            return None
        mapped_pool = [c for c in pool if str(c["system_id"]) in mapped_ids]

        # rare-token
        identity_tokens = [
            t
            for t in normalize_name(identity).split()
            if t not in STOPWORDS and t not in CONSOLE_TERMS
        ]
        freq: dict[str, int] = {}
        for cand in pool:
            cand_words = set(normalize_name(cand["name"]).split())
            for n in cand["names"]:
                cand_words.update(normalize_name(n).split())
            for t in identity_tokens:
                if t in cand_words:
                    freq[t] = freq.get(t, 0) + 1
        for token in identity_tokens:
            if freq.get(token) != 1:
                continue
            matches = [
                c
                for c in mapped_pool
                if c["exact"]
                and token
                in set(normalize_name(c["name"]).split())
                | {w for n in c["names"] for w in normalize_name(n).split()}
            ]
            if len(matches) != 1:
                continue
            cand = matches[0]
            if cand["score"] < 0.60:
                break
            runner_up = max(
                (c["score"] for c in pool if str(c["id"]) != str(cand["id"])),
                default=0.0,
            )
            if cand["score"] - runner_up >= 0.25 or not pool[1:]:
                self._log_rescue("rare-token", query_name, cand)
                return {
                    "id": cand["id"],
                    "name": cand["name"],
                    "system_id": cand["system_id"],
                    "confidence": max(cand["score"], 0.6),
                }
            break

        # ss-alias-trust
        if (
            len(pool) == 1
            and pool[0] in mapped_pool
            and pool[0]["exact"]
            and pool[0]["score"] >= 0.60
        ):
            self._log_rescue("ss-alias-trust", query_name, pool[0])
            return {
                "id": pool[0]["id"],
                "name": pool[0]["name"],
                "system_id": pool[0]["system_id"],
                "confidence": max(pool[0]["score"], 0.6),
            }

        # composite-title split
        norm_identity = normalize_name(identity)
        for cand in mapped_pool:
            parts = re.split(r"\s+And\s+", cand["name"], flags=re.IGNORECASE)
            if any(normalize_name(part) == norm_identity for part in parts):
                self._log_rescue("composite-split", query_name, cand)
                return {
                    "id": cand["id"],
                    "name": cand["name"],
                    "system_id": cand["system_id"],
                    "confidence": max(cand["score"], 1.0),
                }
        return None

    @staticmethod
    def _log_rescue(rule: str, query_name: str, cand: dict) -> None:
        """Grep-friendly acceptance line for the rerun report."""
        logger.info(
            "rescue accepted rule=%s game=%s id=%s score=%.2f",
            rule,
            query_name,
            cand["id"],
            cand.get("score", 0.0),
        )


    def _result_from_dict(
        self,
        result: dict,
        *,
        match_type: str,
    ) -> LookupResult:
        """Convert a ScreenScraper result with explicit match provenance."""
        confidence = result.get("confidence")
        return LookupResult(
            name=result.get("name", ""),
            region="",
            revision="",
            tags=[],
            source="screenscraper",
            confidence=float(confidence) if confidence is not None else 0.85,
            raw_name=result.get("name", ""),
            screenscraper_id=result.get("id"),
            match_type=match_type,
            matched_system_id=result.get("system_id"),
        )

    def is_available(self, system: "System") -> bool:
        """Check if ScreenScraper is available for this system.

        Returns True if the system has ScreenScraper IDs configured.
        """
        return bool(system.all_screenscraper_ids)
