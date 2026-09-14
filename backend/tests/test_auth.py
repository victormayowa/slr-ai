import uuid

from factories import PASSWORD, registration


def login(client, identifier, password=PASSWORD):
    return client.post("/api/auth/login", json={"identifier": identifier, "password": password})


def test_register_then_login_with_email(client):
    user = registration()
    assert client.post("/api/auth/register", json=user).status_code == 200

    response = login(client, user["email"])

    assert response.status_code == 200
    assert response.json()["access_token"]
    assert response.json()["user"]["email"] == user["email"]


def test_login_with_orcid(client):
    orcid = f"orcid-{uuid.uuid4().hex[:12]}"
    client.post("/api/auth/register", json=registration(orcid_id=orcid))

    assert login(client, orcid).status_code == 200


def test_wrong_password_rejected(client):
    user = registration()
    client.post("/api/auth/register", json=user)

    assert login(client, user["email"], "not the password").status_code == 401


def test_password_over_bcrypt_limit_rejected_at_registration(client):
    response = client.post("/api/auth/register", json=registration(password="a" * 80))

    assert response.status_code == 422


def test_overlong_password_at_login_is_rejected_not_a_server_error(client):
    user = registration()
    client.post("/api/auth/register", json=user)

    assert login(client, user["email"], "a" * 80).status_code == 401


def test_invalid_email_rejected(client):
    assert client.post("/api/auth/register", json=registration(email="not-an-email")).status_code == 422


def test_duplicate_email_rejected(client):
    user = registration()
    client.post("/api/auth/register", json=user)

    assert client.post("/api/auth/register", json=registration(email=user["email"])).status_code == 400


def test_duplicate_orcid_rejected(client):
    orcid = f"orcid-{uuid.uuid4().hex[:12]}"
    client.post("/api/auth/register", json=registration(orcid_id=orcid))

    assert client.post("/api/auth/register", json=registration(orcid_id=orcid)).status_code == 400
