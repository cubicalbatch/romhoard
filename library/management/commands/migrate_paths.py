"""Management command to migrate absolute paths to relative paths."""

import os
from django.conf import settings
from django.core.management.base import BaseCommand

from library.models import ROM, GameImage, ScanPath


class Command(BaseCommand):
    help = "Migrate absolute ROM paths to relative paths based on ROM_LIBRARY_ROOT"

    def add_arguments(self, parser):
        parser.add_argument(
            "--dry-run",
            action="store_true",
            help="Show what would be changed without making changes",
        )
        parser.add_argument(
            "--library-root",
            type=str,
            help="Library root path (overrides ROM_LIBRARY_ROOT setting)",
        )
        parser.add_argument(
            "--reverse",
            action="store_true",
            help="Convert relative paths back to absolute paths",
        )

    def handle(self, *args, **options):
        dry_run = options["dry_run"]
        reverse = options["reverse"]
        library_root = options["library_root"] or getattr(
            settings, "ROM_LIBRARY_ROOT", ""
        )

        if not library_root:
            self.stdout.write(
                self.style.ERROR(
                    "ROM_LIBRARY_ROOT is not set. Use --library-root to specify."
                )
            )
            return

        # Normalize the library root path
        library_root = os.path.abspath(library_root)
        if not library_root.endswith(os.sep):
            library_root += os.sep

        if reverse:
            self.stdout.write(
                f"Converting relative paths to absolute using root: {library_root}"
            )
        else:
            self.stdout.write(
                f"Converting absolute paths to relative using root: {library_root}"
            )

        if dry_run:
            self.stdout.write(self.style.WARNING("DRY RUN - no changes will be made"))

        self.stdout.write("")

        # Migrate ROM paths
        rom_count = self._migrate_roms(library_root, dry_run, reverse)

        # Migrate GameImage paths
        image_count = self._migrate_images(library_root, dry_run, reverse)

        # Migrate ScanPath paths
        scan_count = self._migrate_scan_paths(library_root, dry_run, reverse)

        self.stdout.write("")
        self.stdout.write(self.style.SUCCESS("Migration complete!"))
        self.stdout.write(f"  ROMs updated: {rom_count}")
        self.stdout.write(f"  Images updated: {image_count}")
        self.stdout.write(f"  Scan paths updated: {scan_count}")

    def _to_relative(self, absolute_path: str, library_root: str) -> str | None:
        """Convert absolute path to relative path."""
        if not absolute_path:
            return None
        if absolute_path.startswith(library_root):
            return absolute_path[len(library_root) :]
        return None  # Path not under library root

    def _to_absolute(self, relative_path: str, library_root: str) -> str:
        """Convert relative path to absolute path."""
        if not relative_path:
            return relative_path
        if os.path.isabs(relative_path):
            return relative_path  # Already absolute
        return os.path.join(library_root, relative_path)

    def _migrate_roms(self, library_root: str, dry_run: bool, reverse: bool) -> int:
        """Migrate ROM file paths."""
        updated = 0
        roms = ROM.objects.all()

        for rom in roms:
            old_file_path = rom.file_path
            old_archive_path = rom.archive_path

            if reverse:
                # Convert relative to absolute
                new_file_path = self._to_absolute(old_file_path, library_root)
                new_archive_path = (
                    self._to_absolute(old_archive_path, library_root)
                    if old_archive_path
                    else ""
                )
            else:
                # Convert absolute to relative
                new_file_path = self._to_relative(old_file_path, library_root)
                new_archive_path = (
                    self._to_relative(old_archive_path, library_root)
                    if old_archive_path
                    else ""
                )

                if new_file_path is None:
                    continue  # Path not under library root

            if new_file_path != old_file_path or new_archive_path != old_archive_path:
                if dry_run:
                    self.stdout.write(f"  ROM: {old_file_path}")
                    self.stdout.write(f"    -> {new_file_path}")
                else:
                    rom.file_path = new_file_path
                    if new_archive_path is not None:
                        rom.archive_path = new_archive_path
                    rom.save(update_fields=["file_path", "archive_path"])
                updated += 1

        self.stdout.write(f"ROMs to update: {updated}")
        return updated

    def _migrate_images(self, library_root: str, dry_run: bool, reverse: bool) -> int:
        """Migrate GameImage file paths."""
        updated = 0
        images = GameImage.objects.all()

        for image in images:
            old_path = image.file_path

            if reverse:
                new_path = self._to_absolute(old_path, library_root)
            else:
                new_path = self._to_relative(old_path, library_root)
                if new_path is None:
                    continue

            if new_path != old_path:
                if dry_run:
                    self.stdout.write(f"  Image: {old_path}")
                    self.stdout.write(f"    -> {new_path}")
                else:
                    image.file_path = new_path
                    image.save(update_fields=["file_path"])
                updated += 1

        self.stdout.write(f"Images to update: {updated}")
        return updated

    def _migrate_scan_paths(
        self, library_root: str, dry_run: bool, reverse: bool
    ) -> int:
        """Migrate ScanPath paths."""
        updated = 0
        scan_paths = ScanPath.objects.all()

        for scan_path in scan_paths:
            old_path = scan_path.path

            if reverse:
                new_path = self._to_absolute(old_path, library_root)
            else:
                new_path = self._to_relative(old_path, library_root)
                if new_path is None:
                    continue

            if new_path != old_path:
                if dry_run:
                    self.stdout.write(f"  ScanPath: {old_path}")
                    self.stdout.write(f"    -> {new_path}")
                else:
                    scan_path.path = new_path
                    scan_path.save(update_fields=["path"])
                updated += 1

        self.stdout.write(f"Scan paths to update: {updated}")
        return updated
