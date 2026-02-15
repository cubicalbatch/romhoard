"""Tests for ROMSet priority scoring."""

from django.test import TestCase

from library.models import Game, ROM, ROMSet, Setting, System
from library.romset_scoring import (
    STANDALONE_ARCHIVE_BONUS,
    calculate_romset_score,
    get_best_romset,
    get_region_priorities,
    get_region_score,
    is_standalone_archive,
    recalculate_default_romset,
)


class TestRegionScoring(TestCase):
    """Tests for region priority scoring."""

    def test_usa_highest_priority(self):
        """USA should have highest default priority."""
        score = get_region_score("USA")
        self.assertEqual(score, 1000)

    def test_europe_second_priority(self):
        """Europe should have second highest priority."""
        score = get_region_score("Europe")
        self.assertEqual(score, 800)

    def test_japan_third_priority(self):
        """Japan should have third priority."""
        score = get_region_score("Japan")
        self.assertEqual(score, 600)

    def test_world_fourth_priority(self):
        """World should have fourth priority."""
        score = get_region_score("World")
        self.assertEqual(score, 400)

    def test_unknown_region_gets_default_score(self):
        """Unknown regions should get the default low score."""
        score = get_region_score("Brazil")
        self.assertEqual(score, 200)

    def test_multi_region_uses_highest(self):
        """Multi-region strings should use the highest priority."""
        score = get_region_score("USA, Europe")
        self.assertEqual(score, 1000)

        score = get_region_score("Europe, Japan")
        self.assertEqual(score, 800)

    def test_custom_priorities_from_settings(self):
        """Custom priorities from Settings model should be used."""
        Setting.objects.create(
            key="region_priorities",
            value={"Germany": 999, "USA": 500},
        )
        try:
            priorities = get_region_priorities()
            self.assertEqual(priorities["Germany"], 999)
            self.assertEqual(priorities["USA"], 500)

            score = get_region_score("Germany")
            self.assertEqual(score, 999)
        finally:
            Setting.objects.filter(key="region_priorities").delete()


class TestStandaloneArchiveDetection(TestCase):
    """Tests for standalone archive detection."""

    @classmethod
    def setUpTestData(cls):
        cls.system = System.objects.create(
            name="Test System", slug="test", extensions=[".rom"], folder_names=["test"]
        )
        cls.game = Game.objects.create(name="Test Game", system=cls.system)

    def test_loose_file_is_standalone(self):
        """Loose (non-archived) files should be treated as standalone."""
        rom_set = ROMSet.objects.create(game=self.game, region="USA")
        ROM.objects.create(
            rom_set=rom_set,
            file_path="/roms/game.rom",
            file_name="game.rom",
            file_size=1000,
            archive_path="",
        )
        self.assertTrue(is_standalone_archive(rom_set))

    def test_single_rom_archive_is_standalone(self):
        """Archive with single ROM should be standalone."""
        rom_set = ROMSet.objects.create(
            game=self.game, region="USA", source_path="/single"
        )
        ROM.objects.create(
            rom_set=rom_set,
            file_path="/roms/game.zip!game.rom",
            file_name="game.rom",
            file_size=1000,
            archive_path="/roms/game.zip",
            path_in_archive="game.rom",
        )
        self.assertTrue(is_standalone_archive(rom_set))

    def test_multi_rom_archive_not_standalone(self):
        """Archive with multiple ROMs should not be standalone."""
        rom_set = ROMSet.objects.create(
            game=self.game, region="USA", source_path="/multi"
        )
        ROM.objects.create(
            rom_set=rom_set,
            file_path="/roms/collection.zip!game1.rom",
            file_name="game1.rom",
            file_size=1000,
            archive_path="/roms/collection.zip",
            path_in_archive="game1.rom",
        )
        # Add another ROM in same archive (different game)
        game2 = Game.objects.create(name="Test Game 2", system=self.system)
        rom_set2 = ROMSet.objects.create(game=game2, region="USA")
        ROM.objects.create(
            rom_set=rom_set2,
            file_path="/roms/collection.zip!game2.rom",
            file_name="game2.rom",
            file_size=1000,
            archive_path="/roms/collection.zip",
            path_in_archive="game2.rom",
        )
        self.assertFalse(is_standalone_archive(rom_set))

    def test_empty_romset_not_standalone(self):
        """ROMSets with no ROMs should not be standalone."""
        rom_set = ROMSet.objects.create(
            game=self.game, region="USA", source_path="/empty"
        )
        self.assertFalse(is_standalone_archive(rom_set))


class TestRomsetScoring(TestCase):
    """Tests for ROMSet score calculation."""

    @classmethod
    def setUpTestData(cls):
        cls.system = System.objects.create(
            name="Test System",
            slug="test-scoring",
            extensions=[".rom"],
            folder_names=["test"],
        )

    def setUp(self):
        self.game = Game.objects.create(name="Score Test Game", system=self.system)

    def test_usa_standalone_beats_usa_multi_archive(self):
        """USA in standalone archive should score higher than USA in multi-archive."""
        # USA in standalone archive
        rs_standalone = ROMSet.objects.create(
            game=self.game, region="USA", source_path="/standalone"
        )
        ROM.objects.create(
            rom_set=rs_standalone,
            file_path="/roms/game.zip!game.rom",
            file_name="game.rom",
            file_size=1000,
            archive_path="/roms/game.zip",
        )

        # USA in multi-ROM archive
        rs_multi = ROMSet.objects.create(
            game=self.game, region="USA", source_path="/multi"
        )
        ROM.objects.create(
            rom_set=rs_multi,
            file_path="/roms/collection.zip!game.rom",
            file_name="game.rom",
            file_size=1000,
            archive_path="/roms/collection.zip",
        )
        # Add another ROM to same archive (makes it multi-game)
        game2 = Game.objects.create(name="Other Game", system=self.system)
        rs2 = ROMSet.objects.create(game=game2, region="USA")
        ROM.objects.create(
            rom_set=rs2,
            file_path="/roms/collection.zip!other.rom",
            file_name="other.rom",
            file_size=1000,
            archive_path="/roms/collection.zip",
        )

        score_standalone = calculate_romset_score(rs_standalone)
        score_multi = calculate_romset_score(rs_multi)

        # USA (1000) + standalone bonus (100) = 1100
        # USA (1000) = 1000
        self.assertEqual(score_standalone, 1000 + STANDALONE_ARCHIVE_BONUS)
        self.assertEqual(score_multi, 1000)
        self.assertGreater(score_standalone, score_multi)

    def test_usa_multi_archive_beats_europe_standalone(self):
        """Region priority should outweigh standalone bonus."""
        # USA in multi-ROM archive
        rs_usa = ROMSet.objects.create(
            game=self.game, region="USA", source_path="/usa-multi"
        )
        ROM.objects.create(
            rom_set=rs_usa,
            file_path="/roms/pack.zip!game.rom",
            file_name="game.rom",
            file_size=1000,
            archive_path="/roms/pack.zip",
        )
        game2 = Game.objects.create(name="Other", system=self.system)
        rs2 = ROMSet.objects.create(game=game2, region="USA")
        ROM.objects.create(
            rom_set=rs2,
            file_path="/roms/pack.zip!other.rom",
            file_name="other.rom",
            file_size=1000,
            archive_path="/roms/pack.zip",
        )

        # Europe standalone
        rs_eu = ROMSet.objects.create(
            game=self.game, region="Europe", source_path="/eu-standalone"
        )
        ROM.objects.create(
            rom_set=rs_eu,
            file_path="/roms/game-eu.zip!game.rom",
            file_name="game.rom",
            file_size=1000,
            archive_path="/roms/game-eu.zip",
        )

        score_usa = calculate_romset_score(rs_usa)
        score_eu = calculate_romset_score(rs_eu)

        # USA (1000) > Europe (800) + standalone (100) = 900
        self.assertEqual(score_usa, 1000)
        self.assertEqual(score_eu, 800 + STANDALONE_ARCHIVE_BONUS)
        self.assertGreater(score_usa, score_eu)


class TestBestRomsetSelection(TestCase):
    """Tests for get_best_romset function."""

    @classmethod
    def setUpTestData(cls):
        cls.system = System.objects.create(
            name="Test System",
            slug="test-best",
            extensions=[".rom"],
            folder_names=["test"],
        )

    def setUp(self):
        self.game = Game.objects.create(name="Best Test Game", system=self.system)

    def test_selects_highest_scoring_romset(self):
        """Should select the highest scoring available ROMSet."""
        # Europe ROM (lower priority)
        rs_eu = ROMSet.objects.create(
            game=self.game, region="Europe", source_path="/eu"
        )
        ROM.objects.create(
            rom_set=rs_eu,
            file_path="/roms/eu.rom",
            file_name="eu.rom",
            file_size=1000,
        )

        # USA ROM (higher priority)
        rs_usa = ROMSet.objects.create(game=self.game, region="USA", source_path="/usa")
        ROM.objects.create(
            rom_set=rs_usa,
            file_path="/roms/usa.rom",
            file_name="usa.rom",
            file_size=1000,
        )

        best = get_best_romset(self.game)
        self.assertEqual(best, rs_usa)

    def test_skips_empty_romsets(self):
        """Should skip ROMSets with no ROMs."""
        # USA ROMSet but empty (no ROMs)
        ROMSet.objects.create(game=self.game, region="USA", source_path="/usa")

        # Japan ROM available
        rs_jp = ROMSet.objects.create(game=self.game, region="Japan", source_path="/jp")
        ROM.objects.create(
            rom_set=rs_jp,
            file_path="/roms/jp.rom",
            file_name="jp.rom",
            file_size=1000,
        )

        best = get_best_romset(self.game)
        self.assertEqual(best, rs_jp)

    def test_returns_none_for_game_without_romsets(self):
        """Should return None for game with no ROMSets."""
        best = get_best_romset(self.game)
        self.assertIsNone(best)


class TestRecalculateDefault(TestCase):
    """Tests for recalculate_default_romset function."""

    @classmethod
    def setUpTestData(cls):
        cls.system = System.objects.create(
            name="Test System",
            slug="test-recalc",
            extensions=[".rom"],
            folder_names=["test"],
        )

    def setUp(self):
        self.game = Game.objects.create(name="Recalc Test Game", system=self.system)

    def test_sets_best_romset_as_default(self):
        """Should set the best scoring ROMSet as default."""
        rs_eu = ROMSet.objects.create(
            game=self.game, region="Europe", source_path="/eu"
        )
        ROM.objects.create(
            rom_set=rs_eu,
            file_path="/roms/eu.rom",
            file_name="eu.rom",
            file_size=1000,
        )

        rs_usa = ROMSet.objects.create(game=self.game, region="USA", source_path="/usa")
        ROM.objects.create(
            rom_set=rs_usa,
            file_path="/roms/usa.rom",
            file_name="usa.rom",
            file_size=1000,
        )

        # Initially set to Europe
        self.game.default_rom_set = rs_eu
        self.game.save()

        changed = recalculate_default_romset(self.game)

        self.assertTrue(changed)
        self.game.refresh_from_db()
        self.assertEqual(self.game.default_rom_set, rs_usa)

    def test_returns_false_when_no_change(self):
        """Should return False when default doesn't change."""
        rs_usa = ROMSet.objects.create(game=self.game, region="USA", source_path="/usa")
        ROM.objects.create(
            rom_set=rs_usa,
            file_path="/roms/usa.rom",
            file_name="usa.rom",
            file_size=1000,
        )

        self.game.default_rom_set = rs_usa
        self.game.save()

        changed = recalculate_default_romset(self.game)
        self.assertFalse(changed)

    def test_sets_default_when_none_exists(self):
        """Should set default when game has ROMs but no default_rom_set."""
        rs_usa = ROMSet.objects.create(game=self.game, region="USA", source_path="/usa")
        ROM.objects.create(
            rom_set=rs_usa,
            file_path="/roms/usa.rom",
            file_name="usa.rom",
            file_size=1000,
        )

        # Game has ROMs but no default set
        self.assertIsNone(self.game.default_rom_set)

        changed = recalculate_default_romset(self.game)

        self.assertTrue(changed)
        self.game.refresh_from_db()
        self.assertEqual(self.game.default_rom_set, rs_usa)
