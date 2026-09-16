"""Refuse to start in production with settings that would be unsafe to serve real users with.

Only settings that are dangerous rather than merely incomplete stop the server; scripts/production_check.py reports
everything else. Development and tests are unaffected.
"""

import base64
import binascii
import os

import emailer


class UnsafeProductionConfig(RuntimeError):
    pass


def problems() -> list[str]:
    found = []
    key = os.getenv("DATA_ENCRYPTION_KEY", "")
    try:
        if len(base64.urlsafe_b64decode(key)) != 32:
            found.append("DATA_ENCRYPTION_KEY must be 32 random bytes, base64-encoded")
    except (binascii.Error, ValueError):
        found.append("DATA_ENCRYPTION_KEY must be 32 random bytes, base64-encoded")
    origins = [origin.strip() for origin in os.getenv("CORS_ORIGINS", "").split(",") if origin.strip()]
    if not origins or any(origin == "*" or "localhost" in origin or "127.0.0.1" in origin for origin in origins):
        found.append("CORS_ORIGINS must list your site's https addresses, without wildcards or localhost")
    if os.getenv("BILLING_PROVIDER", "").strip().lower() == "dev":
        found.append("BILLING_PROVIDER=dev simulates payments and can't be used in production")
    if emailer.backend() == "console":
        found.append("EMAIL_BACKEND=console writes emails (including password reset links) to the log")
    if os.getenv("ALLOW_PRIVATE_WEBHOOK_URLS", "").strip().lower() == "true":
        found.append("ALLOW_PRIVATE_WEBHOOK_URLS=true would let webhooks reach your private network")
    return found


def enforce() -> None:
    if os.getenv("APP_ENV", "development") != "production":
        return
    found = problems()
    if found:
        raise UnsafeProductionConfig("Refusing to start in production: " + "; ".join(found))
