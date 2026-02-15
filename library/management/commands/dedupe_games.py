"""Management command to find and merge duplicate games."""

from django.core.management.base import BaseCommand

from library.merge import (
    find_duplicate_groups_by_hash,
    find_duplicate_groups_by_name_case,
    find_duplicate_groups_by_screenscraper_id,
    merge_duplicate_group,
    select_canonical_game,
)


class Command(BaseCommand):
    help = "Find and merge duplicate games in the library"

    def add_arguments(self, parser):
        parser.add_argument(
            "--dry-run",
            action="store_true",
            help="Show what would be merged without making changes",
        )
        parser.add_argument(
            "--by-screenscraper",
            action="store_true",
            help="Find duplicates with same ScreenScraper ID",
        )
        parser.add_argument(
            "--by-name",
            action="store_true",
            help="Find duplicates with same name (case-insensitive)",
        )
        parser.add_argument(
            "--by-hash",
            action="store_true",
            help="Find duplicates whose ROMs share the same hash",
        )
        parser.add_argument(
            "--all",
            action="store_true",
            help="Run all duplicate detection methods",
        )
        parser.add_argument(
            "--system",
            type=str,
            help="Limit to specific system slug (e.g., arcade, gba)",
        )

    def handle(self, *args, **options):
        dry_run = options["dry_run"]
        system_slug = options["system"]
        run_all = options["all"]

        # Determine which methods to run
        by_screenscraper = options["by_screenscraper"] or run_all
        by_name = options["by_name"] or run_all
        by_hash = options["by_hash"] or run_all

        if not any([by_screenscraper, by_name, by_hash]):
            self.stdout.write(
                self.style.ERROR(
                    "Specify at least one method: --by-screenscraper, --by-name, --by-hash, or --all"
                )
            )
            return

        if dry_run:
            self.stdout.write(self.style.WARNING("DRY RUN - no changes will be made\n"))

        if system_slug:
            self.stdout.write(f"Filtering by system: {system_slug}\n")

        total_groups = 0
        total_merged = 0

        # 1. ScreenScraper ID duplicates
        if by_screenscraper:
            groups, merged = self._process_duplicates(
                "ScreenScraper ID",
                find_duplicate_groups_by_screenscraper_id(system_slug),
                dry_run,
            )
            total_groups += groups
            total_merged += merged

        # 2. Case-insensitive name duplicates
        if by_name:
            groups, merged = self._process_duplicates(
                "Case-insensitive name",
                find_duplicate_groups_by_name_case(system_slug),
                dry_run,
            )
            total_groups += groups
            total_merged += merged

        # 3. Hash-based duplicates
        if by_hash:
            groups, merged = self._process_duplicates(
                "Hash match",
                find_duplicate_groups_by_hash(system_slug),
                dry_run,
            )
            total_groups += groups
            total_merged += merged

        # Summary
        self.stdout.write("")
        if dry_run:
            self.stdout.write(
                self.style.SUCCESS(
                    f"Found {total_groups} duplicate groups ({total_merged} games would be merged)"
                )
            )
        else:
            self.stdout.write(
                self.style.SUCCESS(
                    f"Processed {total_groups} duplicate groups ({total_merged} games merged)"
                )
            )

    def _process_duplicates(
        self, method_name: str, groups: list, dry_run: bool
    ) -> tuple[int, int]:
        """Process duplicate groups from a detection method."""
        if not groups:
            self.stdout.write(f"\n{method_name}: No duplicates found")
            return 0, 0

        self.stdout.write(f"\n{method_name}: Found {len(groups)} duplicate groups")
        self.stdout.write("-" * 60)

        merged_count = 0

        for group in groups:
            canonical = select_canonical_game(group)
            duplicates = [g for g in group if g.pk != canonical.pk]

            self.stdout.write(f"\nGroup ({len(group)} games):")
            self.stdout.write(f"  Canonical: {canonical.name} (pk={canonical.pk})")
            self.stdout.write(f"    System: {canonical.system.slug}")
            self.stdout.write(
                f"    ScreenScraper ID: {canonical.screenscraper_id or 'N/A'}"
            )
            self.stdout.write(f"    Name source: {canonical.name_source}")
            self.stdout.write(f"    ROMs: {canonical.rom_sets.count()} sets")

            for dup in duplicates:
                self.stdout.write(f"  Duplicate: {dup.name} (pk={dup.pk})")
                self.stdout.write(
                    f"    ScreenScraper ID: {dup.screenscraper_id or 'N/A'}"
                )
                self.stdout.write(f"    Name source: {dup.name_source}")
                self.stdout.write(f"    ROMs: {dup.rom_sets.count()} sets")

            if not dry_run:
                result = merge_duplicate_group(group)
                if result:
                    merged_count += result["merged_count"]
                    self.stdout.write(
                        self.style.SUCCESS(
                            f"  -> Merged {result['merged_count']} games "
                            f"({result['roms_moved']} ROMs moved)"
                        )
                    )
            else:
                merged_count += len(duplicates)

        return len(groups), merged_count
