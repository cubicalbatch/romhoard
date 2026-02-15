"""Tests for romcollections serializers."""

import json
import zipfile

import pytest

from romcollections.models import Collection, CollectionEntry
from romcollections.serializers import (
    EXPORT_VERSION,
    ImportError,
    ValidationResult,
    export_collection,
    import_collection,
    import_collection_with_images,
    validate_collection_zip,
    validate_import_data,
)


@pytest.fixture
def collection_with_entries(db):
    """Create a collection with some entries."""
    collection = Collection.objects.create(
        slug="best-platformers",
        name="Best Platformers",
        description="My favorite platforming games",
        creator="Test User",
        tags=["platformer", "favorites"],
    )
    CollectionEntry.objects.create(
        collection=collection,
        game_name="Super Mario World",
        system_slug="snes",
        position=0,
        notes="A classic!",
    )
    CollectionEntry.objects.create(
        collection=collection,
        game_name="Sonic 2",
        system_slug="genesis",
        position=1,
        notes="",
    )
    return collection


class TestExportCollection:
    def test_export_structure(self, collection_with_entries):
        """Test export produces correct structure."""
        data = export_collection(collection_with_entries)

        assert "romhoard_collection" in data
        assert data["romhoard_collection"]["version"] == EXPORT_VERSION
        assert "exported_at" in data["romhoard_collection"]

        assert "collection" in data
        assert data["collection"]["slug"] == "best-platformers"
        assert data["collection"]["name"] == "Best Platformers"
        assert data["collection"]["description"] == "My favorite platforming games"
        assert data["collection"]["creator"] == "Test User"
        assert data["collection"]["tags"] == ["platformer", "favorites"]
        assert data["collection"]["is_community"] is False

        assert "entries" in data
        assert len(data["entries"]) == 2

    def test_export_includes_is_community(self, db):
        """Test export includes is_community field."""
        collection = Collection.objects.create(
            slug="community-test",
            name="Community Test",
            is_community=True,
        )
        data = export_collection(collection)
        assert data["collection"]["is_community"] is True

    def test_export_entries_order(self, collection_with_entries):
        """Test entries are exported in order."""
        data = export_collection(collection_with_entries)

        assert data["entries"][0]["game_name"] == "Super Mario World"
        assert data["entries"][0]["position"] == 0
        assert data["entries"][1]["game_name"] == "Sonic 2"
        assert data["entries"][1]["position"] == 1

    def test_export_entry_fields(self, collection_with_entries):
        """Test entry fields are exported correctly."""
        data = export_collection(collection_with_entries)

        entry = data["entries"][0]
        assert entry["game_name"] == "Super Mario World"
        assert entry["system_slug"] == "snes"
        assert entry["position"] == 0
        assert entry["notes"] == "A classic!"


class TestValidateImportData:
    def test_valid_data(self, collection_with_entries):
        """Test validation passes for valid data."""
        data = export_collection(collection_with_entries)
        validate_import_data(data)

    def test_missing_header(self):
        """Test validation fails without header."""
        with pytest.raises(ImportError, match="missing 'romhoard_collection' header"):
            validate_import_data({"collection": {}, "entries": []})

    def test_invalid_header(self):
        """Test validation fails with invalid header."""
        with pytest.raises(ImportError, match="invalid header"):
            validate_import_data(
                {"romhoard_collection": "not a dict", "collection": {}, "entries": []}
            )

    def test_missing_collection(self):
        """Test validation fails without collection data."""
        with pytest.raises(ImportError, match="missing 'collection' data"):
            validate_import_data(
                {"romhoard_collection": {"version": "1.0"}, "entries": []}
            )

    def test_missing_required_field(self):
        """Test validation fails without required fields."""
        with pytest.raises(ImportError, match="missing required field 'slug'"):
            validate_import_data(
                {
                    "romhoard_collection": {"version": "1.0"},
                    "collection": {"name": "Test"},
                    "entries": [],
                }
            )

    def test_missing_entries(self):
        """Test validation fails without entries."""
        with pytest.raises(ImportError, match="missing 'entries' array"):
            validate_import_data(
                {
                    "romhoard_collection": {"version": "1.0"},
                    "collection": {"slug": "test", "name": "Test"},
                }
            )

    def test_invalid_entry(self):
        """Test validation fails with invalid entry."""
        with pytest.raises(ImportError, match="entry 0 missing 'game_name'"):
            validate_import_data(
                {
                    "romhoard_collection": {"version": "1.0"},
                    "collection": {"slug": "test", "name": "Test"},
                    "entries": [{"system_slug": "snes"}],
                }
            )


class TestImportCollection:
    def test_import_new_collection(self, db, collection_with_entries):
        """Test importing a new collection."""
        data = export_collection(collection_with_entries)
        collection_with_entries.delete()

        result = import_collection(data)
        collection = result["collection"]

        assert collection.slug == "best-platformers"
        assert collection.name == "Best Platformers"
        assert result["entries_imported"] == 2
        assert collection.entries.count() == 2

    def test_import_overwrites_existing(self, db, collection_with_entries):
        """Test import with overwrite=True replaces existing collection."""
        data = export_collection(collection_with_entries)
        original_pk = collection_with_entries.pk

        data["entries"] = [
            {"game_name": "New Game", "system_slug": "nes", "position": 0, "notes": ""}
        ]

        result = import_collection(data, overwrite=True)
        collection = result["collection"]

        assert collection.pk == original_pk
        assert result["entries_imported"] == 1
        assert collection.entries.count() == 1
        assert collection.entries.first().game_name == "New Game"

    def test_import_fails_without_overwrite(self, db, collection_with_entries):
        """Test import fails when collection exists and overwrite=False."""
        data = export_collection(collection_with_entries)

        with pytest.raises(ImportError, match="already exists"):
            import_collection(data, overwrite=False)

    def test_import_preserves_optional_fields(self, db):
        """Test import handles optional fields correctly."""
        data = {
            "romhoard_collection": {"version": "1.0"},
            "collection": {"slug": "minimal", "name": "Minimal"},
            "entries": [{"game_name": "Test", "system_slug": "nes"}],
        }

        result = import_collection(data)
        collection = result["collection"]

        assert collection.description == ""
        assert collection.creator == "local"  # Default for imports without creator
        assert collection.tags == []
        assert result["entries_imported"] == 1

    def test_import_defaults_to_community(self, db):
        """Test import defaults is_community to True."""
        data = {
            "romhoard_collection": {"version": "1.0"},
            "collection": {"slug": "imported", "name": "Imported"},
            "entries": [],
        }

        result = import_collection(data)
        assert result["collection"].is_community is True

    def test_import_respects_is_personal_flag(self, db):
        """Test import with is_personal: true sets is_community=False."""
        data = {
            "romhoard_collection": {"version": "1.0"},
            "collection": {"slug": "personal", "name": "Personal", "is_personal": True},
            "entries": [],
        }

        result = import_collection(data)
        assert result["collection"].is_community is False

    def test_import_respects_is_community_false(self, db):
        """Test import with is_community: false preserves it."""
        data = {
            "romhoard_collection": {"version": "1.0"},
            "collection": {
                "slug": "exported-personal",
                "name": "Exported Personal",
                "is_community": False,
            },
            "entries": [],
        }

        result = import_collection(data)
        assert result["collection"].is_community is False


class TestImportCollectionOverrides:
    """Tests for creator_override, force_public, and force_community parameters."""

    def test_creator_override_replaces_json_creator(self, db):
        """Test creator_override parameter overrides creator from JSON."""
        data = {
            "romhoard_collection": {"version": "1.0"},
            "collection": {
                "slug": "test-override",
                "name": "Test Override",
                "creator": "json-creator",
            },
            "entries": [],
        }

        result = import_collection(data, creator_override="override-creator")

        assert result["collection"].creator == "override-creator"

    def test_creator_override_with_missing_json_creator(self, db):
        """Test creator_override works when JSON has no creator."""
        data = {
            "romhoard_collection": {"version": "1.0"},
            "collection": {"slug": "no-creator", "name": "No Creator"},
            "entries": [],
        }

        result = import_collection(data, creator_override="hub-user")

        assert result["collection"].creator == "hub-user"

    def test_force_public_overrides_is_public_false(self, db):
        """Test force_public=True overrides is_public=False in JSON."""
        data = {
            "romhoard_collection": {"version": "1.0"},
            "collection": {
                "slug": "private",
                "name": "Private Collection",
                "is_public": False,
            },
            "entries": [],
        }

        result = import_collection(data, force_public=True)

        assert result["collection"].is_public is True

    def test_force_public_false_preserves_json_value(self, db):
        """Test force_public=False preserves is_public from JSON."""
        data = {
            "romhoard_collection": {"version": "1.0"},
            "collection": {
                "slug": "private",
                "name": "Private Collection",
                "is_public": False,
            },
            "entries": [],
        }

        result = import_collection(data, force_public=False)

        assert result["collection"].is_public is False

    def test_force_community_overrides_is_community_false(self, db):
        """Test force_community=True overrides is_community=False in JSON."""
        data = {
            "romhoard_collection": {"version": "1.0"},
            "collection": {
                "slug": "personal",
                "name": "Personal Collection",
                "is_community": False,
            },
            "entries": [],
        }

        result = import_collection(data, force_community=True)

        assert result["collection"].is_community is True

    def test_force_community_overrides_is_personal(self, db):
        """Test force_community=True overrides is_personal=True in JSON."""
        data = {
            "romhoard_collection": {"version": "1.0"},
            "collection": {
                "slug": "personal",
                "name": "Personal Collection",
                "is_personal": True,
            },
            "entries": [],
        }

        result = import_collection(data, force_community=True)

        assert result["collection"].is_community is True

    def test_all_overrides_together(self, db):
        """Test all override parameters work together."""
        data = {
            "romhoard_collection": {"version": "1.0"},
            "collection": {
                "slug": "full-override",
                "name": "Full Override Test",
                "creator": "original-creator",
                "is_public": False,
                "is_community": False,
            },
            "entries": [],
        }

        result = import_collection(
            data,
            creator_override="new-creator",
            force_public=True,
            force_community=True,
        )

        assert result["collection"].creator == "new-creator"
        assert result["collection"].is_public is True
        assert result["collection"].is_community is True


class TestRoundTrip:
    def test_export_import_roundtrip(self, db, collection_with_entries):
        """Test export/import preserves all data."""
        data = export_collection(collection_with_entries)
        collection_with_entries.delete()

        result = import_collection(data)
        new_collection = result["collection"]

        assert new_collection.slug == "best-platformers"
        assert new_collection.name == "Best Platformers"
        assert new_collection.description == "My favorite platforming games"
        assert new_collection.creator == "Test User"
        assert new_collection.tags == ["platformer", "favorites"]

        entries = list(new_collection.entries.all())
        assert len(entries) == 2
        assert entries[0].game_name == "Super Mario World"
        assert entries[0].notes == "A classic!"
        assert entries[1].game_name == "Sonic 2"


@pytest.fixture
def snes_system(db):
    """Get or create SNES system for testing."""
    from library.models import System

    system, _ = System.objects.get_or_create(
        slug="snes",
        defaults={
            "name": "Super Nintendo",
            "extensions": [".sfc", ".smc"],
            "folder_names": ["SNES", "snes", "Super Nintendo"],
        },
    )
    return system


@pytest.fixture
def game_with_screenscraper_id(db, snes_system):
    """Create a game with screenscraper_id."""
    from library.models import Game

    return Game.objects.create(
        name="Super Mario World",
        system=snes_system,
        screenscraper_id=12345,
    )


@pytest.fixture
def game_without_screenscraper_id(db, snes_system):
    """Create a game without screenscraper_id."""
    from library.models import Game

    return Game.objects.create(
        name="Donkey Kong Country",
        system=snes_system,
        screenscraper_id=None,
    )


class TestExportScreenscraperId:
    def test_export_includes_screenscraper_id_when_matched_game_has_one(
        self, db, game_with_screenscraper_id
    ):
        """Test export includes screenscraper_id for entries with matched games."""
        collection = Collection.objects.create(
            slug="test-collection",
            name="Test Collection",
        )
        CollectionEntry.objects.create(
            collection=collection,
            game_name="Super Mario World",
            system_slug="snes",
            position=0,
        )

        data = export_collection(collection)

        assert len(data["entries"]) == 1
        assert data["entries"][0]["screenscraper_id"] == 12345

    def test_export_omits_screenscraper_id_when_game_not_matched(self, db, snes_system):
        """Test export omits screenscraper_id when no matched game exists."""
        collection = Collection.objects.create(
            slug="test-collection",
            name="Test Collection",
        )
        CollectionEntry.objects.create(
            collection=collection,
            game_name="Nonexistent Game",
            system_slug="snes",
            position=0,
        )

        data = export_collection(collection)

        assert "screenscraper_id" not in data["entries"][0]

    def test_export_omits_screenscraper_id_when_game_has_none(
        self, db, game_without_screenscraper_id
    ):
        """Test export omits screenscraper_id when matched game has none."""
        collection = Collection.objects.create(
            slug="test-collection",
            name="Test Collection",
        )
        CollectionEntry.objects.create(
            collection=collection,
            game_name="Donkey Kong Country",
            system_slug="snes",
            position=0,
        )

        data = export_collection(collection)

        assert "screenscraper_id" not in data["entries"][0]


class TestImportScreenscraperId:
    def test_import_saves_screenscraper_id_to_matched_game(
        self, db, game_without_screenscraper_id
    ):
        """Test import saves screenscraper_id to matched game without one."""
        data = {
            "romhoard_collection": {"version": "1.0"},
            "collection": {"slug": "imported", "name": "Imported"},
            "entries": [
                {
                    "game_name": "Donkey Kong Country",
                    "system_slug": "snes",
                    "screenscraper_id": 67890,
                }
            ],
        }

        import_collection(data)

        game_without_screenscraper_id.refresh_from_db()
        assert game_without_screenscraper_id.screenscraper_id == 67890

    def test_import_does_not_overwrite_existing_screenscraper_id(
        self, db, game_with_screenscraper_id
    ):
        """Test import preserves existing screenscraper_id."""
        data = {
            "romhoard_collection": {"version": "1.0"},
            "collection": {"slug": "imported", "name": "Imported"},
            "entries": [
                {
                    "game_name": "Super Mario World",
                    "system_slug": "snes",
                    "screenscraper_id": 99999,
                }
            ],
        }

        import_collection(data)

        game_with_screenscraper_id.refresh_from_db()
        assert game_with_screenscraper_id.screenscraper_id == 12345  # Unchanged

    def test_import_skips_screenscraper_id_when_game_not_matched(self, db, snes_system):
        """Test import creates game when not in library with screenscraper_id."""
        from library.models import Game

        data = {
            "romhoard_collection": {"version": "1.0"},
            "collection": {"slug": "imported", "name": "Imported"},
            "entries": [
                {
                    "game_name": "Nonexistent Game",
                    "system_slug": "snes",
                    "screenscraper_id": 11111,
                }
            ],
        }

        result = import_collection(data)
        assert result["entries_imported"] == 1
        assert result["collection"].entries.count() == 1
        assert result["games_created"] == 1

        # Game should be created with the screenscraper_id from JSON
        game = Game.objects.get(name="Nonexistent Game", system=snes_system)
        assert game.screenscraper_id == 11111
        assert game.name_source == "collection"


class TestImportCreatesGames:
    """Tests for automatic game creation during collection import."""

    def test_import_creates_game_for_unmatched_entry(self, db, snes_system):
        """Test import creates a game when entry doesn't match existing game."""
        from library.models import Game

        data = {
            "romhoard_collection": {"version": "1.0"},
            "collection": {"slug": "test", "name": "Test"},
            "entries": [
                {"game_name": "New Game", "system_slug": "snes"},
            ],
        }

        result = import_collection(data)

        assert result["games_created"] == 1
        game = Game.objects.get(name="New Game", system=snes_system)
        assert game.name_source == "collection"
        assert game.screenscraper_id is None

    def test_import_does_not_create_duplicate_games(self, db, snes_system):
        """Test import doesn't create duplicates for existing games."""
        from library.models import Game

        # Create an existing game
        Game.objects.create(name="Existing Game", system=snes_system)

        data = {
            "romhoard_collection": {"version": "1.0"},
            "collection": {"slug": "test", "name": "Test"},
            "entries": [
                {"game_name": "Existing Game", "system_slug": "snes"},
            ],
        }

        result = import_collection(data)

        assert result["games_created"] == 0
        assert (
            Game.objects.filter(name="Existing Game", system=snes_system).count() == 1
        )

    def test_import_returns_warning_for_invalid_system(self, db, snes_system):
        """Test import returns warning when system_slug doesn't exist."""
        data = {
            "romhoard_collection": {"version": "1.0"},
            "collection": {"slug": "test", "name": "Test"},
            "entries": [
                {"game_name": "Game 1", "system_slug": "invalid_system"},
            ],
        }

        result = import_collection(data)

        assert result["games_created"] == 0
        assert len(result["warnings"]) == 1
        assert "invalid_system" in result["warnings"][0]

    def test_import_returns_multiple_invalid_systems_in_warning(self, db, snes_system):
        """Test import lists all invalid systems in warning."""
        data = {
            "romhoard_collection": {"version": "1.0"},
            "collection": {"slug": "test", "name": "Test"},
            "entries": [
                {"game_name": "Game 1", "system_slug": "invalid1"},
                {"game_name": "Game 2", "system_slug": "invalid2"},
                {"game_name": "Game 3", "system_slug": "invalid1"},  # Duplicate
            ],
        }

        result = import_collection(data)

        assert result["games_created"] == 0
        assert len(result["warnings"]) == 1
        assert "invalid1" in result["warnings"][0]
        assert "invalid2" in result["warnings"][0]

    def test_import_creates_games_for_valid_systems_only(self, db, snes_system):
        """Test import creates games for valid systems, warns about invalid ones."""
        from library.models import Game

        data = {
            "romhoard_collection": {"version": "1.0"},
            "collection": {"slug": "test", "name": "Test"},
            "entries": [
                {"game_name": "Valid Game", "system_slug": "snes"},
                {"game_name": "Invalid Game", "system_slug": "nonexistent"},
            ],
        }

        result = import_collection(data)

        assert result["entries_imported"] == 2
        assert result["games_created"] == 1
        assert len(result["warnings"]) == 1
        assert "nonexistent" in result["warnings"][0]
        assert Game.objects.filter(name="Valid Game").exists()
        assert not Game.objects.filter(name="Invalid Game").exists()

    def test_import_with_screenscraper_id_does_not_queue_metadata(
        self, db, snes_system
    ):
        """Test import doesn't queue metadata when screenscraper_id is provided."""
        data = {
            "romhoard_collection": {"version": "1.0"},
            "collection": {"slug": "test", "name": "Test"},
            "entries": [
                {
                    "game_name": "New Game",
                    "system_slug": "snes",
                    "screenscraper_id": 12345,
                },
            ],
        }

        result = import_collection(data)

        assert result["games_created"] == 1
        # No metadata jobs should be queued since screenscraper_id was provided
        assert result["metadata_jobs_queued"] == 0


class TestImportCollectionWithImages:
    """Tests for ZIP import with images."""

    @pytest.fixture
    def zip_with_collection(self, tmp_path):
        """Create a basic ZIP file with collection.json."""
        zip_path = tmp_path / "test_collection.zip"
        collection_data = {
            "romhoard_collection": {"version": "1.0"},
            "collection": {"slug": "test-import", "name": "Test Import"},
            "entries": [
                {"game_name": "Test Game", "system_slug": "snes", "position": 0}
            ],
        }

        with zipfile.ZipFile(zip_path, "w") as zipf:
            zipf.writestr(
                "collection.json", json.dumps(collection_data, ensure_ascii=False)
            )

        return zip_path

    def test_import_zip_without_collection_json_fails(self, tmp_path, db):
        """Test import fails when collection.json is missing."""
        zip_path = tmp_path / "empty.zip"
        with zipfile.ZipFile(zip_path, "w") as zipf:
            zipf.writestr("dummy.txt", "test")

        with pytest.raises(ImportError, match="collection.json"):
            import_collection_with_images(str(zip_path))

    def test_import_zip_with_invalid_collection_json_fails(self, tmp_path, db):
        """Test import fails when collection.json is not valid JSON."""
        zip_path = tmp_path / "invalid_collection.json.zip"
        with zipfile.ZipFile(zip_path, "w") as zipf:
            zipf.writestr("collection.json", "not-json")

        with pytest.raises(ImportError, match="Invalid collection.json"):
            import_collection_with_images(str(zip_path))

    def test_import_zip_creates_collection(self, db, snes_system, zip_with_collection):
        """Test importing ZIP creates collection."""
        result = import_collection_with_images(str(zip_with_collection))

        assert result["collection"].slug == "test-import"
        assert result["collection"].name == "Test Import"
        assert result["entries_imported"] == 1
        assert result["games_created"] == 1

    def test_import_zip_with_metadata(self, db, snes_system, tmp_path):
        """Test importing ZIP applies metadata from game JSON files."""
        from library.models import Game

        # Create existing game
        game = Game.objects.create(name="Test Game", system=snes_system)

        # Create ZIP with metadata
        zip_path = tmp_path / "with_metadata.zip"
        collection_data = {
            "romhoard_collection": {"version": "1.0"},
            "collection": {"slug": "test-metadata", "name": "Test Metadata"},
            "entries": [
                {"game_name": "Test Game", "system_slug": "snes", "position": 0}
            ],
        }
        game_metadata = {
            "name": "Test Game",
            "system_slug": "snes",
            "description": "A test game description",
            "developer": "Test Developer",
            "publisher": "Test Publisher",
        }

        with zipfile.ZipFile(zip_path, "w") as zipf:
            zipf.writestr(
                "collection.json", json.dumps(collection_data, ensure_ascii=False)
            )
            zipf.writestr(
                "games/Test Game_snes.json",
                json.dumps(game_metadata, ensure_ascii=False),
            )

        import_collection_with_images(str(zip_path))

        game.refresh_from_db()
        assert game.description == "A test game description"
        assert game.developer == "Test Developer"
        assert game.publisher == "Test Publisher"

    def test_import_zip_with_images(self, db, snes_system, tmp_path, settings):
        """Test importing ZIP extracts and creates images."""
        from library.models import Game, GameImage

        # Create existing game
        game = Game.objects.create(name="Test Game", system=snes_system)

        # Set up images directory
        images_dir = tmp_path / "images_output"
        images_dir.mkdir()
        settings.MEDIA_ROOT = str(images_dir)

        # Create a simple 1x1 PNG image
        png_data = (
            b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01"
            b"\x00\x00\x00\x01\x08\x02\x00\x00\x00\x90wS\xde\x00"
            b"\x00\x00\x0cIDATx\x9cc\xf8\x0f\x00\x00\x01\x01\x00"
            b"\x05\x18\xd8N\x00\x00\x00\x00IEND\xaeB`\x82"
        )

        # Create ZIP with image
        zip_path = tmp_path / "with_images.zip"
        collection_data = {
            "romhoard_collection": {"version": "1.0"},
            "collection": {"slug": "test-images", "name": "Test Images"},
            "entries": [
                {"game_name": "Test Game", "system_slug": "snes", "position": 0}
            ],
        }

        with zipfile.ZipFile(zip_path, "w") as zipf:
            zipf.writestr(
                "collection.json", json.dumps(collection_data, ensure_ascii=False)
            )
            zipf.writestr("images/Test Game_snes/cover.png", png_data)

        result = import_collection_with_images(str(zip_path))

        assert result["images_imported"] == 1
        assert GameImage.objects.filter(game=game, image_type="cover").exists()

    def test_import_zip_with_unknown_image_type_imports_as_blank(
        self, db, snes_system, tmp_path, settings
    ):
        """Test importing ZIP 'unknown.png' maps to blank image_type."""
        from library.models import Game, GameImage

        # Create existing game
        game = Game.objects.create(name="Test Game", system=snes_system)

        # Set up images directory
        images_dir = tmp_path / "images_output"
        images_dir.mkdir()
        settings.MEDIA_ROOT = str(images_dir)

        zip_path = tmp_path / "unknown_type.zip"
        collection_data = {
            "romhoard_collection": {"version": "1.0"},
            "collection": {"slug": "test-unknown-type", "name": "Test Unknown Type"},
            "entries": [
                {"game_name": "Test Game", "system_slug": "snes", "position": 0}
            ],
        }
        png_data = b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR"

        with zipfile.ZipFile(zip_path, "w") as zipf:
            zipf.writestr(
                "collection.json", json.dumps(collection_data, ensure_ascii=False)
            )
            zipf.writestr("images/Test Game_snes/unknown.png", png_data)

        result = import_collection_with_images(str(zip_path))

        assert result["images_imported"] == 1
        image = GameImage.objects.get(game=game)
        assert image.image_type == ""

    def test_import_zip_skips_existing_images(
        self, db, snes_system, tmp_path, settings
    ):
        """Test importing ZIP skips images that already exist for the game."""
        from library.models import Game, GameImage

        # Set up images directory
        images_dir = tmp_path / "images_output"
        images_dir.mkdir()
        settings.MEDIA_ROOT = str(images_dir)

        # Create existing game with existing image
        game = Game.objects.create(name="Test Game", system=snes_system)
        existing_image_path = images_dir / "existing_cover.png"
        existing_image_path.write_bytes(b"existing")
        GameImage.objects.create(
            game=game,
            file_path=str(existing_image_path),
            file_name="existing_cover.png",
            file_size=8,
            image_type="cover",
        )

        # Create ZIP with cover image (should be skipped)
        zip_path = tmp_path / "with_duplicate.zip"
        collection_data = {
            "romhoard_collection": {"version": "1.0"},
            "collection": {"slug": "test-skip", "name": "Test Skip"},
            "entries": [
                {"game_name": "Test Game", "system_slug": "snes", "position": 0}
            ],
        }
        png_data = b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR"  # Minimal PNG header

        with zipfile.ZipFile(zip_path, "w") as zipf:
            zipf.writestr(
                "collection.json", json.dumps(collection_data, ensure_ascii=False)
            )
            zipf.writestr("images/Test Game_snes/cover.png", png_data)

        result = import_collection_with_images(str(zip_path))

        # Should not import the cover since it already exists
        assert result["images_imported"] == 0
        assert GameImage.objects.filter(game=game).count() == 1

    def test_import_zip_with_creator_override(self, db, snes_system, tmp_path):
        """Test import_collection_with_images respects creator_override."""
        zip_path = tmp_path / "creator_override.zip"
        collection_data = {
            "romhoard_collection": {"version": "1.0"},
            "collection": {
                "slug": "override-test",
                "name": "Override Test",
                "creator": "json-creator",
            },
            "entries": [],
        }

        with zipfile.ZipFile(zip_path, "w") as zipf:
            zipf.writestr(
                "collection.json", json.dumps(collection_data, ensure_ascii=False)
            )

        result = import_collection_with_images(
            str(zip_path), creator_override="hub-user"
        )

        assert result["collection"].creator == "hub-user"

    def test_import_zip_with_force_public(self, db, snes_system, tmp_path):
        """Test import_collection_with_images respects force_public."""
        zip_path = tmp_path / "force_public.zip"
        collection_data = {
            "romhoard_collection": {"version": "1.0"},
            "collection": {
                "slug": "private-test",
                "name": "Private Test",
                "is_public": False,
            },
            "entries": [],
        }

        with zipfile.ZipFile(zip_path, "w") as zipf:
            zipf.writestr(
                "collection.json", json.dumps(collection_data, ensure_ascii=False)
            )

        result = import_collection_with_images(str(zip_path), force_public=True)

        assert result["collection"].is_public is True

    def test_import_zip_with_force_community(self, db, snes_system, tmp_path):
        """Test import_collection_with_images respects force_community."""
        zip_path = tmp_path / "force_community.zip"
        collection_data = {
            "romhoard_collection": {"version": "1.0"},
            "collection": {
                "slug": "personal-test",
                "name": "Personal Test",
                "is_community": False,
            },
            "entries": [],
        }

        with zipfile.ZipFile(zip_path, "w") as zipf:
            zipf.writestr(
                "collection.json", json.dumps(collection_data, ensure_ascii=False)
            )

        result = import_collection_with_images(str(zip_path), force_community=True)

        assert result["collection"].is_community is True

    def test_import_zip_with_all_overrides(self, db, snes_system, tmp_path):
        """Test import_collection_with_images with all override parameters."""
        zip_path = tmp_path / "all_overrides.zip"
        collection_data = {
            "romhoard_collection": {"version": "1.0"},
            "collection": {
                "slug": "all-override-test",
                "name": "All Override Test",
                "creator": "original",
                "is_public": False,
                "is_community": False,
            },
            "entries": [],
        }

        with zipfile.ZipFile(zip_path, "w") as zipf:
            zipf.writestr(
                "collection.json", json.dumps(collection_data, ensure_ascii=False)
            )

        result = import_collection_with_images(
            str(zip_path),
            creator_override="hub-user",
            force_public=True,
            force_community=True,
        )

        assert result["collection"].creator == "hub-user"
        assert result["collection"].is_public is True
        assert result["collection"].is_community is True


class TestValidateCollectionZip:
    """Tests for the validate_collection_zip function."""

    @pytest.fixture
    def valid_collection_zip(self, tmp_path):
        """Create a valid collection ZIP file."""
        zip_path = tmp_path / "valid_collection.zip"
        collection_data = {
            "romhoard_collection": {"version": "1.0"},
            "collection": {"slug": "test-validation", "name": "Test Validation"},
            "entries": [
                {"game_name": "Test Game", "system_slug": "snes", "position": 0}
            ],
        }

        with zipfile.ZipFile(zip_path, "w") as zipf:
            zipf.writestr(
                "collection.json", json.dumps(collection_data, ensure_ascii=False)
            )

        return zip_path

    def test_validate_valid_zip(self, valid_collection_zip):
        """Test validation passes for a valid ZIP."""
        result = validate_collection_zip(str(valid_collection_zip))

        assert result.is_valid is True
        assert result.has_collection_json is True
        assert result.game_count == 1
        assert len(result.errors) == 0
        assert len(result.info) > 0

    def test_validate_missing_collection_json(self, tmp_path):
        """Test validation fails when collection.json is missing."""
        zip_path = tmp_path / "no_collection.zip"
        with zipfile.ZipFile(zip_path, "w") as zipf:
            zipf.writestr("readme.txt", "This is not a valid collection")

        result = validate_collection_zip(str(zip_path))

        assert result.is_valid is False
        assert result.has_collection_json is False
        assert any("collection.json" in e for e in result.errors)

    def test_validate_invalid_json(self, tmp_path):
        """Test validation fails when collection.json is invalid JSON."""
        zip_path = tmp_path / "invalid_json.zip"
        with zipfile.ZipFile(zip_path, "w") as zipf:
            zipf.writestr("collection.json", "not valid json")

        result = validate_collection_zip(str(zip_path))

        assert result.is_valid is False
        assert any("not valid JSON" in e or "Invalid" in e for e in result.errors)

    def test_validate_corrupt_zip(self, tmp_path):
        """Test validation fails for corrupt ZIP file."""
        zip_path = tmp_path / "corrupt.zip"
        zip_path.write_text("This is not a zip file")

        result = validate_collection_zip(str(zip_path))

        assert result.is_valid is False
        assert any("not a valid ZIP" in e for e in result.errors)

    def test_validate_zip_bomb(self, tmp_path):
        """Test validation fails for zip bomb (excessive uncompressed size)."""
        zip_path = tmp_path / "zip_bomb.zip"
        collection_data = {
            "romhoard_collection": {"version": "1.0"},
            "collection": {"slug": "bomb", "name": "Zip Bomb"},
            "entries": [],
        }

        # Create a ZIP that would expand beyond the limit
        # Use multiple files to simulate high uncompressed size
        with zipfile.ZipFile(zip_path, "w") as zipf:
            zipf.writestr(
                "collection.json", json.dumps(collection_data, ensure_ascii=False)
            )
            # Add files that sum to > max_uncompressed (1000 bytes)
            for i in range(11):  # 11 * 100 bytes = 1100 bytes > 1000 limit
                zipf.writestr(f"huge_file_{i}.bin", b"x" * 100)

        # Set a low max_uncompressed to trigger the zip bomb detection
        result = validate_collection_zip(str(zip_path), max_uncompressed=1000)

        assert result.is_valid is False
        assert any("zip bomb" in e.lower() or "expand" in e.lower() for e in result.errors)

    def test_validate_file_size_limit(self, tmp_path):
        """Test validation fails when file exceeds max size."""
        zip_path = tmp_path / "too_large.zip"
        collection_data = {
            "romhoard_collection": {"version": "1.0"},
            "collection": {"slug": "large", "name": "Large File"},
            "entries": [],
        }

        with zipfile.ZipFile(zip_path, "w") as zipf:
            zipf.writestr(
                "collection.json", json.dumps(collection_data, ensure_ascii=False)
            )

        # Set a very low max_size to trigger the file size check
        result = validate_collection_zip(str(zip_path), max_size=10)

        assert result.is_valid is False
        assert any("too large" in e.lower() or "max" in e.lower() for e in result.errors)

    def test_validate_with_cover_image(self, tmp_path):
        """Test validation detects cover image."""
        zip_path = tmp_path / "with_cover.zip"
        collection_data = {
            "romhoard_collection": {"version": "1.0"},
            "collection": {"slug": "cover-test", "name": "Cover Test"},
            "entries": [],
        }

        with zipfile.ZipFile(zip_path, "w") as zipf:
            zipf.writestr(
                "collection.json", json.dumps(collection_data, ensure_ascii=False)
            )
            zipf.writestr("cover.png", b"fake image data")

        result = validate_collection_zip(str(zip_path))

        assert result.is_valid is True
        assert result.has_cover is True

    def test_validate_with_invalid_cover_extension(self, tmp_path):
        """Test validation warns about unusual cover extension."""
        zip_path = tmp_path / "weird_cover.zip"
        collection_data = {
            "romhoard_collection": {"version": "1.0"},
            "collection": {"slug": "cover-test", "name": "Cover Test"},
            "entries": [],
        }

        with zipfile.ZipFile(zip_path, "w") as zipf:
            zipf.writestr(
                "collection.json", json.dumps(collection_data, ensure_ascii=False)
            )
            zipf.writestr("cover.bmp", b"fake image data")

        result = validate_collection_zip(str(zip_path))

        assert result.is_valid is True
        assert result.has_cover is True
        assert any("unusual extension" in w.lower() for w in result.warnings)

    def test_validate_with_images(self, tmp_path):
        """Test validation counts images correctly."""
        zip_path = tmp_path / "with_images.zip"
        collection_data = {
            "romhoard_collection": {"version": "1.0"},
            "collection": {"slug": "images-test", "name": "Images Test"},
            "entries": [
                {"game_name": "Game 1", "system_slug": "snes", "position": 0}
            ],
        }

        with zipfile.ZipFile(zip_path, "w") as zipf:
            zipf.writestr(
                "collection.json", json.dumps(collection_data, ensure_ascii=False)
            )
            zipf.writestr("images/Game 1_snes/cover.png", b"fake cover")
            zipf.writestr("images/Game 1_snes/screenshot.png", b"fake screenshot")
            zipf.writestr("images/Game 1_snes/wheel.png", b"fake wheel")

        result = validate_collection_zip(str(zip_path))

        assert result.is_valid is True
        assert result.image_count == 3
        assert any("3 image(s)" in i for i in result.info)

    def test_validate_with_invalid_image_extension(self, tmp_path):
        """Test validation warns about invalid image extensions."""
        zip_path = tmp_path / "invalid_image.zip"
        collection_data = {
            "romhoard_collection": {"version": "1.0"},
            "collection": {"slug": "image-test", "name": "Image Test"},
            "entries": [],
        }

        with zipfile.ZipFile(zip_path, "w") as zipf:
            zipf.writestr(
                "collection.json", json.dumps(collection_data, ensure_ascii=False)
            )
            zipf.writestr("images/Game_snes/cover.bmp", b"fake image")

        result = validate_collection_zip(str(zip_path))

        assert result.is_valid is True
        assert result.image_count == 0  # Not counted as valid image
        assert any("non-standard" in w.lower() for w in result.warnings)

    def test_validate_missing_file(self, tmp_path):
        """Test validation fails when file doesn't exist."""
        nonexistent = tmp_path / "does_not_exist.zip"

        result = validate_collection_zip(str(nonexistent))

        assert result.is_valid is False
        assert any("not found" in e.lower() for e in result.errors)

    def test_validate_high_compression_warning(self, tmp_path):
        """Test validation warns about very high compression ratio."""
        zip_path = tmp_path / "high_compression.zip"
        collection_data = {
            "romhoard_collection": {"version": "1.0"},
            "collection": {"slug": "compressed", "name": "Compressed"},
            "entries": [],
        }

        # Create ZIP with a highly compressible large file
        large_content = b"A" * 1000000  # 1MB of same character (very compressible)

        with zipfile.ZipFile(zip_path, "w", compression=zipfile.ZIP_DEFLATED) as zipf:
            zipf.writestr(
                "collection.json", json.dumps(collection_data, ensure_ascii=False)
            )
            zipf.writestr("large_file.txt", large_content)

        # Check the compression ratio warning (1000:1 ratio should trigger warning)
        # Note: This depends on actual compression achieved
        result = validate_collection_zip(str(zip_path))

        # Should still be valid, might have warning
        assert result.is_valid is True

    def test_validate_result_str(self):
        """Test ValidationResult converts to bool correctly."""
        valid_result = ValidationResult(is_valid=True)
        invalid_result = ValidationResult(is_valid=False)

        assert bool(valid_result) is True
        assert bool(invalid_result) is False
