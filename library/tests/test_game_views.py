"""Tests for game-related views (delete, rename)."""

from pathlib import Path

import pytest
from django.urls import reverse

from library.models import Game, GameImage, ROMSet, System
from romcollections.models import Collection, CollectionEntry


@pytest.fixture
def system(db):
    """Create a test system."""
    return System.objects.create(
        name="Test System",
        slug="test-system",
        extensions=[".rom"],
        folder_names=["test"],
    )


@pytest.fixture
def game(db, system):
    """Create a test game."""
    return Game.objects.create(
        name="Test Game",
        system=system,
        name_source=Game.SOURCE_FILENAME,
    )


@pytest.fixture
def game_with_image(db, game, tmp_path):
    """Create a game with an associated image file on disk."""
    # Create a temp image file
    image_file = tmp_path / "test_image.png"
    image_file.write_bytes(b"fake image data")

    GameImage.objects.create(
        game=game,
        file_path=str(image_file),
        file_name="test_image.png",
        file_size=15,
        image_type="cover",
    )
    return game


class TestDeleteGameView:
    def test_delete_game_requires_post(self, client, game):
        """Test that delete_game requires POST method."""
        response = client.get(reverse("library:delete_game", args=[game.pk]))
        assert response.status_code == 405  # Method Not Allowed

    def test_delete_game_success(self, client, game):
        """Test successful game deletion."""
        response = client.post(reverse("library:delete_game", args=[game.pk]))
        assert response.status_code == 200
        assert response["HX-Redirect"] == reverse(
            "library:game_list", args=[game.system.slug]
        )
        assert not Game.objects.filter(pk=game.pk).exists()

    def test_delete_game_removes_image_files(self, client, game_with_image, tmp_path):
        """Test that deleting a game also deletes its image files from disk."""
        game = game_with_image
        image = game.images.first()
        image_path = Path(image.file_path)

        # Verify file exists before deletion
        assert image_path.exists()

        response = client.post(reverse("library:delete_game", args=[game.pk]))
        assert response.status_code == 200

        # Verify image file was deleted
        assert not image_path.exists()

    def test_delete_game_not_found(self, client, db):
        """Test deleting a non-existent game returns 404."""
        response = client.post(reverse("library:delete_game", args=[99999]))
        assert response.status_code == 404

    def test_delete_game_cascades_to_romsets(self, client, game):
        """Test that deleting a game also deletes its ROMSets."""
        rom_set = ROMSet.objects.create(game=game, region="US")
        rom_set_pk = rom_set.pk

        response = client.post(reverse("library:delete_game", args=[game.pk]))
        assert response.status_code == 200
        assert not ROMSet.objects.filter(pk=rom_set_pk).exists()


class TestRenameGameView:
    def test_rename_game_requires_post(self, client, game):
        """Test that rename_game requires POST method."""
        response = client.get(reverse("library:rename_game", args=[game.pk]))
        assert response.status_code == 405  # Method Not Allowed

    def test_rename_game_success(self, client, game):
        """Test successful game rename."""
        new_name = "Renamed Game"
        response = client.post(
            reverse("library:rename_game", args=[game.pk]),
            {"name": new_name},
            HTTP_HX_REQUEST="true",
        )
        assert response.status_code == 200

        game.refresh_from_db()
        assert game.name == new_name
        assert game.name_source == Game.SOURCE_MANUAL

    def test_rename_game_empty_name(self, client, game):
        """Test that renaming to empty name fails."""
        response = client.post(
            reverse("library:rename_game", args=[game.pk]),
            {"name": ""},
            HTTP_HX_REQUEST="true",
        )
        assert response.status_code == 400
        assert b"Name cannot be empty" in response.content

    def test_rename_game_whitespace_only(self, client, game):
        """Test that renaming to whitespace-only name fails."""
        response = client.post(
            reverse("library:rename_game", args=[game.pk]),
            {"name": "   "},
            HTTP_HX_REQUEST="true",
        )
        assert response.status_code == 400

    def test_rename_game_duplicate_name(self, client, game, system):
        """Test that renaming to an existing name (same system) fails."""
        # Create another game with the target name
        Game.objects.create(
            name="Existing Game",
            system=system,
            name_source=Game.SOURCE_FILENAME,
        )

        response = client.post(
            reverse("library:rename_game", args=[game.pk]),
            {"name": "Existing Game"},
            HTTP_HX_REQUEST="true",
        )
        assert response.status_code == 400
        assert b"A game with this name already exists" in response.content

    def test_rename_game_same_name_different_system(self, client, game, db):
        """Test that renaming to a name on different system succeeds."""
        # Create another system and game
        other_system = System.objects.create(
            name="Other System",
            slug="other-system",
            extensions=[".bin"],
            folder_names=["other"],
        )
        Game.objects.create(
            name="Target Name",
            system=other_system,
            name_source=Game.SOURCE_FILENAME,
        )

        # Should succeed because it's on a different system
        response = client.post(
            reverse("library:rename_game", args=[game.pk]),
            {"name": "Target Name"},
            HTTP_HX_REQUEST="true",
        )
        assert response.status_code == 200

        game.refresh_from_db()
        assert game.name == "Target Name"

    def test_rename_game_updates_collection_entries(self, client, game, db):
        """Test that renaming a game updates CollectionEntry references."""
        # Create a collection with an entry for this game
        collection = Collection.objects.create(
            slug="test-collection",
            name="Test Collection",
        )
        entry = CollectionEntry.objects.create(
            collection=collection,
            game_name=game.name,
            system_slug=game.system.slug,
            position=0,
        )

        new_name = "Renamed Game"
        response = client.post(
            reverse("library:rename_game", args=[game.pk]),
            {"name": new_name},
            HTTP_HX_REQUEST="true",
        )
        assert response.status_code == 200

        entry.refresh_from_db()
        assert entry.game_name == new_name

    def test_rename_game_not_found(self, client, db):
        """Test renaming a non-existent game returns 404."""
        response = client.post(
            reverse("library:rename_game", args=[99999]),
            {"name": "New Name"},
        )
        assert response.status_code == 404

    def test_rename_game_non_htmx_redirect(self, client, game):
        """Test that non-HTMX requests redirect to game detail."""
        new_name = "Renamed Game"
        response = client.post(
            reverse("library:rename_game", args=[game.pk]),
            {"name": new_name},
        )
        assert response.status_code == 302
        assert response.url == reverse("library:game_detail", args=[game.pk])

    def test_rename_game_same_name_succeeds(self, client, game):
        """Test that renaming to the same name succeeds (no-op)."""
        original_name = game.name
        response = client.post(
            reverse("library:rename_game", args=[game.pk]),
            {"name": original_name},
            HTTP_HX_REQUEST="true",
        )
        assert response.status_code == 200

        game.refresh_from_db()
        assert game.name == original_name


class TestEditGameView:
    """Tests for edit_game view (system change functionality)."""

    def test_edit_game_get_includes_systems(self, client, game, system, db):
        """Test that GET request includes all systems in context."""
        # Create additional systems
        System.objects.create(
            name="Another System",
            slug="another-system",
            extensions=[".bin"],
            folder_names=["another"],
        )
        response = client.get(
            reverse("library:edit_game", args=[game.pk]),
            HTTP_HX_REQUEST="true",
        )
        assert response.status_code == 200
        # Both systems should be in the dropdown
        assert b"Test System" in response.content
        assert b"Another System" in response.content

    def test_edit_game_change_system_success(self, client, game, db):
        """Test successfully changing a game's system."""
        new_system = System.objects.create(
            name="New System",
            slug="new-system",
            extensions=[".bin"],
            folder_names=["new"],
        )
        response = client.post(
            reverse("library:edit_game", args=[game.pk]),
            {"name": game.name, "system": new_system.slug},
            HTTP_HX_REQUEST="true",
        )
        assert response.status_code == 200

        game.refresh_from_db()
        assert game.system == new_system

    def test_edit_game_change_system_invalid(self, client, game):
        """Test that changing to an invalid system fails."""
        response = client.post(
            reverse("library:edit_game", args=[game.pk]),
            {"name": game.name, "system": "nonexistent-system"},
            HTTP_HX_REQUEST="true",
        )
        assert response.status_code == 400
        assert b"Invalid system selected" in response.content

    def test_edit_game_change_system_duplicate_name(self, client, game, db):
        """Test that changing to a system where game name already exists fails."""
        new_system = System.objects.create(
            name="New System",
            slug="new-system",
            extensions=[".bin"],
            folder_names=["new"],
        )
        # Create a game with the same name in the target system
        Game.objects.create(
            name=game.name,
            system=new_system,
            name_source=Game.SOURCE_FILENAME,
        )

        response = client.post(
            reverse("library:edit_game", args=[game.pk]),
            {"name": game.name, "system": new_system.slug},
            HTTP_HX_REQUEST="true",
        )
        assert response.status_code == 400
        assert b"A game with this name already exists in the selected system" in response.content

    def test_edit_game_change_system_updates_collection_entries(self, client, game, db):
        """Test that changing system updates CollectionEntry references."""
        new_system = System.objects.create(
            name="New System",
            slug="new-system",
            extensions=[".bin"],
            folder_names=["new"],
        )
        # Create a collection with an entry for this game
        collection = Collection.objects.create(
            slug="test-collection",
            name="Test Collection",
        )
        entry = CollectionEntry.objects.create(
            collection=collection,
            game_name=game.name,
            system_slug=game.system.slug,
            position=0,
        )

        response = client.post(
            reverse("library:edit_game", args=[game.pk]),
            {"name": game.name, "system": new_system.slug},
            HTTP_HX_REQUEST="true",
        )
        assert response.status_code == 200

        entry.refresh_from_db()
        assert entry.system_slug == new_system.slug

    def test_edit_game_change_name_and_system(self, client, game, db):
        """Test changing both name and system simultaneously."""
        new_system = System.objects.create(
            name="New System",
            slug="new-system",
            extensions=[".bin"],
            folder_names=["new"],
        )
        new_name = "Renamed Game"

        response = client.post(
            reverse("library:edit_game", args=[game.pk]),
            {"name": new_name, "system": new_system.slug},
            HTTP_HX_REQUEST="true",
        )
        assert response.status_code == 200

        game.refresh_from_db()
        assert game.name == new_name
        assert game.system == new_system

    def test_edit_game_same_system_succeeds(self, client, game, system):
        """Test that keeping the same system works."""
        new_name = "Updated Name"
        response = client.post(
            reverse("library:edit_game", args=[game.pk]),
            {"name": new_name, "system": system.slug},
            HTTP_HX_REQUEST="true",
        )
        assert response.status_code == 200

        game.refresh_from_db()
        assert game.name == new_name
        assert game.system == system


class TestGameMergeSearchView:
    """Tests for game_search_for_merge view."""

    def test_search_returns_matching_games(self, client, game, system):
        """Test that search returns games matching query."""
        # Create additional games
        other_game = Game.objects.create(
            name="Another Test Game",
            system=system,
            name_source=Game.SOURCE_FILENAME,
        )
        response = client.get(
            reverse("library:game_merge_search", args=[game.pk]),
            {"q": "Another"},
            HTTP_HX_REQUEST="true",
        )
        assert response.status_code == 200
        assert b"Another Test Game" in response.content

    def test_search_excludes_current_game(self, client, game, system):
        """Test that search excludes the current game from results."""
        response = client.get(
            reverse("library:game_merge_search", args=[game.pk]),
            {"q": "Test Game"},
            HTTP_HX_REQUEST="true",
        )
        assert response.status_code == 200
        # Current game should not appear in results
        assert b"Test Game" not in response.content or b"No matching games found" in response.content

    def test_search_only_same_system(self, client, game, db):
        """Test that search only returns games from the same system."""
        other_system = System.objects.create(
            name="Other System",
            slug="other-system",
            extensions=[".bin"],
            folder_names=["other"],
        )
        Game.objects.create(
            name="Matching Game",
            system=other_system,
            name_source=Game.SOURCE_FILENAME,
        )
        response = client.get(
            reverse("library:game_merge_search", args=[game.pk]),
            {"q": "Matching"},
            HTTP_HX_REQUEST="true",
        )
        assert response.status_code == 200
        # Game from different system should not appear
        assert b"Matching Game" not in response.content

    def test_search_minimum_query_length(self, client, game):
        """Test that search requires minimum 2 characters."""
        response = client.get(
            reverse("library:game_merge_search", args=[game.pk]),
            {"q": "A"},
            HTTP_HX_REQUEST="true",
        )
        assert response.status_code == 200
        # Should return empty results
        assert b"No matching games found" in response.content

    def test_search_empty_query(self, client, game):
        """Test that empty query returns empty results."""
        response = client.get(
            reverse("library:game_merge_search", args=[game.pk]),
            {"q": ""},
            HTTP_HX_REQUEST="true",
        )
        assert response.status_code == 200


class TestMergeGameView:
    """Tests for merge_game view."""

    def test_merge_requires_post(self, client, game):
        """Test that merge requires POST method."""
        response = client.get(reverse("library:merge_game", args=[game.pk]))
        assert response.status_code == 405

    def test_merge_success(self, client, game, system):
        """Test successful game merge."""
        target_game = Game.objects.create(
            name="Target Game",
            system=system,
            name_source=Game.SOURCE_FILENAME,
        )
        # Create a ROM set on the source game
        source_romset = ROMSet.objects.create(game=game, region="US")

        response = client.post(
            reverse("library:merge_game", args=[game.pk]),
            {"target_game_id": target_game.pk},
            HTTP_HX_REQUEST="true",
        )
        assert response.status_code == 200
        assert response["HX-Redirect"] == reverse(
            "library:game_detail", args=[target_game.pk]
        )

        # Verify source game was deleted
        assert not Game.objects.filter(pk=game.pk).exists()
        # Verify ROM set was moved to target
        source_romset.refresh_from_db()
        assert source_romset.game == target_game

    def test_merge_requires_target_game_id(self, client, game):
        """Test that merge requires target_game_id."""
        response = client.post(
            reverse("library:merge_game", args=[game.pk]),
            {},
            HTTP_HX_REQUEST="true",
        )
        assert response.status_code == 400
        assert b"Target game is required" in response.content

    def test_merge_target_not_found(self, client, game):
        """Test that merge fails if target game doesn't exist."""
        response = client.post(
            reverse("library:merge_game", args=[game.pk]),
            {"target_game_id": 99999},
            HTTP_HX_REQUEST="true",
        )
        assert response.status_code == 404

    def test_merge_different_systems_fails(self, client, game, db):
        """Test that merge fails if games are from different systems."""
        other_system = System.objects.create(
            name="Other System",
            slug="other-system",
            extensions=[".bin"],
            folder_names=["other"],
        )
        target_game = Game.objects.create(
            name="Target Game",
            system=other_system,
            name_source=Game.SOURCE_FILENAME,
        )
        response = client.post(
            reverse("library:merge_game", args=[game.pk]),
            {"target_game_id": target_game.pk},
            HTTP_HX_REQUEST="true",
        )
        assert response.status_code == 400
        assert b"Cannot merge games from different systems" in response.content

    def test_merge_into_itself_fails(self, client, game):
        """Test that merge fails if trying to merge game into itself."""
        response = client.post(
            reverse("library:merge_game", args=[game.pk]),
            {"target_game_id": game.pk},
            HTTP_HX_REQUEST="true",
        )
        assert response.status_code == 400
        assert b"Cannot merge a game into itself" in response.content

    def test_merge_source_not_found(self, client, db):
        """Test that merge fails if source game doesn't exist."""
        response = client.post(
            reverse("library:merge_game", args=[99999]),
            {"target_game_id": 1},
            HTTP_HX_REQUEST="true",
        )
        assert response.status_code == 404
