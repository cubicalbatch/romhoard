"""Management command to export all collections as ZIP files."""

import json
import os
import zipfile
from datetime import datetime
from pathlib import Path

from django.core.management.base import BaseCommand, CommandError
from django.utils import timezone

from romcollections.models import Collection
from romcollections.serializers import export_collection as serialize_export
from romcollections.tasks import export_game_metadata, _sanitize_filename


class Command(BaseCommand):
    help = "Export all collections as ZIP files with images"

    def add_arguments(self, parser):
        parser.add_argument(
            "output_dir",
            nargs="?",
            default="./collection_exports",
            help="Directory to save exported ZIP files (default: ./collection_exports)",
        )
        parser.add_argument(
            "--creator",
            help="Only export collections by this creator",
        )
        parser.add_argument(
            "--with-images",
            action="store_true",
            help="Include game images in the export",
        )
        parser.add_argument(
            "--json-only",
            action="store_true",
            help="Export as JSON files only (no ZIP, no images)",
        )

    def handle(self, *args, **options):
        output_dir = Path(options["output_dir"])
        creator_filter = options["creator"]
        with_images = options["with_images"]
        json_only = options["json_only"]

        # Create output directory
        output_dir.mkdir(parents=True, exist_ok=True)
        self.stdout.write(f"Exporting to: {output_dir.absolute()}")

        # Get collections
        collections = Collection.objects.all()
        if creator_filter:
            collections = collections.filter(creator=creator_filter)

        if not collections:
            raise CommandError("No collections found to export")

        self.stdout.write(f"Found {collections.count()} collection(s) to export")
        self.stdout.write("")

        exported = 0
        failed = 0

        for collection in collections:
            try:
                if json_only:
                    self._export_json(collection, output_dir)
                elif with_images:
                    self._export_with_images(collection, output_dir)
                else:
                    self._export_basic_zip(collection, output_dir)
                exported += 1
            except Exception as e:
                failed += 1
                self.stderr.write(
                    self.style.ERROR(f"  Failed to export {collection.slug}: {e}")
                )

        self.stdout.write("")
        self.stdout.write(
            self.style.SUCCESS(f"Successfully exported {exported} collection(s)")
        )
        if failed:
            self.stdout.write(self.style.ERROR(f"Failed: {failed} collection(s)"))

    def _export_json(self, collection, output_dir):
        """Export collection as JSON file."""
        data = serialize_export(collection)
        filename = f"{collection.creator}_{collection.slug}.json"
        filepath = output_dir / filename

        with open(filepath, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2, ensure_ascii=False)

        self.stdout.write(f"  {filename}")

    def _export_basic_zip(self, collection, output_dir):
        """Export collection as ZIP with JSON only."""
        data = serialize_export(collection)
        filename = f"{collection.creator}_{collection.slug}.zip"
        filepath = output_dir / filename

        with zipfile.ZipFile(filepath, "w", zipfile.ZIP_DEFLATED) as zipf:
            zipf.writestr(
                "collection.json",
                json.dumps(data, indent=2, ensure_ascii=False),
            )

            # Add cover image if exists
            if collection.has_cover and os.path.exists(collection.cover_image_path):
                try:
                    ext = Path(collection.cover_image_path).suffix.lower() or ".png"
                    zipf.write(collection.cover_image_path, f"cover{ext}")
                except (IOError, OSError):
                    pass

        self.stdout.write(f"  {filename}")

    def _export_with_images(self, collection, output_dir):
        """Export collection as ZIP with JSON and all game images."""
        data = serialize_export(collection)
        filename = f"{collection.creator}_{collection.slug}_with_images.zip"
        filepath = output_dir / filename

        with zipfile.ZipFile(filepath, "w", zipfile.ZIP_DEFLATED) as zipf:
            # Add collection.json
            zipf.writestr(
                "collection.json",
                json.dumps(data, indent=2, ensure_ascii=False),
            )

            # Add cover image if exists
            if collection.has_cover and os.path.exists(collection.cover_image_path):
                try:
                    ext = Path(collection.cover_image_path).suffix.lower() or ".png"
                    zipf.write(collection.cover_image_path, f"cover{ext}")
                except (IOError, OSError):
                    pass

            # Process each matched game
            for entry in collection.entries.all():
                game = entry.get_matched_game()
                if not game:
                    continue

                # Create safe folder name
                safe_name = _sanitize_filename(f"{game.name}_{game.system.slug}")

                # Add game metadata JSON
                metadata = export_game_metadata(game)
                zipf.writestr(
                    f"games/{safe_name}.json",
                    json.dumps(metadata, indent=2, ensure_ascii=False),
                )

                # Add images for this game
                for image in game.images.all():
                    if os.path.exists(image.file_path):
                        try:
                            ext = Path(image.file_path).suffix.lower() or ".png"
                            image_filename = (
                                f"{image.image_type}{ext}"
                                if image.image_type
                                else f"unknown{ext}"
                            )
                            image_path = f"images/{safe_name}/{image_filename}"
                            zipf.write(image.file_path, image_path)
                        except (IOError, OSError):
                            pass

        self.stdout.write(f"  {filename}")
