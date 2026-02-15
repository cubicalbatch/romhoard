"""FTP/SFTP upload functionality for sending ROMs to devices."""

import ftplib
import logging
import os
from dataclasses import dataclass
from io import BytesIO
from typing import Callable, Optional

import paramiko

from devices.models import Device
from library.download import get_rom_file
from library.models import Game, ROM

logger = logging.getLogger(__name__)


@dataclass
class SendProgress:
    """Progress tracking for send operations."""

    files_total: int
    files_uploaded: int = 0
    files_skipped: int = 0
    files_failed: int = 0
    current_file: str = ""
    bytes_uploaded: int = 0
    bytes_total: int = 0
    # Image tracking
    images_uploaded: int = 0
    images_skipped: int = 0
    images_failed: int = 0


@dataclass
class FileResult:
    """Result of a single file upload."""

    game_id: int
    filename: str
    remote_path: str
    success: bool
    skipped: bool = False
    error: str = ""
    bytes: int = 0


@dataclass
class ImageResult:
    """Result of a single image upload."""

    game_id: int
    rom_filename: str
    remote_path: str
    success: bool
    skipped: bool = False
    error: str = ""
    bytes: int = 0


class TransferClient:
    """Abstract base for FTP/SFTP clients."""

    def connect(self) -> tuple[bool, str]:
        """Connect and authenticate. Returns (success, error_msg)."""
        raise NotImplementedError

    def test_write(self, test_path: str) -> tuple[bool, str]:
        """Test write permissions. Returns (success, error_msg)."""
        raise NotImplementedError

    def get_remote_size(self, remote_path: str) -> Optional[int]:
        """Get remote file size, or None if doesn't exist."""
        raise NotImplementedError

    def ensure_directory(self, remote_path: str) -> None:
        """Create remote directory tree if needed."""
        raise NotImplementedError

    def upload_file(
        self,
        local_path: str,
        remote_path: str,
        progress_callback: Optional[Callable[[int, int], None]] = None,
    ) -> None:
        """Upload file to remote path."""
        raise NotImplementedError

    def upload_data(self, data: BytesIO, remote_path: str) -> None:
        """Upload data from a BytesIO object to remote path."""
        raise NotImplementedError

    def close(self) -> None:
        """Close connection."""
        raise NotImplementedError


class FTPClient(TransferClient):
    """FTP/FTPS client implementation using ftplib."""

    def __init__(
        self, host: str, port: int, user: str, password: str, use_tls: bool = False
    ):
        self.host = host
        self.port = port
        self.user = user
        self.password = password
        self.use_tls = use_tls
        self.ftp: Optional[ftplib.FTP] = None

    def connect(self) -> tuple[bool, str]:
        """Connect and authenticate."""
        protocol = "FTPS" if self.use_tls else "FTP"
        user_desc = "anonymous" if not self.user else self.user
        logger.debug(
            f"{protocol}: Connecting to {self.host}:{self.port} as {user_desc}"
        )
        try:
            if self.use_tls:
                self.ftp = ftplib.FTP_TLS()
            else:
                self.ftp = ftplib.FTP()
            self.ftp.connect(self.host, self.port, timeout=30)
            # Anonymous login when user is empty
            if self.user:
                self.ftp.login(self.user, self.password)
            else:
                self.ftp.login()  # ftplib defaults to anonymous
            if self.use_tls:
                self.ftp.prot_p()  # Enable data channel encryption
            logger.debug(
                f"{protocol}: Connected successfully to {self.host}:{self.port}"
            )
            return True, ""
        except Exception as e:
            logger.debug(
                f"{protocol}: Connection failed to {self.host}:{self.port}: {e}"
            )
            return False, str(e)

    def test_write(self, test_path: str) -> tuple[bool, str]:
        """Test write permissions by uploading small test file."""
        protocol = "FTPS" if self.use_tls else "FTP"
        logger.debug(f"{protocol}: Testing write permissions at {test_path}")
        try:
            test_data = b"RomHoard test"
            test_file = BytesIO(test_data)
            self.ftp.storbinary(f"STOR {test_path}", test_file)
            # Try to delete the test file
            try:
                self.ftp.delete(test_path)
            except Exception:
                pass  # OK if delete fails
            logger.debug(f"{protocol}: Write test successful at {test_path}")
            return True, ""
        except Exception as e:
            logger.debug(f"{protocol}: Write test failed at {test_path}: {e}")
            return False, str(e)

    def get_remote_size(self, remote_path: str) -> Optional[int]:
        """Get remote file size, or None if doesn't exist."""
        try:
            return self.ftp.size(remote_path)
        except ftplib.error_perm:
            return None

    def ensure_directory(self, remote_path: str) -> None:
        """Create remote directory tree if needed."""
        parts = remote_path.strip("/").split("/")
        current = ""
        for part in parts:
            current = f"{current}/{part}" if current else part
            try:
                self.ftp.mkd(current)
            except (ftplib.error_perm, ftplib.error_temp):
                # Directory likely exists, continue
                # Note: Some devices (e.g., Miyoo) return 4xx instead of 5xx
                pass

    def upload_file(
        self,
        local_path: str,
        remote_path: str,
        progress_callback: Optional[Callable[[int, int], None]] = None,
    ) -> None:
        """Upload file to remote path."""
        file_size = os.path.getsize(local_path)
        bytes_sent = [0]  # Use list to modify in nested function

        def callback(data):
            """Called for each block sent."""
            bytes_sent[0] += len(data)
            if progress_callback:
                progress_callback(bytes_sent[0], file_size)

        with open(local_path, "rb") as f:
            self.ftp.storbinary(f"STOR {remote_path}", f, callback=callback)

    def upload_data(self, data: BytesIO, remote_path: str) -> None:
        """Upload data from BytesIO to remote path."""
        data.seek(0)
        self.ftp.storbinary(f"STOR {remote_path}", data)

    def close(self) -> None:
        """Close connection."""
        if self.ftp:
            try:
                self.ftp.quit()
            except Exception:
                pass


class SFTPClient(TransferClient):
    """SFTP client implementation using paramiko."""

    def __init__(self, host: str, port: int, user: str, password: str):
        self.host = host
        self.port = port
        self.user = user
        self.password = password
        self.client: Optional[paramiko.SSHClient] = None
        self.sftp: Optional[paramiko.SFTPClient] = None

    def connect(self) -> tuple[bool, str]:
        """Connect and authenticate."""
        logger.debug(f"SFTP: Connecting to {self.host}:{self.port} as {self.user}")
        try:
            self.client = paramiko.SSHClient()
            # Auto-accept host keys - required for connecting to gaming devices
            self.client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
            self.client.connect(
                self.host,
                port=self.port,
                username=self.user,
                password=self.password,
                timeout=30,
                allow_agent=False,
                look_for_keys=False,
            )
            self.sftp = self.client.open_sftp()
            logger.debug(f"SFTP: Connected successfully to {self.host}:{self.port}")
            return True, ""
        except Exception as e:
            logger.debug(f"SFTP: Connection failed to {self.host}:{self.port}: {e}")
            return False, str(e)

    def test_write(self, test_path: str) -> tuple[bool, str]:
        """Test write permissions by uploading small test file."""
        logger.debug(f"SFTP: Testing write permissions at {test_path}")
        try:
            # Ensure parent directory exists first
            parent_dir = "/".join(test_path.split("/")[:-1])
            if parent_dir:
                self.ensure_directory(parent_dir)

            test_data = b"RomHoard test"
            with self.sftp.open(test_path, "wb") as f:
                f.write(test_data)
            # Try to delete the test file
            try:
                self.sftp.remove(test_path)
            except Exception:
                pass  # OK if delete fails
            logger.debug(f"SFTP: Write test successful at {test_path}")
            return True, ""
        except Exception as e:
            logger.debug(f"SFTP: Write test failed at {test_path}: {e}")
            return False, str(e)

    def get_remote_size(self, remote_path: str) -> Optional[int]:
        """Get remote file size, or None if doesn't exist."""
        try:
            return self.sftp.stat(remote_path).st_size
        except IOError:
            return None

    def ensure_directory(self, remote_path: str) -> None:
        """Create remote directory tree if needed."""
        parts = remote_path.strip("/").split("/")
        current = ""
        for part in parts:
            current = f"{current}/{part}" if current else part
            try:
                self.sftp.mkdir(current)
            except IOError:
                # Directory likely exists, continue
                pass

    def upload_file(
        self,
        local_path: str,
        remote_path: str,
        progress_callback: Optional[Callable[[int, int], None]] = None,
    ) -> None:
        """Upload file to remote path."""
        self.sftp.put(local_path, remote_path, callback=progress_callback)

    def upload_data(self, data: BytesIO, remote_path: str) -> None:
        """Upload data from BytesIO to remote path."""
        data.seek(0)
        with self.sftp.open(remote_path, "wb") as remote_file:
            remote_file.write(data.read())

    def close(self) -> None:
        """Close connection."""
        if self.sftp:
            try:
                self.sftp.close()
            except Exception:
                pass
        if self.client:
            try:
                self.client.close()
            except Exception:
                pass


def create_transfer_client(device: Device) -> TransferClient:
    """Factory function to create appropriate transfer client."""
    if device.transfer_type == Device.TRANSFER_SFTP:
        return SFTPClient(
            host=device.transfer_host,
            port=device.effective_port,
            user=device.transfer_user,
            password=device.transfer_password,
        )
    else:
        # Use empty credentials for anonymous FTP
        user = "" if device.transfer_anonymous else device.transfer_user
        password = "" if device.transfer_anonymous else device.transfer_password
        return FTPClient(
            host=device.transfer_host,
            port=device.effective_port,
            user=user,
            password=password,
            use_tls=(device.transfer_type == Device.TRANSFER_FTPS),
        )


def _sanitize_filename(name: str) -> str:
    """Sanitize filename for safe remote storage."""
    # Remove or replace unsafe characters
    unsafe_chars = '<>:"|?*'
    for char in unsafe_chars:
        name = name.replace(char, "_")
    return name


def _upload_game_image(
    client: TransferClient,
    game: Game,
    rom_filename: str,
    device: Device,
    progress: SendProgress,
) -> ImageResult | None:
    """Upload image for a game to device.

    Args:
        client: Connected TransferClient instance
        game: Game to upload image for
        rom_filename: The ROM filename (used for image path building)
        device: Device with image configuration
        progress: SendProgress to update

    Returns:
        ImageResult or None if images not enabled
    """
    from library.image_utils import prepare_image_for_device

    # Get remote image path
    image_remote_path = device.get_effective_image_path(game.system.slug, rom_filename)
    if not image_remote_path:
        return None

    # Prepare image data (with optional resize)
    result = prepare_image_for_device(
        game=game,
        image_type=device.image_type,
        max_width=device.image_max_width,
    )
    if not result:
        progress.images_skipped += 1
        return ImageResult(
            game_id=game.pk,
            rom_filename=rom_filename,
            remote_path=image_remote_path,
            success=False,
            skipped=True,
            error="No image available",
        )

    image_data, ext = result

    # Check if image already exists with same size
    data_size = len(image_data.getvalue())
    remote_size = client.get_remote_size(image_remote_path)
    if remote_size is not None and remote_size == data_size:
        progress.images_skipped += 1
        return ImageResult(
            game_id=game.pk,
            rom_filename=rom_filename,
            remote_path=image_remote_path,
            success=True,
            skipped=True,
            bytes=data_size,
        )

    # Ensure directory exists
    remote_dir = "/".join(image_remote_path.split("/")[:-1])
    if remote_dir:
        client.ensure_directory(remote_dir)

    # Upload
    try:
        client.upload_data(image_data, image_remote_path)
        progress.images_uploaded += 1
        logger.info(f"Uploaded image for {game.name} -> {image_remote_path}")
        return ImageResult(
            game_id=game.pk,
            rom_filename=rom_filename,
            remote_path=image_remote_path,
            success=True,
            bytes=data_size,
        )
    except Exception as e:
        progress.images_failed += 1
        logger.error(f"Failed to upload image for {game.name}: {e}")
        return ImageResult(
            game_id=game.pk,
            rom_filename=rom_filename,
            remote_path=image_remote_path,
            success=False,
            error=str(e),
        )


def send_games_to_device(
    games: list[Game],
    device: Device,
    progress_callback: Optional[Callable[[SendProgress], None]] = None,
    max_retries: int = 3,
    roms: Optional[list[ROM]] = None,
) -> tuple[list[FileResult], list[FileResult], list[FileResult], list[ImageResult]]:
    """
    Upload games and images to device via FTP/SFTP.

    Args:
        games: List of games to upload (ignored if roms is provided)
        device: Device with transfer configuration
        progress_callback: Optional callback for progress updates
        max_retries: Number of retry attempts per file (default: 3)
        roms: Optional list of specific ROMs to upload (takes precedence over games)

    Returns:
        Tuple of (uploaded, skipped, failed, image_results) file results

    Raises:
        Exception: If connection fails or other critical error occurs
    """
    uploaded = []
    skipped = []
    failed = []
    image_results = []

    # 1. Collect all ROM files to upload
    rom_files = []
    if roms:
        # Use specific ROMs provided
        for rom in roms:
            rom_files.append((rom.rom_set.game, rom))
    else:
        # Collect all ROMs from games
        for game in games:
            for rom_set in game.rom_sets.all():
                for rom in rom_set.roms.all():
                    rom_files.append((game, rom))

    if not rom_files:
        return uploaded, skipped, failed, image_results

    # 2. Calculate total bytes using stored file_size (works for archived ROMs too)
    total_bytes = sum(rom.file_size for game, rom in rom_files)

    progress = SendProgress(files_total=len(rom_files), bytes_total=total_bytes)

    # 3. Create transfer client and connect
    client = create_transfer_client(device)
    success, error = client.connect()
    if not success:
        raise Exception(f"Failed to connect: {error}")

    try:
        # 4. Test connection with write test
        test_path = device.get_effective_transfer_path(".romhoard_test")
        success, error = client.test_write(test_path)
        if not success:
            raise Exception(f"Write test failed: {error}")

        logger.info(
            f"Connected to {device.transfer_host} via {device.transfer_type.upper()}"
        )

        # 5. Upload each file
        for game, rom in rom_files:
            progress.current_file = rom.file_name

            # Use get_rom_file to handle both loose and archived ROMs
            try:
                with get_rom_file(rom) as (local_path, actual_filename):
                    # Build remote path using sanitized names
                    game_name_safe = _sanitize_filename(game.name)
                    filename_safe = _sanitize_filename(actual_filename)

                    # Build relative path (system folder + optional game folder + filename)
                    system_folder = device.get_system_folder(game.system.slug)
                    if device.use_game_folders_for_system(game.system.slug):
                        relative_path = (
                            f"{system_folder}/{game_name_safe}/{filename_safe}"
                        )
                    else:
                        relative_path = f"{system_folder}/{filename_safe}"

                    # Get full remote path including transfer_path_prefix
                    remote_path = device.get_effective_transfer_path(relative_path)

                    # Check if file already exists with same size
                    local_size = os.path.getsize(local_path)
                    remote_size = client.get_remote_size(remote_path)
                    if remote_size is not None and remote_size == local_size:
                        # Skip - same size
                        result = FileResult(
                            game_id=game.pk,
                            filename=actual_filename,
                            remote_path=remote_path,
                            success=True,
                            skipped=True,
                            bytes=local_size,
                        )
                        skipped.append(result)
                        progress.files_skipped += 1
                        logger.info(f"Skipped {actual_filename} (same size)")

                        # Still upload image even if ROM was skipped
                        if device.include_images:
                            image_result = _upload_game_image(
                                client=client,
                                game=game,
                                rom_filename=actual_filename,
                                device=device,
                                progress=progress,
                            )
                            if image_result:
                                image_results.append(image_result)

                        if progress_callback:
                            progress_callback(progress)
                        continue

                    # Ensure remote directory exists
                    remote_dir = "/".join(remote_path.split("/")[:-1])
                    if remote_dir:
                        client.ensure_directory(remote_dir)

                    # Upload with retries
                    last_error = ""
                    upload_success = False
                    for attempt in range(max_retries):
                        try:
                            bytes_before = progress.bytes_uploaded

                            def file_progress(bytes_transferred, total_bytes):
                                progress.bytes_uploaded = (
                                    bytes_before + bytes_transferred
                                )
                                if progress_callback:
                                    progress_callback(progress)

                            client.upload_file(local_path, remote_path, file_progress)
                            upload_success = True
                            progress.bytes_uploaded = bytes_before + local_size
                            break
                        except Exception as e:
                            last_error = str(e)
                            logger.warning(
                                f"Upload attempt {attempt + 1}/{max_retries} failed for {actual_filename}: {e}"
                            )
                            if attempt < max_retries - 1:
                                # Wait a bit before retry
                                import time

                                time.sleep(1)

                    if upload_success:
                        result = FileResult(
                            game_id=game.pk,
                            filename=actual_filename,
                            remote_path=remote_path,
                            success=True,
                            bytes=local_size,
                        )
                        uploaded.append(result)
                        progress.files_uploaded += 1
                        logger.info(f"Uploaded {actual_filename} -> {remote_path}")

                        # Upload image for this game if enabled
                        if device.include_images:
                            image_result = _upload_game_image(
                                client=client,
                                game=game,
                                rom_filename=actual_filename,
                                device=device,
                                progress=progress,
                            )
                            if image_result:
                                image_results.append(image_result)
                    else:
                        result = FileResult(
                            game_id=game.pk,
                            filename=actual_filename,
                            remote_path=remote_path,
                            success=False,
                            error=last_error,
                        )
                        failed.append(result)
                        progress.files_failed += 1
                        logger.error(
                            f"Failed to upload {actual_filename}: {last_error}"
                        )

                    if progress_callback:
                        progress_callback(progress)

            except (FileNotFoundError, IOError, OSError) as e:
                # File missing or extraction failed
                result = FileResult(
                    game_id=game.pk,
                    filename=rom.file_name,
                    remote_path="",
                    success=False,
                    error=str(e),
                )
                failed.append(result)
                progress.files_failed += 1
                logger.error(f"Failed to access {rom.file_name}: {e}")
                if progress_callback:
                    progress_callback(progress)

    finally:
        client.close()

    return uploaded, skipped, failed, image_results
