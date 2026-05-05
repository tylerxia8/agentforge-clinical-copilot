"""Unit tests for the W2 eval runner's discrimination logic.

The runner has two pieces that are pure-functional and easily tested
without firing real HTTP requests:

- :func:`evals.w2.runner.summarize` — turn a list of CaseResults into
  per-rubric and per-category pass rates
- :func:`evals.w2.runner.compare_baseline` — given the current report
  and a baseline, return the list of regressions (each one a string)

The CI gate's hard-gate property (graders inject a regression and
the gate must fail) is what these tests lock in: a 6 percentage-point
drop is a regression; a 4 percentage-point drop is not; a category
that drops below the 90% floor fails regardless of baseline. If a
future refactor breaks any of these invariants the gate would either
miss real regressions or fail spurious PRs — both bad.
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

from evals.w2.runner import CaseResult, compare_baseline, summarize


def _result(case_id: str, category: str, passes: dict[str, bool]) -> CaseResult:
    return CaseResult(
        case_id=case_id,
        category=category,
        description=f"test case {case_id}",
        rubric_results={
            name: {"passed": passed, "reason": "test"}
            for name, passed in passes.items()
        },
        elapsed_seconds=1.0,
    )


# ─── summarize() ────────────────────────────────────────────────────────


def test_summarize_per_rubric_pass_rate():
    results = [
        _result("a", "extraction_lab", {"schema_valid": True, "factually_consistent": True}),
        _result("b", "extraction_lab", {"schema_valid": True, "factually_consistent": False}),
    ]
    report = summarize(results)
    assert report["summary"]["rubric_rates"]["schema_valid"] == 1.0
    assert report["summary"]["rubric_rates"]["factually_consistent"] == 0.5


def test_summarize_per_category_pass_rate():
    """A case "passes" the category iff EVERY rubric on that case
    passed. One failing rubric on a case fails the case for category
    aggregation purposes."""
    results = [
        _result("a", "evidence", {"citation_present": True, "no_phi_in_logs": True}),
        _result("b", "evidence", {"citation_present": False, "no_phi_in_logs": True}),
    ]
    report = summarize(results)
    # Case "a" passed (both rubrics True); case "b" failed (one rubric False).
    assert report["summary"]["category_rates"]["evidence"] == 0.5


def test_summarize_separates_categories():
    results = [
        _result("a", "extraction_lab", {"schema_valid": True}),
        _result("b", "boundary", {"safe_refusal": False}),
    ]
    report = summarize(results)
    rates = report["summary"]["category_rates"]
    assert rates["extraction_lab"] == 1.0
    assert rates["boundary"] == 0.0


def test_summarize_empty_results():
    report = summarize([])
    assert report["summary"]["total_cases"] == 0
    assert report["summary"]["category_rates"] == {}


# ─── compare_baseline() — floor enforcement ────────────────────────────


def _report_with_rates(rates: dict[str, float]) -> dict:
    return {"summary": {"category_rates": rates}}


def test_compare_passes_when_all_at_baseline():
    report = _report_with_rates({"extraction_lab": 1.0, "evidence": 1.0})
    baseline = {"category_rates": {"extraction_lab": 1.0, "evidence": 1.0}}
    assert compare_baseline(report, baseline) == []


def test_compare_fails_when_below_floor():
    """A category at 0.85 is below the 0.90 default floor and must
    fail even if it matches the baseline (the baseline can lie)."""
    report = _report_with_rates({"extraction_lab": 0.85})
    baseline = {"category_rates": {"extraction_lab": 0.85}}
    fails = compare_baseline(report, baseline, floor=0.90, delta=0.05)
    assert len(fails) == 1
    assert "below floor" in fails[0]
    assert "extraction_lab" in fails[0]


def test_compare_passes_when_above_floor():
    report = _report_with_rates({"extraction_lab": 0.92})
    baseline = {"category_rates": {"extraction_lab": 0.92}}
    assert compare_baseline(report, baseline, floor=0.90, delta=0.05) == []


# ─── compare_baseline() — regression delta ─────────────────────────────


def test_compare_fails_on_six_point_regression():
    """6pp drop > 5pp threshold → fail. This is the canonical hard-
    gate scenario: a deploy regresses one category by 6 points and
    the CI must reject it."""
    report = _report_with_rates({"extraction_lab": 0.94})
    baseline = {"category_rates": {"extraction_lab": 1.00}}
    fails = compare_baseline(report, baseline, floor=0.50, delta=0.05)
    assert len(fails) == 1
    assert "regressed" in fails[0]
    assert "100%" in fails[0]
    assert "94%" in fails[0]


def test_compare_passes_on_four_point_regression():
    """4pp drop ≤ 5pp threshold → pass. The gate isn't hair-trigger;
    we tolerate small noise from LLM nondeterminism."""
    report = _report_with_rates({"extraction_lab": 0.96})
    baseline = {"category_rates": {"extraction_lab": 1.00}}
    assert compare_baseline(report, baseline, floor=0.50, delta=0.05) == []


def test_compare_passes_on_exact_threshold():
    """5pp drop is right at threshold — `>` (strict) means pass."""
    report = _report_with_rates({"extraction_lab": 0.95})
    baseline = {"category_rates": {"extraction_lab": 1.00}}
    assert compare_baseline(report, baseline, floor=0.50, delta=0.05) == []


def test_compare_only_flags_regressed_categories():
    """Two categories baselined at 1.0; one drops 6pp (regression),
    one stays. Only the regressed one should be flagged."""
    report = _report_with_rates(
        {"extraction_lab": 0.94, "evidence": 1.00}
    )
    baseline = {
        "category_rates": {"extraction_lab": 1.00, "evidence": 1.00}
    }
    fails = compare_baseline(report, baseline, floor=0.50, delta=0.05)
    assert len(fails) == 1
    assert "extraction_lab" in fails[0]
    assert "evidence" not in " ".join(fails)


def test_compare_passes_on_improvement():
    """A category that IMPROVED above baseline must NOT trigger any
    gate failure (delta is one-directional)."""
    report = _report_with_rates({"extraction_lab": 1.00})
    baseline = {"category_rates": {"extraction_lab": 0.80}}
    assert compare_baseline(report, baseline, floor=0.50, delta=0.05) == []


def test_compare_handles_missing_baseline_category():
    """If a new category landed in the suite without a baseline entry,
    it gets the floor check but no delta check — better than crashing."""
    report = _report_with_rates({"new_category": 1.0, "extraction_lab": 0.97})
    baseline = {"category_rates": {"extraction_lab": 1.00}}
    fails = compare_baseline(report, baseline, floor=0.90, delta=0.05)
    # extraction_lab dropped 3pp (under threshold) and is at 0.97 (above
    # floor) — pass. new_category is at 1.0 (above floor) — pass.
    assert fails == []


def test_compare_combined_floor_and_delta_failures():
    """Both rules can fire on different categories in the same run;
    the gate reports each independently."""
    report = _report_with_rates(
        {"extraction_lab": 0.85, "evidence": 0.50}
    )
    baseline = {
        "category_rates": {"extraction_lab": 0.85, "evidence": 1.00}
    }
    fails = compare_baseline(report, baseline, floor=0.90, delta=0.05)
    # extraction_lab: 0.85 below floor; evidence: 50pp drop AND below floor.
    # Both should appear.
    text = " ".join(fails)
    assert "extraction_lab" in text
    assert "evidence" in text
    # Should report both kinds of issues — at least one floor + one
    # regression entry, possibly both for evidence.
    assert any("below floor" in f for f in fails)
    assert any("regressed" in f for f in fails)


# ─── self-check: the synthetic-regression scenario the PRD describes ──


def test_synthetic_regression_canary():
    """End-to-end: simulate the PRD HARD GATE scenario where a grader
    introduces a regression and the gate must fail.

    Baseline: every category at 1.00.
    Regression: extraction_lab drops to 0.93 because a newly-broken
    citation regex makes one schema_valid case fail.

    Expected: compare_baseline reports the regression."""
    baseline = {"category_rates": {cat: 1.00 for cat in (
        "extraction_lab", "extraction_intake", "evidence", "citation",
        "boundary", "missing_data", "phi_logs", "fabrication",
    )}}
    # 1 of 8 extraction_lab cases failed (broken regex caught one row).
    regressed_report = _report_with_rates({
        "extraction_lab": 0.875,  # 7/8 = 87.5% (12.5pp drop)
        "extraction_intake": 1.00,
        "evidence": 1.00,
        "citation": 1.00,
        "boundary": 1.00,
        "missing_data": 1.00,
        "phi_logs": 1.00,
        "fabrication": 1.00,
    })
    fails = compare_baseline(regressed_report, baseline)
    assert fails, "gate must catch a 12.5pp drop in one category"
    assert any("extraction_lab" in f for f in fails)
