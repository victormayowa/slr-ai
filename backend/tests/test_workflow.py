import pytest
from sqlalchemy import select
from workflow_helpers import (
    GENERATED_PROTOCOL,
    PROTOCOL,
    RECORDS,
    add_member,
    complete_stage,
    create_project,
    decide,
    generate_protocol,
    import_records,
    lock_protocol,
    open_extraction,
    open_screening,
    open_synthesis,
    url,
)

import models
import records_routes
from database import SessionLocal
from project_defaults import DEFAULT_EXTRACTION_FIELDS
from services.errors import SearchError


@pytest.fixture
def project(client, auth_headers):
    return create_project(client, auth_headers), auth_headers


def audit_actions(client, project_id, headers):
    return [event["action"] for event in client.get(url(project_id, "audit"), headers=headers).json()["events"]]


# Protocol


def test_new_project_has_a_protocol_and_default_extraction_fields(client, project):
    project_id, headers = project

    assert client.get(url(project_id, "protocol"), headers=headers).json()["framework"] == "PICO"
    assert client.get(url(project_id, "extraction-fields"), headers=headers).json() == DEFAULT_EXTRACTION_FIELDS


def test_protocol_changes_persist_and_are_audited(client, project):
    project_id, headers = project

    assert client.put(url(project_id, "protocol"), json=PROTOCOL, headers=headers).status_code == 200

    saved = client.get(url(project_id, "protocol"), headers=headers).json()
    assert {key: saved[key] for key in PROTOCOL} == PROTOCOL
    assert "protocol.updated" in audit_actions(client, project_id, headers)


def test_search_strategy_checklists_are_not_risk_of_bias_tools(client, project):
    project_id, headers = project

    response = client.put(url(project_id, "protocol"), json={**PROTOCOL, "rob_tool": "PRESS"}, headers=headers)

    assert response.status_code == 422


def test_viewers_cannot_edit_the_protocol(client, project, make_user):
    project_id, owner = project
    viewer = add_member(client, project_id, owner, make_user, "viewer")

    assert client.put(url(project_id, "protocol"), json=PROTOCOL, headers=viewer).status_code == 403


def test_generating_a_protocol_needs_a_study_description(client, project, fake_provider):
    project_id, headers = project
    fake_provider(GENERATED_PROTOCOL)

    assert client.post(url(project_id, "protocol/generate"), json={}, headers=headers).status_code == 400


def test_generated_protocol_is_stored_with_provenance(client, project, fake_provider):
    project_id, headers = project

    generated = generate_protocol(client, project_id, headers, fake_provider)

    assert [(c["kind"], c["text"], c["status"]) for c in generated["criteria"]] == [
        ("inclusion", "Adults", "pending"),
        ("inclusion", "Randomized trials", "pending"),
        ("exclusion", "Children", "pending"),
    ]
    assert [s["database"] for s in generated["search_strategies"]] == ["PubMed", "Embase"]
    assert generated["extraction_fields"][-2:] == ["Country", "Follow-up"]
    with SessionLocal() as db:
        run = db.scalar(select(models.AIRun).where(models.AIRun.project_id == project_id))
        assert (run.task, run.status, run.provider, run.prompt_version) == (
            "protocol",
            "succeeded",
            "gemini",
            "protocol-v1",
        )


def test_regenerating_keeps_criteria_a_reviewer_has_accepted(client, project, fake_provider):
    project_id, headers = project
    first = generate_protocol(client, project_id, headers, fake_provider)
    kept_id = first["criteria"][0]["id"]
    client.patch(url(project_id, f"criteria/{kept_id}"), json={"status": "accepted"}, headers=headers)

    second = generate_protocol(client, project_id, headers, fake_provider)

    statuses = [(c["id"] == kept_id, c["status"]) for c in second["criteria"]]
    assert statuses.count((True, "accepted")) == 1
    assert statuses.count((False, "pending")) == 3


def test_failed_generation_is_recorded_and_changes_nothing(client, project):
    project_id, headers = project
    client.put(url(project_id, "protocol"), json=PROTOCOL, headers=headers)

    response = client.post(url(project_id, "protocol/generate"), json={"provider": "openai"}, headers=headers)

    assert response.status_code == 502
    assert client.get(url(project_id, "criteria"), headers=headers).json() == []
    with SessionLocal() as db:
        run = db.scalar(select(models.AIRun).where(models.AIRun.project_id == project_id))
        assert run.status == "failed"
        assert "OPENAI_API_KEY" in run.error


def test_accept_all_only_accepts_that_kind(client, project, fake_provider):
    project_id, headers = project
    generate_protocol(client, project_id, headers, fake_provider)

    criteria = client.post(url(project_id, "criteria/accept-all"), json={"kind": "inclusion"}, headers=headers).json()

    assert {(c["kind"], c["status"]) for c in criteria} == {("inclusion", "accepted"), ("exclusion", "pending")}


def test_edited_search_strategy_is_saved(client, project, fake_provider):
    project_id, headers = project
    strategy_id = generate_protocol(client, project_id, headers, fake_provider)["search_strategies"][0]["id"]

    client.patch(url(project_id, f"search-strategies/{strategy_id}"), json={"query": "aspirin[mh]"}, headers=headers)

    assert client.get(url(project_id, "search-strategies"), headers=headers).json()[0]["query"] == "aspirin[mh]"


def test_extraction_fields_can_be_replaced(client, project):
    project_id, headers = project

    response = client.put(
        url(project_id, "extraction-fields"),
        json={"names": ["Country", " Sample Size ", "Country", ""]},
        headers=headers,
    )

    assert response.json() == ["Country", "Sample Size"]
    assert client.get(url(project_id, "extraction-fields"), headers=headers).json() == ["Country", "Sample Size"]


# Search, import, deduplication


def test_search_run_stores_records_with_an_honest_source_label(client, project, fake_provider, monkeypatch):
    project_id, headers = project
    embase = lock_protocol(client, project_id, headers, fake_provider)["search_strategies"][1]
    result = {
        "id": "W1",
        "title": "Aspirin",
        "authors": "A",
        "year": 2020,
        "venue": "",
        "doi": "10.1/x",
        "abstract": "a",
    }
    monkeypatch.setattr(records_routes, "search_openalex", lambda query, limit: [result])

    run = client.post(url(project_id, "searches"), json={"strategy_id": embase["id"]}, headers=headers)

    assert run.status_code == 201
    assert run.json()["source"] == "OpenAlex (no native Embase connector yet)"
    records = client.get(url(project_id, "records"), headers=headers).json()
    assert [(r["title"], r["year"], r["source"]) for r in records] == [
        ("Aspirin", "2020", "OpenAlex (no native Embase connector yet)")
    ]


def test_failed_search_stores_nothing(client, project, fake_provider, monkeypatch):
    project_id, headers = project
    pubmed = lock_protocol(client, project_id, headers, fake_provider)["search_strategies"][0]

    def unavailable(query, limit):
        raise SearchError("PubMed search failed. Please try again shortly.")

    monkeypatch.setattr(records_routes, "search_pubmed", unavailable)

    response = client.post(url(project_id, "searches"), json={"strategy_id": pubmed["id"]}, headers=headers)
    assert response.status_code == 502
    assert client.get(url(project_id, "records"), headers=headers).json() == []


def test_deduplication_and_prisma_counts_come_from_stored_data(client, project, fake_provider):
    project_id, headers = project
    lock_protocol(client, project_id, headers, fake_provider)
    import_records(client, project_id, headers)
    duplicates = [{"title": "Different title", "doi": "10.1/A"}, {"title": "Statin trial!"}]
    import_records(client, project_id, headers, duplicates, file_name="second.csv")

    assert client.post(url(project_id, "deduplicate"), headers=headers).json() == {"duplicates_marked": 2}
    assert client.post(url(project_id, "deduplicate"), headers=headers).json() == {"duplicates_marked": 0}
    assert len(client.get(url(project_id, "records"), headers=headers).json()) == 2
    assert len(client.get(url(project_id, "records?include_duplicates=true"), headers=headers).json()) == 4
    assert client.get(url(project_id, "prisma"), headers=headers).json() == {
        "identified_from_databases": 0,
        "identified_from_uploads": 4,
        "by_source": {"Manual upload: export.csv": 2, "Manual upload: second.csv": 2},
        "duplicates_removed": 2,
        "screened": 2,
        "excluded": 0,
        "included": 0,
        "awaiting_decision": 2,
    }


def test_clearing_records_needs_edit_permission(client, project, make_user, fake_provider):
    project_id, owner = project
    lock_protocol(client, project_id, owner, fake_provider)
    import_records(client, project_id, owner)
    screener = add_member(client, project_id, owner, make_user, "screener")

    assert client.delete(url(project_id, "records"), headers=screener).status_code == 403
    assert client.delete(url(project_id, "records"), headers=owner).status_code == 204
    assert client.get(url(project_id, "records"), headers=owner).json() == []
    assert client.get(url(project_id, "prisma"), headers=owner).json()["screened"] == 0


# Screening


def test_ai_screening_waits_for_search_to_be_signed_off(client, project, fake_provider):
    project_id, headers = project
    lock_protocol(client, project_id, headers, fake_provider)
    records = import_records(client, project_id, headers)

    response = client.post(url(project_id, "screening/ai"), json={"record_ids": [records[0]["id"]]}, headers=headers)

    assert response.status_code == 409
    assert "Search and deduplication" in response.json()["detail"]


def test_ai_screening_suggests_but_never_decides(client, project, fake_provider):
    project_id, headers = project
    records = open_screening(client, project_id, headers, fake_provider)
    fake_provider('{"decision": "Include", "reasoning": "Adults in a trial", "supporting_quote": "randomized"}')

    body = {"record_ids": [r["id"] for r in records], "provider": "gemini"}
    screened = client.post(url(project_id, "screening/ai"), json=body, headers=headers).json()

    assert [(r["ai_screening"]["decision"], r["final_decision"]) for r in screened] == [("Include", None)] * 2
    assert client.get(url(project_id, "prisma"), headers=headers).json()["included"] == 0


def test_unusable_ai_screening_output_is_stored_as_an_error(client, project, fake_provider):
    project_id, headers = project
    record_id = open_screening(client, project_id, headers, fake_provider)[0]["id"]
    fake_provider('{"decision": "Probably", "reasoning": "unsure"}')

    body = {"record_ids": [record_id], "provider": "gemini"}
    screened = client.post(url(project_id, "screening/ai"), json=body, headers=headers).json()[0]

    assert screened["ai_screening"]["decision"] is None
    assert "valid decision" in screened["ai_screening"]["error"]
    stored = client.get(url(project_id, "records"), headers=headers).json()[0]
    assert "valid decision" in stored["ai_screening"]["error"]


def test_reviewer_decisions_drive_prisma_and_are_audited(client, project, fake_provider):
    project_id, headers = project
    first, second = open_screening(client, project_id, headers, fake_provider)

    decide(client, project_id, headers, first["id"], "include")
    decide(client, project_id, headers, second["id"], "exclude")
    changed = decide(client, project_id, headers, second["id"], "include")

    assert (changed["my_decision"], changed["final_decision"]) == ("include", "include")
    prisma = client.get(url(project_id, "prisma"), headers=headers).json()
    assert (prisma["included"], prisma["excluded"], prisma["awaiting_decision"]) == (2, 0, 0)
    events = client.get(url(project_id, "audit"), headers=headers).json()["events"]
    assert events[0]["action"] == "screening.decided"
    assert events[0]["details"] == {"stage": "title_abstract", "decision": "include", "previous": "exclude"}


def test_records_from_other_projects_cannot_be_decided(client, make_user, fake_provider):
    _, alice = make_user()
    _, bob = make_user()
    alice_project = create_project(client, alice, "A")
    bob_project = create_project(client, bob, "B")
    alice_record = open_screening(client, alice_project, alice, fake_provider)[0]["id"]
    open_screening(client, bob_project, bob, fake_provider)

    response = client.put(
        url(bob_project, f"records/{alice_record}/decision"), json={"decision": "exclude"}, headers=bob
    )

    assert response.status_code == 404


def test_duplicate_records_cannot_be_decided(client, project, fake_provider):
    project_id, headers = project
    lock_protocol(client, project_id, headers, fake_provider)
    import_records(client, project_id, headers, [RECORDS[0], RECORDS[0]])
    client.post(url(project_id, "deduplicate"), headers=headers)
    complete_stage(client, project_id, headers, "search")
    duplicate = client.get(url(project_id, "records?include_duplicates=true"), headers=headers).json()[1]

    response = client.put(
        url(project_id, f"records/{duplicate['id']}/decision"), json={"decision": "include"}, headers=headers
    )

    assert response.status_code == 409


# Extraction, appraisal, synthesis


def test_extraction_and_appraisal_run_only_on_included_records(client, project, fake_provider):
    project_id, headers = project
    included, excluded = open_extraction(client, project_id, headers, fake_provider)
    fake_provider('{"Sample Size": 120}')

    excluded_body = {"record_ids": [excluded["id"]], "provider": "gemini"}
    assert client.post(url(project_id, "extraction/ai"), json=excluded_body, headers=headers).status_code == 400

    body = {"record_ids": [included["id"]], "provider": "gemini"}
    extracted = client.post(url(project_id, "extraction/ai"), json=body, headers=headers).json()[0]
    assert extracted["extraction"]["values"] == {
        "Sample Size": "120",
        "Mean Age": "Missing from AI response",
        "Primary Outcome Result": "Missing from AI response",
        "Adverse Events": "Missing from AI response",
        "Country": "Missing from AI response",
        "Follow-up": "Missing from AI response",
    }
    complete_stage(client, project_id, headers, "extraction")

    fake_provider('{"D1: Randomization": "Low"}')
    appraised = client.post(url(project_id, "appraisal/ai"), json=body, headers=headers).json()[0]
    assert appraised["appraisal"]["tool"] == "ROB-2"
    assert appraised["appraisal"]["judgments"]["D1: Randomization"] == "Low"
    assert appraised["appraisal"]["judgments"]["Overall"] == "Missing from AI response"


def test_screeners_cannot_run_extraction(client, project, make_user):
    project_id, owner = project
    screener = add_member(client, project_id, owner, make_user, "screener")

    response = client.post(url(project_id, "extraction/ai"), json={"record_ids": [1]}, headers=screener)

    assert response.status_code == 403


def test_synthesis_waits_for_risk_of_bias_sign_off(client, project, fake_provider):
    project_id, headers = project
    open_extraction(client, project_id, headers, fake_provider)
    fake_provider("A narrative.")

    response = client.post(url(project_id, "synthesis"), json={}, headers=headers)

    assert response.status_code == 409


def test_synthesis_is_saved_and_retrievable(client, project, fake_provider):
    project_id, headers = project
    open_synthesis(client, project_id, headers, fake_provider)
    fake_provider("## Narrative synthesis\nOne included trial.")

    created = client.post(url(project_id, "synthesis"), json={"provider": "gemini"}, headers=headers)

    assert created.status_code == 201
    assert created.json()["record_count"] == 1
    assert client.get(url(project_id, "synthesis"), headers=headers).json()["content"].startswith("## Narrative")


# Audit trail


def test_audit_chain_is_valid_and_detects_tampering(client, project):
    project_id, headers = project
    client.put(url(project_id, "protocol"), json=PROTOCOL, headers=headers)
    client.put(url(project_id, "extraction-fields"), json={"names": ["Country"]}, headers=headers)
    assert client.get(url(project_id, "audit"), headers=headers).json()["chain_valid"] is True

    with SessionLocal() as db:
        event = db.scalar(
            select(models.AuditEvent).where(
                models.AuditEvent.project_id == project_id, models.AuditEvent.action == "protocol.updated"
            )
        )
        event.details = {"changes": {"description": "rewritten history"}}
        db.commit()

    assert client.get(url(project_id, "audit"), headers=headers).json()["chain_valid"] is False


def test_audit_trail_is_visible_to_auditors_but_not_screeners(client, project, make_user):
    project_id, owner = project
    auditor = add_member(client, project_id, owner, make_user, "auditor")
    screener = add_member(client, project_id, owner, make_user, "screener")

    assert client.get(url(project_id, "audit"), headers=auditor).status_code == 200
    assert client.get(url(project_id, "audit"), headers=screener).status_code == 403
