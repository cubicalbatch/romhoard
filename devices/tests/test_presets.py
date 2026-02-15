"""Tests for device preset functionality."""

import pytest

from devices.models import Device, DevicePreset
from devices.preset_loader import get_presets_config, sync_presets


class TestDevicePresetModel:
    """Tests for DevicePreset model."""

    @pytest.mark.django_db
    def test_device_preset_creation(self):
        """Test creating a device preset."""
        preset = DevicePreset.objects.create(
            slug="test-preset",
            name="Test Preset",
            description="A test preset",
            tags=["test", "example"],
            is_builtin=False,
            folders_config={"root_path": "Roms/"},
            images_config={
                "path_template": "{root_path}/{system}/Imgs/{romname}.png",
                "max_width": 250,
            },
            transfer_config={"protocol": "ftp", "port": 21},
        )
        assert preset.slug == "test-preset"
        assert preset.name == "Test Preset"
        assert preset.has_folders is True
        assert preset.has_images is True
        assert preset.has_transfer is True

    @pytest.mark.django_db
    def test_device_preset_optional_sections(self):
        """Test preset with only some sections."""
        preset = DevicePreset.objects.create(
            slug="images-only",
            name="Images Only",
            images_config={"path_template": "{root_path}/{system}/Imgs/{romname}.png"},
        )
        assert preset.has_folders is False
        assert preset.has_images is True
        assert preset.has_transfer is False


class TestDeviceApplyPreset:
    """Tests for Device.apply_preset method."""

    @pytest.mark.django_db
    def test_apply_preset_folders(self):
        """Test applying folder config from preset."""
        preset = DevicePreset.objects.create(
            slug="folder-preset",
            name="Folder Preset",
            folders_config={
                "root_path": "roms/",
                "system_paths": {"gba": {"folder": "Game Boy Advance"}},
            },
        )
        device = Device.objects.create(name="Test", slug="test-folders")
        device.apply_preset(preset)
        device.save()

        assert device.root_path == "roms/"
        assert device.system_paths == {"gba": {"folder": "Game Boy Advance"}}
        assert device.applied_preset == "folder-preset"

    @pytest.mark.django_db
    def test_apply_preset_images(self):
        """Test applying image config from preset."""
        preset = DevicePreset.objects.create(
            slug="image-preset",
            name="Image Preset",
            images_config={
                "path_template": "MUOS/info/catalogue/{system}/box/{romname}.png",
                "max_width": 515,
                "image_type": "cover",
            },
        )
        device = Device.objects.create(name="Test", slug="test-images")
        device.apply_preset(preset)
        device.save()

        assert device.include_images is True
        assert (
            device.image_path_template
            == "MUOS/info/catalogue/{system}/box/{romname}.png"
        )
        assert device.image_max_width == 515
        assert device.image_type == "cover"

    @pytest.mark.django_db
    def test_apply_preset_transfer(self):
        """Test applying transfer config from preset."""
        preset = DevicePreset.objects.create(
            slug="transfer-preset",
            name="Transfer Preset",
            transfer_config={
                "protocol": "ftp",
                "port": 21,
                "user": "root",
                "password": "root",
                "path_prefix": "/mnt/SDCARD",
            },
        )
        device = Device.objects.create(name="Test", slug="test-transfer")
        device.apply_preset(preset)
        device.save()

        assert device.transfer_type == "ftp"
        assert device.transfer_port == 21
        assert device.transfer_user == "root"
        assert device.transfer_password == "root"
        assert device.transfer_path_prefix == "/mnt/SDCARD"

    @pytest.mark.django_db
    def test_apply_preset_merges_system_paths(self):
        """Test that applying preset merges system_paths with existing."""
        preset = DevicePreset.objects.create(
            slug="merge-preset",
            name="Merge Preset",
            folders_config={
                "system_paths": {"snes": {"folder": "SNES"}},
            },
        )
        device = Device.objects.create(
            name="Test",
            slug="test-merge",
            system_paths={"gba": {"folder": "GBA"}},
        )
        device.apply_preset(preset)
        device.save()

        # Both should be present
        assert device.system_paths["gba"] == {"folder": "GBA"}
        assert device.system_paths["snes"] == {"folder": "SNES"}


class TestPresetLoader:
    """Tests for preset loading from JSON."""

    def test_get_presets_config(self):
        """Test loading presets from JSON file."""
        presets = get_presets_config()
        assert isinstance(presets, list)
        # Should have the built-in presets
        slugs = [p["slug"] for p in presets]
        assert "onionos" in slugs
        assert "muos" in slugs
        assert "minui" in slugs
        assert "arkos" in slugs

    @pytest.mark.django_db
    def test_sync_presets(self):
        """Test syncing presets to database."""
        count = sync_presets()
        assert count >= 4  # At least the built-in presets

        # Verify presets exist in database
        assert DevicePreset.objects.filter(slug="onionos").exists()
        assert DevicePreset.objects.filter(slug="muos").exists()

        # Verify is_builtin is set
        preset = DevicePreset.objects.get(slug="muos")
        assert preset.is_builtin is True

    @pytest.mark.django_db
    def test_sync_presets_updates_existing(self):
        """Test that sync updates existing presets."""
        # Create a preset manually
        DevicePreset.objects.create(
            slug="muos",
            name="Old Name",
            is_builtin=True,
        )

        # Sync should update it
        sync_presets()

        preset = DevicePreset.objects.get(slug="muos")
        assert preset.name == "muOS"
