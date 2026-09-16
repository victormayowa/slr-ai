"""Interoperability: record exports and imports, decisions exchanged with Rayyan and Covidence, the FHIR bundle,
scoped API tokens, and webhook subscriptions and deliveries."""

import csv
import io
import json

import pytest
from workflow_helpers import create_project, decide, open_screening, url

import webhooks


def token_headers(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def create_token(client, headers, **overrides):
    body = {"name": "Reporting", "scopes": ["read"], **overrides}
    response = client.post("/api/me/tokens", json=body, headers=headers)
    assert response.status_code == 201, response.text
    return response.json()


@pytest.fixture
def screened(client, auth_headers, fake_provider):
    project_id = create_project(client, auth_headers)
    included, excluded = open_screening(client, project_id, auth_headers, fake_provider)
    decide(client, project_id, auth_headers, included["id"], "include")
    decide(client, project_id, auth_headers, excluded["id"], "exclude")
    return project_id, included, excluded


@pytest.mark.parametrize(
    "fmt,marker",
    [("ris", b"TY  - "), ("bibtex", b"@"), ("endnote_xml", b"<records>"), ("csv", b"title"), ("json", b'"title"')],
)
def test_records_export_in_every_reference_format(client, auth_headers, screened, fmt, marker):
    project_id, _, _ = screened

    response = client.get(url(project_id, "records/export"), params={"format": fmt}, headers=auth_headers)

    assert response.status_code == 200, response.text
    assert marker in response.content
    assert "attachment" in response.headers["content-disposition"]


def test_json_export_carries_the_final_decisions(client, auth_headers, screened):
    project_id, included, excluded = screened

    response = client.get(url(project_id, "records/export"), params={"format": "json"}, headers=auth_headers)

    by_id = {record["id"]: record for record in json.loads(response.content)}
    assert by_id[included["id"]]["decision"] == "include"
    assert by_id[excluded["id"]]["decision"] == "exclude"


def test_export_scope_limits_records_to_those_included(client, auth_headers, screened):
    project_id, included, _ = screened

    response = client.get(
        url(project_id, "records/export"), params={"format": "json", "scope": "included"}, headers=auth_headers
    )

    # Only records included at both stages count as included, and full-text screening hasn't happened here.
    assert json.loads(response.content) == []


def test_rayyan_and_covidence_exports_carry_the_decisions(client, auth_headers, screened):
    project_id, _, _ = screened

    rayyan = client.get(url(project_id, "records/export/rayyan"), params={"reviewer": "Ada"}, headers=auth_headers)
    assert rayyan.status_code == 200, rayyan.text
    rows = list(csv.reader(io.StringIO(rayyan.content.decode("utf-8-sig"))))
    header, body = rows[0], rows[1:]
    inclusion = header.index("RAYYAN-INCLUSION")
    decisions = {row[inclusion] for row in body}
    assert decisions == {'{"Ada"=>"Included"}', '{"Ada"=>"Excluded"}'}

    covidence = client.get(url(project_id, "records/export/covidence"), headers=auth_headers)
    assert covidence.status_code == 200
    assert b"Title and abstract screening" in covidence.content and b"Excluded" in covidence.content


def test_decisions_come_back_from_a_rayyan_export(client, auth_headers, fake_provider, make_user):
    project_id = create_project(client, auth_headers)
    included, excluded = open_screening(client, project_id, auth_headers, fake_provider)
    csv = (
        "key,title,doi,RAYYAN-INCLUSION\n"
        f'{included["id"]},Aspirin trial,10.1/a,"{{""Ada""=>""Included""}}"\n'
        f'{excluded["id"]},Statin trial,,"{{""Ada""=>""Excluded""}}"\n'
    ).encode()

    preview = client.post(
        url(project_id, "screening/import-decisions"),
        files={"file": ("rayyan.csv", csv, "text/csv")},
        params={"dry_run": True},
        headers=auth_headers,
    )
    assert preview.status_code == 200, preview.text
    assert preview.json()["applied"] == 2 and preview.json()["dry_run"] is True
    # A dry run changes nothing.
    assert client.get(url(project_id, "records"), headers=auth_headers).json()[0]["my_decision"] is None

    applied = client.post(
        url(project_id, "screening/import-decisions"),
        files={"file": ("rayyan.csv", csv, "text/csv")},
        headers=auth_headers,
    )
    assert applied.json()["applied"] == 2
    records = {record["id"]: record for record in client.get(url(project_id, "records"), headers=auth_headers).json()}
    assert records[included["id"]]["my_decision"] == "include"
    assert records[excluded["id"]]["my_decision"] == "exclude"


def test_a_file_without_a_decision_column_is_refused(client, auth_headers, fake_provider):
    project_id = create_project(client, auth_headers)
    open_screening(client, project_id, auth_headers, fake_provider)

    response = client.post(
        url(project_id, "screening/import-decisions"),
        files={"file": ("plain.csv", b"title,doi\nAspirin trial,10.1/a\n", "text/csv")},
        headers=auth_headers,
    )

    assert response.status_code == 400
    assert "decision column" in response.json()["detail"]


def test_records_import_from_json_and_jats(client, auth_headers, fake_provider):
    project_id = create_project(client, auth_headers)
    from workflow_helpers import lock_protocol

    lock_protocol(client, project_id, auth_headers, fake_provider)
    payload = json.dumps(
        [{"title": "Imported trial", "authors": "Smith J; Jones A", "year": "2024", "doi": "10.5/x"}]
    ).encode()

    imported = client.post(
        url(project_id, "records/import/structured"),
        files={"file": ("records.json", payload, "application/json")},
        params={"format": "json"},
        headers=auth_headers,
    )
    assert imported.status_code == 201, imported.text
    assert imported.json()["records"] == 1

    jats = (
        b"<article><front><article-meta><title-group><article-title>JATS trial</article-title></title-group>"
        b"<abstract><p>Adults randomized.</p></abstract></article-meta></front></article>"
    )
    from_jats = client.post(
        url(project_id, "records/import/structured"),
        files={"file": ("article.xml", jats, "application/xml")},
        params={"format": "jats"},
        headers=auth_headers,
    )
    assert from_jats.status_code == 201, from_jats.text
    titles = {record["title"] for record in client.get(url(project_id, "records"), headers=auth_headers).json()}
    assert {"Imported trial", "JATS trial"} <= titles


def test_synthesis_exports_explain_when_there_is_nothing_to_export(client, auth_headers, screened):
    project_id, _, _ = screened

    assert client.get(url(project_id, "exports/revman.csv"), headers=auth_headers).status_code == 409
    assert client.get(url(project_id, "exports/gradepro.csv"), headers=auth_headers).status_code == 409
    assert client.get(url(project_id, "manuscript/export/jats"), headers=auth_headers).status_code == 404


def test_fhir_bundle_describes_the_review(client, auth_headers, screened):
    project_id, _, _ = screened

    response = client.get(url(project_id, "fhir/bundle"), headers=auth_headers)

    assert response.status_code == 200, response.text
    bundle = response.json()
    assert bundle["resourceType"] == "Bundle" and bundle["type"] == "collection"
    citations = [e["resource"] for e in bundle["entry"] if e["resource"]["resourceType"] == "Citation"]
    assert citations and citations[0]["title"]


# --- API tokens ---


def test_a_read_token_reads_but_cannot_write(client, auth_headers, screened):
    project_id, _, _ = screened
    created = create_token(client, auth_headers, scopes=["read"])
    assert created["token"].startswith("omr_")
    headers = token_headers(created["token"])

    assert client.get("/api/projects", headers=headers).status_code == 200
    assert client.get(url(project_id, "records"), headers=headers).status_code == 200

    refused = client.post(url(project_id, "tasks"), json={"title": "No"}, headers=headers)
    assert refused.status_code == 403
    assert "write scope" in refused.json()["detail"]


def test_a_token_cannot_manage_tokens(client, auth_headers):
    created = create_token(client, auth_headers, scopes=["read", "write"])

    response = client.get("/api/me/tokens", headers=token_headers(created["token"]))

    assert response.status_code == 403
    assert "sign in" in response.json()["detail"].lower()


def test_a_token_limited_to_one_project_cannot_reach_another(client, auth_headers, screened):
    project_id, _, _ = screened
    other_id = create_project(client, auth_headers, title="Another review")
    created = create_token(client, auth_headers, scopes=["read"], project_ids=[project_id])
    headers = token_headers(created["token"])

    assert client.get(url(project_id, "records"), headers=headers).status_code == 200
    assert client.get(url(other_id, "records"), headers=headers).status_code == 403


def test_a_token_can_only_be_limited_to_your_own_projects(client, auth_headers, make_user):
    _, other_headers = make_user()
    theirs = create_project(client, other_headers)

    response = client.post(
        "/api/me/tokens", json={"name": "Nope", "scopes": ["read"], "project_ids": [theirs]}, headers=auth_headers
    )

    assert response.status_code == 422


def test_a_revoked_token_stops_working(client, auth_headers):
    created = create_token(client, auth_headers)
    headers = token_headers(created["token"])
    assert client.get("/api/projects", headers=headers).status_code == 200

    assert client.delete(f"/api/me/tokens/{created['id']}", headers=auth_headers).status_code == 204

    assert client.get("/api/projects", headers=headers).status_code == 401


# --- Webhooks ---


@pytest.fixture
def allow_any_url(monkeypatch):
    """The safety check resolves DNS, which tests must not depend on."""
    import webhook_routes

    monkeypatch.setattr(webhook_routes, "ensure_public_url", lambda url: None)
    monkeypatch.setattr(webhooks, "ensure_public_url", lambda url: None)


def test_a_webhook_is_created_with_a_secret_shown_once(client, auth_headers, allow_any_url):
    project_id = create_project(client, auth_headers)

    created = client.post(
        url(project_id, "webhooks"),
        json={"url": "https://example.org/hook", "events": ["stage.*", "project.created"]},
        headers=auth_headers,
    )

    assert created.status_code == 201, created.text
    assert created.json()["secret"]
    listed = client.get(url(project_id, "webhooks"), headers=auth_headers).json()
    assert listed[0]["events"] == ["project.created", "stage.*"]
    assert "secret" not in listed[0]


def test_an_audit_event_queues_a_delivery_for_a_matching_webhook(client, auth_headers, allow_any_url):
    project_id = create_project(client, auth_headers)
    created = client.post(
        url(project_id, "webhooks"),
        json={"url": "https://example.org/hook", "events": ["task.*"]},
        headers=auth_headers,
    ).json()

    client.post(url(project_id, "tasks"), json={"title": "Draft the protocol"}, headers=auth_headers)

    deliveries = client.get(url(project_id, f"webhooks/{created['id']}/deliveries"), headers=auth_headers).json()
    assert [delivery["action"] for delivery in deliveries] == ["task.created"]
    assert deliveries[0]["status"] == "pending"


def test_events_that_do_not_match_are_not_queued(client, auth_headers, allow_any_url):
    project_id = create_project(client, auth_headers)
    created = client.post(
        url(project_id, "webhooks"),
        json={"url": "https://example.org/hook", "events": ["stage.completed"]},
        headers=auth_headers,
    ).json()

    client.post(url(project_id, "tasks"), json={"title": "Draft the protocol"}, headers=auth_headers)

    assert client.get(url(project_id, f"webhooks/{created['id']}/deliveries"), headers=auth_headers).json() == []


def test_a_delivery_is_signed_and_retried_until_it_succeeds(client, auth_headers, allow_any_url, monkeypatch):
    import hashlib
    import hmac

    from database import SessionLocal

    project_id = create_project(client, auth_headers)
    created = client.post(
        url(project_id, "webhooks"),
        json={"url": "https://example.org/hook", "events": ["task.*"]},
        headers=auth_headers,
    ).json()
    client.post(url(project_id, "tasks"), json={"title": "Draft the protocol"}, headers=auth_headers)

    sent: list[dict] = []

    class Reply:
        status_code = 500

    def failing_post(url, data, timeout, allow_redirects, headers):
        sent.append({"url": url, "data": data, "headers": headers})
        return Reply()

    queued = client.get(url(project_id, f"webhooks/{created['id']}/deliveries"), headers=auth_headers).json()[0]

    monkeypatch.setattr(webhooks.requests, "post", failing_post)
    with SessionLocal() as db:
        assert webhooks.deliver_due(db) == 0

    # Other projects' deliveries are due at the same time, so find the one for this subscription.
    mine = next(item for item in sent if item["headers"]["X-OmniReview-Delivery"] == str(queued["id"]))
    expected = hmac.new(created["secret"].encode(), mine["data"], hashlib.sha256).hexdigest()
    assert mine["headers"]["X-OmniReview-Signature"] == f"sha256={expected}"
    assert mine["headers"]["X-OmniReview-Event"] == "task.created"

    # A failure is kept for another attempt rather than dropped.
    delivery = client.get(url(project_id, f"webhooks/{created['id']}/deliveries"), headers=auth_headers).json()[0]
    assert delivery["status"] == "pending" and delivery["attempts"] == 1 and delivery["response_status"] == 500
