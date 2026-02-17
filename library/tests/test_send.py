"""Tests for FTP/SFTP send functionality."""

import threading
import time
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


class TestFTPClientKeepalive:
    """Test FTPClient keepalive methods."""

    def test_is_connected_returns_true_when_noop_succeeds(self):
        """is_connected should return True when NOOP command succeeds."""
        from library.send import FTPClient

        client = FTPClient(
            host="test.example.com",
            port=21,
            user="user",
            password="pass",
        )

        mock_ftp = MagicMock()
        client.ftp = mock_ftp

        assert client.is_connected() is True
        mock_ftp.voidcmd.assert_called_with("NOOP")

    def test_is_connected_returns_false_when_ftp_is_none(self):
        """is_connected should return False when ftp is None."""
        from library.send import FTPClient

        client = FTPClient(
            host="test.example.com",
            port=21,
            user="user",
            password="pass",
        )
        client.ftp = None

        assert client.is_connected() is False

    def test_is_connected_returns_false_when_noop_fails(self):
        """is_connected should return False when NOOP raises exception."""
        from library.send import FTPClient

        client = FTPClient(
            host="test.example.com",
            port=21,
            user="user",
            password="pass",
        )

        mock_ftp = MagicMock()
        mock_ftp.voidcmd.side_effect = Exception("Connection lost")
        client.ftp = mock_ftp

        assert client.is_connected() is False

    def test_send_keepalive_returns_true_on_success(self):
        """send_keepalive should return True when NOOP succeeds."""
        from library.send import FTPClient

        client = FTPClient(
            host="test.example.com",
            port=21,
            user="user",
            password="pass",
        )

        mock_ftp = MagicMock()
        client.ftp = mock_ftp

        assert client.send_keepalive() is True
        mock_ftp.voidcmd.assert_called_with("NOOP")

    def test_send_keepalive_returns_false_on_failure(self):
        """send_keepalive should return False when NOOP fails."""
        from library.send import FTPClient

        client = FTPClient(
            host="test.example.com",
            port=21,
            user="user",
            password="pass",
        )

        mock_ftp = MagicMock()
        mock_ftp.voidcmd.side_effect = Exception("Timeout")
        client.ftp = mock_ftp

        assert client.send_keepalive() is False

    def test_close_sets_ftp_to_none(self):
        """close() should set ftp to None after closing."""
        from library.send import FTPClient

        client = FTPClient(
            host="test.example.com",
            port=21,
            user="user",
            password="pass",
        )

        mock_ftp = MagicMock()
        client.ftp = mock_ftp

        client.close()

        assert client.ftp is None
        mock_ftp.quit.assert_called_once()


class TestSFTPClientKeepalive:
    """Test SFTPClient keepalive methods."""

    def test_is_connected_returns_true_when_stat_succeeds(self):
        """is_connected should return True when stat command succeeds."""
        from library.send import SFTPClient

        client = SFTPClient(
            host="test.example.com",
            port=22,
            user="user",
            password="pass",
        )

        mock_sftp = MagicMock()
        client.sftp = mock_sftp
        client.client = MagicMock()

        assert client.is_connected() is True
        mock_sftp.stat.assert_called_with(".")

    def test_is_connected_returns_false_when_sftp_is_none(self):
        """is_connected should return False when sftp is None."""
        from library.send import SFTPClient

        client = SFTPClient(
            host="test.example.com",
            port=22,
            user="user",
            password="pass",
        )
        client.sftp = None
        client.client = None

        assert client.is_connected() is False

    def test_is_connected_returns_false_when_stat_fails(self):
        """is_connected should return False when stat command fails."""
        from library.send import SFTPClient

        client = SFTPClient(
            host="test.example.com",
            port=22,
            user="user",
            password="pass",
        )

        mock_sftp = MagicMock()
        mock_sftp.stat.side_effect = Exception("Socket closed")
        client.sftp = mock_sftp
        client.client = MagicMock()

        assert client.is_connected() is False

    def test_send_keepalive_returns_true_on_success(self):
        """send_keepalive should return True when stat succeeds."""
        from library.send import SFTPClient

        client = SFTPClient(
            host="test.example.com",
            port=22,
            user="user",
            password="pass",
        )

        mock_sftp = MagicMock()
        client.sftp = mock_sftp

        assert client.send_keepalive() is True
        mock_sftp.stat.assert_called_with(".")

    def test_send_keepalive_returns_false_on_failure(self):
        """send_keepalive should return False when stat fails."""
        from library.send import SFTPClient

        client = SFTPClient(
            host="test.example.com",
            port=22,
            user="user",
            password="pass",
        )

        mock_sftp = MagicMock()
        mock_sftp.stat.side_effect = Exception("Connection closed")
        client.sftp = mock_sftp

        assert client.send_keepalive() is False

    def test_close_sets_sftp_and_client_to_none(self):
        """close() should set sftp and client to None after closing."""
        from library.send import SFTPClient

        client = SFTPClient(
            host="test.example.com",
            port=22,
            user="user",
            password="pass",
        )

        mock_sftp = MagicMock()
        mock_ssh = MagicMock()
        client.sftp = mock_sftp
        client.client = mock_ssh

        client.close()

        assert client.sftp is None
        assert client.client is None
        mock_sftp.close.assert_called_once()
        mock_ssh.close.assert_called_once()


class TestKeepaliveDuring:
    """Test keepalive_during context manager."""

    def test_starts_and_stops_background_thread(self):
        """keepalive_during should start a thread that stops on exit."""
        from library.send import keepalive_during

        mock_client = MagicMock()
        mock_client.send_keepalive.return_value = True

        # Check active threads before
        initial_threads = threading.active_count()

        with keepalive_during(mock_client, interval=0.1):
            # Give thread time to start
            time.sleep(0.05)
            # Thread should be running
            assert threading.active_count() >= initial_threads

        # After exit, thread should have stopped
        time.sleep(0.1)
        assert threading.active_count() == initial_threads

    def test_sends_keepalive_periodically(self):
        """keepalive_during should send keepalive at interval."""
        from library.send import keepalive_during

        mock_client = MagicMock()
        mock_client.send_keepalive.return_value = True

        with keepalive_during(mock_client, interval=0.05):
            time.sleep(0.15)

        # Should have been called multiple times
        assert mock_client.send_keepalive.call_count >= 2

    def test_stops_on_keepalive_failure(self):
        """Thread should stop when keepalive fails."""
        from library.send import keepalive_during

        mock_client = MagicMock()
        mock_client.send_keepalive.return_value = False

        with keepalive_during(mock_client, interval=0.05):
            time.sleep(0.2)

        # Should have been called at least once, but thread stops on failure
        assert mock_client.send_keepalive.call_count >= 1


class TestReconnectOnFailure:
    """Test automatic reconnection when connection is lost."""

    def test_reconnect_calls_close_then_connect(self):
        """reconnect() should close then reconnect."""
        from library.send import FTPClient

        client = FTPClient(
            host="test.example.com",
            port=21,
            user="user",
            password="pass",
        )

        mock_ftp = MagicMock()
        client.ftp = mock_ftp

        # Mock connect to succeed
        with patch.object(client, "connect", return_value=(True, "")) as mock_connect:
            success, error = client.reconnect()

        assert success is True
        assert error == ""
        mock_ftp.quit.assert_called_once()  # close was called
        mock_connect.assert_called_once()  # connect was called

    def test_reconnect_returns_failure_if_connect_fails(self):
        """reconnect() should return failure if connect fails."""
        from library.send import FTPClient

        client = FTPClient(
            host="test.example.com",
            port=21,
            user="user",
            password="pass",
        )

        mock_ftp = MagicMock()
        client.ftp = mock_ftp

        # Mock connect to fail
        with patch.object(
            client, "connect", return_value=(False, "Connection refused")
        ) as mock_connect:
            success, error = client.reconnect()

        assert success is False
        assert error == "Connection refused"
        mock_connect.assert_called_once()

    @pytest.fixture
    def mock_device(self, db):
        """Create a mock device for testing."""
        from devices.models import Device

        device = Device.objects.create(
            name="Test Device",
            slug="test-device-reconnect",
            transfer_type=Device.TRANSFER_FTP,
            transfer_host="test.example.com",
            transfer_port=21,
            transfer_user="user",
        )
        device.transfer_password = "pass"
        device.save()
        return device

    @pytest.fixture
    def game_with_rom(self, db):
        """Create a game with a single ROM."""
        from library.models import Game, ROM, ROMSet, System

        system, _ = System.objects.get_or_create(
            slug="nes",
            defaults={
                "name": "NES",
                "extensions": [".nes"],
                "folder_names": ["NES"],
            },
        )
        game = Game.objects.create(name="Test Game", system=system)
        romset = ROMSet.objects.create(game=game, region="USA")
        ROM.objects.create(
            rom_set=romset,
            file_path="/test/game.nes",
            file_name="game.nes",
            file_size=1024,
        )
        return game

    def test_reconnect_triggered_on_connection_loss(self, mock_device, game_with_rom):
        """Upload retry should reconnect when connection is lost."""
        from library.send import send_games_to_device

        with patch("library.send.create_transfer_client") as mock_create_client:
            mock_client = MagicMock()
            mock_client.connect.return_value = (True, "")
            mock_client.test_write.return_value = (True, "")
            mock_client.get_remote_size.return_value = None  # File doesn't exist

            # First call: connection is lost
            # Second call: reconnected successfully
            mock_client.is_connected.side_effect = [False, True]
            mock_client.reconnect.return_value = (True, "")

            mock_create_client.return_value = mock_client

            with patch("library.send.get_rom_file") as mock_get_rom:
                # Make get_rom_file succeed
                import tempfile

                with tempfile.NamedTemporaryFile(delete=False) as f:
                    f.write(b"ROM data")
                    temp_path = f.name

                try:

                    class FakeContextManager:
                        def __enter__(self):
                            return (temp_path, "game.nes")

                        def __exit__(self, *args):
                            pass

                    mock_get_rom.return_value = FakeContextManager()

                    # Run the send
                    send_games_to_device(
                        games=[game_with_rom],
                        device=mock_device,
                    )

                    # Verify reconnect was called due to is_connected returning False
                    mock_client.reconnect.assert_called_once()
                finally:
                    import os

                    os.unlink(temp_path)
