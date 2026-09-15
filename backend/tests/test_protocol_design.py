"""The structured review question, analysis plan, PRISMA-P protocol document, criteria elements, and protocol checks."""

import json

import pytest
from sqlalchemy import select
from workflow_helpers import (
    ANALYSIS_PLAN,
    PROTOCOL,
    QUESTION,
    add_member,
    complete_stage,
    create_project,
    design_protocol,
    generate_protocol,
    url,
)

import models
from database import SessionLocal


@pytest.fixture
def project(client, auth_headers):
    return create_project(client, auth_headers), auth_headers


def issue_codes(client, project_id, headers):
    return [issue["code"] for issue in client.get(url(project_id, "protocol-checks"), headers=headers).json()["issues"]]


def test_the_catalog_lists_frameworks_finer_and_prisma_p_sections(client, auth_headers):
    assert client.get("/api/protocol-frameworks").status_code == 401

    catalog = client.get("/api/protocol-frameworks", headers=auth_headers).json()

    assert [f["key"] for f in catalog["frameworks"]] == ["PICO", "PICOS", "PECO", "SPIDER", "PCC"]
    assert [e["key"] for e in catalog["frameworks"][0]["elements"]] == [
        "population",
        "intervention",
        "comparator",
        "outcomes",
    ]
    assert [c["key"] for c in catalog["finer_criteria"]] == ["feasible", "interesting", "novel", "ethical", "relevant"]
    assert [s["key"] for s in catalog["sections"] if s["required"]] == ["rationale", "objectives", "synthesis"]
    assert {s["prisma_p_item"] for s in catalog["sections"]} >= {"6", "7", "15a–15d", "17"}


def test_the_question_is_validated_saved_in_framework_order_and_audited(client, project, make_user):
    project_id, headers = project

    assert (
        client.put(url(project_id, "question"), json={**QUESTION, "framework": "XYZ"}, headers=headers).status_code
        == 422
    )
    bad_element = {**QUESTION, "elements": {"exposure": "Smoking"}}
    assert client.put(url(project_id, "question"), json=bad_element, headers=headers).status_code == 422
    bad_finer = {**QUESTION, "finer": {"cheap": {"rating": "yes"}}}
    assert client.put(url(project_id, "question"), json=bad_finer, headers=headers).status_code == 422

    body = {**QUESTION, "elements": {"outcomes": " Heart attacks ", "population": "Adults"}}
    body["finer"] = {"novel": {"rating": "partly", "note": "Two older reviews"}}
    saved = client.put(url(project_id, "question"), json=body, headers=headers).json()

    assert list(saved["elements"].items()) == [
        ("population", "Adults"),
        ("intervention", ""),
        ("comparator", ""),
        ("outcomes", "Heart attacks"),
    ]
    assert saved["finer"] == {"novel": {"rating": "partly", "note": "Two older reviews"}}
    event = client.get(url(project_id, "audit"), headers=headers).json()["events"][0]
    assert event["action"] == "question.updated"

    spider = client.put(
        url(project_id, "question"),
        json={"framework": "SPIDER", "question": "q", "elements": {"sample": "Nurses"}},
        headers=headers,
    ).json()
    assert list(spider["elements"]) == ["sample", "phenomenon_of_interest", "design", "evaluation", "research_type"]
    viewer = add_member(client, project_id, headers, make_user, "viewer")
    assert client.put(url(project_id, "question"), json=QUESTION, headers=viewer).status_code == 403


def test_an_ai_question_suggestion_changes_nothing_until_saved(client, project, fake_provider):
    project_id, headers = project
    client.put(url(project_id, "protocol"), json=PROTOCOL, headers=headers)
    reply = {
        "framework": "PICO",
        "question": "Does aspirin prevent heart attacks in adults?",
        "elements": [{"element": "population", "text": "Adults"}, {"element": "bogus", "text": "x"}],
        "finer_notes": [{"criterion": "novel", "note": "Check for recent reviews."}],
    }
    fake_provider(json.dumps(reply))

    response = client.post(url(project_id, "question/ai"), headers=headers)

    assert response.status_code == 200
    content = response.json()["content"]
    assert content["elements"] == {"population": "Adults", "intervention": "", "comparator": "", "outcomes": ""}
    assert content["finer_notes"] == {"novel": "Check for recent reviews."}
    assert client.get(url(project_id, "question"), headers=headers).json()["question"] == ""
    with SessionLocal() as db:
        run = db.scalar(select(models.AIRun).where(models.AIRun.project_id == project_id))
        assert (run.task, run.prompt_version, run.status) == ("question", "question-v1", "succeeded")


def test_the_analysis_plan_is_validated_and_saved(client, project):
    project_id, headers = project
    invalid = {"outcomes": [{"name": "Death", "priority": "important"}]}

    assert client.put(url(project_id, "analysis-plan"), json=invalid, headers=headers).status_code == 422
    saved = client.put(url(project_id, "analysis-plan"), json=ANALYSIS_PLAN, headers=headers).json()

    assert saved["synthesis_approach"] == "meta_analysis"
    assert saved["outcomes"] == [
        {"name": "Myocardial infarction", "priority": "primary", "timepoint": "", "measure": ""}
    ]
    assert client.get(url(project_id, "analysis-plan"), headers=headers).json() == saved
    event = client.get(url(project_id, "audit"), headers=headers).json()["events"][0]
    assert event["action"] == "analysis_plan.updated"


def test_section_drafts_are_grounded_and_accepted_with_provenance(client, project, make_user, fake_provider):
    project_id, headers = project
    client.put(url(project_id, "protocol"), json=PROTOCOL, headers=headers)
    client.put(url(project_id, "question"), json=QUESTION, headers=headers)
    calls = fake_provider(
        json.dumps({"content": "Funded by [TO COMPLETE: funding source].", "missing_information": ["funding source"]})
    )

    draft = client.post(url(project_id, "protocol-sections/support/ai-draft"), headers=headers).json()

    assert draft["content"]["missing_information"] == ["funding source"]
    assert "Never invent facts" in calls[0]["prompt"]
    assert "Does aspirin prevent heart attacks in adults?" in calls[0]["prompt"]
    sections = {s["key"]: s for s in client.get(url(project_id, "protocol-sections"), headers=headers).json()}
    assert (sections["support"]["content"], sections["support"]["draft"]["id"]) == ("", draft["id"])

    body = {"content": draft["content"]["content"], "based_on_draft_id": draft["id"]}
    saved = client.put(url(project_id, "protocol-sections/support"), json=body, headers=headers).json()
    assert (saved["ai_assisted"], saved["content"]) == (True, "Funded by [TO COMPLETE: funding source].")
    edited = client.put(url(project_id, "protocol-sections/support"), json={"content": "Funded by X."}, headers=headers)
    assert edited.json()["ai_assisted"] is True, "accepted AI help stays on record through later edits"
    assert "section_placeholder" not in issue_codes(client, project_id, headers)

    wrong_section = {"content": "Text", "based_on_draft_id": draft["id"]}
    assert (
        client.put(url(project_id, "protocol-sections/rationale"), json=wrong_section, headers=headers).status_code
        == 404
    )
    assert (
        client.put(url(project_id, "protocol-sections/nonsense"), json={"content": "x"}, headers=headers).status_code
        == 404
    )
    _, other = make_user()
    other_project = create_project(client, other, "Other")
    stolen = {"content": "Text", "based_on_draft_id": draft["id"]}
    assert client.put(url(other_project, "protocol-sections/support"), json=stolen, headers=other).status_code == 404


def test_protocol_checks_report_what_blocks_locking(client, project):
    project_id, headers = project

    codes = issue_codes(client, project_id, headers)

    assert codes.count("element_missing") == 4
    assert {"question_missing", "no_primary_outcome", "section_missing", "finer_incomplete"} <= set(codes)

    client.put(url(project_id, "question"), json=QUESTION, headers=headers)
    for kind, text in (("inclusion", "Adults"), ("exclusion", "adults.")):
        body = {"kind": kind, "text": text, "element": "population"}
        assert client.post(url(project_id, "criteria"), json=body, headers=headers).status_code == 201
    client.put(
        url(project_id, "protocol-sections/rationale"), json={"content": "Why [TO COMPLETE: data]"}, headers=headers
    )

    codes = issue_codes(client, project_id, headers)
    assert "contradictory_criteria" in codes
    assert "section_placeholder" in codes
    assert "element_missing" not in codes


def test_the_ai_consistency_review_keeps_only_real_criteria_and_elements(client, project, fake_provider):
    project_id, headers = project
    client.put(url(project_id, "question"), json=QUESTION, headers=headers)
    criterion = client.post(
        url(project_id, "criteria"), json={"kind": "inclusion", "text": "Children aged 5 to 12"}, headers=headers
    ).json()
    reply = {
        "issues": [
            {
                "severity": "error",
                "message": "The criterion includes children but the population is adults.",
                "criterion_ids": [criterion["id"], 999_999],
                "elements": ["population", "bogus"],
            }
        ]
    }
    fake_provider(json.dumps(reply))

    review = client.post(url(project_id, "protocol-checks/ai"), headers=headers).json()

    assert review["content"]["issues"][0]["criterion_ids"] == [criterion["id"]]
    assert review["content"]["issues"][0]["elements"] == ["population"]
    checks = client.get(url(project_id, "protocol-checks"), headers=headers).json()
    assert checks["ai_review"]["id"] == review["id"]


def test_criteria_carry_the_element_they_restrict(client, project, fake_provider):
    project_id, headers = project

    generated = generate_protocol(client, project_id, headers, fake_provider)
    assert [(c["text"], c["element"], c["source"]) for c in generated["criteria"]] == [
        ("Adults", "population", "ai"),
        ("Randomized trials", "study_design", "ai"),
        ("Children", "population", "ai"),
    ]

    added = client.post(
        url(project_id, "criteria"),
        json={"kind": "exclusion", "text": "Pregnant women", "element": "population"},
        headers=headers,
    ).json()
    assert (added["status"], added["source"]) == ("accepted", "reviewer")
    unknown = {"kind": "inclusion", "text": "x", "element": "exposure"}
    assert client.post(url(project_id, "criteria"), json=unknown, headers=headers).status_code == 422
    relinked = client.patch(url(project_id, f"criteria/{added['id']}"), json={"element": "setting"}, headers=headers)
    assert relinked.json()["element"] == "setting"
    unlinked = client.patch(url(project_id, f"criteria/{added['id']}"), json={"element": ""}, headers=headers)
    assert unlinked.json()["element"] is None


def test_the_protocol_locks_only_when_designed_and_the_snapshot_includes_the_design(client, project, fake_provider):
    project_id, headers = project
    generate_protocol(client, project_id, headers, fake_provider)
    for kind in ("inclusion", "exclusion"):
        client.post(url(project_id, "criteria/accept-all"), json={"kind": kind}, headers=headers)

    blocked = client.post(url(project_id, "workflow/protocol/complete"), json={"note": "Approved"}, headers=headers)
    assert blocked.status_code == 409
    assert "Review question" in blocked.json()["detail"]
    assert "primary outcome" in blocked.json()["detail"]

    design_protocol(client, project_id, headers)
    complete_stage(client, project_id, headers, "protocol")

    [version] = client.get(url(project_id, "workflow/protocol/snapshots"), headers=headers).json()
    content = client.get(url(project_id, f"workflow/snapshots/{version['id']}"), headers=headers).json()["content"]
    assert content["protocol"]["question"] == QUESTION["question"]
    assert content["protocol"]["analysis_plan"]["outcomes"][0]["priority"] == "primary"
    assert set(content["sections"]) == {"rationale", "objectives", "synthesis"}
    assert content["criteria"][0]["element"] == "population"
    assert client.put(url(project_id, "question"), json=QUESTION, headers=headers).status_code == 409
