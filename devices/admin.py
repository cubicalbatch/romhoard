"""Admin registration for devices app."""

from django import forms
from django.contrib import admin

from .models import Device, DevicePreset


@admin.register(DevicePreset)
class DevicePresetAdmin(admin.ModelAdmin):
    """Admin for DevicePreset model."""

    list_display = [
        "name",
        "slug",
        "is_builtin",
        "has_folders",
        "has_images",
        "has_transfer",
    ]
    search_fields = ["name", "slug", "description"]
    list_filter = ["is_builtin"]
    readonly_fields = ["created_at", "updated_at"]


class DeviceAdminForm(forms.ModelForm):
    """Custom form for Device admin with password handling."""

    # Use a password input that doesn't render the encrypted value
    transfer_password = forms.CharField(
        widget=forms.PasswordInput(render_value=False),
        required=False,
        help_text="Leave blank to keep existing password",
    )

    class Meta:
        model = Device
        fields = "__all__"

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        # Don't show the encrypted password value in the field
        if self.instance and self.instance.pk:
            # Show placeholder if password is set
            if self.instance._transfer_password:
                self.fields["transfer_password"].widget.attrs["placeholder"] = (
                    "••••••••"
                )

    def save(self, commit=True):
        instance = super().save(commit=False)
        # Only update password if a new value was provided
        password = self.cleaned_data.get("transfer_password")
        if password:
            instance.transfer_password = password
        # If blank, keep the existing encrypted password
        if commit:
            instance.save()
        return instance


@admin.register(Device)
class DeviceAdmin(admin.ModelAdmin):
    """Admin for Device model."""

    form = DeviceAdminForm
    list_display = [
        "name",
        "slug",
        "root_path",
        "transfer_type",
        "created_at",
    ]
    search_fields = ["name", "slug", "description"]
    prepopulated_fields = {"slug": ["name"]}
    list_filter = ["transfer_type", "has_wifi"]
    fieldsets = (
        (None, {"fields": ("name", "slug", "description")}),
        (
            "ROM Organization",
            {
                "fields": ("root_path", "system_paths"),
            },
        ),
        (
            "Network Transfer",
            {
                "fields": (
                    "has_wifi",
                    "transfer_type",
                    "transfer_host",
                    "transfer_port",
                    "transfer_user",
                    "transfer_password",
                    "transfer_path_prefix",
                ),
                "classes": ("collapse",),
            },
        ),
    )
