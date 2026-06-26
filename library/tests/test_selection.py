"""Tests for the selection size estimate endpoint and helper."""

import json

import pytest
from django.urls import reverse

from library.models import ROM, Game, ROMSet, System
from library.selection import estimate_games_size


@pytest.fixture
def system(db):
    system, _ = System.objects.get_or_create(
        slug="snes",
        defaults={
            "name": "Super Nintendo",
            "extensions": [".sfc"],
            "folder_names": ["SNES", "snes"],
        },
    )
    return system


def _make_game(system, name, rom_sizes, default_region="USA"):
    """Create a game with one ROMSet per size (in ``rom_sizes``).

    The first ROMSet becomes the default_rom_set.
    """
    game = Game.objects.create(name=name, system=system)
    romsets = []
    for region, sizes in rom_sizes:
        rs = ROMSet.objects.create(game=game, region=region)
        for i, size in enumerate(sizes):
            ROM.objects.create(
                rom_set=rs,
                file_path=f"/t/{name}-{region}-{i}.sfc",
                file_name=f"{name}-{region}-{i}.sfc",
                file_size=size,
            )
        romsets.append(rs)
    game.default_rom_set = romsets[0]
    game.save(update_fields=["default_rom_set"])
    return game


class TestEstimateGamesSize:
    def test_empty(self, db):
        assert estimate_games_size([]) == 0

    def test_sums_default_romset_only(self, db, system):
        game = _make_game(system, "Game A", [("USA", [500, 300]), ("EUR", [9999])])
        # Only the default USA ROMSet's ROMs count (500 + 300)
        assert estimate_games_size([game.pk]) == 800

    def test_multiple_games(self, db, system):
        g1 = _make_game(system, "Game A", [("USA", [500, 300])])
        g2 = _make_game(system, "Game B", [("USA", [1000])])
        assert estimate_games_size([g1.pk, g2.pk]) == 1800

    def test_game_without_romset(self, db, system):
        bare = Game.objects.create(name="Bare", system=system)
        assert estimate_games_size([bare.pk]) == 0

    def test_dedup_repeated_ids(self, db, system):
        game = _make_game(system, "Game A", [("USA", [700])])
        assert estimate_games_size([game.pk, game.pk]) == 700


class TestEstimateSelectionSizeView:
    def test_invalid_json(self, client, db):
        response = client.post(
            reverse("library:selection_size"),
            data="nope",
            content_type="application/json",
        )
        assert response.status_code == 400

    def test_unknown_item_type(self, client, db, system):
        response = client.post(
            reverse("library:selection_size"),
            data=json.dumps({"ids": [1], "item_type": "collection"}),
            content_type="application/json",
        )
        assert response.status_code == 400

    def test_returns_total_bytes(self, client, db, system):
        g1 = _make_game(system, "Game A", [("USA", [500, 300])])
        g2 = _make_game(system, "Game B", [("USA", [1000])])
        bare = Game.objects.create(name="Bare", system=system)

        response = client.post(
            reverse("library:selection_size"),
            data=json.dumps({"ids": [g1.pk, g2.pk, bare.pk], "item_type": "game"}),
            content_type="application/json",
        )
        assert response.status_code == 200
        assert json.loads(response.content) == {"total_bytes": 1800}

    def test_empty_ids(self, client, db):
        response = client.post(
            reverse("library:selection_size"),
            data=json.dumps({"ids": [], "item_type": "game"}),
            content_type="application/json",
        )
        assert response.status_code == 200
        assert json.loads(response.content) == {"total_bytes": 0}
