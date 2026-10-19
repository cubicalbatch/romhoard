"""Regression tests for the unique-constraint race on placeholder task_ids.

Scans, uploads, and manual metadata fetches create job rows with a
placeholder task_id before enqueueing the real Procrastinate task. The
placeholder used to be the literal string "pending", which violated the
unique constraint whenever two creators raced - e.g. two overlapping
scheduled scans, or a scan running while a user clicks "Fetch metadata"
on a game page (seen in production as ScanJob FAILED with
``duplicate key value violates unique constraint
"library_metadatajob_task_id_key"``).

Placeholders must now be NULL: Postgres unique constraints treat NULLs as
distinct, so any number of creators can race safely.
"""

import threading
import time
from itertools import count
from unittest.mock import patch

import pytest

from library.models import Game, MetadataJob, System
from library.tasks import queue_game_metadata


def _make_game(name: str) -> Game:
    system, _ = System.objects.get_or_create(
        slug="taskid-race-test",
        defaults={
            "name": "TaskId Race Test",
            "extensions": [".bin"],
            "folder_names": [],
            "screenscraper_ids": [1],
        },
    )
    return Game.objects.create(name=name, system=system)


@pytest.fixture
def _scraper_available():
    with patch(
        "library.metadata.screenscraper.screenscraper_available", return_value=True
    ):
        yield


@pytest.mark.django_db(transaction=True)
def test_concurrent_metadata_queueing_does_not_collide(_scraper_available):
    """Two racing queue_game_metadata calls must both create their job row."""
    game_a = _make_game("Race Game A")
    game_b = _make_game("Race Game B")

    task_ids = count(1000)
    errors: list[BaseException] = []

    def defer_slowly(**kwargs):
        # Widen the create -> save window so both threads are guaranteed to
        # hold an unsaved placeholder row at the same time.
        time.sleep(0.3)
        return next(task_ids)

    def worker(game: Game, barrier: threading.Barrier):
        try:
            barrier.wait(timeout=5)
            with patch(
                "library.tasks.run_metadata_job_for_game"
            ) as mock_task, connection_cleanup():
                mock_task.configure.return_value.defer.side_effect = defer_slowly
                queue_game_metadata(game)
        except BaseException as exc:  # surfaced to the main thread below
            errors.append(exc)

    class connection_cleanup:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            from django.db import connection

            connection.close()

    barrier = threading.Barrier(2)
    threads = [
        threading.Thread(target=worker, args=(game_a, barrier)),
        threading.Thread(target=worker, args=(game_b, barrier)),
    ]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=15)

    assert errors == [], f"race produced exceptions: {errors!r}"
    jobs = MetadataJob.objects.filter(game__in=[game_a, game_b])
    assert jobs.count() == 2
    stored = list(jobs.values_list("task_id", flat=True))
    assert len(set(stored)) == 2, f"task_ids must be unique: {stored}"
    assert "pending" not in stored
