"""Tests for romcollections views."""

import json

import pytest
from django.urls import reverse

from library.models import Game, ROMSet, System
from romcollections.models import Collection, CollectionEntry


@pytest.fixture
def collection(db):
    """Create a test collection."""
    return Collection.objects.create(
        creator="local",
        slug="test-collection",
        name="Test Collection",
        description="A test collection",
    )


@pytest.fixture
def collection_with_entry(db, collection, system):
    """Create a collection with an entry."""
    CollectionEntry.objects.create(
        collection=collection,
        game_name="Super Mario World",
        system_slug="snes",
        position=0,
    )
    return collection


class TestCollectionListView:
    def test_list_shows_favorites_by_default(self, client, db):
        """Test that Favorites collection always exists and is shown."""
        response = client.get(reverse("romcollections:collection_list"))
        assert response.status_code == 200
        # Favorites collection should always exist
        assert b"Favorites" in response.content
        assert response.context["total_personal"] == 1  # Favorites

    def test_list_with_collections(self, client, collection):
        """Test listing collections."""
        response = client.get(reverse("romcollections:collection_list"))
        assert response.status_code == 200
        assert b"Test Collection" in response.content

    def test_list_separates_personal_and_community(self, client, db):
        """Test that personal and community collections are separated."""
        Collection.objects.create(
            creator="local", slug="personal-1", name="Personal One", is_community=False
        )
        Collection.objects.create(
            creator="local", slug="community-1", name="Community One", is_community=True
        )

        response = client.get(reverse("romcollections:collection_list"))
        assert response.status_code == 200
        # Favorites + Personal One = 2 personal collections
        assert response.context["total_personal"] == 2
        assert response.context["total_community"] == 1

    def test_list_pagination(self, client, db):
        """Test pagination for collection lists."""
        # Create 15 personal collections (more than page size of 12)
        for i in range(15):
            Collection.objects.create(
                creator="local",
                slug=f"personal-{i}",
                name=f"Personal {i}",
                is_community=False,
            )

        response = client.get(reverse("romcollections:collection_list"))
        assert response.status_code == 200
        # First page should have 12
        assert len(response.context["personal_page_obj"]) == 12
        assert response.context["personal_page_obj"].has_next()

    def test_community_collections_ordered_by_matched_count(self, client, system):
        """Test that community collections are ordered by matched count descending."""
        # Create games with ROMs in the library
        game1 = Game.objects.create(name="Super Mario World", system=system)
        ROMSet.objects.create(game=game1, region="USA")
        game2 = Game.objects.create(name="Donkey Kong Country", system=system)
        ROMSet.objects.create(game=game2, region="USA")
        game3 = Game.objects.create(name="Chrono Trigger", system=system)
        ROMSet.objects.create(game=game3, region="USA")

        # Create community collections with different numbers of matched games
        # Collection A: 1 matched game (alphabetically first, but fewer matches)
        col_a = Collection.objects.create(
            creator="local",
            slug="aaa-collection",
            name="AAA Collection",
            is_community=True,
        )
        CollectionEntry.objects.create(
            collection=col_a, game_name="Super Mario World", system_slug="snes"
        )

        # Collection B: 3 matched games (should appear first due to most matches)
        col_b = Collection.objects.create(
            creator="local",
            slug="bbb-collection",
            name="BBB Collection",
            is_community=True,
        )
        CollectionEntry.objects.create(
            collection=col_b, game_name="Super Mario World", system_slug="snes"
        )
        CollectionEntry.objects.create(
            collection=col_b, game_name="Donkey Kong Country", system_slug="snes"
        )
        CollectionEntry.objects.create(
            collection=col_b, game_name="Chrono Trigger", system_slug="snes"
        )

        # Collection C: 2 matched games (should be in middle)
        col_c = Collection.objects.create(
            creator="local",
            slug="ccc-collection",
            name="CCC Collection",
            is_community=True,
        )
        CollectionEntry.objects.create(
            collection=col_c, game_name="Super Mario World", system_slug="snes"
        )
        CollectionEntry.objects.create(
            collection=col_c, game_name="Donkey Kong Country", system_slug="snes"
        )

        response = client.get(reverse("romcollections:collection_list"))
        assert response.status_code == 200

        community_collections = list(response.context["community_page_obj"])
        # Should be ordered: BBB (3 matches), CCC (2 matches), AAA (1 match)
        assert len(community_collections) == 3
        assert community_collections[0].slug == "bbb-collection"
        assert community_collections[1].slug == "ccc-collection"
        assert community_collections[2].slug == "aaa-collection"

    def test_personal_collections_remain_alphabetical(self, client, system):
        """Test that personal collections are still ordered alphabetically."""
        # Create games with ROMs
        game1 = Game.objects.create(name="Super Mario World", system=system)
        ROMSet.objects.create(game=game1, region="USA")
        game2 = Game.objects.create(name="Donkey Kong Country", system=system)
        ROMSet.objects.create(game=game2, region="USA")

        # Create personal collections (should be ordered alphabetically, not by matches)
        col_z = Collection.objects.create(
            creator="local",
            slug="zzz-personal",
            name="ZZZ Personal",
            is_community=False,
        )
        # ZZZ has more matches but should appear last (alphabetically)
        CollectionEntry.objects.create(
            collection=col_z, game_name="Super Mario World", system_slug="snes"
        )
        CollectionEntry.objects.create(
            collection=col_z, game_name="Donkey Kong Country", system_slug="snes"
        )

        col_a = Collection.objects.create(
            creator="local",
            slug="aaa-personal",
            name="AAA Personal",
            is_community=False,
        )
        # AAA has fewer matches but should appear first (alphabetically)
        CollectionEntry.objects.create(
            collection=col_a, game_name="Super Mario World", system_slug="snes"
        )

        response = client.get(reverse("romcollections:collection_list"))
        assert response.status_code == 200

        personal_collections = list(response.context["personal_page_obj"])
        # Should be ordered: Favorites (first, special), then AAA, ZZZ (alphabetically)
        assert len(personal_collections) == 3
        assert personal_collections[0].is_favorites is True  # Favorites first
        assert personal_collections[1].slug == "aaa-personal"
        assert personal_collections[2].slug == "zzz-personal"

    def test_community_collections_same_count_sorted_by_name(self, client, system):
        """Test collections with same matched count are sorted alphabetically."""
        game1 = Game.objects.create(name="Super Mario World", system=system)
        ROMSet.objects.create(game=game1, region="USA")

        # Both collections have 1 matched game - should be sorted by name
        col_z = Collection.objects.create(
            creator="local",
            slug="zzz-community",
            name="ZZZ Community",
            is_community=True,
        )
        CollectionEntry.objects.create(
            collection=col_z, game_name="Super Mario World", system_slug="snes"
        )

        col_a = Collection.objects.create(
            creator="local",
            slug="aaa-community",
            name="AAA Community",
            is_community=True,
        )
        CollectionEntry.objects.create(
            collection=col_a, game_name="Super Mario World", system_slug="snes"
        )

        response = client.get(reverse("romcollections:collection_list"))
        assert response.status_code == 200

        community_collections = list(response.context["community_page_obj"])
        # Same matched count, so alphabetical: AAA, ZZZ
        assert len(community_collections) == 2
        assert community_collections[0].slug == "aaa-community"
        assert community_collections[1].slug == "zzz-community"


class TestCollectionSearchView:
    def test_search_by_name(self, client, db):
        """Test searching collections by name."""
        Collection.objects.create(
            creator="local", slug="mario-games", name="Mario Games"
        )
        Collection.objects.create(
            creator="local", slug="sonic-games", name="Sonic Games"
        )

        response = client.get(reverse("romcollections:collection_search") + "?q=mario")
        assert response.status_code == 200
        content = response.content.decode()
        assert "Mario Games" in content
        assert "Sonic Games" not in content

    def test_search_by_description(self, client, db):
        """Test searching collections by description."""
        Collection.objects.create(
            creator="local",
            slug="test-1",
            name="Test 1",
            description="Contains platformers",
        )
        Collection.objects.create(
            creator="local",
            slug="test-2",
            name="Test 2",
            description="Contains racing games",
        )

        response = client.get(
            reverse("romcollections:collection_search") + "?q=platformers"
        )
        assert response.status_code == 200
        content = response.content.decode()
        assert "Test 1" in content
        assert "Test 2" not in content

    def test_search_by_tags(self, client, db):
        """Test searching collections by tags."""
        Collection.objects.create(
            creator="local",
            slug="tagged-1",
            name="Tagged 1",
            tags=["retro", "classics"],
        )
        Collection.objects.create(
            creator="local", slug="tagged-2", name="Tagged 2", tags=["modern"]
        )

        response = client.get(reverse("romcollections:collection_search") + "?q=retro")
        assert response.status_code == 200
        content = response.content.decode()
        assert "Tagged 1" in content
        assert "Tagged 2" not in content

    def test_filter_personal_only(self, client, db):
        """Test filtering to show only personal collections."""
        Collection.objects.create(
            creator="local", slug="personal", name="Personal", is_community=False
        )
        Collection.objects.create(
            creator="local", slug="community", name="Community", is_community=True
        )

        response = client.get(
            reverse("romcollections:collection_search") + "?type=personal"
        )
        assert response.status_code == 200
        assert response.context["show_personal"] is True
        assert response.context["show_community"] is False

    def test_filter_community_only(self, client, db):
        """Test filtering to show only community collections."""
        Collection.objects.create(
            creator="local", slug="personal", name="Personal", is_community=False
        )
        Collection.objects.create(
            creator="local", slug="community", name="Community", is_community=True
        )

        response = client.get(
            reverse("romcollections:collection_search") + "?type=community"
        )
        assert response.status_code == 200
        assert response.context["show_personal"] is False
        assert response.context["show_community"] is True

    def test_filter_all(self, client, db):
        """Test filtering to show all collections."""
        Collection.objects.create(
            creator="local", slug="personal", name="Personal", is_community=False
        )
        Collection.objects.create(
            creator="local", slug="community", name="Community", is_community=True
        )

        response = client.get(reverse("romcollections:collection_search") + "?type=all")
        assert response.status_code == 200
        assert response.context["show_personal"] is True
        assert response.context["show_community"] is True

    def test_search_by_game_name_in_entries(self, client, db):
        """Test searching collections by game names in entries."""
        col1 = Collection.objects.create(
            creator="local", slug="col-1", name="Collection One"
        )
        col2 = Collection.objects.create(
            creator="local", slug="col-2", name="Collection Two"
        )

        # Add a Mario game to col1
        CollectionEntry.objects.create(
            collection=col1,
            game_name="Super Mario World",
            system_slug="snes",
            position=0,
        )
        # Add a different game to col2
        CollectionEntry.objects.create(
            collection=col2,
            game_name="Sonic the Hedgehog",
            system_slug="genesis",
            position=0,
        )

        response = client.get(reverse("romcollections:collection_search") + "?q=Mario")
        assert response.status_code == 200
        content = response.content.decode()
        assert "Collection One" in content
        assert "Collection Two" not in content

    def test_search_by_creator(self, client, db):
        """Test searching collections by creator name."""
        Collection.objects.create(slug="col-1", name="Col One", creator="nintendo-fan")
        Collection.objects.create(slug="col-2", name="Col Two", creator="sega-lover")

        response = client.get(
            reverse("romcollections:collection_search") + "?q=nintendo"
        )
        assert response.status_code == 200
        content = response.content.decode()
        assert "Col One" in content
        assert "Col Two" not in content

    def test_search_by_system_slug(self, client, system):
        """Test searching collections by system slug (e.g., 'snes')."""
        col1 = Collection.objects.create(
            creator="local", slug="snes-col", name="SNES Collection"
        )
        col2 = Collection.objects.create(
            creator="local", slug="other-col", name="Other Collection"
        )

        # Add SNES game to col1
        CollectionEntry.objects.create(
            collection=col1, game_name="Chrono Trigger", system_slug="snes", position=0
        )
        # Add different system game to col2
        CollectionEntry.objects.create(
            collection=col2, game_name="Sonic", system_slug="genesis", position=0
        )

        response = client.get(reverse("romcollections:collection_search") + "?q=snes")
        assert response.status_code == 200
        content = response.content.decode()
        assert "SNES Collection" in content
        assert "Other Collection" not in content

    def test_search_by_system_full_name(self, client, system):
        """Test searching collections by full system name (e.g., 'Super Nintendo')."""
        col1 = Collection.objects.create(
            creator="local", slug="snes-col", name="SNES Collection"
        )
        col2 = Collection.objects.create(
            creator="local", slug="other-col", name="Other Collection"
        )

        # Add SNES game to col1
        CollectionEntry.objects.create(
            collection=col1, game_name="Chrono Trigger", system_slug="snes", position=0
        )
        # Add different system game to col2
        CollectionEntry.objects.create(
            collection=col2, game_name="Sonic", system_slug="genesis", position=0
        )

        # Search by full name - should find collections with SNES games
        response = client.get(
            reverse("romcollections:collection_search") + "?q=Super%20Nintendo"
        )
        assert response.status_code == 200
        content = response.content.decode()
        assert "SNES Collection" in content
        assert "Other Collection" not in content

    def test_search_combined_filters(self, client, db):
        """Test that search combines multiple filter types (OR logic)."""
        Collection.objects.create(
            creator="local",
            slug="col-1",
            name="Mario Collection",
            description="Best Mario games",
        )
        col2 = Collection.objects.create(
            creator="local",
            slug="col-2",
            name="RPG Games",
            description="Role playing games",
        )

        # col2 has a Mario game entry
        CollectionEntry.objects.create(
            collection=col2, game_name="Super Mario RPG", system_slug="snes", position=0
        )

        # Search for "Mario" should find both:
        # col1 matches on name, col2 matches on entry game name
        response = client.get(reverse("romcollections:collection_search") + "?q=Mario")
        assert response.status_code == 200
        content = response.content.decode()
        assert "Mario Collection" in content
        assert "RPG Games" in content

    def test_search_ranking_title_beats_description(self, client, db):
        """Test that title matches rank higher than description-only matches."""
        # Collection with search term in title
        Collection.objects.create(
            creator="local",
            slug="mario-games",
            name="Mario Games",  # "Mario" in title
            description="A collection of platformers",
        )
        # Collection with search term only in description
        Collection.objects.create(
            creator="local",
            slug="platformers",
            name="Best Platformers",
            description="Including Mario and other classics",  # "Mario" in description
        )

        response = client.get(reverse("romcollections:collection_search") + "?q=Mario")
        assert response.status_code == 200
        content = response.content.decode()

        # Both should be found
        assert "Mario Games" in content
        assert "Best Platformers" in content

        # Title match should appear first (higher relevance)
        title_pos = content.find("Mario Games")
        desc_pos = content.find("Best Platformers")
        assert title_pos < desc_pos, (
            "Title match should rank higher than description match"
        )

    def test_search_ranking_description_beats_game_entries(self, client, db):
        """Test that description matches rank higher than game-entry-only matches."""
        # Collection with search term in description
        Collection.objects.create(
            creator="local",
            slug="zelda-collection",
            name="Nintendo Classics",
            description="Features Zelda and other adventures",  # "Zelda" in description
        )
        # Collection with search term only in game entries
        col_game = Collection.objects.create(
            creator="local",
            slug="rpg-games",
            name="RPG Games",
            description="Role playing games",
        )
        CollectionEntry.objects.create(
            collection=col_game,
            game_name="Legend of Zelda",  # "Zelda" in game name
            system_slug="nes",
            position=0,
        )

        response = client.get(reverse("romcollections:collection_search") + "?q=Zelda")
        assert response.status_code == 200
        content = response.content.decode()

        # Both should be found
        assert "Nintendo Classics" in content
        assert "RPG Games" in content

        # Description match should appear first (higher relevance)
        desc_pos = content.find("Nintendo Classics")
        game_pos = content.find("RPG Games")
        assert desc_pos < game_pos, (
            "Description match should rank higher than game entry match"
        )

    def test_search_ranking_more_games_rank_higher(self, client, db):
        """Test that collections with more matching games rank higher."""
        # Collection with one matching game
        col_one = Collection.objects.create(
            creator="local",
            slug="one-kirby",
            name="Single Kirby",
            description="Has one Kirby game",
        )
        CollectionEntry.objects.create(
            collection=col_one,
            game_name="Kirby's Adventure",
            system_slug="nes",
            position=0,
        )

        # Collection with three matching games
        col_many = Collection.objects.create(
            creator="local",
            slug="many-kirby",
            name="Kirby Collection",
            description="Has multiple Kirby games",
        )
        for i, game in enumerate(
            ["Kirby's Adventure", "Kirby's Dream Land", "Kirby Super Star"]
        ):
            CollectionEntry.objects.create(
                collection=col_many, game_name=game, system_slug="snes", position=i
            )

        response = client.get(reverse("romcollections:collection_search") + "?q=Kirby")
        assert response.status_code == 200
        content = response.content.decode()

        # Both should be found
        assert "Single Kirby" in content
        assert "Kirby Collection" in content

        # Collection with more matching games should rank higher
        # Note: "Kirby Collection" also matches on name, so it definitely ranks higher
        many_pos = content.find("Kirby Collection")
        one_pos = content.find("Single Kirby")
        assert many_pos < one_pos, "Collection with more matches should rank higher"

    def test_search_no_query_community_ordered_by_matches(self, client, system):
        """Test search with no query orders community collections by matched count."""
        # Create games with ROMs in the library
        game1 = Game.objects.create(name="Super Mario World", system=system)
        ROMSet.objects.create(game=game1, region="USA")
        game2 = Game.objects.create(name="Donkey Kong Country", system=system)
        ROMSet.objects.create(game=game2, region="USA")

        # Create community collections with different match counts
        col_few = Collection.objects.create(
            creator="local",
            slug="few-matches",
            name="AAA Few Matches",
            is_community=True,
        )
        CollectionEntry.objects.create(
            collection=col_few, game_name="Super Mario World", system_slug="snes"
        )

        col_many = Collection.objects.create(
            creator="local",
            slug="many-matches",
            name="ZZZ Many Matches",
            is_community=True,
        )
        CollectionEntry.objects.create(
            collection=col_many, game_name="Super Mario World", system_slug="snes"
        )
        CollectionEntry.objects.create(
            collection=col_many, game_name="Donkey Kong Country", system_slug="snes"
        )

        # Search with no query - should order by matched count
        response = client.get(
            reverse("romcollections:collection_search") + "?type=community"
        )
        assert response.status_code == 200

        community_collections = list(response.context["community_page_obj"])
        # ZZZ Many Matches (2) should come before AAA Few Matches (1)
        assert len(community_collections) == 2
        assert community_collections[0].slug == "many-matches"
        assert community_collections[1].slug == "few-matches"


class TestAdoptCollectionView:
    def test_adopt_community_collection(self, client, db):
        """Test adopting a community collection converts it to personal."""
        collection = Collection.objects.create(
            creator="local", slug="to-adopt", name="To Adopt", is_community=True
        )

        response = client.post(
            reverse(
                "romcollections:adopt_collection",
                kwargs={"creator": "local", "slug": "to-adopt"},
            )
        )
        assert response.status_code == 302

        collection.refresh_from_db()
        assert collection.is_community is False

    def test_adopt_personal_collection_404(self, client, db):
        """Test adopting a personal collection returns 404."""
        Collection.objects.create(
            creator="local",
            slug="already-personal",
            name="Already Personal",
            is_community=False,
        )

        response = client.post(
            reverse(
                "romcollections:adopt_collection",
                kwargs={"creator": "local", "slug": "already-personal"},
            )
        )
        assert response.status_code == 404

    def test_adopt_requires_post(self, client, db):
        """Test that adopt endpoint only accepts POST."""
        Collection.objects.create(
            creator="local", slug="test", name="Test", is_community=True
        )

        response = client.get(
            reverse(
                "romcollections:adopt_collection",
                kwargs={"creator": "local", "slug": "test"},
            )
        )
        assert response.status_code == 405


class TestUnadoptCollectionView:
    def test_unadopt_personal_collection(self, client, db):
        """Test unadopting a personal collection converts it to community."""
        collection = Collection.objects.create(
            creator="local", slug="to-unadopt", name="To Unadopt", is_community=False
        )

        response = client.post(
            reverse(
                "romcollections:unadopt_collection",
                kwargs={"creator": "local", "slug": "to-unadopt"},
            )
        )
        assert response.status_code == 302

        collection.refresh_from_db()
        assert collection.is_community is True

    def test_unadopt_community_collection_404(self, client, db):
        """Test unadopting a community collection returns 404."""
        Collection.objects.create(
            creator="local",
            slug="already-community",
            name="Already Community",
            is_community=True,
        )

        response = client.post(
            reverse(
                "romcollections:unadopt_collection",
                kwargs={"creator": "local", "slug": "already-community"},
            )
        )
        assert response.status_code == 404

    def test_unadopt_requires_post(self, client, db):
        """Test that unadopt endpoint only accepts POST."""
        Collection.objects.create(
            creator="local", slug="test", name="Test", is_community=False
        )

        response = client.get(
            reverse(
                "romcollections:unadopt_collection",
                kwargs={"creator": "local", "slug": "test"},
            )
        )
        assert response.status_code == 405


class TestCollectionDetailView:
    def test_detail_view(self, client, collection_with_entry, game):
        """Test collection detail view."""
        response = client.get(
            reverse(
                "romcollections:collection_detail",
                kwargs={
                    "creator": collection_with_entry.creator,
                    "slug": collection_with_entry.slug,
                },
            )
        )
        assert response.status_code == 200
        assert b"Test Collection" in response.content
        assert b"Super Mario World" in response.content

    def test_detail_shows_match_status(self, client, collection_with_entry, game):
        """Test that match status is shown."""
        response = client.get(
            reverse(
                "romcollections:collection_detail",
                kwargs={
                    "creator": collection_with_entry.creator,
                    "slug": collection_with_entry.slug,
                },
            )
        )
        assert response.status_code == 200
        assert b"In Library" in response.content

    def test_detail_404(self, client, db):
        """Test 404 for nonexistent collection."""
        response = client.get(
            reverse(
                "romcollections:collection_detail",
                kwargs={"creator": "local", "slug": "nonexistent"},
            )
        )
        assert response.status_code == 404

    def test_detail_sorting_by_name(self, client, db, system):
        """Test sorting entries by name."""
        collection = Collection.objects.create(
            creator="local", slug="sort-test", name="Sort Test"
        )
        CollectionEntry.objects.create(
            collection=collection, game_name="Zelda", system_slug="snes", position=0
        )
        CollectionEntry.objects.create(
            collection=collection, game_name="Aladdin", system_slug="snes", position=1
        )

        # Sort by name ascending
        response = client.get(
            reverse(
                "romcollections:collection_detail",
                kwargs={"creator": "local", "slug": "sort-test"},
            )
            + "?sort=name&order=asc"
        )
        assert response.status_code == 200
        content = response.content.decode()
        # Aladdin should come before Zelda in ASC order
        assert content.index("Aladdin") < content.index("Zelda")

        # Sort by name descending
        response = client.get(
            reverse(
                "romcollections:collection_detail",
                kwargs={"creator": "local", "slug": "sort-test"},
            )
            + "?sort=name&order=desc"
        )
        content = response.content.decode()
        # Zelda should come before Aladdin in DESC order
        assert content.index("Zelda") < content.index("Aladdin")

    def test_detail_sorting_by_status(self, client, db, system):
        """Test sorting entries by match status."""
        collection = Collection.objects.create(
            creator="local", slug="status-test", name="Status Test"
        )
        # Create a matched game
        game = Game.objects.create(name="Super Mario World", system=system)
        ROMSet.objects.create(game=game, region="USA")

        # One matched entry
        CollectionEntry.objects.create(
            collection=collection,
            game_name="Super Mario World",
            system_slug="snes",
            position=0,
        )
        # One unmatched entry
        CollectionEntry.objects.create(
            collection=collection,
            game_name="NonexistentGame",
            system_slug="snes",
            position=1,
        )

        # Sort by status descending (matched first)
        response = client.get(
            reverse(
                "romcollections:collection_detail",
                kwargs={"creator": "local", "slug": "status-test"},
            )
            + "?sort=status&order=desc"
        )
        assert response.status_code == 200
        content = response.content.decode()
        # "In Library" badge should appear before "Not in Library" badge
        assert content.index("In Library") < content.index("Not in Library")

    def test_detail_sorting_context(self, client, collection_with_entry):
        """Test that sorting context variables are passed to template."""
        response = client.get(
            reverse(
                "romcollections:collection_detail",
                kwargs={
                    "creator": collection_with_entry.creator,
                    "slug": collection_with_entry.slug,
                },
            )
            + "?sort=name&order=desc"
        )
        assert response.status_code == 200
        assert response.context["current_sort"] == "name"
        assert response.context["current_order"] == "desc"

    def test_detail_system_icon_shown(self, client, db):
        """Test that system icon is passed to template even for unmatched entries."""
        system, _ = System.objects.get_or_create(
            slug="gba",
            defaults={
                "name": "Game Boy Advance",
                "extensions": [".gba"],
                "folder_names": ["GBA"],
            },
        )
        # Update the icon_path for the test
        system.icon_path = "/path/to/icon.png"
        system.save()

        collection = Collection.objects.create(
            creator="local", slug="icon-test", name="Icon Test"
        )
        CollectionEntry.objects.create(
            collection=collection,
            game_name="Some Unmatched Game",
            system_slug="gba",
            position=0,
        )

        response = client.get(
            reverse(
                "romcollections:collection_detail",
                kwargs={"creator": "local", "slug": "icon-test"},
            )
        )
        assert response.status_code == 200
        # The system should be in the context entries
        entries = response.context["entries"]
        assert len(entries) == 1
        assert entries[0]["system"] == system
        assert entries[0]["system"].icon_path == "/path/to/icon.png"

    def test_detail_pagination_default_page_size(self, client, db, system):
        """Test pagination defaults to 25 items per page."""
        collection = Collection.objects.create(
            creator="local", slug="pagination-test", name="Test"
        )
        # Create 30 entries
        for i in range(30):
            CollectionEntry.objects.create(
                collection=collection,
                game_name=f"Game {i:02d}",
                system_slug="snes",
                position=i,
            )

        response = client.get(
            reverse(
                "romcollections:collection_detail",
                kwargs={"creator": "local", "slug": "pagination-test"},
            )
        )
        assert response.status_code == 200
        assert response.context["current_page_size"] == 25
        assert response.context["page_obj"].paginator.per_page == 25
        assert len(response.context["entries"]) == 25
        assert response.context["total_count"] == 30

    def test_detail_pagination_custom_page_size(self, client, db, system):
        """Test pagination with custom page size."""
        collection = Collection.objects.create(
            creator="local", slug="pagination-test", name="Test"
        )
        for i in range(60):
            CollectionEntry.objects.create(
                collection=collection,
                game_name=f"Game {i:02d}",
                system_slug="snes",
                position=i,
            )

        response = client.get(
            reverse(
                "romcollections:collection_detail",
                kwargs={"creator": "local", "slug": "pagination-test"},
            )
            + "?page_size=50"
        )
        assert response.status_code == 200
        assert response.context["current_page_size"] == 50
        assert len(response.context["entries"]) == 50

    def test_detail_pagination_invalid_page_size_fallback(self, client, db, system):
        """Test that invalid page size falls back to 25."""
        collection = Collection.objects.create(
            creator="local", slug="pagination-test", name="Test"
        )
        for i in range(30):
            CollectionEntry.objects.create(
                collection=collection,
                game_name=f"Game {i:02d}",
                system_slug="snes",
                position=i,
            )

        # Test with invalid page size
        response = client.get(
            reverse(
                "romcollections:collection_detail",
                kwargs={"creator": "local", "slug": "pagination-test"},
            )
            + "?page_size=999"
        )
        assert response.status_code == 200
        assert response.context["current_page_size"] == 25

        # Test with non-numeric page size
        response = client.get(
            reverse(
                "romcollections:collection_detail",
                kwargs={"creator": "local", "slug": "pagination-test"},
            )
            + "?page_size=invalid"
        )
        assert response.status_code == 200
        assert response.context["current_page_size"] == 25

    def test_detail_pagination_session_persistence(self, client, db, system):
        """Test that page size preference is stored in session."""
        collection = Collection.objects.create(
            creator="local", slug="pagination-test", name="Test"
        )
        for i in range(60):
            CollectionEntry.objects.create(
                collection=collection,
                game_name=f"Game {i:02d}",
                system_slug="snes",
                position=i,
            )

        # First request with page_size=50
        response = client.get(
            reverse(
                "romcollections:collection_detail",
                kwargs={"creator": "local", "slug": "pagination-test"},
            )
            + "?page_size=50"
        )
        assert response.status_code == 200
        assert response.context["current_page_size"] == 50

        # Second request without page_size should use session value
        response = client.get(
            reverse(
                "romcollections:collection_detail",
                kwargs={"creator": "local", "slug": "pagination-test"},
            )
        )
        assert response.status_code == 200
        assert response.context["current_page_size"] == 50

    def test_detail_pagination_page_navigation(self, client, db, system):
        """Test page navigation works correctly."""
        collection = Collection.objects.create(
            creator="local", slug="pagination-test", name="Test"
        )
        for i in range(60):
            CollectionEntry.objects.create(
                collection=collection,
                game_name=f"Game {i:02d}",
                system_slug="snes",
                position=i,
            )

        # Get page 2
        response = client.get(
            reverse(
                "romcollections:collection_detail",
                kwargs={"creator": "local", "slug": "pagination-test"},
            )
            + "?page=2&page_size=25"
        )
        assert response.status_code == 200
        page_obj = response.context["page_obj"]
        assert page_obj.number == 2
        assert page_obj.has_previous()
        assert page_obj.has_next()


class TestCollectionCreateView:
    def test_create_get(self, client, db):
        """Test GET for create form."""
        response = client.get(reverse("romcollections:collection_create"))
        assert response.status_code == 200
        assert b"New Collection" in response.content

    def test_create_post(self, client, db):
        """Test creating a collection."""
        response = client.post(
            reverse("romcollections:collection_create"),
            {
                "name": "New Collection",
                "description": "A new collection",
                "creator": "Test User",
                "tags": "tag1, tag2",
            },
        )
        assert response.status_code == 302
        collection = Collection.objects.get(slug="new-collection")
        assert collection.name == "New Collection"
        assert collection.tags == ["tag1", "tag2"]

    def test_create_generates_unique_slug(self, client, collection):
        """Test slug generation when name conflicts."""
        response = client.post(
            reverse("romcollections:collection_create"),
            {"name": "Test Collection", "creator": "local"},
        )
        assert response.status_code == 302
        assert Collection.objects.filter(slug="test-collection-1").exists()

    def test_create_description_max_length_valid(self, client, db):
        """Test creating collection with exactly 1000 char description succeeds."""
        response = client.post(
            reverse("romcollections:collection_create"),
            {
                "name": "Max Length Test",
                "creator": "local",
                "description": "x" * 1000,
            },
        )
        assert response.status_code == 302
        assert Collection.objects.filter(name="Max Length Test").exists()

    def test_create_description_max_length_invalid(self, client, db):
        """Test creating collection with >1000 char description fails."""
        response = client.post(
            reverse("romcollections:collection_create"),
            {
                "name": "Too Long Description",
                "creator": "local",
                "description": "x" * 1001,
            },
        )
        assert response.status_code == 200  # Re-renders form with error
        assert b"1000 characters or less" in response.content
        assert not Collection.objects.filter(name="Too Long Description").exists()


class TestCollectionEditView:
    def test_edit_get(self, client, collection):
        """Test GET for edit form."""
        response = client.get(
            reverse(
                "romcollections:collection_edit",
                kwargs={"creator": collection.creator, "slug": collection.slug},
            )
        )
        assert response.status_code == 200
        assert b"Edit Collection" in response.content

    def test_edit_post(self, client, collection):
        """Test editing a collection."""
        response = client.post(
            reverse(
                "romcollections:collection_edit",
                kwargs={"creator": collection.creator, "slug": collection.slug},
            ),
            {
                "name": "Updated Name",
                "description": "Updated description",
                "creator": "",
                "tags": "",
            },
        )
        assert response.status_code == 302
        collection.refresh_from_db()
        assert collection.name == "Updated Name"

    def test_edit_description_max_length_valid(self, client, collection):
        """Test editing collection with exactly 1000 char description succeeds."""
        response = client.post(
            reverse(
                "romcollections:collection_edit",
                kwargs={"creator": collection.creator, "slug": collection.slug},
            ),
            {
                "name": collection.name,
                "description": "x" * 1000,
            },
        )
        assert response.status_code == 302
        collection.refresh_from_db()
        assert len(collection.description) == 1000

    def test_edit_description_max_length_invalid(self, client, collection):
        """Test editing collection with >1000 char description fails."""
        original_description = collection.description
        response = client.post(
            reverse(
                "romcollections:collection_edit",
                kwargs={"creator": collection.creator, "slug": collection.slug},
            ),
            {
                "name": collection.name,
                "description": "x" * 1001,
            },
        )
        assert response.status_code == 200  # Re-renders form with error
        assert b"1000 characters or less" in response.content
        collection.refresh_from_db()
        assert collection.description == original_description  # Unchanged


class TestCollectionDeleteView:
    def test_delete(self, client, collection):
        """Test deleting a collection."""
        response = client.post(
            reverse(
                "romcollections:collection_delete",
                kwargs={"creator": collection.creator, "slug": collection.slug},
            )
        )
        assert response.status_code == 302
        assert not Collection.objects.filter(slug=collection.slug).exists()


class TestAddEntryView:
    def test_add_entry(self, client, collection):
        """Test adding an entry."""
        response = client.post(
            reverse(
                "romcollections:add_entry",
                kwargs={"creator": collection.creator, "slug": collection.slug},
            ),
            {
                "game_name": "New Game",
                "system_slug": "snes",
                "notes": "Test note",
            },
        )
        assert response.status_code == 200
        assert collection.entries.filter(game_name="New Game").exists()

    def test_add_entry_duplicate(self, client, collection_with_entry):
        """Test adding duplicate entry fails."""
        response = client.post(
            reverse(
                "romcollections:add_entry",
                kwargs={
                    "creator": collection_with_entry.creator,
                    "slug": collection_with_entry.slug,
                },
            ),
            {
                "game_name": "Super Mario World",
                "system_slug": "snes",
            },
        )
        assert response.status_code == 400

    def test_add_entry_missing_fields(self, client, collection):
        """Test adding entry without required fields fails."""
        response = client.post(
            reverse(
                "romcollections:add_entry",
                kwargs={"creator": collection.creator, "slug": collection.slug},
            ),
            {"game_name": "Test"},
        )
        assert response.status_code == 400

    def test_add_entry_from_library_search(self, client, collection, game):
        """Test adding entry returns JSON success response."""
        response = client.post(
            reverse(
                "romcollections:add_entry",
                kwargs={"creator": collection.creator, "slug": collection.slug},
            ),
            {
                "game_name": game.name,
                "system_slug": game.system.slug,
            },
        )
        assert response.status_code == 200
        assert collection.entries.filter(game_name=game.name).exists()
        # Should return JSON success response
        data = response.json()
        assert data["success"] is True
        assert data["collection_name"] == collection.name
        assert data["game_name"] == game.name

    def test_add_entry_notes_max_length_valid(self, client, collection):
        """Test adding entry with exactly 1000 char notes succeeds."""
        response = client.post(
            reverse(
                "romcollections:add_entry",
                kwargs={"creator": collection.creator, "slug": collection.slug},
            ),
            {
                "game_name": "Long Notes Game",
                "system_slug": "snes",
                "notes": "x" * 1000,
            },
        )
        assert response.status_code == 200
        entry = collection.entries.get(game_name="Long Notes Game")
        assert len(entry.notes) == 1000

    def test_add_entry_notes_max_length_invalid(self, client, collection):
        """Test adding entry with >1000 char notes fails."""
        response = client.post(
            reverse(
                "romcollections:add_entry",
                kwargs={"creator": collection.creator, "slug": collection.slug},
            ),
            {
                "game_name": "Too Long Notes",
                "system_slug": "snes",
                "notes": "x" * 1001,
            },
        )
        assert response.status_code == 400
        assert b"1000 characters or less" in response.content
        assert not collection.entries.filter(game_name="Too Long Notes").exists()


class TestRemoveEntryView:
    def test_remove_entry(self, client, collection_with_entry):
        """Test removing an entry."""
        entry = collection_with_entry.entries.first()
        response = client.post(
            reverse(
                "romcollections:remove_entry",
                kwargs={
                    "creator": collection_with_entry.creator,
                    "slug": collection_with_entry.slug,
                    "pk": entry.pk,
                },
            )
        )
        assert response.status_code == 200
        assert not collection_with_entry.entries.exists()


class TestReorderEntriesView:
    def test_reorder_entries(self, client, collection):
        """Test reordering entries."""
        entry1 = CollectionEntry.objects.create(
            collection=collection,
            game_name="Game 1",
            system_slug="snes",
            position=0,
        )
        entry2 = CollectionEntry.objects.create(
            collection=collection,
            game_name="Game 2",
            system_slug="snes",
            position=1,
        )

        response = client.post(
            reverse(
                "romcollections:reorder_entries",
                kwargs={"creator": collection.creator, "slug": collection.slug},
            ),
            json.dumps({"order": [entry2.pk, entry1.pk]}),
            content_type="application/json",
        )
        assert response.status_code == 200

        entry1.refresh_from_db()
        entry2.refresh_from_db()
        assert entry1.position == 1
        assert entry2.position == 0


class TestBulkRemoveEntriesView:
    def test_bulk_remove_entries(self, client, collection):
        """Test bulk removing multiple entries."""
        entry1 = CollectionEntry.objects.create(
            collection=collection,
            game_name="Game 1",
            system_slug="snes",
            position=0,
        )
        entry2 = CollectionEntry.objects.create(
            collection=collection,
            game_name="Game 2",
            system_slug="snes",
            position=1,
        )
        entry3 = CollectionEntry.objects.create(
            collection=collection,
            game_name="Game 3",
            system_slug="snes",
            position=2,
        )

        response = client.post(
            reverse(
                "romcollections:bulk_remove_entries",
                kwargs={"creator": collection.creator, "slug": collection.slug},
            ),
            json.dumps({"entry_ids": [entry1.pk, entry2.pk]}),
            content_type="application/json",
        )
        assert response.status_code == 200

        data = json.loads(response.content)
        assert data["deleted"] == 2

        # Verify entries were deleted
        assert not CollectionEntry.objects.filter(pk=entry1.pk).exists()
        assert not CollectionEntry.objects.filter(pk=entry2.pk).exists()
        # entry3 should still exist
        assert CollectionEntry.objects.filter(pk=entry3.pk).exists()

    def test_bulk_remove_empty_list(self, client, collection):
        """Test bulk remove with empty list returns error."""
        response = client.post(
            reverse(
                "romcollections:bulk_remove_entries",
                kwargs={"creator": collection.creator, "slug": collection.slug},
            ),
            json.dumps({"entry_ids": []}),
            content_type="application/json",
        )
        assert response.status_code == 400

    def test_bulk_remove_invalid_json(self, client, collection):
        """Test bulk remove with invalid JSON returns error."""
        response = client.post(
            reverse(
                "romcollections:bulk_remove_entries",
                kwargs={"creator": collection.creator, "slug": collection.slug},
            ),
            "invalid json",
            content_type="application/json",
        )
        assert response.status_code == 400


class TestUpdateEntryNotesView:
    def test_update_entry_notes(self, client, collection_with_entry):
        """Test updating entry notes via POST."""
        entry = collection_with_entry.entries.first()
        response = client.post(
            reverse(
                "romcollections:update_entry_notes",
                kwargs={
                    "creator": collection_with_entry.creator,
                    "slug": collection_with_entry.slug,
                    "pk": entry.pk,
                },
            ),
            {"notes": "New notes content"},
        )
        assert response.status_code == 200
        entry.refresh_from_db()
        assert entry.notes == "New notes content"
        # Verify response contains updated entry row
        assert b"New notes content" in response.content

    def test_update_entry_notes_clears_notes(self, client, collection_with_entry):
        """Test that empty notes field clears notes."""
        entry = collection_with_entry.entries.first()
        entry.notes = "Old notes"
        entry.save()

        response = client.post(
            reverse(
                "romcollections:update_entry_notes",
                kwargs={
                    "creator": collection_with_entry.creator,
                    "slug": collection_with_entry.slug,
                    "pk": entry.pk,
                },
            ),
            {"notes": ""},
        )
        assert response.status_code == 200
        entry.refresh_from_db()
        assert entry.notes == ""

    def test_update_entry_notes_invalid_collection(self, client, collection_with_entry):
        """Test updating entry with wrong collection returns 404."""
        entry = collection_with_entry.entries.first()
        response = client.post(
            reverse(
                "romcollections:update_entry_notes",
                kwargs={"creator": "local", "slug": "invalid-slug", "pk": entry.pk},
            ),
            {"notes": "Test"},
        )
        assert response.status_code == 404

    def test_update_entry_notes_strips_whitespace(self, client, collection_with_entry):
        """Test that notes are stripped of leading/trailing whitespace."""
        entry = collection_with_entry.entries.first()
        response = client.post(
            reverse(
                "romcollections:update_entry_notes",
                kwargs={
                    "creator": collection_with_entry.creator,
                    "slug": collection_with_entry.slug,
                    "pk": entry.pk,
                },
            ),
            {"notes": "  \n  Trimmed notes  \n  "},
        )
        assert response.status_code == 200
        entry.refresh_from_db()
        assert entry.notes == "Trimmed notes"

    def test_update_entry_notes_mobile_context(self, client, collection_with_entry):
        """Test that mobile context returns the card template."""
        entry = collection_with_entry.entries.first()
        response = client.post(
            reverse(
                "romcollections:update_entry_notes",
                kwargs={
                    "creator": collection_with_entry.creator,
                    "slug": collection_with_entry.slug,
                    "pk": entry.pk,
                },
            ),
            {"notes": "Mobile test", "context": "mobile"},
        )
        assert response.status_code == 200
        entry.refresh_from_db()
        assert entry.notes == "Mobile test"
        # Verify it returns the card template (has entry-card class)
        assert b"entry-card" in response.content

    def test_update_entry_notes_max_length_valid(self, client, collection_with_entry):
        """Test updating entry with exactly 1000 char notes succeeds."""
        entry = collection_with_entry.entries.first()
        response = client.post(
            reverse(
                "romcollections:update_entry_notes",
                kwargs={
                    "creator": collection_with_entry.creator,
                    "slug": collection_with_entry.slug,
                    "pk": entry.pk,
                },
            ),
            {"notes": "x" * 1000},
        )
        assert response.status_code == 200
        entry.refresh_from_db()
        assert len(entry.notes) == 1000

    def test_update_entry_notes_max_length_invalid(self, client, collection_with_entry):
        """Test updating entry with >1000 char notes fails."""
        entry = collection_with_entry.entries.first()
        original_notes = entry.notes
        response = client.post(
            reverse(
                "romcollections:update_entry_notes",
                kwargs={
                    "creator": collection_with_entry.creator,
                    "slug": collection_with_entry.slug,
                    "pk": entry.pk,
                },
            ),
            {"notes": "x" * 1001},
        )
        assert response.status_code == 400
        assert b"1000 characters or less" in response.content
        entry.refresh_from_db()
        assert entry.notes == original_notes  # Unchanged

    def test_update_entry_notes_has_roms_context(self, client, collection, game):
        """Test that has_roms context is correctly passed when game has ROMs."""
        # Create an entry that matches the game (which has a ROMSet)
        entry = CollectionEntry.objects.create(
            collection=collection,
            game_name="Super Mario World",
            system_slug="snes",
            position=0,
        )
        response = client.post(
            reverse(
                "romcollections:update_entry_notes",
                kwargs={
                    "creator": collection.creator,
                    "slug": collection.slug,
                    "pk": entry.pk,
                },
            ),
            {"notes": "Test notes"},
        )
        assert response.status_code == 200
        # The game has ROMs, so "In Library" should be shown
        assert b"In Library" in response.content
        assert b"Not in Library" not in response.content

    def test_update_entry_notes_has_roms_false(self, client, collection, system):
        """Test that has_roms=False when game has no ROMs."""
        # Create a game without any ROMSets
        game_no_roms = Game.objects.create(name="Super Mario World", system=system)
        # Create an entry that matches this game
        entry = CollectionEntry.objects.create(
            collection=collection,
            game_name="Super Mario World",
            system_slug="snes",
            position=0,
        )
        response = client.post(
            reverse(
                "romcollections:update_entry_notes",
                kwargs={
                    "creator": collection.creator,
                    "slug": collection.slug,
                    "pk": entry.pk,
                },
            ),
            {"notes": "Test notes"},
        )
        assert response.status_code == 200
        # The game has no ROMs, so "Not in Library" should be shown
        assert b"Not in Library" in response.content
        assert b"In Library" not in response.content


class TestExportCollectionView:
    def test_export_json(self, client, collection_with_entry):
        """Test exporting collection as JSON."""
        response = client.get(
            reverse(
                "romcollections:export_collection",
                kwargs={
                    "creator": collection_with_entry.creator,
                    "slug": collection_with_entry.slug,
                },
            )
        )
        assert response.status_code == 200
        assert response["Content-Type"] == "application/json"
        assert "attachment" in response["Content-Disposition"]

        data = json.loads(response.content)
        assert data["collection"]["slug"] == "test-collection"


class TestImportCollectionView:
    def test_import_get(self, client, db):
        """Test GET for import form."""
        response = client.get(reverse("romcollections:import_collection"))
        assert response.status_code == 200
        assert b"Import Collection" in response.content

    def test_import_valid_file(self, client, db):
        """Test importing a valid collection file."""
        from io import BytesIO

        data = {
            "romhoard_collection": {"version": "1.0"},
            "collection": {"slug": "imported", "name": "Imported Collection"},
            "entries": [{"game_name": "Test Game", "system_slug": "snes"}],
        }
        file_content = json.dumps(data).encode()
        file = BytesIO(file_content)
        file.name = "collection.json"

        response = client.post(
            reverse("romcollections:import_collection"),
            {"file": file},
        )
        assert response.status_code == 302
        # Imported collections get creator="local" if not provided
        collection = Collection.objects.get(creator="local", slug="imported")
        assert collection.name == "Imported Collection"

    def test_import_zip_file(self, client, db, tmp_path):
        """Test importing a valid collection ZIP file."""
        import zipfile
        from io import BytesIO

        collection_data = {
            "romhoard_collection": {"version": "1.0"},
            "collection": {"slug": "zip-import", "name": "ZIP Import"},
            "entries": [{"game_name": "Test Game", "system_slug": "snes"}],
        }

        # Create ZIP file
        zip_path = tmp_path / "test_collection.zip"
        with zipfile.ZipFile(zip_path, "w") as zipf:
            zipf.writestr(
                "collection.json", json.dumps(collection_data, ensure_ascii=False)
            )

        with open(zip_path, "rb") as f:
            file = BytesIO(f.read())
            file.name = "test_collection.zip"

            response = client.post(
                reverse("romcollections:import_collection"),
                {"file": file},
            )

        assert response.status_code == 302
        # Imported collections get creator="local" if not provided
        collection = Collection.objects.get(creator="local", slug="zip-import")
        assert collection.name == "ZIP Import"

    def test_import_invalid_zip_shows_error(self, client, db, tmp_path):
        """Test importing an invalid ZIP shows error message."""
        import zipfile
        from io import BytesIO

        # Create ZIP without collection.json
        zip_path = tmp_path / "invalid.zip"
        with zipfile.ZipFile(zip_path, "w") as zipf:
            zipf.writestr("readme.txt", "No collection here")

        with open(zip_path, "rb") as f:
            file = BytesIO(f.read())
            file.name = "invalid.zip"

            response = client.post(
                reverse("romcollections:import_collection"),
                {"file": file},
            )

        assert response.status_code == 200
        content = response.content.decode()
        assert "collection.json" in content or "Import failed" in content

    def test_import_corrupt_zip_shows_error(self, client, db):
        """Test importing a corrupt ZIP shows error message."""
        from io import BytesIO

        file = BytesIO(b"This is not a zip file")
        file.name = "corrupt.zip"

        response = client.post(
            reverse("romcollections:import_collection"),
            {"file": file},
        )

        assert response.status_code == 200
        content = response.content.decode()
        assert "not a valid ZIP" in content or "Invalid ZIP" in content

    def test_import_zip_with_images(self, client, db, tmp_path, settings):
        """Test importing a ZIP with images."""
        import zipfile
        from io import BytesIO

        from library.models import Game, System

        # Create system first
        system, _ = System.objects.get_or_create(
            slug="snes",
            defaults={
                "name": "Super Nintendo",
                "extensions": [".sfc"],
                "folder_names": ["SNES"],
            },
        )

        # Create game
        game = Game.objects.create(name="Test Game", system=system)

        # Set up images directory
        images_dir = tmp_path / "images_output"
        images_dir.mkdir()
        settings.MEDIA_ROOT = str(images_dir)

        collection_data = {
            "romhoard_collection": {"version": "1.0"},
            "collection": {"slug": "images-import", "name": "Images Import"},
            "entries": [{"game_name": "Test Game", "system_slug": "snes"}],
        }

        # Minimal PNG data
        png_data = (
            b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01"
            b"\x00\x00\x00\x01\x08\x02\x00\x00\x00\x90wS\xde\x00"
            b"\x00\x00\x0cIDATx\x9cc\xf8\x0f\x00\x00\x01\x01\x00"
            b"\x05\x18\xd8N\x00\x00\x00\x00IEND\xaeB`\x82"
        )

        zip_path = tmp_path / "with_images.zip"
        with zipfile.ZipFile(zip_path, "w") as zipf:
            zipf.writestr(
                "collection.json", json.dumps(collection_data, ensure_ascii=False)
            )
            zipf.writestr("images/Test Game_snes/cover.png", png_data)

        with open(zip_path, "rb") as f:
            file = BytesIO(f.read())
            file.name = "with_images.zip"

            response = client.post(
                reverse("romcollections:import_collection"),
                {"file": file},
            )

        assert response.status_code == 302
        # Imported collections get creator="local" if not provided
        collection = Collection.objects.get(creator="local", slug="images-import")
        assert collection.name == "Images Import"


class TestDownloadCollectionView:
    def test_download_no_matches(self, client, collection_with_entry):
        """Test download with no matched games."""
        response = client.post(
            reverse(
                "romcollections:download_collection",
                kwargs={
                    "creator": collection_with_entry.creator,
                    "slug": collection_with_entry.slug,
                },
            )
        )
        assert response.status_code == 400

    def test_download_single_game(self, client, collection_with_entry, game):
        """Test download with single matched game."""
        response = client.post(
            reverse(
                "romcollections:download_collection",
                kwargs={
                    "creator": collection_with_entry.creator,
                    "slug": collection_with_entry.slug,
                },
            )
        )
        assert response.status_code == 200
        data = json.loads(response.content)
        assert "redirect_url" in data


class TestFavoritesCollection:
    """Tests for the special Favorites collection functionality."""

    def test_favorites_collection_exists(self, db):
        """Test that Favorites collection is created by migration."""
        favorites = Collection.objects.filter(is_favorites=True).first()
        assert favorites is not None
        assert favorites.name == "Favorites"
        assert favorites.is_community is False

    def test_toggle_favorite_add(self, client, db, game):
        """Test adding a game to favorites via toggle endpoint."""
        favorites = Collection.objects.get(is_favorites=True)
        assert CollectionEntry.objects.filter(collection=favorites).count() == 0

        response = client.post(
            reverse("romcollections:toggle_favorite", args=[game.pk])
        )
        assert response.status_code == 200
        data = json.loads(response.content)
        assert data["is_favorite"] is True
        assert CollectionEntry.objects.filter(
            collection=favorites, game_name__iexact=game.name
        ).exists()

    def test_toggle_favorite_remove(self, client, db, game):
        """Test removing a game from favorites via toggle endpoint."""
        favorites = Collection.objects.get(is_favorites=True)
        CollectionEntry.objects.create(
            collection=favorites,
            game_name=game.name,
            system_slug=game.system.slug,
            position=0,
        )

        response = client.post(
            reverse("romcollections:toggle_favorite", args=[game.pk])
        )
        assert response.status_code == 200
        data = json.loads(response.content)
        assert data["is_favorite"] is False
        assert not CollectionEntry.objects.filter(
            collection=favorites, game_name__iexact=game.name
        ).exists()

    def test_cannot_delete_favorites(self, client, db):
        """Test that Favorites collection cannot be deleted."""
        favorites = Collection.objects.get(is_favorites=True)

        response = client.post(
            reverse(
                "romcollections:collection_delete",
                kwargs={"creator": favorites.creator, "slug": favorites.slug},
            )
        )
        assert response.status_code == 400
        assert Collection.objects.filter(is_favorites=True).exists()

    def test_favorites_in_picker_as_default(self, client, db):
        """Test that Favorites collection is included and is the default selection."""
        response = client.get(reverse("romcollections:collection_picker"))
        assert response.status_code == 200
        # Favorites should appear in the picker
        assert b"Favorites" in response.content
        # Favorites should be the default selection (passed as context)
        content = response.content.decode()
        # Check that Favorites collection data is passed for default selection
        assert "selectedCreator" in content
        assert "selectedSlug" in content

    def test_favorites_appears_first_in_list(self, client, db):
        """Test that Favorites collection appears first in the collection list."""
        Collection.objects.create(
            creator="local",
            slug="aaa-collection",
            name="AAA Collection",  # Alphabetically before Favorites
            is_community=False,
        )
        response = client.get(reverse("romcollections:collection_list"))
        assert response.status_code == 200
        content = response.content.decode()
        # Favorites should appear before AAA Collection
        favorites_pos = content.find("Favorites")
        aaa_pos = content.find("AAA Collection")
        assert favorites_pos < aaa_pos
