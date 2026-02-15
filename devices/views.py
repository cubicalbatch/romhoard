"""Views for devices app."""

import json
import logging
import uuid

from django.contrib import messages
from django.http import HttpResponse, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils.text import slugify
from django.views.decorators.http import require_POST

from library.models import Setting, System

from .models import Device, DevicePreset

logger = logging.getLogger(__name__)


def device_list(request):
    """List all devices."""
    devices = Device.objects.all()

    # Get default device from settings
    default_device_id = None
    try:
        setting = Setting.objects.get(key="default_device_id")
        default_device_id = setting.value
    except Setting.DoesNotExist:
        pass

    # Calculate stats for dashboard
    device_count = devices.count()
    transfer_ready_count = sum(1 for d in devices if d.has_transfer_config)

    context = {
        "devices": devices,
        "default_device_id": default_device_id,
        "device_count": device_count,
        "transfer_ready_count": transfer_ready_count,
    }
    return render(request, "devices/device_list.html", context)


def device_create(request):
    """Create a new device."""
    systems = System.objects.all().order_by("name")

    if request.method == "POST":
        name = request.POST.get("name", "").strip()
        description = request.POST.get("description", "").strip()

        if not name:
            context = {
                "error": "Name is required",
                "systems": systems,
                "is_create": True,
            }
            return render(request, "devices/device_form.html", context)

        # Generate unique slug
        slug = slugify(name)
        base_slug = slug
        counter = 1
        while Device.objects.filter(slug=slug).exists():
            slug = f"{base_slug}-{counter}"
            counter += 1

        # Parse system_paths JSON
        system_paths_json = request.POST.get("system_paths", "{}").strip()
        try:
            system_paths = json.loads(system_paths_json) if system_paths_json else {}
        except json.JSONDecodeError:
            context = {
                "error": "Invalid JSON in system paths",
                "systems": systems,
                "is_create": True,
            }
            return render(request, "devices/device_form.html", context)

        # Create device with all fields
        has_wifi = request.POST.get("has_wifi") == "on"
        device = Device.objects.create(
            slug=slug,
            name=name,
            description=description,
            root_path=request.POST.get("root_path", "Roms/").strip(),
            system_paths=system_paths,
            has_wifi=has_wifi,
        )

        # Save transfer config if has_wifi is True
        if has_wifi:
            device.transfer_type = request.POST.get("transfer_type", "").strip()
            device.transfer_host = request.POST.get("transfer_host", "").strip()
            transfer_port = request.POST.get("transfer_port", "").strip()
            device.transfer_port = int(transfer_port) if transfer_port else None
            device.transfer_user = request.POST.get("transfer_user", "").strip()
            device.transfer_password = request.POST.get("transfer_password", "").strip()
            device.transfer_anonymous = request.POST.get("transfer_anonymous") == "on"
            device.transfer_path_prefix = request.POST.get(
                "transfer_path_prefix", ""
            ).strip()

        # Image configuration
        device.include_images = request.POST.get("include_images") == "on"
        if device.include_images:
            device.image_type = request.POST.get("image_type", "cover").strip()
            device.image_path_template = request.POST.get(
                "image_path_template", ""
            ).strip()
            image_max_width = request.POST.get("image_max_width", "").strip()
            device.image_max_width = int(image_max_width) if image_max_width else None
        else:
            # Clear image config when disabled
            device.image_type = "cover"
            device.image_path_template = ""
            device.image_max_width = None

        device.save()

        device.save()

        return redirect("devices:device_list")

    context = {
        "systems": systems,
        "is_create": True,
    }
    return render(request, "devices/device_form.html", context)


def device_edit(request, slug):
    """Edit an existing device."""
    device = get_object_or_404(Device, slug=slug)
    systems = System.objects.all().order_by("name")

    if request.method == "POST":
        name = request.POST.get("name", "").strip()
        description = request.POST.get("description", "").strip()

        if not name:
            context = {
                "device": device,
                "systems": systems,
                "error": "Name is required",
            }
            return render(request, "devices/device_form.html", context)

        # Parse system_paths JSON
        system_paths_json = request.POST.get("system_paths", "{}").strip()
        try:
            system_paths = json.loads(system_paths_json) if system_paths_json else {}
        except json.JSONDecodeError:
            context = {
                "device": device,
                "systems": systems,
                "error": "Invalid JSON in system paths",
                "system_paths_json": system_paths_json,
            }
            return render(request, "devices/device_form.html", context)

        # Update device fields
        device.name = name
        device.description = description
        device.root_path = request.POST.get("root_path", "Roms/").strip()
        device.system_paths = system_paths
        device.has_wifi = request.POST.get("has_wifi") == "on"

        # Transfer configuration (only saved if has_wifi is True)
        if device.has_wifi:
            device.transfer_type = request.POST.get("transfer_type", "").strip()
            device.transfer_host = request.POST.get("transfer_host", "").strip()
            transfer_port = request.POST.get("transfer_port", "").strip()
            device.transfer_port = int(transfer_port) if transfer_port else None
            device.transfer_user = request.POST.get("transfer_user", "").strip()
            device.transfer_password = request.POST.get("transfer_password", "").strip()
            device.transfer_anonymous = request.POST.get("transfer_anonymous") == "on"
            device.transfer_path_prefix = request.POST.get(
                "transfer_path_prefix", ""
            ).strip()
        else:
            # Clear transfer config if WiFi is disabled
            device.transfer_type = ""
            device.transfer_host = ""
            device.transfer_port = None
            device.transfer_user = ""
            device.transfer_password = ""
            device.transfer_anonymous = False
            device.transfer_path_prefix = ""

        # Image configuration
        device.include_images = request.POST.get("include_images") == "on"
        if device.include_images:
            device.image_type = request.POST.get("image_type", "cover").strip()
            device.image_path_template = request.POST.get(
                "image_path_template", ""
            ).strip()
            image_max_width = request.POST.get("image_max_width", "").strip()
            device.image_max_width = int(image_max_width) if image_max_width else None
        else:
            # Clear image config when disabled
            device.image_type = "cover"
            device.image_path_template = ""
            device.image_max_width = None

        device.save()

        device.save()

        return redirect("devices:device_list")

    context = {
        "device": device,
        "systems": systems,
        "system_paths_json": json.dumps(device.system_paths, indent=2),
    }
    return render(request, "devices/device_form.html", context)


@require_POST
def device_delete(request, slug):
    """Delete a device."""
    device = get_object_or_404(Device, slug=slug)
    device.delete()

    if request.headers.get("HX-Request"):
        return HttpResponse(
            status=200, headers={"HX-Redirect": reverse("devices:device_list")}
        )
    return redirect("devices:device_list")


def export_device(request, slug):
    """Export device as JSON."""
    from .serializers import export_device as serialize_export

    device = get_object_or_404(Device, slug=slug)
    data = serialize_export(device)

    response = HttpResponse(
        json.dumps(data, indent=2),
        content_type="application/json",
    )
    response["Content-Disposition"] = f'attachment; filename="{device.slug}.json"'
    return response


def import_device(request):
    """Import device from JSON file or URL.

    For URL imports, redirects to a preview page before committing.
    For file uploads, imports directly (existing behavior).
    """
    from romhoard.url_fetch import URLFetchError, fetch_json_from_url

    from .serializers import ImportError as SerializerImportError
    from .serializers import import_device as serialize_import
    from .serializers import validate_import_data

    if request.method == "POST":
        url = request.POST.get("url", "").strip()
        overwrite = request.POST.get("overwrite") == "on"

        # URL import - fetch and show preview
        if url:
            try:
                data = fetch_json_from_url(url)
                validate_import_data(data)

                # Store in session for preview
                token = uuid.uuid4().hex
                request.session[f"import_device_{token}"] = {
                    "data": data,
                    "url": url,
                    "overwrite": overwrite,
                }

                return redirect("devices:import_device_preview", token=token)

            except URLFetchError as e:
                context = {"error": f"Failed to fetch URL: {e}"}
                return render(request, "devices/import.html", context)
            except SerializerImportError as e:
                context = {"error": str(e)}
                return render(request, "devices/import.html", context)

        # File upload - import directly (existing behavior)
        if "file" not in request.FILES:
            context = {"error": "Please provide a file or URL"}
            return render(request, "devices/import.html", context)

        file = request.FILES["file"]

        try:
            data = json.loads(file.read().decode("utf-8"))
            serialize_import(data, overwrite=overwrite)
            return redirect("devices:device_list")
        except json.JSONDecodeError:
            context = {"error": "Invalid JSON file"}
            return render(request, "devices/import.html", context)
        except SerializerImportError as e:
            context = {"error": str(e)}
            return render(request, "devices/import.html", context)

    return render(request, "devices/import.html")


def import_device_preview(request, token):
    """Preview and confirm URL import for a device."""
    from .serializers import ImportError as SerializerImportError
    from .serializers import import_device as serialize_import

    session_key = f"import_device_{token}"
    session_data = request.session.get(session_key)

    if not session_data:
        messages.error(request, "Import session expired. Please try again.")
        return redirect("devices:import_device")

    data = session_data["data"]
    url = session_data["url"]
    overwrite = session_data["overwrite"]

    device_data = data.get("device", {})
    slug = device_data.get("slug", "")

    # Check if device already exists
    existing = Device.objects.filter(slug=slug).first() if slug else None

    if request.method == "POST":
        action = request.POST.get("action")

        if action == "cancel":
            # Clean up session and return to import page
            del request.session[session_key]
            return redirect("devices:import_device")

        if action == "confirm":
            # Check if overwrite was selected on this page
            if request.POST.get("overwrite"):
                overwrite = True

            # Perform the import
            try:
                device = serialize_import(data, overwrite=overwrite)

                # Clean up session
                del request.session[session_key]

                messages.success(
                    request, f"Successfully imported device '{device.name}'"
                )
                return redirect("devices:device_list")
            except SerializerImportError as e:
                # Clean up session on error too
                del request.session[session_key]
                messages.error(request, str(e))
                return redirect("devices:import_device")

    # Count system paths
    system_paths = device_data.get("system_paths", {})
    system_paths_count = len(system_paths)

    context = {
        "token": token,
        "url": url,
        "overwrite": overwrite,
        "device_data": device_data,
        "system_paths_count": system_paths_count,
        "existing_device": existing,
    }
    return render(request, "devices/import_preview.html", context)


@require_POST
def set_default_device(request):
    """Set the default device for downloads/transfers."""
    device_id = request.POST.get("device_id")

    if device_id:
        try:
            device_id = int(device_id)
            # Verify it exists
            Device.objects.get(pk=device_id)

            Setting.objects.update_or_create(
                key="default_device_id", defaults={"value": device_id}
            )
        except (ValueError, Device.DoesNotExist):
            return JsonResponse(
                {"success": False, "error": "Invalid device"}, status=400
            )
    else:
        # Clear default
        Setting.objects.filter(key="default_device_id").delete()

    return JsonResponse({"success": True})


def device_picker(request):
    """HTMX partial for device picker dropdown."""
    transfer_only = request.GET.get("transfer_only") == "1"

    query = Device.objects.order_by("name")

    # Filter by has_wifi for transfer mode
    if transfer_only:
        query = query.filter(has_wifi=True)

    devices = list(query)

    # Get default device from settings
    default_device_id = None
    try:
        setting = Setting.objects.get(key="default_device_id")
        default_device_id = int(setting.value)
    except (Setting.DoesNotExist, ValueError, TypeError):
        pass

    context = {
        "devices": devices,
        "default_device_id": default_device_id,
        "transfer_only": transfer_only,
    }
    return render(request, "devices/_device_picker.html", context)


def device_transfer_config(request, device_id):
    """HTMX partial for device transfer configuration form."""
    device = get_object_or_404(Device, pk=device_id)

    context = {
        "device": device,
    }
    return render(request, "devices/_transfer_config_form.html", context)


@require_POST
def test_transfer_connection(request):
    """Test FTP/SFTP connection with provided credentials.

    Accepts POST with transfer config parameters and returns JSON result.
    """
    from library.send import FTPClient, SFTPClient

    transfer_type = request.POST.get("transfer_type", "").strip()
    transfer_host = request.POST.get("transfer_host", "").strip()
    transfer_port = request.POST.get("transfer_port", "").strip()
    transfer_user = request.POST.get("transfer_user", "").strip()
    transfer_password = request.POST.get("transfer_password", "").strip()
    transfer_anonymous = request.POST.get("transfer_anonymous", "").lower() in (
        "true",
        "1",
        "on",
    )
    transfer_path_prefix = request.POST.get("transfer_path_prefix", "").strip()

    # Validate required fields
    if not transfer_type:
        return JsonResponse(
            {"success": False, "error": "Transfer protocol is required"}
        )
    if not transfer_host:
        return JsonResponse({"success": False, "error": "Host is required"})

    # Determine port
    if transfer_port:
        try:
            port = int(transfer_port)
        except ValueError:
            return JsonResponse({"success": False, "error": "Invalid port number"})
    else:
        # Default ports
        port = 22 if transfer_type == "sftp" else 21

    # Create appropriate client
    logger.info(f"Testing {transfer_type.upper()} connection to {transfer_host}:{port}")
    try:
        if transfer_type == "sftp":
            client = SFTPClient(
                host=transfer_host,
                port=port,
                user=transfer_user,
                password=transfer_password,
            )
        elif transfer_type in ("ftp", "ftps"):
            # Use empty credentials for anonymous FTP
            user = "" if transfer_anonymous else transfer_user
            password = "" if transfer_anonymous else transfer_password
            client = FTPClient(
                host=transfer_host,
                port=port,
                user=user,
                password=password,
                use_tls=(transfer_type == "ftps"),
            )
        else:
            logger.warning(f"Unknown transfer type requested: {transfer_type}")
            return JsonResponse(
                {"success": False, "error": f"Unknown transfer type: {transfer_type}"}
            )

        # Test connection
        success, error = client.connect()
        if not success:
            logger.warning(
                f"Connection test failed for {transfer_type.upper()} "
                f"{transfer_host}:{port}: {error}"
            )
            return JsonResponse(
                {"success": False, "error": f"Connection failed: {error}"}
            )

        # Test write permissions
        test_path = ".romhoard_test"
        if transfer_path_prefix:
            # Clean prefix and combine
            prefix = transfer_path_prefix.rstrip("/")
            test_path = f"{prefix}/{test_path}"

        success, error = client.test_write(test_path)
        client.close()

        if not success:
            logger.warning(
                f"Write test failed for {transfer_type.upper()} "
                f"{transfer_host}:{port} at {test_path}: {error}"
            )
            return JsonResponse(
                {"success": False, "error": f"Write test failed: {error}"}
            )

        logger.info(
            f"Connection test successful for {transfer_type.upper()} "
            f"{transfer_host}:{port}"
        )
        return JsonResponse({"success": True})

    except Exception as e:
        logger.exception(
            f"Unexpected error testing {transfer_type.upper()} "
            f"connection to {transfer_host}:{port}"
        )
        return JsonResponse({"success": False, "error": str(e)})


# Preset API endpoints


def preset_list(request):
    """Return list of available device presets as JSON."""
    presets = DevicePreset.objects.all()
    data = [
        {
            "slug": p.slug,
            "name": p.name,
            "description": p.description,
            "tags": p.tags,
            "has_folders": p.has_folders,
            "has_images": p.has_images,
            "has_transfer": p.has_transfer,
            "is_builtin": p.is_builtin,
        }
        for p in presets
    ]
    return JsonResponse({"presets": data})


def preset_detail(request, slug):
    """Return full preset configuration as JSON."""
    preset = get_object_or_404(DevicePreset, slug=slug)
    data = {
        "slug": preset.slug,
        "name": preset.name,
        "description": preset.description,
        "tags": preset.tags,
        "folders": preset.folders_config,
        "images": preset.images_config,
        "transfer": preset.transfer_config,
    }
    return JsonResponse(data)
