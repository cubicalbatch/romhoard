"""Regression coverage for moving metadata, including manually copied folders."""

from pathlib import Path
from unittest.mock import Mock, patch

import pytest
from django.urls import reverse

from library.image_utils import get_downloaded_images_stats
from library.metadata.matcher import download_images_for_game
from library.models import Game, GameImage, ImageMigrationJob, Setting, System
from library.tasks import run_image_migration

pytestmark = pytest.mark.django_db


@pytest.fixture
def storage(tmp_path, settings):
    old, new = tmp_path / "old", tmp_path / "new"
    old.mkdir()
    settings.IMAGE_STORAGE_PATH = str(old)
    system = System.objects.create(
        name="Audit", slug="audit", extensions=[], folder_names=[]
    )
    game = Game.objects.create(name="Sample", system=system)
    return old, new, game


def write(path, content=b"image"):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(content)
    return path


def register(game, path, source="downloaded", image_type="cover"):
    return GameImage.objects.create(
        game=game,
        file_path=str(path),
        file_name=path.name,
        file_size=path.stat().st_size,
        image_type=image_type,
        source=source,
    )


def migrate(old, new, action="move"):
    job = ImageMigrationJob.objects.create(
        action=action, old_path=str(old), new_path=str(new)
    )
    result = run_image_migration(Mock(should_abort=Mock(return_value=False)), job.pk)
    job.refresh_from_db()
    return result, job


def test_inventory_counts_cached_unregistered_json_and_missing(storage):
    old, new, game = storage
    cover = write(old / "audit/Sample/box-2D.png", b"c" * 800)
    register(game, cover, source="scanned")
    write(old / "audit/Sample/mixrbv1.png", b"m" * 200)
    write(old / "audit/Sample/metadata.json", b"{}")
    missing = write(old / "audit/Sample/ss.png", b"s" * 50)
    register(game, missing, image_type="screenshot")
    missing.unlink()
    stats = get_downloaded_images_stats()
    assert stats == {
        "count": 4,
        "size_bytes": 1002,
        "size_human": "1002.0 B",
        "missing_count": 1,
    }
    write(new / missing.relative_to(old), b"s" * 50)
    stats = get_downloaded_images_stats(old, new)
    assert stats["size_bytes"] == 1052
    assert stats["missing_count"] == 0


@pytest.mark.parametrize("layout", ["original", "copied", "moved"])
def test_move_relinks_images_icons_and_preserves_cache(storage, client, layout):
    old, new, game = storage
    cover = write(old / "audit/Sample/box-2D.png")
    image = register(game, cover, source="scanned")
    icon = write(old / "systems/audit/icon.png", b"icon")
    game.system.icon_path = str(icon)
    game.system.save()
    cache = write(old / "audit/Sample/metadata.json", b'{"id":123}')
    unregistered = write(old / "audit/Sample/mixrbv1.png", b"mix")
    paths = [cover, icon, cache, unregistered]
    contents = {path: path.read_bytes() for path in paths}
    if layout != "original":
        for path in paths:
            write(new / path.relative_to(old), contents[path])
            if layout == "moved":
                path.unlink()
    result, job = migrate(old, new)
    assert job.status == "completed"
    assert result["failed"] == 0
    image.refresh_from_db()
    game.system.refresh_from_db()
    assert image.file_path == str(new / cover.relative_to(old))
    assert game.system.icon_path == str(new / icon.relative_to(old))
    for path in paths:
        assert (new / path.relative_to(old)).read_bytes() == contents[path]
        assert not path.exists()
    response = client.get(reverse("library:serve_image", args=[image.pk]))
    assert b"".join(response.streaming_content) == b"image"
    response.close()


def test_same_size_different_destination_is_preserved_and_reported(storage):
    old, new, game = storage
    cover = write(old / "box-2D.png", b"first")
    image = register(game, cover)
    destination = write(new / "box-2D.png", b"other")
    good = write(old / "metadata.json", b"{}")
    result, job = migrate(old, new)
    assert result["failed"] == 1
    assert job.status == "failed"
    assert "different file" in job.error_message
    assert cover.read_bytes() == b"first"
    assert destination.read_bytes() == b"other"
    image.refresh_from_db()
    assert image.file_path == str(cover)
    assert (new / good.name).read_bytes() == b"{}"


def test_missing_both_copies_keeps_reference_and_reports_failure(storage):
    old, new, game = storage
    path = write(old / "cover.png")
    image = register(game, path)
    path.unlink()
    result, job = migrate(old, new)
    assert result["failed"] == 1
    assert job.status == "failed"
    assert "Source file not found" in job.error_message
    image.refresh_from_db()
    assert image.file_path == str(path)


@pytest.mark.parametrize("action", ["move", "delete", "orphan"])
def test_operations_are_scoped_to_root_and_do_not_follow_symlinks(storage, action):
    old, new, game = storage
    managed = write(old / "cover.png")
    managed_image = register(game, managed, source="scanned")
    outside = write(old.parent / "roms/cover.png", b"outside")
    outside_image = register(game, outside)
    sibling = write(old.parent / "old-other/cover.png", b"sibling")
    sibling_image = register(game, sibling)
    (old / "linked.png").symlink_to(outside)
    (old / "linked-dir").symlink_to(outside.parent, target_is_directory=True)
    assert get_downloaded_images_stats()["count"] == 1
    result, job = migrate(old, new, action)
    assert result["processed"] == 1
    assert outside.read_bytes() == b"outside"
    assert sibling.read_bytes() == b"sibling"
    assert GameImage.objects.filter(pk=outside_image.pk).exists()
    assert GameImage.objects.filter(pk=sibling_image.pk).exists()
    if action == "orphan":
        assert managed.exists()
        assert not GameImage.objects.filter(pk=managed_image.pk).exists()
    elif action == "delete":
        assert not managed.exists()
        assert not GameImage.objects.filter(pk=managed_image.pk).exists()


def test_delete_failure_preserves_reference(storage):
    old, new, game = storage
    image = register(game, write(old / "cover.png"))
    with patch(
        "library.image_utils.delete_downloaded_image", return_value="Permission denied"
    ):
        result, job = migrate(old, new, "delete")
    assert job.status == "failed"
    assert result["failed"] == 1
    assert GameImage.objects.filter(pk=image.pk).exists()
    assert Path(image.file_path).exists()


def test_destination_symlink_is_not_followed(storage):
    old, new, game = storage
    source = write(old / "cover.png")
    register(game, source)
    target = write(old.parent / "elsewhere.png", b"elsewhere")
    new.mkdir()
    (new / "cover.png").symlink_to(target)
    result, job = migrate(old, new)
    assert result["failed"] == 1
    assert source.exists()
    assert target.read_bytes() == b"elsewhere"


def test_destination_record_for_same_game_is_reused(storage):
    old, new, game = storage
    source = write(old / "cover.png")
    original = register(game, source)
    destination = register(game, write(new / "cover.png"))
    result, job = migrate(old, new)
    assert result["failed"] == 0
    assert not GameImage.objects.filter(pk=original.pk).exists()
    assert GameImage.objects.filter(pk=destination.pk).exists()
    assert not source.exists()


def test_destination_record_for_other_game_is_not_stolen(storage):
    old, new, game = storage
    source = write(old / "cover.png")
    original = register(game, source)
    other = Game.objects.create(name="Other", system=game.system)
    destination = register(other, write(new / "cover.png"))
    result, job = migrate(old, new)
    assert result["failed"] == 1
    assert "another game" in job.error_message
    assert source.exists()
    original.refresh_from_db()
    destination.refresh_from_db()
    assert original.file_path == str(source)
    assert destination.game_id == other.pk


@pytest.mark.parametrize("relative", ["nested", "parent"])
def test_nested_storage_paths_rejected_without_changing_files(storage, relative):
    old, new, game = storage
    new = old / "child" if relative == "nested" else old.parent
    path = write(old / "cover.png")
    with pytest.raises(ValueError, match="non-nested"):
        migrate(old, new)
    assert path.exists()


def test_progress_counts_unregistered_files_and_json(storage):
    old, new, game = storage
    for i in range(55):
        write(old / str(i) / "metadata.json", b"{}")
    result, job = migrate(old, new)
    assert job.total_images == job.processed_images == 55
    assert job.progress_percent == 100
    assert result["failed"] == 0


@pytest.mark.parametrize("reset_default", [False, True])
def test_settings_migrate_effective_default_path(
    storage, client, settings, reset_default
):
    old, new, game = storage
    if reset_default:
        settings.IMAGE_STORAGE_PATH = str(new)
        Setting.set("metadata_image_path", str(old))
    image = register(game, write(old / "cover.png"))
    payload = {
        "save_image_settings": "1",
        "image_path": "" if reset_default else str(new),
    }
    response = client.post(reverse("library:metadata"), payload)
    assert response.status_code == 200
    assert response.context["migration_old_path"] == str(old)
    assert response.context["migration_new_path"] == str(new)
    assert response.context["migration_stats"]["count"] == 1
    with patch("library.tasks.run_image_migration.defer", return_value=123):
        response = client.post(
            reverse("library:metadata"), {**payload, "image_action": "move"}
        )
    assert response.status_code == 302
    job = ImageMigrationJob.objects.get()
    assert job.old_path == str(old)
    assert job.new_path == str(new)
    run_image_migration(Mock(should_abort=Mock(return_value=False)), job.pk)
    image.refresh_from_db()
    assert image.file_path == str(new / "cover.png")


@pytest.mark.parametrize(
    "invalid", ["relative/path", "nested", "bad-action", "active-job"]
)
def test_invalid_settings_do_not_queue_migration(storage, client, invalid):
    old, new, game = storage
    Setting.set("metadata_image_path", str(old))
    payload = {
        "save_image_settings": "1",
        "image_path": str(new),
        "image_action": "move",
    }
    if invalid == "relative/path":
        payload["image_path"] = invalid
    elif invalid == "nested":
        payload["image_path"] = str(old / "nested")
    elif invalid == "bad-action":
        payload["image_action"] = "typo"
    else:
        ImageMigrationJob.objects.create(
            action="move", old_path=str(old), new_path=str(new)
        )
    with patch("library.tasks.run_image_migration.defer") as defer:
        response = client.post(reverse("library:metadata"), payload)
    assert response.status_code == 302
    defer.assert_not_called()
    assert Setting.get("metadata_image_path") == str(old)


@pytest.mark.parametrize("enabled", [True, False])
def test_mix_setting_controls_future_downloads(storage, client, enabled):
    old, new, game = storage
    payload = {"save_media_settings": "1"}
    if enabled:
        payload["download_mix"] = "1"
    assert client.post(reverse("library:metadata"), payload).status_code == 302
    assert Setting.get("metadata_download_mix") is enabled

    def download(url, path):
        Path(path).write_bytes(b"image")
        return ".png"

    with patch("library.metadata.matcher.ScreenScraperClient") as api:
        api.return_value.download_image.side_effect = download
        count = download_images_for_game(
            game,
            [
                {"type": "box-2D", "url": "https://example.invalid/box"},
                {"type": "mixrbv1", "url": "https://example.invalid/mix"},
            ],
        )
    assert count == (2 if enabled else 1)
    assert game.images.filter(image_type="mix").exists() is enabled
    assert game.images.filter(image_type="cover").exists()


def test_disabling_mix_keeps_existing_artwork_and_default_is_enabled(storage, client):
    old, new, game = storage
    assert client.get(reverse("library:metadata")).context["download_mix"] is True
    path = write(old / "audit/Sample/mixrbv1.png")
    image = register(game, path, image_type="mix")
    client.post(reverse("library:metadata"), {"save_media_settings": "1"})
    assert path.exists()
    assert GameImage.objects.filter(pk=image.pk).exists()


def test_storage_root_symlink_keeps_image_references_in_inventory(storage):
    old, new, game = storage
    alias = old.parent / "alias"
    alias.symlink_to(old, target_is_directory=True)
    image = register(game, write(alias / "cover.png"))
    Setting.set("metadata_image_path", str(alias))
    assert get_downloaded_images_stats()["count"] == 1
    result, job = migrate(alias, new)
    assert result["failed"] == 0
    image.refresh_from_db()
    assert image.file_path == str(new / "cover.png")


def test_removed_original_directory_can_be_relinked(storage):
    old, new, game = storage
    image = register(game, write(old / "cover.png"))
    old.rename(new)
    result, job = migrate(old, new)
    assert result["failed"] == 0
    image.refresh_from_db()
    assert image.file_path == str(new / "cover.png")


def test_unreadable_inventory_is_reported_without_changing_settings(storage, client):
    old, new, game = storage
    Setting.set("metadata_image_path", str(old))
    with patch(
        "library.metadata_storage.os.walk", side_effect=PermissionError("denied")
    ):
        response = client.post(
            reverse("library:metadata"),
            {
                "save_image_settings": "1",
                "image_path": str(new),
                "image_action": "move",
            },
        )
    assert response.status_code == 302
    assert Setting.get("metadata_image_path") == str(old)
    assert not ImageMigrationJob.objects.exists()


@pytest.mark.parametrize("action", ["move", "delete", "orphan"])
def test_legacy_empty_source_does_not_treat_working_directory_as_metadata(
    storage, action
):
    old, new, game = storage
    image = register(game, write(old / "cover.png"))
    with pytest.raises(ValueError, match="absolute path"):
        migrate("", new, action)
    assert Path(image.file_path).exists()
    assert GameImage.objects.filter(pk=image.pk).exists()
