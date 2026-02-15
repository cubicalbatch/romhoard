"""Tests for scan views."""

from unittest.mock import patch

from django.test import TestCase, Client
from django.urls import reverse


class TestScanFormHasheousDefault(TestCase):
    """Test Hasheous checkbox default state based on ScreenScraper configuration."""

    def setUp(self):
        self.client = Client()
        self.url = reverse("library:scan")

    def test_hasheous_checked_when_screenscraper_not_configured(self):
        """Hasheous checkbox should be checked by default when ScreenScraper is not configured."""
        with patch(
            "library.views.scan.screenscraper_available", return_value=False
        ):
            response = self.client.get(self.url)

        self.assertEqual(response.status_code, 200)
        # Check that the checkbox has 'checked' attribute
        content = response.content.decode()
        # Find the use_hasheous checkbox input and verify it has 'checked'
        self.assertIn('name="use_hasheous"', content)
        # The checkbox should have 'checked' when screenscraper is not available
        # Look for the pattern where 'checked' appears after the id="use_hasheous"
        checkbox_section = content[content.find('id="use_hasheous"') :]
        checkbox_section = checkbox_section[: checkbox_section.find(">") + 1]
        self.assertIn("checked", checkbox_section)

    def test_hasheous_unchecked_when_screenscraper_configured(self):
        """Hasheous checkbox should be unchecked by default when ScreenScraper is configured."""
        with patch(
            "library.views.scan.screenscraper_available", return_value=True
        ):
            response = self.client.get(self.url)

        self.assertEqual(response.status_code, 200)
        content = response.content.decode()
        # Find the use_hasheous checkbox input and verify it does NOT have 'checked'
        self.assertIn('name="use_hasheous"', content)
        # The checkbox should NOT have 'checked' when screenscraper is available
        # Look for the pattern where 'checked' would appear after the id="use_hasheous"
        checkbox_section = content[content.find('id="use_hasheous"') :]
        checkbox_section = checkbox_section[: checkbox_section.find(">") + 1]
        self.assertNotIn("checked", checkbox_section)

    def test_screenscraper_configured_passed_to_context(self):
        """The screenscraper_configured variable should be passed to template context."""
        with patch(
            "library.views.scan.screenscraper_available", return_value=True
        ):
            response = self.client.get(self.url)

        self.assertEqual(response.status_code, 200)
        self.assertIn("screenscraper_configured", response.context)
        self.assertTrue(response.context["screenscraper_configured"])

        with patch(
            "library.views.scan.screenscraper_available", return_value=False
        ):
            response = self.client.get(self.url)

        self.assertEqual(response.status_code, 200)
        self.assertIn("screenscraper_configured", response.context)
        self.assertFalse(response.context["screenscraper_configured"])
