"""Hybrid retriever: BM25 + optional dense + optional rerank.

Three layers, each optional except BM25:

1. **BM25** (always on, in-memory via ``rank_bm25``). Fast, no
   network. Returns top-K candidates by lexical overlap.
2. **Dense embeddings** (optional). Whoever constructs the
   :class:`Retriever` injects an ``embed_fn(query) -> list[float]``
   and a pre-computed ``chunk_embeddings`` matrix. Returns top-K
   candidates by cosine similarity. If ``embed_fn`` is ``None``,
   this layer is skipped.
3. **Reranker** (optional). The injected
   ``rerank_fn(query, candidates) -> list[(chunk, score)]`` runs
   over the BM25 ∪ dense union and returns the final top-N. If
   ``rerank_fn`` is ``None``, the union is sorted by reciprocal-
   rank fusion of the upstream scores.

The injection design is deliberate: tests pass stubs to exercise
each layer independently. Production wires Voyage embeddings +
Cohere rerank at construction time. A deploy with no Voyage /
Cohere keys still runs (BM25 only) — degraded but functional.

Returns :class:`RetrievalResult`\\ s ordered by final relevance,
each carrying enough metadata for the agent to inline-cite as
``[Guideline#<chunk_id>]``.
"""

from __future__ import annotations

import logging
import math
import re
from dataclasses import dataclass
from typing import Callable, Sequence

from copilot.rag.corpus import Chunk

logger = logging.getLogger(__name__)


EmbedFn = Callable[[str], Sequence[float]]
"""Synchronous embed; return one vector for the query string."""

RerankFn = Callable[[str, list[Chunk]], list[tuple[Chunk, float]]]
"""Rerank; return (chunk, score) pairs in descending score order."""


@dataclass(frozen=True)
class RetrievalResult:
    """One retrieved chunk + the score that earned it the slot."""

    chunk: Chunk
    score: float
    source_layer: str  # "bm25", "dense", "rerank"


class Retriever:
    """Hybrid retriever over a fixed corpus.

    Build once at startup, query many times. Re-instantiating per
    query would re-tokenize the corpus.

    Parameters
    ----------
    chunks : list[Chunk]
        The corpus to index.
    embed_fn : EmbedFn | None
        Optional dense embedding function. If provided, ``chunk_embeddings``
        must also be provided.
    chunk_embeddings : list[Sequence[float]] | None
        Pre-computed embeddings, one per chunk in the same order.
    rerank_fn : RerankFn | None
        Optional reranker. Runs over the BM25 ∪ dense union.
    """

    def __init__(
        self,
        chunks: Sequence[Chunk],
        *,
        embed_fn: EmbedFn | None = None,
        chunk_embeddings: Sequence[Sequence[float]] | None = None,
        rerank_fn: RerankFn | None = None,
    ) -> None:
        if not chunks:
            raise ValueError("retriever requires a non-empty corpus")
        if (embed_fn is None) != (chunk_embeddings is None):
            raise ValueError(
                "embed_fn and chunk_embeddings must be provided together"
            )
        if chunk_embeddings is not None and len(chunk_embeddings) != len(chunks):
            raise ValueError(
                f"chunk_embeddings length {len(chunk_embeddings)} does not "
                f"match corpus length {len(chunks)}"
            )

        self._chunks = list(chunks)
        self._embed_fn = embed_fn
        self._rerank_fn = rerank_fn
        self._chunk_embeddings = (
            [list(v) for v in chunk_embeddings] if chunk_embeddings else None
        )
        self._bm25 = _BM25Index([c.text for c in self._chunks])

    def retrieve(
        self, query: str, *, top_k: int = 5, candidate_pool: int = 15
    ) -> list[RetrievalResult]:
        """Return the top ``top_k`` chunks for ``query``.

        Pipeline:
          1. BM25 → top ``candidate_pool`` candidates by score
          2. If embed_fn: dense top ``candidate_pool`` by cosine
          3. Union (dedup by chunk_id)
          4. If rerank_fn: rerank and take top_k
             else: fuse upstream scores (reciprocal rank) and take top_k
        """
        if not query.strip():
            return []
        if top_k <= 0:
            return []

        bm25_hits = self._bm25.top_k(query, candidate_pool)
        bm25_chunks = [self._chunks[i] for i, _ in bm25_hits]

        dense_chunks: list[Chunk] = []
        dense_scores: dict[str, float] = {}
        if self._embed_fn is not None and self._chunk_embeddings is not None:
            try:
                q_vec = list(self._embed_fn(query))
                dense_hits = _cosine_top_k(
                    q_vec, self._chunk_embeddings, candidate_pool
                )
                dense_chunks = [self._chunks[i] for i, _ in dense_hits]
                dense_scores = {
                    self._chunks[i].chunk_id: s for i, s in dense_hits
                }
            except Exception:  # noqa: BLE001 — degrade, never fail the query
                logger.warning("dense retrieval failed; using BM25 only", exc_info=True)

        bm25_scores = {c.chunk_id: s for c, s in zip(bm25_chunks, [s for _, s in bm25_hits])}

        # Union, preserving order: BM25 first, then any dense not seen
        seen: set[str] = set()
        union: list[Chunk] = []
        for c in bm25_chunks:
            if c.chunk_id not in seen:
                seen.add(c.chunk_id)
                union.append(c)
        for c in dense_chunks:
            if c.chunk_id not in seen:
                seen.add(c.chunk_id)
                union.append(c)

        if not union:
            return []

        if self._rerank_fn is not None:
            try:
                reranked = self._rerank_fn(query, union)
            except Exception:  # noqa: BLE001
                logger.warning(
                    "reranker failed; falling back to score fusion", exc_info=True
                )
                reranked = None
            if reranked is not None:
                return [
                    RetrievalResult(chunk=c, score=s, source_layer="rerank")
                    for c, s in reranked[:top_k]
                ]

        # No reranker → reciprocal-rank fusion of bm25 + dense.
        rrf_scores: dict[str, float] = {}
        for rank, c in enumerate(bm25_chunks):
            rrf_scores[c.chunk_id] = rrf_scores.get(c.chunk_id, 0.0) + 1.0 / (60 + rank)
        for rank, c in enumerate(dense_chunks):
            rrf_scores[c.chunk_id] = rrf_scores.get(c.chunk_id, 0.0) + 1.0 / (60 + rank)

        ordered = sorted(union, key=lambda c: rrf_scores.get(c.chunk_id, 0.0), reverse=True)
        layer = "dense" if dense_chunks else "bm25"
        return [
            RetrievalResult(
                chunk=c,
                score=rrf_scores.get(c.chunk_id, 0.0),
                source_layer=layer,
            )
            for c in ordered[:top_k]
        ]


# ─── BM25 internals ────────────────────────────────────────────────────


class _BM25Index:
    """Thin wrapper over rank_bm25.BM25Okapi.

    Imports the lib lazily so `corpus.py` can be loaded without it
    (e.g. during a build that doesn't need to query)."""

    def __init__(self, texts: Sequence[str]) -> None:
        from rank_bm25 import BM25Okapi  # type: ignore[import-untyped]

        self._tokenized = [_tokenize(t) for t in texts]
        self._bm25 = BM25Okapi(self._tokenized)

    def top_k(self, query: str, k: int) -> list[tuple[int, float]]:
        scores = self._bm25.get_scores(_tokenize(query))
        # Argsort descending; keep only positive scores (a 0 means
        # zero token overlap, not "in the corpus").
        ranked = sorted(
            ((i, float(s)) for i, s in enumerate(scores) if s > 0),
            key=lambda pair: pair[1],
            reverse=True,
        )
        return ranked[:k]


_TOKEN_RE = re.compile(r"[a-z0-9]+")


def _tokenize(text: str) -> list[str]:
    """Lowercase + split on non-alphanum. Same recipe as the matcher
    so query-vs-chunk overlap is calculated consistently across the
    pipeline."""
    return _TOKEN_RE.findall(text.lower())


# ─── cosine helper for the dense layer ─────────────────────────────────


def _cosine_top_k(
    query_vec: Sequence[float],
    chunk_vecs: Sequence[Sequence[float]],
    k: int,
) -> list[tuple[int, float]]:
    q_norm = math.sqrt(sum(x * x for x in query_vec))
    if q_norm == 0:
        return []
    out: list[tuple[int, float]] = []
    for i, v in enumerate(chunk_vecs):
        v_norm = math.sqrt(sum(x * x for x in v))
        if v_norm == 0:
            continue
        dot = sum(a * b for a, b in zip(query_vec, v))
        out.append((i, dot / (q_norm * v_norm)))
    out.sort(key=lambda pair: pair[1], reverse=True)
    return out[:k]
