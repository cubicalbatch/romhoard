"""Tests for devices app views."""

from unittest.mock import MagicMock, patch

import pytest
from django.urls import reverse
from devices.models import Device


@pytest.mark.django_db
def test_device_transfer_config(client):
    """Test that the transfer config form is pre-filled with existing device data."""
    device = Device.objects.create(
        name="Miyoo Mini",
        slug="miyoo-mini",
        root_path="Roms/",
        transfer_type=Device.TRANSFER_SFTP,
        transfer_host="192.168.1.100",
        transfer_port=22,
        transfer_user="user",
        transfer_password="password123",
    )

    # Request the transfer config form
    url = reverse("devices:device_transfer_config", kwargs={"device_id": device.pk})
    response = client.get(url)

    assert response.status_code == 200

    # Check that form is pre-filled with existing values
    content = response.content.decode()
    assert 'name="transfer_type"' in content
    assert "192.168.1.100" in content
    assert 'name="transfer_port"' in content
    assert 'name="transfer_user"' in content
    assert 'name="transfer_password"' in content


@pytest.mark.django_db
def test_device_transfer_config_empty(client):
    """Test that the transfer config form works with no existing config."""
    device = Device.objects.create(name="Miyoo Mini", slug="miyoo-mini")

    # Request the transfer config form
    url = reverse("devices:device_transfer_config", kwargs={"device_id": device.pk})
    response = client.get(url)

    assert response.status_code == 200
    content = response.content.decode()

    # Check that form fields exist but are empty/default
    assert 'name="transfer_type"' in content
    assert 'name="transfer_host"' in content
    assert 'name="transfer_port"' in content
    assert 'name="transfer_user"' in content
    assert 'name="transfer_password"' in content


@pytest.mark.django_db
def test_device_transfer_config_not_found(client):
    """Test that non-existent device returns 404."""
    url = reverse("devices:device_transfer_config", kwargs={"device_id": 99999})
    response = client.get(url)
    assert response.status_code == 404


@pytest.mark.django_db
def test_device_create(client):
    """Test creating a device."""
    url = reverse("devices:device_create")
    response = client.post(
        url,
        {
            "name": "New Device",
            "description": "Test device",
            "root_path": "Roms/",
        },
    )

    # Should redirect to device list page
    assert response.status_code == 302
    assert response.url == reverse("devices:device_list")

    # Verify device was created
    device = Device.objects.get(slug="new-device")
    assert device.name == "New Device"
    assert device.root_path == "Roms/"


@pytest.mark.django_db
def test_device_picker_shows_device(client):
    """Test that device picker shows device."""
    device = Device.objects.create(
        name="Test Device", slug="test-device", root_path="Roms/"
    )

    url = reverse("devices:device_picker")
    response = client.get(url)

    assert response.status_code == 200
    content = response.content.decode()

    # Should show device name
    assert "Test Device" in content
    # Device ID should be in the click handler
    assert f"deviceId = '{device.id}'" in content


@pytest.mark.django_db
def test_device_picker_transfer_only_shows_wifi_devices(client):
    """Test that transfer_only mode only shows devices with WiFi capability."""
    # Device with WiFi and transfer config
    Device.objects.create(
        name="WiFi Device",
        slug="wifi-device",
        has_wifi=True,
        transfer_type=Device.TRANSFER_FTP,
        transfer_host="192.168.1.2",
    )

    # Device without WiFi
    Device.objects.create(
        name="No WiFi Device",
        slug="no-wifi-device",
        has_wifi=False,
    )

    # When transfer_only=1, only WiFi devices should show
    url = reverse("devices:device_picker")
    response = client.get(url, {"transfer_only": "1"})
    assert response.status_code == 200
    content = response.content.decode()

    # Only WiFi device should appear
    assert "WiFi Device" in content
    assert "No WiFi Device" not in content


@pytest.mark.django_db
def test_device_picker_shows_all_devices_without_transfer_only(client):
    """Test that all devices appear when transfer_only=0."""
    # Device with WiFi
    Device.objects.create(
        name="WiFi Device",
        slug="wifi-device",
        has_wifi=True,
    )

    # Device without WiFi
    Device.objects.create(
        name="No WiFi Device",
        slug="no-wifi-device",
        has_wifi=False,
    )

    url = reverse("devices:device_picker")
    response = client.get(url, {"transfer_only": "0"})

    assert response.status_code == 200
    content = response.content.decode()

    # Both devices should appear when not filtering for transfer
    assert "WiFi Device" in content
    assert "No WiFi Device" in content


@pytest.mark.django_db
def test_transfer_connection_missing_host(client):
    """Test that test-connection returns error when host is missing."""
    url = reverse("devices:test_transfer_connection")
    response = client.post(
        url,
        {
            "transfer_type": "sftp",
            "transfer_host": "",
            "transfer_port": "",
            "transfer_user": "user",
            "transfer_password": "pass",
        },
    )

    assert response.status_code == 200
    data = response.json()
    assert data["success"] is False
    assert "Host is required" in data["error"]


@pytest.mark.django_db
def test_transfer_connection_missing_type(client):
    """Test that test-connection returns error when type is missing."""
    url = reverse("devices:test_transfer_connection")
    response = client.post(
        url,
        {
            "transfer_type": "",
            "transfer_host": "192.168.1.100",
            "transfer_port": "",
            "transfer_user": "user",
            "transfer_password": "pass",
        },
    )

    assert response.status_code == 200
    data = response.json()
    assert data["success"] is False
    assert "Transfer protocol is required" in data["error"]


@pytest.mark.django_db
def test_transfer_connection_invalid_type(client):
    """Test that test-connection returns error for invalid type."""
    url = reverse("devices:test_transfer_connection")
    response = client.post(
        url,
        {
            "transfer_type": "invalid",
            "transfer_host": "192.168.1.100",
            "transfer_port": "",
            "transfer_user": "user",
            "transfer_password": "pass",
        },
    )

    assert response.status_code == 200
    data = response.json()
    assert data["success"] is False
    assert "Unknown transfer type" in data["error"]


@pytest.mark.django_db
def test_transfer_connection_invalid_port(client):
    """Test that test-connection returns error for invalid port."""
    url = reverse("devices:test_transfer_connection")
    response = client.post(
        url,
        {
            "transfer_type": "sftp",
            "transfer_host": "192.168.1.100",
            "transfer_port": "invalid",
            "transfer_user": "user",
            "transfer_password": "pass",
        },
    )

    assert response.status_code == 200
    data = response.json()
    assert data["success"] is False
    assert "Invalid port number" in data["error"]


@pytest.mark.django_db
def test_transfer_connection_sftp_success(client):
    """Test successful SFTP connection."""
    # Mock the SFTPClient
    mock_client = MagicMock()
    mock_client.connect.return_value = (True, "")
    mock_client.test_write.return_value = (True, "")

    with patch("library.send.SFTPClient", return_value=mock_client) as mock_sftp_class:
        url = reverse("devices:test_transfer_connection")
        response = client.post(
            url,
            {
                "transfer_type": "sftp",
                "transfer_host": "192.168.1.100",
                "transfer_port": "22",
                "transfer_user": "user",
                "transfer_password": "pass",
            },
        )

        assert response.status_code == 200
        data = response.json()
        assert data["success"] is True

        # Verify client was created with correct params
        mock_sftp_class.assert_called_once_with(
            host="192.168.1.100",
            port=22,
            user="user",
            password="pass",
        )
        mock_client.connect.assert_called_once()
        mock_client.test_write.assert_called_once_with(".romhoard_test")
        mock_client.close.assert_called_once()


@pytest.mark.django_db
def test_transfer_connection_ftp_success(client):
    """Test successful FTP connection."""
    # Mock the FTPClient
    mock_client = MagicMock()
    mock_client.connect.return_value = (True, "")
    mock_client.test_write.return_value = (True, "")

    with patch("library.send.FTPClient", return_value=mock_client) as mock_ftp_class:
        url = reverse("devices:test_transfer_connection")
        response = client.post(
            url,
            {
                "transfer_type": "ftp",
                "transfer_host": "192.168.1.100",
                "transfer_port": "",  # Should use default 21
                "transfer_user": "user",
                "transfer_password": "pass",
            },
        )

        assert response.status_code == 200
        data = response.json()
        assert data["success"] is True

        # Verify client was created with correct params
        mock_ftp_class.assert_called_once_with(
            host="192.168.1.100",
            port=21,  # Default FTP port
            user="user",
            password="pass",
            use_tls=False,
        )


@pytest.mark.django_db
def test_transfer_connection_ftps_success(client):
    """Test successful FTPS connection."""
    # Mock the FTPClient
    mock_client = MagicMock()
    mock_client.connect.return_value = (True, "")
    mock_client.test_write.return_value = (True, "")

    with patch("library.send.FTPClient", return_value=mock_client) as mock_ftp_class:
        url = reverse("devices:test_transfer_connection")
        response = client.post(
            url,
            {
                "transfer_type": "ftps",
                "transfer_host": "192.168.1.100",
                "transfer_port": "990",
                "transfer_user": "user",
                "transfer_password": "pass",
            },
        )

        assert response.status_code == 200
        data = response.json()
        assert data["success"] is True

        # Verify client was created with use_tls=True
        mock_ftp_class.assert_called_once_with(
            host="192.168.1.100",
            port=990,
            user="user",
            password="pass",
            use_tls=True,
        )


@pytest.mark.django_db
def test_transfer_connection_connect_failure(client):
    """Test connection failure is reported correctly."""
    # Mock the SFTPClient
    mock_client = MagicMock()
    mock_client.connect.return_value = (False, "Connection refused")

    with patch("library.send.SFTPClient", return_value=mock_client):
        url = reverse("devices:test_transfer_connection")
        response = client.post(
            url,
            {
                "transfer_type": "sftp",
                "transfer_host": "192.168.1.100",
                "transfer_port": "22",
                "transfer_user": "user",
                "transfer_password": "pass",
            },
        )

        assert response.status_code == 200
        data = response.json()
        assert data["success"] is False
        assert "Connection refused" in data["error"]


@pytest.mark.django_db
def test_transfer_connection_write_failure(client):
    """Test write test failure is reported correctly."""
    # Mock the SFTPClient
    mock_client = MagicMock()
    mock_client.connect.return_value = (True, "")
    mock_client.test_write.return_value = (False, "Permission denied")

    with patch("library.send.SFTPClient", return_value=mock_client):
        url = reverse("devices:test_transfer_connection")
        response = client.post(
            url,
            {
                "transfer_type": "sftp",
                "transfer_host": "192.168.1.100",
                "transfer_port": "22",
                "transfer_user": "user",
                "transfer_password": "pass",
            },
        )

        assert response.status_code == 200
        data = response.json()
        assert data["success"] is False
        assert "Permission denied" in data["error"]


@pytest.mark.django_db
def test_transfer_connection_with_prefix(client):
    """Test connection with transfer_path_prefix."""
    # Mock the SFTPClient
    mock_client = MagicMock()
    mock_client.connect.return_value = (True, "")
    mock_client.test_write.return_value = (True, "")

    with patch("library.send.SFTPClient", return_value=mock_client):
        url = reverse("devices:test_transfer_connection")
        response = client.post(
            url,
            {
                "transfer_type": "sftp",
                "transfer_host": "192.168.1.100",
                "transfer_user": "user",
                "transfer_password": "pass",
                "transfer_path_prefix": "/mnt/SDCARD",
            },
        )

        assert response.status_code == 200
        data = response.json()
        assert data["success"] is True

        # Verify test_write was called with prefixed path
        mock_client.test_write.assert_called_with("/mnt/SDCARD/.romhoard_test")


@pytest.mark.django_db
def test_transfer_connection_without_prefix(client):
    """Test connection without transfer_path_prefix."""
    # Mock the SFTPClient
    mock_client = MagicMock()
    mock_client.connect.return_value = (True, "")
    mock_client.test_write.return_value = (True, "")

    with patch("library.send.SFTPClient", return_value=mock_client):
        url = reverse("devices:test_transfer_connection")
        response = client.post(
            url,
            {
                "transfer_type": "sftp",
                "transfer_host": "192.168.1.100",
                "transfer_user": "user",
                "transfer_password": "pass",
                "transfer_path_prefix": "",
            },
        )

        assert response.status_code == 200
        data = response.json()
        assert data["success"] is True

        # Verify test_write was called with default path
        mock_client.test_write.assert_called_with(".romhoard_test")

@pytest.mark.django_db
def test_device_edit(client):
    """Test editing a device."""
    device = Device.objects.create(
        name="Old Name",
        slug="old-name",
        root_path="Old/",
    )

    url = reverse("devices:device_edit", kwargs={"slug": device.slug})
    response = client.post(
        url,
        {
            "name": "New Name",
            "description": "Updated",
            "root_path": "New/",
        },
    )

    # Should redirect to device list page
    assert response.status_code == 302
    assert response.url == reverse("devices:device_list")

    device.refresh_from_db()
    assert device.name == "New Name"
    assert device.root_path == "New/"
