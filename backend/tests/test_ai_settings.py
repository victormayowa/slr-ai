"""The model catalog, per-project model pinning, and users' own API keys."""

import json

from sqlalchemy import select
from workflow_helpers import GENERATED_PROTOCOL, PROTOCOL, ProviderHTTPError, add_member, create_project, url

import models
from database import SessionLocal
from llm import adapters

ALL_PROVIDERS = {"anthropic", "gemini", "openai", "qwen", "kimi", "deepseek", "glm", "mistral"}


def catalog_id(client, headers, provider, model_id):
    catalog = client.get("/api/ai/models", headers=headers).json()
    return next(model["id"] for model in catalog if (model["provider"], model["model_id"]) == (provider, model_id))


def pin(client, headers, project_id, provider, model_id):
    body = {"ai_model_id": catalog_id(client, headers, provider, model_id)}
    response = client.put(f"/api/projects/{project_id}/ai-model", json=body, headers=headers)
    assert response.status_code == 200, response.text
    return response.json()


def generate(client, headers, project_id):
    client.put(url(project_id, "protocol"), json=PROTOCOL, headers=headers)
    return client.post(url(project_id, "protocol/generate"), headers=headers)


def latest_run(project_id):
    with SessionLocal() as db:
        return db.scalar(
            select(models.AIRun).where(models.AIRun.project_id == project_id).order_by(models.AIRun.id.desc())
        )


def test_catalog_offers_every_provider_with_a_data_location(client, auth_headers):
    catalog = client.get("/api/ai/models", headers=auth_headers).json()
    providers = client.get("/api/ai/providers", headers=auth_headers).json()

    assert {model["provider"] for model in catalog} == ALL_PROVIDERS
    assert [model["model_id"] for model in catalog if model["is_default"]] == ["gemini-3.8-flash"]
    assert not any(model["available"] for model in catalog), "no server or user keys are configured in tests"
    assert {provider["id"] for provider in providers} == ALL_PROVIDERS
    assert all(provider["data_location"] for provider in providers)


def test_new_projects_start_with_the_default_model(client, auth_headers):
    project_id = create_project(client, auth_headers)

    project = client.get(f"/api/projects/{project_id}", headers=auth_headers).json()

    assert (project["ai_model"]["provider"], project["ai_model"]["model_id"]) == ("gemini", "gemini-3.8-flash")


def test_pinned_model_is_audited_and_used_for_ai_tasks(client, auth_headers, fake_provider):
    project_id = create_project(client, auth_headers)

    project = pin(client, auth_headers, project_id, "deepseek", "deepseek-v4-flash")

    assert project["ai_model"]["model_id"] == "deepseek-v4-flash"
    event = client.get(url(project_id, "audit"), headers=auth_headers).json()["events"][0]
    assert event["action"] == "project.ai_model_changed"
    assert event["details"] == {"from": "gemini/gemini-3.8-flash", "to": "deepseek/deepseek-v4-flash"}

    calls = fake_provider(GENERATED_PROTOCOL)
    assert generate(client, auth_headers, project_id).status_code == 200
    assert [(call["provider"], call["model"], call["api_key"]) for call in calls] == [
        ("deepseek", "deepseek-v4-flash", "test-deepseek-key")
    ]
    assert calls[0]["schema"]["properties"].keys() == {"inclusion_criteria", "exclusion_criteria", "boolean_searches"}


def test_only_roles_that_edit_the_project_can_change_its_model(client, auth_headers, make_user):
    project_id = create_project(client, auth_headers)
    screener = add_member(client, project_id, auth_headers, make_user, "screener")
    body = {"ai_model_id": catalog_id(client, auth_headers, "mistral", "mistral-large-latest")}

    assert client.put(f"/api/projects/{project_id}/ai-model", json=body, headers=screener).status_code == 403


def test_withdrawn_models_cannot_be_pinned_or_used(client, auth_headers, fake_provider):
    project_id = create_project(client, auth_headers)
    model_id = pin(client, auth_headers, project_id, "glm", "glm-5")["ai_model"]["id"]
    fake_provider(GENERATED_PROTOCOL)
    with SessionLocal() as db:
        db.get(models.AIModel, model_id).enabled = False
        db.commit()
    try:
        response = generate(client, auth_headers, project_id)
        assert response.status_code == 409
        assert "no longer offered" in response.json()["detail"]
        body = {"ai_model_id": model_id}
        assert client.put(f"/api/projects/{project_id}/ai-model", json=body, headers=auth_headers).status_code == 404
    finally:
        with SessionLocal() as db:
            db.get(models.AIModel, model_id).enabled = True
            db.commit()


def test_ai_tasks_without_any_key_explain_how_to_add_one(client, auth_headers):
    project_id = create_project(client, auth_headers)

    response = generate(client, auth_headers, project_id)

    assert response.status_code == 400
    assert "Settings" in response.json()["detail"]
    assert latest_run(project_id) is None, "nothing was sent to a provider, so there is no run to record"


def test_saved_keys_are_encrypted_hidden_and_used_only_for_their_owner(client, make_user, fake_provider):
    email, alice = make_user()
    secret = "sk-alice-secret-value-1234"

    saved = client.put("/api/me/api-keys/openai", json={"api_key": f"  {secret} "}, headers=alice)

    assert saved.status_code == 200
    assert saved.json()["last_four"] == "1234"
    listed = client.get("/api/me/api-keys", headers=alice).json()
    providers = client.get("/api/ai/providers", headers=alice).json()
    assert secret not in json.dumps(listed) + json.dumps(providers)
    assert [key["provider"] for key in listed] == ["openai"]
    with SessionLocal() as db:
        row = db.scalar(select(models.UserAPIKey).join(models.User).where(models.User.email == email))
        assert secret.encode() not in row.encrypted_key

    project_id = create_project(client, alice)
    pin(client, alice, project_id, "openai", "gpt-5.4-mini")
    calls = fake_provider(GENERATED_PROTOCOL)
    assert generate(client, alice, project_id).status_code == 200
    assert calls[-1]["api_key"] == secret
    assert latest_run(project_id).key_source == "user"

    lead = add_member(client, project_id, alice, make_user, "lead_reviewer")
    assert client.get("/api/me/api-keys", headers=lead).json() == []
    assert client.post(url(project_id, "protocol/generate"), headers=lead).status_code == 200
    assert calls[-1]["api_key"] == "test-openai-key"
    assert latest_run(project_id).key_source == "platform"


def test_deleted_keys_are_gone(client, auth_headers):
    client.put("/api/me/api-keys/mistral", json={"api_key": "mistral-key-abcdefgh"}, headers=auth_headers)

    assert client.delete("/api/me/api-keys/mistral", headers=auth_headers).status_code == 204
    assert client.get("/api/me/api-keys", headers=auth_headers).json() == []
    assert client.delete("/api/me/api-keys/mistral", headers=auth_headers).status_code == 404


def test_unknown_providers_and_malformed_keys_are_rejected(client, auth_headers):
    key = {"api_key": "abcdefghijkl"}
    assert client.put("/api/me/api-keys/bogus", json=key, headers=auth_headers).status_code == 404
    malformed = {"api_key": "has spaces in it"}
    assert client.put("/api/me/api-keys/openai", json=malformed, headers=auth_headers).status_code == 422


def test_saving_keys_needs_server_encryption(client, auth_headers, monkeypatch):
    monkeypatch.setenv("DATA_ENCRYPTION_KEY", "")

    response = client.put("/api/me/api-keys/kimi", json={"api_key": "sk-kimi-key-0001"}, headers=auth_headers)

    assert response.status_code == 503


def test_key_check_reports_accepted_and_rejected_keys(client, auth_headers, monkeypatch):
    client.put("/api/me/api-keys/anthropic", json={"api_key": "sk-ant-test-key-0001"}, headers=auth_headers)
    used_keys = []

    async def accept(spec, model, prompt, api_key, *, json_schema, max_tokens):
        used_keys.append(api_key)
        return adapters.ProviderReply("ready")

    monkeypatch.setattr(adapters, "call_provider", accept)
    accepted = client.post("/api/me/api-keys/anthropic/test", headers=auth_headers).json()

    assert accepted["valid"] is True
    assert accepted["key"]["last_verified_at"] is not None
    assert used_keys == ["sk-ant-test-key-0001"]

    async def reject(*args, **kwargs):
        raise ProviderHTTPError(401)

    monkeypatch.setattr(adapters, "call_provider", reject)
    rejected = client.post("/api/me/api-keys/anthropic/test", headers=auth_headers).json()

    assert rejected["valid"] is False
    assert "rejected the API key" in rejected["message"]
    assert "sk-secret-123" not in json.dumps(rejected)
