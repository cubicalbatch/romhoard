"""Regressions for import integrity and independent collection artifacts."""

import copy
import json
import zipfile
from pathlib import Path
from unittest.mock import patch

import pytest
from django.urls import reverse
from PIL import Image

from romcollections.models import Collection, CollectionEntry, CoverJob, ExportJob
from romcollections.serializers import (
    ImportError,
    import_collection,
    validate_import_data,
)
from romcollections.tasks import create_collection_export, process_cover_upload


def collection_data():
    return {
        "romhoard_collection": {"version": "1.0"},
        "collection": {"slug": "favorites", "name": "Favorites", "creator": "alice"},
        "entries": [{"game_name": "Example", "system_slug": "unknown-system"}],
    }


@pytest.mark.parametrize("value", [None, [], "slug name", 1])
def test_import_rejects_non_object_collection(value):
    data = collection_data()
    data["collection"] = value
    with pytest.raises(ImportError, match="must be an object"):
        validate_import_data(data)


@pytest.mark.parametrize("slug", ["../outside", "/outside", "", "a" * 51, ["slug"]])
def test_import_rejects_invalid_collection_slug(slug):
    data = collection_data()
    data["collection"]["slug"] = slug
    with pytest.raises(ImportError):
        validate_import_data(data)


@pytest.mark.parametrize(
    "field,value",
    [
        ("game_name", None),
        ("system_slug", []),
        ("position", -1),
        ("position", True),
        ("notes", {}),
        ("screenscraper_id", "123"),
    ],
)
def test_import_rejects_invalid_entry_fields(field, value):
    data = collection_data()
    data["entries"][0][field] = value
    with pytest.raises(ImportError):
        validate_import_data(data)


@pytest.mark.django_db
def test_null_creator_still_imports_into_local_namespace():
    data = collection_data()
    data["collection"]["slug"] = "null-creator"
    data["collection"]["creator"] = None
    result = import_collection(data)
    assert result["collection"].creator == "local"


@pytest.mark.django_db
def test_duplicate_import_preserves_existing_entries():
    original = import_collection(collection_data())["collection"]
    data = collection_data()
    data["collection"]["name"] = "Replacement"
    data["entries"] = [
        {"game_name": "Replacement", "system_slug": "unknown-system"},
        {"game_name": "Replacement", "system_slug": "unknown-system"},
    ]

    with pytest.raises(ImportError, match="duplicate entry"):
        import_collection(data, overwrite=True)

    original.refresh_from_db()
    assert original.name == "Favorites"
    assert list(original.entries.values_list("game_name", flat=True)) == ["Example"]


@pytest.mark.django_db
def test_failed_import_rolls_back_all_replacement_entries(monkeypatch):
    original = import_collection(collection_data())["collection"]
    data = collection_data()
    data["collection"]["name"] = "Replacement"
    data["entries"].append({"game_name": "Second", "system_slug": "unknown-system"})
    real_create = CollectionEntry.objects.create

    def fail_second(**kwargs):
        if kwargs["game_name"] == "Second":
            raise RuntimeError("simulated database failure")
        return real_create(**kwargs)

    monkeypatch.setattr(CollectionEntry.objects, "create", fail_second)
    with pytest.raises(RuntimeError, match="simulated database failure"):
        import_collection(data, overwrite=True)

    original.refresh_from_db()
    assert original.name == "Favorites"
    assert list(original.entries.values_list("game_name", flat=True)) == ["Example"]


@pytest.mark.django_db
def test_same_slug_creators_keep_separate_covers(tmp_path, settings):
    settings.IMAGE_STORAGE_PATH = str(tmp_path / "images")
    collections = []
    for creator, color in (("alice", "red"), ("bob", "blue")):
        collection = Collection.objects.create(
            slug="favorites", name=creator, creator=creator
        )
        source = tmp_path / f"{creator}.png"
        Image.new("RGB", (10, 10), color=color).save(source)
        job = CoverJob.objects.create(collection=collection, upload_path=str(source))
        process_cover_upload(job.pk)
        collection.refresh_from_db()
        collections.append(collection)

    assert collections[0].cover_image_path != collections[1].cover_image_path
    with Image.open(collections[0].cover_image_path) as image:
        assert image.convert("RGB").getpixel((0, 0)) == (255, 0, 0)
    collections[1].delete_cover()
    assert Path(collections[0].cover_image_path).exists()


@pytest.mark.django_db
@pytest.mark.parametrize("delete_collection", [False, True])
def test_removing_a_legacy_shared_cover_preserves_other_collection(
    tmp_path, delete_collection
):
    shared_path = tmp_path / "shared.png"
    shared_path.write_bytes(b"legacy cover")
    collections = [
        Collection.objects.create(
            slug="favorites",
            name=creator,
            creator=creator,
            cover_image_path=str(shared_path),
            has_cover=True,
        )
        for creator in ("alice", "bob")
    ]
    if delete_collection:
        collections[0].delete()
    else:
        collections[0].delete_cover()
    assert shared_path.read_bytes() == b"legacy cover"
    collections[1].refresh_from_db()
    assert collections[1].has_cover is True


@pytest.mark.django_db
def test_same_slug_creators_keep_separate_exports(tmp_path, settings):
    settings.IMAGE_STORAGE_PATH = str(tmp_path)
    jobs = []
    for creator in ("alice", "bob"):
        collection = Collection.objects.create(
            slug="favorites", name=creator, creator=creator
        )
        job = ExportJob.objects.create(collection=collection)
        create_collection_export(job.pk)
        job.refresh_from_db()
        jobs.append(job)

    assert jobs[0].file_path != jobs[1].file_path
    for job, creator in zip(jobs, ("alice", "bob")):
        with zipfile.ZipFile(job.file_path) as archive:
            data = json.loads(archive.read("collection.json"))
            assert data["collection"]["creator"] == creator


@pytest.mark.django_db
def test_failed_export_preserves_previous_download(tmp_path, settings):
    settings.IMAGE_STORAGE_PATH = str(tmp_path)
    collection = Collection.objects.create(
        slug="favorites", name="Favorites", creator="alice"
    )
    previous = ExportJob.objects.create(collection=collection)
    create_collection_export(previous.pk)
    previous.refresh_from_db()
    contents = Path(previous.file_path).read_bytes()
    replacement = ExportJob.objects.create(collection=collection)

    with patch(
        "romcollections.tasks.serialize_export", side_effect=ValueError("failed")
    ):
        with pytest.raises(ValueError, match="failed"):
            create_collection_export(replacement.pk)

    assert ExportJob.objects.filter(pk=previous.pk).exists()
    assert Path(previous.file_path).read_bytes() == contents
    assert collection.get_latest_export().pk == previous.pk


@pytest.mark.django_db
def test_export_cleanup_preserves_a_legacy_file_shared_by_another_collection(
    tmp_path, settings
):
    settings.IMAGE_STORAGE_PATH = str(tmp_path)
    shared_path = tmp_path / "legacy-shared.zip"
    shared_path.write_bytes(b"legacy export")
    collections = [
        Collection.objects.create(slug="favorites", name=creator, creator=creator)
        for creator in ("alice", "bob")
    ]
    for collection in collections:
        ExportJob.objects.create(
            collection=collection,
            status=ExportJob.STATUS_COMPLETED,
            file_path=str(shared_path),
        )
    replacement = ExportJob.objects.create(collection=collections[0])
    create_collection_export(replacement.pk)
    assert shared_path.read_bytes() == b"legacy export"
    assert collections[1].export_jobs.filter(file_path=str(shared_path)).exists()


@pytest.mark.django_db
def test_export_enqueue_preserves_fast_worker_result(client):
    Collection.objects.create(slug="favorites", name="Favorites", creator="alice")

    def complete(**kwargs):
        ExportJob.objects.filter(pk=kwargs["export_job_id"]).update(
            status=ExportJob.STATUS_COMPLETED, file_size=123
        )
        return 42

    with patch("romcollections.views.create_collection_export.configure") as configure:
        configure.return_value.defer.side_effect = complete
        response = client.post(
            reverse(
                "romcollections:start_export_with_images", args=["alice", "favorites"]
            )
        )

    job = ExportJob.objects.get(pk=response.json()["job_id"])
    assert job.status == ExportJob.STATUS_COMPLETED
    assert job.file_size == 123
    assert job.task_id == "42"


@pytest.mark.django_db
def test_import_preview_checks_creator_namespace(client):
    Collection.objects.create(slug="favorites", name="Bobs", creator="bob")
    session = client.session
    session["import_collection_test"] = {
        "data": copy.deepcopy(collection_data()),
        "url": "https://example.com/list.json",
        "overwrite": False,
    }
    session.save()
    response = client.get(
        reverse("romcollections:import_collection_preview", args=["test"])
    )
    assert response.context["existing_collection"] is None


@pytest.mark.django_db
@pytest.mark.parametrize("field,value", [("slug", "another"), ("creator", "bob")])
@pytest.mark.parametrize("as_zip", [False, True])
def test_sync_identity_change_cannot_replace_another_collection(
    client, field, value, as_zip
):
    original = import_collection(collection_data())["collection"]
    original.source_url = (
        "https://example.com/list.zip?version=2"
        if as_zip
        else "https://example.com/list.json"
    )
    original.save(update_fields=["source_url"])
    unrelated_data = collection_data()
    unrelated_data["collection"][field] = value
    unrelated = import_collection(unrelated_data)["collection"]
    incoming = copy.deepcopy(unrelated_data)
    incoming["entries"] = [
        {"game_name": "Replacement", "system_slug": "unknown-system"}
    ]

    def download_zip(url, path):
        with zipfile.ZipFile(path, "w") as archive:
            archive.writestr("collection.json", json.dumps(incoming))

    with (
        patch("romhoard.url_fetch.fetch_json_from_url", return_value=incoming),
        patch("romhoard.url_fetch.fetch_zip_from_url", side_effect=download_zip),
    ):
        response = client.post(
            reverse("romcollections:sync_collection", args=["alice", "favorites"])
        )

    assert response.status_code == 302
    for collection in (original, unrelated):
        assert list(collection.entries.values_list("game_name", flat=True)) == [
            "Example"
        ]
    from django.contrib.messages import get_messages

    assert any(
        "different collection" in str(message)
        for message in get_messages(response.wsgi_request)
    )


@pytest.mark.django_db
def test_zip_source_sync_updates_the_existing_collection(client):
    original = import_collection(collection_data())["collection"]
    original.source_url = "https://example.com/list.zip?version=2"
    original.save(update_fields=["source_url"])
    incoming = collection_data()
    incoming["entries"] = [
        {"game_name": "Replacement", "system_slug": "unknown-system"}
    ]
    downloaded_paths = []

    def download_zip(url, path):
        downloaded_paths.append(path)
        with zipfile.ZipFile(path, "w") as archive:
            archive.writestr("collection.json", json.dumps(incoming))

    with (
        patch("romhoard.url_fetch.fetch_zip_from_url", side_effect=download_zip),
        patch("romhoard.url_fetch.fetch_json_from_url") as json_fetch,
    ):
        response = client.post(
            reverse("romcollections:sync_collection", args=["alice", "favorites"])
        )

    assert response.status_code == 302
    json_fetch.assert_not_called()
    assert list(original.entries.values_list("game_name", flat=True)) == ["Replacement"]
    original.refresh_from_db()
    assert original.last_synced_at is not None
    assert all(not Path(path).exists() for path in downloaded_paths)


@pytest.mark.django_db
def test_invalid_zip_sync_preserves_existing_collection(client):
    original = import_collection(collection_data())["collection"]
    original.source_url = "https://example.com/list.zip"
    original.save(update_fields=["source_url"])
    downloaded_paths = []

    def download_zip(url, path):
        downloaded_paths.append(path)
        with zipfile.ZipFile(path, "w") as archive:
            archive.writestr("../unsafe.txt", "unsafe")
            archive.writestr("collection.json", json.dumps(collection_data()))

    with patch("romhoard.url_fetch.fetch_zip_from_url", side_effect=download_zip):
        response = client.post(
            reverse("romcollections:sync_collection", args=["alice", "favorites"])
        )

    assert response.status_code == 302
    assert list(original.entries.values_list("game_name", flat=True)) == ["Example"]
    assert all(not Path(path).exists() for path in downloaded_paths)
