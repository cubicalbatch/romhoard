"""Management command to recalculate default ROMSets using priority scoring."""

from django.core.management.base import BaseCommand

from library.models import Game
from library.romset_scoring import get_best_romset, recalculate_default_romset


class Command(BaseCommand):
    help = "Recalculate default ROMSets for games using priority scoring"

    def add_arguments(self, parser):
        parser.add_argument(
            "--dry-run",
            action="store_true",
            help="Show what would change without making changes",
        )
        parser.add_argument(
            "--system",
            type=str,
            help="Only process games for this system slug",
        )
        parser.add_argument(
            "--missing-only",
            action="store_true",
            help="Only process games without a default_rom_set set",
        )

    def handle(self, *args, **options):
        dry_run = options["dry_run"]
        system_slug = options["system"]
        missing_only = options["missing_only"]

        games = Game.objects.prefetch_related("rom_sets__roms")

        if system_slug:
            games = games.filter(system__slug=system_slug)
            self.stdout.write(f"Filtering by system: {system_slug}\n")

        if missing_only:
            games = games.filter(default_rom_set__isnull=True)
            self.stdout.write("Only processing games without default_rom_set\n")

        if dry_run:
            self.stdout.write(self.style.WARNING("DRY RUN - no changes will be made\n"))

        changed = 0
        total = 0

        for game in games.iterator(chunk_size=100):
            total += 1
            best = get_best_romset(game)

            if best and best != game.default_rom_set:
                if dry_run:
                    old_name = (
                        str(game.default_rom_set) if game.default_rom_set else "None"
                    )
                    self.stdout.write(f"{game.name}: {old_name} -> {best}")
                else:
                    recalculate_default_romset(game)
                changed += 1

        action = "Would update" if dry_run else "Updated"
        self.stdout.write("")
        self.stdout.write(self.style.SUCCESS(f"{action} {changed} of {total} games"))
