"""Tests for the retro_modal template tag."""

from django.template import Context, Template, TemplateSyntaxError
from django.test import SimpleTestCase


class TestRetroModalTemplateTag(SimpleTestCase):
    """Test the {% retro_modal %} template tag."""

    def test_retro_modal_basic_rendering(self):
        """Test basic modal rendering with required arguments."""
        template_str = """
        {% load retro_components %}
        {% retro_modal id="test" title="Test Modal" %}
        <div class="retro-modal-body">
            <p>Test content</p>
        </div>
        <div class="retro-modal-footer">
            <button data-modal-close="test" class="retro-btn">Cancel</button>
        </div>
        {% endretro_modal %}
        """
        template = Template(template_str)
        result = template.render(Context())

        self.assertIn("modal-test", result)
        self.assertIn("Test Modal", result)
        self.assertIn("retro-modal-backdrop", result)
        self.assertIn('data-modal-close="test"', result)
        self.assertIn("Test content", result)

    def test_retro_modal_without_close_button(self):
        """Test modal with close button disabled."""
        template_str = """
        {% load retro_components %}
        {% retro_modal id="test" title="Test Modal" close_btn=False %}
        <div class="retro-modal-body">
            <p>Content</p>
        </div>
        {% endretro_modal %}
        """
        template = Template(template_str)
        result = template.render(Context())

        self.assertNotIn("retro-modal-close", result)

    def test_retro_modal_with_close_button(self):
        """Test modal with close button enabled (default)."""
        template_str = """
        {% load retro_components %}
        {% retro_modal id="test" title="Test Modal" %}
        <div class="retro-modal-body"><p>Content</p></div>
        {% endretro_modal %}
        """
        template = Template(template_str)
        result = template.render(Context())

        self.assertIn("retro-modal-close", result)
        self.assertIn("&times;", result)


class TestRetroModalSizes(SimpleTestCase):
    """Test all size options for the retro_modal tag."""

    def _render_modal_with_size(self, size):
        """Helper to render modal with given size."""
        template_str = f"""
        {{% load retro_components %}}
        {{% retro_modal id="test" title="Test" size="{size}" %}}
        <p>Content</p>
        {{% endretro_modal %}}
        """
        template = Template(template_str)
        return template.render(Context())

    def test_size_sm(self):
        """Test small size modal."""
        result = self._render_modal_with_size("sm")
        self.assertIn("max-w-sm", result)
        self.assertNotIn("max-w-md", result)

    def test_size_md(self):
        """Test medium size modal (default)."""
        result = self._render_modal_with_size("md")
        self.assertIn("max-w-md", result)

    def test_size_lg(self):
        """Test large size modal."""
        result = self._render_modal_with_size("lg")
        self.assertIn("max-w-lg", result)

    def test_size_xl(self):
        """Test extra-large size modal."""
        result = self._render_modal_with_size("xl")
        self.assertIn("max-w-xl", result)

    def test_size_2xl(self):
        """Test 2xl size modal."""
        result = self._render_modal_with_size("2xl")
        self.assertIn("max-w-2xl", result)

    def test_default_size_when_not_specified(self):
        """Test that default size is md when not specified."""
        template_str = """
        {% load retro_components %}
        {% retro_modal id="test" title="Test" %}
        <p>Content</p>
        {% endretro_modal %}
        """
        template = Template(template_str)
        result = template.render(Context())
        self.assertIn("max-w-md", result)

    def test_invalid_size_defaults_to_md(self):
        """Test that invalid size falls back to md."""
        result = self._render_modal_with_size("invalid")
        self.assertIn("max-w-md", result)


class TestRetroModalAlpineIntegration(SimpleTestCase):
    """Test Alpine.js integration in the retro_modal tag."""

    def test_modal_has_x_data_attribute(self):
        """Test that modal has x-data for Alpine.js."""
        template_str = """
        {% load retro_components %}
        {% retro_modal id="test" title="Test" %}
        <p>Content</p>
        {% endretro_modal %}
        """
        template = Template(template_str)
        result = template.render(Context())

        self.assertIn("x-data", result)

    def test_modal_backdrop_click_closes(self):
        """Test that clicking backdrop triggers close via Alpine store."""
        template_str = """
        {% load retro_components %}
        {% retro_modal id="mymodal" title="Test" %}
        <p>Content</p>
        {% endretro_modal %}
        """
        template = Template(template_str)
        result = template.render(Context())

        # Check for backdrop click handler
        self.assertIn("@click.self", result)
        self.assertIn("$store.modals.close('mymodal')", result)

    def test_close_button_uses_alpine_store(self):
        """Test that close button uses Alpine store."""
        template_str = """
        {% load retro_components %}
        {% retro_modal id="mymodal" title="Test" %}
        <p>Content</p>
        {% endretro_modal %}
        """
        template = Template(template_str)
        result = template.render(Context())

        # Check close button has Alpine click handler
        self.assertIn("@click=\"$store.modals.close('mymodal')\"", result)

    def test_modal_lock_check_on_backdrop_click(self):
        """Test that modal lock is checked before closing on backdrop click."""
        template_str = """
        {% load retro_components %}
        {% retro_modal id="mymodal" title="Test" %}
        <p>Content</p>
        {% endretro_modal %}
        """
        template = Template(template_str)
        result = template.render(Context())

        # Check for lock check in backdrop click handler
        self.assertIn("$store.modals.isLocked('mymodal')", result)

    def test_modal_data_attribute(self):
        """Test that modal has data-modal attribute for identification."""
        template_str = """
        {% load retro_components %}
        {% retro_modal id="uniqueid" title="Test" %}
        <p>Content</p>
        {% endretro_modal %}
        """
        template = Template(template_str)
        result = template.render(Context())

        self.assertIn('data-modal="uniqueid"', result)


class TestRetroModalStructure(SimpleTestCase):
    """Test structural elements of the retro_modal."""

    def test_modal_id_has_prefix(self):
        """Test that modal ID gets 'modal-' prefix."""
        template_str = """
        {% load retro_components %}
        {% retro_modal id="test" title="Test" %}
        <p>Content</p>
        {% endretro_modal %}
        """
        template = Template(template_str)
        result = template.render(Context())

        self.assertIn('id="modal-test"', result)

    def test_modal_starts_hidden(self):
        """Test that modal is hidden by default."""
        template_str = """
        {% load retro_components %}
        {% retro_modal id="test" title="Test" %}
        <p>Content</p>
        {% endretro_modal %}
        """
        template = Template(template_str)
        result = template.render(Context())

        self.assertIn("hidden", result)

    def test_modal_has_fixed_position(self):
        """Test that modal uses fixed positioning for overlay."""
        template_str = """
        {% load retro_components %}
        {% retro_modal id="test" title="Test" %}
        <p>Content</p>
        {% endretro_modal %}
        """
        template = Template(template_str)
        result = template.render(Context())

        self.assertIn("fixed", result)
        self.assertIn("inset-0", result)

    def test_modal_has_z_index(self):
        """Test that modal has z-index for stacking."""
        template_str = """
        {% load retro_components %}
        {% retro_modal id="test" title="Test" %}
        <p>Content</p>
        {% endretro_modal %}
        """
        template = Template(template_str)
        result = template.render(Context())

        self.assertIn("z-50", result)

    def test_modal_header_structure(self):
        """Test modal header contains title and is properly structured."""
        template_str = """
        {% load retro_components %}
        {% retro_modal id="test" title="My Title" %}
        <p>Content</p>
        {% endretro_modal %}
        """
        template = Template(template_str)
        result = template.render(Context())

        self.assertIn("retro-modal-header", result)
        self.assertIn("retro-modal-title", result)
        self.assertIn("My Title", result)

    def test_nodelist_content_rendered(self):
        """Test that content between tags is rendered."""
        template_str = """
        {% load retro_components %}
        {% retro_modal id="test" title="Test" %}
        <div class="custom-content">Custom content here</div>
        {% endretro_modal %}
        """
        template = Template(template_str)
        result = template.render(Context())

        self.assertIn("custom-content", result)
        self.assertIn("Custom content here", result)


class TestRetroModalErrors(SimpleTestCase):
    """Test error handling for retro_modal tag."""

    def test_missing_id_raises_error(self):
        """Test that missing id argument raises TemplateSyntaxError."""
        template_str = """
        {% load retro_components %}
        {% retro_modal title="Test" %}
        <p>Content</p>
        {% endretro_modal %}
        """
        with self.assertRaises(TemplateSyntaxError):
            Template(template_str)

    def test_missing_title_raises_error(self):
        """Test that missing title argument raises TemplateSyntaxError."""
        template_str = """
        {% load retro_components %}
        {% retro_modal id="test" %}
        <p>Content</p>
        {% endretro_modal %}
        """
        with self.assertRaises(TemplateSyntaxError):
            Template(template_str)

    def test_no_arguments_raises_error(self):
        """Test that no arguments raises TemplateSyntaxError."""
        template_str = """
        {% load retro_components %}
        {% retro_modal %}
        <p>Content</p>
        {% endretro_modal %}
        """
        with self.assertRaises(TemplateSyntaxError):
            Template(template_str)

    def test_invalid_argument_format_raises_error(self):
        """Test that invalid argument format raises TemplateSyntaxError."""
        template_str = """
        {% load retro_components %}
        {% retro_modal id="test" title="Test" invalid_arg %}
        <p>Content</p>
        {% endretro_modal %}
        """
        with self.assertRaises(TemplateSyntaxError):
            Template(template_str)


class TestRetroModalContextVariables(SimpleTestCase):
    """Test that retro_modal works with context variables."""

    def test_id_from_context(self):
        """Test that id can come from context variable."""
        template_str = """
        {% load retro_components %}
        {% retro_modal id=modal_id title="Test" %}
        <p>Content</p>
        {% endretro_modal %}
        """
        template = Template(template_str)
        result = template.render(Context({"modal_id": "dynamic-id"}))

        self.assertIn('id="modal-dynamic-id"', result)
        self.assertIn('data-modal="dynamic-id"', result)

    def test_title_from_context(self):
        """Test that title can come from context variable."""
        template_str = """
        {% load retro_components %}
        {% retro_modal id="test" title=modal_title %}
        <p>Content</p>
        {% endretro_modal %}
        """
        template = Template(template_str)
        result = template.render(Context({"modal_title": "Dynamic Title"}))

        self.assertIn("Dynamic Title", result)

    def test_size_from_context(self):
        """Test that size can come from context variable."""
        template_str = """
        {% load retro_components %}
        {% retro_modal id="test" title="Test" size=modal_size %}
        <p>Content</p>
        {% endretro_modal %}
        """
        template = Template(template_str)
        result = template.render(Context({"modal_size": "xl"}))

        self.assertIn("max-w-xl", result)
