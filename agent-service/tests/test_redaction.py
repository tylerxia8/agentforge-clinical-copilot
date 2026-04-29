from __future__ import annotations

import os

os.environ.setdefault("ANTHROPIC_API_KEY", "test")
os.environ.setdefault("OPENEMR_BASE_URL", "http://localhost:8080")
os.environ.setdefault("OPENEMR_OAUTH_CLIENT_ID", "test")
os.environ.setdefault("OPENEMR_OAUTH_CLIENT_SECRET", "test")
os.environ.setdefault("AGENT_SHARED_SECRET", "test-secret-test-secret")

from copilot.redaction.tokens import TokenMap


def test_tokenizes_known_phi_fields():
    tm = TokenMap()
    out = tm.tokenize_dict({
        "fname": "Sarah",
        "lname": "Klein",
        "ss": "111-22-3333",
        "DOB": "1961-03-04",
        "diagnosis": "diabetes",  # not PHI — passthrough
    })
    assert out["fname"].startswith("[PT_NAME_")
    assert out["lname"].startswith("[PT_NAME_")
    assert out["ss"].startswith("[SSN_")
    assert out["DOB"].startswith("[AGE_")
    assert out["diagnosis"] == "diabetes"


def test_same_value_gets_same_token_within_turn():
    tm = TokenMap()
    a = tm.tokenize_dict({"fname": "Sarah"})
    b = tm.tokenize_dict({"fname": "Sarah"})
    assert a["fname"] == b["fname"]


def test_rehydrate_restores_original_values():
    tm = TokenMap()
    tm.tokenize_dict({"fname": "Sarah", "lname": "Klein"})
    rehydrated = tm.rehydrate("Hello [PT_NAME_1] [PT_NAME_2], welcome.")
    assert "Sarah" in rehydrated and "Klein" in rehydrated


def test_unknown_tokens_pass_through():
    tm = TokenMap()
    assert tm.rehydrate("[PT_NAME_99] is unknown") == "[PT_NAME_99] is unknown"
