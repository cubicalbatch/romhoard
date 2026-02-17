"""Device and DevicePreset models for ROM organization and transfer configuration."""

from __future__ import annotations

from django.db import models

from library.crypto import decrypt_value, encrypt_value


class DevicePreset(models.Model):
    """Pre-configured device settings that can be applied to devices."""

    slug = models.SlugField(unique=True, primary_key=True)
    name = models.CharField(max_length=100)
    description = models.TextField(blank=True)
    tags = models.JSONField(
        default=list, help_text="Search tags like ['muos', 'anbernic']"
    )
    is_builtin = models.BooleanField(
        default=False, help_text="True for repo-shipped presets"
    )

    # Configuration sections (each optional)
    folders_config = models.JSONField(
        null=True,
        blank=True,
        help_text="Folder settings: {root_path, system_paths}",
    )
    images_config = models.JSONField(
        null=True,
        blank=True,
        help_text="Image settings: {path_template, max_width, image_type}",
    )
    transfer_config = models.JSONField(
        null=True,
        blank=True,
        help_text="Transfer settings: {protocol, port, user, password, path_prefix, anonymous}",
    )

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["name"]

    def __str__(self) -> str:
        return self.name

    @property
    def has_folders(self) -> bool:
        """Check if preset has folder configuration."""
        return bool(self.folders_config)

    @property
    def has_images(self) -> bool:
        """Check if preset has image configuration."""
        return bool(self.images_config)

    @property
    def has_transfer(self) -> bool:
        """Check if preset has transfer configuration."""
        return bool(self.transfer_config)


class Device(models.Model):
    """A gaming device with ROM organization and optional transfer configuration."""

    # Device identity
    name = models.CharField(max_length=100)
    slug = models.SlugField(unique=True)
    description = models.TextField(blank=True)

    # ROM Organization
    root_path = models.CharField(
        max_length=255,
        default="Roms/",
        help_text="Root path in ZIP (e.g., 'Roms/', '/Roms/', 'ROMS/')",
    )
    system_paths = models.JSONField(
        default=dict,
        help_text="Per-system folder config: {system_slug: {folder: str, game_folders?: bool}}",
    )

    # WiFi capability
    has_wifi = models.BooleanField(
        default=True,
        help_text="Whether this device has WiFi capability for FTP/SFTP transfers",
    )

    # Transfer configuration
    TRANSFER_NONE = ""
    TRANSFER_FTP = "ftp"
    TRANSFER_FTPS = "ftps"
    TRANSFER_SFTP = "sftp"
    TRANSFER_CHOICES = [
        (TRANSFER_NONE, "None"),
        (TRANSFER_FTP, "FTP"),
        (TRANSFER_FTPS, "FTPS (TLS)"),
        (TRANSFER_SFTP, "SFTP (SSH)"),
    ]

    transfer_type = models.CharField(
        max_length=10,
        choices=TRANSFER_CHOICES,
        default=TRANSFER_NONE,
        blank=True,
        help_text="Protocol for transferring ROMs to device",
    )
    transfer_host = models.CharField(
        max_length=255, blank=True, help_text="FTP/SFTP server hostname or IP"
    )
    transfer_port = models.IntegerField(
        null=True,
        blank=True,
        help_text="Port (default: 21 for FTP/FTPS, 22 for SFTP)",
    )
    transfer_user = models.CharField(max_length=100, blank=True, help_text="Username")
    _transfer_password = models.CharField(
        max_length=255,
        blank=True,
        help_text="Encrypted password",
        db_column="transfer_password",  # Keep same DB column name
    )
    transfer_anonymous = models.BooleanField(
        default=False,
        help_text="Use anonymous FTP login (no credentials)",
    )
    transfer_path_prefix = models.CharField(
        max_length=255,
        blank=True,
        help_text="Absolute path to storage mount point on device (e.g., '/mnt/SDCARD')",
    )

    # Image configuration
    IMAGE_TYPE_COVER = "cover"
    IMAGE_TYPE_SCREENSHOT = "screenshot"
    IMAGE_TYPE_CHOICES = [
        (IMAGE_TYPE_COVER, "Box Art"),
        (IMAGE_TYPE_SCREENSHOT, "Screenshot"),
    ]

    include_images = models.BooleanField(
        default=False,
        help_text="Include images when sending/downloading games to this device",
    )
    image_type = models.CharField(
        max_length=20,
        choices=IMAGE_TYPE_CHOICES,
        default=IMAGE_TYPE_COVER,
        help_text="Default image type to include",
    )

    # Preset tracking
    applied_preset = models.CharField(
        max_length=50,
        blank=True,
        help_text="Slug of last applied preset (informational only)",
    )
    image_path_template = models.CharField(
        max_length=255,
        blank=True,
        default="",
        help_text="Path pattern for images. Placeholders: {root_path}, {system}, {romname}, {romname_ext}",
    )
    image_max_width = models.PositiveIntegerField(
        null=True,
        blank=True,
        help_text="Maximum image width in pixels (resizes maintaining aspect ratio)",
    )

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["name"]

    def __str__(self) -> str:
        return self.name

    # Password encryption property
    @property
    def transfer_password(self) -> str:
        """Get decrypted transfer password."""
        if not self._transfer_password:
            return ""
        decrypted = decrypt_value(self._transfer_password)
        if decrypted is None:
            # Decryption failed (e.g., SECRET_KEY changed) - return empty
            return ""
        return decrypted

    @transfer_password.setter
    def transfer_password(self, value: str) -> None:
        """Set and encrypt transfer password."""
        if not value:
            self._transfer_password = ""
        elif value.startswith("enc:"):
            # Already encrypted, store as-is
            self._transfer_password = value
        else:
            # Encrypt the plaintext password
            self._transfer_password = encrypt_value(value)

    # ROM path methods

    def get_system_folder(self, system_slug: str) -> str:
        """Get the folder name for a system.

        Falls back to uppercase system slug if not configured.

        Args:
            system_slug: System identifier (e.g., "gba")

        Returns:
            Folder name (e.g., "GBA")
        """
        config = self.system_paths.get(system_slug, {})
        if isinstance(config, str):
            # Handle legacy simple string format
            return config
        return config.get("folder", system_slug.upper())

    def use_game_folders_for_system(self, system_slug: str) -> bool:
        """Check if game folders should be used for a system.

        Args:
            system_slug: System identifier (e.g., "gba")

        Returns:
            Whether to use game folders for this system (defaults to False)
        """
        config = self.system_paths.get(system_slug, {})
        if isinstance(config, dict):
            return config.get("game_folders", False)
        return False

    def get_rom_path(self, system_slug: str, game_name: str, filename: str) -> str:
        """Build the full path for a ROM in the ZIP.

        Args:
            system_slug: System identifier (e.g., "gba")
            game_name: Sanitized game name
            filename: ROM filename

        Returns:
            Full path like "Roms/GBA/game.gba" or "Roms/GBA/Game Name/game.gba"
        """
        parts = [self.root_path.strip("/")]
        parts.append(self.get_system_folder(system_slug))

        if self.use_game_folders_for_system(system_slug):
            parts.append(game_name)

        parts.append(filename)
        return "/".join(parts)

    # Transfer methods

    @property
    def default_port(self) -> int:
        """Get default port for transfer type."""
        if self.transfer_type == self.TRANSFER_SFTP:
            return 22
        return 21

    @property
    def effective_port(self) -> int:
        """Get configured port or default."""
        return self.transfer_port or self.default_port

    @property
    def has_transfer_config(self) -> bool:
        """Check if transfer is configured."""
        return bool(self.transfer_type and self.transfer_host)

    def get_effective_transfer_path(self, relative_path: str = "") -> str:
        """Build the full remote path for FTP/SFTP transfers.

        Combines transfer_path_prefix, root_path, and optional relative_path.

        Args:
            relative_path: Optional path relative to the root_path

        Returns:
            Full absolute or relative remote path
        """
        prefix = self.transfer_path_prefix.rstrip("/")
        root = self.root_path.strip("/")

        # Determine if we should treat this as an absolute path
        is_absolute = self.transfer_path_prefix.startswith("/")

        if prefix:
            path = f"{prefix}/{root}" if root else prefix
        else:
            path = root

        if relative_path:
            path = f"{path}/{relative_path.lstrip('/')}"

        if is_absolute and not path.startswith("/"):
            path = f"/{path}"

        return path

    # Image path methods

    def get_image_path(self, system_slug: str, rom_filename: str) -> str | None:
        """Build the path for an image file (for ZIP archives).

        Uses the image_path_template with placeholder substitution.

        Args:
            system_slug: System identifier (e.g., "gba")
            rom_filename: The ROM filename including extension (e.g., "mario.gba")

        Returns:
            Image path or None if images not enabled
        """
        if not self.include_images or not self.image_path_template:
            return None

        from pathlib import Path

        # Get system folder name
        system_folder = self.get_system_folder(system_slug)

        # Parse ROM filename
        rom_path = Path(rom_filename)
        romname = rom_path.stem  # Without extension
        romname_ext = rom_filename  # With extension

        # Build path from template
        path = self.image_path_template.format(
            root_path=self.root_path.strip("/"),
            system=system_folder,
            romname=romname,
            romname_ext=romname_ext,
        )

        return path

    def get_effective_image_path(
        self, system_slug: str, rom_filename: str
    ) -> str | None:
        """Build the full remote path for an image including transfer_path_prefix.

        Args:
            system_slug: System identifier
            rom_filename: ROM filename with extension

        Returns:
            Full remote path for FTP/SFTP or None if images not enabled
        """
        image_path = self.get_image_path(system_slug, rom_filename)
        if not image_path:
            return None

        prefix = self.transfer_path_prefix.rstrip("/")
        if prefix:
            return f"{prefix}/{image_path.lstrip('/')}"
        return image_path

    # Preset methods

    def apply_preset(self, preset: DevicePreset) -> None:
        """Apply a preset configuration to this device.

        Copies all available configuration from the preset to this device.

        Args:
            preset: DevicePreset to apply
        """
        if preset.folders_config:
            config = preset.folders_config
            if "root_path" in config:
                self.root_path = config["root_path"]
            if "system_paths" in config:
                # Merge with existing, preset takes precedence
                self.system_paths = {**self.system_paths, **config["system_paths"]}

        if preset.images_config:
            config = preset.images_config
            self.include_images = True
            if "path_template" in config:
                self.image_path_template = config["path_template"]
            if "max_width" in config:
                self.image_max_width = config["max_width"]
            if "image_type" in config:
                self.image_type = config["image_type"]

        if preset.transfer_config:
            config = preset.transfer_config
            if "protocol" in config:
                self.transfer_type = config["protocol"]
            if "port" in config:
                self.transfer_port = config["port"]
            if "user" in config:
                self.transfer_user = config["user"]
            if "password" in config:
                self.transfer_password = config["password"]
            if "anonymous" in config:
                self.transfer_anonymous = config["anonymous"]
            if "path_prefix" in config:
                self.transfer_path_prefix = config["path_prefix"]

        self.applied_preset = preset.slug
