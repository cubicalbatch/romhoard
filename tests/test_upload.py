"""Tests for upload functionality."""

import os
import pytest
import tempfile
from pathlib import Path
from unittest.mock import MagicMock, patch


class TestDetectSystemFromExtension:
    """Tests for detect_system_from_extension function."""

    @pytest.mark.django_db
    def test_detects_gba_extension(self):
        """Test that .gba files are detected as GBA system."""
        from library.upload import detect_system_from_extension
        from library.models import System

        # Get or create a GBA system with exclusive extension
        system, _ = System.objects.get_or_create(
            slug="gba",
            defaults={
                "name": "Game Boy Advance",
                "exclusive_extensions": [".gba"],
                "extensions": [".gba"],
                "folder_names": ["GBA", "gba"],
            },
        )
        # Ensure it has the exclusive extension
        if ".gba" not in system.exclusive_extensions:
            system.exclusive_extensions = [".gba"]
            system.save()

        result = detect_system_from_extension("Pokemon.gba")
        assert result is not None
        assert result.slug == "gba"

    @pytest.mark.django_db
    def test_returns_none_for_unknown_extension(self):
        """Test that unknown extensions return None."""
        from library.upload import detect_system_from_extension

        result = detect_system_from_extension("file.xyz")
        assert result is None

    @pytest.mark.django_db
    def test_handles_uppercase_extension(self):
        """Test that uppercase extensions are handled correctly."""
        from library.upload import detect_system_from_extension
        from library.models import System

        system, _ = System.objects.get_or_create(
            slug="nes",
            defaults={
                "name": "Nintendo Entertainment System",
                "exclusive_extensions": [".nes"],
                "extensions": [".nes"],
                "folder_names": ["NES", "nes"],
            },
        )
        # Ensure it has the exclusive extension
        if ".nes" not in system.exclusive_extensions:
            system.exclusive_extensions = [".nes"]
            system.save()

        result = detect_system_from_extension("Game.NES")
        assert result is not None
        assert result.slug == "nes"


class TestCheckDuplicate:
    """Tests for check_duplicate function."""

    @pytest.mark.django_db
    def test_detects_existing_rom(self):
        """Test that existing ROMs are detected as duplicates."""
        from library.upload import check_duplicate
        from library.models import System, Game, ROMSet, ROM

        system, _ = System.objects.get_or_create(
            slug="gba",
            defaults={
                "name": "GBA",
                "extensions": [".gba"],
                "folder_names": ["GBA"],
            },
        )
        game = Game.objects.create(name="Test Game Dup Check", system=system)
        romset = ROMSet.objects.create(game=game, region="USA")
        ROM.objects.create(
            rom_set=romset,
            file_path="/test/path_dup.gba",
            file_name="test_dup_check.gba",
            file_size=1000,
        )

        assert check_duplicate("test_dup_check.gba", system) is True

    @pytest.mark.django_db
    def test_no_duplicate_for_new_file(self):
        """Test that new files are not detected as duplicates."""
        from library.upload import check_duplicate
        from library.models import System

        system, _ = System.objects.get_or_create(
            slug="gba",
            defaults={
                "name": "GBA",
                "extensions": [".gba"],
                "folder_names": ["GBA"],
            },
        )
        assert check_duplicate("new_game_unique_12345.gba", system) is False


class TestGetLibraryRoot:
    """Tests for get_library_root function."""

    @pytest.mark.django_db
    def test_returns_empty_when_not_configured(self):
        """Test that empty string is returned when library_root is not set."""
        from library.upload import get_library_root

        result = get_library_root()
        assert result == ""

    @pytest.mark.django_db
    def test_returns_configured_path(self):
        """Test that configured path is returned."""
        from library.upload import get_library_root
        from library.models import Setting

        Setting.objects.create(key="library_root", value="/path/to/library")

        result = get_library_root()
        assert result == "/path/to/library"


class TestBuildExtensionMapForFrontend:
    """Tests for build_extension_map_for_frontend function."""

    @pytest.mark.django_db
    def test_returns_extension_to_slug_map(self):
        """Test that extension map contains correct mappings."""
        from library.upload import build_extension_map_for_frontend
        from library.models import System

        gba, _ = System.objects.get_or_create(
            slug="gba",
            defaults={
                "name": "Game Boy Advance",
                "exclusive_extensions": [".gba"],
                "extensions": [".gba"],
                "folder_names": ["GBA", "gba"],
            },
        )
        # Ensure it has the exclusive extension
        if ".gba" not in gba.exclusive_extensions:
            gba.exclusive_extensions = [".gba"]
            gba.save()

        nes, _ = System.objects.get_or_create(
            slug="nes",
            defaults={
                "name": "Nintendo Entertainment System",
                "exclusive_extensions": [".nes"],
                "extensions": [".nes"],
                "folder_names": ["NES", "nes"],
            },
        )
        # Ensure it has the exclusive extension
        if ".nes" not in nes.exclusive_extensions:
            nes.exclusive_extensions = [".nes"]
            nes.save()

        result = build_extension_map_for_frontend()

        assert result.get(".gba") == "gba"
        assert result.get(".nes") == "nes"


class TestGetUniquePath:
    """Tests for get_unique_filepath function."""

    def test_returns_original_if_not_exists(self, tmp_path):
        """Test that original path is returned if file doesn't exist."""
        from library.upload import get_unique_filepath

        result = get_unique_filepath(str(tmp_path), "test.gba")
        assert result == str(tmp_path / "test.gba")

    def test_appends_counter_if_exists(self, tmp_path):
        """Test that counter is appended if file exists."""
        from library.upload import get_unique_filepath

        # Create existing file
        (tmp_path / "test.gba").touch()

        result = get_unique_filepath(str(tmp_path), "test.gba")
        assert result == str(tmp_path / "test_1.gba")

    def test_increments_counter_for_multiple_duplicates(self, tmp_path):
        """Test that counter increments for multiple duplicates."""
        from library.upload import get_unique_filepath

        # Create multiple existing files
        (tmp_path / "test.gba").touch()
        (tmp_path / "test_1.gba").touch()
        (tmp_path / "test_2.gba").touch()

        result = get_unique_filepath(str(tmp_path), "test.gba")
        assert result == str(tmp_path / "test_3.gba")


class TestUploadJob:
    """Tests for UploadJob model."""

    @pytest.mark.django_db
    def test_progress_percent_uploading_phase(self):
        """Test progress calculation during uploading phase."""
        from library.models import UploadJob

        job = UploadJob.objects.create(
            files_total=10,
            files_uploaded=5,
            status=UploadJob.STATUS_UPLOADING,
        )

        assert job.progress_percent == 50

    @pytest.mark.django_db
    def test_progress_percent_processing_phase(self):
        """Test progress calculation during processing phase."""
        from library.models import UploadJob

        job = UploadJob.objects.create(
            files_total=10,
            games_added=3,
            games_skipped=2,
            games_failed=1,
            status=UploadJob.STATUS_PROCESSING,
        )

        # 6 processed out of 10
        assert job.progress_percent == 60

    @pytest.mark.django_db
    def test_progress_percent_completed(self):
        """Test progress is 100% when completed."""
        from library.models import UploadJob

        job = UploadJob.objects.create(
            files_total=10,
            status=UploadJob.STATUS_COMPLETED,
        )

        assert job.progress_percent == 100

    @pytest.mark.django_db
    def test_files_processed_property(self):
        """Test files_processed calculation."""
        from library.models import UploadJob

        job = UploadJob.objects.create(
            files_total=10,
            games_added=3,
            games_skipped=2,
            games_failed=1,
        )

        assert job.files_processed == 6


class TestDetectSystemsFromArchive:
    """Tests for detect_systems_from_archive function."""

    @pytest.mark.django_db
    def test_detects_gba_from_archive_extension(self, tmp_path):
        """Test that .gba files inside archives are detected."""
        import zipfile
        from library.upload import detect_systems_from_archive
        from library.models import System

        # Create a GBA system with exclusive extension
        system, _ = System.objects.get_or_create(
            slug="gba",
            defaults={
                "name": "Game Boy Advance",
                "exclusive_extensions": [".gba"],
                "extensions": [".gba"],
                "folder_names": ["GBA", "gba"],
            },
        )
        if ".gba" not in system.exclusive_extensions:
            system.exclusive_extensions = [".gba"]
            system.save()

        # Create a test zip with a .gba file
        archive_path = tmp_path / "test.zip"
        with zipfile.ZipFile(archive_path, "w") as zf:
            zf.writestr("Pokemon.gba", b"fake rom data")

        result = detect_systems_from_archive(str(archive_path))

        assert len(result) == 1
        path_in_archive, detected_system, crc32 = result[0]
        assert path_in_archive == "Pokemon.gba"
        assert detected_system.slug == "gba"
        assert crc32  # Should have a CRC32 value

    @pytest.mark.django_db
    def test_skips_non_rom_files(self, tmp_path):
        """Test that non-ROM files are skipped."""
        import zipfile
        from library.upload import detect_systems_from_archive
        from library.models import System

        # Create a system
        System.objects.get_or_create(
            slug="gba",
            defaults={
                "name": "Game Boy Advance",
                "exclusive_extensions": [".gba"],
                "extensions": [".gba"],
                "folder_names": ["GBA"],
            },
        )

        # Create a zip with non-ROM files only
        archive_path = tmp_path / "readme.zip"
        with zipfile.ZipFile(archive_path, "w") as zf:
            zf.writestr("readme.txt", b"some text")
            zf.writestr("image.png", b"fake png data")

        result = detect_systems_from_archive(str(archive_path))

        assert len(result) == 0

    @pytest.mark.django_db
    def test_detects_multiple_systems(self, tmp_path):
        """Test that files for multiple systems are detected."""
        import zipfile
        from library.upload import detect_systems_from_archive
        from library.models import System

        # Create multiple systems
        gba, _ = System.objects.get_or_create(
            slug="gba",
            defaults={
                "name": "Game Boy Advance",
                "exclusive_extensions": [".gba"],
                "extensions": [".gba"],
                "folder_names": ["GBA"],
            },
        )
        if ".gba" not in gba.exclusive_extensions:
            gba.exclusive_extensions = [".gba"]
            gba.save()

        nes, _ = System.objects.get_or_create(
            slug="nes",
            defaults={
                "name": "Nintendo Entertainment System",
                "exclusive_extensions": [".nes"],
                "extensions": [".nes"],
                "folder_names": ["NES"],
            },
        )
        if ".nes" not in nes.exclusive_extensions:
            nes.exclusive_extensions = [".nes"]
            nes.save()

        # Create a zip with files for multiple systems
        archive_path = tmp_path / "mixed.zip"
        with zipfile.ZipFile(archive_path, "w") as zf:
            zf.writestr("Pokemon.gba", b"gba rom data")
            zf.writestr("Mario.nes", b"nes rom data")

        result = detect_systems_from_archive(str(archive_path))

        assert len(result) == 2
        systems = {r[1].slug for r in result}
        assert systems == {"gba", "nes"}

    @pytest.mark.django_db
    def test_returns_empty_for_invalid_archive(self, tmp_path):
        """Test that invalid archives return empty list."""
        from library.upload import detect_systems_from_archive

        # Create a fake "archive" that isn't actually valid
        fake_archive = tmp_path / "not_real.zip"
        fake_archive.write_text("this is not a zip file")

        result = detect_systems_from_archive(str(fake_archive))

        assert result == []


class TestIdentifyRomByHash:
    """Tests for identify_rom_by_hash function."""

    @pytest.mark.django_db
    def test_returns_none_for_unknown_hash(self, tmp_path):
        """Test that unknown hashes return None."""
        from library.upload import identify_rom_by_hash

        # Create a file with random content (won't be in Hasheous)
        test_file = tmp_path / "random.bin"
        test_file.write_bytes(b"random content that is not a known ROM")

        # Mock the Hasheous API to return None
        with patch("library.upload.identify_system_by_hash") as mock_identify:
            mock_identify.return_value = None
            result = identify_rom_by_hash(str(test_file))

        assert result is None

    @pytest.mark.django_db
    def test_handles_file_read_errors(self, tmp_path):
        """Test that file read errors return None."""
        from library.upload import identify_rom_by_hash

        # Try to read a non-existent file
        result = identify_rom_by_hash(str(tmp_path / "nonexistent.bin"))

        assert result is None

    @pytest.mark.django_db
    def test_uses_internal_sha1_for_chd_files(self, tmp_path):
        """Test that CHD files use internal SHA1 instead of file CRC32."""
        from library.upload import identify_rom_by_hash
        from library.models import System

        # Create a PS1 system
        ps1, _ = System.objects.get_or_create(
            slug="ps1",
            defaults={
                "name": "PlayStation",
                "extensions": [".chd", ".bin", ".cue"],
                "folder_names": ["PS1", "PlayStation"],
            },
        )

        # Create a fake CHD file
        test_file = tmp_path / "game.chd"
        test_file.write_bytes(b"fake chd content")

        # Mock CHD detection and SHA1 extraction (patch at source since imported inside function)
        with (
            patch("library.chd.is_chd_file") as mock_is_chd,
            patch("library.chd.extract_chd_sha1") as mock_extract,
            patch("library.upload.identify_system_by_hash") as mock_identify,
        ):
            mock_is_chd.return_value = True
            mock_extract.return_value = "7cfabe8b3589bc037eb7daf5e4bce27f15eb2d4c"
            mock_identify.return_value = ps1

            result = identify_rom_by_hash(str(test_file))

            # Verify SHA1 was used (not CRC32)
            mock_extract.assert_called_once_with(str(test_file))
            mock_identify.assert_called_once_with(
                sha1="7cfabe8b3589bc037eb7daf5e4bce27f15eb2d4c"
            )
            assert result == ps1

    @pytest.mark.django_db
    def test_returns_none_when_chd_sha1_extraction_fails(self, tmp_path):
        """Test that CHD files return None if SHA1 extraction fails."""
        from library.upload import identify_rom_by_hash

        # Create a fake CHD file
        test_file = tmp_path / "game.chd"
        test_file.write_bytes(b"fake chd content")

        # Mock CHD detection to return True but SHA1 extraction to fail
        with (
            patch("library.chd.is_chd_file") as mock_is_chd,
            patch("library.chd.extract_chd_sha1") as mock_extract,
        ):
            mock_is_chd.return_value = True
            mock_extract.return_value = None  # Extraction failed

            result = identify_rom_by_hash(str(test_file))

            assert result is None

    @pytest.mark.django_db
    def test_uses_crc32_for_non_chd_files(self, tmp_path):
        """Test that non-CHD files use CRC32."""
        from library.upload import identify_rom_by_hash
        from library.models import System

        # Create a GBA system
        gba, _ = System.objects.get_or_create(
            slug="gba",
            defaults={
                "name": "Game Boy Advance",
                "extensions": [".gba"],
                "folder_names": ["GBA"],
            },
        )

        # Create a regular ROM file
        test_file = tmp_path / "game.gba"
        test_file.write_bytes(b"fake gba content")

        # Mock the hash lookup
        with (
            patch("library.upload.compute_file_crc32") as mock_crc,
            patch("library.upload.identify_system_by_hash") as mock_identify,
        ):
            mock_crc.return_value = "12345678"
            mock_identify.return_value = gba

            result = identify_rom_by_hash(str(test_file))

            # Verify CRC32 was used
            mock_crc.assert_called_once()
            mock_identify.assert_called_once_with(crc32="12345678")
            assert result == gba


class TestIdentifySystemByHash:
    """Tests for identify_system_by_hash function."""

    @pytest.mark.django_db
    def test_returns_system_on_match(self):
        """Test that matching hash returns correct system."""
        from library.upload import identify_system_by_hash
        from library.models import System

        # Create a system that matches Hasheous platform
        gba, _ = System.objects.get_or_create(
            slug="gba",
            defaults={
                "name": "Game Boy Advance",
                "extensions": [".gba"],
                "folder_names": ["GBA"],
            },
        )

        # Mock the Hasheous API
        with patch(
            "library.lookup.hasheous.HasheousLookupService._api_lookup"
        ) as mock_api:
            mock_api.return_value = {
                "name": "Test Game",
                "platform": {"name": "Nintendo Game Boy Advance"},
            }

            result = identify_system_by_hash(crc32="12345678")

        assert result is not None
        assert result.slug == "gba"

    @pytest.mark.django_db
    def test_returns_none_for_unknown_platform(self):
        """Test that unknown platforms return None."""
        from library.upload import identify_system_by_hash

        # Mock the Hasheous API with unknown platform
        with patch(
            "library.lookup.hasheous.HasheousLookupService._api_lookup"
        ) as mock_api:
            mock_api.return_value = {
                "name": "Test Game",
                "platform": {"name": "Unknown Console XYZ"},
            }

            result = identify_system_by_hash(crc32="12345678")

        assert result is None

    @pytest.mark.django_db
    def test_returns_none_when_no_hash_provided(self):
        """Test that no hash returns None."""
        from library.upload import identify_system_by_hash

        result = identify_system_by_hash()

        assert result is None


class TestCheckDuplicatesEndpoint:
    """Tests for check_duplicates view endpoint."""

    @pytest.mark.django_db
    def test_detects_duplicate_by_name_and_size(self, client):
        """Test that existing ROMs are detected as duplicates with game info."""
        from library.models import System, Game, ROMSet, ROM

        # Create a ROM in the database
        system, _ = System.objects.get_or_create(
            slug="gba",
            defaults={
                "name": "GBA",
                "extensions": [".gba"],
                "folder_names": ["GBA"],
            },
        )
        game = Game.objects.create(name="Test Game", system=system)
        romset = ROMSet.objects.create(game=game, region="USA")
        ROM.objects.create(
            rom_set=romset,
            file_path="/test/existing.gba",
            file_name="existing.gba",
            file_size=1024,
        )

        # Check for duplicates
        response = client.post(
            "/upload/check-duplicates/",
            data=[
                {"name": "existing.gba", "size": 1024},  # Should be duplicate
                {"name": "new_game.gba", "size": 2048},  # Should not be duplicate
            ],
            content_type="application/json",
        )

        assert response.status_code == 200
        data = response.json()
        # Duplicate returns game info with system details
        dup_info = data["duplicates"]["existing.gba"]
        assert dup_info is not None
        assert dup_info["game_id"] == game.pk
        assert dup_info["game_name"] == "Test Game"
        assert dup_info["system_slug"] == "gba"
        assert dup_info["has_icon"] is False
        # Non-duplicate returns None
        assert data["duplicates"]["new_game.gba"] is None

    @pytest.mark.django_db
    def test_same_name_different_size_not_duplicate(self, client):
        """Test that same filename with different size is not a duplicate."""
        from library.models import System, Game, ROMSet, ROM

        system, _ = System.objects.get_or_create(
            slug="gba",
            defaults={
                "name": "GBA",
                "extensions": [".gba"],
                "folder_names": ["GBA"],
            },
        )
        game = Game.objects.create(name="Test Game 2", system=system)
        romset = ROMSet.objects.create(game=game, region="USA")
        ROM.objects.create(
            rom_set=romset,
            file_path="/test/game.gba",
            file_name="game.gba",
            file_size=1024,
        )

        # Check with different size
        response = client.post(
            "/upload/check-duplicates/",
            data=[{"name": "game.gba", "size": 2048}],
            content_type="application/json",
        )

        assert response.status_code == 200
        data = response.json()
        assert data["duplicates"]["game.gba"] is None

    @pytest.mark.django_db
    def test_handles_empty_list(self, client):
        """Test that empty list returns empty duplicates."""
        response = client.post(
            "/upload/check-duplicates/",
            data=[],
            content_type="application/json",
        )

        assert response.status_code == 200
        data = response.json()
        assert data["duplicates"] == {}

    @pytest.mark.django_db
    def test_rejects_invalid_json(self, client):
        """Test that invalid JSON returns error."""
        response = client.post(
            "/upload/check-duplicates/",
            data="not valid json",
            content_type="application/json",
        )

        assert response.status_code == 400
        data = response.json()
        assert "error" in data

    @pytest.mark.django_db
    def test_rejects_non_list_input(self, client):
        """Test that non-list input returns error."""
        response = client.post(
            "/upload/check-duplicates/",
            data={"name": "test.gba", "size": 1024},
            content_type="application/json",
        )

        assert response.status_code == 400
        data = response.json()
        assert data["error"] == "Expected list of files"
