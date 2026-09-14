"""Background AI jobs: progress, audit, refusal rules, and checks made again when a job runs."""

import asyncio

import pytest
from sqlalchemy import select
from workflow_helpers import create_project, open_screening, url

import ai_tasks
import jobs
import models
from database import SessionLocal

REAL_ENQUEUE = jobs.enqueue_job
SCREENING_REPLY = '{"decision": "Include", "reasoning": "Adults in a trial", "supporting_quote": null}'


@pytest.fixture
def project(client, auth_headers):
    return create_project(client, auth_headers), auth_headers


@pytest.fixture
def held_jobs(monkeypatch):
    """Queue jobs without running them. Returns the queued job ids."""
    queued: list[int] = []

    async def hold(job_id: int) -> None:
        queued.append(job_id)

    monkeypatch.setattr(jobs, "enqueue_job", hold)
    return queued


def get_job(client, project_id, headers, job_id):
    response = client.get(url(project_id, f"jobs/{job_id}"), headers=headers)
    assert response.status_code == 200, response.text
    return response.json()


def screening_body(records):
    return {"record_ids": [record["id"] for record in records]}


def test_a_job_reports_progress_and_is_audited(client, project, fake_provider):
    project_id, headers = project
    records = open_screening(client, project_id, headers, fake_provider)
    fake_provider(SCREENING_REPLY)

    response = client.post(url(project_id, "screening/ai"), json=screening_body(records), headers=headers)

    assert response.status_code == 202
    job = get_job(client, project_id, headers, response.json()["id"])
    assert (job["task"], job["status"], job["total"], job["processed"], job["failed"]) == (
        "screening",
        "completed",
        2,
        2,
        0,
    )
    assert job["finished_at"] is not None
    actions = [event["action"] for event in client.get(url(project_id, "audit"), headers=headers).json()["events"]]
    assert actions[:2] == ["ai.screening", "ai_job.queued"]
    assert client.get(url(project_id, "jobs"), headers=headers).json()[0]["id"] == job["id"]


def test_failed_records_are_counted_without_failing_the_job(client, project, fake_provider):
    project_id, headers = project
    records = open_screening(client, project_id, headers, fake_provider)
    fake_provider('{"decision": "Probably"}')

    job = client.post(url(project_id, "screening/ai"), json=screening_body(records), headers=headers).json()

    assert (job["status"], job["processed"], job["failed"]) == ("completed", 2, 2)


def test_a_project_runs_one_job_per_task_at_a_time(client, project, fake_provider, held_jobs):
    project_id, headers = project
    records = open_screening(client, project_id, headers, fake_provider)
    fake_provider(SCREENING_REPLY)

    first = client.post(url(project_id, "screening/ai"), json=screening_body(records), headers=headers)
    second = client.post(url(project_id, "screening/ai"), json=screening_body(records), headers=headers)

    assert first.json()["status"] == "queued"
    assert second.status_code == 409
    assert client.post(url(project_id, "embeddings"), headers=headers).status_code == 202


def test_jobs_fail_clearly_when_the_queue_is_unavailable(client, project, fake_provider, monkeypatch):
    project_id, headers = project
    records = open_screening(client, project_id, headers, fake_provider)
    fake_provider(SCREENING_REPLY)

    async def unavailable(job_id: int) -> None:
        raise jobs.JobQueueUnavailable("Background AI jobs need Redis.")

    monkeypatch.setattr(jobs, "enqueue_job", unavailable)
    response = client.post(url(project_id, "screening/ai"), json=screening_body(records), headers=headers)

    assert response.status_code == 503
    assert response.json()["detail"] == "Background AI jobs need Redis."
    stored = client.get(url(project_id, "jobs"), headers=headers).json()[0]
    assert (stored["status"], stored["error"]) == ("failed", "Background AI jobs need Redis.")


def test_queueing_needs_redis_to_be_configured(monkeypatch):
    monkeypatch.setenv("REDIS_URL", "")

    with pytest.raises(jobs.JobQueueUnavailable):
        asyncio.run(REAL_ENQUEUE(1))


def test_jobs_that_cannot_run_are_refused_before_queueing(client, project, fake_provider):
    project_id, headers = project
    open_screening(client, project_id, headers, fake_provider)

    response = client.post(url(project_id, "screening/ai"), json={"record_ids": [999_999]}, headers=headers)

    assert response.status_code == 404
    assert client.get(url(project_id, "jobs"), headers=headers).json() == []


def test_a_job_checks_permission_again_when_it_runs(client, project, fake_provider, make_user, held_jobs):
    project_id, owner = project
    records = open_screening(client, project_id, owner, fake_provider)
    email, screener = make_user()
    client.post(url(project_id, "members"), json={"email": email, "role": "screener"}, headers=owner)
    fake_provider(SCREENING_REPLY)
    assert (
        client.post(url(project_id, "screening/ai"), json=screening_body(records), headers=screener).status_code == 202
    )
    with SessionLocal() as db:
        user_id = db.scalar(select(models.User.id).where(models.User.email == email))
    client.delete(url(project_id, f"members/{user_id}"), headers=owner)

    assert asyncio.run(ai_tasks.run_job(held_jobs[0])) == "failed"
    job = get_job(client, project_id, owner, held_jobs[0])
    assert "no longer has permission" in job["error"]
    assert all(
        record["ai_screening"] is None for record in client.get(url(project_id, "records"), headers=owner).json()
    )


def test_finished_jobs_are_not_run_again(client, project, fake_provider):
    project_id, headers = project
    records = open_screening(client, project_id, headers, fake_provider)
    calls = fake_provider(SCREENING_REPLY)
    job = client.post(url(project_id, "screening/ai"), json=screening_body(records), headers=headers).json()
    calls.clear()

    assert asyncio.run(ai_tasks.run_job(job["id"])) == "completed"
    assert calls == []


def test_other_projects_jobs_are_hidden(client, make_user, fake_provider):
    _, alice = make_user()
    _, bob = make_user()
    alice_project = create_project(client, alice, "A")
    bob_project = create_project(client, bob, "B")
    records = open_screening(client, alice_project, alice, fake_provider)
    fake_provider(SCREENING_REPLY)
    job = client.post(url(alice_project, "screening/ai"), json=screening_body(records), headers=alice).json()

    assert client.get(url(bob_project, f"jobs/{job['id']}"), headers=bob).status_code == 404
