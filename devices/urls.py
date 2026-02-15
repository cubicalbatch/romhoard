"""URL patterns for devices app."""

from django.urls import path

from . import views

app_name = "devices"

urlpatterns = [
    # Device CRUD
    path("", views.device_list, name="device_list"),
    # Static paths must come before slug patterns
    path("new/", views.device_create, name="device_create"),
    path("import/", views.import_device, name="import_device"),
    path(
        "import/preview/<str:token>/",
        views.import_device_preview,
        name="import_device_preview",
    ),
    path("set-default/", views.set_default_device, name="set_default_device"),
    path("picker/", views.device_picker, name="device_picker"),
    path(
        "test-connection/",
        views.test_transfer_connection,
        name="test_transfer_connection",
    ),
    # Preset API endpoints
    path("presets/", views.preset_list, name="preset_list"),
    path("presets/<slug:slug>/", views.preset_detail, name="preset_detail"),
    # Device transfer config (uses device ID)
    path(
        "<int:device_id>/transfer-config/",
        views.device_transfer_config,
        name="device_transfer_config",
    ),
    # Slug-based device paths (must come after static paths)
    path("<slug:slug>/edit/", views.device_edit, name="device_edit"),
    path("<slug:slug>/delete/", views.device_delete, name="device_delete"),
    path("<slug:slug>/export/", views.export_device, name="export_device"),
]
