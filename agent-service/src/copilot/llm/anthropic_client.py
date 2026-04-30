"""Thin wrapper around the Anthropic SDK.

Why a wrapper:
- Keeps the orchestrator from directly depending on the SDK's response
  shape (easier to swap to AWS Bedrock for production).
- Single chokepoint for retry logic and Langfuse tracing.
"""

from __future__ import annotations

from typing import Any

from anthropic import AsyncAnthropic

from copilot.observability import langfuse_client, observe
from copilot.settings import settings


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

        response = await self._client.messages.create(**kwargs)

        # Record model + token usage on the current Langfuse generation.
        if langfuse_client is not None:
            try:
                usage = getattr(response, "usage", None)
                input_tokens = getattr(usage, "input_tokens", 0) if usage else 0
                output_tokens = getattr(usage, "output_tokens", 0) if usage else 0
                langfuse_client.update_current_generation(
                    model=self._model,
                    usage_details={
                        "input": input_tokens,
                        "output": output_tokens,
                        "total": input_tokens + output_tokens,
                    },
                )
            except Exception:  # noqa: BLE001
                pass  # observability must not fail the call

        return response
