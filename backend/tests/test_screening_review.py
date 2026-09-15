"""Dual and blinded screening, adjudication, agreement, full-text screening, prioritization, stopping rules,
quality-assurance sampling, AI accuracy, settings, and the PRISMA 2020 flow diagram."""

import hashlib
import json
from datetime import timedelta

import pytest
from sqlalchemy import select
from workflow_helpers import (
    add_member,
    complete_stage,
    create_project,
    decide,
    import_records,
    lock_protocol,
    open_extraction,
    open_screening,
    run_ai,
    url,
)

import models
from database import SessionLocal

SCREENING_REPLY = '{"decision": "Include", "reasoning": "Adults in a trial", "supporting_quote": null}'
THREE_RECORDS = [
    {"title": "Aspirin trial", "doi": "10.1/a", "abstract": "Adults randomized to aspirin."},
    {"title": "Statin trial", "abstract": "Adults randomized to statins."},
    {"title": "Metformin trial", "abstract": "Adults randomized to metformin."},
]


@pytest.fixture
def project(client, auth_headers):
    return create_project(client, auth_headers), auth_headers


def set_screening(client, project_id, headers, **changes):
    current = client.get(url(project_id, "review-settings"), headers=headers).json()
    body = {"screening": {**current["screening"], **changes}, "extraction": current["extraction"]}
    return client.put(url(project_id, "review-settings"), json=body, headers=headers)


def records_by_id(client, project_id, headers):
    return {record["id"]: record for record in client.get(url(project_id, "records"), headers=headers).json()}


def test_dual_screening_is_blinded_and_disagreements_need_adjudication(client, project, make_user, fake_provider):
    project_id, owner = project
    first, second = open_screening(client, project_id, owner, fake_provider)
    assert set_screening(client, project_id, owner, title_abstract_reviewers=2).status_code == 200
    screener = add_member(client, project_id, owner, make_user, "screener")
    fake_provider(SCREENING_REPLY)
    run_ai(client, project_id, owner, "screening/ai", {"record_ids": [first["id"], second["id"]]})

    mine = decide(client, project_id, owner, first["id"], "include")

    assert (mine["screening"]["title_abstract"]["state"], mine["final_decision"]) == ("awaiting_second_reviewer", None)
    unseen = records_by_id(client, project_id, screener)[first["id"]]
    assert (unseen["screening"]["title_abstract"]["state"], unseen["ai_screening"], unseen["ai_screening_hidden"]) == (
        "hidden",
        None,
        True,
    )
    conflict = decide(client, project_id, screener, first["id"], "exclude")
    assert conflict["screening"]["title_abstract"]["state"] == "conflict"
    decide(client, project_id, owner, second["id"], "exclude")
    decide(client, project_id, screener, second["id"], "exclude")
    blocked = client.post(url(project_id, "workflow/screening/complete"), json={"note": "Done"}, headers=owner)
    assert blocked.status_code == 409
    assert "No unresolved disagreements between reviewers" in blocked.json()["detail"]

    assert client.get(url(project_id, "screening/conflicts"), headers=screener).status_code == 403
    conflicts = client.get(url(project_id, "screening/conflicts"), headers=owner).json()
    assert [(c["record"]["id"], sorted(d["decision"] for d in c["decisions"])) for c in conflicts] == [
        (first["id"], ["exclude", "include"])
    ]
    agreed = {"decision": "include", "rationale": "Agreed records are not adjudicated"}
    assert (
        client.put(url(project_id, f"records/{second['id']}/adjudication"), json=agreed, headers=owner).status_code
        == 409
    )
    adjudication = {"decision": "include", "rationale": "Adults in a randomized trial meet every criterion."}
    settled = client.put(
        url(project_id, f"records/{first['id']}/adjudication"), json=adjudication, headers=owner
    ).json()
    assert (settled["screening"]["title_abstract"]["state"], settled["final_decision"]) == ("adjudicated", "include")

    stats = client.get(url(project_id, "screening/agreement"), headers=owner).json()
    assert (stats["overall"]["n"], stats["overall"]["observed_agreement"]) == (2, 0.5)
    assert len(stats["pairs"]) == 1
    complete_stage(client, project_id, owner, "screening")


def test_full_text_decisions_need_reasons_and_feed_the_prisma_flow(client, project, fake_provider):
    project_id, headers = project
    first, second, third = open_screening(client, project_id, headers, fake_provider, records=THREE_RECORDS)
    for record in (first, second, third):
        decide(client, project_id, headers, record["id"], "include")
    complete_stage(client, project_id, headers, "screening")

    no_reason = {"decision": "exclude", "stage": "full_text"}
    response = client.put(url(project_id, f"records/{first['id']}/decision"), json=no_reason, headers=headers)
    assert response.status_code == 422
    decide(client, project_id, headers, first["id"], "exclude", stage="full_text", reason_code="wrong_population")
    decide(client, project_id, headers, second["id"], "include", stage="full_text")
    decide(client, project_id, headers, third["id"], "not_retrieved", stage="full_text")

    flow = client.get(url(project_id, "prisma/flow"), headers=headers).json()
    column = flow["columns"]["databases"]
    assert (
        column["reports_sought"],
        column["reports_not_retrieved"],
        column["reports_assessed"],
        column["reports_excluded"],
    ) == (3, 1, 2, {"Wrong population": 1})
    assert flow["included"] == {"studies": 1, "reports": 1}
    svg = client.get(url(project_id, "prisma/flow.svg"), headers=headers)
    assert svg.headers["content-type"].startswith("image/svg+xml")
    assert "Wrong population (n = 1)" in svg.text
    exported = client.get(url(project_id, "prisma/flow.csv"), headers=headers).text
    assert "dbr_excluded" in exported and "Wrong population, 1" in exported

    complete_stage(client, project_id, headers, "full_text_screening")
    studies = client.get(url(project_id, "studies"), headers=headers).json()
    assert [[report["record_id"] for report in study["reports"]] for study in studies] == [[second["id"]]]


def test_prioritized_screening_ranks_the_queue_and_stopping_needs_a_passing_test(client, project, fake_provider):
    project_id, headers = project
    records = [
        {"title": "Aspirin randomized trial for cardiovascular prevention in adults"},
        {"title": "Knee replacement surgery outcomes in an older adult cohort"},
        {"title": "Knee arthroplasty cohort outcomes"},
        {"title": "Aspirin and cardiovascular events: a randomized placebo trial"},
        {"title": "Hip and knee surgery registry cohort"},
        {"title": "Low-dose aspirin randomized trial for prevention"},
    ]
    imported = open_screening(client, project_id, headers, fake_provider, records=records)
    by_title = {record["title"]: record for record in imported}
    decide(client, project_id, headers, by_title[records[0]["title"]]["id"], "include")
    decide(client, project_id, headers, by_title[records[1]["title"]]["id"], "exclude")

    model = client.post(url(project_id, "screening/rank"), headers=headers).json()
    queue = client.get(url(project_id, "screening/queue"), headers=headers).json()

    assert (model["includes"], model["excludes"], model["ranked"]) == (1, 1, 4)
    assert queue["remaining"] == 4
    assert queue["records"][0]["title"].startswith(("Aspirin", "Low-dose aspirin"))
    assert queue["records"][0]["priority"]["rank"] == 1
    evaluation = client.post(url(project_id, "screening/stopping"), json={}, headers=headers).json()
    assert evaluation["result"]["can_stop"] is False
    accept = {"rationale": "We would like to stop screening now."}
    accepted = client.post(
        url(project_id, f"screening/stopping/{evaluation['id']}/accept"), json=accept, headers=headers
    )
    assert accepted.status_code == 409
    sample = client.post(url(project_id, "screening/qa-samples"), json={"size": 2}, headers=headers).json()
    assert (sample["size"], sample["pool_size"], sample["screened"]) == (2, 4, 0)


def test_an_accepted_stopping_rule_lets_screening_finish_and_is_reported(client, project, fake_provider):
    project_id, headers = project
    lock_protocol(client, project_id, headers, fake_provider)
    imported = import_records(
        client, project_id, headers, [{"title": hashlib.sha256(str(i).encode()).hexdigest()} for i in range(300)]
    )
    complete_stage(client, project_id, headers, "search")
    with SessionLocal() as db:
        owner_id = db.scalar(select(models.ProjectMember.user_id).where(models.ProjectMember.project_id == project_id))
        start = models.utcnow() - timedelta(days=1)
        for position, record in enumerate(imported[:290]):
            db.add(
                models.ScreeningDecision(
                    record_id=record["id"],
                    stage="title_abstract",
                    reviewer_id=owner_id,
                    decision="include" if position < 10 else "exclude",
                    created_at=start + timedelta(seconds=position),
                )
            )
        db.commit()

    evaluation = client.post(url(project_id, "screening/stopping"), json={}, headers=headers).json()
    assert evaluation["result"]["can_stop"] is True
    accept = {"rationale": "Recall of 95% is supported by the hypergeometric test."}
    accepted = client.post(
        url(project_id, f"screening/stopping/{evaluation['id']}/accept"), json=accept, headers=headers
    )

    assert accepted.status_code == 200, accepted.text
    complete_stage(client, project_id, headers, "screening")
    counts = client.get(url(project_id, "prisma"), headers=headers).json()
    assert (counts["excluded_by_automation"], counts["excluded"], counts["reports_sought_for_retrieval"]) == (
        10,
        280,
        10,
    )


def test_full_text_ai_screening_judges_each_criterion_with_evidence_passages(client, project, fake_provider):
    project_id, headers = project
    first, second = open_screening(client, project_id, headers, fake_provider)
    decide(client, project_id, headers, first["id"], "include")
    decide(client, project_id, headers, second["id"], "exclude")
    complete_stage(client, project_id, headers, "screening")
    text = b"Methods\nWe randomized 120 adults to aspirin or placebo.\n\nResults\nFewer heart attacks with aspirin."
    document = client.post(
        url(project_id, f"records/{first['id']}/documents"), files={"file": ("trial.txt", text)}, headers=headers
    ).json()
    spans = client.get(url(project_id, f"documents/{document['id']}"), headers=headers).json()["spans"]
    span_id = next(span["id"] for span in spans if "randomized 120 adults" in span["text"])
    criteria = [c for c in client.get(url(project_id, "criteria"), headers=headers).json() if c["status"] == "accepted"]
    fake_provider(
        json.dumps(
            {
                "criteria": [
                    {
                        "criterion_id": criteria[0]["id"],
                        "judgment": "met",
                        "rationale": "Adults were randomized",
                        "quote": "randomized 120 adults",
                        "passage_id": span_id,
                    }
                ],
                "decision": "Include",
                "confidence": 0.9,
                "reasoning": "Meets the criteria",
                "supporting_quote": "a sentence that is not in the paper",
                "supporting_passage_id": span_id,
            }
        )
    )

    screened = run_ai(client, project_id, headers, "full-text-screening/ai", {"record_ids": [first["id"]]})[0]

    suggestion = screened["ai_full_text_screening"]
    judgments = suggestion["criteria_judgments"]
    assert (judgments[0]["judgment"], judgments[0]["quote_verified"], judgments[0]["span_id"]) == ("met", True, span_id)
    assert [j["judgment"] for j in judgments[1:]] == ["unclear"] * (len(criteria) - 1)
    assert (suggestion["quote_verified"], suggestion["supporting_span_id"], suggestion["confidence"]) == (
        False,
        None,
        0.9,
    )
    refused = client.post(
        url(project_id, "full-text-screening/ai"), json={"record_ids": [second["id"]]}, headers=headers
    )
    assert refused.status_code == 400
    decide(client, project_id, headers, first["id"], "include", stage="full_text")
    performance = client.get(url(project_id, "screening/ai-performance?stage=full_text"), headers=headers).json()
    assert (performance["compared"], performance["true_positives"], performance["sensitivity"]) == (1, 1, 1.0)


def test_settings_changes_are_audited_and_locked_for_signed_off_stages(client, project, fake_provider):
    project_id, headers = project
    open_extraction(client, project_id, headers, fake_provider)

    locked = set_screening(client, project_id, headers, title_abstract_reviewers=2)
    current = client.get(url(project_id, "review-settings"), headers=headers).json()
    extraction = {"screening": current["screening"], "extraction": {**current["extraction"], "mode": "dual"}}
    changed = client.put(url(project_id, "review-settings"), json=extraction, headers=headers)

    assert locked.status_code == 409
    assert changed.status_code == 200
    assert changed.json()["extraction"]["mode"] == "dual"
    events = client.get(url(project_id, "audit"), headers=headers).json()["events"]
    assert events[0]["action"] == "review_settings.updated"
