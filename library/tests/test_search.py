from django.test import TestCase
from django.urls import reverse

from library.models import Game, ROM, ROMSet, System


class GlobalSearchTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        # Use unique slugs to avoid conflicts with synced systems
        cls.gba = System.objects.create(
            name="Game Boy Advance Test",
            slug="gba-test",
            extensions=["gba"],
            folder_names=["GBA"],
        )
        cls.nes = System.objects.create(
            name="Nintendo Entertainment System Test",
            slug="nes-test",
            extensions=["nes"],
            folder_names=["NES"],
        )

        cls.game1 = Game.objects.create(name="Advance Wars", system=cls.gba)
        cls.game2 = Game.objects.create(name="Sonic Advance", system=cls.gba)
        cls.game3 = Game.objects.create(name="Super Mario Bros", system=cls.nes)

        # Create ROM sets and ROMs so systems show up (game_count > 0)
        for game in [cls.game1, cls.game2, cls.game3]:
            romset = ROMSet.objects.create(game=game, region="USA")
            ROM.objects.create(
                rom_set=romset,
                file_path=f"/fake/{game.name}.rom",
                file_name=f"{game.name}.rom",
                file_size=1024,
            )

    def test_empty_query_returns_system_grid(self):
        """Empty query should return the default system grid partial."""
        response = self.client.get(reverse("library:global_search"))
        self.assertEqual(response.status_code, 200)
        self.assertIn("systems", response.context)
        self.assertTemplateUsed(response, "library/_system_grid.html")

    def test_empty_query_with_whitespace(self):
        """Query with only whitespace should return the default system grid."""
        response = self.client.get(reverse("library:global_search"), {"q": "   "})
        self.assertEqual(response.status_code, 200)
        self.assertTemplateUsed(response, "library/_system_grid.html")

    def test_search_finds_systems(self):
        """Searching for a system name should return matching systems."""
        response = self.client.get(reverse("library:global_search"), {"q": "advance"})
        self.assertEqual(response.status_code, 200)
        self.assertTemplateUsed(response, "library/_global_search_results.html")
        self.assertIn(self.gba, list(response.context["matched_systems"]))

    def test_search_finds_games(self):
        """Searching for a game name should return matching games."""
        response = self.client.get(reverse("library:global_search"), {"q": "sonic"})
        self.assertEqual(response.status_code, 200)
        games_by_system = response.context["matched_games_by_system"]
        all_games = [g for _, games in games_by_system for g in games]
        self.assertIn(self.game2, all_games)

    def test_search_groups_games_by_system(self):
        """Games should be grouped by their system."""
        response = self.client.get(reverse("library:global_search"), {"q": "advance"})
        games_by_system = response.context["matched_games_by_system"]
        # Both "Advance Wars" and "Sonic Advance" match, grouped under GBA
        self.assertEqual(len(games_by_system), 1)
        system, games = games_by_system[0]
        self.assertEqual(system, self.gba)
        self.assertEqual(len(games), 2)

    def test_search_case_insensitive(self):
        """Search should be case insensitive."""
        response = self.client.get(reverse("library:global_search"), {"q": "MARIO"})
        games_by_system = response.context["matched_games_by_system"]
        all_games = [g for _, games in games_by_system for g in games]
        self.assertIn(self.game3, all_games)

    def test_no_results(self):
        """Query with no matches should return empty results."""
        response = self.client.get(
            reverse("library:global_search"), {"q": "zzzznotfound"}
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(response.context["matched_systems"]), 0)
        self.assertEqual(len(response.context["matched_games_by_system"]), 0)

    def test_systems_have_counts(self):
        """Matched systems should include game_count annotation."""
        response = self.client.get(reverse("library:global_search"), {"q": "advance"})
        matched_systems = list(response.context["matched_systems"])
        self.assertEqual(len(matched_systems), 1)
        gba = matched_systems[0]
        self.assertEqual(gba.game_count, 2)  # Advance Wars and Sonic Advance

    def test_query_in_context(self):
        """The query should be included in the context."""
        response = self.client.get(reverse("library:global_search"), {"q": "mario"})
        self.assertEqual(response.context["query"], "mario")

    def test_games_have_rom_count(self):
        """Games in results should have rom_count annotation."""
        response = self.client.get(reverse("library:global_search"), {"q": "advance"})
        games_by_system = response.context["matched_games_by_system"]
        for system, games in games_by_system:
            for game in games:
                self.assertTrue(hasattr(game, "rom_count"))
                self.assertEqual(game.rom_count, 1)  # Each test game has 1 romset

    def test_search_finds_systems_by_slug(self):
        """Searching for a system slug should return matching systems."""
        response = self.client.get(reverse("library:global_search"), {"q": "gba-test"})
        self.assertEqual(response.status_code, 200)
        self.assertTemplateUsed(response, "library/_global_search_results.html")
        matched_systems = list(response.context["matched_systems"])
        self.assertEqual(len(matched_systems), 1)
        self.assertEqual(matched_systems[0], self.gba)

    def test_search_finds_systems_by_partial_slug(self):
        """Searching for partial slug should return matching systems."""
        response = self.client.get(reverse("library:global_search"), {"q": "nes-"})
        self.assertEqual(response.status_code, 200)
        matched_systems = list(response.context["matched_systems"])
        self.assertIn(self.nes, matched_systems)
