import auth_routes
import security


def test_login_attempts_are_rate_limited(client):
    body = {"identifier": "nobody@example.org", "password": "wrong password"}
    limit = auth_routes.LOGIN_RATE_LIMIT_PER_MINUTE

    statuses = [client.post("/api/auth/login", json=body).status_code for _ in range(limit + 1)]

    assert statuses[:limit] == [401] * limit
    assert statuses[limit] == 429


def test_rate_limited_response_says_when_to_retry(client):
    body = {"identifier": "nobody@example.org", "password": "wrong password"}
    for _ in range(auth_routes.LOGIN_RATE_LIMIT_PER_MINUTE):
        client.post("/api/auth/login", json=body)

    response = client.post("/api/auth/login", json=body)

    assert response.headers["retry-after"] == "60"


def test_api_responses_carry_security_headers(client, auth_headers):
    response = client.get("/api/projects", headers=auth_headers)

    assert response.headers["x-content-type-options"] == "nosniff"
    assert response.headers["x-frame-options"] == "DENY"
    assert "default-src 'none'" in response.headers["content-security-policy"]
    assert response.headers["cache-control"] == "no-store"


def test_oversized_request_body_rejected(client):
    oversized = b"x" * (security.MAX_BODY_BYTES + 1)

    response = client.post("/api/auth/login", content=oversized, headers={"Content-Type": "application/json"})

    assert response.status_code == 413
