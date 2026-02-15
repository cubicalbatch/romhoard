"""CHD (Compressed Hunks of Data) file utilities.

CHD files are compressed disc images used by MAME and other emulators.
The internal SHA1 hash (not the file's SHA1) is used for identification
against databases like Redump and Hasheous.

Requires: chdman (from mame-tools package)
"""

import logging
import shutil
import subprocess
from functools import lru_cache

logger = logging.getLogger(__name__)


@lru_cache(maxsize=1)
def is_chdman_available() -> bool:
    """Check if chdman is available on the system."""
    return shutil.which("chdman") is not None


def extract_chd_sha1(file_path: str) -> str | None:
    """Extract the internal SHA1 hash from a CHD file.

    CHD files store disc images with internal metadata including SHA1.
    This SHA1 represents the original uncompressed disc data and is
    what Redump/Hasheous databases use for identification.

    Args:
        file_path: Path to the CHD file

    Returns:
        40-character SHA1 hex string, or None if extraction fails

    Example:
        >>> sha1 = extract_chd_sha1("/path/to/game.chd")
        >>> sha1
        '5e9463fa3c0181d4fc7cf3354c50893ab6ad37d2'
    """
    if not is_chdman_available():
        logger.debug("chdman not available, cannot extract CHD SHA1")
        return None

    try:
        result = subprocess.run(
            ["chdman", "info", "-i", file_path],
            capture_output=True,
            text=True,
            timeout=30,
        )

        if result.returncode != 0:
            logger.debug("chdman failed for %s: %s", file_path, result.stderr)
            return None

        # Parse output for SHA1 line
        # Format: "SHA1:         5e9463fa3c0181d4fc7cf3354c50893ab6ad37d2"
        for line in result.stdout.splitlines():
            line = line.strip()
            if line.startswith("SHA1:"):
                parts = line.split()
                if len(parts) >= 2:
                    sha1 = parts[1].lower()
                    # Validate it's a proper SHA1 (40 hex chars)
                    if len(sha1) == 40 and all(c in "0123456789abcdef" for c in sha1):
                        logger.debug("Extracted CHD SHA1 for %s: %s", file_path, sha1)
                        return sha1

        logger.debug("No SHA1 found in chdman output for %s", file_path)
        return None

    except subprocess.TimeoutExpired:
        logger.warning("chdman timed out for %s", file_path)
        return None
    except Exception as e:
        logger.warning("Failed to extract CHD SHA1 from %s: %s", file_path, e)
        return None


def is_chd_file(filename: str) -> bool:
    """Check if a file is a CHD file based on extension."""
    return filename.lower().endswith(".chd")
