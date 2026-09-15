"""Linking reports into studies, study arms, and entity linking in documents."""

import json

import pytest
from workflow_helpers import (
    complete_stage,
    create_project,
    decide,
    extract,
    form_fields,
    open_screening,
    url,
)

import entities_routes

LINKED_RECORDS = [
    {
        "title": "Aspirin trial: main results",
        "abstract": "Registered as NCT01234567. Adults were randomized.",
        "authors": "Smith J; Doe A",
        "year": "2019",
    },
    {
        "title": "Aspirin trial: ten-year follow-up",
        "abstract": "Long-term follow-up of NCT01234567.",
        "authors": "Smith J; Roe B",
        "year": "2021",
    },
    {"title": "Statin trial", "abstract": "Adults randomized to statins.", "authors": "Lee K", "year": "2018"},
]


@pytest.fixture
def project(client, auth_headers):
    return create_project(client, auth_headers), auth_headers


def open_extraction_with(client, project_id, headers, fake_provider, records):
    imported = open_screening(client, project_id, headers, fake_provider, records=records)
    for record in imported:
        decide(client, project_id, headers, record["id"], "include")
    complete_stage(client, project_id, headers, "screening")
    for record in imported:
        decide(client, project_id, headers, record["id"], "include", stage="full_text")
    complete_stage(client, project_id, headers, "full_text_screening")
    return imported


def test_reports_of_one_study_are_suggested_linked_and_split(client, project, fake_provider):
    project_id, headers = project
    main, follow_up, statin = open_extraction_with(client, project_id, headers, fake_provider, LINKED_RECORDS)

    studies = {s["reports"][0]["record_id"]: s for s in client.get(url(project_id, "studies"), headers=headers).json()}
    assert (studies[main["id"]]["label"], studies[main["id"]]["registry_ids"]) == ("Smith 2019", ["NCT01234567"])
    [candidate] = client.get(url(project_id, "studies/link-candidates"), headers=headers).json()
    assert {candidate["record"]["id"], candidate["other"]["id"]} == {main["id"], follow_up["id"]}
    assert candidate["reasons"] == ["Same trial registration: NCT01234567"]

    merged = client.post(
        url(project_id, f"studies/{studies[main['id']]['id']}/merge"),
        json={"other_study_id": studies[follow_up["id"]]["id"]},
        headers=headers,
    ).json()
    assert sorted(report["record_id"] for report in merged["reports"]) == sorted([main["id"], follow_up["id"]])
    assert len(client.get(url(project_id, "studies"), headers=headers).json()) == 2
    assert client.get(url(project_id, "studies/link-candidates"), headers=headers).json() == []

    split = client.post(
        url(project_id, f"studies/{merged['id']}/split"), json={"record_id": follow_up["id"]}, headers=headers
    ).json()
    assert len(split) == 3
    rejection = {"record_id": main["id"], "other_record_id": follow_up["id"]}
    assert client.post(url(project_id, "study-link-decisions"), json=rejection, headers=headers).status_code == 201
    assert client.get(url(project_id, "studies/link-candidates"), headers=headers).json() == []

    statin_study = next(s for s in split if s["reports"][0]["record_id"] == statin["id"])
    field = form_fields(client, project_id, headers)["Sample Size"]
    extract(client, project_id, headers, statin_study["id"], field["id"], {"text": "200"})
    blocked = client.post(
        url(project_id, f"studies/{merged['id']}/merge"), json={"other_study_id": statin_study["id"]}, headers=headers
    )
    assert blocked.status_code == 409


def test_arms_with_values_cannot_be_removed(client, project, fake_provider):
    project_id, headers = project
    open_extraction_with(client, project_id, headers, fake_provider, LINKED_RECORDS[2:])
    study = client.get(url(project_id, "studies"), headers=headers).json()[0]
    form = client.get(url(project_id, "extraction/form"), headers=headers).json()
    fields = [{**f, "id": f["id"]} for f in form["fields"]] + [
        {"name": "Participants randomized", "field_type": "integer", "per_arm": True, "section": "Arms"}
    ]
    saved = client.put(
        url(project_id, "extraction/form"),
        json={"fields": fields, "note": "Added arm-level sample sizes"},
        headers=headers,
    )
    assert saved.status_code == 200, saved.text
    arms = client.put(
        url(project_id, f"studies/{study['id']}/arms"),
        json={"arms": [{"label": "Statin"}, {"label": "Placebo"}]},
        headers=headers,
    ).json()["arms"]
    per_arm = form_fields(client, project_id, headers)["Participants randomized"]

    missing_arm = client.put(
        url(project_id, f"studies/{study['id']}/extraction/values"),
        json={"field_id": per_arm["id"], "value": {"number": 50}},
        headers=headers,
    )
    assert missing_arm.status_code == 422
    extract(client, project_id, headers, study["id"], per_arm["id"], {"number": 50}, arm_id=arms[0]["id"])

    removed = client.put(
        url(project_id, f"studies/{study['id']}/arms"),
        json={"arms": [{"label": "Placebo", "id": arms[1]["id"]}]},
        headers=headers,
    )
    assert removed.status_code == 409


def test_ai_entity_mentions_are_verified_looked_up_and_reviewed(client, project, fake_provider, monkeypatch):
    project_id, headers = project
    first, _ = open_screening(client, project_id, headers, fake_provider)
    decide(client, project_id, headers, first["id"], "include")
    text = b"Methods\nAdults with type 2 diabetes received metformin 500 mg daily.\n\nResults\nHbA1c fell by 0.5%."
    document = client.post(
        url(project_id, f"records/{first['id']}/documents"), files={"file": ("trial.txt", text)}, headers=headers
    ).json()
    spans = client.get(url(project_id, f"documents/{document['id']}"), headers=headers).json()["spans"]
    methods = next(span["id"] for span in spans if "metformin" in span["text"])
    lookups = []

    def fake_lookup(ontology, term, limit):
        lookups.append((ontology, term))
        return [{"ontology": ontology, "code": f"{ontology}:{term}", "label": term.title()}]

    monkeypatch.setattr(entities_routes, "lookup", fake_lookup)
    fake_provider(
        json.dumps(
            {
                "entities": [
                    {"text": "type 2 diabetes", "entity_type": "condition", "passage_id": methods},
                    {"text": "metformin", "entity_type": "drug", "passage_id": 999_999},
                    {"text": "insulin glargine", "entity_type": "drug", "passage_id": methods},
                ]
            }
        )
    )

    result = client.post(url(project_id, f"documents/{document['id']}/entities/suggest"), headers=headers)

    assert result.status_code == 200, result.text
    body = result.json()
    assert body["dropped_unverified"] == 1
    entities = {entity["text"]: entity for entity in body["entities"]}
    assert set(entities) == {"type 2 diabetes", "metformin"}
    assert entities["metformin"]["span_id"] == methods
    assert [c["ontology"] for c in entities["metformin"]["candidates"]] == ["rxnorm", "atc", "mesh"]
    assert (entities["metformin"]["code"], entities["metformin"]["status"]) == ("rxnorm:metformin", "suggested")
    assert ("icd11", "type 2 diabetes") not in lookups, "ICD-11 isn't queried without credentials"

    confirmed = client.patch(
        url(project_id, f"entities/{entities['metformin']['id']}"), json={"status": "confirmed"}, headers=headers
    )
    assert confirmed.json()["status"] == "confirmed"
    cleared = {"status": "confirmed", "code": "", "ontology": ""}
    assert (
        client.patch(
            url(project_id, f"entities/{entities['type 2 diabetes']['id']}"), json=cleared, headers=headers
        ).status_code
        == 422
    )
    manual = {"span_id": methods, "entity_type": "outcome", "text": "not in this passage"}
    assert (
        client.post(url(project_id, f"documents/{document['id']}/entities"), json=manual, headers=headers).status_code
        == 422
    )
