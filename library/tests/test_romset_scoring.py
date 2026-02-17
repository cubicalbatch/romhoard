"""Tests for ROMSet priority scoring."""

from django.test import TestCase

from library.models import Game, ROM, ROMSet, Setting, System
from library.romset_scoring import (
    ARCHIVE_PENALTY_PER_ROM,
    LOOSE_FILE_BONUS,
    MAX_ARCHIVE_PENALTY,
    SINGLE_ROM_ARCHIVE_BONUS,
    STANDALONE_ARCHIVE_BONUS,
    calculate_romset_score,
    get_all_known_regions,
    get_archive_score,
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

    def test_list_format_priorities(self):
        """List-format preferences should generate correct descending scores."""
        Setting.objects.create(
            key="region_priorities",
            value=["Europe", "USA", "Japan", "World"],
        )
        try:
            priorities = get_region_priorities()
            self.assertEqual(priorities["Europe"], 1000)
            self.assertEqual(priorities["USA"], 900)
            self.assertEqual(priorities["Japan"], 800)
            self.assertEqual(priorities["World"], 700)
        finally:
            Setting.objects.filter(key="region_priorities").delete()

    def test_list_format_europe_first_ranks_correctly(self):
        """Europe-first list preference should rank EUR ROM above USA."""
        Setting.objects.create(
            key="region_priorities",
            value=["Europe", "USA", "Japan"],
        )
        try:
            score_eu = get_region_score("Europe")
            score_usa = get_region_score("USA")
            self.assertGreater(score_eu, score_usa)
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


class TestArchiveScoring(TestCase):
    """Tests for archive-based scoring with tiered penalties."""

    @classmethod
    def setUpTestData(cls):
        cls.system = System.objects.create(
            name="Test System",
            slug="test-archive-scoring",
            extensions=[".rom"],
            folder_names=["test"],
        )
        cls.game = Game.objects.create(name="Archive Test Game", system=cls.system)

    def test_loose_file_beats_single_archive(self):
        """Loose file should score higher than single-ROM archive."""
        # Loose file
        rs_loose = ROMSet.objects.create(
            game=self.game, region="USA", source_path="/loose"
        )
        ROM.objects.create(
            rom_set=rs_loose,
            file_path="/roms/game.rom",
            file_name="game.rom",
            file_size=1000,
            archive_path="",
        )

        # Single-ROM archive
        rs_archive = ROMSet.objects.create(
            game=self.game, region="USA", source_path="/single-archive"
        )
        ROM.objects.create(
            rom_set=rs_archive,
            file_path="/roms/game.zip!game.rom",
            file_name="game.rom",
            file_size=1000,
            archive_path="/roms/game.zip",
        )

        score_loose = get_archive_score(rs_loose)
        score_archive = get_archive_score(rs_archive)

        self.assertEqual(score_loose, LOOSE_FILE_BONUS)  # 150
        self.assertEqual(score_archive, SINGLE_ROM_ARCHIVE_BONUS)  # 100
        self.assertGreater(score_loose, score_archive)

    def test_single_archive_beats_multi_archive(self):
        """Single-ROM archive should score higher than multi-ROM archive."""
        # Single-ROM archive
        rs_single = ROMSet.objects.create(
            game=self.game, region="USA", source_path="/single"
        )
        ROM.objects.create(
            rom_set=rs_single,
            file_path="/roms/single.zip!game.rom",
            file_name="game.rom",
            file_size=1000,
            archive_path="/roms/single.zip",
        )

        # Multi-ROM archive (10 ROMs)
        rs_multi = ROMSet.objects.create(
            game=self.game, region="USA", source_path="/multi10"
        )
        ROM.objects.create(
            rom_set=rs_multi,
            file_path="/roms/multi10.zip!game.rom",
            file_name="game.rom",
            file_size=1000,
            archive_path="/roms/multi10.zip",
        )
        # Add 9 more ROMs to the same archive
        for i in range(9):
            other_game = Game.objects.create(
                name=f"Other Game {i}", system=self.system
            )
            other_rs = ROMSet.objects.create(game=other_game, region="USA")
            ROM.objects.create(
                rom_set=other_rs,
                file_path=f"/roms/multi10.zip!other{i}.rom",
                file_name=f"other{i}.rom",
                file_size=1000,
                archive_path="/roms/multi10.zip",
            )

        score_single = get_archive_score(rs_single)
        score_multi = get_archive_score(rs_multi)

        self.assertEqual(score_single, SINGLE_ROM_ARCHIVE_BONUS)  # 100
        self.assertEqual(score_multi, -(10 * ARCHIVE_PENALTY_PER_ROM))  # -20
        self.assertGreater(score_single, score_multi)

    def test_archive_penalty_scales_with_size(self):
        """Archive penalty should scale with number of ROMs."""
        # Create archives with different sizes
        for size in [5, 20]:
            rs = ROMSet.objects.create(
                game=self.game, region="USA", source_path=f"/size{size}"
            )
            archive_path = f"/roms/archive{size}.zip"
            ROM.objects.create(
                rom_set=rs,
                file_path=f"{archive_path}!game.rom",
                file_name="game.rom",
                file_size=1000,
                archive_path=archive_path,
            )
            # Add other ROMs to make archive the specified size
            for i in range(size - 1):
                other_game = Game.objects.create(
                    name=f"Size{size} Other {i}", system=self.system
                )
                other_rs = ROMSet.objects.create(game=other_game, region="USA")
                ROM.objects.create(
                    rom_set=other_rs,
                    file_path=f"{archive_path}!other{i}.rom",
                    file_name=f"other{i}.rom",
                    file_size=1000,
                    archive_path=archive_path,
                )

        rs5 = ROMSet.objects.get(source_path="/size5")
        rs20 = ROMSet.objects.get(source_path="/size20")

        score5 = get_archive_score(rs5)
        score20 = get_archive_score(rs20)

        # 5 ROMs = -10, 20 ROMs = -40
        self.assertEqual(score5, -(5 * ARCHIVE_PENALTY_PER_ROM))
        self.assertEqual(score20, -(20 * ARCHIVE_PENALTY_PER_ROM))
        self.assertGreater(score5, score20)  # -10 > -40

    def test_archive_penalty_capped(self):
        """Archive penalty should be capped at MAX_ARCHIVE_PENALTY."""
        # Create a large archive (100+ ROMs)
        rs = ROMSet.objects.create(
            game=self.game, region="USA", source_path="/huge"
        )
        archive_path = "/roms/huge.zip"
        ROM.objects.create(
            rom_set=rs,
            file_path=f"{archive_path}!game.rom",
            file_name="game.rom",
            file_size=1000,
            archive_path=archive_path,
        )
        # Add 99 more ROMs (total 100)
        for i in range(99):
            other_game = Game.objects.create(
                name=f"Huge Other {i}", system=self.system
            )
            other_rs = ROMSet.objects.create(game=other_game, region="USA")
            ROM.objects.create(
                rom_set=other_rs,
                file_path=f"{archive_path}!other{i}.rom",
                file_name=f"other{i}.rom",
                file_size=1000,
                archive_path=archive_path,
            )

        score = get_archive_score(rs)

        # 100 * 2 = 200, but capped at 75
        self.assertEqual(score, -MAX_ARCHIVE_PENALTY)  # -75

    def test_multi_rom_romset_uses_worst_score(self):
        """Multi-ROM ROMSet should use the worst (minimum) score."""
        # ROMSet with one loose file and one in a big archive
        rs = ROMSet.objects.create(
            game=self.game, region="USA", source_path="/mixed"
        )

        # First ROM: loose file (score 150)
        ROM.objects.create(
            rom_set=rs,
            file_path="/roms/game-disc1.rom",
            file_name="game-disc1.rom",
            file_size=1000,
            archive_path="",
        )

        # Second ROM: in multi-ROM archive (10 ROMs)
        archive_path = "/roms/mixed-archive.zip"
        ROM.objects.create(
            rom_set=rs,
            file_path=f"{archive_path}!game-disc2.rom",
            file_name="game-disc2.rom",
            file_size=1000,
            archive_path=archive_path,
        )
        # Add 9 more ROMs to the archive
        for i in range(9):
            other_game = Game.objects.create(
                name=f"Mixed Other {i}", system=self.system
            )
            other_rs = ROMSet.objects.create(game=other_game, region="USA")
            ROM.objects.create(
                rom_set=other_rs,
                file_path=f"{archive_path}!other{i}.rom",
                file_name=f"other{i}.rom",
                file_size=1000,
                archive_path=archive_path,
            )

        score = get_archive_score(rs)

        # Should use the worst score: -20 (10-ROM archive) not 150 (loose)
        self.assertEqual(score, -(10 * ARCHIVE_PENALTY_PER_ROM))

    def test_empty_romset_returns_zero(self):
        """Empty ROMSet should return 0."""
        rs = ROMSet.objects.create(
            game=self.game, region="USA", source_path="/empty-archive"
        )
        score = get_archive_score(rs)
        self.assertEqual(score, 0)


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

        # USA (1000) + single-ROM archive bonus (100) = 1100
        # USA (1000) + penalty for 2-ROM archive (-4) = 996
        self.assertEqual(score_standalone, 1000 + SINGLE_ROM_ARCHIVE_BONUS)
        self.assertEqual(score_multi, 1000 - (2 * ARCHIVE_PENALTY_PER_ROM))
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

        # USA (1000) - 2-ROM penalty (4) = 996 > Europe (800) + single archive (100) = 900
        self.assertEqual(score_usa, 1000 - (2 * ARCHIVE_PENALTY_PER_ROM))
        self.assertEqual(score_eu, 800 + SINGLE_ROM_ARCHIVE_BONUS)
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

    def test_europe_first_preference_changes_default(self):
        """With Europe-first preference, Europe ROM should become default."""
        rs_usa = ROMSet.objects.create(game=self.game, region="USA", source_path="/usa")
        ROM.objects.create(
            rom_set=rs_usa,
            file_path="/roms/usa.rom",
            file_name="usa.rom",
            file_size=1000,
        )

        rs_eu = ROMSet.objects.create(
            game=self.game, region="Europe", source_path="/eu"
        )
        ROM.objects.create(
            rom_set=rs_eu,
            file_path="/roms/eu.rom",
            file_name="eu.rom",
            file_size=1000,
        )

        # Set USA as default initially
        self.game.default_rom_set = rs_usa
        self.game.save()

        # Change region preference to Europe-first
        Setting.objects.create(
            key="region_priorities",
            value=["Europe", "USA", "Japan", "World"],
        )
        try:
            changed = recalculate_default_romset(self.game)
            self.assertTrue(changed)
            self.game.refresh_from_db()
            self.assertEqual(self.game.default_rom_set, rs_eu)
        finally:
            Setting.objects.filter(key="region_priorities").delete()


class TestGetAllKnownRegions(TestCase):
    """Tests for get_all_known_regions function."""

    @classmethod
    def setUpTestData(cls):
        cls.system = System.objects.create(
            name="Test System",
            slug="test-regions",
            extensions=[".rom"],
            folder_names=["test"],
        )

    def test_includes_default_regions(self):
        """Should always include the four default regions."""
        regions = get_all_known_regions()
        for r in ["USA", "Europe", "Japan", "World"]:
            self.assertIn(r, regions)

    def test_includes_database_regions(self):
        """Should include unique regions from ROMSets in the database."""
        game = Game.objects.create(name="Test Game", system=self.system)
        ROMSet.objects.create(game=game, region="Brazil")
        regions = get_all_known_regions()
        self.assertIn("Brazil", regions)

    def test_splits_multi_region_strings(self):
        """Should split multi-region strings and include each part."""
        game = Game.objects.create(name="Test Game 2", system=self.system)
        ROMSet.objects.create(game=game, region="Korea, Taiwan")
        regions = get_all_known_regions()
        self.assertIn("Korea", regions)
        self.assertIn("Taiwan", regions)

    def test_no_duplicates(self):
        """Should not include duplicate regions."""
        game = Game.objects.create(name="Test Game 3", system=self.system)
        ROMSet.objects.create(game=game, region="USA")
        regions = get_all_known_regions()
        self.assertEqual(regions.count("USA"), 1)
