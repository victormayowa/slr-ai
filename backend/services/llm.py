"""Shared access to the supported AI providers.

Failures raise LLMError. Callers must surface the error; never substitute a made-up result.
"""

import asyncio
import json
import logging
import os

import anthropic
import openai
from dotenv import load_dotenv
from google import genai
from google.genai import types

from services.errors import LLMError

load_dotenv()

logger = logging.getLogger(__name__)

GEMINI_MODEL = os.getenv("GEMINI_MODEL", "gemini-3.6-flash")
OPENAI_MODEL = os.getenv("OPENAI_MODEL", "gpt-4o")
ANTHROPIC_MODEL = os.getenv("ANTHROPIC_MODEL", "claude-sonnet-5")
REQUEST_TIMEOUT_SECONDS = 60

# Caps simultaneous provider calls from this process so large batches don't trip rate limits.
_semaphore = asyncio.Semaphore(int(os.getenv("LLM_MAX_CONCURRENCY", "5")))


def _api_key(name: str) -> str | None:
    """Return a configured key, treating blanks and template placeholders as missing."""
    value = os.getenv(name, "").strip()
    if not value or value.lower().startswith("your"):
        return None
    return value


_gemini_key = _api_key("GEMINI_API_KEY")
gemini_client = (
    genai.Client(api_key=_gemini_key, http_options=types.HttpOptions(timeout=REQUEST_TIMEOUT_SECONDS * 1000))
    if _gemini_key
    else None
)

_openai_key = _api_key("OPENAI_API_KEY")
openai_client = openai.AsyncOpenAI(api_key=_openai_key, timeout=REQUEST_TIMEOUT_SECONDS) if _openai_key else None

_anthropic_key = _api_key("ANTHROPIC_API_KEY")
anthropic_client = (
    anthropic.AsyncAnthropic(api_key=_anthropic_key, timeout=REQUEST_TIMEOUT_SECONDS) if _anthropic_key else None
)


async def _call_provider(prompt: str, provider: str, json_output: bool, max_tokens: int) -> str:
    if provider == "openai":
        if openai_client is None:
            raise LLMError("OPENAI_API_KEY is not configured on the server")
        extra = {"response_format": {"type": "json_object"}} if json_output else {}
        response = await openai_client.chat.completions.create(
            model=OPENAI_MODEL,
            messages=[{"role": "user", "content": prompt}],
            **extra,
        )
        return response.choices[0].message.content or ""

    if provider == "anthropic":
        if anthropic_client is None:
            raise LLMError("ANTHROPIC_API_KEY is not configured on the server")
        if json_output:
            prompt += "\n\nOutput only raw JSON. Do not include markdown code fences."
        response = await anthropic_client.messages.create(
            model=ANTHROPIC_MODEL,
            max_tokens=max_tokens,
            messages=[{"role": "user", "content": prompt}],
        )
        return "".join(block.text for block in response.content if block.type == "text")

    if provider == "gemini":
        if gemini_client is None:
            raise LLMError("GEMINI_API_KEY is not configured on the server")
        config = types.GenerateContentConfig(response_mime_type="application/json") if json_output else None
        response = await gemini_client.aio.models.generate_content(model=GEMINI_MODEL, contents=prompt, config=config)
        return response.text or ""

    raise LLMError(f"Unsupported AI provider: {provider}")


async def _complete(prompt: str, provider: str, json_output: bool, max_tokens: int) -> str:
    async with _semaphore:
        try:
            text = await _call_provider(prompt, provider, json_output, max_tokens)
        except LLMError:
            raise
        except Exception as exc:
            logger.warning("%s request failed", provider, exc_info=True)
            raise LLMError(
                f"The {provider} request failed. Check the API key, model name, and rate limits."
            ) from exc
    if not text.strip():
        raise LLMError(f"{provider} returned an empty response")
    return text


def _parse_json_object(text: str, provider: str) -> dict:
    cleaned = text.strip()
    if cleaned.startswith("```"):
        cleaned = cleaned.split("\n", 1)[1] if "\n" in cleaned else ""
        cleaned = cleaned.rsplit("```", 1)[0]
    try:
        data = json.loads(cleaned)
    except json.JSONDecodeError as exc:
        raise LLMError(f"{provider} returned a response that is not valid JSON") from exc
    if not isinstance(data, dict):
        raise LLMError(f"{provider} returned JSON that is not an object")
    return data


async def generate_text(prompt: str, provider: str, max_tokens: int = 2000) -> str:
    return await _complete(prompt, provider, json_output=False, max_tokens=max_tokens)


async def generate_json(prompt: str, provider: str, max_tokens: int = 2000) -> dict:
    text = await _complete(prompt, provider, json_output=True, max_tokens=max_tokens)
    return _parse_json_object(text, provider)
