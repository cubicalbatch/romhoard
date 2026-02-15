import html

from django import template
from django.utils.safestring import mark_safe

register = template.Library()

# 15 colors for header hashing (excludes gray/cream which are special purpose)
HEADER_COLORS = [
    "magenta",
    "blue",
    "purple",
    "teal",
    "green",
    "orange",
    "red",
    "navy",
    "gold",
    "slate",
    "olive",
    "maroon",
    "indigo",
    "coral",
    "cyan",
]


@register.filter
def header_color(slug: str) -> str:
    """Return consistent header color class based on slug hash.

    Args:
        slug: The slug to hash for color selection.

    Returns:
        CSS class name like 'retro-header-magenta'.
    """
    if not slug:
        return "retro-header-magenta"
    # Special case: Favorites collection always gets gold
    if slug == "favorites":
        return "retro-header-gold"
    hash_val = hash(slug)
    return f"retro-header-{HEADER_COLORS[hash_val % len(HEADER_COLORS)]}"


@register.filter
def in_set(value, collection):
    """Check if a value is in a collection (set, list, etc.)."""
    if collection is None:
        return False
    return value in collection


@register.filter
def unescape(value):
    """Decode HTML entities in a string (e.g., &amp; → &)."""
    if value is None:
        return ""
    return html.unescape(str(value))


# Region name to flag image file mapping
REGION_TO_FLAG = {
    "usa": "us",
    "us": "us",
    "united states": "us",
    "europe": "eu",
    "eu": "eu",
    "japan": "jp",
    "jp": "jp",
    "uk": "gb",
    "united kingdom": "gb",
    "great britain": "gb",
    "gb": "gb",
    "germany": "de",
    "de": "de",
    "france": "fr",
    "fr": "fr",
    "spain": "es",
    "es": "es",
    "italy": "it",
    "it": "it",
    "australia": "au",
    "au": "au",
    "korea": "kr",
    "kr": "kr",
    "china": "cn",
    "cn": "cn",
    "brazil": "br",
    "br": "br",
    "netherlands": "nl",
    "nl": "nl",
    "sweden": "se",
    "se": "se",
    "russia": "ru",
    "ru": "ru",
}


@register.filter
def region_flag(value):
    """Convert a region name to a flag image tag."""
    if not value:
        flag_code = "un"
    else:
        flag_code = REGION_TO_FLAG.get(str(value).lower(), "un")
    return mark_safe(
        f'<img src="/static/img/flags/{flag_code}.png" alt="{value or "Unknown"}" '
        f'class="inline-block h-4 w-auto" title="{value or "Unknown"}">'
    )


@register.tag(name="retro_modal")
def do_retro_modal(parser, token):
    bits = token.split_contents()

    if len(bits) < 3:
        raise template.TemplateSyntaxError(
            "{% retro_modal %} tag requires at least 'id' and 'title' arguments"
        )

    kwargs = {}
    for bit in bits[1:]:
        if "=" in bit:
            key, value = bit.split("=", 1)
            kwargs[key] = parser.compile_filter(value)
        else:
            raise template.TemplateSyntaxError(
                f"Invalid argument: {bit}. Use format: key=value"
            )

    nodelist = parser.parse(("endretro_modal",))
    parser.delete_first_token()

    return RetroModalNode(nodelist, kwargs)


class RetroModalNode(template.Node):
    def __init__(self, nodelist, kwargs):
        self.nodelist = nodelist
        self.kwargs = kwargs

    def render(self, context):
        id_val = self.kwargs["id"].resolve(context)
        title_val = self.kwargs["title"].resolve(context)

        close_btn_var = self.kwargs.get("close_btn")
        close_btn = close_btn_var.resolve(context) if close_btn_var else True

        size_var = self.kwargs.get("size")
        size = size_var.resolve(context) if size_var else "md"

        size_classes = {
            "sm": "max-w-sm",
            "md": "max-w-md",
            "lg": "max-w-lg",
            "xl": "max-w-xl",
            "2xl": "max-w-2xl",
        }
        max_width_class = size_classes.get(size, size_classes["md"])

        close_btn_html = ""
        if close_btn:
            close_btn_html = f'<button @click="$store.modals.close(\'{id_val}\')" class="retro-modal-close">&times;</button>'

        # Only render header if there's a title or close button
        header_html = ""
        if title_val or close_btn:
            header_html = f"""<div class="retro-modal-header">
            <h3 class="retro-modal-title">{title_val}</h3>
            {close_btn_html}
        </div>"""

        output = f"""
<div id="modal-{id_val}" x-data class="fixed inset-0 retro-modal-backdrop z-50 hidden items-center justify-center p-4" data-modal="{id_val}" @click.self="!$store.modals.isLocked('{id_val}') && $store.modals.close('{id_val}')">
    <div class="retro-modal {max_width_class}">
        {header_html}
        {self.nodelist.render(context)}
    </div>
</div>
"""
        return mark_safe(output)
