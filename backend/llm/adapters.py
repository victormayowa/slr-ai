"""One adapter per provider API style. Each returns the reply text (JSON text for structured calls) and token usage."""

import hashlib
import json
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

import anthropic
import httpx
import openai
from google import genai
from google.genai import types

from llm.providers import ProviderSpec

REQUEST_TIMEOUT_SECONDS = 120
RETRYABLE_STATUS_CODES = {408, 409, 429, 500, 502, 503, 504, 529}
_MAX_CACHED_CLIENTS = 64


@dataclass
class ProviderReply:
    text: str
    input_tokens: int | None = None
    output_tokens: int | None = None


@dataclass
class EmbeddingReply:
    vectors: list[list[float]]
    input_tokens: int | None = None
    output_tokens: int | None = None


_clients: dict[tuple[str, str], Any] = {}


def _client(spec: ProviderSpec, api_key: str, create: Callable[[], Any]) -> Any:
    """Reuse SDK clients per provider and key (users can bring their own keys), without keeping keys as dict keys."""
    cache_key = (spec.id, hashlib.sha256(api_key.encode()).hexdigest())
    if cache_key not in _clients:
        if len(_clients) >= _MAX_CACHED_CLIENTS:
            _clients.clear()
        _clients[cache_key] = create()
    return _clients[cache_key]


def _gemini_client(spec: ProviderSpec, api_key: str) -> genai.Client:
    return _client(
        spec,
        api_key,
        lambda: genai.Client(api_key=api_key, http_options=types.HttpOptions(timeout=REQUEST_TIMEOUT_SECONDS * 1000)),
    )


def _openai_client(spec: ProviderSpec, api_key: str) -> openai.AsyncOpenAI:
    return _client(
        spec,
        api_key,
        lambda: openai.AsyncOpenAI(
            api_key=api_key, base_url=spec.resolved_base_url, timeout=REQUEST_TIMEOUT_SECONDS, max_retries=0
        ),
    )


async def call_provider(
    spec: ProviderSpec,
    model: str,
    prompt: str,
    api_key: str,
    *,
    json_schema: dict[str, Any] | None,
    max_tokens: int,
) -> ProviderReply:
    if spec.adapter == "anthropic":
        return await _call_anthropic(spec, model, prompt, api_key, json_schema, max_tokens)
    if spec.adapter == "gemini":
        return await _call_gemini(spec, model, prompt, api_key, json_schema)
    return await _call_openai_compatible(spec, model, prompt, api_key, json_schema, max_tokens)


async def _call_anthropic(
    spec: ProviderSpec, model: str, prompt: str, api_key: str, json_schema: dict[str, Any] | None, max_tokens: int
) -> ProviderReply:
    client: anthropic.AsyncAnthropic = _client(
        spec, api_key, lambda: anthropic.AsyncAnthropic(api_key=api_key, timeout=REQUEST_TIMEOUT_SECONDS, max_retries=0)
    )
    if json_schema is None:
        message = await client.messages.create(
            model=model, max_tokens=max_tokens, messages=[{"role": "user", "content": prompt}]
        )
        text = "".join(block.text for block in message.content if block.type == "text")
    else:
        # A forced tool call makes Claude return arguments that follow the schema.
        message = await client.messages.create(
            model=model,
            max_tokens=max_tokens,
            messages=[{"role": "user", "content": prompt}],
            tools=[
                {
                    "name": "record_result",
                    "description": "Record the result in the required structure.",
                    "input_schema": json_schema,
                }
            ],
            tool_choice={"type": "tool", "name": "record_result"},
        )
        tool_use = next((block for block in message.content if block.type == "tool_use"), None)
        text = json.dumps(tool_use.input) if tool_use is not None else ""
    return ProviderReply(text, message.usage.input_tokens, message.usage.output_tokens)


async def _call_gemini(
    spec: ProviderSpec, model: str, prompt: str, api_key: str, json_schema: dict[str, Any] | None
) -> ProviderReply:
    client = _gemini_client(spec, api_key)
    config = (
        types.GenerateContentConfig(response_mime_type="application/json", response_json_schema=json_schema)
        if json_schema is not None
        else None
    )
    response = await client.aio.models.generate_content(model=model, contents=prompt, config=config)
    usage = response.usage_metadata
    return ProviderReply(
        response.text or "",
        usage.prompt_token_count if usage else None,
        usage.candidates_token_count if usage else None,
    )


async def _call_openai_compatible(
    spec: ProviderSpec, model: str, prompt: str, api_key: str, json_schema: dict[str, Any] | None, max_tokens: int
) -> ProviderReply:
    client = _openai_client(spec, api_key)
    # JSON mode is the structured-output feature all OpenAI-compatible providers share; the runner validates the reply.
    response_format: Any = {"type": "json_object"} if json_schema is not None else openai.omit
    if spec.max_tokens_param == "max_completion_tokens":
        completion = await client.chat.completions.create(
            model=model,
            messages=[{"role": "user", "content": prompt}],
            response_format=response_format,
            max_completion_tokens=max_tokens,
        )
    else:
        completion = await client.chat.completions.create(
            model=model,
            messages=[{"role": "user", "content": prompt}],
            response_format=response_format,
            max_tokens=max_tokens,
        )
    usage = completion.usage
    return ProviderReply(
        completion.choices[0].message.content or "",
        usage.prompt_tokens if usage else None,
        usage.completion_tokens if usage else None,
    )


async def embed_texts(
    spec: ProviderSpec, model: str, texts: list[str], api_key: str, *, dimensions: int
) -> EmbeddingReply:
    if spec.adapter == "gemini":
        response = await _gemini_client(spec, api_key).aio.models.embed_content(
            model=model,
            contents=texts,
            config=types.EmbedContentConfig(output_dimensionality=dimensions, task_type="SEMANTIC_SIMILARITY"),
        )
        return EmbeddingReply([list(embedding.values or []) for embedding in response.embeddings or []])
    if spec.adapter == "openai_compatible":
        result = await _openai_client(spec, api_key).embeddings.create(
            model=model, input=texts, dimensions=dimensions if spec.embedding_dimensions_param else openai.omit
        )
        vectors = [item.embedding for item in sorted(result.data, key=lambda item: item.index)]
        return EmbeddingReply(vectors, result.usage.prompt_tokens if result.usage else None)
    raise ValueError(f"{spec.label} does not offer embeddings")


def status_code(exc: BaseException) -> int | None:
    for attribute in ("status_code", "code"):
        value = getattr(exc, attribute, None)
        if isinstance(value, int):
            return value
    return None


def is_retryable(exc: BaseException) -> bool:
    """Rate limits, overload, server errors, timeouts, and dropped connections are worth retrying."""
    if isinstance(exc, TimeoutError | httpx.TransportError | anthropic.APIConnectionError | openai.APIConnectionError):
        return True
    code = status_code(exc)
    return code in RETRYABLE_STATUS_CODES


def safe_error_message(spec: ProviderSpec, model: str, exc: BaseException) -> str:
    """A message for users that never includes provider response bodies, which can echo keys or prompts."""
    code = status_code(exc)
    if code in (401, 403):
        return f"{spec.label} rejected the API key. Check the key in Settings or the server configuration."
    if code == 404:
        return f'{spec.label} does not recognise the model "{model}". Choose another model for this project.'
    if code == 429:
        return f"{spec.label} is rate limiting requests. Wait a moment and try again."
    if is_retryable(exc):
        return f"{spec.label} is unavailable right now. Try again shortly."
    if code == 400:
        return f'{spec.label} rejected the request for model "{model}".'
    return f"The request to {spec.label} failed."
