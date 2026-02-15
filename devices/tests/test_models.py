"""Tests for devices app models."""

import pytest
from devices.models import Device


@pytest.mark.parametrize(
    "prefix, root, expected",
    [
        ("", "Roms/", "Roms"),
        ("/mnt/SDCARD", "Roms/", "/mnt/SDCARD/Roms"),
        ("/mnt/SDCARD/", "Roms/", "/mnt/SDCARD/Roms"),
        ("/mnt/SDCARD", "/Roms/", "/mnt/SDCARD/Roms"),
        ("/mnt/SDCARD/", "/Roms/", "/mnt/SDCARD/Roms"),
        ("storage", "roms", "storage/roms"),
    ],
)
def test_device_get_effective_transfer_path(prefix, root, expected):
    """Test smart merging of FTP root and root path on Device."""
    device = Device(transfer_path_prefix=prefix, root_path=root)
    assert device.get_effective_transfer_path() == expected


def test_device_get_effective_transfer_path_with_relative():
    """Test merging with an additional relative path."""
    device = Device(transfer_path_prefix="/mnt/SDCARD", root_path="Roms/")
    assert (
        device.get_effective_transfer_path("GBA/game.gba")
        == "/mnt/SDCARD/Roms/GBA/game.gba"
    )


@pytest.mark.django_db
def test_device_has_wifi_default():
    """Test that has_wifi defaults to True."""
    device = Device.objects.create(name="Test Device", slug="test-device")
    assert device.has_wifi is True


@pytest.mark.django_db
def test_device_has_transfer_config():
    """Test the has_transfer_config property."""
    # Device without transfer config
    device = Device.objects.create(name="No Transfer", slug="no-transfer")
    assert device.has_transfer_config is False

    # Device with transfer config
    device_with_config = Device.objects.create(
        name="With Transfer",
        slug="with-transfer",
        transfer_type=Device.TRANSFER_FTP,
        transfer_host="192.168.1.100",
    )
    assert device_with_config.has_transfer_config is True


@pytest.mark.django_db
def test_device_root_path_default():
    """Test that root_path defaults to Roms/."""
    device = Device.objects.create(name="Test Device", slug="test-device-path")
    assert device.root_path == "Roms/"


@pytest.mark.django_db
def test_device_get_system_folder():
    """Test getting system folder from system_paths."""
    device = Device.objects.create(
        name="Test Device",
        slug="test-device-system",
        root_path="Roms/",
        system_paths={"gba": {"folder": "GBA"}, "snes": {"folder": "SNES"}},
    )

    # Custom system folder (just the folder name, not full path)
    assert device.get_system_folder("gba") == "GBA"
    assert device.get_system_folder("snes") == "SNES"

    # Fallback to slug uppercase
    assert device.get_system_folder("nes") == "NES"


@pytest.mark.django_db
def test_device_use_game_folders_for_system():
    """Test game folders setting per system."""
    device = Device.objects.create(
        name="Test Device",
        slug="test-device-folders",
        system_paths={
            "arcade": {"folder": "ARCADE", "game_folders": False},
            "psx": {"folder": "PS", "game_folders": True},
        },
    )

    # Defaults to False when not configured
    assert device.use_game_folders_for_system("gba") is False

    # System-specific settings
    assert device.use_game_folders_for_system("arcade") is False
    assert device.use_game_folders_for_system("psx") is True


@pytest.mark.django_db
def test_device_get_rom_path():
    """Test building full ROM path."""
    device = Device.objects.create(
        name="Test Device",
        slug="test-device-rom-path",
        root_path="Roms/",
        system_paths={
            "gba": {"folder": "GBA"},
            "psx": {"folder": "PS", "game_folders": True},
        },
    )

    # Without game folders (default)
    path = device.get_rom_path("gba", "Super Mario", "rom.gba")
    assert path == "Roms/GBA/rom.gba"

    # With game folders (per-system)
    path = device.get_rom_path("psx", "Crash Bandicoot", "game.bin")
    assert path == "Roms/PS/Crash Bandicoot/game.bin"


# Image path tests


@pytest.mark.django_db
def test_device_get_image_path_disabled():
    """Image path returns None when include_images is False."""
    device = Device.objects.create(
        name="Test",
        slug="test-image-disabled",
        include_images=False,
        image_path_template="{root_path}/{system}/Imgs/{romname}.png",
    )
    assert device.get_image_path("gba", "mario.gba") is None


@pytest.mark.django_db
def test_device_get_image_path_no_template():
    """Image path returns None when no template is set."""
    device = Device.objects.create(
        name="Test",
        slug="test-image-no-template",
        include_images=True,
        image_path_template="",
    )
    assert device.get_image_path("gba", "mario.gba") is None


@pytest.mark.django_db
def test_device_get_image_path_onionos():
    """OnionOS-style image path."""
    device = Device.objects.create(
        name="Test",
        slug="test-image-onionos",
        root_path="Roms/",
        include_images=True,
        image_path_template="{root_path}/{system}/Imgs/{romname}.png",
        system_paths={"gba": {"folder": "GBA"}},
    )
    path = device.get_image_path("gba", "mario.gba")
    assert path == "Roms/GBA/Imgs/mario.png"


@pytest.mark.django_db
def test_device_get_image_path_minui():
    """MinUI-style image path with romname_ext (includes ROM extension)."""
    device = Device.objects.create(
        name="Test",
        slug="test-image-minui",
        root_path="Roms/",
        include_images=True,
        image_path_template="{root_path}/{system}/.res/{romname_ext}.png",
        system_paths={"gba": {"folder": "GBA"}},
    )
    path = device.get_image_path("gba", "mario.gba")
    assert path == "Roms/GBA/.res/mario.gba.png"


@pytest.mark.django_db
def test_device_get_image_path_muos():
    """muOS-style image path (absolute, not under root_path)."""
    device = Device.objects.create(
        name="Test",
        slug="test-image-muos",
        root_path="Roms/",
        include_images=True,
        image_path_template="MUOS/info/catalogue/{system}/box/{romname}.png",
        system_paths={"gba": {"folder": "GBA"}},
    )
    path = device.get_image_path("gba", "mario.gba")
    assert path == "MUOS/info/catalogue/GBA/box/mario.png"


@pytest.mark.django_db
def test_device_get_image_path_arkos():
    """ArkOS/JELOS/Batocera-style image path."""
    device = Device.objects.create(
        name="Test",
        slug="test-image-arkos",
        root_path="roms/",
        include_images=True,
        image_path_template="{root_path}/{system}/media/images/{romname}.png",
        system_paths={"gba": {"folder": "gba"}},
    )
    path = device.get_image_path("gba", "Super Mario Advance 4 (USA).gba")
    assert path == "roms/gba/media/images/Super Mario Advance 4 (USA).png"


@pytest.mark.django_db
def test_device_get_image_path_system_folder_fallback():
    """Image path uses uppercase system slug when no folder configured."""
    device = Device.objects.create(
        name="Test",
        slug="test-image-fallback",
        root_path="Roms/",
        include_images=True,
        image_path_template="{root_path}/{system}/Imgs/{romname}.png",
    )
    path = device.get_image_path("snes", "zelda.sfc")
    assert path == "Roms/SNES/Imgs/zelda.png"


@pytest.mark.django_db
def test_device_get_effective_image_path():
    """Effective image path includes transfer_path_prefix."""
    device = Device.objects.create(
        name="Test",
        slug="test-effective-image",
        root_path="Roms/",
        transfer_path_prefix="/mnt/SDCARD",
        include_images=True,
        image_path_template="{root_path}/{system}/Imgs/{romname}.png",
        system_paths={"gba": {"folder": "GBA"}},
    )
    path = device.get_effective_image_path("gba", "mario.gba")
    assert path == "/mnt/SDCARD/Roms/GBA/Imgs/mario.png"


@pytest.mark.django_db
def test_device_get_effective_image_path_no_prefix():
    """Effective image path without transfer_path_prefix."""
    device = Device.objects.create(
        name="Test",
        slug="test-effective-image-no-prefix",
        root_path="Roms/",
        transfer_path_prefix="",
        include_images=True,
        image_path_template="{root_path}/{system}/Imgs/{romname}.png",
        system_paths={"gba": {"folder": "GBA"}},
    )
    path = device.get_effective_image_path("gba", "mario.gba")
    assert path == "Roms/GBA/Imgs/mario.png"


@pytest.mark.django_db
def test_device_get_effective_image_path_disabled():
    """Effective image path returns None when images disabled."""
    device = Device.objects.create(
        name="Test",
        slug="test-effective-image-disabled",
        include_images=False,
    )
    assert device.get_effective_image_path("gba", "mario.gba") is None


@pytest.mark.django_db
def test_device_image_defaults():
    """Test default values for image fields."""
    device = Device.objects.create(name="Test Device", slug="test-defaults")
    assert device.include_images is False
    assert device.applied_preset == ""
    assert device.image_type == "cover"
    assert device.image_path_template == ""
    assert device.image_max_width is None
