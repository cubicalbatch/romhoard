"""User-visible ROM diagnosis and privacy guarantees."""

import json
from io import StringIO
from zipfile import ZipFile

import pytest
from django.core.management import call_command

from library.models import Game, ROM, ROMSet, ScanJob, ScanPath, Setting, System
from library.system_loader import sync_systems


@pytest.mark.django_db
def test_diagnose_roms_explains_missing_and_wrong_systems_without_leaking_names(
    tmp_path,
):
    sync_systems()
    root = tmp_path / "private-user" / "roms"
    (root / "XBOX360").mkdir(parents=True)
    (root / "PSP").mkdir()
    (root / "unrecognized-private-folder").mkdir()
    (root / "NeoGeo").mkdir()
    xbox_file = root / "XBOX360" / "private-title.iso"
    xbox_file.write_bytes(b"private-content")
    (root / "PSP" / "missing-title.iso").write_bytes(b"psp")
    (root / "PSP" / "second-title.iso").write_bytes(b"psp")
    (root / "unrecognized-private-folder" / "lost-title.iso").write_bytes(b"unknown")
    (root / "unrecognized-private-folder" / "secret.private-extension").write_bytes(
        b"x"
    )
    with ZipFile(root / "NeoGeo" / "private-arcade.zip", "w") as zf:
        zf.writestr("private-arcade.bin", b"rom")

    ScanPath.objects.create(path=str(root))
    ScanJob.objects.create(
        path=str(root),
        task_id="private-task",
        errors=["private-password at /private/root"],
    )
    Setting.objects.create(key="screenscraper_username", value="private-credential")
    game = Game.objects.create(
        name="private-game",
        system=System.objects.get(slug="psp"),
        name_source="private-source",
    )
    ROM.objects.create(
        rom_set=ROMSet.objects.create(game=game),
        file_path=str(xbox_file),
        file_name=xbox_file.name,
        file_size=xbox_file.stat().st_size,
        crc32="deadbeef",
    )

    out = StringIO()
    call_command("diagnose_roms", stdout=out)
    raw = out.getvalue()
    report = json.loads(raw)
    files = report["scan_paths"][0]["files"]
    assert report["scan_paths"][0]["last_scan"]["error_count"] == 1
    assert any(
        f["reason"] == "folder"
        and f["predicted_system"] == "xbox360"
        and f.get("stored_system") == ["psp"]
        and f["system_mismatch"]
        for f in files
    )
    assert any(
        f["extension"] == ".iso"
        and f["reason"] == "no_folder_match"
        and f["status"] == "skipped"
        for f in files
    )
    assert any(
        f["predicted_system"] == "psp"
        and f["status"] == "not_imported"
        and f["count"] == 2
        for f in files
    )
    assert any(
        f["reason"] == "archive_as_rom" and f["predicted_system"] == "neogeo"
        for f in files
    )
    assert any(
        f["extension"] == "other" and f["reason"] == "no_folder_match" for f in files
    )
    assert all(
        secret not in raw
        for secret in (
            "private-user",
            "private-title",
            "private-game",
            "unrecognized-private-folder",
            "private-password",
            "private-credential",
            "deadbeef",
            "private-content",
            "private-extension",
            "private-source",
        )
    )

    out = StringIO()
    call_command("diagnose_roms", "--details", stdout=out, stderr=StringIO())
    detailed = out.getvalue()
    assert "XBOX360/private-title.iso" in detailed
    assert "deadbeef" in detailed
    assert "private-game" in detailed
    assert str(tmp_path) not in detailed
    assert "private-password" not in detailed


@pytest.mark.django_db
def test_diagnose_roms_archive_members_and_missing_records(tmp_path):
    sync_systems()
    root = tmp_path / "roms"
    (root / "PSP").mkdir(parents=True)
    with ZipFile(root / "PSP" / "bundle.zip", "w") as zf:
        zf.writestr("one.iso", b"one")
        zf.writestr("two.iso", b"two")
        zf.writestr("nested.zip", b"nested")
    psp = System.objects.get(slug="psp")
    game = Game.objects.create(name="Old", system=psp)
    ROM.objects.create(
        rom_set=ROMSet.objects.create(game=game),
        file_path=str(root / "PSP" / "gone.iso"),
        file_name="gone.iso",
        file_size=1,
    )
    out = StringIO()
    call_command("diagnose_roms", str(root), stdout=out)
    rows = json.loads(out.getvalue())["scan_paths"][0]["files"]
    assert (
        sum(
            row["count"]
            for row in rows
            if row["kind"] == "member" and row["predicted_system"] == "psp"
        )
        == 2
    )
    assert any(row["reason"] == "nested_archive" for row in rows)
    assert any(
        row["reason"] == "missing_on_disk" and row["stored_system"] == ["psp"]
        for row in rows
    )
    assert any(
        row["status"] == "not_imported" and row["kind"] == "member" for row in rows
    )


@pytest.mark.django_db
def test_diagnose_roms_exposes_ancestor_precedence_and_redacts_custom_alias(tmp_path):
    sync_systems()
    root = tmp_path / "XBOX360" / "roms"
    root.mkdir(parents=True)
    (root / "game.iso").write_bytes(b"rom")
    (root / "PSP").mkdir()
    (root / "PSP" / "game.iso").write_bytes(b"rom")
    out = StringIO()
    call_command("diagnose_roms", str(root), stdout=out)
    rows = json.loads(out.getvalue())["scan_paths"][0]["files"]
    # The scan-root ancestor alias classifies files with no nearer folder.
    ancestor = next(
        row for row in rows if row["folder_scope"] == "ancestor_of_scan_root"
    )
    assert ancestor["predicted_system"] == "xbox360"
    assert ancestor["folder_alias"] == "XBOX360"
    # A nearer folder inside the scan root wins over the ancestor alias.
    nested = next(row for row in rows if row["folder_scope"] == "within_scan_root")
    assert nested["predicted_system"] == "psp"
    assert nested["folder_alias"] == "PSP"

    psp = System.objects.get(slug="psp")
    psp.folder_names = ["private-custom-alias"]
    psp.save(update_fields=["folder_names"])
    (root / "private-custom-alias").mkdir()
    (root / "private-custom-alias" / "game.cso").write_bytes(b"rom")
    out = StringIO()
    call_command("diagnose_roms", str(root), stdout=out)
    raw = out.getvalue()
    assert "private-custom-alias" not in raw
    report = json.loads(raw)
    assert {"system": "psp", "fields": ["folder_names"]} in report["config_drift"]
    assert any(
        row["folder_alias"] == "custom_alias"
        for row in report["scan_paths"][0]["files"]
    )

    psp.delete()
    out = StringIO()
    call_command("diagnose_roms", str(root), stdout=out)
    assert {"system": "psp", "fields": ["missing_from_database"]} in json.loads(
        out.getvalue()
    )["config_drift"]
