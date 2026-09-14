"""Tests run against a dedicated PostgreSQL database (TEST_DATABASE_URL) that is rebuilt from the migrations on every
run. The environment is set before the app is imported, so tests never touch development data or call AI providers."""

import os
from pathlib import Path

import pytest
from dotenv import load_dotenv
from sqlalchemy.engine import make_url

BACKEND_DIR = Path(__file__).resolve().parents[1]
load_dotenv(BACKEND_DIR / ".env")

_test_database_url = os.environ.get("TEST_DATABASE_URL", "")
if not _test_database_url:
    pytest.exit(
        "TEST_DATABASE_URL is not set. Run backend/scripts/setup_local_services.sh (see README.md).", returncode=2
    )
if not (make_url(_test_database_url).database or "").endswith("_test"):
    pytest.exit(
        "TEST_DATABASE_URL must name a database ending in _test, because every test run erases it.", returncode=2
    )

os.environ["DATABASE_URL"] = _test_database_url
os.environ["JWT_SECRET_KEY"] = "test-only-secret-key-with-plenty-of-length-0123456789"
os.environ["CORS_ORIGINS"] = "http://localhost:5173"
os.environ["APP_ENV"] = "test"
os.environ["SENTRY_DSN"] = ""
os.environ["REDIS_URL"] = ""
os.environ["BCRYPT_ROUNDS"] = "4"
for provider_key in ("GEMINI_API_KEY", "OPENAI_API_KEY", "ANTHROPIC_API_KEY"):
    os.environ[provider_key] = ""

from alembic import command  # noqa: E402
from alembic.config import Config  # noqa: E402
from factories import PASSWORD, registration  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

import main  # noqa: E402
import rate_limiting  # noqa: E402

# Start from an empty schema each run; this also checks that every migration downgrades and upgrades cleanly.
_alembic_config = Config(str(BACKEND_DIR / "alembic.ini"))
command.downgrade(_alembic_config, "base")
command.upgrade(_alembic_config, "head")


@pytest.fixture(autouse=True)
def reset_rate_limits():
    rate_limiting.reset_rate_limits()


@pytest.fixture
def client():
    with TestClient(main.app) as test_client:
        yield test_client


@pytest.fixture
def login(client):
    """Sign in and return auth headers."""

    def sign_in(identifier: str, password: str = PASSWORD) -> dict[str, str]:
        response = client.post("/api/auth/login", json={"identifier": identifier, "password": password})
        assert response.status_code == 200, response.text
        return {"Authorization": f"Bearer {response.json()['access_token']}"}

    return sign_in


@pytest.fixture
def make_user(client, login):
    """Register and sign in a new user; returns (email, auth headers)."""

    def create(**overrides) -> tuple[str, dict[str, str]]:
        user = registration(**overrides)
        response = client.post("/api/auth/register", json=user)
        assert response.status_code == 200, response.text
        return user["email"], login(user["email"])

    return create


@pytest.fixture
def auth_headers(make_user):
    return make_user()[1]


@pytest.fixture
def fake_provider(monkeypatch):
    """Replace the network call to AI providers with a canned response text."""

    def install(response_text: str):
        async def fake_call(prompt, provider, json_output, max_tokens):
            return response_text

        monkeypatch.setattr("services.llm._call_provider", fake_call)

    return install
