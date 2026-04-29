"""Thin wrapper around the Anthropic SDK.

Why a wrapper:
- Keeps the orchestrator from directly depending on the SDK's response
  shape (easier to swap to AWS Bedrock for production).
- Single chokepoint for retry logic and Langfuse tracing (TODO).
"""

from __future__ import annotations

from typing import Any

from anthropic import AsyncAnthropic

from copilot.settings import settings


class LLM:
    def __init__(self, model_id: str | None = None) -> None:
        self._client = AsyncAnthropic(api_key=settings.anthropic_api_key)
        self._model = model_id or settings.model_id

    async def complete(
        self,
        messages: list[dict[str, Any]],
        system: str,
        tools: list[dict[str, Any]] | None = None,
    ) -> Any:
        # TODO(thursday): wrap in a Langfuse @observe span; record
        # input_tokens, output_tokens, cost, latency.
        kwargs: dict[str, Any] = {
            "model": self._model,
            "max_tokens": settings.max_tokens,
            "system": system,
            "messages": messages,
        }
        if tools:
            kwargs["tools"] = tools
        return await self._client.messages.create(**kwargs)
