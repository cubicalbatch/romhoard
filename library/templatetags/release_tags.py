"""Template component for rendering one release tag badge."""

from django import template

from library.release_tags import describe_release_tag

register = template.Library()


@register.inclusion_tag("library/_release_tag.html")
def release_tag(tag: str) -> dict:
    """Render a release tag, with a tooltip when the tag is known."""
    return {
        "tag": tag,
        "description": describe_release_tag(tag),
    }
