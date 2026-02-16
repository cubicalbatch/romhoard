"""
Custom template context processors for RomHoard.
"""

from django.conf import settings


def bundled_assets(request):
    """
    Expose USE_BUNDLED_ASSETS setting to templates.

    When True, templates load assets from static/vendor/ instead of CDN.
    Used in Docker images for offline support.
    """
    return {
        "USE_BUNDLED_ASSETS": getattr(settings, "USE_BUNDLED_ASSETS", False),
    }
