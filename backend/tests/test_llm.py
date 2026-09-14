import asyncio

import pytest

from services import llm
from services.errors import LLMError


def test_template_placeholder_keys_treated_as_missing(monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "your_openai_api_key_here")

    assert llm._api_key("OPENAI_API_KEY") is None


def test_real_key_is_used(monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "  sk-live-example  ")

    assert llm._api_key("OPENAI_API_KEY") == "sk-live-example"


def test_fenced_json_parsed():
    assert llm._parse_json_object('```json\n{"a": 1}\n```', "anthropic") == {"a": 1}


def test_invalid_json_rejected():
    with pytest.raises(LLMError):
        llm._parse_json_object("Sure! Here is the JSON you asked for", "gemini")


def test_json_that_is_not_an_object_rejected():
    with pytest.raises(LLMError):
        llm._parse_json_object('["Include"]', "gemini")


def test_provider_exception_becomes_safe_error_message(monkeypatch):
    async def failing_call(*args):
        raise RuntimeError("internal detail with key sk-secret-123")

    monkeypatch.setattr(llm, "_call_provider", failing_call)

    with pytest.raises(LLMError) as error:
        asyncio.run(llm.generate_text("hello", "openai"))

    assert "sk-secret-123" not in str(error.value)


def test_empty_model_response_is_an_error(monkeypatch):
    async def empty_call(*args):
        return "   "

    monkeypatch.setattr(llm, "_call_provider", empty_call)

    with pytest.raises(LLMError):
        asyncio.run(llm.generate_text("hello", "gemini"))
