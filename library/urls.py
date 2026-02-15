from django.urls import path

from . import views

app_name = "library"

urlpatterns = [
    path("", views.system_list, name="system_list"),
    path("search/", views.global_search, name="global_search"),
    path("filter-options/systems/", views.filter_systems, name="filter_systems"),
    path("filter-options/genres/", views.filter_genres, name="filter_genres"),
    path("scan/", views.scan_form, name="scan"),
    path("scan/status/", views.scan_status, name="scan_status"),
    path("scan/cancel/<int:job_id>/", views.cancel_scan_job, name="cancel_scan_job"),
    path("scan/path/<int:pk>/rescan/", views.rescan_path, name="rescan_path"),
    path(
        "scan/path/<int:pk>/toggle-hasheous/",
        views.toggle_hasheous_path,
        name="toggle_hasheous_path",
    ),
    path(
        "scan/path/<int:pk>/toggle-fetch-metadata/",
        views.toggle_fetch_metadata_path,
        name="toggle_fetch_metadata_path",
    ),
    path("scan/path/<int:pk>/delete/", views.delete_scan_path, name="delete_scan_path"),
    path(
        "scan/path/<int:pk>/delete-info/",
        views.scan_path_delete_info,
        name="scan_path_delete_info",
    ),
    path(
        "scan/path/<int:pk>/schedule/",
        views.update_scan_schedule,
        name="update_scan_schedule",
    ),
    path("clear/", views.clear_library, name="clear_library"),
    path("image/<int:pk>/", views.serve_image, name="serve_image"),
    path("system-icon/<slug:slug>/", views.serve_system_icon, name="serve_system_icon"),
    path("download/rom/<int:pk>/", views.download_rom, name="download_rom"),
    path(
        "download/romset/<int:pk>/picker/",
        views.romset_download_picker,
        name="romset_download_picker",
    ),
    path(
        "download/romset/<int:pk>/start/",
        views.start_romset_download,
        name="start_romset_download",
    ),
    path("download/romset/<int:pk>/", views.download_romset, name="download_romset"),
    path("download/game/<int:pk>/", views.download_game, name="download_game"),
    path(
        "download/status/<int:job_id>/", views.download_status, name="download_status"
    ),
    path(
        "download/bundle/<int:job_id>/",
        views.serve_download_bundle,
        name="serve_download_bundle",
    ),
    path("preview-games/", views.preview_games, name="preview_games"),
    # Settings URLs
    path("settings/", views.metadata_page, name="metadata"),
    path("settings/status/", views.metadata_status, name="metadata_status"),
    path(
        "settings/image-migration-status/",
        views.image_migration_status,
        name="image_migration_status",
    ),
    path(
        "settings/metadata/start/", views.start_metadata_job, name="start_metadata_job"
    ),
    path(
        "settings/screenscraper/clear-pause/",
        views.clear_screenscraper_pause,
        name="clear_screenscraper_pause",
    ),
    path(
        "settings/screenscraper/revalidate/",
        views.revalidate_screenscraper,
        name="revalidate_screenscraper",
    ),
    path(
        "settings/metadata/cancel/<int:batch_id>/",
        views.cancel_metadata_batch,
        name="cancel_metadata_batch",
    ),
    path(
        "settings/metadata/fetch-game/<int:pk>/",
        views.fetch_game_metadata,
        name="fetch_game_metadata",
    ),
    path(
        "settings/metadata/system/<slug:system_slug>/missing/",
        views.games_missing_metadata,
        name="games_missing_metadata",
    ),
    path(
        "games/<int:pk>/hash-lookup/",
        views.hash_lookup,
        name="hash_lookup",
    ),
    path(
        "games/<int:pk>/set-ssid/",
        views.set_screenscraper_id,
        name="set_screenscraper_id",
    ),
    path(
        "games/<int:pk>/delete/",
        views.delete_game,
        name="delete_game",
    ),
    path(
        "games/<int:pk>/rename/",
        views.rename_game,
        name="rename_game",
    ),
    path(
        "games/<int:pk>/edit/",
        views.edit_game,
        name="edit_game",
    ),
    path(
        "games/<int:pk>/merge-search/",
        views.game_search_for_merge,
        name="game_merge_search",
    ),
    path(
        "games/<int:pk>/merge/",
        views.merge_game,
        name="merge_game",
    ),
    path(
        "games/<int:pk>/delete-image/",
        views.delete_game_image,
        name="delete_game_image",
    ),
    path(
        "settings/systems/fetch/",
        views.fetch_system_metadata,
        name="fetch_system_metadata",
    ),
    path(
        "settings/systems/status/",
        views.system_metadata_status,
        name="system_metadata_status",
    ),
    path(
        "settings/systems/cancel/<int:job_id>/",
        views.cancel_system_metadata_job,
        name="cancel_system_metadata_job",
    ),
    path("games/<int:pk>/", views.game_detail, name="game_detail"),
    path("library/<slug:slug>/", views.game_list, name="game_list"),
    path("library/<slug:slug>/search/", views.game_search, name="game_search"),
    path(
        "library/<slug:slug>/multi-download/",
        views.start_multi_download,
        name="start_multi_download",
    ),
    # Send (FTP/SFTP) URLs
    path("library/<slug:slug>/send/", views.start_send, name="start_send"),
    path("send/status/<int:job_id>/", views.send_status, name="send_status"),
    # Upload URLs
    path("upload/", views.upload_page, name="upload"),
    path("upload/start/", views.start_upload, name="start_upload"),
    path("upload/check-duplicates/", views.check_duplicates, name="check_duplicates"),
    path("upload/<int:job_id>/file/", views.upload_file, name="upload_file"),
    path(
        "upload/<int:job_id>/finalize/", views.finalize_upload, name="finalize_upload"
    ),
    path("upload/<int:job_id>/status/", views.upload_status, name="upload_status"),
    path(
        "upload/<int:job_id>/resolve/",
        views.resolve_unidentified,
        name="resolve_unidentified",
    ),
]
