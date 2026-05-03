"""Lightweight rate limiting for the agent service.

Two layers, both in-process (no external store required):

1. **Global concurrency cap.** A semaphore caps how many chat turns can
   be in flight at once. Anthropic's per-org limit is 30K input tokens
   per minute and a single chat turn costs ~3–4K input tokens; if four
   turns run concurrently and each takes 10–15s, we stay safely under
   the budget. Without this we got hit on 2026-05-02 when a parallel
   probe of all 14 patients raised RateLimitError on patient #8.

2. **Per-IP rolling-minute cap.** A small ring buffer per client IP
   tracks request times in the last 60 seconds and rejects further
   requests with HTTP 429 when the cap is reached. This is the cheap
   abuse guard for the demo `/demo/chat` endpoint, which has no
   bearer-token gate.

Neither layer is suitable for multi-replica production at scale; for
that we'd move to Redis-backed buckets (the cache is already there).
But for the single-replica Railway deployment this is enough to keep
the service stable under bursty access.
"""

from __future__ import annotations

import asyncio
import time
from collections import defaultdict, deque
from contextlib import asynccontextmanager
from typing import AsyncIterator

# Tunables. Conservative — easy to relax once we see the real load shape.
GLOBAL_MAX_CONCURRENT_CHATS = 4
PER_IP_REQUESTS_PER_MINUTE = 10

_chat_semaphore = asyncio.Semaphore(GLOBAL_MAX_CONCURRENT_CHATS)
_ip_buckets: dict[str, deque[float]] = defaultdict(deque)
_ip_lock = asyncio.Lock()


class RateLimitExceeded(Exception):
    """Raised when a per-IP or global cap is hit. The view layer should
    map this to HTTP 429 with a Retry-After hint."""

    def __init__(self, *, retry_after_seconds: int, reason: str) -> None:
        super().__init__(reason)
        self.retry_after_seconds = retry_after_seconds
        self.reason = reason


async def check_ip_quota(client_ip: str) -> None:
    """Raise RateLimitExceeded if `client_ip` has exceeded its rolling
    one-minute budget. Records this request as the side effect of the
    check, so a successful return reserves the slot."""
    now = time.monotonic()
    cutoff = now - 60.0
    async with _ip_lock:
        bucket = _ip_buckets[client_ip]
        while bucket and bucket[0] < cutoff:
            bucket.popleft()
        if len(bucket) >= PER_IP_REQUESTS_PER_MINUTE:
            # Approximate retry: when the oldest call ages out.
            retry = max(1, int(bucket[0] + 60 - now))
            raise RateLimitExceeded(
                retry_after_seconds=retry,
                reason=f"too many requests from {client_ip} (cap {PER_IP_REQUESTS_PER_MINUTE}/min)",
            )
        bucket.append(now)


@asynccontextmanager
async def chat_concurrency_slot() -> AsyncIterator[None]:
    """Acquire one of GLOBAL_MAX_CONCURRENT_CHATS slots. If none free
    within 200ms, raise rather than queue indefinitely — a queued
    request that ages out past the LLM rate limit would just fail
    anyway, and a fast 429 is a better signal to the caller."""
    try:
        await asyncio.wait_for(_chat_semaphore.acquire(), timeout=0.2)
    except asyncio.TimeoutError as exc:
        raise RateLimitExceeded(
            retry_after_seconds=5,
            reason=f"agent busy ({GLOBAL_MAX_CONCURRENT_CHATS} chats in flight); retry shortly",
        ) from exc
    try:
        yield
    finally:
        _chat_semaphore.release()
