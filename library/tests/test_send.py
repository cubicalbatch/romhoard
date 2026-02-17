"""Tests for FTP/SFTP send functionality."""

from unittest.mock import MagicMock, patch

import pytest


class TestFTPClientTestWrite:
    """Test FTPClient.test_write() creates parent directories."""

    def test_ftp_test_write_creates_parent_directories(self):
        """Bug #1: FTP test_write should create parent directories before writing.

        When the test path includes subdirectories (e.g., '/Roms/.romhoard_test'),
        FTP should create the parent directory first, just like SFTP does.
        """
        from library.send import FTPClient

        # Create FTP client
        client = FTPClient(
            host="test.example.com",
            port=21,
            user="user",
            password="pass",
            use_tls=False,
        )

        # Mock the FTP connection
        mock_ftp = MagicMock()
        client.ftp = mock_ftp

        # Simulate directories not existing: cwd raises error, mkd succeeds
        from ftplib import error_perm

        mock_ftp.cwd.side_effect = error_perm("Directory does not exist")

        # Test path with subdirectory
        test_path = "/Roms/subfolder/.romhoard_test"

        # Call test_write
        client.test_write(test_path)

        # Verify ensure_directory was called for parent path
        # After cwd fails, mkd should be called to create directories
        calls = mock_ftp.mkd.call_args_list
        assert len(calls) >= 1, "ensure_directory should call mkd for parent dirs"

        # Check that storbinary was called
        assert mock_ftp.storbinary.called

    def test_ftp_test_write_with_root_path(self):
        """FTP test_write should work with paths at root level."""
        from library.send import FTPClient

        client = FTPClient(
            host="test.example.com",
            port=21,
            user="user",
            password="pass",
            use_tls=False,
        )

        mock_ftp = MagicMock()
        client.ftp = mock_ftp

        # Test path at root (no parent directory)
        test_path = ".romhoard_test"

        # Call test_write
        client.test_write(test_path)

        # Should NOT call mkd for root-level file
        assert not mock_ftp.mkd.called

        # Should still call storbinary
        assert mock_ftp.storbinary.called


class TestSFTPClientTestWrite:
    """Test SFTPClient.test_write() for reference (already works)."""

    def test_sftp_test_write_creates_parent_directories(self):
        """SFTP test_write already creates parent directories - verify it still works."""
        from library.send import SFTPClient

        client = SFTPClient(
            host="test.example.com",
            port=22,
            user="user",
            password="pass",
        )

        # Mock the SFTP connection
        mock_sftp = MagicMock()
        client.sftp = mock_sftp

        # Test path with subdirectory
        test_path = "/Roms/subfolder/.romhoard_test"

        # Call test_write
        client.test_write(test_path)

        # Verify mkdir was called for parent directories
        calls = mock_sftp.mkdir.call_args_list
        assert len(calls) >= 1, "ensure_directory should create parent dirs"

        # Check that open was called for writing
        assert mock_sftp.open.called

    def test_sftp_ensure_directory_absolute_path(self):
        """Verify that ensure_directory handles absolute paths correctly."""
        from library.send import SFTPClient

        client = SFTPClient(
            host="test.example.com",
            port=22,
            user="user",
            password="pass",
        )

        mock_sftp = MagicMock()
        client.sftp = mock_sftp

        # Test absolute path
        test_path = "/mnt/SDCARD/Roms/5200"

        # Call ensure_directory
        client.ensure_directory(test_path)

        # Verify mkdir was called with absolute paths
        calls = [call.args[0] for call in mock_sftp.mkdir.call_args_list]
        assert "/mnt" in calls
        assert "/mnt/SDCARD" in calls
        assert "/mnt/SDCARD/Roms" in calls
        assert "/mnt/SDCARD/Roms/5200" in calls
        # Ensure no relative paths were tried
        assert "mnt" not in calls


class TestSendGamesToDevice:
    """Test send_games_to_device ROM selection."""

    @pytest.fixture
    def mock_device(self, db):
        """Create a mock device for testing."""
        from devices.models import Device

        device = Device.objects.create(
            name="Test Device",
            slug="test-device",
            transfer_type=Device.TRANSFER_FTP,
            transfer_host="test.example.com",
            transfer_port=21,
            transfer_user="user",
        )
        device.transfer_password = "pass"
        device.save()
        return device

    @pytest.fixture
    def game_with_multiple_romsets(self, db):
        """Create a game with multiple ROMSets to test default selection."""
        from library.models import Game, ROM, ROMSet, System

        system, _ = System.objects.get_or_create(
            slug="gba",
            defaults={
                "name": "Game Boy Advance",
                "extensions": [".gba"],
                "folder_names": ["GBA"],
            },
        )
        game = Game.objects.create(name="Multi-ROMSet Game", system=system)

        # Create USA ROMSet (should be default based on region priority)
        romset_usa = ROMSet.objects.create(game=game, region="USA")
        ROM.objects.create(
            rom_set=romset_usa,
            file_path="/test/game_usa.gba",
            file_name="game_usa.gba",
            file_size=1024,
        )

        # Create Japan ROMSet (should NOT be sent)
        romset_japan = ROMSet.objects.create(game=game, region="Japan")
        ROM.objects.create(
            rom_set=romset_japan,
            file_path="/test/game_japan.gba",
            file_name="game_japan.gba",
            file_size=1024,
        )

        # Create Europe ROMSet (should NOT be sent)
        romset_europe = ROMSet.objects.create(game=game, region="Europe")
        ROM.objects.create(
            rom_set=romset_europe,
            file_path="/test/game_europe.gba",
            file_name="game_europe.gba",
            file_size=1024,
        )

        return game

    def test_send_games_uses_default_romset_only(
        self, mock_device, game_with_multiple_romsets
    ):
        """Bug #2: Send should only send the default ROMSet, not all ROMs.

        When sending by game (not specific ROM), the send function should
        use get_default_romset() to select only the best ROMSet's ROMs,
        just like the download feature does.
        """
        from library.send import send_games_to_device

        game = game_with_multiple_romsets

        # Mock the transfer client to avoid actual FTP connection
        with patch("library.send.create_transfer_client") as mock_create_client:
            mock_client = MagicMock()
            mock_client.connect.return_value = (True, "")
            mock_client.test_write.return_value = (True, "")
            mock_client.get_remote_size.return_value = None
            mock_create_client.return_value = mock_client

            # Mock get_rom_file to avoid file access
            with patch("library.send.get_rom_file") as mock_get_rom:
                # Make get_rom_file raise to see which ROMs it tries to send
                roms_attempted = []

                def track_rom_access(rom):
                    roms_attempted.append(rom.file_name)
                    raise FileNotFoundError("Test - tracking only")

                mock_get_rom.side_effect = track_rom_access

                # Send the game (not specific ROM)
                try:
                    send_games_to_device(
                        games=[game],
                        device=mock_device,
                    )
                except Exception:
                    pass  # Expected due to mocking

                # Bug: Before fix, all 3 ROMs would be attempted
                # After fix: Only 1 ROM (from default ROMSet) should be attempted
                assert len(roms_attempted) == 1, (
                    f"Expected 1 ROM (default ROMSet only), "
                    f"but got {len(roms_attempted)}: {roms_attempted}"
                )

    def test_send_specific_roms_sends_all_provided(
        self, mock_device, game_with_multiple_romsets
    ):
        """When specific ROMs are provided, all should be sent."""
        from library.models import ROM
        from library.send import send_games_to_device

        game = game_with_multiple_romsets
        all_roms = list(ROM.objects.filter(rom_set__game=game))

        with patch("library.send.create_transfer_client") as mock_create_client:
            mock_client = MagicMock()
            mock_client.connect.return_value = (True, "")
            mock_client.test_write.return_value = (True, "")
            mock_client.get_remote_size.return_value = None
            mock_create_client.return_value = mock_client

            with patch("library.send.get_rom_file") as mock_get_rom:
                roms_attempted = []

                def track_rom_access(rom):
                    roms_attempted.append(rom.file_name)
                    raise FileNotFoundError("Test - tracking only")

                mock_get_rom.side_effect = track_rom_access

                # Send specific ROMs (all of them)
                try:
                    send_games_to_device(
                        games=[game],
                        device=mock_device,
                        roms=all_roms,  # Explicit list of ROMs
                    )
                except Exception:
                    pass

                # When specific ROMs provided, all should be sent
                assert len(roms_attempted) == 3, (
                    f"Expected all 3 ROMs when explicitly provided, "
                    f"but got {len(roms_attempted)}: {roms_attempted}"
                )
