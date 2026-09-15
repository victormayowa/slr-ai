"""Topic exploration: source counts, existing reviews, outdated-review flags, feasibility, and gap-based questions."""

import json
from datetime import UTC, datetime

import pytest
from workflow_helpers import PROTOCOL, add_member, create_project, url

import topic_exploration
from services.errors import SearchError
from topic_exploration import WorkloadAssumptions, estimate_workload, meta_analysis_feasibility

THIS_YEAR = datetime.now(UTC).year
OLD_REVIEW = {
    "id": "111",
    "source": "PubMed",
    "title": "Aspirin for primary prevention: a systematic review",
    "year": "2016",
    "venue": "BMJ",
    "doi": "10.1/old",
    "url": "https://pubmed.ncbi.nlm.nih.gov/111/",
}
RECENT_REVIEW = {
    **OLD_REVIEW,
    "id": "222",
    "title": "Aspirin in older adults: a meta-analysis",
    "year": str(THIS_YEAR),
    "doi": "",
}


@pytest.fixture
def project(client, auth_headers):
    return create_project(client, auth_headers), auth_headers


@pytest.fixture
def sources(monkeypatch):
    """Stand-ins for the literature and registry APIs. Returns the PubMed terms searched."""
    terms: list[str] = []
    monkeypatch.setattr(topic_exploration, "PUBMED_PAUSE_SECONDS", 0)

    def count_pubmed(term):
        terms.append(term)
        if "randomized controlled trial[pt]" not in term:
            return 1200
        return 2 if "[dp]" in term else 3

    def clinical_trials(query):
        raise SearchError("ClinicalTrials.gov search failed. Please try again shortly.")

    monkeypatch.setattr(topic_exploration, "count_pubmed", count_pubmed)
    monkeypatch.setattr(topic_exploration, "find_pubmed_reviews", lambda query: [dict(OLD_REVIEW), dict(RECENT_REVIEW)])
    monkeypatch.setattr(
        topic_exploration, "openalex_counts_by_year", lambda query: (2500, {THIS_YEAR - 1: 100, THIS_YEAR: 40})
    )
    duplicate = {
        **OLD_REVIEW,
        "id": "W9",
        "source": "OpenAlex",
        "title": "Aspirin for Primary Prevention: A Systematic Review.",
    }
    monkeypatch.setattr(topic_exploration, "find_openalex_reviews", lambda query: [duplicate])
    monkeypatch.setattr(topic_exploration, "count_clinical_trials", clinical_trials)
    registration = {
        "id": "abc12",
        "title": "Aspirin review protocol",
        "registered": "2024-02-01",
        "url": "https://osf.io/abc12",
    }
    monkeypatch.setattr(topic_exploration, "search_osf_registrations", lambda query: [registration])
    return terms


def test_exploration_reports_every_source_and_flags_outdated_reviews(client, project, sources):
    project_id, headers = project

    response = client.post(
        url(project_id, "topic-explorations"), json={"query": "aspirin primary prevention"}, headers=headers
    )

    assert response.status_code == 201
    results = response.json()["results"]
    assert {key: (s["count"], s["error"]) for key, s in results["sources"].items()} == {
        "pubmed": (1200, None),
        "pubmed_randomized_trials": (3, None),
        "openalex": (2500, None),
        "clinicaltrials_gov": (None, "ClinicalTrials.gov search failed. Please try again shortly."),
    }
    reviews = results["existing_reviews"]
    assert [(r["ref"], r["possibly_outdated"], r["newer_randomized_trials"]) for r in reviews] == [
        ("PubMed:111", True, 2),
        ("PubMed:222", False, None),
    ], "the OpenAlex copy of the same review is merged away"
    assert f'("{2017}"[dp] : "3000"[dp])' in sources[-1]
    assert results["meta_analysis_feasibility"]["level"] == "possible"
    assert results["registrations"][0]["url"] == "https://osf.io/abc12"
    assert list(results["publications_by_year"])[-2:] == [str(THIS_YEAR - 1), str(THIS_YEAR)]
    assert results["workload"]["low"]["records"] == 2500
    assert results["workload"]["high"]["records"] == 3700

    listed = client.get(url(project_id, "topic-explorations"), headers=headers).json()
    assert [e["query"] for e in listed] == ["aspirin primary prevention"]
    assert (
        client.get(url(project_id, f"topic-explorations/{listed[0]['id']}"), headers=headers).json()["results"]
        == results
    )
    event = client.get(url(project_id, "audit"), headers=headers).json()["events"][0]
    assert (event["action"], event["details"]["sources_failed"]) == (
        "topic.explored",
        ["ClinicalTrials.gov registered studies"],
    )


def test_only_protocol_editors_can_explore(client, project, make_user, sources):
    project_id, owner = project
    viewer = add_member(client, project_id, owner, make_user, "viewer")

    assert (
        client.post(url(project_id, "topic-explorations"), json={"query": "aspirin"}, headers=viewer).status_code == 403
    )


def test_workload_estimates_follow_their_assumptions():
    assumptions = WorkloadAssumptions(
        reviewers=2,
        minutes_per_abstract=0.5,
        full_text_fraction=0.1,
        minutes_per_full_text=6,
        include_fraction=0.5,
        hours_per_included_study=1,
    )

    workload = estimate_workload([600, None, 400], assumptions)

    assert workload["low"] == {
        "records": 600,
        "full_texts": 60,
        "included_studies": 30,
        "screening_hours": 10.0,
        "full_text_hours": 12.0,
        "extraction_hours": 60.0,
        "total_hours": 82.0,
    }
    assert workload["high"]["records"] == 1000
    assert estimate_workload([None], assumptions) is None


@pytest.mark.parametrize(
    "trials,level", [(None, "unknown"), (0, "unlikely"), (1, "unlikely"), (3, "possible"), (8, "likely")]
)
def test_meta_analysis_feasibility_levels(trials, level):
    assert meta_analysis_feasibility(trials)["level"] == level


def test_ai_questions_cite_only_retrieved_reviews(client, project, sources, fake_provider):
    project_id, headers = project
    client.put(url(project_id, "protocol"), json=PROTOCOL, headers=headers)
    exploration = client.post(url(project_id, "topic-explorations"), json={"query": "aspirin"}, headers=headers).json()
    reply = {
        "questions": [
            {
                "question": "Does aspirin prevent cardiovascular events in adults over 70?",
                "framework": "PICO",
                "gap": "outdated_review",
                "rationale": "The 2016 review predates trials in older adults.",
                "based_on_review_ids": ["PubMed:111", "PubMed:999"],
            }
        ],
        "evidence_limitations": "Only PubMed and OpenAlex were searched.",
    }
    calls = fake_provider(json.dumps(reply))

    suggestion = client.post(
        url(project_id, f"topic-explorations/{exploration['id']}/ai-questions"), headers=headers
    ).json()

    assert suggestion["content"]["questions"][0]["based_on_review_ids"] == ["PubMed:111"]
    assert suggestion["content"]["exploration_id"] == exploration["id"]
    assert "never claim that no review exists anywhere" in calls[0]["prompt"]
    stored = client.get(url(project_id, f"topic-explorations/{exploration['id']}"), headers=headers).json()
    assert stored["ai_questions"]["id"] == suggestion["id"]
