"""Visibility / introspection endpoints — built in response to W2
MVP grader feedback ("stronger visibility into the retrieval
architecture, eval coverage, and worker orchestration").

Surfaces three views as JSON, plus a static HTML page that renders
them. Goal is to let a reviewer see HOW the system works — what's
in the corpus, how the supervisor routes, what the eval suite
covers, what BM25/dense/rerank actually produces — without needing
to clone the repo or run the CLI.

Routes (mounted in main.py):

- ``GET /visibility``           → static HTML page
- ``GET /visibility/data``      → JSON aggregate (corpus, eval coverage, routing rules, recent traces)
- ``POST /visibility/retrieve`` → JSON breakdown for a live query: BM25 / dense / rerank tiers with scores
"""

from __future__ import annotations

import json
import time
from collections import deque
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from copilot.rag.corpus import Chunk, load_corpus
from copilot.workers.routing import EVIDENCE_TRIGGER_TOKENS, HOP_LIMIT, route_decision


# ─── ring buffer for recent supervisor decisions ──────────────────────


@dataclass
class TraceEntry:
    timestamp: float
    message: str
    decision: str
    hops: int
    has_attachment: bool
    has_evidence: bool


_TRACE_BUFFER: deque[TraceEntry] = deque(maxlen=20)


def record_supervisor_decision(
    *,
    message: str,
    decision: str,
    hops: int,
    has_attachment: bool,
    has_evidence: bool,
) -> None:
    """Capture a supervisor decision for the visibility page. Called
    inline by the supervisor node so the buffer reflects real
    routing, not a synthetic replay."""
    _TRACE_BUFFER.append(
        TraceEntry(
            timestamp=time.time(),
            message=message[:200],  # truncate for the JSON payload
            decision=decision,
            hops=hops,
            has_attachment=has_attachment,
            has_evidence=has_evidence,
        )
    )


def recent_traces() -> list[dict[str, Any]]:
    """Most-recent-first trace dump for the visibility page."""
    return [
        {
            "timestamp": t.timestamp,
            "ago_seconds": round(time.time() - t.timestamp, 1),
            "message_preview": t.message,
            "decision": t.decision,
            "hops": t.hops,
            "has_attachment": t.has_attachment,
            "has_evidence": t.has_evidence,
        }
        for t in reversed(_TRACE_BUFFER)
    ]


# ─── corpus + routing snapshot ────────────────────────────────────────


def corpus_snapshot() -> list[dict[str, Any]]:
    """Render every chunk in the seeded corpus as a JSON-friendly
    dict for the visibility page. Truncates the body so the payload
    stays small; full text is one click away in the source JSON."""
    chunks = load_corpus()
    out: list[dict[str, Any]] = []
    for c in chunks:
        out.append(
            {
                "chunk_id": c.chunk_id,
                "title": c.title,
                "section": c.section,
                "source": c.source,
                "source_url": c.source_url,
                "year": c.year,
                "text_preview": c.text[:240] + ("…" if len(c.text) > 240 else ""),
                "text_full": c.text,
            }
        )
    out.sort(key=lambda r: (r["source"], r["year"], r["chunk_id"]))
    return out


def routing_snapshot() -> dict[str, Any]:
    """Document the supervisor's routing rules — the same structure
    a developer would see in workers/routing.py, exposed so the
    visibility page can show what triggers what."""
    return {
        "hop_limit": HOP_LIMIT,
        "decision_order": [
            {
                "rule": "hop_overflow",
                "test": f"hops >= {HOP_LIMIT}",
                "decision": "answer",
                "rationale": "hard escape against routing loops; alerts via Langfuse if it fires",
            },
            {
                "rule": "attachment_present",
                "test": "state.attachment is not None",
                "decision": "intake_extractor",
                "rationale": "incoming PDF/DOCX/XLSX/TIFF must be parsed before answering",
            },
            {
                "rule": "evidence_trigger_token",
                "test": f"any token in EVIDENCE_TRIGGER_TOKENS in message AND no evidence yet this turn",
                "decision": "evidence_retriever",
                "rationale": "guideline-shaped questions get RAG context before the answer node",
            },
            {
                "rule": "default",
                "test": "(no other rule matched)",
                "decision": "answer",
                "rationale": "plain chart questions short-circuit straight to the orchestrator",
            },
        ],
        "evidence_trigger_tokens": sorted(EVIDENCE_TRIGGER_TOKENS),
    }


# ─── eval coverage snapshot ───────────────────────────────────────────


def eval_coverage_snapshot() -> dict[str, Any]:
    """Pull cases + baseline together so the visibility page can
    show what each category tests and where the current pass rate
    stands. Reads cases.py + baseline.json at request time so the
    page reflects whatever's in the deployed code."""
    try:
        from evals.w2.cases import CATEGORY_TARGETS, all_cases
    except ImportError:
        # cases.py imports test fixtures that may not ship in every
        # deploy. Surface a friendly error rather than 500.
        return {
            "available": False,
            "reason": "evals.w2.cases is not importable in this deploy",
        }

    baseline_path = (
        Path(__file__).parent.parent.parent / "evals" / "w2" / "baseline.json"
    )
    baseline: dict[str, Any] = {}
    if baseline_path.exists():
        try:
            baseline = json.loads(baseline_path.read_text())
        except Exception:  # noqa: BLE001
            baseline = {}

    cases = all_cases()
    by_category: dict[str, list[dict[str, str]]] = {}
    for c in cases:
        by_category.setdefault(c.category, []).append(
            {"case_id": c.case_id, "description": c.description}
        )

    rates = baseline.get("category_rates", {})
    rubric_rates = baseline.get("rubric_rates", {})

    categories: list[dict[str, Any]] = []
    for cat, target in CATEGORY_TARGETS.items():
        cs = by_category.get(cat, [])
        categories.append(
            {
                "category": cat,
                "target_count": target,
                "case_count": len(cs),
                "baseline_rate": rates.get(cat),
                "cases": cs,
            }
        )

    return {
        "available": True,
        "total_cases": len(cases),
        "category_targets": dict(CATEGORY_TARGETS),
        "categories": categories,
        "rubric_rates": rubric_rates,
        "baseline_saved_at": baseline.get("saved_at"),
    }


# ─── live retrieval inspector ─────────────────────────────────────────


def retrieval_breakdown(retriever: Any, query: str, top_k: int = 5) -> dict[str, Any]:
    """Run a query through the deployed retriever and return what
    came back at each layer (BM25 / dense / rerank), with scores so
    the visibility page can show the hybrid retrieval working.

    The retriever instance is shared with the production /agent/chat
    path — same corpus, same scoring — so what you see here is what
    the agent sees."""
    if retriever is None:
        return {"error": "retriever not initialized in this process"}
    if not query.strip():
        return {"error": "query is empty"}

    try:
        results = retriever.retrieve(query, top_k=top_k)
    except Exception as exc:  # noqa: BLE001 — never crash the inspector
        return {"error": f"{type(exc).__name__}: {exc}"}

    return {
        "query": query,
        "top_k": top_k,
        "results": [
            {
                "rank": i + 1,
                "chunk_id": r.chunk.chunk_id,
                "source": r.chunk.source,
                "title": r.chunk.title,
                "year": r.chunk.year,
                "score": round(r.score, 4),
                "source_layer": r.source_layer,
                "text_preview": r.chunk.text[:200] + ("…" if len(r.chunk.text) > 200 else ""),
            }
            for i, r in enumerate(results)
        ],
    }
