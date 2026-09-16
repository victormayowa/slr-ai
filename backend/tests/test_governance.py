"""AI governance: administrator-only catalog and benchmark routes, per-project calibration and the gate it can put in
front of AI screening, bias monitoring, and the quality reports."""

import pytest
from sqlalchemy import select
from workflow_helpers import RECORDS, complete_stage, create_project, decide, import_records, lock_protocol, url

import models
from database import SessionLocal

ELIGIBILITY = '{"decision": "Include", "reasoning": "Adults were randomized.", "confidence": 0.9}'
EXCLUSION = '{"decision": "Exclude", "reasoning": "Not a trial.", "confidence": 0.8}'


def make_admin(email: str) -> None:
    with SessionLocal() as db:
        user = db.scalar(select(models.User).where(models.User.email == email))
        assert user is not None
        user.is_platform_admin = True
        db.commit()


@pytest.fixture
def admin(client, make_user):
    email, headers = make_user()
    make_admin(email)
    return headers


# Distinct enough that deduplication finds no candidate pairs, so the search stage closes without review.
CALIBRATION_TOPICS = [
    ("Aspirin and myocardial infarction in older adults", "cardiology"),
    ("Statins for primary prevention of stroke", "neurology"),
    ("Metformin versus insulin in gestational diabetes", "endocrinology"),
    ("Cognitive behavioural therapy for chronic insomnia", "psychiatry"),
    ("Physiotherapy after anterior cruciate ligament repair", "orthopaedics"),
    ("Vitamin D supplements and childhood asthma", "paediatrics"),
    ("Antibiotic duration in uncomplicated pneumonia", "respiratory medicine"),
    ("Screening colonoscopy intervals and colorectal cancer", "gastroenterology"),
    ("Tranexamic acid in postpartum haemorrhage", "obstetrics"),
    ("Mindfulness programmes for workplace burnout", "occupational health"),
    ("Salt substitution and blood pressure in rural clinics", "public health"),
    ("Remote monitoring for chronic heart failure", "cardiology"),
]


def screened_project(client, headers, fake_provider, count=12):
    """A project whose reviewers have decided enough records for calibration."""
    project_id = create_project(client, headers)
    lock_protocol(client, project_id, headers, fake_provider)
    records = [
        {
            "title": title,
            "abstract": f"A randomized trial in {field} enrolling adults, reported in {2014 + index}.",
            "doi": f"10.1000/omnireview.{index}",
            "year": str(2014 + index),
        }
        for index, (title, field) in enumerate(CALIBRATION_TOPICS[:count])
    ]
    imported = import_records(client, project_id, headers, records)
    complete_stage(client, project_id, headers, "search")
    for position, record in enumerate(imported):
        decide(client, project_id, headers, record["id"], "include" if position < 4 else "exclude")
    return project_id, imported


def test_administration_routes_refuse_ordinary_accounts(client, auth_headers):
    assert client.get("/api/admin/models", headers=auth_headers).status_code == 403
    assert client.get("/api/admin/benchmarks/runs", headers=auth_headers).status_code == 403


def test_administrator_sees_the_catalog_with_benchmark_status(client, admin):
    response = client.get("/api/admin/models", headers=admin)

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["models"] and body["models"][0]["benchmark_status"] == "unvalidated"
    assert "screening" in body["thresholds"] and "recall" in body["thresholds"]["screening"]
    assert body["validation_required"] is False


def test_marking_a_model_exempt_needs_a_reason(client, admin):
    model_id = client.get("/api/admin/models", headers=admin).json()["models"][0]["id"]

    refused = client.patch(f"/api/admin/models/{model_id}", json={"benchmark_status": "exempt"}, headers=admin)
    assert refused.status_code == 422

    accepted = client.patch(
        f"/api/admin/models/{model_id}",
        json={"benchmark_status": "exempt", "status_note": "Used only for the help assistant."},
        headers=admin,
    )
    assert accepted.status_code == 200
    assert accepted.json()["benchmark_status"] == "exempt"


def test_the_statistics_benchmark_dataset_is_offered(client, admin):
    response = client.get("/api/admin/benchmarks/datasets", headers=admin)

    assert response.status_code == 200, response.text
    keys = {dataset["key"]: dataset for dataset in response.json()["datasets"]}
    assert "statistics-reference" in keys
    assert keys["statistics-reference"]["task"] == "statistics"


def test_an_invalid_benchmark_dataset_is_refused(client, admin):
    response = client.post(
        "/api/admin/benchmarks/datasets",
        files={"file": ("bad.json", b'{"key": "bad", "task": "screening"}', "application/json")},
        headers=admin,
    )

    assert response.status_code == 422
    assert "name" in response.json()["detail"].lower()


def test_calibration_needs_enough_decided_records(client, auth_headers, fake_provider):
    project_id = create_project(client, auth_headers)
    lock_protocol(client, project_id, auth_headers, fake_provider)
    import_records(client, project_id, auth_headers, RECORDS)

    response = client.post(url(project_id, "calibration"), json={"sample_size": 10}, headers=auth_headers)

    assert response.status_code == 409
    assert "already decided" in response.json()["detail"]


def test_calibration_compares_the_model_with_the_reviewers(client, auth_headers, fake_provider):
    project_id, _ = screened_project(client, auth_headers, fake_provider)
    fake_provider(ELIGIBILITY)

    response = client.post(url(project_id, "calibration"), json={"sample_size": 12, "seed": 7}, headers=auth_headers)

    assert response.status_code == 201, response.text
    report = response.json()
    assert report["status"] == "completed"
    metrics = report["metrics"]
    # The model said Include for everything, so it found every include but rejected none of the excludes.
    assert metrics["recall"] == 1.0
    assert metrics["specificity"] == 0.0
    assert report["passed"] is True
    assert len(report["items"]) == 12


def test_a_calibration_below_target_needs_a_reason_to_accept(client, auth_headers, fake_provider):
    project_id, _ = screened_project(client, auth_headers, fake_provider)
    fake_provider(EXCLUSION)
    report = client.post(url(project_id, "calibration"), json={"sample_size": 12}, headers=auth_headers).json()
    assert report["passed"] is False and report["metrics"]["recall"] == 0.0

    refused = client.post(
        url(project_id, f"calibration/{report['id']}/accept"), json={"note": ""}, headers=auth_headers
    )
    assert refused.status_code == 422

    accepted = client.post(
        url(project_id, f"calibration/{report['id']}/accept"),
        json={"note": "Accepted for a pilot; every record is still screened by a human."},
        headers=auth_headers,
    )
    assert accepted.status_code == 200
    assert accepted.json()["accepted_at"] is not None


def test_requiring_calibration_blocks_ai_screening_until_one_is_accepted(client, auth_headers, fake_provider):
    project_id, imported = screened_project(client, auth_headers, fake_provider)
    settings = client.get(url(project_id, "review-settings"), headers=auth_headers).json()
    settings["screening"]["require_ai_calibration"] = True
    saved = client.put(
        url(project_id, "review-settings"),
        json={"screening": settings["screening"], "extraction": settings["extraction"]},
        headers=auth_headers,
    )
    assert saved.status_code == 200, saved.text

    fake_provider(ELIGIBILITY)
    blocked = client.post(
        url(project_id, "screening/ai"), json={"record_ids": [imported[0]["id"]]}, headers=auth_headers
    )
    assert blocked.status_code == 400
    assert "calibration" in blocked.json()["detail"].lower()

    report = client.post(url(project_id, "calibration"), json={"sample_size": 12}, headers=auth_headers).json()
    client.post(url(project_id, f"calibration/{report['id']}/accept"), json={"note": ""}, headers=auth_headers)

    allowed = client.post(
        url(project_id, "screening/ai"), json={"record_ids": [imported[0]["id"]]}, headers=auth_headers
    )
    assert allowed.status_code == 202, allowed.text


def test_bias_report_compares_retrieved_and_included_records(client, auth_headers, fake_provider):
    project_id, _ = screened_project(client, auth_headers, fake_provider)

    response = client.get(url(project_id, "bias-report"), headers=auth_headers)

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["records"] == 12 and body["included"] == 4
    assert {group["field"] for group in body["groups"]} == {"year", "source", "venue"}


def test_quality_reports_describe_how_the_review_was_run(client, auth_headers, fake_provider):
    project_id, _ = screened_project(client, auth_headers, fake_provider)

    sop = client.get(url(project_id, "reports/sop"), headers=auth_headers).json()
    assert [stage["stage"] for stage in sop["stages"]][:2] == ["protocol", "search"]
    assert next(stage for stage in sop["stages"] if stage["stage"] == "protocol")["completed_by"] is not None

    provenance = client.get(url(project_id, "reports/provenance"), headers=auth_headers).json()
    assert provenance["audit"]["chain_valid"] is True
    assert provenance["searches"] and provenance["searches"][0]["results"] == 12

    validation = client.get(url(project_id, "reports/validation"), headers=auth_headers).json()
    assert validation["require_ai_calibration"] is False
    assert validation["model"] is None or "benchmark_status" in validation["model"]


def test_reproducing_a_run_needs_a_successful_analysis(client, auth_headers, fake_provider):
    project_id, _ = screened_project(client, auth_headers, fake_provider)

    response = client.post(url(project_id, "analysis-runs/1/reproduce"), headers=auth_headers)

    assert response.status_code == 404
    assert client.get(url(project_id, "reproducibility-checks"), headers=auth_headers).json() == []
