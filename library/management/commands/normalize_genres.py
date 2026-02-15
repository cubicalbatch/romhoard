"""Consolidate existing genres to canonical names."""

from django.core.management.base import BaseCommand
from django.db import transaction
from django.utils.text import slugify

from library.metadata.normalize import GENRE_NORMALIZATION_MAP
from library.models import Genre


class Command(BaseCommand):
    help = "Normalize existing genres to canonical names"

    def add_arguments(self, parser):
        parser.add_argument(
            "--dry-run",
            action="store_true",
            help="Show what would be normalized without making changes",
        )

    def _get_or_create_genre(self, name: str) -> Genre:
        """Get or create a genre, handling slug collisions."""
        target_slug = slugify(name)

        # First try exact name match
        try:
            return Genre.objects.get(name=name)
        except Genre.DoesNotExist:
            pass

        # Try slug match (handles casing differences like "Board game" vs "Board Game")
        try:
            existing = Genre.objects.get(slug=target_slug)
            # Update name to canonical form if different
            if existing.name != name:
                existing.name = name
                existing.save(update_fields=["name"])
            return existing
        except Genre.DoesNotExist:
            pass

        # Create new genre
        return Genre.objects.create(name=name, slug=target_slug)

    def handle(self, *args, **options):
        dry_run = options["dry_run"]

        if dry_run:
            self.stdout.write(self.style.WARNING("DRY RUN - no changes will be made\n"))

        normalized_count = 0
        deleted_count = 0

        for old_name, new_name in GENRE_NORMALIZATION_MAP.items():
            try:
                old_genre = Genre.objects.get(name=old_name)
            except Genre.DoesNotExist:
                continue

            games = list(old_genre.games.all())
            if not games:
                # No games, just delete empty genre
                if not dry_run:
                    old_genre.delete()
                self.stdout.write(f"Deleted empty genre: {old_name}")
                deleted_count += 1
                continue

            prefix = "[DRY RUN] " if dry_run else ""
            self.stdout.write(f"{prefix}{old_name} -> {new_name} ({len(games)} games)")

            if not dry_run:
                with transaction.atomic():
                    new_genre = self._get_or_create_genre(new_name)
                    for game in games:
                        game.genres.remove(old_genre)
                        game.genres.add(new_genre)
                    old_genre.delete()

            normalized_count += 1
            deleted_count += 1

        # Report final count
        final_count = Genre.objects.count()
        prefix = "[DRY RUN] " if dry_run else ""
        self.stdout.write("")
        self.stdout.write(
            self.style.SUCCESS(
                f"{prefix}Normalized {normalized_count} genres, "
                f"deleted {deleted_count} old genres"
            )
        )
        self.stdout.write(
            self.style.SUCCESS(f"{prefix}Final genre count: {final_count}")
        )
