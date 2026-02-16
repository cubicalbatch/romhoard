"""Library views module.

Re-exports all view functions for URL routing compatibility.
"""

# Browse views
from .browse import (
    game_detail,
    game_list,
    game_search,
    global_search,
    home,
    system_list,
)

# Download views
from .download import (
    download_game,
    download_rom,
    download_romset,
    download_status,
    preview_games,
    romset_download_picker,
    serve_download_bundle,
    serve_image,
    serve_system_icon,
    start_multi_download,
    start_romset_download,
)

# Filter views
from .filters import (
    filter_genres,
    filter_systems,
)

# Game views
from .game import (
    delete_game,
    delete_game_image,
    edit_game,
    game_search_for_merge,
    merge_game,
    rename_game,
)

# Metadata views
from .metadata import (
    cancel_metadata_batch,
    cancel_system_metadata_job,
    clear_screenscraper_pause,
    fetch_game_metadata,
    fetch_system_metadata,
    games_missing_metadata,
    hash_lookup,
    image_migration_status,
    metadata_page,
    metadata_status,
    revalidate_screenscraper,
    save_region_preferences,
    set_screenscraper_id,
    start_metadata_job,
    system_metadata_status,
)

# Scan views
from .scan import (
    cancel_scan_job,
    clear_library,
    delete_scan_path,
    rescan_path,
    scan_form,
    scan_path_delete_info,
    scan_status,
    toggle_fetch_metadata_path,
    toggle_hasheous_path,
    update_scan_schedule,
)

# Send views
from .send import (
    send_status,
    start_send,
)

# Upload views
from .upload import (
    check_duplicates,
    finalize_upload,
    resolve_unidentified,
    start_upload,
    upload_file,
    upload_page,
    upload_status,
)

__all__ = [
    # Browse
    "home",
    "system_list",
    "global_search",
    "game_list",
    "game_search",
    "game_detail",
    # Scan
    "scan_form",
    "rescan_path",
    "delete_scan_path",
    "scan_path_delete_info",
    "toggle_hasheous_path",
    "toggle_fetch_metadata_path",
    "update_scan_schedule",
    "clear_library",
    "scan_status",
    "cancel_scan_job",
    # Download
    "serve_image",
    "serve_system_icon",
    "download_rom",
    "download_romset",
    "romset_download_picker",
    "start_romset_download",
    "download_game",
    "start_multi_download",
    "download_status",
    "preview_games",
    "serve_download_bundle",
    # Send
    "start_send",
    "send_status",
    # Metadata
    "metadata_page",
    "image_migration_status",
    "start_metadata_job",
    "metadata_status",
    "clear_screenscraper_pause",
    "revalidate_screenscraper",
    "cancel_metadata_batch",
    "fetch_system_metadata",
    "system_metadata_status",
    "cancel_system_metadata_job",
    "fetch_game_metadata",
    "games_missing_metadata",
    "hash_lookup",
    "set_screenscraper_id",
    "save_region_preferences",
    # Game
    "delete_game",
    "rename_game",
    "edit_game",
    "delete_game_image",
    "game_search_for_merge",
    "merge_game",
    # Upload
    "upload_page",
    "start_upload",
    "check_duplicates",
    "upload_file",
    "finalize_upload",
    "upload_status",
    "resolve_unidentified",
    # Filters
    "filter_systems",
    "filter_genres",
]
