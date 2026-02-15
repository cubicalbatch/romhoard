"""Management command to split ROMSets with ROMs from different sources."""

import os
from collections import defaultdict

from django.core.management.base import BaseCommand

from library.models import ROM, ROMSet


def get_rom_source(rom: ROM) -> str:
    """Get source identifier for a ROM.

    For archived ROMs: archive_path + parent folder within archive
    For loose files: parent directory
    """
    if rom.archive_path:
        if rom.path_in_archive:
            parent_in_archive = os.path.dirname(rom.path_in_archive)
            if parent_in_archive:
                return f"{rom.archive_path}/{parent_in_archive}"
        return rom.archive_path
    return os.path.dirname(rom.file_path)


class Command(BaseCommand):
    help = "Split ROMSets that have ROMs from different sources into separate ROMSets"

    def add_arguments(self, parser):
        parser.add_argument(
            "--dry-run",
            action="store_true",
            help="Show what would be changed without making changes",
        )
        parser.add_argument(
            "--system",
            type=str,
            help="Limit to specific system slug (e.g., gb, gba)",
        )

    def handle(self, *args, **options):
        dry_run = options["dry_run"]
        system_slug = options["system"]

        if dry_run:
            self.stdout.write(self.style.WARNING("DRY RUN - no changes will be made\n"))

        # Get all ROMSets, optionally filtered by system
        romsets = ROMSet.objects.select_related("game__system").prefetch_related("roms")
        if system_slug:
            romsets = romsets.filter(game__system__slug=system_slug)
            self.stdout.write(f"Filtering by system: {system_slug}\n")

        total_updated = 0
        total_split = 0
        total_new_romsets = 0

        for romset in romsets:
            roms = list(romset.roms.all())
            if not roms:
                continue

            # Group ROMs by source
            roms_by_source = defaultdict(list)
            for rom in roms:
                source = get_rom_source(rom)
                roms_by_source[source].append(rom)

            sources = list(roms_by_source.keys())

            if len(sources) == 1:
                # All ROMs from same source - just update source_path if needed
                source = sources[0]
                if romset.source_path != source:
                    if not dry_run:
                        romset.source_path = source
                        romset.save(update_fields=["source_path"])
                    total_updated += 1
            else:
                # Multiple sources - need to split
                self.stdout.write(
                    f"\nROMSet {romset.pk}: {romset.game.name} ({romset.region or 'no region'})"
                )
                self.stdout.write(
                    f"  Has {len(roms)} ROMs from {len(sources)} sources:"
                )

                # Keep first source in original ROMSet, create new ones for others
                primary_source = sources[0]
                primary_roms = roms_by_source[primary_source]

                self.stdout.write(
                    f"  - Primary ({len(primary_roms)} ROMs): {primary_source}"
                )

                if not dry_run:
                    romset.source_path = primary_source
                    romset.save(update_fields=["source_path"])

                # Create new ROMSets for other sources
                for source in sources[1:]:
                    source_roms = roms_by_source[source]
                    self.stdout.write(
                        f"  - New ROMSet ({len(source_roms)} ROMs): {source}"
                    )

                    if not dry_run:
                        # Create new ROMSet
                        new_romset = ROMSet.objects.create(
                            game=romset.game,
                            region=romset.region,
                            revision=romset.revision,
                            source_path=source,
                        )
                        # Move ROMs to new ROMSet
                        ROM.objects.filter(pk__in=[r.pk for r in source_roms]).update(
                            rom_set=new_romset
                        )
                        total_new_romsets += 1

                total_split += 1

        # Summary
        self.stdout.write("")
        if dry_run:
            self.stdout.write(
                self.style.SUCCESS(
                    f"Would update {total_updated} ROMSets with source_path"
                )
            )
            self.stdout.write(
                self.style.SUCCESS(
                    f"Would split {total_split} ROMSets with mixed sources"
                )
            )
        else:
            self.stdout.write(
                self.style.SUCCESS(f"Updated {total_updated} ROMSets with source_path")
            )
            self.stdout.write(
                self.style.SUCCESS(
                    f"Split {total_split} ROMSets, created {total_new_romsets} new ROMSets"
                )
            )
