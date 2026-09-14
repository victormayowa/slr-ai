def test_healthz(client):
    assert client.get("/healthz").json() == {"status": "ok"}


def test_readyz_checks_database(client):
    response = client.get("/readyz")

    assert response.status_code == 200
    assert response.json() == {"status": "ready"}


def test_request_id_generated_when_absent(client):
    assert len(client.get("/healthz").headers["x-request-id"]) == 32


def test_well_formed_request_id_is_reused(client):
    response = client.get("/healthz", headers={"X-Request-ID": "trace-123"})

    assert response.headers["x-request-id"] == "trace-123"


def test_malformed_request_id_is_replaced(client):
    response = client.get("/healthz", headers={"X-Request-ID": "bad id\nwith newline"})

    assert response.headers["x-request-id"] != "bad id\nwith newline"
    assert len(response.headers["x-request-id"]) == 32
