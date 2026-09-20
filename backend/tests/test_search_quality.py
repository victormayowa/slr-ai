"""Choosing databases with the AI, strategy versions and post-lock amendments, translation, MeSH lookup, recall
checks, and PRESS peer review."""

import json
from dataclasses import replace
from datetime import date

import pytest
from workflow_helpers import (
    PROTOCOL,
    add_member,
    complete_stage,
    create_project,
    design_protocol,
    generate_protocol,
    import_records,
    url,
    workflow,
)

import search_quality
import search_quality_routes
import search_sources

PRESS_REQUIREMENT = "PRESS peer review approved for every current search strategy, or PRESS waived with a reason"
APPROVE_ALL = {key: {"rating": "no_revision", "comment": ""} for key in search_quality.PRESS_KEYS}


@pytest.fixture
def project(client, auth_headers):
    return create_project(client, auth_headers), auth_headers


def lock_for_search(client, project_id, headers, fake_provider):
    """Generate, design, and sign off the protocol, and waive registration, leaving PRESS undone."""
    generated = generate_protocol(client, project_id, headers, fake_provider)
    for kind in ("inclusion", "exclusion"):
        client.post(url(project_id, "criteria/accept-all"), json={"kind": kind}, headers=headers)
    design_protocol(client, project_id, headers)
    complete_stage(client, project_id, headers, "protocol")
    waiver = {"reason": "Teaching exercise; the protocol won't be registered."}
    client.post(url(project_id, "registrations/waiver"), json=waiver, headers=headers)
    return generated["search_strategies"]


def unmet_search(client, project_id, headers):
    return [r["label"] for r in workflow(client, project_id, headers)["search"]["requirements"] if not r["met"]]


def test_the_ai_suggests_databases_and_drafts_a_string_for_the_ones_chosen(client, project, fake_provider):
    project_id, headers = project
    client.put(url(project_id, "protocol"), json=PROTOCOL, headers=headers)
    fake_provider(
        json.dumps(
            {
                "databases": [
                    {"database": "PubMed", "reason": "Core biomedical coverage."},
                    {"database": "Embase", "reason": "European drug trials PubMed misses."},
                    {"database": "pubmed", "reason": "A duplicate that should be dropped."},
                    {"database": "Regional Nursing Index", "reason": "A source OmniReview has no connector for."},
                ]
            }
        )
    )

    suggested = client.post(url(project_id, "search-databases/ai"), headers=headers)

    assert suggested.status_code == 200, suggested.text
    databases = suggested.json()["content"]["databases"]
    assert [item["database"] for item in databases] == ["PubMed", "Embase", "Regional Nursing Index"]
    assert [item["searchable"] for item in databases] == [True, False, False]
    assert "export the results as RIS" in next(item["note"] for item in databases if item["database"] == "Embase")
    assert client.get(url(project_id, "search-strategies"), headers=headers).json() == [], "nothing is added yet"

    fake_provider(
        json.dumps(
            {
                "searches": [
                    {"database": "PubMed", "query": "aspirin[tiab] AND prevention[tiab]"},
                    {"database": "Embase", "query": "'aspirin'/exp AND 'prevention'/exp"},
                ]
            }
        )
    )
    drafted = client.post(
        url(project_id, "search-strategies/ai"), json={"databases": ["PubMed", "Embase", "Scopus"]}, headers=headers
    )

    assert drafted.status_code == 201, drafted.text
    body = drafted.json()
    assert {item["database"] for item in body["strategies"]} == {"PubMed", "Embase"}
    assert body["missing"] == ["Scopus"], "reviewers are told which strings the AI didn't write"
    strategies = client.get(url(project_id, "search-strategies"), headers=headers).json()
    assert {item["database"]: item["searchable"] for item in strategies} == {"PubMed": True, "Embase": False}
    assert all(item["runs"] == 0 and item["last_searched_on"] is None for item in strategies)
    repeated = client.post(url(project_id, "search-strategies/ai"), json={"databases": ["PubMed"]}, headers=headers)
    assert repeated.status_code == 409, "a database that already has a strategy isn't drafted again"


def test_a_strategy_shows_what_its_searches_retrieved(client, project, fake_provider, monkeypatch):
    project_id, headers = project
    pubmed, _ = lock_for_search(client, project_id, headers, fake_provider)
    results = [
        {"title": "Aspirin trial", "authors": "Smith", "year": "2020", "doi": "10.1/a", "id": "1"},
        {"title": "Aspirin cohort", "authors": "Jones", "year": "2021", "doi": "10.1/b", "id": "2"},
    ]
    monkeypatch.setitem(
        search_sources.CONNECTORS,
        "pubmed",
        replace(search_sources.CONNECTORS["pubmed"], search=lambda query, limit: (results, 57)),
    )

    run = client.post(url(project_id, "searches"), json={"strategy_id": pubmed["id"], "limit": 10}, headers=headers)

    assert run.status_code == 201, run.text
    strategy = next(
        item
        for item in client.get(url(project_id, "search-strategies"), headers=headers).json()
        if item["id"] == pubmed["id"]
    )
    assert (strategy["runs"], strategy["records_retrieved"]) == (1, 2)
    assert strategy["last_searched_on"] == date.today().isoformat()
    assert strategy["last_search_version"] == strategy["version"]


def test_strategy_changes_after_the_protocol_is_locked_need_a_reason_and_are_versioned(client, project, fake_provider):
    project_id, headers = project
    pubmed, _ = lock_for_search(client, project_id, headers, fake_provider)
    path = url(project_id, f"search-strategies/{pubmed['id']}")

    assert (
        client.patch(path, json={"query": "aspirin[tiab] OR acetylsalicylic[tiab]"}, headers=headers).status_code == 422
    )
    changed = client.patch(
        path,
        json={"query": "aspirin[tiab] OR acetylsalicylic[tiab]", "note": "Added a synonym after PRESS feedback."},
        headers=headers,
    ).json()

    assert changed["version"] == 2
    versions = client.get(url(project_id, f"search-strategies/{pubmed['id']}/versions"), headers=headers).json()
    assert [(v["version"], v["note"]) for v in versions] == [
        (2, "Added a synonym after PRESS feedback."),
        (1, "Suggested by AI"),
    ]
    event = client.get(url(project_id, "audit"), headers=headers).json()["events"][0]
    assert event["action"] == "search_strategy.amended_after_protocol_lock"

    added = client.post(
        url(project_id, "search-strategies"),
        json={"database": "Scopus", "query": "TITLE-ABS-KEY(aspirin)", "note": "Translated from the PubMed strategy."},
        headers=headers,
    )
    assert (added.status_code, added.json()["version"]) == (201, 1)


def test_pubmed_strategies_translate_and_queries_validate(client, project, fake_provider):
    project_id, headers = project
    pubmed, embase = lock_for_search(client, project_id, headers, fake_provider)

    translated = client.post(
        url(project_id, f"search-strategies/{pubmed['id']}/translate"), json={"target": "ovid_medline"}, headers=headers
    )
    not_pubmed = client.post(
        url(project_id, f"search-strategies/{embase['id']}/translate"), json={"target": "scopus"}, headers=headers
    )
    checked = client.post(
        url(project_id, "search-query/validate"),
        json={"query": "aspirin[tiab] AND", "syntax": "pubmed"},
        headers=headers,
    )

    assert translated.json() == {
        "target": "ovid_medline",
        "target_label": "Ovid MEDLINE",
        "query": "aspirin.ti,ab.",
        "warnings": [],
    }
    assert not_pubmed.status_code == 400
    assert checked.json()["errors"] == ["AND needs a term after it"]


def test_mesh_lookup_returns_headings_and_entry_terms(client, auth_headers, monkeypatch):
    heading = {
        "ui": "D001241",
        "heading": "Aspirin",
        "entry_terms": ["Acetylsalicylic Acid"],
        "scope_note": "An analgesic.",
        "tree_numbers": ["D02.241"],
    }
    monkeypatch.setattr(search_quality_routes, "lookup_mesh", lambda term: [heading])

    response = client.get("/api/vocabulary/mesh?term=aspirin", headers=auth_headers)

    assert response.json() == [heading]
    assert client.get("/api/vocabulary/mesh?term=aspirin").status_code == 401


def test_recall_checks_record_found_and_missed_articles(client, project, fake_provider, monkeypatch):
    project_id, headers = project
    pubmed, embase = lock_for_search(client, project_id, headers, fake_provider)
    received = {}

    def found(connector, query, pmids, dois):
        received.update(connector=connector, query=query, pmids=pmids, dois=dois)
        return {"31234567", "10.1056/nejmoa1800722"}

    monkeypatch.setattr(search_quality_routes, "found_seeds", found)
    body = {"seeds": ["31234567", "https://doi.org/10.1056/NEJMoa1800722", "30000001", "31234567"]}

    check = client.post(url(project_id, f"search-strategies/{pubmed['id']}/recall-checks"), json=body, headers=headers)

    assert check.status_code == 201
    result = check.json()
    assert (result["connector"], result["found"], result["missed"], result["recall"]) == (
        "pubmed",
        ["31234567", "10.1056/nejmoa1800722"],
        ["30000001"],
        0.667,
    )
    assert received == {
        "connector": "pubmed",
        "query": "aspirin[tiab]",
        "pmids": ["31234567", "30000001"],
        "dois": ["10.1056/nejmoa1800722"],
    }
    assert (
        client.post(
            url(project_id, f"search-strategies/{embase['id']}/recall-checks"), json=body, headers=headers
        ).status_code
        == 400
    )
    bad = {"seeds": ["not an identifier"]}
    assert (
        client.post(
            url(project_id, f"search-strategies/{pubmed['id']}/recall-checks"), json=bad, headers=headers
        ).status_code
        == 422
    )


def test_press_reviews_come_from_someone_other_than_the_author_and_expire_when_strategies_change(
    client, project, fake_provider, make_user
):
    project_id, owner = project
    pubmed, embase = lock_for_search(client, project_id, owner, fake_provider)
    import_records(client, project_id, owner)
    methodologist = add_member(client, project_id, owner, make_user, "methodologist")

    assert PRESS_REQUIREMENT in unmet_search(client, project_id, owner)
    own = client.post(
        url(project_id, f"search-strategies/{pubmed['id']}/press-reviews"), json={"answers": APPROVE_ALL}, headers=owner
    )
    assert own.status_code == 409
    partial = {"answers": {"translation": APPROVE_ALL["translation"]}}
    assert (
        client.post(
            url(project_id, f"search-strategies/{pubmed['id']}/press-reviews"), json=partial, headers=methodologist
        ).status_code
        == 422
    )

    needs_work = {**APPROVE_ALL, "subject_headings": {"rating": "revision_required", "comment": "Add Aspirin[mh]."}}
    review = client.post(
        url(project_id, f"search-strategies/{pubmed['id']}/press-reviews"),
        json={"answers": needs_work},
        headers=methodologist,
    )
    assert review.json()["overall"] == "revisions_required"
    for strategy in (pubmed, embase):
        client.post(
            url(project_id, f"search-strategies/{strategy['id']}/press-reviews"),
            json={"answers": APPROVE_ALL},
            headers=methodologist,
        )
    assert PRESS_REQUIREMENT not in unmet_search(client, project_id, owner)

    client.patch(
        url(project_id, f"search-strategies/{pubmed['id']}"),
        json={"query": "aspirin[mh]", "note": "Added the MeSH heading."},
        headers=owner,
    )
    status = client.get(url(project_id, "press-status"), headers=owner).json()
    assert [s["status"] for s in status["strategies"]] == ["outdated", "approved"]
    assert PRESS_REQUIREMENT in unmet_search(client, project_id, owner)

    waiver = client.post(
        url(project_id, "press-waiver"),
        json={"reason": "No second information specialist is available."},
        headers=owner,
    )
    assert waiver.status_code == 201
    assert PRESS_REQUIREMENT not in unmet_search(client, project_id, owner)
