"""Disc bundle exports preserve references to companion ROM files."""

import zipfile
from pathlib import Path

import pytest

from library.download import create_romset_bundle
from library.models import ROM, Game, ROMSet, System
from library.multidownload import create_multi_game_bundle


@pytest.mark.django_db
@pytest.mark.parametrize("archived", [False, True])
@pytest.mark.parametrize("multi_game", [False, True])
@pytest.mark.parametrize("descriptor_extension", ["cue", "gdi", "m3u"])
def test_disc_bundle_preserves_companion_filenames(
    tmp_path, archived, multi_game, descriptor_extension
):
    system = System.objects.create(
        slug="disc-export",
        name="Disc export",
        extensions=[".bin", ".cue"],
        folder_names=[],
    )
    game = Game.objects.create(system=system, name="Adventure")
    rom_set = ROMSet.objects.create(game=game, source_path=str(tmp_path))
    game.default_rom_set = rom_set
    game.save()
    track_name = "Adventure (Disc 1).bin"
    descriptor_name = f"Adventure (Disc 1).{descriptor_extension}"
    contents = {
        track_name: b"disc contents",
        descriptor_name: {
            "cue": f'FILE "{track_name}" BINARY\n',
            "gdi": f'1\n1 0 4 2352 "{track_name}" 0\n',
            "m3u": f"{track_name}\n",
        }[descriptor_extension].encode(),
    }
    archive_path = tmp_path / "game.zip"
    if archived:
        with zipfile.ZipFile(archive_path, "w") as archive:
            for name, content in contents.items():
                archive.writestr(f"disc/{name}", content)
    for name, content in contents.items():
        if not archived:
            (tmp_path / name).write_bytes(content)
        ROM.objects.create(
            rom_set=rom_set,
            file_path=str(tmp_path / name),
            file_name=archive_path.name if archived else name,
            file_size=len(content),
            archive_path=str(archive_path) if archived else "",
            path_in_archive=f"disc/{name}" if archived else "",
            disc=1,
        )

    if multi_game:
        bundle_path, _ = create_multi_game_bundle([game])
        prefix = "Adventure/"
    else:
        bundle_path, _ = create_romset_bundle(rom_set)
        prefix = ""
    try:
        with zipfile.ZipFile(bundle_path) as bundle:
            assert set(bundle.namelist()) == {prefix + name for name in contents}
            descriptor = bundle.read(prefix + descriptor_name).decode()
            assert track_name in descriptor
            assert bundle.read(prefix + track_name) == b"disc contents"
    finally:
        Path(bundle_path).unlink()
