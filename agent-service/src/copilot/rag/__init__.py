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

from copilot.rag.corpus import Chunk, load_corpus
from copilot.rag.retriever import RetrievalResult, Retriever

__all__ = ["Chunk", "RetrievalResult", "Retriever", "load_corpus"]
