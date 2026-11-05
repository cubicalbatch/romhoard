"""Regressions for scan data preservation and fast background workers."""

from unittest.mock import MagicMock, patch

import pytest

from library.models import (
    Game,
    MetadataBatch,
    MetadataJob,
    ROM,
    ROMSet,
    ScanJob,
    System,
)
from library.scanner import scan_directory
from library.tasks import queue_game_metadata, queue_metadata_batch, run_scan


@pytest.fixture
def game(db):
    system = System.objects.create(
        name="Audit",
        slug="audit",
        extensions=[".rom"],
        folder_names=[],
        screenscraper_ids=[1],
    )
    return Game.objects.create(name="Game", system=system)


@pytest.mark.parametrize("failure", ["parse", "stat", "insert"])
def test_failed_reclassification_preserves_old_rom_and_metadata(
    game, tmp_path, failure
):
    """A replacement must succeed before deleting the previous classification."""
    System.objects.create(
        name="New system",
        slug="new",
        exclusive_extensions=[".rom"],
        extensions=[".rom"],
        folder_names=[],
    )
    path = tmp_path / "Game.rom"
    path.write_bytes(b"game")
    game.description = "User's existing metadata"
    game.save()
    rom_set = ROMSet.objects.create(game=game)
    rom = ROM.objects.create(
        rom_set=rom_set, file_path=str(path), file_name=path.name, file_size=4
    )
    target = {
        "parse": "library.scanner.parse_rom_filename",
        "stat": "library.scanner.os.path.getsize",
        "insert": "library.scanner.ROM.objects.create",
    }[failure]
    with patch(target, side_effect=OSError("Simulated unreadable file")):
        result = scan_directory(str(tmp_path), fetch_metadata=False)

    assert result["errors"]
    assert result["reclassified"] == 0
    assert ROM.objects.filter(pk=rom.pk, rom_set=rom_set).exists()
    game.refresh_from_db()
    assert game.description == "User's existing metadata"


def test_queue_metadata_keeps_fast_worker_results(game):
    def finish_job(**kwargs):
        MetadataJob.objects.filter(pk=kwargs["metadata_job_id"]).update(
            status=MetadataJob.STATUS_COMPLETED, matched=True, images_downloaded=3
        )
        return 123

    with (
        patch(
            "library.metadata.screenscraper.screenscraper_available", return_value=True
        ),
        patch("library.tasks.run_metadata_job_for_game") as task,
    ):
        task.configure.return_value.defer.side_effect = finish_job
        assert queue_game_metadata(game)

    job = MetadataJob.objects.get(game=game)
    assert job.task_id == "123"
    assert job.status == MetadataJob.STATUS_COMPLETED
    assert job.matched
    assert job.images_downloaded == 3


def test_batch_completed_while_enqueueing_does_not_remain_pending(game):
    batch = MetadataBatch.objects.create(status=MetadataBatch.STATUS_QUEUING)

    def finish_job(**kwargs):
        MetadataJob.objects.filter(pk=kwargs["metadata_job_id"]).update(
            status=MetadataJob.STATUS_COMPLETED
        )
        return 124

    with (
        patch(
            "library.metadata.screenscraper.screenscraper_available", return_value=True
        ),
        patch("library.tasks.run_metadata_job_for_game") as task,
    ):
        task.configure.return_value.defer.side_effect = finish_job
        queue_metadata_batch.func(batch.pk)

    batch.refresh_from_db()
    assert batch.status == MetadataBatch.STATUS_COMPLETED
    assert batch.completed_at is not None


def test_scan_completion_retains_progress_counts(db):
    job = ScanJob.objects.create(path="/unused")
    context = MagicMock()
    context.should_abort.return_value = False

    def scan(path, progress_callback, **kwargs):
        progress_callback({"files_processed": 12, "roms_found": 7, "images_found": 2})
        return {"added": 7, "images_added": 2}

    with (
        patch("library.tasks.scan_directory", side_effect=scan),
        patch("library.tasks.auto_fetch_system_icons"),
    ):
        run_scan.func(context, job.pk)

    job.refresh_from_db()
    assert job.status == ScanJob.STATUS_COMPLETED
    assert job.files_processed == 12
    assert job.roms_found == 7
    assert job.images_found == 2


def test_fetch_metadata_view_keeps_fast_worker_results(game, client):
    from django.urls import reverse

    def finish_job(**kwargs):
        MetadataJob.objects.filter(pk=kwargs["metadata_job_id"]).update(
            status=MetadataJob.STATUS_COMPLETED, matched=True
        )
        return 125

    with patch("library.tasks.run_metadata_job_for_game") as task:
        task.configure.return_value.defer.side_effect = finish_job
        response = client.post(reverse("library:fetch_game_metadata", args=[game.pk]))

    assert response.status_code == 302
    job = MetadataJob.objects.get(game=game)
    assert job.task_id == "125"
    assert job.status == MetadataJob.STATUS_COMPLETED
    assert job.matched


@pytest.mark.parametrize(
    "status", [MetadataBatch.STATUS_QUEUING, MetadataBatch.STATUS_CANCELLED]
)
def test_batch_completion_preserves_queuing_and_cancellation(db, status):
    from library.tasks import _check_batch_completion

    batch = MetadataBatch.objects.create(status=status)
    _check_batch_completion(batch)
    batch.refresh_from_db()
    assert batch.status == status
    assert batch.completed_at is None


def test_enqueue_rolls_back_task_if_job_id_cannot_be_saved(db):
    from django.db import connection
    from library.queues import enqueue_job

    job = ScanJob.objects.create(path="/unused")
    with connection.cursor() as cursor:
        cursor.execute("SELECT count(*) FROM procrastinate_jobs")
        before = cursor.fetchone()[0]

    with patch.object(ScanJob.objects, "filter") as records:
        records.return_value.update.side_effect = RuntimeError("Failed to save task ID")
        with pytest.raises(RuntimeError, match="Failed to save task ID"):
            enqueue_job(job, run_scan, scan_job_id=job.pk)

    with connection.cursor() as cursor:
        cursor.execute("SELECT count(*) FROM procrastinate_jobs")
        assert cursor.fetchone()[0] == before
    job.refresh_from_db()
    assert job.task_id is None


def test_metadata_merge_preserves_recalculated_default_romset(game):
    from library.metadata.matcher import apply_metadata_to_game

    canonical = Game.objects.create(
        name="Canonical", system=game.system, screenscraper_id=123
    )
    rom_set = ROMSet.objects.create(game=game, region="USA")
    ROM.objects.create(
        rom_set=rom_set, file_path="/test.rom", file_name="test.rom", file_size=4
    )

    assert apply_metadata_to_game(game, {"id": 123, "description": "Updated metadata"})

    canonical.refresh_from_db()
    assert canonical.default_rom_set_id == rom_set.pk
    assert canonical.description == "Updated metadata"
    assert not Game.objects.filter(pk=game.pk).exists()


def test_unreadable_archive_size_preserves_existing_members(game, tmp_path):
    import zipfile

    system = System.objects.create(
        name="Arcade",
        slug="arcade-audit",
        extensions=[".zip"],
        folder_names=[tmp_path.name],
        archive_as_rom=True,
    )
    path = tmp_path / "Game.zip"
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr("game.rom", b"ROM")
    rom_set = ROMSet.objects.create(game=game)
    rom = ROM.objects.create(
        rom_set=rom_set,
        file_path=f"{path}!game.rom",
        file_name="game.rom",
        archive_path=str(path),
        path_in_archive="game.rom",
        file_size=3,
    )
    with patch("library.scanner.os.path.getsize", side_effect=OSError("Unreadable")):
        result = scan_directory(str(tmp_path), fetch_metadata=False)

    assert system.archive_as_rom
    assert result["errors"]
    assert result["deleted_roms"] == 0
    assert ROM.objects.filter(pk=rom.pk).exists()
