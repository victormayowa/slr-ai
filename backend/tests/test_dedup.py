"""Automatic duplicate detection, candidate pairs for reviewers, reviewer decisions, and file imports."""

import pytest
from test_billing import billing_on, credit, set_limits  # noqa: F401  (billing_on is used as a fixture)
from workflow_helpers import create_project, import_records, lock_protocol, url, workflow

import models
from database import SessionLocal
from dedup import candidate_pairs, find_duplicates

RIS_EXPORT = b"""TY  - JOUR
TI  - Aspirin for primary prevention of cardiovascular events
AU  - Smith, John
PY  - 2019
DO  - 10.5555/aspirin
AB  - Adults were randomized.
ER  -
"""


@pytest.fixture
def project(client, auth_headers):
    return create_project(client, auth_headers), auth_headers


def record(record_id, title, year="2019", doi="", authors="Smith J", identifiers=None):
    return models.Record(
        id=record_id,
        title=title,
        year=year,
        doi=doi,
        authors=authors,
        identifiers=identifiers or {},
        duplicate_of_id=None,
    )


def test_shared_identifiers_and_matching_titles_are_certain_duplicates():
    records = [
        record(1, "Aspirin trial in older adults", identifiers={"pmid": "31234567"}),
        record(2, "ASPREE: aspirin in the elderly", identifiers={"pmid": "31234567"}),
        record(3, "Aspirin trial in older adults.", doi="https://doi.org/10.1/X"),
        record(4, "Statins and dementia", doi="10.1/a"),
        record(5, "Statins and dementia", doi="10.1/b"),
        record(6, "Aspirin trial in older adults", year="2020"),
    ]

    assert find_duplicates(records) == {2: 1, 3: 1}


def test_near_matches_are_candidates_for_a_reviewer_not_marked_automatically():
    records = [
        record(1, "Low-dose aspirin for primary prevention in adults aged 50 to 70"),
        record(2, "Low dose aspirin for the primary prevention in adults aged 50-70", year="2020"),
        record(3, "Statins and dementia", doi="10.1/a"),
        record(4, "Statins and dementia", doi="10.1/b"),
        record(5, "A completely different study of exercise"),
    ]

    pairs = candidate_pairs(records, reviewed=set())

    assert [(p.record_id, p.other_id) for p in pairs] == [(3, 4), (1, 2)]
    assert "Different DOIs" in pairs[0].reasons
    assert pairs[1].reasons[1:] == ["Years differ by one", "Same first author"]
    assert candidate_pairs(records, reviewed={(1, 2), (3, 4)}) == []


def test_the_plan_says_how_many_pairs_are_reviewed_at_a_time(client, project, fake_provider, billing_on):  # noqa: F811
    project_id, headers = project
    credit("user", client.get("/api/auth/me", headers=headers).json()["id"], "5")
    lock_protocol(client, project_id, headers, fake_provider)
    # Six pairs of near-identical titles; the free plan offers ten at a time, so lower it to see the batch work.
    import_records(
        client,
        project_id,
        headers,
        [
            {"title": f"Exercise and depression in older people trial {number}", "year": "2018", "doi": f"10.1/{i}"}
            for number in range(3)
            for i in (f"{number}a", f"{number}b")
        ],
    )
    set_limits("free", duplicate_batch=2)
    try:
        listing = client.get(url(project_id, "duplicate-candidates"), headers=headers).json()

        assert listing["batch"] == 2
        assert len(listing["pairs"]) == 2
        assert listing["total"] >= 3, "the rest are still waiting"

        decided = client.post(
            url(project_id, "duplicate-candidates/decide-all"), json={"decision": "not_duplicate"}, headers=headers
        ).json()

        assert decided["pairs"] == 2, "deciding them all decides the batch that was shown"
        assert decided["remaining"] >= 1
    finally:
        set_limits("free", duplicate_batch=10)


def test_every_pair_can_be_decided_at_once(client, project, fake_provider):
    project_id, headers = project
    lock_protocol(client, project_id, headers, fake_provider)
    first, second, third, fourth, fifth = import_records(
        client,
        project_id,
        headers,
        [
            # Three near-identical titles, so the pairs chain: merging one changes what the next pair points at.
            {"title": "Low-dose aspirin for primary prevention in adults", "year": "2019", "doi": "10.1/a"},
            {"title": "Low dose aspirin for the primary prevention in adults", "year": "2019", "doi": "10.1/b"},
            {"title": "Low-dose aspirin for primary prevention in adult patients", "year": "2019", "doi": "10.1/c"},
            {"title": "Exercise and depression in older people", "year": "2018"},
            {"title": "Exercise and depression in older people: a trial", "year": "2018"},
        ],
    )

    decided = client.post(
        url(project_id, "duplicate-candidates/decide-all"), json={"decision": "duplicate"}, headers=headers
    )

    assert decided.status_code == 200, decided.text
    assert decided.json()["merged"] >= 3, "the later record of every pair is set aside"
    assert client.get(url(project_id, "duplicate-candidates"), headers=headers).json()["pairs"] == []
    with SessionLocal() as db:
        kept_by = {
            row["id"]: db.get(models.Record, row["id"]).duplicate_of_id for row in (first, second, third, fourth, fifth)
        }
    assert kept_by[first["id"]] is None, "the earliest record of each pair is kept"
    assert kept_by[second["id"]] == first["id"]
    assert kept_by[third["id"]] == first["id"], "a chain of pairs ends at one record"
    assert kept_by[fourth["id"]] is None
    assert kept_by[fifth["id"]] == fourth["id"]
    unmet = [r["label"] for r in workflow(client, project_id, headers)["search"]["requirements"] if not r["met"]]
    assert "Possible duplicates reviewed" not in unmet
    event = client.get(url(project_id, "audit"), headers=headers).json()["events"][0]
    assert event["action"] == "duplicates.reviewed_all"
    assert event["details"]["decision"] == "duplicate"


def test_all_pairs_can_be_kept_as_separate_studies(client, project, fake_provider):
    project_id, headers = project
    lock_protocol(client, project_id, headers, fake_provider)
    first, second = import_records(
        client,
        project_id,
        headers,
        [
            {"title": "Exercise and depression in older people", "year": "2018"},
            {"title": "Exercise and depression in older people: a trial", "year": "2018"},
        ],
    )

    decided = client.post(
        url(project_id, "duplicate-candidates/decide-all"), json={"decision": "not_duplicate"}, headers=headers
    )

    assert (decided.status_code, decided.json()["merged"]) == (200, 0)
    with SessionLocal() as db:
        kept_by = [db.get(models.Record, row["id"]).duplicate_of_id for row in (first, second)]
    assert kept_by == [None, None], "nothing is set aside"
    assert client.get(url(project_id, "duplicate-candidates"), headers=headers).json()["pairs"] == []


def test_reviewers_decide_candidate_pairs_before_search_sign_off(client, project, fake_provider):
    project_id, headers = project
    lock_protocol(client, project_id, headers, fake_provider)
    first, second, third, fourth = import_records(
        client,
        project_id,
        headers,
        [
            {"title": "Low-dose aspirin for primary prevention in adults", "year": "2019", "doi": "10.1/a"},
            {"title": "Low dose aspirin for the primary prevention in adults", "year": "2019", "doi": "10.1/b"},
            {"title": "Exercise and depression in older people", "year": "2018"},
            {"title": "Exercise and depression in older people: a trial", "year": "2018"},
        ],
    )
    requirement = "Possible duplicates reviewed"

    pairs = client.get(url(project_id, "duplicate-candidates"), headers=headers).json()["pairs"]
    assert {(p["record"]["id"], p["other"]["id"]) for p in pairs} == {
        (first["id"], second["id"]),
        (third["id"], fourth["id"]),
    }
    unmet = [r["label"] for r in workflow(client, project_id, headers)["search"]["requirements"] if not r["met"]]
    assert requirement in unmet

    not_same = {"record_id": first["id"], "other_record_id": second["id"], "decision": "not_duplicate"}
    assert (
        client.post(url(project_id, "duplicate-candidates/decision"), json=not_same, headers=headers).status_code == 200
    )
    missing_keep = {"record_id": third["id"], "other_record_id": fourth["id"], "decision": "duplicate"}
    assert (
        client.post(url(project_id, "duplicate-candidates/decision"), json=missing_keep, headers=headers).status_code
        == 422
    )
    same = {**missing_keep, "keep_record_id": third["id"]}
    decided = client.post(url(project_id, "duplicate-candidates/decision"), json=same, headers=headers).json()

    assert decided["records"][1]["duplicate_of_id"] == third["id"]
    assert client.get(url(project_id, "duplicate-candidates"), headers=headers).json()["pairs"] == []
    unmet = [r["label"] for r in workflow(client, project_id, headers)["search"]["requirements"] if not r["met"]]
    assert requirement not in unmet
    event = client.get(url(project_id, "audit"), headers=headers).json()["events"][0]
    assert (event["action"], event["details"]["kept_record_id"]) == ("duplicates.reviewed", third["id"])


def test_export_files_import_with_prisma_s_details(client, project, fake_provider):
    project_id, headers = project
    lock_protocol(client, project_id, headers, fake_provider)
    form = {"database": "Embase", "interface": "Embase.com", "searched_on": "2026-09-01", "query": "'aspirin'/exp"}

    response = client.post(
        url(project_id, "imports/file"),
        files={"file": ("embase.ris", RIS_EXPORT, "application/x-research-info-systems")},
        data=form,
        headers=headers,
    )

    assert response.status_code == 201, response.text
    run = response.json()
    assert (run["kind"], run["file_format"], run["result_count"], run["interface"], run["searched_on"]) == (
        "database",
        "ris",
        1,
        "Embase.com",
        "2026-09-01",
    )
    assert run["source"] == "Embase export (embase.ris)"
    assert client.get(url(project_id, "prisma"), headers=headers).json()["identified_from_databases"] == 1
    unreadable = client.post(
        url(project_id, "imports/file"),
        files={"file": ("notes.docx", b"hello", "application/octet-stream")},
        data=form,
        headers=headers,
    )
    assert unreadable.status_code == 400
