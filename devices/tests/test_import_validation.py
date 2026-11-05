"""Malformed device imports must fail before overwriting working settings."""

import pytest
from django.core.files.uploadedfile import SimpleUploadedFile
from django.urls import reverse

from devices.models import Device
from devices.serializers import ImportError, import_device, validate_import_data


def device_data():
    return {
        "romhoard_device": {"version": "3.0"},
        "device": {"slug": "handheld", "name": "Handheld"},
    }


@pytest.mark.parametrize("value", [None, [], "slug name", 1])
def test_device_must_be_an_object(value):
    data = device_data()
    data["device"] = value
    with pytest.raises(ImportError, match="must be an object"):
        validate_import_data(data)


@pytest.mark.parametrize(
    "field,value",
    [
        ("slug", "../bad"),
        ("name", None),
        ("system_paths", []),
        ("system_paths", {"gba": None}),
        ("system_paths", {"gba": {"folder": 42}}),
        ("system_paths", {"gba": {"game_folders": "yes"}}),
        ("has_wifi", "false"),
        ("image_max_width", -1),
        ("image_max_width", True),
        ("image_type", "invalid"),
    ],
)
def test_device_rejects_invalid_settings(field, value):
    data = device_data()
    data["device"][field] = value
    with pytest.raises(ImportError):
        validate_import_data(data)


@pytest.mark.django_db
def test_invalid_overwrite_keeps_device_configuration():
    original = import_device(device_data())
    data = device_data()
    data["device"].update(name="Replacement", system_paths=["invalid"])
    with pytest.raises(ImportError):
        import_device(data, overwrite=True)
    original.refresh_from_db()
    assert original.name == "Handheld"
    assert original.system_paths == {}


@pytest.mark.django_db
def test_zero_image_width_still_means_no_resize():
    data = device_data()
    data["device"]["image_max_width"] = 0
    assert import_device(data).image_max_width == 0


@pytest.mark.django_db
def test_invalid_utf8_upload_returns_import_error(client):
    upload = SimpleUploadedFile(
        "device.json", b"\xff\xfe", content_type="application/json"
    )
    response = client.post(reverse("devices:import_device"), {"file": upload})
    assert response.status_code == 200
    assert "Invalid JSON file" in response.content.decode()
    assert not Device.objects.filter(slug="handheld").exists()
