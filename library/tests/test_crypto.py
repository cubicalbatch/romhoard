"""Tests for the encryption utilities."""

import pytest

from library.crypto import (
    SENSITIVE_KEYS,
    decrypt_value,
    encrypt_value,
    is_sensitive_key,
)
from library.models import Setting


class TestCryptoFunctions:
    """Test the low-level crypto functions."""

    def test_encrypt_adds_prefix(self):
        """Test that encrypted values have the enc: prefix."""
        encrypted = encrypt_value("secret")
        assert encrypted.startswith("enc:")

    def test_encrypt_decrypt_roundtrip(self):
        """Test that encrypt/decrypt roundtrip works correctly."""
        original = "my_secret_password"
        encrypted = encrypt_value(original)
        decrypted = decrypt_value(encrypted)
        assert decrypted == original

    def test_decrypt_unencrypted_returns_as_is(self):
        """Test that unencrypted values are returned as-is (legacy support)."""
        plaintext = "old_plaintext_password"
        result = decrypt_value(plaintext)
        assert result == plaintext

    def test_decrypt_invalid_token_returns_none(self):
        """Test that invalid ciphertext returns None."""
        # Simulate SECRET_KEY change by using wrong ciphertext
        invalid = "enc:gAAAAABinvalidtokenxyz"
        result = decrypt_value(invalid)
        assert result is None

    def test_is_sensitive_key_for_password(self):
        """Test that screenscraper_password is marked as sensitive."""
        assert is_sensitive_key("screenscraper_password") is True

    def test_is_sensitive_key_for_non_sensitive(self):
        """Test that non-sensitive keys return False."""
        assert is_sensitive_key("screenscraper_user") is False
        assert is_sensitive_key("metadata_image_path") is False
        assert is_sensitive_key("library_root") is False

    def test_sensitive_keys_includes_password(self):
        """Test that SENSITIVE_KEYS includes the expected keys."""
        assert "screenscraper_password" in SENSITIVE_KEYS


@pytest.mark.django_db
class TestSettingEncryption:
    """Test Setting model encryption integration."""

    def test_set_sensitive_key_encrypts_value(self):
        """Test that Setting.set encrypts sensitive keys."""
        Setting.set("screenscraper_password", "secret123")

        # Check raw database value is encrypted
        setting = Setting.objects.get(key="screenscraper_password")
        assert setting.value.startswith("enc:")
        assert "secret123" not in setting.value

    def test_get_sensitive_key_decrypts_value(self):
        """Test that Setting.get decrypts sensitive keys."""
        Setting.set("screenscraper_password", "secret123")
        result = Setting.get("screenscraper_password")
        assert result == "secret123"

    def test_non_sensitive_key_not_encrypted(self):
        """Test that non-sensitive keys are stored as plain text."""
        Setting.set("screenscraper_user", "myusername")

        # Check raw database value is NOT encrypted
        setting = Setting.objects.get(key="screenscraper_user")
        assert setting.value == "myusername"

    def test_get_non_sensitive_key_returns_value(self):
        """Test that non-sensitive keys are retrieved normally."""
        Setting.set("library_root", "/my/path")
        result = Setting.get("library_root")
        assert result == "/my/path"

    def test_decryption_failure_clears_setting(self):
        """Test that decryption failure clears the invalid credential."""
        # Store encrypted password
        Setting.set("screenscraper_password", "secret123")
        # Also store valid credentials marker
        Setting.set("screenscraper_credentials_valid", True)

        # Corrupt the encrypted value to simulate SECRET_KEY change
        setting = Setting.objects.get(key="screenscraper_password")
        setting.value = "enc:gAAAAABinvalidtokenxyz"
        setting.save()

        # Attempt to get should return default and clear the setting
        result = Setting.get("screenscraper_password", default="default_val")
        assert result == "default_val"

        # Setting should be deleted
        assert not Setting.objects.filter(key="screenscraper_password").exists()

        # Credentials valid marker should also be deleted
        assert not Setting.objects.filter(
            key="screenscraper_credentials_valid"
        ).exists()

    def test_legacy_plaintext_password_still_works(self):
        """Test that legacy unencrypted passwords still work."""
        # Directly create a setting with plain text (legacy data)
        Setting.objects.create(key="screenscraper_password", value="old_password")

        # Get should return the plain text
        result = Setting.get("screenscraper_password")
        assert result == "old_password"

    def test_get_missing_key_returns_default(self):
        """Test that getting a missing key returns the default."""
        result = Setting.get("nonexistent_key", default="fallback")
        assert result == "fallback"

    def test_encrypt_empty_string(self):
        """Test that empty strings can be encrypted/decrypted."""
        Setting.set("screenscraper_password", "")
        result = Setting.get("screenscraper_password")
        assert result == ""

    def test_encrypt_special_characters(self):
        """Test that special characters are handled correctly."""
        special = "p@ss!w0rd#$%^&*()[]{}|;':\",./<>?`~"
        Setting.set("screenscraper_password", special)
        result = Setting.get("screenscraper_password")
        assert result == special

    def test_encrypt_unicode(self):
        """Test that unicode characters are handled correctly."""
        unicode_pass = "p\u00e4ssw\u00f6rd\U0001f512"  # with umlaut and emoji
        Setting.set("screenscraper_password", unicode_pass)
        result = Setting.get("screenscraper_password")
        assert result == unicode_pass

    def test_update_encrypted_value(self):
        """Test that updating an encrypted value works correctly."""
        Setting.set("screenscraper_password", "old_secret")
        Setting.set("screenscraper_password", "new_secret")

        result = Setting.get("screenscraper_password")
        assert result == "new_secret"

        # Verify only one setting exists
        assert Setting.objects.filter(key="screenscraper_password").count() == 1
