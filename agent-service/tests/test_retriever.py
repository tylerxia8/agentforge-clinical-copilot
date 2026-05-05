"""Tests for the hybrid retriever.

The retriever is built around three injectable layers (BM25 always
on, dense + rerank optional). These tests exercise each layer
independently using stub embed_fn / rerank_fn so no vendor API keys
are needed. The seed corpus on disk is used for the BM25 path.
"""

from __future__ import annotations

import os

os.environ.setdefault("ANTHROPIC_API_KEY", "test")
os.environ.setdefault("OPENEMR_BASE_URL", "http://localhost:8080")
os.environ.setdefault("OPENEMR_OAUTH_CLIENT_ID", "test")
os.environ.setdefault("OPENEMR_OAUTH_CLIENT_SECRET", "test")
os.environ.setdefault("OPENEMR_SERVICE_USERNAME", "test")
os.environ.setdefault("OPENEMR_SERVICE_PASSWORD", "test")
os.environ.setdefault("AGENT_SHARED_SECRET", "test-secret-test-secret")

import pytest

from copilot.rag import Chunk, Retriever, load_corpus


# ─── corpus loader ──────────────────────────────────────────────────────


def test_seed_corpus_loads_without_error():
    chunks = load_corpus()
    assert len(chunks) >= 5  # at least the seed files we shipped
    # Every chunk has a non-empty text block and a unique chunk_id
    seen_ids = set()
    for chunk in chunks:
        assert chunk.text.strip()
        assert chunk.chunk_id not in seen_ids
        seen_ids.add(chunk.chunk_id)


def test_chunk_id_validation_rejects_spaces_and_slashes():
    """chunk_id must survive being inlined as ``Guideline#<chunk_id>``
    in the agent's response. The verifier regex terminates on `#` and
    spaces; we forbid those chars at corpus-load time."""
    with pytest.raises(ValueError, match="forbidden"):
        Chunk(
            chunk_id="bad id with space",
            title="x", section="x", source="x",
            source_url="x", year=2024, text="t",
        )
    with pytest.raises(ValueError, match="forbidden"):
        Chunk(
            chunk_id="bad/id",
            title="x", section="x", source="x",
            source_url="x", year=2024, text="t",
        )


def test_chunk_empty_text_rejected():
    with pytest.raises(ValueError, match="empty text"):
        Chunk(
            chunk_id="ok-id",
            title="x", section="x", source="x",
            source_url="x", year=2024, text="   ",
        )


# ─── BM25-only retrieval ───────────────────────────────────────────────


def test_bm25_finds_topical_match():
    """A query with strong lexical overlap to one chunk should
    surface that chunk first."""
    chunks = load_corpus()
    r = Retriever(chunks)
    results = r.retrieve("hypertension blood pressure screening adults", top_k=3)
    assert len(results) >= 1
    assert results[0].chunk.chunk_id == "uspstf-htn-screen-2021"
    assert results[0].source_layer == "bm25"


def test_bm25_query_about_diabetes_prefers_diabetes_chunks():
    chunks = load_corpus()
    r = Retriever(chunks)
    results = r.retrieve("type 2 diabetes screening overweight adults", top_k=3)
    top_ids = {res.chunk.chunk_id for res in results}
    assert "uspstf-prediabetes-t2dm-screen-2021" in top_ids


def test_bm25_no_overlap_returns_empty():
    """A query with zero token overlap with any chunk should return
    nothing — better than confidently returning a wrong chunk."""
    chunks = load_corpus()
    r = Retriever(chunks)
    results = r.retrieve("cooking pasta marinara", top_k=5)
    assert results == []


def test_empty_query_returns_empty():
    chunks = load_corpus()
    r = Retriever(chunks)
    assert r.retrieve("", top_k=5) == []
    assert r.retrieve("   ", top_k=5) == []


def test_top_k_bounds_result_count():
    chunks = load_corpus()
    r = Retriever(chunks)
    results = r.retrieve("vaccine immunization adult", top_k=2)
    assert len(results) <= 2


def test_top_k_zero_returns_empty():
    chunks = load_corpus()
    r = Retriever(chunks)
    assert r.retrieve("anything", top_k=0) == []


# ─── construction-time guards ──────────────────────────────────────────


def test_retriever_rejects_empty_corpus():
    with pytest.raises(ValueError, match="non-empty corpus"):
        Retriever([])


def test_embed_fn_and_embeddings_must_be_paired():
    chunks = [Chunk(
        chunk_id="x", title="x", section="x", source="x",
        source_url="x", year=2024, text="hello world",
    )]
    with pytest.raises(ValueError, match="must be provided together"):
        Retriever(chunks, embed_fn=lambda q: [0.1, 0.2])


def test_embeddings_length_must_match_corpus():
    chunks = [
        Chunk(chunk_id="a", title="a", section="x", source="x",
              source_url="x", year=2024, text="alpha"),
        Chunk(chunk_id="b", title="b", section="x", source="x",
              source_url="x", year=2024, text="beta"),
    ]
    with pytest.raises(ValueError, match="length"):
        Retriever(
            chunks,
            embed_fn=lambda q: [1.0, 0.0],
            chunk_embeddings=[[1.0, 0.0]],  # only 1 vec for 2 chunks
        )


# ─── dense layer (with stub embed_fn) ──────────────────────────────────


def _stub_embed(target_chunk_id: str, all_chunks):
    """Build an embed_fn + embeddings such that the query vector
    perfectly matches `target_chunk_id` — useful for asserting that
    the dense layer can flip a result that BM25 missed."""
    chunk_vecs: list[list[float]] = []
    target_vec = [1.0, 0.0]
    other_vec = [0.0, 1.0]
    for c in all_chunks:
        chunk_vecs.append(target_vec if c.chunk_id == target_chunk_id else other_vec)

    def embed_fn(query: str):
        return target_vec

    return embed_fn, chunk_vecs


def test_dense_layer_brings_in_chunk_bm25_missed():
    chunks = load_corpus()
    target = "ada-foot-eye-exam-cadence-2024"
    embed_fn, vecs = _stub_embed(target, chunks)
    r = Retriever(chunks, embed_fn=embed_fn, chunk_embeddings=vecs)
    # Query that has no lexical overlap with the foot-eye-exam chunk
    # — BM25 returns nothing, dense pulls the target in via its
    # cosine = 1.0 with the query vector.
    results = r.retrieve("blue green orange purple cyan", top_k=3)
    top_ids = [res.chunk.chunk_id for res in results]
    assert target in top_ids


def test_dense_layer_failure_falls_back_to_bm25():
    """A failing dense embedding call must NOT take down the query
    — log + degrade to BM25-only is the contract."""
    chunks = load_corpus()

    def boom(_query: str):
        raise RuntimeError("voyage api 429")

    bad_embed = boom
    vecs = [[1.0, 0.0]] * len(chunks)
    r = Retriever(chunks, embed_fn=bad_embed, chunk_embeddings=vecs)
    results = r.retrieve("hypertension screening", top_k=3)
    # BM25 still finds the HTN chunk
    assert any(
        res.chunk.chunk_id == "uspstf-htn-screen-2021" for res in results
    )


# ─── reranker layer (with stub rerank_fn) ──────────────────────────────


def test_reranker_can_reorder_results():
    chunks = load_corpus()

    # Identify the diabetes screen chunk so we can promote it.
    target = "uspstf-prediabetes-t2dm-screen-2021"

    def stub_rerank(query: str, candidates: list):
        # Promote the target chunk to position 0 with score 1.0,
        # demote everything else to score 0.0 in their existing order.
        ordered = [c for c in candidates if c.chunk_id == target] + \
                  [c for c in candidates if c.chunk_id != target]
        return [(c, 1.0 if c.chunk_id == target else 0.0) for c in ordered]

    r = Retriever(chunks, rerank_fn=stub_rerank)
    # A query that BM25 ranks something else first
    results = r.retrieve("hypertension diabetes statin", top_k=3)
    assert results[0].chunk.chunk_id == target
    assert results[0].source_layer == "rerank"


def test_reranker_failure_falls_back_to_fusion():
    """A failing reranker must NOT take down the query — log +
    degrade to RRF over BM25(+dense) is the contract."""
    chunks = load_corpus()

    def boom(_query, _cands):
        raise RuntimeError("cohere api timeout")

    r = Retriever(chunks, rerank_fn=boom)
    results = r.retrieve("hypertension screening", top_k=3)
    # Some BM25 result still surfaces
    assert results, "rerank failure should not zero out results"
    assert all(r.source_layer == "bm25" for r in results)
