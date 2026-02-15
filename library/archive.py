"""Archive handling utilities for scanning compressed files."""

import binascii
import logging
import tempfile
import zipfile
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)


class ZipSlipError(ValueError):
    """Raised when archive path traversal attack is detected."""

    pass


def _validate_archive_path(internal_path: str, dest_dir: str) -> Path:
    """Validate that an archive internal path doesn't escape the destination directory.

    Prevents ZIP slip attacks where malicious archives contain paths like
    '../../../etc/passwd' that could write files outside the intended directory.

    Args:
        internal_path: The path inside the archive
        dest_dir: The destination directory for extraction

    Returns:
        The resolved safe path

    Raises:
        ZipSlipError: If the path would escape the destination directory
    """
    # Normalize the destination directory
    dest = Path(dest_dir).resolve()

    # Join and resolve the full path
    # Using Path.joinpath ensures proper path handling
    target = (dest / internal_path).resolve()

    # Check if the resolved path is within the destination
    try:
        target.relative_to(dest)
    except ValueError:
        raise ZipSlipError(
            f"Attempted path traversal in archive: '{internal_path}' "
            f"would escape destination '{dest_dir}'"
        )

    return target


try:
    import py7zr

    HAS_7Z_SUPPORT = True
except ImportError:
    HAS_7Z_SUPPORT = False
    logger.warning("py7zr not available - .7z archive support disabled")

SUPPORTED_ARCHIVE_EXTENSIONS = {".zip", ".7z"}


class ArchiveInfo:
    """Information about a file inside an archive."""

    def __init__(self, name: str, size: int, crc32: str = ""):
        self.name = name  # Path inside archive
        self.size = size  # Uncompressed size
        self.crc32 = crc32  # CRC32 hash as hex string (from archive header)


def list_archive_contents(archive_path: str) -> list[ArchiveInfo]:
    """
    List files inside an archive with their uncompressed sizes.

    Args:
        archive_path: Path to .zip or .7z file

    Returns:
        List of ArchiveInfo objects for each file

    Raises:
        ValueError: If archive format not supported
        IOError: If archive cannot be read
    """
    ext = Path(archive_path).suffix.lower()

    if ext == ".zip":
        return _list_zip_contents(archive_path)
    elif ext == ".7z":
        if not HAS_7Z_SUPPORT:
            raise ValueError("7z support requires py7zr package")
        return _list_7z_contents(archive_path)
    else:
        raise ValueError(f"Unsupported archive format: {ext}")


def _list_zip_contents(archive_path: str) -> list[ArchiveInfo]:
    """List contents of a ZIP file, including CRC32 from headers."""
    contents = []
    try:
        with zipfile.ZipFile(archive_path, "r") as zf:
            for info in zf.infolist():
                if not info.is_dir():
                    # CRC is stored as unsigned 32-bit int, convert to 8-char hex
                    crc_hex = format(info.CRC, "08x") if info.CRC else ""
                    contents.append(
                        ArchiveInfo(
                            name=info.filename,
                            size=info.file_size,  # Uncompressed size
                            crc32=crc_hex,
                        )
                    )
    except Exception as e:
        logger.error("Failed to read ZIP archive %s: %s", archive_path, e)
        raise IOError(f"Failed to read ZIP archive: {e}") from e
    return contents


def _list_7z_contents(archive_path: str) -> list[ArchiveInfo]:
    """List contents of a 7z file with CRC32 from headers."""
    contents = []
    try:
        with py7zr.SevenZipFile(archive_path, "r") as szf:
            for info in szf.list():
                if not info.is_directory:
                    # py7zr exposes CRC32 as int, convert to 8-char hex like ZIP
                    crc_hex = format(info.crc32, "08x") if info.crc32 else ""
                    contents.append(
                        ArchiveInfo(
                            name=info.filename,
                            size=info.uncompressed,
                            crc32=crc_hex,
                        )
                    )
    except Exception as e:
        logger.error("Failed to read 7z archive %s: %s", archive_path, e)
        raise IOError(f"Failed to read 7z archive: {e}") from e
    return contents


def file_exists_in_archive(archive_path: str, internal_path: str) -> bool:
    """
    Check if a specific file exists inside an archive.

    Args:
        archive_path: Path to archive file
        internal_path: Path to file within archive

    Returns:
        True if file exists in archive, False otherwise
    """
    try:
        contents = list_archive_contents(archive_path)
        return any(c.name == internal_path for c in contents)
    except (IOError, ValueError):
        return False


def is_archive_file(filename: str) -> bool:
    """
    Check if filename has a supported archive extension.

    Args:
        filename: Filename to check

    Returns:
        True if file has .zip or .7z extension, False otherwise
    """
    return Path(filename).suffix.lower() in SUPPORTED_ARCHIVE_EXTENSIONS


def is_nested_archive(filename: str) -> bool:
    """
    Check if a filename inside an archive is itself an archive.

    This is used to prevent recursively scanning archives within archives.

    Args:
        filename: Filename to check

    Returns:
        True if filename is an archive, False otherwise
    """
    return is_archive_file(filename)


def extract_file_from_archive(
    archive_path: str, internal_path: str, dest_path: str
) -> None:
    """
    Extract a single file from .zip or .7z archive to destination path.

    Args:
        archive_path: Path to the archive file
        internal_path: Path of the file within the archive to extract
        dest_path: Destination path where the file should be extracted

    Raises:
        FileNotFoundError: If internal_path doesn't exist in the archive
        ValueError: If archive format is unsupported
        IOError: If archive cannot be read or extraction fails
    """
    # First check if the file exists in the archive
    if not file_exists_in_archive(archive_path, internal_path):
        raise FileNotFoundError(
            f"File '{internal_path}' not found in archive '{archive_path}'"
        )

    ext = Path(archive_path).suffix.lower()

    if ext == ".zip":
        _extract_from_zip(archive_path, internal_path, dest_path)
    elif ext == ".7z":
        if not HAS_7Z_SUPPORT:
            raise ValueError("7z support requires py7zr package")
        _extract_from_7z(archive_path, internal_path, dest_path)
    else:
        raise ValueError(f"Unsupported archive format: {ext}")


def _extract_from_zip(archive_path: str, internal_path: str, dest_path: str) -> None:
    """Extract a file from a ZIP archive.

    Validates paths to prevent ZIP slip attacks.
    """
    try:
        with zipfile.ZipFile(archive_path, "r") as zf:
            # If dest_path is a directory, extract to that directory
            dest = Path(dest_path)
            if dest.is_dir():
                # Validate path to prevent ZIP slip
                _validate_archive_path(internal_path, dest_path)
                zf.extract(internal_path, path=dest_path)
            else:
                # Extract to parent directory - validate against parent
                parent_dir = dest.parent
                _validate_archive_path(internal_path, str(parent_dir))
                # Extract to exact path via memory (safer)
                with zf.open(internal_path) as source, open(dest_path, "wb") as target:
                    target.write(source.read())
    except ZipSlipError:
        raise  # Re-raise our security exception
    except KeyError:
        # This should not happen as we check file_exists_in_archive first,
        # but keeping it as a safety measure
        raise FileNotFoundError(
            f"File '{internal_path}' not found in ZIP archive"
        ) from None
    except Exception as e:
        logger.error("Failed to extract from ZIP archive %s: %s", archive_path, e)
        raise IOError(f"Failed to extract from ZIP archive: {e}") from e


def _extract_from_7z(archive_path: str, internal_path: str, dest_path: str) -> None:
    """Extract a file from a 7z archive.

    Validates paths to prevent ZIP slip attacks.
    """
    import shutil

    try:
        dest = Path(dest_path)
        if dest.is_dir():
            # Validate path to prevent ZIP slip before extraction
            _validate_archive_path(internal_path, dest_path)
            # Extract directly to directory
            with py7zr.SevenZipFile(archive_path, "r") as szf:
                szf.extract(targets=[internal_path], path=dest_path)
        else:
            # py7zr only extracts to directories, so use a temp dir
            # then copy the file to the destination path
            with tempfile.TemporaryDirectory() as temp_dir:
                # Validate path even for temp extraction
                _validate_archive_path(internal_path, temp_dir)

                with py7zr.SevenZipFile(archive_path, "r") as szf:
                    szf.extract(targets=[internal_path], path=temp_dir)

                # The extracted file path is already validated above
                extracted_file = Path(temp_dir) / internal_path
                # Double-check the resolved path is within temp_dir
                resolved = extracted_file.resolve()
                if not str(resolved).startswith(str(Path(temp_dir).resolve())):
                    raise ZipSlipError(
                        f"Extracted file escaped temp directory: {internal_path}"
                    )

                if not resolved.exists():
                    raise FileNotFoundError(
                        f"File '{internal_path}' not found after extraction"
                    )

                # Copy to destination (overwrites if exists)
                shutil.copy2(resolved, dest_path)
    except ZipSlipError:
        raise  # Re-raise our security exception
    except Exception as e:
        logger.error("Failed to extract from 7z archive %s: %s", archive_path, e)
        raise IOError(f"Failed to extract from 7z archive: {e}") from e


def compute_file_crc32(file_path: str) -> str:
    """
    Compute CRC32 hash of a file.

    Args:
        file_path: Path to the file

    Returns:
        CRC32 hash as 8-character lowercase hex string

    Raises:
        IOError: If file cannot be read
    """
    try:
        crc = 0
        with open(file_path, "rb") as f:
            while True:
                chunk = f.read(65536)  # 64KB chunks
                if not chunk:
                    break
                crc = binascii.crc32(chunk, crc)
        # Ensure positive value and format as 8-char hex
        return format(crc & 0xFFFFFFFF, "08x")
    except Exception as e:
        raise IOError(f"Failed to compute CRC32 for {file_path}: {e}") from e


def compute_archived_file_crc32(archive_path: str, internal_path: str) -> Optional[str]:
    """
    Get CRC32 for a file inside an archive from headers.

    Both ZIP and 7z formats store CRC32 in headers, so no extraction needed.

    Args:
        archive_path: Path to the archive file
        internal_path: Path to the file within the archive

    Returns:
        CRC32 as 8-character hex string, or None if not available
    """
    try:
        contents = list_archive_contents(archive_path)
        for item in contents:
            if item.name == internal_path and item.crc32:
                return item.crc32
        return None
    except Exception as e:
        logger.warning(
            "Failed to get CRC32 for %s in %s: %s",
            internal_path,
            archive_path,
            e,
        )
        return None
