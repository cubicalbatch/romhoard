"""Download service module for ROM files and ROMSet bundles."""

import tempfile
import zipfile
from contextlib import contextmanager
from pathlib import Path
from typing import Generator

from .archive import extract_file_from_archive
from .models import ROM, ROMSet
from .scanner import to_absolute_path


def is_single_rom_archive(rom: ROM) -> bool:
    """Return True if the ROM lives in an archive that contains only this ROM.

    Logic: an archive is 1:1 when the database contains exactly one ROM with
    the same archive_path.

    Args:
        rom: ROM instance to check

    Returns:
        True if the ROM is in a 1:1 archive, False otherwise
    """
    if not rom.is_archived:
        return False
    return ROM.objects.filter(archive_path=rom.archive_path).count() == 1


@contextmanager
def get_rom_file(rom: ROM) -> Generator[tuple[str, str], None, None]:
    """
    Context manager yielding (file_path, filename) for a ROM.
    Extracts archived ROMs to temp, cleans up on exit.

    Args:
        rom: ROM instance to get file for

    Yields:
        Tuple of (file_path, filename) where file_path is the path to the
        actual ROM file (temp file for archived ROMs) and filename is the
        original ROM filename.

    Raises:
        FileNotFoundError: If ROM file cannot be found or extracted
        IOError: If file extraction fails
    """
    if not rom.is_archived:
        # For loose ROMs, yield the original file path (resolved to absolute)
        yield (to_absolute_path(rom.file_path), rom.file_name)
    else:
        # Resolve archive path to absolute
        archive_path = to_absolute_path(rom.archive_path)

        # Extract from archive to temp file
        temp_path = None
        try:
            # Use the actual filename from path_in_archive (not file_name which may be the archive name)
            actual_filename = Path(rom.path_in_archive).name
            extension = Path(actual_filename).suffix
            with tempfile.NamedTemporaryFile(
                delete=False, suffix=extension
            ) as temp_file:
                temp_path = temp_file.name

            # Extract the file from archive to temp location
            extract_file_from_archive(
                archive_path=archive_path,
                internal_path=rom.path_in_archive,
                dest_path=temp_path,
            )

            yield (temp_path, actual_filename)
        finally:
            # Clean up temp file
            if temp_path and Path(temp_path).exists():
                Path(temp_path).unlink()


@contextmanager
def get_rom_file_as_stored(rom: ROM) -> Generator[tuple[str, str], None, None]:
    """Yield (path, filename) for the as-stored download mode.

    Cases:
    1. Loose ROM - return the file path directly.
    2. 1:1 archive - serve the archive file directly.
    3. Multi-ROM archive - fall back to normal extraction (same as extract mode).

    Args:
        rom: ROM instance to get file for

    Yields:
        Tuple of (file_path, filename) where file_path is the path to the
        file and filename is the name to use for the download.

    Raises:
        FileNotFoundError: If ROM file cannot be found or extracted
        IOError: If file extraction fails
    """
    if not rom.is_archived:
        # Loose file - just return the path (resolved to absolute)
        yield (to_absolute_path(rom.file_path), rom.file_name)
        return

    # Resolve archive path to absolute
    archive_path = to_absolute_path(rom.archive_path)

    # 1:1 archive - serve the archive file itself
    if is_single_rom_archive(rom):
        archive_name = Path(archive_path).name
        yield (archive_path, archive_name)
        return

    # Multi-ROM archive - fall back to normal extraction
    with get_rom_file(rom) as result:
        yield result


def create_romset_bundle(rom_set: ROMSet) -> tuple[str, str]:
    """
    Create ZIP bundle of all ROMs in a ROMSet.
    Returns (temp_zip_path, suggested_filename).
    Caller must clean up temp file.

    Handles missing files gracefully by skipping them and adding a
    "missing_files.txt" log to the ZIP.

    Args:
        rom_set: ROMSet instance to create bundle for

    Returns:
        Tuple of (temp_zip_path, suggested_filename)

    Raises:
        FileNotFoundError: If no ROMs are available in the ROMSet
        IOError: If ZIP creation fails
    """
    # Get all ROMs in the ROMSet
    available_roms = rom_set.roms.all()
    if not available_roms.exists():
        raise FileNotFoundError(f"No available ROMs in ROMSet: {rom_set}")

    # Create temp ZIP file
    with tempfile.NamedTemporaryFile(suffix=".zip", delete=False) as temp_file:
        zip_path = temp_file.name

    try:
        missing_files = []
        added_files = 0

        with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as zipf:
            for rom in available_roms:
                try:
                    with get_rom_file(rom) as (rom_path, rom_filename):
                        # For multi-disc ROMs, include disc number in filename
                        if rom.disc is not None:
                            # Insert disc number before extension
                            name_parts = (
                                Path(rom_filename).stem,
                                rom.disc,
                                Path(rom_filename).suffix,
                            )
                            zip_filename = (
                                f"{name_parts[0]} (Disc {name_parts[1]}){name_parts[2]}"
                            )
                        else:
                            zip_filename = rom_filename

                        # Add the ROM file to the ZIP
                        zipf.write(rom_path, zip_filename)
                        added_files += 1

                except (FileNotFoundError, IOError, OSError) as e:
                    # Log the missing file and continue
                    missing_files.append(f"{rom.file_name}: {e}")

            # Add missing files log if any files were skipped
            if missing_files:
                missing_content = "The following files could not be included:\n\n"
                missing_content += "\n".join(missing_files)
                zipf.writestr("missing_files.txt", missing_content)

        # Raise error only if ALL files failed
        if added_files == 0 and missing_files:
            if Path(zip_path).exists():
                Path(zip_path).unlink()
            raise FileNotFoundError(
                f"All files in ROMSet are missing: {', '.join(missing_files)}"
            )

        # Generate the suggested filename for the bundle
        bundle_filename = get_romset_bundle_filename(rom_set)
        return (zip_path, bundle_filename)
    except Exception:
        # Clean up temp file if something went wrong
        if Path(zip_path).exists():
            Path(zip_path).unlink()
        raise


def get_romset_bundle_filename(rom_set: ROMSet) -> str:
    """
    Generate filename like 'Game Name.zip' for a ROMSet.

    Args:
        rom_set: ROMSet instance to generate filename for

    Returns:
        Filename string for the ROMSet bundle
    """
    return f"{rom_set.game.name}.zip"
