"""Risk of bias assessment, statistical synthesis in R, and certainty of evidence, through the API."""

import io
import json
import zipfile

import pytest
from workflow_helpers import (
    add_member,
    appraise,
    complete_assessment,
    complete_stage,
    create_project,
    decide,
    extract,
    open_screening,
    url,
    workflow,
)

import certainty_routes
from stats_engine import engine_status

requires_r = pytest.mark.skipif(
    not engine_status().available or bool(engine_status().missing()),
    reason="R or its packages aren't installed: run backend/scripts/setup_r_env.sh and set RSCRIPT_PATH",
)

TRIALS = [
    {
        "title": "Aspirin trial A",
        "doi": "10.1/a",
        "abstract": "Adults were randomized to aspirin or placebo. Allocation was concealed with sealed envelopes.",
    },
    {
        "title": "Aspirin trial B",
        "doi": "10.1/b",
        "abstract": "A randomized placebo-controlled trial of aspirin in adults.",
    },
]
# (events, total) for aspirin and placebo in each trial.
EVENTS = [((10, 100), (18, 100)), ((14, 150), (22, 148))]
OUTCOME = "Myocardial infarction"


@pytest.fixture
def project(client, auth_headers):
    return create_project(client, auth_headers), auth_headers


def open_appraisal(client, project_id, headers, fake_provider):
    """Include both trials, extract their design and arm-level event counts, and lock the extraction data set."""
    imported = open_screening(client, project_id, headers, fake_provider, records=TRIALS)
    for record in imported:
        decide(client, project_id, headers, record["id"], "include")
    complete_stage(client, project_id, headers, "screening")
    for record in imported:
        decide(client, project_id, headers, record["id"], "include", stage="full_text")
    complete_stage(client, project_id, headers, "full_text_screening")
    form = [
        {"name": "Study design", "field_type": "text", "required": True},
        {"name": OUTCOME, "section": "Results", "field_type": "dichotomous", "per_arm": True, "required": True},
    ]
    saved = client.put(
        url(project_id, "extraction/form"), json={"fields": form, "note": "Outcome data"}, headers=headers
    )
    assert saved.status_code == 200, saved.text
    fields = {field["name"]: field["id"] for field in saved.json()["fields"]}
    studies = sorted(
        client.get(url(project_id, "studies"), headers=headers).json(), key=lambda s: s["reports"][0]["record_id"]
    )
    arms = {}
    for study, counts in zip(studies, EVENTS, strict=True):
        body = {"arms": [{"label": "Aspirin"}, {"label": "Placebo"}]}
        response = client.put(url(project_id, f"studies/{study['id']}/arms"), json=body, headers=headers)
        assert response.status_code == 200, response.text
        aspirin, placebo = (arm["id"] for arm in response.json()["arms"])
        arms[study["id"]] = (aspirin, placebo)
        extract(
            client, project_id, headers, study["id"], fields["Study design"], {"text": "Randomized controlled trial"}
        )
        for arm_id, (events, total) in zip((aspirin, placebo), counts, strict=True):
            value = {"events": events, "total": total}
            extract(client, project_id, headers, study["id"], fields[OUTCOME], value, arm_id=arm_id)
    complete_stage(client, project_id, headers, "extraction")
    return studies, fields, arms


def unmet(client, project_id, headers, stage):
    return [r["label"] for r in workflow(client, project_id, headers)[stage]["requirements"] if not r["met"]]


def test_risk_of_bias_is_recommended_answered_suggested_and_signed_off(client, project, fake_provider):
    project_id, headers = project
    studies, _, _ = open_appraisal(client, project_id, headers, fake_provider)

    overview = client.get(url(project_id, "appraisal/overview"), headers=headers).json()
    assert [(o["design"], o["recommended_tools"][0]["tool"]) for o in overview] == [
        ("Randomized controlled trial", "rob2")
    ] * 2
    first = studies[0]["id"]
    unexplained = {"study_id": first, "tool": "nos_cohort"}
    assert client.post(url(project_id, "appraisal/assessments"), json=unexplained, headers=headers).status_code == 422
    body = {"study_id": first, "tool": "rob2", "outcome": OUTCOME}
    created = client.post(url(project_id, "appraisal/assessments"), json=body, headers=headers)
    assert created.status_code == 201, created.text
    assessment = created.json()
    assert assessment["selection_reason"].startswith("Randomized trials are assessed with RoB 2")
    path = url(project_id, f"appraisal/assessments/{assessment['id']}")
    early = client.put(
        f"{path}/domains/d1", json={"judgment": "low", "rationale": "Concealed allocation."}, headers=headers
    )
    assert early.status_code == 409

    fake_provider(
        json.dumps(
            {
                "answers": [
                    {
                        "question_id": "1.2",
                        "answer": "Y",
                        "rationale": "Envelopes.",
                        "quote": "Allocation was concealed",
                    },
                    {
                        "question_id": "1.1",
                        "answer": "Y",
                        "rationale": "Random.",
                        "quote": "A sentence not in the paper",
                    },
                ],
                "domains": [{"domain": "d1", "judgment": "low", "rationale": "Adequate randomization."}],
            }
        )
    )
    job = client.post(url(project_id, "appraisal/ai"), json={"assessment_ids": [assessment["id"]]}, headers=headers)
    assert job.status_code == 202, job.text
    assert job.json()["status"] == "completed", job.json()
    suggested = client.get(path, headers=headers).json()
    by_question = {s["question_id"]: s for s in suggested["ai_suggestions"] if s["question_id"]}
    assert (by_question["1.2"]["grounded"], by_question["1.1"]["grounded"]) == (True, False)
    assert suggested["answers"] == {}

    signed = complete_assessment(client, project_id, headers, suggested)
    assert signed["status"] == "signed_off"
    assert signed["domain_judgments"]["d1"]["algorithm_judgment"] == "low"
    changed = client.put(f"{path}/answers", json={"answers": [{"question_id": "1.2", "answer": "N"}]}, headers=headers)
    changed_assessment = changed.json()
    assert changed_assessment["status"] == "in_progress"
    assert "d1" not in changed_assessment["domain_judgments"] and "d2" in changed_assessment["domain_judgments"]
    assert changed_assessment["suggested"]["domains"]["d1"] == "high"
    assert unmet(client, project_id, headers, "appraisal") == [
        "Every included study has a risk of bias or quality assessment",
        "Every assessment signed off, with each domain judged and justified",
    ]

    checklist = client.post(
        url(project_id, "reporting/assessments"),
        json={"study_id": studies[1]["id"], "checklist": "consort"},
        headers=headers,
    )
    assert checklist.status_code == 201, checklist.text
    checklist_path = url(project_id, f"reporting/assessments/{checklist.json()['id']}")
    assert client.post(f"{checklist_path}/sign-off", headers=headers).status_code == 409
    items = [{"item_id": item["item_id"], "status": "reported"} for item in checklist.json()["items"]]
    assert client.put(f"{checklist_path}/items", json={"items": items}, headers=headers).status_code == 200
    assert client.post(f"{checklist_path}/sign-off", headers=headers).json()["status"] == "signed_off"


@requires_r
def test_meta_analysis_grade_and_interpretation_complete_the_review(
    client, project, make_user, fake_provider, monkeypatch
):
    project_id, headers = project
    studies, fields, arms = open_appraisal(client, project_id, headers, fake_provider)
    for study in studies:
        appraise(client, project_id, headers, study["id"])
    complete_stage(client, project_id, headers, "appraisal")
    plot = client.get(url(project_id, "appraisal/plots/rob2?kind=summary"), headers=headers)
    assert plot.status_code == 200, plot.text
    assert b"<svg" in plot.content[:500]

    # Synthesis: pre-specified analyses name their plan item; the statistician approves the model.
    pairs = [{"study_id": sid, "treatment_arm_id": a, "control_arm_id": p} for sid, (a, p) in arms.items()]
    spec = {"field_id": fields[OUTCOME], "measure": "RR", "arms": pairs, "publication_bias": {"funnel": True}}
    body = {
        "title": "Aspirin versus placebo",
        "outcome": OUTCOME,
        "analysis_type": "pairwise",
        "spec": {**spec, "plot_formats": ["svg"]},
        "prespecified": True,
        "plan_reference": OUTCOME,
    }
    analyses = url(project_id, "analyses")
    assert client.post(analyses, json={**body, "plan_reference": "Stroke"}, headers=headers).status_code == 422
    assert client.post(analyses, json={**body, "prespecified": False}, headers=headers).status_code == 422
    analysis = client.post(analyses, json=body, headers=headers).json()
    analysis_path = url(project_id, f"analyses/{analysis['id']}")
    dataset = client.get(f"{analysis_path}/dataset", headers=headers).json()
    assert (dataset["source"], dataset["pooling"]["recommendation"]) == ("locked", "meta_analysis")
    assert {(r["ai"], r["n1i"], r["ci"], r["n2i"]) for r in dataset["rows"]} == {(10, 100, 18, 100), (14, 150, 22, 148)}

    exploratory = client.post(f"{analysis_path}/runs", headers=headers)
    assert exploratory.status_code == 202, exploratory.text
    run = exploratory.json()["run"]
    assert (exploratory.json()["job"]["status"], run["status"], run["is_final"]) == ("completed", "succeeded", False)
    assert run["results"]["summary"]["k"] == 2
    extractor = add_member(client, project_id, headers, make_user, "extractor")
    statistician = add_member(client, project_id, headers, make_user, "statistician")
    approval = {"note": "REML with Hartung-Knapp suits two trials."}
    assert client.post(f"{analysis_path}/approve", json=approval, headers=extractor).status_code == 403
    assert client.post(f"{analysis_path}/approve", json=approval, headers=statistician).json()["status"] == "approved"
    final = client.post(f"{analysis_path}/runs", headers=statistician).json()["run"]
    assert (final["status"], final["is_final"]) == ("succeeded", True), final["error"]
    forest = client.get(url(project_id, f"analysis-runs/{final['id']}/plots/forest.svg"), headers=headers)
    assert forest.status_code == 200 and b"<svg" in forest.content[:500]
    export = client.get(url(project_id, f"analysis-runs/{final['id']}/export"), headers=headers)
    exported = set(zipfile.ZipFile(io.BytesIO(export.content)).namelist())
    assert {"analysis.R", "spec.json", "data.csv", "results.json", "analysis.do", "analysis_pymare.py"} <= exported
    assert {"README.md", "plots/forest.svg"} <= exported

    revised = client.put(
        analysis_path, json={**body, "spec": {**body["spec"], "tau_method": "PM"}}, headers=statistician
    )
    assert (revised.json()["status"], revised.json()["final_run_id"]) == ("draft", None)
    assert "Every analysis approved by a statistician" in unmet(client, project_id, headers, "synthesis")
    client.post(f"{analysis_path}/approve", json=approval, headers=statistician)
    final = client.post(f"{analysis_path}/runs", headers=statistician).json()["run"]
    assert unmet(client, project_id, headers, "synthesis") == []
    complete_stage(client, project_id, headers, "synthesis")

    # GRADE: suggested ratings from the analysis; every domain rated, departures justified, then signed off.
    outcomes = client.get(url(project_id, "certainty/outcomes"), headers=headers).json()
    assert [(o["outcome"], o["final_run_id"], o["grade_assessment_id"]) for o in outcomes] == [
        (OUTCOME, final["id"], None)
    ]
    grade_body = {
        "outcome": OUTCOME,
        "analysis_id": analysis["id"],
        "baseline_risks": [{"label": "Moderate risk", "risk": 0.15}],
    }
    grade = client.post(url(project_id, "grade"), json=grade_body, headers=headers).json()
    assert {"risk_of_bias", "inconsistency", "imprecision", "publication_bias"} <= set(grade["suggestions"])
    sof = grade["summary_of_findings"]
    assert sof["relative_effect"].startswith("RR ") and sof["final"] is True
    assert sof["absolute_effects"][0]["baseline_per_1000"] == 150
    grade_path = url(project_id, f"grade/{grade['id']}")
    note = {"note": "Reviewed against the GRADE handbook."}
    assert client.post(f"{grade_path}/sign-off", json=note, headers=headers).status_code == 409
    no_reason = {**grade_body, "domains": {"imprecision": {"rating": -1}}}
    assert client.put(grade_path, json=no_reason, headers=headers).status_code == 422
    ratings = {key: {"rating": 0} for key in ("risk_of_bias", "inconsistency", "indirectness", "publication_bias")}
    ratings["imprecision"] = {"rating": -1, "rationale": "Two trials with few events and a wide interval."}
    rated = client.put(grade_path, json={**grade_body, "domains": ratings}, headers=headers).json()
    assert rated["certainty"] == "moderate"
    assert client.post(f"{grade_path}/sign-off", json=note, headers=headers).json()["status"] == "signed_off"
    docx = client.get(url(project_id, "summary-of-findings?format=docx"), headers=headers)
    assert docx.status_code == 200 and docx.content[:2] == b"PK"
    table = client.get(url(project_id, "summary-of-findings?format=csv"), headers=headers).text
    assert OUTCOME in table and "Moderate" in table

    # Prior reviews: overlap with the included studies and conclusions compared.
    def works(filter_name, values, select):
        return [
            {
                "id": "https://openalex.org/W900",
                "publication_year": 2016,
                "referenced_works": ["https://openalex.org/W1"],
            }
        ]

    monkeypatch.setattr(certainty_routes, "openalex_raw_works", works)
    monkeypatch.setattr(
        certainty_routes,
        "resolve_openalex_ids",
        lambda records: {r.id: f"W{i}" for i, r in enumerate(sorted(records, key=lambda r: r.id), start=1)},
    )
    review = {"title": "Aspirin for prevention: a review", "doi": "10.1/review", "outcome": OUTCOME}
    prior = client.post(
        url(project_id, "prior-reviews"),
        json={**review, "conclusion_direction": "favours_intervention"},
        headers=headers,
    ).json()
    assert (prior["openalex_id"], prior["references"], prior["year"]) == ("W900", 1, "2016")
    comparison = client.get(url(project_id, "prior-reviews/comparison"), headers=headers).json()
    assert sorted(row["cited_by"] for row in comparison["matrix"]) == [[False], [True]]
    assert comparison["corrected_covered_area"] == pytest.approx(0.5)
    assert [c["prior"] for c in comparison["conclusions"]] == ["favours_intervention"]

    # Interpretation: rules-based text, and an AI plain-language summary whose numbers are checked.
    statements = client.post(
        url(project_id, "interpretation/rules"), json={"kind": "informative_statement"}, headers=headers
    )
    assert statements.json()[0]["content"].startswith("Aspirin likely results in")
    limitations = client.post(url(project_id, "interpretation/rules"), json={"kind": "limitations"}, headers=headers)
    assert "serious imprecision" in limitations.json()[0]["content"]
    fake_provider(
        json.dumps({"summary": "Aspirin probably makes little difference to heart attacks (2 trials, 99 people)."})
    )
    summary = client.post(url(project_id, "interpretation/plain-language"), headers=headers)
    assert summary.status_code == 201, summary.text
    text = summary.json()
    assert (text["generated_by"], text["unverified_numbers"]) == ("ai", ["99"])
    clinician = add_member(client, project_id, headers, make_user, "clinical_expert")
    text_path = url(project_id, f"interpretation/{text['id']}")
    assert client.post(f"{text_path}/approve", headers=extractor).status_code == 403
    assert client.post(f"{text_path}/approve", headers=clinician).status_code == 409
    corrected = {"content": "Aspirin probably makes little difference to heart attacks (2 trials)."}
    assert client.put(text_path, json=corrected, headers=headers).json()["unverified_numbers"] == []
    assert "Every interpretive text approved by a clinical expert" in unmet(client, project_id, headers, "certainty")
    for item in client.get(url(project_id, "interpretation"), headers=headers).json():
        approved = client.post(url(project_id, f"interpretation/{item['id']}/approve"), headers=clinician)
        assert approved.status_code == 200, approved.text

    stages = complete_stage(client, project_id, headers, "certainty")
    assert stages["certainty"]["status"] == "completed"
