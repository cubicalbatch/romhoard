from django.urls import path

from . import views

app_name = "romcollections"

urlpatterns = [
    # Static routes (must come before <creator>/<slug> pattern)
    path("", views.collection_list, name="collection_list"),
    path("new/", views.collection_create, name="collection_create"),
    path("import/", views.import_collection, name="import_collection"),
    path(
        "import/preview/<str:token>/",
        views.import_collection_preview,
        name="import_collection_preview",
    ),
    path("picker/", views.collection_picker, name="collection_picker"),
    path("search/", views.collection_search, name="collection_search"),
    path(
        "favorites/toggle/<int:game_pk>/", views.toggle_favorite, name="toggle_favorite"
    ),
    # Creator profile page
    path("u/<slug:creator>/", views.creator_page, name="creator_page"),
    # Dynamic routes with creator/slug pattern
    path("<slug:creator>/<slug:slug>/", views.collection_detail, name="collection_detail"),
    path(
        "<slug:creator>/<slug:slug>/<int:game_pk>/",
        views.collection_game_detail,
        name="collection_game_detail",
    ),
    path(
        "<slug:creator>/<slug:slug>/search/",
        views.collection_entry_search,
        name="collection_entry_search",
    ),
    path(
        "<slug:creator>/<slug:slug>/filter-options/systems/",
        views.collection_filter_systems,
        name="collection_filter_systems",
    ),
    path(
        "<slug:creator>/<slug:slug>/filter-options/genres/",
        views.collection_filter_genres,
        name="collection_filter_genres",
    ),
    path("<slug:creator>/<slug:slug>/sync/", views.sync_collection_from_source, name="sync_collection"),
    path("<slug:creator>/<slug:slug>/adopt/", views.adopt_collection, name="adopt_collection"),
    path("<slug:creator>/<slug:slug>/unadopt/", views.unadopt_collection, name="unadopt_collection"),
    path("<slug:creator>/<slug:slug>/edit/", views.collection_edit, name="collection_edit"),
    path("<slug:creator>/<slug:slug>/delete/", views.collection_delete, name="collection_delete"),
    path("<slug:creator>/<slug:slug>/entries/add/", views.add_entry, name="add_entry"),
    path(
        "<slug:creator>/<slug:slug>/entries/bulk-add/", views.bulk_add_entries, name="bulk_add_entries"
    ),
    path(
        "<slug:creator>/<slug:slug>/entries/<int:pk>/remove/", views.remove_entry, name="remove_entry"
    ),
    path("<slug:creator>/<slug:slug>/entries/reorder/", views.reorder_entries, name="reorder_entries"),
    path(
        "<slug:creator>/<slug:slug>/entries/bulk-remove/",
        views.bulk_remove_entries,
        name="bulk_remove_entries",
    ),
    path(
        "<slug:creator>/<slug:slug>/entries/<int:pk>/update-notes/",
        views.update_entry_notes,
        name="update_entry_notes",
    ),
    path("<slug:creator>/<slug:slug>/export/", views.export_collection, name="export_collection"),
    path(
        "<slug:creator>/<slug:slug>/export/with-images/",
        views.start_export_with_images,
        name="start_export_with_images",
    ),
    path(
        "<slug:creator>/<slug:slug>/export/status/",
        views.export_status,
        name="export_status",
    ),
    path(
        "<slug:creator>/<slug:slug>/export/download/<int:job_id>/",
        views.download_export,
        name="download_export",
    ),
    path(
        "<slug:creator>/<slug:slug>/download/", views.download_collection, name="download_collection"
    ),
    path("<slug:creator>/<slug:slug>/download/status/", views.download_status, name="download_status"),
    path("<slug:creator>/<slug:slug>/send/", views.send_collection, name="send_collection"),
    # Cover image endpoints
    path("<slug:creator>/<slug:slug>/cover/", views.serve_cover, name="serve_cover"),
    path("<slug:creator>/<slug:slug>/cover/upload/", views.upload_cover, name="upload_cover"),
    path("<slug:creator>/<slug:slug>/cover/generate/", views.generate_cover, name="generate_cover"),
    path("<slug:creator>/<slug:slug>/cover/remove/", views.remove_cover, name="remove_cover"),
    path("<slug:creator>/<slug:slug>/cover/status/", views.cover_status, name="cover_status"),
]
