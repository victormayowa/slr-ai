"""The production readiness checklist, as checks that run against the real configuration and services.

    uv run python -m scripts.production_check            # print every check
    uv run python -m scripts.production_check --strict   # exit 1 when any check fails (use in deploys)

Run it on the production server (inside the API container: docker compose -f docker-compose.prod.yml exec api
python -m scripts.production_check --strict). A "fail" must be fixed before launch; a "warn" is a decision to make
consciously. docs/production-readiness.md explains each item, including the ones that can't be checked by software
(legal review, penetration test, accessibility audit, load test).
"""

import argparse
import base64
import binascii
import os
import sys
from pathlib import Path
from urllib.parse import urlparse

from sqlalchemy import select
from sqlalchemy.orm import Session

import models
import ops
from ops import Check

BACKEND_DIR = Path(__file__).resolve().parents[1]
LEGAL_DIR = Path(os.getenv("LEGAL_DOCS_DIR", str(BACKEND_DIR.parent / "docs" / "legal")))
DEV_JWT_SECRET = "dev-only-insecure-secret-do-not-use-in-production-0123"
DEV_ENCRYPTION_KEY = "ZGV2LW9ubHktaW5zZWN1cmUtZW5jcnlwdGlvbi1rZXk="
REQUIRED_LEGAL = ("terms", "privacy", "cookies", "subprocessors", "dpa", "acceptable-use", "copyright")
PLACEHOLDER = "[TO COMPLETE"


def env(name: str) -> str:
    return os.getenv(name, "").strip()


def _https(value: str) -> bool:
    parsed = urlparse(value)
    return parsed.scheme == "https" and bool(parsed.hostname) and parsed.hostname not in ("localhost", "127.0.0.1")


def configuration_checks() -> list[Check]:
    checks: list[Check] = []

    def add(name: str, ok: bool, good: str, bad: str, severity: str = "fail") -> None:
        checks.append(Check(name, "ok" if ok else severity, good if ok else bad))

    add("app_env", env("APP_ENV") == "production", "APP_ENV=production", "Set APP_ENV=production")
    secret = env("JWT_SECRET_KEY")
    add(
        "jwt_secret",
        len(secret) >= 48 and secret != DEV_JWT_SECRET,
        "A strong session signing key is set",
        "JWT_SECRET_KEY must be a new random value of at least 48 characters, not the development default",
    )
    key = env("DATA_ENCRYPTION_KEY")
    try:
        valid_key = len(base64.urlsafe_b64decode(key)) == 32 and key != DEV_ENCRYPTION_KEY
    except (binascii.Error, ValueError):
        valid_key = False
    add(
        "encryption_key",
        valid_key,
        "The data encryption key is set (keep an offline copy: losing it makes saved keys and two-factor secrets "
        "unreadable)",
        "DATA_ENCRYPTION_KEY must be 32 random bytes, base64-encoded, and not the development default",
    )
    database_url = env("DATABASE_URL")
    add(
        "database_password",
        bool(database_url) and "CHANGE_ME" not in database_url and "omnireview-dev-password" not in database_url,
        "The database uses its own password",
        "DATABASE_URL still uses a placeholder or the development password",
    )
    origins = [origin.strip() for origin in env("CORS_ORIGINS").split(",") if origin.strip()]
    add(
        "cors",
        bool(origins) and all(_https(origin) for origin in origins),
        f"CORS allows {', '.join(origins)}",
        "CORS_ORIGINS must list only your https site addresses",
    )
    add("app_url", _https(env("APP_URL")), f"APP_URL is {env('APP_URL')}", "APP_URL must be your https site address")
    add(
        "password_hashing",
        int(env("BCRYPT_ROUNDS") or "12") >= 12,
        "Passwords are hashed with bcrypt cost 12 or more",
        "BCRYPT_ROUNDS must be at least 12 (lower values are only for tests)",
    )
    add(
        "email_backend",
        env("EMAIL_BACKEND") in ("", "smtp") and bool(env("SMTP_HOST") and env("SMTP_FROM")),
        f"Email goes through {env('SMTP_HOST')}",
        "Set SMTP_HOST and SMTP_FROM (and EMAIL_BACKEND=smtp): password resets need real email",
    )
    add(
        "email_verification",
        env("REQUIRE_EMAIL_VERIFICATION").lower() == "true",
        "New accounts must confirm their email address",
        "REQUIRE_EMAIL_VERIFICATION is off, so accounts can use addresses they don't own",
        "warn",
    )
    add(
        "webhook_urls",
        env("ALLOW_PRIVATE_WEBHOOK_URLS").lower() != "true",
        "Webhooks can only reach public addresses",
        "ALLOW_PRIVATE_WEBHOOK_URLS must not be true in production (it allows requests into your network)",
    )
    add(
        "document_storage",
        bool(env("DOCUMENT_STORAGE_DIR")),
        f"Documents are stored in {env('DOCUMENT_STORAGE_DIR')}",
        "Set DOCUMENT_STORAGE_DIR to a persistent, backed-up volume",
    )
    add(
        "error_reporting", bool(env("SENTRY_DSN")), "Errors go to Sentry", "Set SENTRY_DSN to hear about errors", "warn"
    )
    add(
        "validated_models",
        env("REQUIRE_VALIDATED_MODELS").lower() == "true",
        "Only benchmarked AI models can be pinned",
        "REQUIRE_VALIDATED_MODELS is off, so projects can pin models that haven't passed the benchmarks",
        "warn",
    )
    add(
        "billing_enabled",
        env("BILLING_ENABLED").lower() == "true",
        "Plan limits are enforced",
        "BILLING_ENABLED is off, so every account is unlimited",
        "warn",
    )
    provider = env("BILLING_PROVIDER") or "manual"
    add(
        "billing_provider",
        provider != "dev",
        f"Payments use the {provider} provider",
        "BILLING_PROVIDER=dev simulates payments and must not be used in production",
    )
    if provider == "stripe":
        add(
            "stripe",
            env("STRIPE_SECRET_KEY").startswith("sk_live_") and env("STRIPE_WEBHOOK_SECRET").startswith("whsec_"),
            "Stripe live keys and the webhook secret are set",
            "Set STRIPE_SECRET_KEY (a live sk_live_ key) and STRIPE_WEBHOOK_SECRET",
        )
    add(
        "literature_contacts",
        bool(env("NCBI_EMAIL") or env("OPENALEX_EMAIL")) and bool(env("UNPAYWALL_EMAIL") or env("CROSSREF_EMAIL")),
        "Contact addresses are set for NCBI/OpenAlex and Unpaywall/Crossref",
        "Set NCBI_EMAIL (or OPENALEX_EMAIL) and UNPAYWALL_EMAIL: the services' terms ask for a contact address",
        "warn",
    )
    for slug in REQUIRED_LEGAL:
        path = LEGAL_DIR / f"{slug}.md"
        if not path.is_file():
            checks.append(Check(f"legal_{slug}", "fail", f"docs/legal/{slug}.md is missing"))
        elif PLACEHOLDER in path.read_text():
            checks.append(Check(f"legal_{slug}", "fail", f"docs/legal/{slug}.md still has {PLACEHOLDER} placeholders"))
        else:
            checks.append(Check(f"legal_{slug}", "ok", f"docs/legal/{slug}.md is complete"))
    return checks


def database_checks(db: Session) -> list[Check]:
    checks = []
    admins = db.scalars(
        select(models.User.email).where(models.User.is_platform_admin.is_(True), models.User.is_active.is_(True))
    ).all()
    checks.append(
        Check("administrator", "ok", f"{len(admins)} platform administrator(s)")
        if admins
        else Check(
            "administrator", "fail", "No platform administrator: run python -m scripts.make_admin you@example.org"
        )
    )
    demo = db.scalar(select(models.User.id).where(models.User.email.like("%@omnireview.test")).limit(1))
    checks.append(
        Check("demo_accounts", "fail", "Demo accounts from scripts.seed_dev exist; remove them from production")
        if demo
        else Check("demo_accounts", "ok", "No demo accounts")
    )
    default_model = db.scalar(
        select(models.AIModel).where(models.AIModel.is_default.is_(True), models.AIModel.purpose == "chat")
    )
    checks.append(
        Check("default_model", "ok", f"Default model: {default_model.label}")
        if default_model is not None
        else Check("default_model", "warn", "No default chat model, so new projects and the help assistant have none")
    )
    return checks


def run_checks(db: Session) -> list[Check]:
    return configuration_checks() + ops.system_status(db) + database_checks(db)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--strict", action="store_true", help="exit with status 1 when any check fails")
    args = parser.parse_args()
    from database import SessionLocal

    with SessionLocal() as db:
        checks = run_checks(db)
    symbols = {"ok": "PASS", "warn": "WARN", "fail": "FAIL"}
    for check in checks:
        print(f"[{symbols[check.status]}] {check.name}: {check.detail}")
    failed = [check for check in checks if check.status == "fail"]
    warned = [check for check in checks if check.status == "warn"]
    print(f"\n{len(checks) - len(failed) - len(warned)} passed, {len(warned)} warnings, {len(failed)} failed")
    return 1 if args.strict and failed else 0


if __name__ == "__main__":
    sys.exit(main())
