"""W2 hybrid RAG over a small clinical-guideline corpus.

Surface:

- :class:`Chunk` — one corpus entry (text + metadata for citing).
- :func:`load_corpus` — read the seed corpus from disk.
- :class:`Retriever` — query → top-k chunks, with optional dense
  vector and reranker hooks injected at construction time so tests
  (and BM25-only deploys) need no vendor API keys.

See :doc:`W2_ARCHITECTURE.md` §3 for the design and the corpus
sourcing rules.
"""

import logging
import os

from copilot.rag.corpus import Chunk, load_corpus
from copilot.rag.retriever import RetrievalResult, Retriever

__all__ = [
    "Chunk",
    "RetrievalResult",
    "Retriever",
    "load_corpus",
    "make_retriever",
]

_logger = logging.getLogger(__name__)


def make_retriever() -> Retriever:
    """Build the production retriever.

    Loads the seed corpus from disk; wires Voyage embeddings and
    Cohere rerank only if their API keys are present in the env.
    Otherwise falls back to BM25-only — the deploy still works,
    just degraded (architecture §3 documents this fallback).

    The Voyage / Cohere clients are imported lazily so a deploy
    without the ``rag-dense`` extra installed (no ``voyageai``,
    no ``cohere`` packages) still boots cleanly.
    """
    chunks = load_corpus()

    voyage_key = os.environ.get("VOYAGE_API_KEY")
    cohere_key = os.environ.get("COHERE_API_KEY")

    embed_fn = None
    chunk_embeddings = None
    if voyage_key:
        try:
            import voyageai  # type: ignore[import-untyped]

            client = voyageai.Client(api_key=voyage_key)

            def embed_fn(query: str):
                resp = client.embed([query], model="voyage-3", input_type="query")
                return resp.embeddings[0]

            corpus_resp = client.embed(
                [c.text for c in chunks],
                model="voyage-3",
                input_type="document",
            )
            chunk_embeddings = corpus_resp.embeddings
            _logger.info("Voyage dense layer enabled (%d chunk embeddings)", len(chunks))
        except Exception:  # noqa: BLE001 — degrade to BM25-only
            _logger.warning(
                "Voyage embedding init failed; running BM25-only", exc_info=True
            )
            embed_fn = None
            chunk_embeddings = None
    else:
        _logger.info("VOYAGE_API_KEY not set — BM25-only retrieval")

    rerank_fn = None
    if cohere_key:
        try:
            import cohere  # type: ignore[import-untyped]

            cohere_client = cohere.Client(api_key=cohere_key)

            def rerank_fn(query: str, candidates: list):
                resp = cohere_client.rerank(
                    model="rerank-3.5",
                    query=query,
                    documents=[c.text for c in candidates],
                    top_n=min(5, len(candidates)),
                )
                return [(candidates[r.index], r.relevance_score) for r in resp.results]

            _logger.info("Cohere rerank layer enabled")
        except Exception:  # noqa: BLE001
            _logger.warning(
                "Cohere init failed; falling back to RRF fusion", exc_info=True
            )
            rerank_fn = None
    else:
        _logger.info("COHERE_API_KEY not set — RRF fusion fallback")

    return Retriever(
        chunks,
        embed_fn=embed_fn,
        chunk_embeddings=chunk_embeddings,
        rerank_fn=rerank_fn,
    )
