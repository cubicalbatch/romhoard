"""Management command to import collection(s) from JSON or ZIP files."""

import json
import tempfile
import zipfile
from pathlib import Path

from django.core.management.base import BaseCommand, CommandError

from romcollections.serializers import ImportError as SerializerImportError
from romcollections.serializers import import_collection, import_collection_with_images


class Command(BaseCommand):
    help = "Import collection(s) from JSON or ZIP files"

    def add_arguments(self, parser):
        parser.add_argument(
            "paths",
            nargs="+",
            help="File path(s), directory path(s), or glob patterns to import",
        )
        parser.add_argument(
            "--overwrite",
            action="store_true",
            help="Overwrite existing collections with the same slug",
        )
        parser.add_argument(
            "--dry-run",
            action="store_true",
            help="Validate files without importing",
        )
        parser.add_argument(
            "--with-images",
            action="store_true",
            help="Import images from ZIP files (default: metadata only)",
        )

    def handle(self, *_args, **options):
        paths = options["paths"]
        overwrite = options["overwrite"]
        dry_run = options["dry_run"]
        with_images = options["with_images"]

        # Expand paths and collect all files
        all_files = []
        for pattern in paths:
            path = Path(pattern)
            if path.is_dir():
                # Recursively find all .json and .zip files in directory
                all_files.extend(path.rglob("*.json"))
                all_files.extend(path.rglob("*.zip"))
            elif path.is_file():
                all_files.append(path)
            elif "*" in pattern or "?" in pattern:
                # Glob pattern
                base = Path(".")
                all_files.extend(base.glob(pattern))
            else:
                raise CommandError(f"Path not found: {pattern}")

        # Filter to only .json and .zip files
        all_files = [f for f in all_files if f.suffix.lower() in (".json", ".zip")]

        if not all_files:
            raise CommandError("No importable files found (.json or .zip)")

        self.stdout.write(f"Found {len(all_files)} file(s) to import")
        if dry_run:
            self.stdout.write(self.style.WARNING("DRY RUN - no changes will be made"))

        total_imported = 0
        total_entries = 0
        total_games_created = 0
        total_metadata_jobs = 0
        errors = []

        for file_path in sorted(all_files):
            try:
                if file_path.suffix.lower() == ".zip":
                    result = self._import_zip(
                        file_path,
                        overwrite=overwrite,
                        dry_run=dry_run,
                        with_images=with_images,
                    )
                else:
                    result = self._import_json(
                        file_path, overwrite=overwrite, dry_run=dry_run
                    )

                if result:
                    total_imported += 1
                    total_entries += result["entries_imported"]
                    total_games_created += result["games_created"]
                    total_metadata_jobs += result["metadata_jobs_queued"]

                    self.stdout.write(
                        self.style.SUCCESS(
                            f"  {file_path.name}: {result['collection'].name} - "
                            f"{result['entries_imported']} entries, {result['games_created']} games created"
                        )
                    )

                    for warning in result.get("warnings", []):
                        self.stdout.write(self.style.WARNING(f"    Warning: {warning}"))

            except json.JSONDecodeError as e:
                errors.append((file_path, f"Invalid JSON: {e}"))
                self.stderr.write(self.style.ERROR(f"  {file_path.name}: Invalid JSON"))
            except SerializerImportError as e:
                errors.append((file_path, str(e)))
                self.stderr.write(self.style.ERROR(f"  {file_path.name}: {e}"))
            except zipfile.BadZipFile as e:
                errors.append((file_path, f"Invalid ZIP: {e}"))
                self.stderr.write(self.style.ERROR(f"  {file_path.name}: Invalid ZIP"))
            except Exception as e:
                errors.append((file_path, str(e)))
                self.stderr.write(self.style.ERROR(f"  {file_path.name}: {e}"))

        # Summary
        self.stdout.write("")
        if dry_run:
            self.stdout.write(f"Validated {len(all_files) - len(errors)} file(s)")
        else:
            self.stdout.write(
                self.style.SUCCESS(
                    f"Imported {total_imported} collection(s), "
                    f"{total_entries} entries, {total_games_created} games created"
                )
            )
            if total_metadata_jobs:
                self.stdout.write(f"Queued {total_metadata_jobs} metadata lookup(s)")

        if errors:
            self.stdout.write(self.style.ERROR(f"Failed: {len(errors)} file(s)"))
            for path, error in errors:
                self.stderr.write(f"  {path}: {error}")

    def _import_json(self, file_path, *, overwrite, dry_run):
        """Import a JSON file."""
        with open(file_path, "r", encoding="utf-8") as f:
            data = json.load(f)

        if dry_run:
            from romcollections.serializers import validate_import_data

            validate_import_data(data)
            slug = data["collection"]["slug"]
            name = data["collection"]["name"]
            entry_count = len(data.get("entries", []))
            self.stdout.write(
                f"  {file_path.name}: {name} ({slug}) - {entry_count} entries (valid)"
            )
            return None

        return import_collection(data, overwrite=overwrite)

    def _import_zip(self, file_path, *, overwrite, dry_run, with_images):
        """Import a ZIP file containing collection.json."""
        with zipfile.ZipFile(file_path, "r") as zipf:
            # Check for collection.json
            if "collection.json" not in zipf.namelist():
                raise SerializerImportError("ZIP missing collection.json")

            # Read collection.json
            with zipf.open("collection.json") as f:
                data = json.load(f)

            if dry_run:
                from romcollections.serializers import validate_import_data

                validate_import_data(data)
                slug = data["collection"]["slug"]
                name = data["collection"]["name"]
                entry_count = len(data.get("entries", []))
                has_images = any(name.startswith("images/") for name in zipf.namelist())
                self.stdout.write(
                    f"  {file_path.name}: {name} ({slug}) - {entry_count} entries "
                    f"({'with images' if has_images else 'metadata only'})"
                )
                return None

            if with_images:
                # Extract to temp directory and import with images
                with tempfile.TemporaryDirectory() as tmpdir:
                    zipf.extractall(tmpdir)
                    return import_collection_with_images(
                        data, tmpdir, overwrite=overwrite
                    )
            else:
                # Import metadata only (collection.json)
                return import_collection(data, overwrite=overwrite)
