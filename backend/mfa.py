"""Two-factor sign-in with an authenticator app: time-based one-time passwords (RFC 6238, SHA-1, 6 digits, 30-second
steps, as Google Authenticator, 1Password, and others expect) and single-use recovery codes.

The shared secret is encrypted at rest (crypto.py). A code is accepted for its own time step or one step either side,
to allow for clock drift, and never for a step at or before the last one accepted, so an intercepted code can't be
replayed. Recovery codes are stored as hashes and removed when used.
"""

import base64
import hashlib
import hmac
import os
import secrets
import time
from urllib.parse import quote

import crypto
import models

STEP_SECONDS = 30
DIGITS = 6
DRIFT_STEPS = 1
RECOVERY_CODES = 10


def issuer() -> str:
    return os.getenv("MFA_ISSUER", "OmniReview")


def secret_context(user_id: int) -> str:
    return f"mfa:{user_id}"


def new_secret() -> str:
    return base64.b32encode(secrets.token_bytes(20)).decode().rstrip("=")


def provisioning_uri(secret: str, account: str) -> str:
    """The otpauth:// link authenticator apps read (usually from a QR code), or which can be pasted into them."""
    label = quote(f"{issuer()}:{account}")
    return (
        f"otpauth://totp/{label}?secret={secret}&issuer={quote(issuer())}"
        f"&algorithm=SHA1&digits={DIGITS}&period={STEP_SECONDS}"
    )


def code_at(secret: str, step: int) -> str:
    key = base64.b32decode(secret + "=" * (-len(secret) % 8), casefold=True)
    digest = hmac.new(key, step.to_bytes(8, "big"), hashlib.sha1).digest()
    offset = digest[-1] & 0x0F
    number = (int.from_bytes(digest[offset : offset + 4], "big") & 0x7FFFFFFF) % 10**DIGITS
    return f"{number:0{DIGITS}d}"


def current_step(now: float | None = None) -> int:
    return int((time.time() if now is None else now) // STEP_SECONDS)


def matching_step(secret: str, code: str, now: float | None = None) -> int | None:
    """The time step the code belongs to, or None when it doesn't match any step in the drift window."""
    cleaned = "".join(character for character in code if character.isdigit())
    if len(cleaned) != DIGITS:
        return None
    step = current_step(now)
    for candidate in range(step - DRIFT_STEPS, step + DRIFT_STEPS + 1):
        if hmac.compare_digest(code_at(secret, candidate), cleaned):
            return candidate
    return None


def _normalize_recovery(code: str) -> str:
    return "".join(character for character in code.lower() if character.isalnum())


def hash_recovery_code(code: str) -> str:
    return hashlib.sha256(_normalize_recovery(code).encode()).hexdigest()


def new_recovery_codes() -> list[str]:
    codes = []
    for _ in range(RECOVERY_CODES):
        raw = secrets.token_hex(5)
        codes.append(f"{raw[:5]}-{raw[5:]}")
    return codes


def user_secret(user: models.User) -> str | None:
    if user.mfa_secret_encrypted is None:
        return None
    return crypto.decrypt(user.mfa_secret_encrypted, secret_context(user.id))


def verify_totp(user: models.User, code: str, now: float | None = None) -> bool:
    """Check an authenticator code against the user's secret, rejecting replays. Updates mfa_last_step on success."""
    secret = user_secret(user)
    if secret is None:
        return False
    step = matching_step(secret, code, now)
    if step is None or (user.mfa_last_step is not None and step <= user.mfa_last_step):
        return False
    user.mfa_last_step = step
    return True


def use_recovery_code(user: models.User, code: str) -> bool:
    """Accept a recovery code once. Updates the user's remaining codes on success."""
    digest = hash_recovery_code(code)
    remaining = list(user.mfa_recovery_hashes or [])
    if digest not in remaining:
        return False
    remaining.remove(digest)
    user.mfa_recovery_hashes = remaining
    return True


def verify_second_factor(user: models.User, code: str, now: float | None = None) -> bool:
    """An authenticator code or, failing that, an unused recovery code."""
    if verify_totp(user, code, now):
        return True
    return use_recovery_code(user, code)
