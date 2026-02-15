import pytest
from django.urls import reverse
from library.models import System, Game, ROMSet
from romcollections.models import Collection, CollectionEntry


@pytest.mark.django_db
def test_game_list_includes_collection_summary(client):
    # Setup system
    system = System.objects.create(
        name="Super Nintendo", slug="sfc", extensions=[".sfc"], folder_names=["SFC"]
    )

    # Setup games in library (with romsets so they appear in the list)
    game1 = Game.objects.create(name="Super Mario World", system=system)
    ROMSet.objects.create(game=game1, region="USA")
    game2 = Game.objects.create(name="Zelda: Link to the Past", system=system)
    ROMSet.objects.create(game=game2, region="USA")
    game3 = Game.objects.create(name="Metroid", system=system)
    ROMSet.objects.create(game=game3, region="USA")

    # Setup collections
    c1 = Collection.objects.create(name="Mario Games", slug="mario", creator="local")
    c2 = Collection.objects.create(name="Top 100", slug="top-100", creator="local")

    # Add games to collections
    # Mario in both
    CollectionEntry.objects.create(
        collection=c1, game_name="Super Mario World", system_slug="sfc"
    )
    CollectionEntry.objects.create(
        collection=c2, game_name="Super Mario World", system_slug="sfc"
    )

    # Zelda in top 100 only
    CollectionEntry.objects.create(
        collection=c2, game_name="Zelda: Link to the Past", system_slug="sfc"
    )

    # Metroid not in any collection

    # Game not in library but in collection (should not be counted)
    CollectionEntry.objects.create(
        collection=c2, game_name="Donkey Kong Country", system_slug="sfc"
    )

    url = reverse("library:game_list", kwargs={"slug": system.slug})
    response = client.get(url)

    assert response.status_code == 200

    # Verify summary data in context
    # 2 distinct games (Mario, Zelda) from this system are in collections
    assert response.context["total_in_collections"] == 2

    # Collections list
    col_list = response.context["system_collections"]
    assert len(col_list) == 2

    # Sorted by count desc: Top 100 has 2 games, Mario has 1
    assert col_list[0].name == "Top 100"
    assert col_list[0].system_count == 2
    assert col_list[1].name == "Mario Games"
    assert col_list[1].system_count == 1


@pytest.mark.django_db
def test_page_size_default(client):
    """Test that default page size is 50."""
    system = System.objects.create(
        name="Super Nintendo", slug="sfc", extensions=[".sfc"], folder_names=["SFC"]
    )

    # Create 100 games (with romsets so they appear in the list)
    for i in range(100):
        game = Game.objects.create(name=f"Game {i:03d}", system=system)
        ROMSet.objects.create(game=game, region="USA")

    response = client.get(reverse("library:game_list", kwargs={"slug": system.slug}))

    assert response.status_code == 200
    assert response.context["current_page_size"] == 50
    assert response.context["page_obj"].paginator.per_page == 50


@pytest.mark.django_db
def test_page_size_query_param(client):
    """Test that page_size query param overrides default."""
    system = System.objects.create(
        name="Super Nintendo", slug="sfc", extensions=[".sfc"], folder_names=["SFC"]
    )

    for i in range(100):
        game = Game.objects.create(name=f"Game {i:03d}", system=system)
        ROMSet.objects.create(game=game, region="USA")

    url = reverse("library:game_list", kwargs={"slug": system.slug})
    response = client.get(f"{url}?page_size=100")

    assert response.status_code == 200
    assert response.context["current_page_size"] == 100
    assert response.context["page_obj"].paginator.per_page == 100


@pytest.mark.django_db
def test_page_size_session_persistence(client):
    """Test that page size persists in session."""
    system = System.objects.create(
        name="Super Nintendo", slug="sfc", extensions=[".sfc"], folder_names=["SFC"]
    )

    for i in range(100):
        game = Game.objects.create(name=f"Game {i:03d}", system=system)
        ROMSet.objects.create(game=game, region="USA")

    # First request with page_size=200
    url = reverse("library:game_list", kwargs={"slug": system.slug})
    response1 = client.get(f"{url}?page_size=200")
    assert response1.status_code == 200

    # Second request without page_size should use session value
    response2 = client.get(url)
    assert response2.status_code == 200
    assert response2.context["current_page_size"] == 200


@pytest.mark.django_db
def test_page_size_invalid_fallback(client):
    """Test that invalid page_size falls back to 50."""
    system = System.objects.create(
        name="Super Nintendo", slug="sfc", extensions=[".sfc"], folder_names=["SFC"]
    )

    for i in range(100):
        game = Game.objects.create(name=f"Game {i:03d}", system=system)
        ROMSet.objects.create(game=game, region="USA")

    # Invalid values should fallback to 50
    url = reverse("library:game_list", kwargs={"slug": system.slug})
    for invalid in ["999", "abc", "25", "250", "-50"]:
        response = client.get(f"{url}?page_size={invalid}")
        assert response.status_code == 200
        assert response.context["current_page_size"] == 50
        assert response.context["page_obj"].paginator.per_page == 50
