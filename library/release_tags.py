"""Human-readable descriptions for release tags found in ROM filenames.

The glossary follows the TOSEC naming convention:
https://www.tosecdev.org/tosec-naming-convention

Only tags whose stored form is unambiguous are described. The parser discards
the original parentheses/brackets and the library does not reliably know which
naming convention produced a filename, so conflicting or unsupported codes are
left undescribed rather than guessed.
"""

import re

# Coded flags are case-sensitive and only match the complete tag or
# "<code> <descriptor>". Descriptors are preserved verbatim.
_CODE_TAGS = {
    "cr": "Cracked",
    "f": "Fixed / altered for compatibility",
    "h": "Hacked / deliberately modified",
    "m": "Modified (not necessarily deliberately)",
    "p": "Pirated / unlicensed version",
    "o": "Overdump: duplicated or excess dumped data",
    "u": "Underdump: missing dumped data",
    "v": "Virus-infected",
    "b": "Bad or damaged dump",
    "a": "Alternate original version",
}

# Word tags are matched case-insensitively against the complete tag only.
_WORD_TAGS = {
    "bios": "BIOS: system firmware image",
    "demo": "Demo version",
    "demo-kiosk": "Kiosk demo version",
    "demo-playable": "Playable demo version",
    "demo-rolling": "Rolling demo (non-interactive attract mode)",
    "demo-slideshow": "Slideshow demo",
    "beta": "Beta version",
    "proto": "Prototype version",
    "unl": "Unlicensed release",
}

_TRAINER_PATTERN = re.compile(r"^t\s+\+(\d+)(?:\s+(.+))?$")


def _with_descriptor(description: str, descriptor: str | None) -> str:
    """Append a descriptor to a base description when present."""
    if descriptor:
        return f"{description}: {descriptor}"
    return description


def describe_release_tag(tag: str) -> str | None:
    """Return a concise sourced description, or None when unknown/ambiguous.

    Args:
        tag: Raw release tag as stored on ``ROM.tags`` (without parentheses).

    Returns:
        A short human-readable description, or ``None`` when the tag has no
        unambiguous glossary entry.
    """
    if not isinstance(tag, str):
        return None

    value = tag.strip()
    if not value:
        return None

    if value == "!":
        return "Verified good dump"

    # Parameterized trainer forms come before the generic coded-flag match so
    # "t +2" is not read as a plain descriptor. A malformed numeric form is
    # ambiguous and must not fall through to "trained: <text>".
    if re.match(r"^t\s+\+", value):
        match = _TRAINER_PATTERN.match(value)
        if not match:
            return None
        count = int(match.group(1))
        descriptor = (match.group(2) or "").strip()
        base = f"Trained: {count} trainer{'s' if count != 1 else ''} added"
        return _with_descriptor(base, descriptor)

    if value == "t":
        return "Trained: cheats or a cheat menu added"

    if value == "tr":
        return "Translation"

    translation_match = re.match(r"^tr(?:\s+(.+))?$", value)
    if translation_match:
        descriptor = (translation_match.group(1) or "").strip()
        return _with_descriptor("Translation", descriptor)

    generic_match = re.match(r"^t(?:\s+(.+))?$", value)
    if generic_match:
        descriptor = (generic_match.group(1) or "").strip()
        return _with_descriptor("Trained", descriptor)

    for code, description in _CODE_TAGS.items():
        match = re.fullmatch(re.escape(code) + r"(?:\s+(.+))?", value)
        if match:
            descriptor = (match.group(1) or "").strip()
            return _with_descriptor(description, descriptor)

    return _WORD_TAGS.get(value.casefold())
