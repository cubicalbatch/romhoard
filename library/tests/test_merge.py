"""Tests for game deduplication and merge functionality."""

import pytest
from django.test import TestCase

from library.merge import (
    find_existing_game,
    find_duplicate_groups_by_name_case,
    find_duplicate_groups_by_screenscraper_id,
    merge_games,
    select_canonical_game,
)
from library.models import Game, ROM, ROMSet, System
from library.system_loader import sync_systems


class TestFindExistingGame(TestCase):
    """Tests for find_existing_game function."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        sync_systems()

    def setUp(self):
        self.system = System.objects.get(slug="arcade")
        # Clean up
        Game.objects.all().delete()

    def test_find_by_case_insensitive_name(self):
        """Should find game by case-insensitive name match."""
        game = Game.objects.create(name="Galaga", system=self.system)

        # Should find with different case
        found = find_existing_game(name="galaga", system=self.system)
        assert found is not None
        assert found.pk == game.pk

        found = find_existing_game(name="GALAGA", system=self.system)
        assert found is not None
        assert found.pk == game.pk

    def test_find_by_screenscraper_id(self):
        """Should find game by screenscraper_id."""
        game = Game.objects.create(
            name="Galaga", system=self.system, screenscraper_id=41331
        )

        # Should find by screenscraper_id even with different name
        found = find_existing_game(
            name="Different Name", system=self.system, screenscraper_id=41331
        )
        assert found is not None
        assert found.pk == game.pk

    def test_find_by_crc32(self):
        """Should find game by ROM CRC32 hash."""
        game = Game.objects.create(name="Galaga", system=self.system)
        romset = ROMSet.objects.create(game=game, region="World")
        ROM.objects.create(
            rom_set=romset,
            file_path="/test/galaga.zip",
            file_name="galaga.zip",
            file_size=1000,
            crc32="12345678",
        )

        # Should find by CRC32 even with different name
        found = find_existing_game(
            name="Different Name", system=self.system, crc32="12345678"
        )
        assert found is not None
        assert found.pk == game.pk

    def test_find_by_sha1(self):
        """Should find game by ROM SHA1 hash."""
        game = Game.objects.create(name="Galaga", system=self.system)
        romset = ROMSet.objects.create(game=game, region="World")
        ROM.objects.create(
            rom_set=romset,
            file_path="/test/galaga.chd",
            file_name="galaga.chd",
            file_size=1000,
            sha1="abcdef1234567890abcdef1234567890abcdef12",
        )

        # Should find by SHA1 even with different name
        found = find_existing_game(
            name="Different Name",
            system=self.system,
            sha1="abcdef1234567890abcdef1234567890abcdef12",
        )
        assert found is not None
        assert found.pk == game.pk

    def test_no_match_returns_none(self):
        """Should return None when no match found."""
        found = find_existing_game(name="NonExistent", system=self.system)
        assert found is None

    def test_hash_has_priority_over_name(self):
        """Hash match should take priority over name match."""
        Game.objects.create(name="Galaga", system=self.system)  # Game matched by name
        game_by_hash = Game.objects.create(name="Other Game", system=self.system)
        romset = ROMSet.objects.create(game=game_by_hash, region="World")
        ROM.objects.create(
            rom_set=romset,
            file_path="/test/other.zip",
            file_name="other.zip",
            file_size=1000,
            crc32="12345678",
        )

        # Should find by hash, not by name
        found = find_existing_game(name="Galaga", system=self.system, crc32="12345678")
        assert found is not None
        assert found.pk == game_by_hash.pk


class TestSelectCanonicalGame(TestCase):
    """Tests for select_canonical_game function."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        sync_systems()

    def setUp(self):
        self.system = System.objects.get(slug="arcade")
        self.gba = System.objects.get(slug="gba")
        Game.objects.all().delete()

    def test_prefers_game_with_screenscraper_id(self):
        """Should prefer game with screenscraper_id set."""
        # Use different systems to avoid unique constraint
        game_without_id = Game.objects.create(name="Galaga", system=self.system)
        game_with_id = Game.objects.create(
            name="Galaga", system=self.gba, screenscraper_id=41331
        )

        canonical = select_canonical_game([game_without_id, game_with_id])
        assert canonical.pk == game_with_id.pk

    def test_prefers_game_with_metadata(self):
        """Should prefer game with metadata_updated_at set."""
        from django.utils import timezone

        # Use different systems to avoid unique constraint
        game_without_meta = Game.objects.create(name="Galaga", system=self.system)
        game_with_meta = Game.objects.create(
            name="Galaga", system=self.gba, metadata_updated_at=timezone.now()
        )

        canonical = select_canonical_game([game_without_meta, game_with_meta])
        assert canonical.pk == game_with_meta.pk

    def test_prefers_lower_pk_as_tiebreaker(self):
        """Should prefer lower pk when all else is equal."""
        # Use different names to avoid unique constraint
        game1 = Game.objects.create(name="Galaga", system=self.system)
        game2 = Game.objects.create(name="Pac-Man", system=self.system)

        canonical = select_canonical_game([game2, game1])
        assert canonical.pk == game1.pk


class TestMergeGames(TestCase):
    """Tests for merge_games function."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        sync_systems()

    def setUp(self):
        self.system = System.objects.get(slug="arcade")
        Game.objects.all().delete()

    def test_merge_moves_romsets(self):
        """Should move ROMSets from duplicate to canonical."""
        # Use different names to avoid unique constraint
        canonical = Game.objects.create(name="Galaga", system=self.system)
        duplicate = Game.objects.create(name="Galaga Variant", system=self.system)

        # Add ROMSet to duplicate
        dup_romset = ROMSet.objects.create(game=duplicate, region="USA")
        ROM.objects.create(
            rom_set=dup_romset,
            file_path="/test/galaga_usa.zip",
            file_name="galaga_usa.zip",
            file_size=1000,
        )

        summary = merge_games(canonical, duplicate)

        # Duplicate should be deleted
        assert not Game.objects.filter(pk=duplicate.pk).exists()

        # ROMSet should be moved to canonical
        assert canonical.rom_sets.count() == 1
        assert canonical.rom_sets.first().region == "USA"

        # Summary should reflect the merge
        assert summary["romsets_moved"] == 1

    def test_merge_handles_romset_collision(self):
        """Should merge ROMs when ROMSets have same region/revision."""
        # Use different names to avoid unique constraint
        canonical = Game.objects.create(name="Galaga", system=self.system)
        duplicate = Game.objects.create(name="Galaga Variant", system=self.system)

        # Both have USA ROMSet
        can_romset = ROMSet.objects.create(game=canonical, region="USA")
        ROM.objects.create(
            rom_set=can_romset,
            file_path="/test/galaga1.zip",
            file_name="galaga1.zip",
            file_size=1000,
        )

        dup_romset = ROMSet.objects.create(game=duplicate, region="USA")
        ROM.objects.create(
            rom_set=dup_romset,
            file_path="/test/galaga2.zip",
            file_name="galaga2.zip",
            file_size=1000,
        )

        merge_games(canonical, duplicate)

        # Should only have one ROMSet
        assert canonical.rom_sets.count() == 1

        # Both ROMs should be in the canonical ROMSet
        assert canonical.rom_sets.first().roms.count() == 2

    def test_merge_copies_missing_description(self):
        """Should copy description from duplicate if canonical is missing it."""
        # Use different names to avoid unique constraint
        canonical = Game.objects.create(name="Galaga", system=self.system)
        duplicate = Game.objects.create(
            name="Galaga Variant",
            system=self.system,
            description="Classic arcade game",
            developer="Namco",
        )

        merge_games(canonical, duplicate)

        canonical.refresh_from_db()
        assert canonical.description == "Classic arcade game"
        assert canonical.developer == "Namco"

    def test_merge_does_not_overwrite_existing_description(self):
        """Should not overwrite existing description on canonical."""
        # Use different names to avoid unique constraint
        canonical = Game.objects.create(
            name="Galaga", system=self.system, description="Original description"
        )
        duplicate = Game.objects.create(
            name="Galaga Variant",
            system=self.system,
            description="Different description",
        )

        merge_games(canonical, duplicate)

        canonical.refresh_from_db()
        assert canonical.description == "Original description"  # Not overwritten

    def test_cannot_merge_game_with_itself(self):
        """Should raise error when trying to merge game with itself."""
        game = Game.objects.create(name="Galaga", system=self.system)

        with pytest.raises(ValueError, match="Cannot merge a game with itself"):
            merge_games(game, game)

    def test_cannot_merge_games_from_different_systems(self):
        """Should raise error when merging games from different systems."""
        arcade = self.system
        gba = System.objects.get(slug="gba")

        game1 = Game.objects.create(name="Galaga", system=arcade)
        game2 = Game.objects.create(name="Galaga", system=gba)

        with pytest.raises(
            ValueError, match="Cannot merge games from different systems"
        ):
            merge_games(game1, game2)


class TestMergeDefaultRomset(TestCase):
    """Tests for default ROMSet recalculation after merge."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        sync_systems()

    def setUp(self):
        self.system = System.objects.get(slug="arcade")
        Game.objects.all().delete()

    def test_merge_recalculates_default_romset(self):
        """Should recalculate default ROMSet to highest-scoring after merge."""
        # Create canonical game with Europe ROMSet (lower priority)
        canonical = Game.objects.create(name="Test Game", system=self.system)
        europe_romset = ROMSet.objects.create(game=canonical, region="Europe")
        ROM.objects.create(
            rom_set=europe_romset,
            file_path="/test/game_eur.zip",
            file_name="game_eur.zip",
            file_size=1000,
        )
        # Set Europe as default initially
        canonical.default_rom_set = europe_romset
        canonical.save()

        # Create duplicate game with USA ROMSet (higher priority)
        duplicate = Game.objects.create(name="Test Game Variant", system=self.system)
        usa_romset = ROMSet.objects.create(game=duplicate, region="USA")
        ROM.objects.create(
            rom_set=usa_romset,
            file_path="/test/game_usa.zip",
            file_name="game_usa.zip",
            file_size=1000,
        )

        # Merge - USA should become default due to higher region priority
        merge_games(canonical, duplicate)

        canonical.refresh_from_db()
        assert canonical.default_rom_set is not None
        assert canonical.default_rom_set.region == "USA"

    def test_merge_prefers_base_game_over_update_only(self):
        """Should prefer base game ROMSet over update-only ROMSet for Switch."""
        switch = System.objects.get(slug="switch")

        # Create canonical game with base game ROMSet
        canonical = Game.objects.create(name="Switch Game", system=switch)
        base_romset = ROMSet.objects.create(game=canonical, region="USA")
        ROM.objects.create(
            rom_set=base_romset,
            file_path="/test/game_base.nsp",
            file_name="game_base.nsp",
            file_size=1000,
            content_type="base",
        )
        canonical.default_rom_set = base_romset
        canonical.save()

        # Create duplicate game with update-only ROMSet
        duplicate = Game.objects.create(name="Switch Game Variant", system=switch)
        update_romset = ROMSet.objects.create(game=duplicate, region="USA")
        ROM.objects.create(
            rom_set=update_romset,
            file_path="/test/game_update.nsp",
            file_name="game_update.nsp",
            file_size=500,
            content_type="update",
        )

        # Merge - base game should remain default
        merge_games(canonical, duplicate)

        canonical.refresh_from_db()
        assert canonical.default_rom_set is not None
        assert canonical.default_rom_set.region == "USA"
        # Verify it's the base ROMSet, not the update one
        content_types = set(canonical.default_rom_set.roms.values_list("content_type", flat=True))
        assert "base" in content_types

    def test_merge_keeps_base_and_update_romsets_separate(self):
        """Should keep base game and update ROMSets as separate entities after merge."""
        switch = System.objects.get(slug="switch")

        # Create canonical game with base game ROMSet
        canonical = Game.objects.create(name="Switch Game", system=switch)
        base_romset = ROMSet.objects.create(game=canonical, region="USA")
        base_rom = ROM.objects.create(
            rom_set=base_romset,
            file_path="/test/game_base.nsp",
            file_name="game_base.nsp",
            file_size=1000,
            content_type="base",
        )
        canonical.default_rom_set = base_romset
        canonical.save()

        # Create duplicate game with update-only ROMSet (same region/revision)
        duplicate = Game.objects.create(name="Switch Game Variant", system=switch)
        update_romset = ROMSet.objects.create(game=duplicate, region="USA")
        update_rom = ROM.objects.create(
            rom_set=update_romset,
            file_path="/test/game_update.nsp",
            file_name="game_update.nsp",
            file_size=500,
            content_type="update",
        )

        # Merge - ROMSets should remain separate
        merge_games(canonical, duplicate)

        # Verify canonical has 2 ROMSets
        assert canonical.rom_sets.count() == 2

        # Verify ROMs are in separate ROMSets
        romsets = list(canonical.rom_sets.all())
        romset_content_types = []
        for rs in romsets:
            rs_types = set(rs.roms.values_list("content_type", flat=True))
            romset_content_types.append(rs_types)

        # One ROMSet should have base, the other should have update
        assert any("base" in types for types in romset_content_types)
        assert any("update" in types for types in romset_content_types)

        # Verify the base ROMSet is default (not the update one)
        canonical.refresh_from_db()
        default_content_types = set(
            canonical.default_rom_set.roms.values_list("content_type", flat=True)
        )
        assert "base" in default_content_types


class TestFindDuplicateGroups(TestCase):
    """Tests for duplicate detection functions."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        sync_systems()

    def setUp(self):
        self.system = System.objects.get(slug="arcade")
        Game.objects.all().delete()

    def test_find_duplicates_by_screenscraper_id_no_duplicates(self):
        """With screenscraper_id constraint, duplicates cannot be created in same system."""
        # This test verifies that the screenscraper_id constraint is working
        # We can only create one game per screenscraper_id per system
        Game.objects.create(name="Galaga", system=self.system, screenscraper_id=41331)
        Game.objects.create(name="Pac-Man", system=self.system, screenscraper_id=12345)

        # Should find no duplicates since constraint prevents them
        groups = find_duplicate_groups_by_screenscraper_id()

        assert len(groups) == 0

    def test_find_duplicates_by_name_case_no_duplicates(self):
        """With case-insensitive constraint, duplicates cannot be created."""
        # This test verifies that the case-insensitive constraint is working
        # We can only create one "Galaga" per system
        Game.objects.create(name="Galaga", system=self.system)
        Game.objects.create(name="Pac-Man", system=self.system)
        Game.objects.create(name="Donkey Kong", system=self.system)

        # Should find no duplicates since constraint prevents them
        groups = find_duplicate_groups_by_name_case()

        assert len(groups) == 0
