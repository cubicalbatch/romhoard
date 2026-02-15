from django import template

register = template.Library()


@register.filter
def format_duration(duration):
    """Format a timedelta into short form string (e.g., "2m 15s", "1h 5m")."""
    if not duration:
        return "0s"

    total_seconds = int(duration.total_seconds())

    if total_seconds < 60:
        return f"{total_seconds}s"

    minutes = total_seconds // 60
    seconds = total_seconds % 60

    if minutes < 60:
        if seconds == 0:
            return f"{minutes}m"
        return f"{minutes}m {seconds}s"

    hours = minutes // 60
    minutes = minutes % 60

    parts = []
    if hours > 0:
        parts.append(f"{hours}h")
    if minutes > 0:
        parts.append(f"{minutes}m")

    return " ".join(parts) if parts else "0s"
