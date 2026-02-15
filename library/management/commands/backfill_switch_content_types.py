"""Management command to backfill Switch content types for existing ROMs."""

from django.core.management.base import BaseCommand

from library.models import ROM
from library.parser import detect_switch_content_type, extract_switch_title_id
from library.romset_scoring import recalculate_default_romset


class Command(BaseCommand):
    help = "Backfill content_type and switch_title_id for existing Switch ROMs"

    def add_arguments(self, parser):
        parser.add_argument(
            "--dry-run",
            action="store_true",
            help="Show what would change without making changes",
        )

    def handle(self, *args, **options):
        dry_run = options["dry_run"]

        if dry_run:
            self.stdout.write(self.style.WARNING("DRY RUN - no changes will be made\n"))

        # Get all Switch ROMs without content_type set
        switch_roms = ROM.objects.filter(
            rom_set__game__system__slug="switch",
            content_type="",
        ).select_related("rom_set__game")

        updated = 0
        skipped = 0
        games_to_recalc = set()

        for rom in switch_roms.iterator(chunk_size=500):
            title_id = extract_switch_title_id(rom.file_name)

            if not title_id:
                skipped += 1
                continue

            content_type = detect_switch_content_type(title_id)

            if dry_run:
                self.stdout.write(
                    f"  {rom.file_name}: {content_type} (TID: {title_id})"
                )
            else:
                rom.switch_title_id = title_id
                rom.content_type = content_type
                rom.save(update_fields=["switch_title_id", "content_type"])
                games_to_recalc.add(rom.rom_set.game_id)

            updated += 1

        # Recalculate default ROMSets for affected games
        if not dry_run and games_to_recalc:
            from library.models import Game

            self.stdout.write(f"\nRecalculating defaults for {len(games_to_recalc)} games...")
            for game in Game.objects.filter(pk__in=games_to_recalc).prefetch_related(
                "rom_sets__roms"
            ):
                recalculate_default_romset(game)

        self.stdout.write("")
        action = "Would update" if dry_run else "Updated"
        self.stdout.write(
            self.style.SUCCESS(f"{action} {updated} ROMs, skipped {skipped} (no Title ID)")
        )

        if not dry_run and games_to_recalc:
            self.stdout.write(
                self.style.SUCCESS(f"Recalculated defaults for {len(games_to_recalc)} games")
            )
