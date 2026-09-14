"""Environment is set before the app is imported, so tests never touch real data or call AI providers."""

import os
import tempfile

import pytest

_test_db_dir = tempfile.mkdtemp(prefix="omnireview-tests-")
os.environ["DATABASE_URL"] = os.environ.get("TEST_DATABASE_URL", f"sqlite:///{_test_db_dir}/test.db")
os.environ["JWT_SECRET_KEY"] = "test-only-secret-key-with-plenty-of-length-0123456789"
os.environ["CORS_ORIGINS"] = "http://localhost:5173"
os.environ["SENTRY_DSN"] = ""
for provider_key in ("GEMINI_API_KEY", "OPENAI_API_KEY", "ANTHROPIC_API_KEY"):
    os.environ[provider_key] = ""

from factories import PASSWORD, registration  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

import main  # noqa: E402


@pytest.fixture
def client():
    with TestClient(main.app) as test_client:
        yield test_client


@pytest.fixture
def auth_headers(client):
    user = registration()
    assert client.post("/api/auth/register", json=user).status_code == 200
    response = client.post("/api/auth/login", json={"identifier": user["email"], "password": PASSWORD})
    assert response.status_code == 200
    return {"Authorization": f"Bearer {response.json()['access_token']}"}


@pytest.fixture
def fake_provider(monkeypatch):
    """Replace the network call to AI providers with a canned response text."""

    def install(response_text: str):
        async def fake_call(prompt, provider, json_output, max_tokens):
            return response_text

        monkeypatch.setattr("services.llm._call_provider", fake_call)

    return install
