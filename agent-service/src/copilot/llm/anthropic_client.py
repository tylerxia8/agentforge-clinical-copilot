"""Thin wrapper around the Anthropic SDK.

Why a wrapper:
- Keeps the orchestrator from directly depending on the SDK's response
  shape (easier to swap to AWS Bedrock for production).
- Single chokepoint for retry logic and Langfuse tracing.
"""

from __future__ import annotations

import asyncio
import logging
import random
from typing import Any

from anthropic import (
    APIConnectionError,
    APIStatusError,
    APITimeoutError,
    AsyncAnthropic,
)

from copilot.observability import langfuse_client, observe
from copilot.settings import settings

logger = logging.getLogger(__name__)

# Retry-on-overload: Anthropic returns 529 (overloaded_error) under load
# spikes — the SDK doesn't auto-retry, the LangGraph layer's retry
# helper doesn't either, so a single 529 propagates and refuses the
# turn. During W2 calibration we saw ~10 cases miss because of this
# (golden / multistep / extraction / evidence categories all dropped
# 30-80pp despite a healthy live agent). Retrying with capped
# exponential backoff + small jitter makes the calibration robust to
# brief Anthropic incidents without changing user-visible latency on
# the happy path.
_RETRY_STATUSES = (529, 503, 502, 500, 408, 425)
_MAX_RETRIES = 4
_BACKOFF_BASE_SECONDS = 1.5


async def _call_with_retry(client: AsyncAnthropic, kwargs: dict[str, Any]) -> Any:
    last_exc: Exception | None = None
    for attempt in range(_MAX_RETRIES + 1):
        try:
            return await client.messages.create(**kwargs)
        except APIStatusError as exc:
            status = getattr(exc, "status_code", None)
            if status not in _RETRY_STATUSES or attempt == _MAX_RETRIES:
                raise
            last_exc = exc
        except (APIConnectionError, APITimeoutError) as exc:
            if attempt == _MAX_RETRIES:
                raise
            last_exc = exc
        # Exponential backoff with jitter. Cap so a 529 storm doesn't
        # turn a single chat turn into a 30-second wait.
        delay = min(_BACKOFF_BASE_SECONDS * (2 ** attempt), 12.0)
        delay += random.uniform(0, 0.5)
        logger.warning(
            "anthropic call retrying after %s (attempt %d/%d, sleep %.1fs)",
            type(last_exc).__name__, attempt + 1, _MAX_RETRIES, delay,
        )
        await asyncio.sleep(delay)
    # Defensive — the loop returns or raises before reaching here.
    raise last_exc if last_exc else RuntimeError("retry loop exited without result")

# Per-million-token rates in USD. Source: anthropic.com/pricing as
# of Sonnet 4.6 launch. The Langfuse cost dashboard reads these via
# update_current_generation(cost_details=...).
#
# Cache-write is the 5-minute TTL price; cache-read is the hit price.
# Cache-creation tokens incur both the write rate AND the input rate
# in our accounting, matching how Anthropic bills us.
_PRICING_PER_MTOK: dict[str, dict[str, float]] = {
    "claude-sonnet-4-6": {
        "input":         3.00,
        "output":       15.00,
        "cache_write":   3.75,
        "cache_read":    0.30,
    },
    "claude-opus-4-7": {
        "input":        15.00,
        "output":       75.00,
        "cache_write": 18.75,
        "cache_read":   1.50,
    },
    "claude-haiku-4-5": {
        "input":         1.00,
        "output":        5.00,
        "cache_write":   1.25,
        "cache_read":    0.10,
    },
}


def _compute_cost(model: str, usage: Any) -> dict[str, float] | None:
    """Translate token counts into per-call USD costs Langfuse can
    aggregate. Returns ``None`` for unknown models so the dashboard
    falls back to whatever default it would otherwise compute."""
    if not usage:
        return None
    rates = _PRICING_PER_MTOK.get(model)
    if rates is None:
        return None

    input_tok = getattr(usage, "input_tokens", 0) or 0
    output_tok = getattr(usage, "output_tokens", 0) or 0
    cache_write_tok = getattr(usage, "cache_creation_input_tokens", 0) or 0
    cache_read_tok = getattr(usage, "cache_read_input_tokens", 0) or 0

    # Anthropic's token counts already separate cache-create / cache-read
    # from regular input. But we DO bill for both lines on a cache write
    # (the input goes through both pipes once). Match the invoice:
    cost_input = input_tok * rates["input"] / 1_000_000
    cost_output = output_tok * rates["output"] / 1_000_000
    cost_cache_w = cache_write_tok * rates["cache_write"] / 1_000_000
    cost_cache_r = cache_read_tok * rates["cache_read"] / 1_000_000
    return {
        "input": round(cost_input, 6),
        "output": round(cost_output, 6),
        "cache_write": round(cost_cache_w, 6),
        "cache_read": round(cost_cache_r, 6),
        "total": round(cost_input + cost_output + cost_cache_w + cost_cache_r, 6),
    }


class LLM:
    def __init__(self, model_id: str | None = None) -> None:
        self._client = AsyncAnthropic(api_key=settings.anthropic_api_key)
        self._model = model_id or settings.model_id

    @observe(name="llm.complete", as_type="generation")
    async def complete(
        self,
        messages: list[dict[str, Any]],
        system: str,
        tools: list[dict[str, Any]] | None = None,
    ) -> Any:
        kwargs: dict[str, Any] = {
            "model": self._model,
            "max_tokens": settings.max_tokens,
            "system": system,
            "messages": messages,
        }
        if tools:
            kwargs["tools"] = tools

        response = await _call_with_retry(self._client, kwargs)

        # Record model + token usage + cost on the current Langfuse
        # generation. Cache tokens (creation_input / read_input) are
        # tracked separately so the dashboard can show cache-hit rate
        # — that's the W2 cost lever, not raw token volume.
        if langfuse_client is not None:
            try:
                usage = getattr(response, "usage", None)
                input_tokens = getattr(usage, "input_tokens", 0) if usage else 0
                output_tokens = getattr(usage, "output_tokens", 0) if usage else 0
                cache_write = (
                    getattr(usage, "cache_creation_input_tokens", 0) if usage else 0
                ) or 0
                cache_read = (
                    getattr(usage, "cache_read_input_tokens", 0) if usage else 0
                ) or 0
                stop_reason = getattr(response, "stop_reason", None)

                update_kwargs: dict[str, Any] = {
                    "model": self._model,
                    "usage_details": {
                        "input": input_tokens,
                        "output": output_tokens,
                        "cache_read_input": cache_read,
                        "cache_write_input": cache_write,
                        "total": input_tokens + output_tokens + cache_write + cache_read,
                    },
                    "metadata": {
                        "stop_reason": stop_reason,
                        "tools_offered": [t.get("name") for t in (tools or [])],
                    },
                }
                cost_details = _compute_cost(self._model, usage)
                if cost_details is not None:
                    update_kwargs["cost_details"] = cost_details
                langfuse_client.update_current_generation(**update_kwargs)
            except Exception:  # noqa: BLE001
                pass  # observability must not fail the call

        return response
