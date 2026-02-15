"""Management command to scan directories for ROMs."""

from django.core.management.base import BaseCommand

from library.scanner import scan_directory


class Command(BaseCommand):
    help = "Scan a directory for ROM files and add them to the library"

    def add_arguments(self, parser):
        parser.add_argument(
            "path",
            type=str,
            help="Path to the ROM directory to scan",
        )

    def handle(self, *args, **options):
        path = options["path"]

        self.stdout.write(f"Scanning: {path}")
        self.stdout.write("")

        result = scan_directory(path)

        self.stdout.write(self.style.SUCCESS(f"Added: {result['added']}"))
        self.stdout.write(f"Skipped (already exists): {result['skipped']}")
        self.stdout.write(f"Deleted: {result['deleted_roms']}")

        if result["errors"]:
            self.stdout.write("")
            self.stdout.write(self.style.WARNING(f"Errors: {len(result['errors'])}"))
            for error in result["errors"][:10]:  # Show first 10 errors
                self.stdout.write(f"  - {error}")
            if len(result["errors"]) > 10:
                self.stdout.write(f"  ... and {len(result['errors']) - 10} more")

        self.stdout.write("")
        self.stdout.write("Done!")
