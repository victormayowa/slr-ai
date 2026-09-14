"""Encryption for secrets stored in the database, such as users' own API keys (AES-256-GCM)."""

import base64
import binascii
import os

from cryptography.hazmat.primitives.ciphers.aead import AESGCM

_NONCE_BYTES = 12


class EncryptionNotConfigured(RuntimeError):
    pass


def _key() -> bytes:
    try:
        key = base64.urlsafe_b64decode(os.getenv("DATA_ENCRYPTION_KEY", ""))
    except (binascii.Error, ValueError):
        key = b""
    if len(key) != 32:
        raise EncryptionNotConfigured(
            "DATA_ENCRYPTION_KEY must be 32 random bytes, base64-encoded. Generate one with: "
            'python -c "import base64, os; print(base64.urlsafe_b64encode(os.urandom(32)).decode())"'
        )
    return key


def encrypt(plaintext: str, context: str) -> bytes:
    """Encrypt and bind the result to `context` (for example the owning user), so it can't be moved to another row."""
    nonce = os.urandom(_NONCE_BYTES)
    return nonce + AESGCM(_key()).encrypt(nonce, plaintext.encode(), context.encode())


def decrypt(ciphertext: bytes, context: str) -> str:
    return AESGCM(_key()).decrypt(ciphertext[:_NONCE_BYTES], ciphertext[_NONCE_BYTES:], context.encode()).decode()
