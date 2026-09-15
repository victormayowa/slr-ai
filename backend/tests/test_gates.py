import pytest
from sqlalchemy import select
from workflow_helpers import (
    PROTOCOL,
    RECORDS,
    add_member,
    complete_stage,
    create_project,
    decide,
    design_protocol,
    generate_protocol,
    import_records,
    lock_protocol,
    open_screening,
    url,
    workflow,
)

import models
from database import SessionLocal


@pytest.fixture
def project(client, auth_headers):
    return create_project(client, auth_headers), auth_headers


def statuses(client, project_id, headers):
    return {stage: info["status"] for stage, info in workflow(client, project_id, headers).items()}


def test_a_new_project_starts_with_only_the_protocol_open(client, project):
    project_id, headers = project

    stages = workflow(client, project_id, headers)

    assert [info["status"] for info in stages.values()] == ["open"] + ["not_started"] * 5
    unmet = [r["label"] for r in stages["protocol"]["requirements"] if not r["met"]]
    assert unmet == [
        "Study description written",
        "Review question and every framework element written",
        "At least one accepted inclusion criterion",
        "At least one search strategy",
        "At least one primary outcome pre-specified",
        "Required protocol sections written (Rationale, Objectives, Data synthesis)",
    ]


def test_later_stages_refuse_changes_until_earlier_ones_are_signed_off(client, project):
    project_id, headers = project

    response = client.post(
        url(project_id, "imports"), json={"file_name": "export.csv", "records": RECORDS}, headers=headers
    )

    assert response.status_code == 409
    assert response.json()["detail"] == "Complete Protocol before working on Search and deduplication."


def test_a_stage_cannot_be_signed_off_until_its_requirements_are_met(client, project, fake_provider):
    project_id, headers = project
    generate_protocol(client, project_id, headers, fake_provider)

    response = client.post(url(project_id, "workflow/protocol/complete"), json={"note": "Looks good"}, headers=headers)

    assert response.status_code == 409
    assert "Every suggested criterion accepted or rejected" in response.json()["detail"]


def test_signing_off_the_protocol_versions_it_and_locks_editing(client, project, fake_provider):
    project_id, headers = project

    lock_protocol(client, project_id, headers, fake_provider)

    assert statuses(client, project_id, headers)["search"] == "open"
    blocked = client.put(url(project_id, "protocol"), json=PROTOCOL, headers=headers)
    assert blocked.status_code == 409
    assert "Reopen it with a rationale" in blocked.json()["detail"]
    [version] = client.get(url(project_id, "workflow/protocol/snapshots"), headers=headers).json()
    assert (version["version"], version["intact"], version["note"]) == (1, True, "Reviewed and approved")
    content = client.get(url(project_id, f"workflow/snapshots/{version['id']}"), headers=headers).json()["content"]
    assert [c["text"] for c in content["criteria"]] == ["Adults", "Randomized trials", "Children"]
    assert content["protocol"]["description"] == PROTOCOL["description"]


def test_amending_the_protocol_needs_a_rationale_and_creates_the_next_version(client, project, fake_provider):
    project_id, headers = project
    lock_protocol(client, project_id, headers, fake_provider)

    short = client.post(url(project_id, "workflow/protocol/reopen"), json={"rationale": "typo"}, headers=headers)
    assert short.status_code == 422

    rationale = "Adding an exclusion criterion for pediatric populations after peer review."
    reopened = client.post(url(project_id, "workflow/protocol/reopen"), json={"rationale": rationale}, headers=headers)
    assert reopened.status_code == 200
    assert client.put(url(project_id, "protocol"), json=PROTOCOL, headers=headers).status_code == 200

    complete_stage(client, project_id, headers, "protocol", note="Amendment approved")
    versions = client.get(url(project_id, "workflow/protocol/snapshots"), headers=headers).json()
    assert [v["version"] for v in versions] == [2, 1]
    latest = client.get(url(project_id, f"workflow/snapshots/{versions[0]['id']}"), headers=headers).json()
    assert latest["content"]["amendment_rationale"] == rationale


def test_reopening_a_stage_reopens_every_later_stage(client, project, fake_provider):
    project_id, headers = project
    open_screening(client, project_id, headers, fake_provider)

    rationale = "The PubMed strategy missed a key synonym for aspirin."
    client.post(url(project_id, "workflow/protocol/reopen"), json={"rationale": rationale}, headers=headers)

    stages = workflow(client, project_id, headers)
    assert (stages["protocol"]["status"], stages["search"]["status"]) == ("open", "not_started")
    assert stages["search"]["reopen_rationale"] == f"Reopened with Protocol: {rationale}"
    actions = [e["action"] for e in client.get(url(project_id, "audit"), headers=headers).json()["events"]]
    assert actions[0] == "stage.reopened"


def test_search_cannot_be_signed_off_while_duplicates_remain(client, project, fake_provider):
    project_id, headers = project
    lock_protocol(client, project_id, headers, fake_provider)
    import_records(client, project_id, headers, [RECORDS[0], RECORDS[0]])

    blocked = client.post(url(project_id, "workflow/search/complete"), json={"note": "Done"}, headers=headers)
    assert blocked.status_code == 409
    assert "Deduplication run on every record" in blocked.json()["detail"]

    client.post(url(project_id, "deduplicate"), headers=headers)
    stages = complete_stage(client, project_id, headers, "search")
    assert stages["screening"]["status"] == "open"


def test_screening_cannot_be_signed_off_with_undecided_records(client, project, fake_provider):
    project_id, headers = project
    first, second = open_screening(client, project_id, headers, fake_provider)
    decide(client, project_id, headers, first["id"], "include")
    decide(client, project_id, headers, second["id"], "undecided")

    blocked = client.post(url(project_id, "workflow/screening/complete"), json={"note": "Done"}, headers=headers)

    assert blocked.status_code == 409
    assert "Every record has an include or exclude decision" in blocked.json()["detail"]


def test_later_snapshots_record_the_protocol_version_they_followed(client, project, fake_provider):
    project_id, headers = project
    open_screening(client, project_id, headers, fake_provider)

    [search] = client.get(url(project_id, "workflow/search/snapshots"), headers=headers).json()
    content = client.get(url(project_id, f"workflow/snapshots/{search['id']}"), headers=headers).json()["content"]

    assert content["protocol_version"] == 1
    assert [run["result_count"] for run in content["runs"]] == [2]


def test_only_permitted_roles_can_sign_off_each_stage(client, project, make_user, fake_provider):
    project_id, owner = project
    generate_protocol(client, project_id, owner, fake_provider)
    for kind in ("inclusion", "exclusion"):
        client.post(url(project_id, "criteria/accept-all"), json={"kind": kind}, headers=owner)
    design_protocol(client, project_id, owner)
    screener = add_member(client, project_id, owner, make_user, "screener")
    statistician = add_member(client, project_id, owner, make_user, "statistician")
    methodologist = add_member(client, project_id, owner, make_user, "methodologist")
    body = {"note": "Approved"}

    assert client.post(url(project_id, "workflow/protocol/complete"), json=body, headers=screener).status_code == 403
    assert (
        client.post(url(project_id, "workflow/protocol/complete"), json=body, headers=methodologist).status_code == 200
    )
    import_records(client, project_id, owner)
    assert client.post(url(project_id, "workflow/search/complete"), json=body, headers=statistician).status_code == 403
    stages = workflow(client, project_id, screener)
    assert (stages["search"]["can_manage"], stages["synthesis"]["can_manage"]) == (False, False)


def test_tampered_snapshots_are_detected(client, project, fake_provider):
    project_id, headers = project
    lock_protocol(client, project_id, headers, fake_provider)
    [version] = client.get(url(project_id, "workflow/protocol/snapshots"), headers=headers).json()

    with SessionLocal() as db:
        snapshot = db.scalar(select(models.StageSnapshot).where(models.StageSnapshot.id == version["id"]))
        snapshot.content = {**snapshot.content, "criteria": []}
        db.commit()

    assert client.get(url(project_id, f"workflow/snapshots/{version['id']}"), headers=headers).json()["intact"] is False


def test_unknown_stages_are_not_found(client, project):
    project_id, headers = project

    response = client.post(url(project_id, "workflow/manuscript/complete"), json={"note": "Done"}, headers=headers)

    assert response.status_code == 404
