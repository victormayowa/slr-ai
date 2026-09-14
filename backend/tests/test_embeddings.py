"""Record embeddings, similar-record pairs, and marking duplicates by hand."""

import pytest
from sqlalchemy import select
from workflow_helpers import create_project, import_records, lock_protocol, open_screening, url

import models
from database import SessionLocal
from llm import adapters

SIMILAR = [
    {
        "title": "Low-dose aspirin for primary prevention in older adults",
        "abstract": "Adults aged 70 or older were randomized to aspirin 100 mg or placebo for five years.",
    },
    {
        "title": "Low dose aspirin for primary prevention in older adults: a randomised trial",
        "abstract": "Adults aged 70 or older were randomised to aspirin 100 mg or placebo for 5 years.",
    },
    {
        "title": "Statins and dementia risk",
        "abstract": "A cohort of 40,000 people followed for incident dementia after starting statins.",
    },
]


@pytest.fixture
def project(client, auth_headers):
    return create_project(client, auth_headers), auth_headers


def embed_project(client, project_id, headers, fake_provider):
    lock_protocol(client, project_id, headers, fake_provider)
    records = import_records(client, project_id, headers, SIMILAR)
    response = client.post(url(project_id, "embeddings"), headers=headers)
    assert response.status_code == 202, response.text
    return response.json(), records


def test_embeddings_use_the_projects_embedding_model_and_skip_unchanged_records(client, project, fake_provider):
    project_id, headers = project

    job, _ = embed_project(client, project_id, headers, fake_provider)

    assert (job["status"], job["total"], job["processed"], job["failed"]) == ("completed", 3, 3, 0)
    with SessionLocal() as db:
        rows = db.scalars(select(models.RecordEmbedding).where(models.RecordEmbedding.project_id == project_id)).all()
        assert {row.model for row in rows} == {"gemini/gemini-embedding-001"}
        assert [len(row.embedding) for row in rows] == [1024] * 3
        run = db.scalar(
            select(models.AIRun).where(models.AIRun.project_id == project_id, models.AIRun.task == "embedding")
        )
        assert (run.provider, run.model, run.status, run.key_source, run.input_tokens) == (
            "gemini",
            "gemini-embedding-001",
            "succeeded",
            "platform",
            30,
        )

    calls = fake_provider("")
    calls.clear()
    again = client.post(url(project_id, "embeddings"), headers=headers).json()

    assert (again["status"], again["processed"]) == ("completed", 3)
    assert calls == [], "unchanged records are not embedded again"


def test_similar_records_are_paired_for_review(client, project, fake_provider):
    project_id, headers = project
    embed_project(client, project_id, headers, fake_provider)

    result = client.get(url(project_id, "similar-pairs?min_similarity=0.8"), headers=headers).json()

    assert (result["model"], result["embedded_records"], result["unique_records"]) == (
        "gemini/gemini-embedding-001",
        3,
        3,
    )
    assert [(pair["record"]["title"], pair["other"]["title"]) for pair in result["pairs"]] == [
        (SIMILAR[0]["title"], SIMILAR[1]["title"])
    ]
    assert result["pairs"][0]["similarity"] >= 0.8


def test_embedding_failures_are_reported_on_the_job(client, project, fake_provider, monkeypatch):
    project_id, headers = project
    lock_protocol(client, project_id, headers, fake_provider)
    import_records(client, project_id, headers, SIMILAR)

    async def short_vectors(spec, model, texts, api_key, *, dimensions):
        return adapters.EmbeddingReply([[0.1] * 12 for _ in texts])

    monkeypatch.setattr(adapters, "embed_texts", short_vectors)
    job = client.post(url(project_id, "embeddings"), headers=headers).json()

    assert (job["status"], job["processed"], job["failed"]) == ("completed", 3, 3)
    assert "expected size 1024" in job["error"]


def test_reviewers_can_mark_and_clear_duplicates_by_hand(client, project, fake_provider):
    project_id, headers = project
    lock_protocol(client, project_id, headers, fake_provider)
    first, second, third = import_records(client, project_id, headers, SIMILAR)

    def mark(record, original):
        body = {"duplicate_of_id": original["id"] if original else None}
        return client.put(url(project_id, f"records/{record['id']}/duplicate-of"), json=body, headers=headers)

    marked = mark(second, first)

    assert marked.status_code == 200
    assert marked.json()["duplicate_of_id"] == first["id"]
    assert client.get(url(project_id, "prisma"), headers=headers).json()["duplicates_removed"] == 1
    event = client.get(url(project_id, "audit"), headers=headers).json()["events"][0]
    assert (event["action"], event["details"]) == (
        "record.duplicate_marked",
        {"duplicate_of_id": first["id"], "previous": None, "also_moved": []},
    )
    assert mark(third, second).status_code == 409, "a duplicate can't be the original of another record"
    assert mark(first, first).status_code == 422

    assert mark(first, third).status_code == 200
    everything = {
        r["id"]: r for r in client.get(url(project_id, "records?include_duplicates=true"), headers=headers).json()
    }
    assert everything[second["id"]]["duplicate_of_id"] == third["id"], "duplicates move with their original"

    cleared = mark(first, None)
    assert cleared.json()["duplicate_of_id"] is None


def test_duplicates_can_be_marked_only_while_search_is_open(client, project, fake_provider):
    project_id, headers = project
    first, second = open_screening(client, project_id, headers, fake_provider)

    body = {"duplicate_of_id": first["id"]}
    response = client.put(url(project_id, f"records/{second['id']}/duplicate-of"), json=body, headers=headers)

    assert response.status_code == 409


def test_chat_and_embedding_models_are_pinned_separately(client, auth_headers):
    project_id = create_project(client, auth_headers)
    project = client.get(f"/api/projects/{project_id}", headers=auth_headers).json()
    embedding_models = client.get("/api/ai/models?purpose=embedding", headers=auth_headers).json()
    chat_model = client.get("/api/ai/models", headers=auth_headers).json()[0]
    mistral = next(model for model in embedding_models if model["provider"] == "mistral")

    assert project["embedding_model"]["model_id"] == "gemini-embedding-001"
    assert {model["provider"] for model in embedding_models} == {"gemini", "openai", "qwen", "glm", "mistral"}
    wrong_chat = client.put(
        f"/api/projects/{project_id}/ai-model", json={"ai_model_id": mistral["id"]}, headers=auth_headers
    )
    assert wrong_chat.status_code == 404
    wrong_embedding = client.put(
        f"/api/projects/{project_id}/embedding-model", json={"ai_model_id": chat_model["id"]}, headers=auth_headers
    )
    assert wrong_embedding.status_code == 404

    pinned = client.put(
        f"/api/projects/{project_id}/embedding-model", json={"ai_model_id": mistral["id"]}, headers=auth_headers
    )

    assert pinned.json()["embedding_model"]["model_id"] == "mistral-embed"
    event = client.get(url(project_id, "audit"), headers=auth_headers).json()["events"][0]
    assert (event["action"], event["details"]["to"]) == ("project.embedding_model_changed", "mistral/mistral-embed")
