"""Regressions for upload retries, invalid requests, and file preservation."""

from unittest.mock import patch

import pytest
from django.core.files.uploadedfile import SimpleUploadedFile

from library.models import UploadJob


pytestmark = pytest.mark.django_db


@pytest.mark.parametrize(
    "count,size",
    [
        ("bad", "1"),
        ("1", "bad"),
        ("-1", "1"),
        ("1", "-1"),
        (str(2**31), "1"),
        ("1", str(2**63)),
    ],
)
def test_invalid_upload_counts_do_not_create_jobs(client, count, size):
    with patch("library.upload.get_library_root", return_value="/library"):
        response = client.post(
            "/upload/start/", {"file_count": count, "total_size": size}
        )
    assert response.status_code == 400
    assert not UploadJob.objects.exists()


@pytest.mark.parametrize(
    "files",
    [
        [None],
        [1],
        [{"name": "game.rom", "size": "bad"}],
        [{"name": {}, "size": 1}],
        [{"name": "game.rom", "size": -1}],
    ],
)
def test_invalid_duplicate_checks_return_bad_request(client, files):
    response = client.post(
        "/upload/check-duplicates/", files, content_type="application/json"
    )
    assert response.status_code == 400


def test_upload_retry_preserves_first_file(client, tmp_path, settings):
    settings.UPLOAD_TEMP_DIR = str(tmp_path)
    job = UploadJob.objects.create(files_total=2)
    url = f"/upload/{job.pk}/file/"
    first = client.post(url, {"file": SimpleUploadedFile("game.rom", b"original")})
    retry = client.post(url, {"file": SimpleUploadedFile("game.rom", b"replacement")})
    assert first.status_code == 200
    assert retry.status_code == 409
    assert (tmp_path / str(job.pk) / "game.rom").read_bytes() == b"original"
    job.refresh_from_db()
    assert job.files_uploaded == 1
    assert job.bytes_uploaded == len(b"original")


def test_processing_job_rejects_new_files(client, tmp_path, settings):
    settings.UPLOAD_TEMP_DIR = str(tmp_path)
    job = UploadJob.objects.create(status=UploadJob.STATUS_PROCESSING)
    response = client.post(
        f"/upload/{job.pk}/file/", {"file": SimpleUploadedFile("game.rom", b"data")}
    )
    assert response.status_code == 400
    assert not list(tmp_path.iterdir())


def test_finalize_retry_only_enqueues_once_and_preserves_completed_job(client):
    job = UploadJob.objects.create(files_total=2, files_uploaded=1)
    with patch("library.tasks.process_upload_job.configure") as configure:
        configure.return_value.defer.return_value = 123
        first = client.post(f"/upload/{job.pk}/finalize/")
        retry = client.post(f"/upload/{job.pk}/finalize/")
        UploadJob.objects.filter(pk=job.pk).update(
            status=UploadJob.STATUS_COMPLETED, games_added=1
        )
        completed = client.post(f"/upload/{job.pk}/finalize/")
    assert first.json()["status"] == retry.json()["status"] == "processing"
    assert completed.json()["status"] == "completed"
    configure.return_value.defer.assert_called_once()
    job.refresh_from_db()
    assert job.games_added == 1


@pytest.mark.parametrize(
    "payload",
    [
        "invalid",
        "[]",
        '{"assignments": null}',
        '{"assignments": {}}',
        '{"assignments": {"unknown": "skip"}}',
    ],
)
def test_invalid_resolution_keeps_files_and_job_available(client, tmp_path, payload):
    rom = tmp_path / "unidentified.bin"
    rom.write_bytes(b"rom data")
    job = UploadJob.objects.create(
        status=UploadJob.STATUS_AWAITING_INPUT,
        unidentified_files=[{"temp_path": str(rom), "filename": rom.name}],
    )
    response = client.post(
        f"/upload/{job.pk}/resolve/", payload, content_type="application/json"
    )
    assert response.status_code == 400
    job.refresh_from_db()
    assert job.status == UploadJob.STATUS_AWAITING_INPUT
    assert job.unidentified_files
    assert rom.read_bytes() == b"rom data"


def test_unknown_system_keeps_upload_available(client, tmp_path):
    rom = tmp_path / "unidentified.bin"
    rom.write_bytes(b"rom data")
    job = UploadJob.objects.create(
        status=UploadJob.STATUS_AWAITING_INPUT,
        unidentified_files=[{"temp_path": str(rom), "filename": rom.name}],
    )
    response = client.post(
        f"/upload/{job.pk}/resolve/",
        {"assignments": {str(rom): "unknown-system"}},
        content_type="application/json",
    )
    assert response.status_code == 400
    job.refresh_from_db()
    assert job.status == UploadJob.STATUS_AWAITING_INPUT
    assert rom.exists()


def test_explicit_skip_completes_resolution(client, tmp_path, settings):
    settings.UPLOAD_TEMP_DIR = str(tmp_path)
    job = UploadJob.objects.create(status=UploadJob.STATUS_AWAITING_INPUT)
    folder = tmp_path / str(job.pk)
    folder.mkdir()
    rom = folder / "unidentified.bin"
    rom.write_bytes(b"rom data")
    job.unidentified_files = [{"temp_path": str(rom), "filename": rom.name}]
    job.save()
    response = client.post(
        f"/upload/{job.pk}/resolve/",
        {"assignments": {str(rom): "skip"}},
        content_type="application/json",
    )
    assert response.status_code == 200
    job.refresh_from_db()
    assert job.status == UploadJob.STATUS_COMPLETED
    assert job.games_skipped == 1
    assert not folder.exists()
