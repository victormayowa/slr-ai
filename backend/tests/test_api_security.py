import os
from datetime import UTC, datetime, timedelta

import jwt
import pytest

PROTECTED_ROUTES = [
    ("GET", "/api/projects"),
    ("GET", "/api/projects/1/records"),
    ("PUT", "/api/projects/1/protocol"),
    ("POST", "/api/projects/1/screening/ai"),
    ("GET", "/api/projects/1/audit"),
    ("POST", "/api/chat"),
]


def bearer(secret, expires_in=timedelta(hours=1)):
    token = jwt.encode({"sub": "someone@example.org", "exp": datetime.now(UTC) + expires_in}, secret, algorithm="HS256")
    return {"Authorization": f"Bearer {token}"}


@pytest.mark.parametrize("method,path", PROTECTED_ROUTES)
def test_routes_require_login(client, method, path):
    assert client.request(method, path, json={}).status_code == 401


def test_token_signed_with_old_default_secret_rejected(client):
    headers = bearer("super_secret_omnireview_key")

    assert client.post("/api/chat", json={"query": "hi"}, headers=headers).status_code == 401


def test_expired_token_rejected(client):
    headers = bearer(os.environ["JWT_SECRET_KEY"], expires_in=timedelta(minutes=-1))

    assert client.post("/api/chat", json={"query": "hi"}, headers=headers).status_code == 401


def test_unconfigured_provider_returns_502_with_reason(client, auth_headers):
    response = client.post("/api/chat", json={"query": "hi", "provider": "openai"}, headers=auth_headers)

    assert response.status_code == 502
    assert "OPENAI_API_KEY" in response.json()["detail"]


def test_unknown_provider_rejected(client, auth_headers):
    assert client.post("/api/chat", json={"query": "hi", "provider": "bogus"}, headers=auth_headers).status_code == 422


def test_cors_does_not_allow_unknown_origin(client):
    response = client.options(
        "/api/chat", headers={"Origin": "https://evil.example", "Access-Control-Request-Method": "POST"}
    )

    assert "access-control-allow-origin" not in response.headers


def test_cors_allows_configured_origin(client):
    response = client.options(
        "/api/chat", headers={"Origin": "http://localhost:5173", "Access-Control-Request-Method": "PUT"}
    )

    assert response.headers["access-control-allow-origin"] == "http://localhost:5173"
