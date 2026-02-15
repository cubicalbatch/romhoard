"""Management command to auto-generate collection covers from box art."""

import uuid

from django.core.management.base import BaseCommand

from library.queues import PRIORITY_HIGH
from romcollections.models import Collection, CoverJob
from romcollections.tasks import generate_collection_cover


class Command(BaseCommand):
    help = "Auto-generate collection covers from game box art"

    def add_arguments(self, parser):
        parser.add_argument(
            "--all",
            action="store_true",
            help="Regenerate covers for all collections, even those with existing covers",
        )
        parser.add_argument(
            "--image-type",
            choices=["cover", "screenshot", "mix"],
            default="cover",
            help="Type of game images to use for collage (default: cover)",
        )
        parser.add_argument(
            "--dry-run",
            action="store_true",
            help="Show what would be done without making changes",
        )

    def handle(self, *_args, **options):
        regenerate_all = options["all"]
        image_type = options["image_type"]
        dry_run = options["dry_run"]

        # Get collections to process
        if regenerate_all:
            collections = Collection.objects.all()
        else:
            collections = Collection.objects.filter(has_cover=False)

        total = collections.count()
        if total == 0:
            self.stdout.write("No collections to process")
            return

        self.stdout.write(f"Found {total} collection(s) to process")
        if dry_run:
            self.stdout.write(self.style.WARNING("DRY RUN - no changes will be made"))

        queued = 0
        skipped = 0

        for collection in collections:
            # Check if collection has games with images
            sample_covers = collection.get_sample_covers(limit=1)
            if not sample_covers:
                self.stdout.write(
                    self.style.WARNING(
                        f"  {collection.name}: Skipped (no games with images)"
                    )
                )
                skipped += 1
                continue

            if dry_run:
                self.stdout.write(f"  {collection.name}: Would generate cover")
                queued += 1
                continue

            # Create cover job and queue task
            task_id = str(uuid.uuid4())
            job = CoverJob.objects.create(
                collection=collection,
                task_id=task_id,
                job_type=CoverJob.JOB_TYPE_GENERATE,
                image_type=image_type,
            )

            generate_collection_cover.configure(priority=PRIORITY_HIGH).defer(
                cover_job_id=job.pk
            )

            self.stdout.write(
                self.style.SUCCESS(f"  {collection.name}: Queued cover generation")
            )
            queued += 1

        # Summary
        self.stdout.write("")
        if dry_run:
            self.stdout.write(f"Would queue {queued} cover generation(s)")
        else:
            self.stdout.write(
                self.style.SUCCESS(f"Queued {queued} cover generation(s)")
            )
        if skipped:
            self.stdout.write(f"Skipped {skipped} collection(s) (no images available)")

        if not dry_run and queued:
            self.stdout.write("")
            self.stdout.write("Run 'uv run ./manage.py worker' to process the queue")
