"""Verifies the verification layer — the property the architecture
spends a chapter defending. These tests should NEVER regress."""

from __future__ import annotations

import os

os.environ.setdefault("ANTHROPIC_API_KEY", "test")
os.environ.setdefault("OPENEMR_BASE_URL", "http://localhost:8080")
os.environ.setdefault("OPENEMR_OAUTH_CLIENT_ID", "test")
os.environ.setdefault("OPENEMR_OAUTH_CLIENT_SECRET", "test")
os.environ.setdefault("AGENT_SHARED_SECRET", "test-secret-test-secret")

from copilot.verification import rules as domain_rules
from copilot.verification.structural import verify_structural


def _tr(rows):
    return [{"rows": rows}]


# --- structural ---

def test_structural_passes_with_cited_claim():
    text = "She is on lisinopril 20 mg daily [prescriptions#244]."
    tr = _tr([{"id": "prescriptions#244", "active": True, "end_date": None}])
    assert verify_structural(text, tr).passed


def test_structural_fails_when_citation_invented():
    text = "She is on lisinopril 20 mg daily [prescriptions#999]."
    tr = _tr([{"id": "prescriptions#244"}])
    v = verify_structural(text, tr)
    assert not v.passed and "999" in v.reason


def test_structural_fails_uncited_clinical_claim():
    text = "She had a recent ED visit for chest pain."
    tr = _tr([])
    v = verify_structural(text, tr)
    assert not v.passed and "no tool row" in v.reason


def test_structural_passes_non_clinical_prose():
    text = "Hi! How can I help with this chart?"
    assert verify_structural(text, _tr([])).passed


# --- domain rules ---

def test_vitals_rule_requires_unit_or_explicit_disclaimer():
    text = "Her temperature was 37.2 [form_vitals#1]."
    tr = _tr([{"id": "form_vitals#1", "unit_verified": False}])
    assert not domain_rules.vitals_unit_rule(text, tr).passed


def test_vitals_rule_passes_with_units_not_recorded():
    text = "Her temperature was 37.2 (units not recorded) [form_vitals#1]."
    tr = _tr([{"id": "form_vitals#1", "unit_verified": False}])
    assert domain_rules.vitals_unit_rule(text, tr).passed


def test_vitals_rule_passes_when_unit_verified():
    text = "Her temperature was 37.2 [form_vitals#1]."
    tr = _tr([{"id": "form_vitals#1", "unit_verified": True}])
    assert domain_rules.vitals_unit_rule(text, tr).passed


def test_med_active_rule_rejects_currently_on_for_inactive_med():
    text = "She is currently on metformin 500 BID [prescriptions#7]."
    tr = _tr([{"id": "prescriptions#7", "active": False, "end_date": None}])
    assert not domain_rules.med_active_state_rule(text, tr).passed


def test_med_active_rule_passes_for_active_med():
    text = "She is currently on metformin 500 BID [prescriptions#7]."
    tr = _tr([{"id": "prescriptions#7", "active": True, "end_date": None}])
    assert domain_rules.med_active_state_rule(text, tr).passed
