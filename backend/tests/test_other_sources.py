"""Citation searching and grey literature: provenance, automatic deduplication, stage rules, and PRISMA counts."""

import pytest
from sqlalchemy import select
from workflow_helpers import create_project, decide, import_records, lock_protocol, open_screening, url

import citation_chasing
import models
from database import SessionLocal


def work(openalex_id, title, doi="", referenced=()):
    return {
        "id": f"https://openalex.org/{openalex_id}",
        "doi": f"https://doi.org/{doi}" if doi else None,
        "referenced_works": [f"https://openalex.org/{ref}" for ref in referenced],
    }


def openalex_record(openalex_id, title, doi=""):
    return {
        "id": openalex_id,
        "title": title,
        "authors": "",
        "year": 2018,
        "venue": "",
        "doi": doi,
        "abstract": "",
        "identifiers": {"openalex": openalex_id},
        "url": "",
    }


@pytest.fixture
def project(client, auth_headers):
    return create_project(client, auth_headers), auth_headers


@pytest.fixture
def openalex(monkeypatch):
    """A tiny citation graph: W1 (with DOI 10.1/a) cites W2 and W3; W9 cites W1."""
    works = {"W1": work("W1", "Aspirin trial", "10.1/a", referenced=("W2", "W3"))}
    records = {
        "W2": openalex_record("W2", "Earlier aspirin trial"),
        "W3": {**openalex_record("W3", "Statin trial", doi="10.1/b"), "year": ""},
        "W9": openalex_record("W9", "Later aspirin review"),
    }

    def raw_works(filter_name, values, select):
        if filter_name == "doi":
            return [w for w in works.values() if (w["doi"] or "").endswith(tuple(values))]
        if filter_name == "openalex":
            return [works[v] for v in values if v in works]
        return []

    monkeypatch.setattr(citation_chasing, "openalex_raw_works", raw_works)
    monkeypatch.setattr(citation_chasing, "openalex_records", lambda ids: [records[i] for i in ids if i in records])
    monkeypatch.setattr(
        citation_chasing, "openalex_citing", lambda work_id, limit: ([records["W9"]] if work_id == "W1" else [], 1)
    )


def test_citation_searching_adds_new_records_with_provenance_and_marks_known_ones(
    client, project, fake_provider, openalex
):
    project_id, headers = project
    first, second = open_screening(client, project_id, headers, fake_provider)
    decide(client, project_id, headers, first["id"], "include")

    response = client.post(url(project_id, "citation-searches"), json={"direction": "both"}, headers=headers)

    assert response.status_code == 201, response.text
    result = response.json()
    assert (result["new_records"], result["already_in_project"], result["unresolved_seed_record_ids"]) == (2, 1, [])
    assert result["run"]["kind"] == "citation"
    assert result["run"]["filters"]["seed_record_ids"] == [first["id"]]
    records = {
        r["title"]: r for r in client.get(url(project_id, "records?include_duplicates=true"), headers=headers).json()
    }
    assert records["Statin trial"]["duplicate_of_id"] == second["id"], (
        "the reference with the same DOI was already found"
    )
    assert records["Earlier aspirin trial"]["final_decision"] is None, "new finds still need screening"
    with SessionLocal() as db:
        links = db.scalars(select(models.CitationLink).where(models.CitationLink.project_id == project_id)).all()
        assert sorted(link.direction for link in links) == ["backward", "backward", "forward"]
        assert {link.seed_record_id for link in links} == {first["id"]}
    prisma = client.get(url(project_id, "prisma"), headers=headers).json()
    assert prisma["other_methods"] == {"citation_searching": 3, "grey_literature_and_websites": 0}
    assert prisma["identified_from_other_methods"] == 3


def test_records_openalex_cannot_identify_are_reported(client, project, fake_provider, openalex):
    project_id, headers = project
    records = open_screening(client, project_id, headers, fake_provider)
    statin = records[1]

    result = client.post(
        url(project_id, "citation-searches"), json={"record_ids": [statin["id"]]}, headers=headers
    ).json()

    assert (result["new_records"], result["unresolved_seed_record_ids"]) == (0, [statin["id"]])


def test_other_methods_need_search_or_screening_to_be_open(client, project, fake_provider, openalex):
    project_id, headers = project
    entry = {
        "source_type": "thesis",
        "source_name": "ProQuest Dissertations",
        "url": "https://example.org/thesis/1",
        "accessed_on": "2026-09-10",
        "title": "Aspirin use in rural clinics",
    }

    assert client.post(url(project_id, "grey-literature"), json=entry, headers=headers).status_code == 409
    assert client.post(url(project_id, "citation-searches"), json={}, headers=headers).status_code == 409


def test_grey_literature_is_grouped_by_source_and_date_and_deduplicated(client, project, fake_provider):
    project_id, headers = project
    lock_protocol(client, project_id, headers, fake_provider)
    import_records(client, project_id, headers, [{"title": "Aspirin use in rural clinics", "year": "2021"}])
    entry = {
        "source_type": "thesis",
        "source_name": "ProQuest Dissertations",
        "url": "https://example.org/thesis/1",
        "accessed_on": "2026-09-10",
        "search_terms": "aspirin AND rural",
        "title": "Aspirin use in rural clinics",
        "year": "2021",
    }

    first = client.post(url(project_id, "grey-literature"), json=entry, headers=headers).json()
    second = client.post(
        url(project_id, "grey-literature"),
        json={**entry, "title": "Aspirin adherence in older adults", "url": "https://example.org/thesis/2"},
        headers=headers,
    ).json()

    assert first["run"]["id"] == second["run"]["id"]
    assert second["run"]["result_count"] == 2
    assert second["run"]["source"] == "ProQuest Dissertations (thesis or dissertation)"
    assert first["record"]["duplicate_of_id"] is not None
    assert second["record"]["url"] == "https://example.org/thesis/2"
    assert (
        client.post(url(project_id, "grey-literature"), json={**entry, "url": "ftp://x"}, headers=headers).status_code
        == 422
    )
    prisma = client.get(url(project_id, "prisma"), headers=headers).json()
    assert prisma["other_methods"]["grey_literature_and_websites"] == 2
