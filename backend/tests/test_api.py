import os
from datetime import UTC, datetime, timedelta

import jwt
import pytest

import main
from services.errors import SearchError

PAPER = {"id": "p1", "title": "Aspirin trial", "abstract": "A randomized trial of aspirin in adults."}

PROTECTED_ENDPOINTS = [
    ("/api/protocol/generate", {"research_question": "aspirin"}),
    ("/api/search", {"database": "PubMed", "query": "aspirin"}),
    ("/api/screen/abstract", {"papers": [PAPER], "criteria": "adults"}),
    ("/api/screen/fulltext", {"papers": [PAPER], "columns": ["Sample Size"]}),
    ("/api/screen/rob", {"papers": [PAPER], "tool": "ROB-2"}),
    ("/api/screen/meta", {"papers": [PAPER]}),
    ("/api/chat", {"query": "hi"}),
]


def bearer(secret, exp_delta=timedelta(hours=1)):
    token = jwt.encode({"sub": "someone@example.org", "exp": datetime.now(UTC) + exp_delta}, secret, algorithm="HS256")
    return {"Authorization": f"Bearer {token}"}


@pytest.mark.parametrize("path,body", PROTECTED_ENDPOINTS)
def test_endpoints_require_login(client, path, body):
    assert client.post(path, json=body).status_code == 401


def test_token_signed_with_old_default_secret_rejected(client):
    headers = bearer("super_secret_omnireview_key")

    assert client.post("/api/chat", json={"query": "hi"}, headers=headers).status_code == 401


def test_expired_token_rejected(client):
    headers = bearer(os.environ["JWT_SECRET_KEY"], exp_delta=timedelta(minutes=-1))

    assert client.post("/api/chat", json={"query": "hi"}, headers=headers).status_code == 401


def test_unconfigured_provider_returns_502_with_reason(client, auth_headers):
    response = client.post("/api/chat", json={"query": "hi", "provider": "openai"}, headers=auth_headers)

    assert response.status_code == 502
    assert "OPENAI_API_KEY" in response.json()["detail"]


def test_failed_screening_reports_error_without_inventing_a_decision(client, auth_headers):
    body = {"papers": [PAPER], "criteria": "adults", "provider": "openai"}

    result = client.post("/api/screen/abstract", json=body, headers=auth_headers).json()["results"][0]

    assert "ai_decision" not in result
    assert "OPENAI_API_KEY" in result["ai_error"]


def test_screening_success_records_decision_and_clears_stale_error(client, auth_headers, fake_provider):
    fake_provider('{"decision": "Include", "reasoning": "Adults in an RCT", "supporting_quote": "randomized"}')
    body = {"papers": [{**PAPER, "ai_error": "earlier failure"}], "criteria": "adults", "provider": "gemini"}

    result = client.post("/api/screen/abstract", json=body, headers=auth_headers).json()["results"][0]

    assert result["ai_decision"] == "Include"
    assert result["ai_reasoning"] == "Adults in an RCT"
    assert "ai_error" not in result


def test_unrecognised_model_decision_reported_as_error(client, auth_headers, fake_provider):
    fake_provider('{"decision": "Probably", "reasoning": "unsure"}')
    body = {"papers": [PAPER], "criteria": "adults", "provider": "gemini"}

    result = client.post("/api/screen/abstract", json=body, headers=auth_headers).json()["results"][0]

    assert "ai_decision" not in result
    assert "valid decision" in result["ai_error"]


def test_markdown_fenced_json_from_model_accepted(client, auth_headers, fake_provider):
    fake_provider('```json\n{"decision": "Exclude", "reasoning": "Children only"}\n```')
    body = {"papers": [PAPER], "criteria": "adults", "provider": "anthropic"}

    result = client.post("/api/screen/abstract", json=body, headers=auth_headers).json()["results"][0]

    assert result["ai_decision"] == "Exclude"


def test_protocol_response_missing_fields_returns_502(client, auth_headers, fake_provider):
    fake_provider('{"inclusion_criteria": ["Adults"]}')

    response = client.post("/api/protocol/generate", json={"research_question": "aspirin"}, headers=auth_headers)

    assert response.status_code == 502


def test_extraction_flags_columns_missing_from_model_response(client, auth_headers, fake_provider):
    fake_provider('{"Sample Size": 120}')
    body = {"papers": [PAPER], "columns": ["Sample Size", "Mean Age"], "provider": "gemini"}

    result = client.post("/api/screen/fulltext", json=body, headers=auth_headers).json()["results"][0]

    assert result["extracted_data"] == {"Sample Size": "120", "Mean Age": "Missing from AI response"}


def test_rob_rejects_tools_that_are_not_risk_of_bias_tools(client, auth_headers):
    body = {"papers": [PAPER], "tool": "PRESS"}

    assert client.post("/api/screen/rob", json=body, headers=auth_headers).status_code == 400


def test_unknown_provider_rejected(client, auth_headers):
    body = {"research_question": "aspirin", "provider": "bogus"}

    assert client.post("/api/protocol/generate", json=body, headers=auth_headers).status_code == 422


def test_oversized_batch_rejected(client, auth_headers):
    body = {"papers": [PAPER] * (main.MAX_PAPERS_PER_BATCH + 1), "criteria": "adults"}

    assert client.post("/api/screen/abstract", json=body, headers=auth_headers).status_code == 422


def test_search_failure_returns_502(client, auth_headers, monkeypatch):
    def failing_search(query, max_results):
        raise SearchError("PubMed search failed. Please try again shortly.")

    monkeypatch.setattr(main, "search_pubmed", failing_search)

    response = client.post("/api/search", json={"database": "PubMed", "query": "aspirin"}, headers=auth_headers)

    assert response.status_code == 502
    assert response.json()["detail"] == "PubMed search failed. Please try again shortly."


def test_non_pubmed_search_labelled_as_openalex(client, auth_headers, monkeypatch):
    monkeypatch.setattr(main, "search_openalex", lambda query, max_results: [])

    response = client.post("/api/search", json={"database": "Embase", "query": "aspirin"}, headers=auth_headers)

    assert response.status_code == 200
    assert response.json()["source"].startswith("OpenAlex")


def test_cors_does_not_allow_unknown_origin(client):
    response = client.options(
        "/api/chat", headers={"Origin": "https://evil.example", "Access-Control-Request-Method": "POST"}
    )

    assert "access-control-allow-origin" not in response.headers


def test_cors_allows_configured_origin(client):
    response = client.options(
        "/api/chat", headers={"Origin": "http://localhost:5173", "Access-Control-Request-Method": "POST"}
    )

    assert response.headers["access-control-allow-origin"] == "http://localhost:5173"
