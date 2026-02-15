"""Encryption utilities for sensitive settings.

Uses Fernet symmetric encryption derived from Django's SECRET_KEY.
Handles SECRET_KEY changes gracefully by detecting decryption failures.
"""

import base64
import hashlib

from cryptography.fernet import Fernet, InvalidToken
from django.conf import settings

# Keys that should be encrypted in the database
SENSITIVE_KEYS = {"screenscraper_password"}


def _get_fernet() -> Fernet:
    """Derive a Fernet key from Django's SECRET_KEY."""
    key = hashlib.sha256(settings.SECRET_KEY.encode()).digest()
    return Fernet(base64.urlsafe_b64encode(key))


def encrypt_value(plaintext: str) -> str:
    """Encrypt a string value. Returns prefixed ciphertext."""
    ciphertext = _get_fernet().encrypt(plaintext.encode()).decode()
    return f"enc:{ciphertext}"


def decrypt_value(stored_value: str) -> str | None:
    """Decrypt a stored value.

    Returns None if decryption fails (e.g., SECRET_KEY changed).
    Returns plain text as-is if not encrypted (legacy values).
    """
    if not stored_value.startswith("enc:"):
        # Not encrypted, return as-is (legacy plain text)
        return stored_value
    try:
        ciphertext = stored_value[4:]  # Strip "enc:" prefix
        return _get_fernet().decrypt(ciphertext.encode()).decode()
    except InvalidToken:
        # SECRET_KEY changed or corrupted - return None
        return None


def is_sensitive_key(key: str) -> bool:
    """Check if a setting key should be encrypted."""
    return key in SENSITIVE_KEYS
