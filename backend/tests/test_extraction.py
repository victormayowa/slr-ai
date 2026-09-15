"""Structured extraction: form templates, dual extraction with unit normalization and reconciliation, the locked
dataset and its exports, calculated and imputed values, author contacts, and AI as the second extractor."""

import io
import json
import zipfile

import pytest
from workflow_helpers import (
    ANALYSIS_PLAN,
    add_member,
    complete_stage,
    create_project,
    extract,
    form_fields,
    open_extraction,
    url,
)


@pytest.fixture
def project(client, auth_headers):
    return create_project(client, auth_headers), auth_headers


def set_extraction(client, project_id, headers, **changes):
    current = client.get(url(project_id, "review-settings"), headers=headers).json()
    body = {"screening": current["screening"], "extraction": {**current["extraction"], **changes}}
    response = client.put(url(project_id, "review-settings"), json=body, headers=headers)
    assert response.status_code == 200, response.text


def replace_form(client, project_id, headers, fields, note="Piloted the form and switched to typed fields"):
    response = client.put(url(project_id, "extraction/form"), json={"fields": fields, "note": note}, headers=headers)
    assert response.status_code == 200, response.text
    return {field["name"]: field for field in response.json()["fields"]}


def cells(client, project_id, headers, study_id):
    view = client.get(url(project_id, f"studies/{study_id}/extraction"), headers=headers).json()
    return {(cell["field_id"], cell["arm_id"]): cell for cell in view["cells"]}


def test_templates_add_typed_fields_and_the_form_is_validated(client, project):
    project_id, headers = project
    plan = {
        **ANALYSIS_PLAN,
        "outcomes": [
            {"name": "Myocardial infarction", "priority": "primary", "measure": "Risk ratio", "timepoint": "12 months"}
        ],
    }
    assert client.put(url(project_id, "analysis-plan"), json=plan, headers=headers).status_code == 200

    client.post(url(project_id, "extraction/form/templates"), json={"template": "intervention"}, headers=headers)
    response = client.post(url(project_id, "extraction/form/templates"), json={"template": "outcomes"}, headers=headers)

    fields = {field["name"]: field for field in response.json()["fields"]}
    randomized = fields["Participants randomized"]
    assert (randomized["field_type"], randomized["per_arm"], randomized["required"]) == ("integer", True, True)
    outcome = fields["Outcome: Myocardial infarction (12 months)"]
    assert (outcome["field_type"], outcome["per_arm"], outcome["outcome"]) == (
        "dichotomous",
        True,
        "Myocardial infarction",
    )
    bad = [{"name": "Colour", "field_type": "colour"}]
    assert client.put(url(project_id, "extraction/form"), json={"fields": bad}, headers=headers).status_code == 422
    one_option = [{"name": "Design", "field_type": "categorical", "options": ["RCT"]}]
    assert (
        client.put(url(project_id, "extraction/form"), json={"fields": one_option}, headers=headers).status_code == 422
    )


def test_dual_extraction_normalizes_units_reconciles_and_locks_the_dataset(client, project, make_user, fake_provider):
    project_id, owner = project
    open_extraction(client, project_id, owner, fake_provider)
    set_extraction(client, project_id, owner, mode="dual", numeric_relative_tolerance=0.01)
    assert client.put(url(project_id, "extraction/form"), json={"fields": []}, headers=owner).status_code == 422
    fields = replace_form(
        client,
        project_id,
        owner,
        [
            {
                "name": "Body weight",
                "section": "Participants",
                "field_type": "continuous",
                "per_arm": True,
                "required": True,
                "unit": "kg",
            },
            {"name": "Country", "field_type": "text", "required": True},
        ],
    )
    study = client.get(url(project_id, "studies"), headers=owner).json()[0]
    arms = client.put(
        url(project_id, f"studies/{study['id']}/arms"),
        json={"arms": [{"label": "Aspirin"}, {"label": "Placebo"}]},
        headers=owner,
    ).json()["arms"]
    aspirin, placebo = arms[0]["id"], arms[1]["id"]
    weight, country = fields["Body weight"]["id"], fields["Country"]["id"]
    extractor = add_member(client, project_id, owner, make_user, "extractor")

    first = extract(
        client, project_id, owner, study["id"], weight, {"mean": 70, "sd": 10, "n": 50}, arm_id=aspirin, unit="kg"
    )
    assert first["state"] == "awaiting_second_extractor"
    hidden = cells(client, project_id, extractor, study["id"])[(weight, aspirin)]
    assert (hidden["other_values"], hidden["values_hidden"]) == (None, True)
    second = extract(
        client,
        project_id,
        extractor,
        study["id"],
        weight,
        {"mean": 154.3, "sd": 22.05, "n": 50},
        arm_id=aspirin,
        unit="lb",
    )
    assert second["state"] == "agreed"
    assert second["my_value"]["flags"] == ["unit_converted"]
    assert second["my_value"]["value"]["mean"] == pytest.approx(69.99, abs=0.01)
    for headers in (owner, extractor):
        extract(client, project_id, headers, study["id"], weight, {"mean": 71, "sd": 9, "n": 50}, arm_id=placebo)
    extract(client, project_id, owner, study["id"], country, {"text": "United Kingdom"})
    extract(client, project_id, extractor, study["id"], country, {"text": "Ireland"})

    progress = client.get(url(project_id, "extraction/progress"), headers=owner).json()
    assert progress["totals"]["discrepancies"] == 1
    assert [d["field"] for d in progress["discrepancies"]] == ["Country"]
    blocked = client.post(url(project_id, "workflow/extraction/complete"), json={"note": "Done"}, headers=owner)
    assert blocked.status_code == 409
    assert "No unresolved discrepancies between extractors" in blocked.json()["detail"]

    country_cell = cells(client, project_id, owner, study["id"])[(country, None)]
    ireland = next(v for v in country_cell["other_values"] if v["display"] == "Ireland")
    reconcile = {"field_id": country, "from_value_id": ireland["id"], "rationale": "The methods section names Ireland."}
    assert (
        client.put(
            url(project_id, f"studies/{study['id']}/extraction/finals"), json=reconcile, headers=extractor
        ).status_code
        == 403
    )
    settled = client.put(
        url(project_id, f"studies/{study['id']}/extraction/finals"), json=reconcile, headers=owner
    ).json()
    assert (settled["state"], settled["final"]["display"]) == ("reconciled", "Ireland")
    history = client.get(
        url(project_id, f"studies/{study['id']}/extraction/history?field_id={country}"), headers=owner
    ).json()
    assert history[0]["kind"] == "final" and history[0]["reason"] == "The methods section names Ireland."

    complete_stage(client, project_id, owner, "extraction")

    exported = client.get(url(project_id, "extraction/export?format=csv"), headers=owner)
    assert exported.status_code == 200
    rows = exported.text.splitlines()
    assert rows[0].startswith("study_id,study,registry_ids,field_id,field")
    assert any(",Body weight," in row and ",Aspirin,mean," in row for row in rows)
    assert any(",Country," in row and "Ireland" in row and "reconciled" in row for row in rows)
    metadata = json.loads(client.get(url(project_id, "extraction/export?format=json"), headers=owner).content)[
        "metadata"
    ]
    assert len(metadata["sha256"]) == 64
    archive = zipfile.ZipFile(
        io.BytesIO(client.get(url(project_id, "extraction/export?format=bundle"), headers=owner).content)
    )
    assert {"dataset.csv", "dataset.json", "dataset.xlsx", "load_dataset.R", "load_dataset.py", "README.txt"} <= set(
        archive.namelist()
    )


def test_calculated_and_imputed_values_and_author_contacts(client, project, make_user, fake_provider):
    project_id, owner = project
    open_extraction(client, project_id, owner, fake_provider)
    fields = replace_form(client, project_id, owner, [{"name": "Pain score", "field_type": "continuous"}])
    pain = fields["Pain score"]["id"]
    study = client.get(url(project_id, "studies"), headers=owner).json()[0]

    draft = client.get(
        url(project_id, f"studies/{study['id']}/author-contacts/draft?field_ids={pain}&contact_name=Dr Smith"),
        headers=owner,
    ).json()
    assert draft["missing_items"] == ["- Pain score"]
    assert draft["body"].startswith("Dear Dr Smith,")
    contact = client.post(
        url(project_id, f"studies/{study['id']}/author-contacts"),
        json={
            "contact_name": "Dr Smith",
            "email": "smith@example.org",
            "field_ids": [pain],
            "questions": draft["body"],
        },
        headers=owner,
    ).json()
    sent = client.post(
        url(project_id, f"author-contacts/{contact['id']}/messages"),
        json={"direction": "outgoing", "body": draft["body"]},
        headers=owner,
    ).json()
    assert sent["status"] == "sent"
    client.patch(
        url(project_id, f"author-contacts/{contact['id']}"), json={"reminder_due": "2000-01-01"}, headers=owner
    )
    assert client.get(url(project_id, "author-contacts"), headers=owner).json()[0]["reminder_overdue"] is True
    replied = client.post(
        url(project_id, f"author-contacts/{contact['id']}/messages"),
        json={"direction": "incoming", "body": "Median 5, IQR 3 to 7."},
        headers=owner,
    ).json()
    assert replied["status"] == "replied"

    conversion = client.post(
        url(project_id, "extraction/convert"),
        json={"method": "mean_sd_from_median_iqr", "inputs": {"median": 5, "q1": 3, "q3": 7, "n": 40}},
        headers=owner,
    ).json()
    assert conversion["values"]["mean"] == pytest.approx(5)
    no_derivation = {"field_id": pain, "value": {"mean": 5, "n": 40}, "source": "calculated"}
    assert (
        client.put(
            url(project_id, f"studies/{study['id']}/extraction/values"), json=no_derivation, headers=owner
        ).status_code
        == 422
    )
    calculated = extract(
        client,
        project_id,
        owner,
        study["id"],
        pain,
        {"mean": conversion["values"]["mean"], "sd": conversion["values"]["sd"], "n": 40},
        source="calculated",
        derivation={"method": conversion["method"], "inputs": conversion["inputs"], "formula": conversion["formula"]},
    )
    assert (calculated["state"], calculated["final"]["flags"]) == ("final", ["calculated"])

    unexplained = {
        "field_id": pain,
        "value": {"mean": 5, "sd": 2, "n": 40},
        "source": "imputed",
        "derivation": {"method": "assumed_sd"},
    }
    assert (
        client.put(
            url(project_id, f"studies/{study['id']}/extraction/values"), json=unexplained, headers=owner
        ).status_code
        == 422
    )
    imputed = extract(
        client,
        project_id,
        owner,
        study["id"],
        pain,
        {"mean": 5, "sd": 2, "n": 40},
        source="imputed",
        derivation={"method": "assumed_sd", "inputs": {"source": "similar trial"}},
        reason="The authors confirmed the median only; SD imputed from a similar trial.",
    )
    assert imputed["my_value"]["needs_approval"] is True
    blocked = client.post(url(project_id, "workflow/extraction/complete"), json={"note": "Done"}, headers=owner)
    assert "Every imputed value approved by a second reviewer" in blocked.json()["detail"]
    value_id = imputed["my_value"]["id"]
    assert client.post(url(project_id, f"extraction/values/{value_id}/approve"), headers=owner).status_code == 409
    approver = add_member(client, project_id, owner, make_user, "extractor")
    assert client.post(url(project_id, f"extraction/values/{value_id}/approve"), headers=approver).status_code == 200
    complete_stage(client, project_id, owner, "extraction")


def test_ai_as_the_second_extractor(client, project, fake_provider):
    project_id, headers = project
    open_extraction(client, project_id, headers, fake_provider)
    set_extraction(client, project_id, headers, mode="human_and_ai")
    fields = {name: field["id"] for name, field in form_fields(client, project_id, headers).items()}
    study = client.get(url(project_id, "studies"), headers=headers).json()[0]
    fake_provider(
        json.dumps(
            {
                "values": [
                    {"field_id": fields["Sample Size"], "value": "120", "quote": "Adults randomized to aspirin."},
                    {"field_id": fields["Mean Age"], "not_reported": True},
                    {"field_id": fields["Adverse Events"], "value": "Bleeding", "quote": "Invented quote."},
                ]
            }
        )
    )
    job = client.post(url(project_id, "extraction/ai"), json={"study_ids": [study["id"]]}, headers=headers).json()
    assert job["status"] == "completed", job

    agreed = extract(client, project_id, headers, study["id"], fields["Sample Size"], {"text": "120"})
    not_reported = extract(client, project_id, headers, study["id"], fields["Mean Age"], not_reported=True)
    disputed = extract(client, project_id, headers, study["id"], fields["Adverse Events"], {"text": "Bleeding"})
    waiting = cells(client, project_id, headers, study["id"])[(fields["Primary Outcome Result"], None)]

    assert (agreed["state"], agreed["final"]["source"]) == ("agreed", "agreed")
    assert not_reported["state"] == "agreed"
    assert (disputed["state"], disputed["final"]) == ("discrepancy", None)
    assert waiting["state"] == "empty"
