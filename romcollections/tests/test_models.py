"""Tests for romcollections models."""

import pytest
from django.core.exceptions import ValidationError
from django.utils import timezone

from romcollections.models import Collection, CollectionEntry, ExportJob


@pytest.fixture
def collection(db):
    """Create a test collection."""
    return Collection.objects.create(
        slug="best-platformers",
        name="Best Platformers",
        description="My favorite platforming games",
        creator="Test User",
        tags=["platformer", "favorites"],
    )


class TestCollection:
    def test_create_collection(self, db):
        """Test creating a collection."""
        collection = Collection.objects.create(
            slug="test-collection",
            name="Test Collection",
            description="A test collection",
        )
        assert collection.pk is not None
        assert collection.slug == "test-collection"
        assert collection.name == "Test Collection"
        assert collection.is_public is True
        assert collection.tags == []

    def test_collection_str(self, collection):
        """Test collection string representation."""
        assert str(collection) == "Best Platformers"

    def test_collection_entry_count(self, collection, system):
        """Test entry_count property."""
        assert collection.entry_count == 0

        CollectionEntry.objects.create(
            collection=collection,
            game_name="Super Mario World",
            system_slug="snes",
            position=0,
        )
        assert collection.entry_count == 1

    def test_collection_matched_count(self, collection, game):
        """Test matched_count property."""
        CollectionEntry.objects.create(
            collection=collection,
            game_name="Super Mario World",
            system_slug="snes",
            position=0,
        )
        CollectionEntry.objects.create(
            collection=collection,
            game_name="Nonexistent Game",
            system_slug="snes",
            position=1,
        )
        assert collection.matched_count == 1

    def test_collection_matched_count_excludes_games_without_romsets(
        self, collection, system
    ):
        """Test that matched_count only counts games with romsets."""
        from library.models import Game

        # Create a game WITHOUT romsets
        Game.objects.create(name="Zelda", system=system)

        CollectionEntry.objects.create(
            collection=collection,
            game_name="Zelda",
            system_slug="snes",
            position=0,
        )
        # Game exists but has no romsets, so matched_count should be 0
        assert collection.matched_count == 0

    def test_get_latest_export(self, collection):
        """Test get_latest_export returns most recent completed export."""
        # Create multiple export jobs
        old_job = ExportJob.objects.create(
            collection=collection,
            task_id="old-task",
            status=ExportJob.STATUS_COMPLETED,
            completed_at=timezone.now() - timezone.timedelta(hours=2),
        )
        new_job = ExportJob.objects.create(
            collection=collection,
            task_id="new-task",
            status=ExportJob.STATUS_COMPLETED,
            completed_at=timezone.now() - timezone.timedelta(hours=1),
        )
        pending_job = ExportJob.objects.create(
            collection=collection,
            task_id="pending-task",
            status=ExportJob.STATUS_PENDING,
        )

        # Should return the most recent completed job
        latest = collection.get_latest_export()
        assert latest == new_job

    def test_get_latest_export_none_when_no_completed(self, collection):
        """Test get_latest_export returns None when no completed exports."""
        # Only pending/failed jobs
        ExportJob.objects.create(
            collection=collection,
            task_id="pending-task",
            status=ExportJob.STATUS_PENDING,
        )
        ExportJob.objects.create(
            collection=collection,
            task_id="failed-task",
            status=ExportJob.STATUS_FAILED,
        )

        assert collection.get_latest_export() is None

    def test_get_latest_export_none_when_empty(self, collection):
        """Test get_latest_export returns None when no export jobs."""
        assert collection.get_latest_export() is None


class TestCollectionEntry:
    def test_create_entry(self, collection):
        """Test creating a collection entry."""
        entry = CollectionEntry.objects.create(
            collection=collection,
            game_name="Super Mario World",
            system_slug="snes",
            position=0,
            notes="A classic!",
        )
        assert entry.pk is not None
        assert entry.game_name == "Super Mario World"
        assert entry.system_slug == "snes"

    def test_entry_str(self, collection):
        """Test entry string representation."""
        entry = CollectionEntry.objects.create(
            collection=collection,
            game_name="Super Mario World",
            system_slug="snes",
            position=0,
        )
        assert str(entry) == "Super Mario World (snes)"

    def test_entry_ordering(self, collection):
        """Test entries are ordered by position."""
        entry2 = CollectionEntry.objects.create(
            collection=collection,
            game_name="Game 2",
            system_slug="snes",
            position=1,
        )
        entry1 = CollectionEntry.objects.create(
            collection=collection,
            game_name="Game 1",
            system_slug="snes",
            position=0,
        )
        entries = list(collection.entries.all())
        assert entries[0] == entry1
        assert entries[1] == entry2

    def test_unique_together(self, collection):
        """Test unique constraint on collection + game_name + system_slug."""
        CollectionEntry.objects.create(
            collection=collection,
            game_name="Super Mario World",
            system_slug="snes",
            position=0,
        )
        with pytest.raises(Exception):
            CollectionEntry.objects.create(
                collection=collection,
                game_name="Super Mario World",
                system_slug="snes",
                position=1,
            )

    def test_get_matched_game_found(self, collection, game):
        """Test get_matched_game returns game when found."""
        entry = CollectionEntry.objects.create(
            collection=collection,
            game_name="Super Mario World",
            system_slug="snes",
            position=0,
        )
        matched = entry.get_matched_game()
        assert matched == game

    def test_get_matched_game_case_insensitive(self, collection, game):
        """Test get_matched_game is case-insensitive."""
        entry = CollectionEntry.objects.create(
            collection=collection,
            game_name="super mario world",
            system_slug="snes",
            position=0,
        )
        matched = entry.get_matched_game()
        assert matched == game

    def test_get_matched_game_not_found(self, collection, system):
        """Test get_matched_game returns None when not found."""
        entry = CollectionEntry.objects.create(
            collection=collection,
            game_name="Nonexistent Game",
            system_slug="snes",
            position=0,
        )
        assert entry.get_matched_game() is None

    def test_get_matched_game_wrong_system(self, collection, game):
        """Test get_matched_game returns None for wrong system."""
        entry = CollectionEntry.objects.create(
            collection=collection,
            game_name="Super Mario World",
            system_slug="nes",
            position=0,
        )
        assert entry.get_matched_game() is None

    def test_is_matched_property(self, collection, game):
        """Test is_matched property."""
        entry_matched = CollectionEntry.objects.create(
            collection=collection,
            game_name="Super Mario World",
            system_slug="snes",
            position=0,
        )
        entry_not_matched = CollectionEntry.objects.create(
            collection=collection,
            game_name="Unknown Game",
            system_slug="snes",
            position=1,
        )
        assert entry_matched.is_matched is True
        assert entry_not_matched.is_matched is False


class TestCharacterLimitValidation:
    """Test character limit validation on description and notes fields."""

    def test_collection_description_max_length_valid(self, db):
        """Test collection with exactly 1000 char description is valid."""
        collection = Collection(
            slug="test",
            name="Test",
            description="x" * 1000,
            creator="local",
            tags=[],
        )
        # Should not raise - exclude 'tags' since we're only testing description
        collection.full_clean(exclude=["tags"])

    def test_collection_description_max_length_invalid(self, db):
        """Test collection with >1000 char description raises ValidationError."""
        collection = Collection(
            slug="test",
            name="Test",
            description="x" * 1001,
            creator="local",
            tags=[],
        )
        with pytest.raises(ValidationError) as exc_info:
            collection.full_clean(exclude=["tags"])
        assert "description" in exc_info.value.message_dict

    def test_entry_notes_max_length_valid(self, collection):
        """Test entry with exactly 1000 char notes is valid."""
        entry = CollectionEntry(
            collection=collection,
            game_name="Test Game",
            system_slug="snes",
            position=0,
            notes="x" * 1000,
        )
        # Should not raise
        entry.full_clean()

    def test_entry_notes_max_length_invalid(self, collection):
        """Test entry with >1000 char notes raises ValidationError."""
        entry = CollectionEntry(
            collection=collection,
            game_name="Test Game",
            system_slug="snes",
            position=0,
            notes="x" * 1001,
        )
        with pytest.raises(ValidationError) as exc_info:
            entry.full_clean()
        assert "notes" in exc_info.value.message_dict
