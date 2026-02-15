"""Tests for cascade delete when deleting scan paths."""

import tempfile
from pathlib import Path

import pytest
from django.test import Client
from django.urls import reverse


@pytest.fixture
def scan_path_with_roms(db, gba_system):
    """Create a scan path with associated ROMs, ROMSets, Games, and images."""
    from library.models import Game, GameImage, ROM, ROMSet, ScanPath

    # Create a temporary directory for the scan path
    with tempfile.TemporaryDirectory() as tmpdir:
        scan_path = ScanPath.objects.create(path=tmpdir)

        # Create two games with ROMs under this path
        game1 = Game.objects.create(name="Game 1", system=gba_system)
        romset1 = ROMSet.objects.create(game=game1, region="USA")
        ROM.objects.create(
            rom_set=romset1,
            file_path=f"{tmpdir}/game1.gba",
            file_name="game1.gba",
            file_size=1024,
        )

        game2 = Game.objects.create(name="Game 2", system=gba_system)
        romset2 = ROMSet.objects.create(game=game2, region="USA")
        ROM.objects.create(
            rom_set=romset2,
            file_path=f"{tmpdir}/game2.gba",
            file_name="game2.gba",
            file_size=2048,
        )

        # Create an image for game1
        image_dir = Path(tmpdir) / "images"
        image_dir.mkdir()
        image_path = image_dir / "cover.png"
        image_path.write_bytes(b"fake image data")
        GameImage.objects.create(
            game=game1,
            file_path=str(image_path),
            file_name="cover.png",
            image_type="cover",
        )

        yield {
            "scan_path": scan_path,
            "games": [game1, game2],
            "romsets": [romset1, romset2],
            "image_path": image_path,
        }


@pytest.fixture
def scan_path_with_archive_roms(db, gba_system):
    """Create a scan path with ROMs inside archives (archive_path set)."""
    from library.models import Game, ROM, ROMSet, ScanPath

    with tempfile.TemporaryDirectory() as tmpdir:
        scan_path = ScanPath.objects.create(path=tmpdir)

        game = Game.objects.create(name="Archive Game", system=gba_system)
        romset = ROMSet.objects.create(game=game, region="USA")
        # ROM is inside an archive, so archive_path is set instead of file_path
        ROM.objects.create(
            rom_set=romset,
            file_path=f"{tmpdir}/archives/game.zip/rom.gba",
            archive_path=f"{tmpdir}/archives/game.zip",
            file_name="rom.gba",
            file_size=1024,
        )

        yield {
            "scan_path": scan_path,
            "game": game,
            "romset": romset,
        }


@pytest.fixture
def scan_path_with_mixed_games(db, gba_system):
    """Create games where only some ROMs are under the scan path."""
    from library.models import Game, ROM, ROMSet, ScanPath

    with tempfile.TemporaryDirectory() as tmpdir:
        scan_path = ScanPath.objects.create(path=tmpdir)

        # Game with ROMs both inside and outside the scan path
        game = Game.objects.create(name="Mixed Game", system=gba_system)
        romset = ROMSet.objects.create(game=game, region="USA")
        rom_inside = ROM.objects.create(
            rom_set=romset,
            file_path=f"{tmpdir}/game.gba",
            file_name="game.gba",
            file_size=1024,
        )
        rom_outside = ROM.objects.create(
            rom_set=romset,
            file_path="/other/path/game_alt.gba",
            file_name="game_alt.gba",
            file_size=2048,
        )

        yield {
            "scan_path": scan_path,
            "game": game,
            "romset": romset,
            "rom_inside": rom_inside,
            "rom_outside": rom_outside,
        }


class TestScanPathDeleteInfo:
    """Tests for the delete info endpoint that shows counts."""

    def test_shows_correct_counts(self, scan_path_with_roms):
        """Delete info shows correct ROM, ROMSet, Game, and image counts."""
        from library.models import ROM

        data = scan_path_with_roms
        client = Client()
        url = reverse(
            "library:scan_path_delete_info", kwargs={"pk": data["scan_path"].pk}
        )
        response = client.get(url)

        assert response.status_code == 200
        content = response.content.decode()

        # Should show 2 ROMs
        assert "2 ROMs" in content
        # Should show 2 ROMSets
        assert "2 ROMSets" in content
        # Should show 2 Games
        assert "2 Games" in content
        # Should show 1 image
        assert "1 downloaded image" in content

    def test_shows_zero_roms_for_empty_path(self, db):
        """Delete info shows no ROMs for a path with no associated ROMs."""
        from library.models import ScanPath

        scan_path = ScanPath.objects.create(path="/nonexistent/path")
        client = Client()
        url = reverse("library:scan_path_delete_info", kwargs={"pk": scan_path.pk})
        response = client.get(url)

        assert response.status_code == 200
        content = response.content.decode()
        assert "No ROMs are associated" in content

    def test_handles_archive_roms(self, scan_path_with_archive_roms):
        """Delete info counts ROMs that are inside archives under the path."""
        data = scan_path_with_archive_roms
        client = Client()
        url = reverse(
            "library:scan_path_delete_info", kwargs={"pk": data["scan_path"].pk}
        )
        response = client.get(url)

        assert response.status_code == 200
        content = response.content.decode()
        assert "1 ROM" in content


class TestDeleteScanPath:
    """Tests for the cascade delete endpoint."""

    def test_deletes_roms_romsets_games(self, scan_path_with_roms):
        """Deleting a scan path removes all associated ROMs, ROMSets, and Games."""
        from library.models import Game, ROM, ROMSet, ScanPath

        data = scan_path_with_roms
        initial_rom_count = ROM.objects.count()
        initial_romset_count = ROMSet.objects.count()
        initial_game_count = Game.objects.count()

        client = Client()
        url = reverse("library:delete_scan_path", kwargs={"pk": data["scan_path"].pk})
        response = client.post(url)

        assert response.status_code == 200

        # All should be deleted
        assert ROM.objects.count() == initial_rom_count - 2
        assert ROMSet.objects.count() == initial_romset_count - 2
        assert Game.objects.count() == initial_game_count - 2
        assert not ScanPath.objects.filter(pk=data["scan_path"].pk).exists()

    def test_deletes_image_files_from_disk(self, scan_path_with_roms):
        """Deleting a scan path removes downloaded image files from disk."""
        data = scan_path_with_roms
        image_path = data["image_path"]

        # Verify image exists before deletion
        assert image_path.exists()

        client = Client()
        url = reverse("library:delete_scan_path", kwargs={"pk": data["scan_path"].pk})
        client.post(url)

        # Image should be deleted from disk
        assert not image_path.exists()

    def test_preserves_roms_outside_path(self, scan_path_with_mixed_games):
        """Deleting a scan path preserves ROMs that are outside the path."""
        from library.models import Game, ROM, ROMSet

        data = scan_path_with_mixed_games

        client = Client()
        url = reverse("library:delete_scan_path", kwargs={"pk": data["scan_path"].pk})
        client.post(url)

        # ROM inside should be deleted
        assert not ROM.objects.filter(pk=data["rom_inside"].pk).exists()

        # ROM outside should still exist
        assert ROM.objects.filter(pk=data["rom_outside"].pk).exists()

        # ROMSet and Game should still exist (they have remaining ROMs)
        assert ROMSet.objects.filter(pk=data["romset"].pk).exists()
        assert Game.objects.filter(pk=data["game"].pk).exists()

    def test_handles_archive_roms(self, scan_path_with_archive_roms):
        """Deleting a scan path removes ROMs matched by archive_path."""
        from library.models import Game, ROM, ROMSet

        data = scan_path_with_archive_roms

        client = Client()
        url = reverse("library:delete_scan_path", kwargs={"pk": data["scan_path"].pk})
        client.post(url)

        # Everything should be deleted
        assert not ROM.objects.filter(rom_set=data["romset"]).exists()
        assert not ROMSet.objects.filter(pk=data["romset"].pk).exists()
        assert not Game.objects.filter(pk=data["game"].pk).exists()

    def test_empty_path_just_deletes_scan_path(self, db):
        """Deleting a scan path with no ROMs just removes the path record."""
        from library.models import ScanPath

        scan_path = ScanPath.objects.create(path="/empty/path")

        client = Client()
        url = reverse("library:delete_scan_path", kwargs={"pk": scan_path.pk})
        response = client.post(url)

        assert response.status_code == 200
        assert not ScanPath.objects.filter(pk=scan_path.pk).exists()


class TestOrphanCounting:
    """Tests for the orphan counting helper functions."""

    def test_counts_orphaned_romsets(self, db, gba_system):
        """_count_orphans_for_deletion correctly counts orphaned ROMSets."""
        from library.models import Game, ROM, ROMSet, ScanPath
        from library.views.scan import _count_orphans_for_deletion, _get_roms_under_path

        with tempfile.TemporaryDirectory() as tmpdir:
            scan_path = ScanPath.objects.create(path=tmpdir)

            # Create a game with two ROMSets, one will be orphaned
            game = Game.objects.create(name="Test Game", system=gba_system)
            romset1 = ROMSet.objects.create(game=game, region="USA")
            romset2 = ROMSet.objects.create(game=game, region="EUR")

            # ROMSet 1 has ROMs under the scan path
            ROM.objects.create(
                rom_set=romset1,
                file_path=f"{tmpdir}/game_usa.gba",
                file_name="game_usa.gba",
                file_size=1024,
            )

            # ROMSet 2 has ROMs outside the scan path
            ROM.objects.create(
                rom_set=romset2,
                file_path="/other/path/game_eur.gba",
                file_name="game_eur.gba",
                file_size=1024,
            )

            roms = _get_roms_under_path(tmpdir)
            orphan_romsets, orphan_games, _ = _count_orphans_for_deletion(roms)

            # Only romset1 should be orphaned
            assert orphan_romsets == 1
            # Game should not be orphaned (romset2 remains)
            assert orphan_games == 0

    def test_counts_orphaned_games(self, db, gba_system):
        """_count_orphans_for_deletion correctly counts orphaned Games."""
        from library.models import Game, ROM, ROMSet, ScanPath
        from library.views.scan import _count_orphans_for_deletion, _get_roms_under_path

        with tempfile.TemporaryDirectory() as tmpdir:
            scan_path = ScanPath.objects.create(path=tmpdir)

            # Create a game where all ROMs are under the scan path
            game = Game.objects.create(name="Orphan Game", system=gba_system)
            romset = ROMSet.objects.create(game=game, region="USA")
            ROM.objects.create(
                rom_set=romset,
                file_path=f"{tmpdir}/game.gba",
                file_name="game.gba",
                file_size=1024,
            )

            roms = _get_roms_under_path(tmpdir)
            orphan_romsets, orphan_games, orphan_game_pks = _count_orphans_for_deletion(
                roms
            )

            assert orphan_romsets == 1
            assert orphan_games == 1
            assert game.pk in orphan_game_pks
