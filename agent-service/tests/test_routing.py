"""Tests for the supervisor's routing decision.

The W2 ``safe_refusal`` and ``factually_consistent`` rubrics depend
on routing being deterministic — a question that names a guideline
body should always go through evidence retrieval before answering,
not sometimes. These tests lock in that behavior independently of
LangGraph (the routing function takes a plain dict and returns a
plain string).
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

from copilot.workers import HOP_LIMIT, route_decision
from copilot.workers.state import AttachmentInput, EvidenceRecord


def _state(**overrides) -> dict:
    base = {
        "message": "",
        "patient_uuid": "patient-uuid-1",
        "user_id": 1,
        "attachment": None,
        "extractions": [],
        "evidence": [],
        "hops": 0,
    }
    base.update(overrides)
    return base


def _attachment() -> AttachmentInput:
    return {
        "pdf_bytes": b"%PDF-1.4 ...",
        "doc_type": "lab_pdf",
        "document_reference_id": "doc-1234",
    }


def _evidence() -> EvidenceRecord:
    return {"query": "anything", "results": []}


# ─── attachment routing ─────────────────────────────────────────────────


def test_attachment_routes_to_intake_extractor():
    """A queued attachment is the highest-priority signal — even if
    the message also contains evidence triggers, extract first so
    the answer node sees the extraction."""
    s = _state(
        message="What does USPSTF recommend for this patient?",
        attachment=_attachment(),
    )
    assert route_decision(s) == "intake_extractor"


def test_no_attachment_no_evidence_question_routes_to_answer():
    """Plain chart-data questions go straight to the W1 answer
    pathway. Evidence retrieval is opt-in by query content."""
    s = _state(message="What active medications is this patient on?")
    assert route_decision(s) == "answer"


# ─── evidence routing — token presence ──────────────────────────────────


def test_uspstf_mention_routes_to_evidence():
    s = _state(message="What does USPSTF say about HTN screening in adults?")
    assert route_decision(s) == "evidence_retriever"


def test_aafp_mention_routes_to_evidence():
    s = _state(message="What's the AAFP recommendation for statins?")
    assert route_decision(s) == "evidence_retriever"


def test_screening_token_routes_to_evidence():
    s = _state(message="Should we be screening this patient?")
    assert route_decision(s) == "evidence_retriever"


def test_recommend_token_routes_to_evidence():
    s = _state(message="What is recommended for prediabetes?")
    assert route_decision(s) == "evidence_retriever"


def test_indicated_token_routes_to_evidence():
    s = _state(message="Is a statin indicated for this patient?")
    assert route_decision(s) == "evidence_retriever"


def test_question_with_punctuation_still_triggers():
    """uspstf? with a question mark must still match — the tokenizer
    strips trailing punct."""
    s = _state(message="uspstf?")
    assert route_decision(s) == "evidence_retriever"


def test_case_insensitive_token_match():
    s = _state(message="SHOULD I CONSIDER A STATIN")
    assert route_decision(s) == "evidence_retriever"


# ─── evidence routing — already retrieved ──────────────────────────────


def test_evidence_already_retrieved_routes_to_answer():
    """Once evidence has been pulled, subsequent supervisor passes
    must not loop back to evidence_retriever — that would burn
    tokens forever and never call answer."""
    s = _state(
        message="What does USPSTF recommend?",
        evidence=[_evidence()],
        hops=2,  # supervisor already fired once before the worker, once after
    )
    assert route_decision(s) == "answer"


# ─── evidence routing — non-trigger questions ──────────────────────────


def test_chart_summary_routes_to_answer():
    """A plain chart query — no guideline tokens — goes straight to
    answer. Evidence retrieval is the W2 capability, not the default."""
    s = _state(message="Quick read on this patient")
    assert route_decision(s) == "answer"


def test_specific_med_question_routes_to_answer():
    s = _state(message="When did she start lisinopril?")
    assert route_decision(s) == "answer"


def test_empty_message_routes_to_answer():
    """Empty message is degenerate but should not crash; answer
    node will refuse with the W1 'empty message' refusal."""
    assert route_decision(_state(message="")) == "answer"


# ─── hop cap escape ─────────────────────────────────────────────────────


def test_hop_cap_forces_answer_even_when_other_signals_active():
    """The 5-hop cap is the hard escape against routing loops. If
    we somehow end up at hop 5 with both an attachment and evidence
    triggers, the supervisor must give up routing and let answer run."""
    s = _state(
        message="What does USPSTF recommend?",
        attachment=_attachment(),
        hops=HOP_LIMIT,  # at the cap
    )
    assert route_decision(s) == "answer"


def test_hop_cap_minus_one_still_routes_normally():
    """At hop_limit - 1, normal routing still applies (we get one
    more hop before the escape kicks in)."""
    s = _state(
        message="What does USPSTF recommend?",
        hops=HOP_LIMIT - 1,
    )
    assert route_decision(s) == "evidence_retriever"


# ─── invariants ────────────────────────────────────────────────────────


def test_route_decision_is_total_function():
    """Every reachable state shape must produce a valid routing
    label — no exceptions, no None returns. The graph's conditional
    edge will crash if it gets an unmapped value."""
    valid = {"intake_extractor", "evidence_retriever", "answer"}
    for state in [
        _state(),
        _state(message="hello"),
        _state(message="USPSTF"),
        _state(attachment=_attachment()),
        _state(evidence=[_evidence()]),
        _state(hops=99),
        _state(hops=99, attachment=_attachment(), message="USPSTF"),
    ]:
        assert route_decision(state) in valid


def test_evidence_trigger_token_set_is_lowercase():
    """The matcher lowercases input before checking; trigger tokens
    must be lowercase or matches will silently fail."""
    from copilot.workers.routing import EVIDENCE_TRIGGER_TOKENS
    for token in EVIDENCE_TRIGGER_TOKENS:
        assert token == token.lower(), (
            f"trigger token {token!r} must be lowercase"
        )
