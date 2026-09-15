import json
from dataclasses import replace

import pytest
from sqlalchemy import select
from workflow_helpers import (
    APPRAISAL_REPLY,
    GENERATED_PROTOCOL,
    PROTOCOL,
    RECORDS,
    ProviderHTTPError,
    add_member,
    complete_stage,
    create_project,
    decide,
    extract,
    form_fields,
    generate_protocol,
    import_records,
    lock_protocol,
    open_extraction,
    open_screening,
    open_synthesis,
    run_ai,
    url,
)

import models
import search_sources
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
        assert (run.task, run.status, run.provider, run.model, run.prompt_version, run.key_source) == (
            "protocol",
            "succeeded",
            "gemini",
            "gemini-3.8-flash",
            "protocol-v3",
            "platform",
        )
        assert (run.input_tokens, run.output_tokens, run.attempts) == (100, 20, 1)


def test_regenerating_keeps_criteria_a_reviewer_has_accepted(client, project, fake_provider):
    project_id, headers = project
    first = generate_protocol(client, project_id, headers, fake_provider)
    kept_id = first["criteria"][0]["id"]
    client.patch(url(project_id, f"criteria/{kept_id}"), json={"status": "accepted"}, headers=headers)

    second = generate_protocol(client, project_id, headers, fake_provider)

    statuses = [(c["id"] == kept_id, c["status"]) for c in second["criteria"]]
    assert statuses.count((True, "accepted")) == 1
    assert statuses.count((False, "pending")) == 3


def test_failed_generation_is_recorded_and_changes_nothing(client, project, fake_provider):
    project_id, headers = project
    client.put(url(project_id, "protocol"), json=PROTOCOL, headers=headers)
    fake_provider(ProviderHTTPError(401))

    response = client.post(url(project_id, "protocol/generate"), headers=headers)

    assert response.status_code == 502
    assert "rejected the API key" in response.json()["detail"]
    assert "sk-secret-123" not in response.text
    assert client.get(url(project_id, "criteria"), headers=headers).json() == []
    with SessionLocal() as db:
        run = db.scalar(select(models.AIRun).where(models.AIRun.project_id == project_id))
        assert (run.status, run.attempts) == ("failed", 1)
        assert "rejected the API key" in run.error


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


def test_search_runs_record_prisma_s_details_and_never_substitute_a_database(
    client, project, fake_provider, monkeypatch
):
    project_id, headers = project
    pubmed_strategy, embase = lock_protocol(client, project_id, headers, fake_provider)["search_strategies"]

    refused = client.post(url(project_id, "searches"), json={"strategy_id": embase["id"]}, headers=headers)
    assert refused.status_code == 400
    assert "Run the strategy on Embase.com or Ovid" in refused.json()["detail"]

    result = {
        "id": "123",
        "title": "Aspirin",
        "authors": "A",
        "year": 2020,
        "venue": "",
        "doi": "10.1/x",
        "abstract": "a",
        "identifiers": {"pmid": "123"},
        "url": "https://pubmed.ncbi.nlm.nih.gov/123/",
    }
    pubmed = search_sources.CONNECTORS["pubmed"]
    monkeypatch.setitem(
        search_sources.CONNECTORS, "pubmed", replace(pubmed, search=lambda query, limit: ([result], 1532))
    )

    run = client.post(
        url(project_id, "searches"), json={"strategy_id": pubmed_strategy["id"], "limit": 100}, headers=headers
    )
    assert run.status_code == 201
    body = run.json()
    assert (body["source"], body["result_count"], body["total_available"], body["interface"], body["kind"]) == (
        "PubMed",
        1,
        1532,
        "PubMed (NCBI E-utilities API)",
        "database",
    )
    assert body["filters"] == {"retrieval_limit": 100}
    elsewhere = client.post(
        url(project_id, "searches"), json={"strategy_id": embase["id"], "connector": "pubmed"}, headers=headers
    )
    assert elsewhere.json()["source"] == "PubMed (search string written for Embase)"
    records = client.get(url(project_id, "records?include_duplicates=true"), headers=headers).json()
    assert records[0]["identifiers"] == {"pmid": "123"}
    assert [r["source"] for r in client.get(url(project_id, "search-runs"), headers=headers).json()] == [
        "PubMed",
        "PubMed (search string written for Embase)",
    ]


def test_failed_search_stores_nothing(client, project, fake_provider, monkeypatch):
    project_id, headers = project
    pubmed_strategy = lock_protocol(client, project_id, headers, fake_provider)["search_strategies"][0]

    def unavailable(query, limit):
        raise SearchError("PubMed search failed. Please try again shortly.")

    monkeypatch.setitem(
        search_sources.CONNECTORS, "pubmed", replace(search_sources.CONNECTORS["pubmed"], search=unavailable)
    )

    response = client.post(url(project_id, "searches"), json={"strategy_id": pubmed_strategy["id"]}, headers=headers)
    assert response.status_code == 502
    assert client.get(url(project_id, "records"), headers=headers).json() == []


def test_deduplication_and_prisma_counts_come_from_stored_data(client, project, fake_provider):
    project_id, headers = project
    lock_protocol(client, project_id, headers, fake_provider)
    import_records(client, project_id, headers)
    duplicates = [{"title": "Different title", "doi": "10.1/A"}, {"title": "Statin trial!"}]
    import_records(client, project_id, headers, duplicates, file_name="second.csv")

    assert client.post(url(project_id, "deduplicate"), headers=headers).json() == {
        "duplicates_marked": 2,
        "possible_duplicates": 0,
    }
    assert client.post(url(project_id, "deduplicate"), headers=headers).json() == {
        "duplicates_marked": 0,
        "possible_duplicates": 0,
    }
    assert len(client.get(url(project_id, "records"), headers=headers).json()) == 2
    assert len(client.get(url(project_id, "records?include_duplicates=true"), headers=headers).json()) == 4
    assert client.get(url(project_id, "prisma"), headers=headers).json() == {
        "identified_from_databases": 0,
        "identified_from_registers": 0,
        "identified_from_other_methods": 0,
        "identified_from_uploads": 4,
        "other_methods": {"citation_searching": 0, "grey_literature_and_websites": 0},
        "by_source": {"Manual upload: export.csv": 2, "Manual upload: second.csv": 2},
        "duplicates_removed": 2,
        "screened": 2,
        "excluded": 0,
        "included": 0,
        "awaiting_decision": 2,
        "excluded_by_automation": 0,
        "reports_sought_for_retrieval": 0,
        "reports_not_retrieved": 0,
        "reports_assessed": 0,
        "reports_excluded": {},
        "awaiting_full_text_decision": 0,
        "studies_included": 0,
        "reports_of_included_studies": 0,
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
    fake_provider('{"decision": "Include", "reasoning": "Adults in a trial", "supporting_quote": "Adults  RANDOMIZED"}')

    body = {"record_ids": [r["id"] for r in records]}
    screened = run_ai(client, project_id, headers, "screening/ai", body)

    assert [(r["ai_screening"]["decision"], r["final_decision"]) for r in screened] == [("Include", None)] * 2
    assert [r["ai_screening"]["quote_verified"] for r in screened] == [True, True]
    assert client.get(url(project_id, "prisma"), headers=headers).json()["included"] == 0


def test_quotes_missing_from_the_record_are_flagged_as_unverified(client, project, fake_provider):
    project_id, headers = project
    record_id = open_screening(client, project_id, headers, fake_provider)[0]["id"]
    fake_provider(
        '{"decision": "Include", "reasoning": "r", "supporting_quote": "a double-blind trial of 9,000 adults"}'
    )

    body = {"record_ids": [record_id]}
    screened = run_ai(client, project_id, headers, "screening/ai", body)[0]

    assert screened["ai_screening"]["quote_verified"] is False


def test_unusable_ai_screening_output_is_stored_as_an_error(client, project, fake_provider):
    project_id, headers = project
    record_id = open_screening(client, project_id, headers, fake_provider)[0]["id"]
    calls = fake_provider('{"decision": "Probably", "reasoning": "unsure"}')
    calls.clear()

    body = {"record_ids": [record_id]}
    screened = run_ai(client, project_id, headers, "screening/ai", body)[0]

    assert screened["ai_screening"]["decision"] is None
    assert "required structure" in screened["ai_screening"]["error"]
    assert len(calls) == 2, "an unusable reply gets exactly one repair attempt"
    stored = client.get(url(project_id, "records"), headers=headers).json()[0]
    assert "required structure" in stored["ai_screening"]["error"]


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
    studies = client.get(url(project_id, "studies"), headers=headers).json()
    assert [[report["record_id"] for report in study["reports"]] for study in studies] == [[included["id"]]]
    study_id = studies[0]["id"]
    fields = {name: field["id"] for name, field in form_fields(client, project_id, headers).items()}
    fake_provider(
        json.dumps(
            {
                "arms": [],
                "values": [
                    {"field_id": fields["Sample Size"], "value": "120", "quote": "Adults  RANDOMIZED to aspirin."},
                    {"field_id": fields["Mean Age"], "not_reported": True},
                    {"field_id": fields["Adverse Events"], "value": "Bleeding", "quote": "Bleeding was common."},
                ],
            }
        )
    )

    job = client.post(url(project_id, "extraction/ai"), json={"study_ids": [study_id]}, headers=headers)

    assert job.status_code == 202, job.text
    assert job.json()["status"] == "completed", job.json()
    cells = {
        cell["field_id"]: cell
        for cell in client.get(url(project_id, f"studies/{study_id}/extraction"), headers=headers).json()["cells"]
    }
    assert cells[fields["Sample Size"]]["ai_suggestion"]["grounding"] == "grounded"
    assert cells[fields["Mean Age"]]["ai_suggestion"]["grounding"] == "not_reported"
    ungrounded = cells[fields["Adverse Events"]]["ai_suggestion"]
    assert ungrounded["grounding"] == "ungrounded"
    accept = {"source": "ai_accepted"}
    rejected = client.put(
        url(project_id, f"studies/{study_id}/extraction/values"),
        json={"field_id": fields["Adverse Events"], "ai_suggestion_id": ungrounded["id"], **accept},
        headers=headers,
    )
    assert rejected.status_code == 409
    accepted = extract(
        client,
        project_id,
        headers,
        study_id,
        fields["Sample Size"],
        ai_suggestion_id=cells[fields["Sample Size"]]["ai_suggestion"]["id"],
        **accept,
    )
    assert (accepted["state"], accepted["final"]["display"]) == ("final", "120")
    complete_stage(client, project_id, headers, "extraction")

    assert (
        client.post(url(project_id, "appraisal/ai"), json={"record_ids": [excluded["id"]]}, headers=headers).status_code
        == 400
    )
    fake_provider(APPRAISAL_REPLY)
    appraised = run_ai(client, project_id, headers, "appraisal/ai", {"record_ids": [included["id"]]})[0]
    assert appraised["appraisal"]["tool"] == "ROB-2"
    assert appraised["appraisal"]["judgments"]["D1: Randomization"] == "Low"
    assert appraised["appraisal"]["judgments"]["D2: Deviations"] == "Missing from AI response"
    assert appraised["appraisal"]["judgments"]["Overall"] == "Low Risk"


def test_screeners_cannot_run_extraction(client, project, make_user):
    project_id, owner = project
    screener = add_member(client, project_id, owner, make_user, "screener")

    response = client.post(url(project_id, "extraction/ai"), json={"study_ids": [1]}, headers=screener)

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

    created = client.post(url(project_id, "synthesis"), headers=headers)

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
