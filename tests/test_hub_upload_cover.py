"""Tests for auto-generate cover after hub upload and metadata completion."""

import tempfile
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from tests.conftest import create_test_image


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_system(slug="test-sys"):
    from library.models import System

    system, _ = System.objects.get_or_create(
        slug=slug,
        defaults={
            "name": f"System {slug}",
            "extensions": [".rom"],
            "folder_names": [slug],
        },
    )
    return system


def _make_game_with_image(name, system):
    """Create a Game with a real image file on disk."""
    from library.models import Game, GameImage

    game = Game.objects.create(name=name, system=system)

    with tempfile.NamedTemporaryFile(suffix=".png", delete=False) as f:
        f.write(create_test_image(200, 200))
        image_path = f.name

    GameImage.objects.create(
        game=game,
        file_path=image_path,
        file_name="cover.png",
        image_type="cover",
    )
    return game, image_path


def _make_game_without_image(name, system):
    """Create a Game with no images."""
    from library.models import Game

    return Game.objects.create(name=name, system=system)


def _make_collection(slug, entries):
    """Create a Collection with CollectionEntry records.

    entries: list of (game_name, system_slug)
    """
    from romcollections.models import Collection, CollectionEntry

    collection = Collection.objects.create(
        name=f"Collection {slug}",
        slug=slug,
    )
    for idx, (game_name, system_slug) in enumerate(entries):
        CollectionEntry.objects.create(
            collection=collection,
            game_name=game_name,
            system_slug=system_slug,
            position=idx,
        )
    return collection


def _make_metadata_job(game, status):
    from library.models import MetadataJob

    return MetadataJob.objects.create(
        game=game,
        status=status,
        task_id=f"task-{game.pk}-{status}",
    )


# ---------------------------------------------------------------------------
# Tests for maybe_generate_cover (idempotency + basic behaviour)
# ---------------------------------------------------------------------------


class TestMaybeGenerateCover:
    @pytest.mark.django_db
    def test_queues_cover_job_when_images_exist(self, tmp_path):
        """maybe_generate_cover queues a CoverJob when the collection has games with images."""
        from romcollections.models import CoverJob
        from romcollections.tasks import maybe_generate_cover

        system = _make_system("mgc-sys1")
        game, image_path = _make_game_with_image("Game A", system)
        try:
            collection = _make_collection("mgc-col1", [("Game A", "mgc-sys1")])

            with patch(
                "romcollections.tasks.generate_collection_cover"
            ) as mock_task:
                mock_task.configure.return_value.defer.return_value = 42

                result = maybe_generate_cover(collection)

            assert result is not None
            assert isinstance(result, CoverJob)
            assert CoverJob.objects.filter(collection=collection).exists()
        finally:
            Path(image_path).unlink(missing_ok=True)

    @pytest.mark.django_db
    def test_returns_none_when_no_images(self):
        """maybe_generate_cover returns None when no games have images."""
        from romcollections.tasks import maybe_generate_cover

        system = _make_system("mgc-sys2")
        _make_game_without_image("Game B", system)
        collection = _make_collection("mgc-col2", [("Game B", "mgc-sys2")])

        result = maybe_generate_cover(collection)
        assert result is None

    @pytest.mark.django_db
    def test_idempotent_does_not_create_duplicate_job(self, tmp_path):
        """Calling maybe_generate_cover twice creates only one CoverJob."""
        from romcollections.models import CoverJob
        from romcollections.tasks import maybe_generate_cover

        system = _make_system("mgc-sys3")
        game, image_path = _make_game_with_image("Game C", system)
        try:
            collection = _make_collection("mgc-col3", [("Game C", "mgc-sys3")])

            with patch(
                "romcollections.tasks.generate_collection_cover"
            ) as mock_task:
                mock_task.configure.return_value.defer.return_value = 99

                result1 = maybe_generate_cover(collection)
                result2 = maybe_generate_cover(collection)

            assert result1 is not None
            assert result2 is None  # second call skipped (pending job exists)
            assert CoverJob.objects.filter(collection=collection).count() == 1
        finally:
            Path(image_path).unlink(missing_ok=True)

    @pytest.mark.django_db
    def test_skips_when_cover_already_set(self, tmp_path):
        """maybe_generate_cover returns None when collection already has a cover."""
        from romcollections.tasks import maybe_generate_cover

        system = _make_system("mgc-sys4")
        game, image_path = _make_game_with_image("Game D", system)
        try:
            collection = _make_collection("mgc-col4", [("Game D", "mgc-sys4")])
            # Simulate existing cover (both fields must be set together)
            collection.cover_image_path = "/fake/cover.png"
            collection.has_cover = True
            collection.save()

            result = maybe_generate_cover(collection)
            assert result is None
        finally:
            Path(image_path).unlink(missing_ok=True)


# ---------------------------------------------------------------------------
# Tests for hub upload cover trigger
# ---------------------------------------------------------------------------


class TestHubUploadCoverTrigger:
    @pytest.mark.django_db
    def test_cover_triggered_immediately_when_games_have_images(self, tmp_path):
        """After hub upload, cover is queued when matched games already have images."""
        from romcollections.tasks import maybe_generate_cover

        system = _make_system("hub-sys1")
        game, image_path = _make_game_with_image("Zelda", system)
        try:
            collection = _make_collection("hub-col1", [("Zelda", "hub-sys1")])

            with patch(
                "romcollections.tasks.generate_collection_cover"
            ) as mock_task:
                mock_task.configure.return_value.defer.return_value = 77

                result = maybe_generate_cover(collection)

            assert result is not None
        finally:
            Path(image_path).unlink(missing_ok=True)

    @pytest.mark.django_db
    def test_no_cover_immediately_when_games_are_new_without_images(self):
        """After hub upload, no cover is queued for brand-new games with no images yet."""
        from romcollections.tasks import maybe_generate_cover

        system = _make_system("hub-sys2")
        _make_game_without_image("Mario", system)
        collection = _make_collection("hub-col2", [("Mario", "hub-sys2")])

        result = maybe_generate_cover(collection)
        assert result is None


# ---------------------------------------------------------------------------
# Tests for _trigger_cover_for_collections_if_done
# ---------------------------------------------------------------------------


class TestTriggerCoverAfterMetadata:
    @pytest.mark.django_db
    def test_cover_triggered_after_last_job_completes(self, tmp_path):
        """Cover is queued only after ALL metadata jobs for the collection finish."""
        from library.models import MetadataJob
        from library.tasks import _trigger_cover_for_collections_if_done
        from romcollections.models import CoverJob

        system = _make_system("trig-sys1")
        game1, img1 = _make_game_with_image("Kirby", system)
        game2, img2 = _make_game_with_image("Metroid", system)
        try:
            collection = _make_collection(
                "trig-col1",
                [("Kirby", "trig-sys1"), ("Metroid", "trig-sys1")],
            )

            # game2 still has a pending job
            _make_metadata_job(game2, MetadataJob.STATUS_PENDING)

            with patch(
                "romcollections.tasks.generate_collection_cover"
            ) as mock_task:
                mock_task.configure.return_value.defer.return_value = 11

                # game1 just finished; game2 still pending → no cover yet
                _trigger_cover_for_collections_if_done(game1)

            assert not CoverJob.objects.filter(collection=collection).exists()

            # Now mark game2's job as completed
            MetadataJob.objects.filter(game=game2).update(
                status=MetadataJob.STATUS_COMPLETED
            )

            with patch(
                "romcollections.tasks.generate_collection_cover"
            ) as mock_task:
                mock_task.configure.return_value.defer.return_value = 22

                # game2 just finished; no more pending → cover should be queued
                _trigger_cover_for_collections_if_done(game2)

            assert CoverJob.objects.filter(collection=collection).exists()
        finally:
            Path(img1).unlink(missing_ok=True)
            Path(img2).unlink(missing_ok=True)

    @pytest.mark.django_db
    def test_no_cover_if_no_images_after_all_jobs_done(self):
        """No cover is queued when all jobs complete but no images were downloaded."""
        from library.models import MetadataJob
        from library.tasks import _trigger_cover_for_collections_if_done
        from romcollections.models import CoverJob

        system = _make_system("trig-sys2")
        game = _make_game_without_image("DK", system)
        collection = _make_collection("trig-col2", [("DK", "trig-sys2")])

        # No pending jobs for this game → looks like all done
        _trigger_cover_for_collections_if_done(game)

        assert not CoverJob.objects.filter(collection=collection).exists()

    @pytest.mark.django_db
    def test_game_not_in_any_collection_is_a_noop(self):
        """When game is not in any collection, trigger is a no-op."""
        from library.tasks import _trigger_cover_for_collections_if_done

        system = _make_system("trig-sys3")
        game = _make_game_without_image("Lonely Game", system)

        # Should not raise, should not create anything
        _trigger_cover_for_collections_if_done(game)

    @pytest.mark.django_db
    def test_exception_does_not_propagate(self):
        """Exceptions inside _trigger_cover_for_collections_if_done are swallowed."""
        from library.tasks import _trigger_cover_for_collections_if_done

        system = _make_system("trig-sys4")
        game = _make_game_without_image("Crash Game", system)

        with patch(
            "romcollections.models.CollectionEntry.objects"
        ) as mock_objects:
            mock_objects.filter.side_effect = RuntimeError("db error")

            # Should not raise
            _trigger_cover_for_collections_if_done(game)
