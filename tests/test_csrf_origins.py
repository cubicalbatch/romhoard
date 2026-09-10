"""Tests for CSRF origin handling and CSRF_TRUSTED_ORIGINS settings."""

import importlib
import os
from unittest.mock import patch

from django.test import Client, TestCase


class TestCsrfTrustedOrigins(TestCase):
    """Test CSRF trusted origins configuration and behavior."""

    def test_post_with_untrusted_origin_is_rejected(self):
        """A POST request with an untrusted Origin header should be rejected (403)."""
        client = Client(enforce_csrf_checks=True)
        # Without trusting romhoard.loki.onoz.cc, origin check should fail on HTTPS origin
        response = client.post(
            "/upload/check-duplicates/",
            HTTP_ORIGIN="https://romhoard.loki.onoz.cc",
            headers={"origin": "https://romhoard.loki.onoz.cc"},
        )
        assert response.status_code == 403
        assert b"Origin checking failed" in response.content or response.status_code == 403

    def test_post_with_trusted_origin_passes_origin_check(self):
        """A POST request with a trusted Origin header should pass origin checking."""
        client = Client(enforce_csrf_checks=True)
        with self.settings(CSRF_TRUSTED_ORIGINS=["https://romhoard.loki.onoz.cc"]):
            response_get = client.get("/upload/")
            csrf_token = response_get.cookies.get("csrftoken")
            token_val = csrf_token.value if csrf_token else ""

            response = client.post(
                "/upload/check-duplicates/",
                data="[]",
                content_type="application/json",
                HTTP_ORIGIN="https://romhoard.loki.onoz.cc",
                HTTP_X_CSRFTOKEN=token_val,
                headers={"origin": "https://romhoard.loki.onoz.cc"},
            )
            # Should NOT be 403 Forbidden due to origin check
            assert response.status_code != 403

    def test_csrf_trusted_origins_env_var_parsing(self):
        """Test that CSRF_TRUSTED_ORIGINS env var is parsed correctly in settings."""
        from romhoard import settings as rh_settings

        with patch.dict(
            os.environ,
            {"CSRF_TRUSTED_ORIGINS": "https://romhoard.loki.onoz.cc, http://example.com, unadorned.org"},
        ):
            try:
                importlib.reload(rh_settings)
                assert "https://romhoard.loki.onoz.cc" in rh_settings.CSRF_TRUSTED_ORIGINS
                assert "http://example.com" in rh_settings.CSRF_TRUSTED_ORIGINS
                assert "https://unadorned.org" in rh_settings.CSRF_TRUSTED_ORIGINS
                assert "http://unadorned.org" in rh_settings.CSRF_TRUSTED_ORIGINS
            finally:
                importlib.reload(rh_settings)

    def test_allowed_hosts_auto_populates_csrf_origins(self):
        """Test that specific hosts in ALLOWED_HOSTS are automatically added to CSRF_TRUSTED_ORIGINS."""
        from romhoard import settings as rh_settings

        with patch.dict(
            os.environ,
            {"ALLOWED_HOSTS": "romhoard.loki.onoz.cc", "CSRF_TRUSTED_ORIGINS": ""},
        ):
            try:
                importlib.reload(rh_settings)
                assert "https://romhoard.loki.onoz.cc" in rh_settings.CSRF_TRUSTED_ORIGINS
                assert "http://romhoard.loki.onoz.cc" in rh_settings.CSRF_TRUSTED_ORIGINS
            finally:
                importlib.reload(rh_settings)
