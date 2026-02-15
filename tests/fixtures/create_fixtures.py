#!/usr/bin/env python3
"""
Create test fixture files for ROM scanner tests.

This script generates a complete set of test ROMs, archives, and images
to support integration testing without mocking.
"""

import zipfile
import py7zr
from pathlib import Path


def create_empty_file(path: Path, size: int = 1024):
    """Create an empty file with specified size."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "wb") as f:
        f.write(b"\0" * size)


def create_rom_file(path: Path, name: str, size: int = 4096):
    """Create a ROM file with dummy content."""
    full_path = path / name
    full_path.parent.mkdir(parents=True, exist_ok=True)
    with open(full_path, "wb") as f:
        # Write some dummy ROM content
        f.write(b"ROM:" + name.encode() + b"\0" * (size - len(name) - 4))
    return full_path


def create_image_file(path: Path, name: str):
    """Create a minimal PNG image file."""
    # 1x1 transparent PNG
    png_data = (
        b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01"
        b"\x08\x06\x00\x00\x00\x1f\x15\xc4\x89\x00\x00\x00\nIDATx\x9cc\xf8"
        b"\x00\x00\x00\x01\x00\x01\x00\x00\x00\x00IEND\xaeB`\x82"
    )

    full_path = path / name
    full_path.parent.mkdir(parents=True, exist_ok=True)
    with open(full_path, "wb") as f:
        f.write(png_data)
    return full_path


def create_zip_archive(path: Path, name: str, files: dict):
    """Create a ZIP archive with specified files."""
    full_path = path / name
    full_path.parent.mkdir(parents=True, exist_ok=True)

    with zipfile.ZipFile(full_path, "w") as zf:
        for filename, content in files.items():
            zf.writestr(filename, content)
    return full_path


def create_7z_archive(path: Path, name: str, files: dict):
    """Create a 7z archive with specified files."""
    full_path = path / name
    full_path.parent.mkdir(parents=True, exist_ok=True)

    with py7zr.SevenZipFile(full_path, "w") as zf:
        for filename, content in files.items():
            # Create a temporary file for each content
            import tempfile

            with tempfile.NamedTemporaryFile() as tmp:
                tmp.write(content)
                tmp.flush()
                zf.write(tmp.name, filename)
    return full_path


def main():
    """Generate all fixture files."""
    fixtures_dir = Path(__file__).parent / "rom_library"

    print(f"Creating fixtures in {fixtures_dir}")

    # GBA folder
    gba_dir = fixtures_dir / "GBA"
    create_rom_file(gba_dir, "Super Mario Advance (USA).gba")
    create_rom_file(gba_dir, "Pokemon Emerald (USA, Europe) (Rev 1).gba")
    create_rom_file(gba_dir, "Castlevania (USA) [!].gba")
    create_image_file(gba_dir, "cover.png")
    create_empty_file(gba_dir / "bios" / "gba_bios.bin", 1024)

    # N64 folder
    n64_dir = fixtures_dir / "N64"
    create_rom_file(n64_dir, "Mario 64 (USA).z64")

    # Multi-game archive for N64
    create_zip_archive(
        n64_dir,
        "games.zip",
        {
            "Zelda.z64": b"ROM:Zelda.z64" + b"\0" * 4000,
            "Mario Kart.z64": b"ROM:Mario Kart.z64" + b"\0" * 4000,
        },
    )

    # RandomFolder for exclusive extension detection
    random_dir = fixtures_dir / "RandomFolder"
    create_rom_file(random_dir, "game.gba")
    create_rom_file(random_dir, "game.z64")

    # PS1 folder
    ps1_dir = fixtures_dir / "PS1"

    # Single-game archive (multi-disc)
    create_zip_archive(
        ps1_dir,
        "Final Fantasy VII (USA).zip",
        {
            "Final Fantasy VII (Disc 1).bin": b"ROM:FF7 Disc 1" + b"\0" * 4000,
            "Final Fantasy VII (Disc 2).bin": b"ROM:FF7 Disc 2" + b"\0" * 4000,
        },
    )

    # Multi-game archive (should expand)
    create_7z_archive(
        ps1_dir,
        "Collection.7z",
        {
            "Crash Bandicoot.bin": b"ROM:Crash" + b"\0" * 4000,
            "Spyro.bin": b"ROM:Spyro" + b"\0" * 4000,
        },
    )

    # GB folder with nested archive
    gb_dir = fixtures_dir / "GB"

    # Create inner archive first
    inner_zip = create_zip_archive(
        gb_dir, "Bomberman.zip", {"Bomberman.gb": b"ROM:Bomberman" + b"\0" * 1000}
    )

    # Create outer archive containing the inner archive
    create_zip_archive(
        gb_dir, "ZipInZip.zip", {"Bomberman.zip": inner_zip.read_bytes()}
    )

    # Clean up the inner file since it's now in the outer archive
    inner_zip.unlink()

    # SNES folder with image type detection
    snes_dir = fixtures_dir / "SNES"
    create_rom_file(snes_dir, "Super Mario World (USA).sfc")
    create_rom_file(snes_dir, "Zelda - A Link to the Past (USA).sfc")
    create_rom_file(snes_dir, "Metroid (USA).sfc")
    create_image_file(snes_dir / "mix", "Super Mario World.png")
    create_image_file(snes_dir / "box", "Zelda.png")
    create_image_file(snes_dir / "screenshot", "Metroid.png")

    print("Fixtures created successfully!")


if __name__ == "__main__":
    main()
