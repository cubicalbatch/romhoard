"""CHD (Compressed Hunks of Data) file utilities.

CHD files are compressed disc images used by MAME and other emulators.
The internal SHA1 hash (not the file's SHA1) is used for identification
against databases like Redump and Hasheous. It is stored in the CHD header,
so it is read directly without decompressing anything.
"""

import logging
import struct

logger = logging.getLogger(__name__)

CHD_MAGIC = b"MComprHD"

# Offset of the SHA1 that `chdman info` reports as "SHA1", per header
# version (from MAME's chd.cpp). v5's is the combined data+metadata SHA1;
# versions 1-2 store MD5 only.
SHA1_OFFSETS = {3: 80, 4: 48, 5: 84}
HEADER_PREFIX = 16  # magic (8) + header length (4) + version (4)


def extract_chd_sha1(file_path: str) -> str | None:
    """Read the internal SHA1 hash from a CHD file header.

    This SHA1 represents the original uncompressed disc data and is what
    Redump/Hasheous databases use for identification. It matches the
    "SHA1" line of `chdman info`.

    Args:
        file_path: Path to the CHD file

    Returns:
        40-character lowercase SHA1 hex string, or None if the file is not a
        readable CHD v3-v5 or has no SHA1.

    Example:
        >>> extract_chd_sha1("/path/to/game.chd")
        '5e9463fa3c0181d4fc7cf3354c50893ab6ad37d2'
    """
    try:
        with open(file_path, "rb") as f:
            header = f.read(124)  # v5 header length, the largest
    except OSError as e:
        logger.warning("Cannot read CHD header of %s: %s", file_path, e)
        return None

    if len(header) < HEADER_PREFIX or header[:8] != CHD_MAGIC:
        logger.debug("Not a CHD file: %s", file_path)
        return None

    length, version = struct.unpack(">II", header[8:16])
    offset = SHA1_OFFSETS.get(version)
    if offset is None or len(header) < min(length, offset + 20):
        logger.debug("Unsupported CHD version %d: %s", version, file_path)
        return None

    sha1 = header[offset : offset + 20]
    if sha1 == bytes(20):
        return None
    return sha1.hex()


def is_chd_file(filename: str) -> bool:
    """Check if a file is a CHD file based on extension."""
    return filename.lower().endswith(".chd")
