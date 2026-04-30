"""Langfuse observability — gracefully degrades when keys aren't set.

Two import shapes the orchestrator and tool layer use:

    from copilot.observability import langfuse_client, observe

`observe` is the @observe decorator (or a no-op pass-through if Langfuse
isn't configured). `langfuse_client` is the configured Langfuse client
or None — None means "skip the manual usage/IO updates".
"""

from __future__ import annotations

import logging
import os
from collections.abc import Callable
from typing import Any

logger = logging.getLogger(__name__)

_LANGFUSE_KEYS_SET = bool(
    os.environ.get("LANGFUSE_PUBLIC_KEY")
    and os.environ.get("LANGFUSE_SECRET_KEY")
)

langfuse_client: Any | None = None
observe: Callable[..., Callable[..., Any]]

if _LANGFUSE_KEYS_SET:
    try:
        from langfuse import Langfuse, observe as _real_observe  # type: ignore[import-untyped]

        langfuse_client = Langfuse()
        observe = _real_observe
        logger.info("Langfuse observability enabled (host=%s)", os.environ.get("LANGFUSE_HOST"))
    except Exception as exc:  # noqa: BLE001
        logger.warning("Langfuse import/init failed, observability disabled: %s", exc)
        langfuse_client = None

        def _noop_decorator(*_args: Any, **_kwargs: Any) -> Callable[..., Any]:
            def _wrap(fn: Callable[..., Any]) -> Callable[..., Any]:
                return fn
            return _wrap

        observe = _noop_decorator
else:
    logger.info("Langfuse keys not set — observability disabled (no-op decorators).")

    def _noop_decorator(*_args: Any, **_kwargs: Any) -> Callable[..., Any]:
        def _wrap(fn: Callable[..., Any]) -> Callable[..., Any]:
            return fn
        return _wrap

    observe = _noop_decorator
