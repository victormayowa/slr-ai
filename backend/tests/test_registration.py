"""Protocol export, PROSPERO field mapping, registration records and waivers, OSF deposit, and the search gate."""

from io import BytesIO

import pytest
from docx import Document
from workflow_helpers import (
    add_member,
    complete_stage,
    create_project,
    design_protocol,
    generate_protocol,
    import_records,
    lock_protocol,
    url,
    workflow,
)

import registration_routes
from services.osf import OSFDeposit, OSFError


@pytest.fixture
def project(client, auth_headers):
    return create_project(client, auth_headers), auth_headers


def lock_without_waiver(client, project_id, headers, fake_provider):
    generate_protocol(client, project_id, headers, fake_provider)
    for kind in ("inclusion", "exclusion"):
        client.post(url(project_id, "criteria/accept-all"), json={"kind": kind}, headers=headers)
    design_protocol(client, project_id, headers)
    complete_stage(client, project_id, headers, "protocol")


def unmet_search_requirements(client, project_id, headers):
    return [r["label"] for r in workflow(client, project_id, headers)["search"]["requirements"] if not r["met"]]


def test_a_draft_protocol_exports_with_gaps_marked(client, project, make_user):
    project_id, headers = project

    markdown = client.get(url(project_id, "protocol-export?format=markdown"), headers=headers)

    assert markdown.status_code == 200
    assert markdown.headers["content-disposition"] == 'attachment; filename="aspirin-review-protocol-draft.md"'
    assert "Draft: this protocol isn't locked yet" in markdown.text
    assert "## Rationale\n\n[TO COMPLETE] (required)" in markdown.text
    viewer = add_member(client, project_id, headers, make_user, "viewer")
    assert client.get(url(project_id, "protocol-export"), headers=viewer).status_code == 403
    auditor = add_member(client, project_id, headers, make_user, "auditor")
    assert client.get(url(project_id, "protocol-export"), headers=auditor).status_code == 200


def test_a_locked_protocol_exports_its_version_and_structured_content(client, project, fake_provider):
    project_id, headers = project
    lock_protocol(client, project_id, headers, fake_provider)

    markdown = client.get(url(project_id, "protocol-export?format=markdown"), headers=headers).text
    word = client.get(url(project_id, "protocol-export"), headers=headers)

    assert "Version 1, locked on" in markdown
    assert "## Review question (PICO)\n\nDoes aspirin prevent heart attacks in adults?" in markdown
    assert "- Include: Adults (Population)" in markdown
    assert "PubMed: aspirin[tiab]" in markdown
    assert "| Myocardial infarction | Primary |  |  |" in markdown
    assert word.headers["content-disposition"].endswith('-v1.docx"')
    headings = [p.text for p in Document(BytesIO(word.content)).paragraphs if p.style.name.startswith("Heading")]
    assert "Review question (PICO)" in headings
    event = client.get(url(project_id, "audit"), headers=headers).json()["events"][0]
    assert (event["action"], event["details"]) == ("protocol.exported", {"format": "docx", "protocol_version": 1})


def test_prospero_fields_are_mapped_and_flag_missing_input(client, project, fake_provider):
    project_id, headers = project
    lock_protocol(client, project_id, headers, fake_provider)

    fields = {f["field"]: f for f in client.get(url(project_id, "prospero-fields"), headers=headers).json()}

    assert fields["Review question"] == {
        "field": "Review question",
        "value": "Does aspirin prevent heart attacks in adults?",
        "source": "Review question",
        "ready": True,
    }
    assert "Myocardial infarction" in fields["Main outcome(s)"]["value"]
    assert "Inclusion: Adults" in fields["Participants or population"]["value"]
    assert fields["Anticipated start and completion dates"]["ready"] is False
    assert fields["Stage of review at time of submission"]["value"].endswith("has not started.")


def test_registrations_need_a_locked_protocol_and_are_audited(client, project, fake_provider):
    project_id, headers = project
    body = {"registry": "PROSPERO", "status": "submitted", "registration_id": "CRD420261234"}

    assert client.post(url(project_id, "registrations"), json=body, headers=headers).status_code == 409
    lock_without_waiver(client, project_id, headers, fake_provider)
    recorded = client.post(url(project_id, "registrations"), json=body, headers=headers).json()
    assert (recorded["status"], recorded["protocol_version"]) == ("submitted", 1)

    registered = client.patch(
        url(project_id, f"registrations/{recorded['id']}"), json={"status": "registered"}, headers=headers
    )
    assert registered.json()["status"] == "registered"
    other = {"registry": "other", "status": "registered", "registration_id": "X1"}
    assert client.post(url(project_id, "registrations"), json=other, headers=headers).status_code == 422
    actions = [e["action"] for e in client.get(url(project_id, "audit"), headers=headers).json()["events"]]
    assert actions[:2] == ["registration.updated", "registration.recorded"]


def test_search_needs_a_registration_or_waiver_and_an_up_to_date_registry_record(client, project, fake_provider):
    project_id, headers = project
    lock_without_waiver(client, project_id, headers, fake_provider)
    import_records(client, project_id, headers)
    requirement = "Protocol registration submitted, or registration waived with a reason"

    assert requirement in unmet_search_requirements(client, project_id, headers)
    assert (
        client.post(url(project_id, "registrations/waiver"), json={"reason": "too short"}, headers=headers).status_code
        == 422
    )
    body = {"registry": "PROSPERO", "status": "registered", "registration_id": "CRD420261234"}
    registration = client.post(url(project_id, "registrations"), json=body, headers=headers).json()
    assert unmet_search_requirements(client, project_id, headers) == []

    rationale = "Adding an exclusion criterion after peer review of the protocol."
    client.post(url(project_id, "workflow/protocol/reopen"), json={"rationale": rationale}, headers=headers)
    complete_stage(client, project_id, headers, "protocol", note="Amendment approved")
    assert unmet_search_requirements(client, project_id, headers) == ["Registry record updated with protocol version 2"]

    client.patch(
        url(project_id, f"registrations/{registration['id']}"), json={"covers_latest_version": True}, headers=headers
    )
    assert unmet_search_requirements(client, project_id, headers) == []


def test_osf_deposit_uploads_the_locked_protocol_without_keeping_the_token(client, project, fake_provider, monkeypatch):
    project_id, headers = project
    lock_protocol(client, project_id, headers, fake_provider)
    received = {}

    def fake_deposit(token, title, description, file_name, content, content_type):
        received.update(token=token, title=title, file_name=file_name, content=content)
        return OSFDeposit(node_id="abc12", url="https://osf.io/abc12/", file_name=file_name)

    monkeypatch.setattr(registration_routes, "deposit_protocol", fake_deposit)
    secret = "osf-personal-token-123456"

    response = client.post(url(project_id, "registrations/osf"), json={"token": secret}, headers=headers)

    assert response.status_code == 201
    assert (response.json()["status"], response.json()["url"]) == ("deposited", "https://osf.io/abc12/")
    assert received["token"] == secret
    assert received["content"][:2] == b"PK"
    assert received["file_name"] == "aspirin-review-protocol-v1.docx"
    audit = client.get(url(project_id, "audit"), headers=headers).text
    assert secret not in audit

    def rejected(*args):
        raise OSFError("OSF rejected the personal access token. It needs the osf.full_write scope.", 400)

    monkeypatch.setattr(registration_routes, "deposit_protocol", rejected)
    failed = client.post(url(project_id, "registrations/osf"), json={"token": secret}, headers=headers)
    assert (failed.status_code, "rejected" in failed.json()["detail"]) == (400, True)
