"""Calls a provider with retries, timeouts, per-provider concurrency limits, and validation of structured output.

Every failure surfaces as LLMError with a message that is safe to show; nothing is ever substituted for a failed call.
"""

import asyncio
import json
import logging
import os
import time
import weakref
from dataclasses import dataclass
from decimal import Decimal
from typing import Any, Literal

from pydantic import BaseModel, ValidationError

from llm import adapters
from llm.providers import ProviderSpec
from services.errors import LLMError

logger = logging.getLogger(__name__)

MAX_ATTEMPTS = int(os.getenv("LLM_MAX_ATTEMPTS", "3"))
BACKOFF_SECONDS = float(os.getenv("LLM_BACKOFF_SECONDS", "1"))
CALL_TIMEOUT_SECONDS = float(os.getenv("LLM_TIMEOUT_SECONDS", "150"))
MAX_CONCURRENCY_PER_PROVIDER = int(os.getenv("LLM_MAX_CONCURRENCY", "5"))


@dataclass(frozen=True)
class AIContext:
    """Which provider, model, and key a call uses. key_source records whether the user's own key paid for it."""

    provider: ProviderSpec
    model: str
    api_key: str
    key_source: Literal["platform", "user"]
    input_price_per_mtok: Decimal | None = None
    output_price_per_mtok: Decimal | None = None


@dataclass
class Usage:
    input_tokens: int | None = None
    output_tokens: int | None = None
    latency_ms: int = 0
    attempts: int = 0

    def record(self, reply: adapters.ProviderReply) -> None:
        if reply.input_tokens is not None:
            self.input_tokens = (self.input_tokens or 0) + reply.input_tokens
        if reply.output_tokens is not None:
            self.output_tokens = (self.output_tokens or 0) + reply.output_tokens


@dataclass
class AIResult[T]:
    value: T
    usage: Usage


class _UnusableReply(Exception):
    pass


# Per event loop as well as per provider, because a semaphore is bound to the loop it first waits on.
_semaphores: weakref.WeakKeyDictionary[asyncio.AbstractEventLoop, dict[str, asyncio.Semaphore]] = (
    weakref.WeakKeyDictionary()
)


def _semaphore(provider_id: str) -> asyncio.Semaphore:
    per_provider = _semaphores.setdefault(asyncio.get_running_loop(), {})
    if provider_id not in per_provider:
        per_provider[provider_id] = asyncio.Semaphore(MAX_CONCURRENCY_PER_PROVIDER)
    return per_provider[provider_id]


async def _call_with_retries(
    ai: AIContext, prompt: str, json_schema: dict[str, Any] | None, max_tokens: int, usage: Usage
) -> adapters.ProviderReply:
    attempt = 0
    while True:
        attempt += 1
        usage.attempts += 1
        started = time.perf_counter()
        try:
            async with _semaphore(ai.provider.id):
                reply = await asyncio.wait_for(
                    adapters.call_provider(
                        ai.provider, ai.model, prompt, ai.api_key, json_schema=json_schema, max_tokens=max_tokens
                    ),
                    CALL_TIMEOUT_SECONDS,
                )
        except LLMError:
            raise
        except Exception as exc:
            usage.latency_ms += round((time.perf_counter() - started) * 1000)
            if attempt < MAX_ATTEMPTS and adapters.is_retryable(exc):
                logger.warning("%s call failed (attempt %s); retrying", ai.provider.id, attempt, exc_info=True)
                await asyncio.sleep(BACKOFF_SECONDS * 2 ** (attempt - 1))
                continue
            logger.warning("%s call failed after %s attempt(s)", ai.provider.id, attempt, exc_info=True)
            raise LLMError(adapters.safe_error_message(ai.provider, ai.model, exc)) from exc
        usage.latency_ms += round((time.perf_counter() - started) * 1000)
        usage.record(reply)
        return reply


def _inline_refs(node: Any, definitions: dict[str, Any]) -> Any:
    if isinstance(node, dict):
        if "$ref" in node:
            return _inline_refs(definitions[node["$ref"].split("/")[-1]], definitions)
        return {key: _inline_refs(value, definitions) for key, value in node.items() if key not in ("$defs", "title")}
    if isinstance(node, list):
        return [_inline_refs(item, definitions) for item in node]
    return node


def json_schema_for(schema: type[BaseModel]) -> dict[str, Any]:
    """The model's JSON Schema with references inlined, which every provider's structured-output feature accepts."""
    raw = schema.model_json_schema()
    return _inline_refs(raw, raw.get("$defs", {}))


def _parse[ModelT: BaseModel](text: str, schema: type[ModelT]) -> ModelT:
    cleaned = text.strip()
    if cleaned.startswith("```"):
        cleaned = cleaned.split("\n", 1)[1] if "\n" in cleaned else ""
        cleaned = cleaned.rsplit("```", 1)[0]
    try:
        data = json.loads(cleaned)
    except json.JSONDecodeError as exc:
        raise _UnusableReply("the reply was not valid JSON") from exc
    try:
        return schema.model_validate(data)
    except ValidationError as exc:
        problems = "; ".join(
            f"{'.'.join(str(part) for part in error['loc']) or 'reply'}: {error['msg']}" for error in exc.errors()[:5]
        )
        raise _UnusableReply(problems) from exc


def _with_usage(error: LLMError, usage: Usage) -> LLMError:
    error.usage = usage
    return error


async def complete_text(ai: AIContext, prompt: str, max_tokens: int) -> AIResult[str]:
    usage = Usage()
    try:
        reply = await _call_with_retries(ai, prompt, None, max_tokens, usage)
    except LLMError as exc:
        raise _with_usage(exc, usage) from exc.__cause__
    if not reply.text.strip():
        raise _with_usage(LLMError(f"{ai.provider.label} returned an empty response"), usage)
    return AIResult(reply.text, usage)


async def complete_structured[ModelT: BaseModel](
    ai: AIContext, prompt: str, schema: type[ModelT], max_tokens: int
) -> AIResult[ModelT]:
    """Ask for JSON matching `schema`. An unusable reply gets one repair attempt that explains what was wrong."""
    usage = Usage()
    json_schema = json_schema_for(schema)
    full_prompt = (
        f"{prompt}\n\nRespond with only a JSON object that matches this JSON Schema:\n{json.dumps(json_schema)}"
    )
    try:
        reply = await _call_with_retries(ai, full_prompt, json_schema, max_tokens, usage)
        try:
            return AIResult(_parse(reply.text, schema), usage)
        except _UnusableReply as first_problem:
            repair_prompt = (
                f"{full_prompt}\n\nYour previous reply could not be used ({first_problem}). "
                "Reply again with only the corrected JSON object."
            )
            reply = await _call_with_retries(ai, repair_prompt, json_schema, max_tokens, usage)
            try:
                return AIResult(_parse(reply.text, schema), usage)
            except _UnusableReply as second_problem:
                raise LLMError(
                    f"{ai.provider.label} returned a response that doesn't match the required structure "
                    f"({second_problem})"
                ) from second_problem
    except LLMError as exc:
        raise _with_usage(exc, usage) from exc.__cause__
