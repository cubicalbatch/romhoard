"""Systems flagged skip_checksum (Switch) never hash their ROM files.

Whole-file CRC32 of multi-gigabyte Switch dumps costs hours of disk reads and
almost never identifies anything ScreenScraper's romnom/name lookups miss.
"""

from unittest.mock import patch

import pytest

from library import archive
from library.models import ROM, Game, ROMSet, System, UploadJob
from library.scanner import scan_directory
from library.system_loader import sync_systems
from library.tasks import _process_uploaded_file, identify_game, queue_game_followup


@pytest.fixture
def systems(db):  # noqa: ARG001
    sync_systems()
    return System.objects.get(slug="switch"), System.objects.get(slug="nes")


def test_only_switch_skips_checksums(systems):
    assert list(
        System.objects.filter(skip_checksum=True).values_list("slug", flat=True)
    ) == ["switch"]


def test_scan_skips_hashing_for_switch_only(tmp_path, systems):
    (tmp_path / "Switch").mkdir()
    (tmp_path / "Switch" / "Big Game [0100000000010000][v0].nsp").write_bytes(b"x" * 64)
    (tmp_path / "NES").mkdir()
    (tmp_path / "NES" / "Small Game (USA).nes").write_bytes(b"rom")

    with (
        patch.object(
            archive, "compute_file_crc32", wraps=archive.compute_file_crc32
        ) as crc,
        patch("library.tasks.queue_game_followup", return_value=(False, False)),
    ):
        scan_directory(str(tmp_path), fetch_metadata=False)

    switch_rom = ROM.objects.get(rom_set__game__system__slug="switch")
    nes_rom = ROM.objects.get(rom_set__game__system__slug="nes")
    assert switch_rom.crc32 == ""
    assert nes_rom.crc32
    hashed = [call.args[0] for call in crc.call_args_list]
    assert hashed == [str(tmp_path / "NES" / "Small Game (USA).nes")]


def test_upload_skips_hashing_for_switch(tmp_path, systems):
    switch, _ = systems
    temp = tmp_path / "upload.nsp"
    temp.write_bytes(b"x" * 64)
    job = UploadJob.objects.create(task_id="t")

    with (
        patch("library.upload.get_library_root", return_value=str(tmp_path / "lib")),
        patch("library.tasks.queue_game_followup", return_value=(False, False)),
        patch("library.archive.compute_file_crc32") as crc,
    ):
        result = _process_uploaded_file(
            str(temp), "Big Game.nsp", switch, str(tmp_path / "lib"), job, False
        )

    assert result["success"], result
    crc.assert_not_called()
    assert ROM.objects.get().crc32 == ""


def test_followup_skips_hasheous_without_checksums(systems):
    switch, _ = systems
    game = Game.objects.create(name="Big Game", system=switch)
    ROMSet.objects.create(game=game)

    with patch.object(identify_game, "configure") as configure:
        queued = queue_game_followup(game, use_hasheous=True, fetch_metadata=False)

    assert queued == (False, False)
    configure.assert_not_called()
