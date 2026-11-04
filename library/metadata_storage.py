"""Inventory metadata storage independently of how artwork was registered."""

import os
from dataclasses import dataclass, field
from pathlib import Path

from .models import GameImage, System


@dataclass
class MetadataFile:
    path: Path
    images: list = field(default_factory=list)
    systems: list = field(default_factory=list)


def metadata_inventory(base_path: str | Path) -> dict[Path, MetadataFile]:
    """Include files on disk and missing DB references inside this storage root.

    Source labels are deliberately ignored: cached downloads used to be marked
    as scanned. ROM-side artwork outside the root is never migrated. Do not
    follow symlinks into other storage locations.
    """
    if not Path(base_path).is_absolute():
        raise ValueError("Metadata storage requires an absolute path.")
    base = Path(base_path).resolve()
    files = {}

    def include(path):
        path = Path(os.path.abspath(path))
        if path.is_symlink():
            return None
        path = path.resolve()
        if not path.is_relative_to(base):
            return None
        return files.setdefault(path, MetadataFile(path))

    def on_error(error):
        raise error

    directories = os.walk(base, onerror=on_error) if base.exists() else []
    for directory, _, names in directories:
        for name in names:
            path = Path(directory) / name
            if path.is_file():
                include(path)

    for image in GameImage.objects.all().only("pk", "file_path", "game_id").iterator():
        entry = include(image.file_path)
        if entry is not None:
            entry.images.append(image)
    for system in (
        System.objects.exclude(icon_path="").only("pk", "icon_path").iterator()
    ):
        entry = include(system.icon_path)
        if entry is not None:
            entry.systems.append(system)
    return files


def validate_storage_move(old_base: str | Path, new_base: str | Path) -> None:
    if not Path(old_base).is_absolute() or not Path(new_base).is_absolute():
        raise ValueError("Metadata storage requires an absolute path.")
    old = Path(old_base).resolve()
    new = Path(new_base).resolve()
    if old == new or old.is_relative_to(new) or new.is_relative_to(old):
        raise ValueError("Metadata locations must be separate, non-nested directories.")
