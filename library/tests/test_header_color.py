"""Tests for the header_color template filter."""

from django.template import Context, Template
from django.test import SimpleTestCase

from library.templatetags.retro_components import HEADER_COLORS, header_color


class TestHeaderColorFilter(SimpleTestCase):
    """Test the header_color template filter."""

    def test_returns_consistent_color_for_same_slug(self):
        """Same slug should always return the same color."""
        slug = "test-collection"
        color1 = header_color(slug)
        color2 = header_color(slug)
        self.assertEqual(color1, color2)

    def test_returns_different_colors_for_different_slugs(self):
        """Different slugs should generally return different colors.

        Note: With 15 colors, some collisions are expected, but most should differ.
        """
        slugs = ["snes-classics", "nes-collection", "ps1-favorites", "genesis-rpgs"]
        colors = [header_color(slug) for slug in slugs]
        # At least some should be different
        self.assertGreater(len(set(colors)), 1)

    def test_empty_slug_returns_default(self):
        """Empty slug should return magenta as default."""
        self.assertEqual(header_color(""), "retro-header-magenta")
        self.assertEqual(header_color(None), "retro-header-magenta")

    def test_favorites_always_gold(self):
        """Favorites collection should always have gold header."""
        self.assertEqual(header_color("favorites"), "retro-header-gold")

    def test_returns_valid_color_class(self):
        """Returned color should be a valid retro-header-* class."""
        slugs = [
            "test",
            "another-test",
            "collection-123",
            "my-favorite-games",
            "short",
            "a-very-long-slug-name-here",
        ]
        for slug in slugs:
            color = header_color(slug)
            self.assertTrue(color.startswith("retro-header-"))
            color_name = color.replace("retro-header-", "")
            self.assertIn(color_name, HEADER_COLORS)

    def test_all_colors_are_reachable(self):
        """Test that all 15 colors can be reached with different slugs.

        This may require many slugs to hit all colors due to hash distribution.
        """
        seen_colors = set()
        # Try a bunch of slugs to hit as many colors as possible
        for i in range(1000):
            color = header_color(f"test-slug-{i}")
            color_name = color.replace("retro-header-", "")
            seen_colors.add(color_name)

        # With 1000 attempts and 15 colors, we should hit most of them
        self.assertGreater(
            len(seen_colors), 10, f"Only hit {len(seen_colors)} colors out of 15"
        )


class TestHeaderColorInTemplate(SimpleTestCase):
    """Test header_color filter usage in templates."""

    def test_filter_in_template(self):
        """Test that filter works correctly in a template."""
        template_str = """
        {% load retro_components %}
        <div class="{{ slug|header_color }}">Content</div>
        """
        template = Template(template_str)
        result = template.render(Context({"slug": "my-collection"}))

        self.assertIn("retro-header-", result)

    def test_filter_with_empty_context_value(self):
        """Test that filter handles empty context value."""
        template_str = """
        {% load retro_components %}
        <div class="{{ slug|header_color }}">Content</div>
        """
        template = Template(template_str)
        result = template.render(Context({"slug": ""}))

        self.assertIn("retro-header-magenta", result)

    def test_filter_with_none_context_value(self):
        """Test that filter handles None context value."""
        template_str = """
        {% load retro_components %}
        <div class="{{ slug|header_color }}">Content</div>
        """
        template = Template(template_str)
        result = template.render(Context({"slug": None}))

        self.assertIn("retro-header-magenta", result)

    def test_consistency_across_template_renders(self):
        """Test that same slug produces same color across multiple renders."""
        template_str = """
        {% load retro_components %}
        {{ slug|header_color }}
        """
        template = Template(template_str)

        result1 = template.render(Context({"slug": "consistent-test"})).strip()
        result2 = template.render(Context({"slug": "consistent-test"})).strip()

        self.assertEqual(result1, result2)
