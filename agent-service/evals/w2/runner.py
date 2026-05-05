"""W2 eval runner.

Fires every case in :mod:`evals.w2.cases`, applies the per-case
rubric checkers, and emits two artifacts:

- a markdown summary table to stdout (human-readable)
- a JSON results blob (machine-readable; CI consumes this)

The CI gate reads the JSON, compares against ``baseline.json``, and
fails the build if any rubric category drops by more than the
allowed margin (default 5 percentage points) or below the absolute
floor (default 0.90).

CLI:

::

    python -m evals.w2.runner                       # markdown to stdout
    python -m evals.w2.runner --json results.json   # also write JSON
    python -m evals.w2.runner --compare baseline.json     # exit nonzero on regression
    python -m evals.w2.runner --save-baseline baseline.json   # snapshot current pass rates
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from collections import defaultdict
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

from evals.w2 import cases as cases_module
from evals.w2.cases import CATEGORY_TARGETS, W2Case

DEFAULT_FLOOR = 0.90
"""Absolute pass-rate floor enforced by the CI gate. A category that
drops below this fails the build regardless of regression delta."""

DEFAULT_REGRESSION_DELTA = 0.05
"""Allowed drop vs. baseline before the CI gate fails. The PRD says
"more than 5%" — we treat 5pp as the threshold."""


@dataclass
class CaseResult:
    case_id: str
    category: str
    description: str
    rubric_results: dict[str, dict[str, Any]]  # rubric_name -> {passed, reason}
    elapsed_seconds: float


def run_case(case: W2Case) -> CaseResult:
    started = time.time()
    try:
        response = case.fire()
    except Exception as exc:  # noqa: BLE001 — never crash the runner
        response = {"_status": -1, "_error": f"{type(exc).__name__}: {exc}"}
    elapsed = time.time() - started

    rubric_results: dict[str, dict[str, Any]] = {}
    for name, checker in case.rubrics.items():
        try:
            passed, reason = checker(response)
        except Exception as exc:  # noqa: BLE001
            passed, reason = False, f"checker raised: {type(exc).__name__}: {exc}"
        rubric_results[name] = {"passed": bool(passed), "reason": reason}

    return CaseResult(
        case_id=case.case_id,
        category=case.category,
        description=case.description,
        rubric_results=rubric_results,
        elapsed_seconds=round(elapsed, 2),
    )


def summarize(results: list[CaseResult]) -> dict[str, Any]:
    """Aggregate per-rubric and per-category pass rates."""
    rubric_totals: dict[str, dict[str, int]] = defaultdict(
        lambda: {"passed": 0, "total": 0}
    )
    category_totals: dict[str, dict[str, int]] = defaultdict(
        lambda: {"passed": 0, "total": 0}
    )

    for result in results:
        all_passed = True
        for rubric_name, outcome in result.rubric_results.items():
            rubric_totals[rubric_name]["total"] += 1
            if outcome["passed"]:
                rubric_totals[rubric_name]["passed"] += 1
            else:
                all_passed = False
        category_totals[result.category]["total"] += 1
        if all_passed:
            category_totals[result.category]["passed"] += 1

    rubric_rates = {
        name: round(t["passed"] / t["total"], 4) if t["total"] else 0.0
        for name, t in rubric_totals.items()
    }
    category_rates = {
        cat: round(t["passed"] / t["total"], 4) if t["total"] else 0.0
        for cat, t in category_totals.items()
    }

    target_status = {
        cat: {
            "current": category_totals.get(cat, {}).get("total", 0),
            "target": target,
            "shortfall": max(0, target - category_totals.get(cat, {}).get("total", 0)),
        }
        for cat, target in CATEGORY_TARGETS.items()
    }

    return {
        "summary": {
            "total_cases": len(results),
            "rubric_rates": rubric_rates,
            "category_rates": category_rates,
            "rubric_totals": dict(rubric_totals),
            "category_totals": dict(category_totals),
            "category_targets": target_status,
        },
        "results": [asdict(r) for r in results],
    }


def render_markdown(report: dict[str, Any]) -> str:
    summary = report["summary"]
    out: list[str] = []
    out.append("# W2 eval results\n")
    out.append(f"_Total cases: {summary['total_cases']}_\n")

    out.append("## Rubric pass rates\n")
    out.append("| Rubric | Passed | Total | Rate |")
    out.append("|---|---|---|---|")
    for name, totals in summary["rubric_totals"].items():
        rate = summary["rubric_rates"][name]
        out.append(f"| {name} | {totals['passed']} | {totals['total']} | {rate:.0%} |")
    out.append("")

    out.append("## Category pass rates\n")
    out.append("| Category | Passed | Total | Rate | Target |")
    out.append("|---|---|---|---|---|")
    for cat, totals in summary["category_totals"].items():
        rate = summary["category_rates"][cat]
        target = summary["category_targets"].get(cat, {}).get("target", "?")
        marker = " ⚠" if totals["total"] < (target if isinstance(target, int) else 0) else ""
        out.append(
            f"| {cat} | {totals['passed']} | {totals['total']} | {rate:.0%} | "
            f"{totals['total']}/{target}{marker} |"
        )
    out.append("")

    fails: list[dict] = []
    for r in report["results"]:
        for rname, ro in r["rubric_results"].items():
            if not ro["passed"]:
                fails.append({"case": r["case_id"], "rubric": rname, "reason": ro["reason"]})
    if fails:
        out.append("## Failures\n")
        out.append("| Case | Rubric | Reason |")
        out.append("|---|---|---|")
        for f in fails:
            out.append(f"| {f['case']} | {f['rubric']} | {f['reason']} |")
        out.append("")
    else:
        out.append("✅ All rubric checks passed.\n")

    return "\n".join(out)


def compare_baseline(
    report: dict[str, Any],
    baseline: dict[str, Any],
    *,
    floor: float = DEFAULT_FLOOR,
    delta: float = DEFAULT_REGRESSION_DELTA,
) -> list[str]:
    """Return a list of failure reasons. Empty list = pass.

    A category fails if EITHER:
      - its current rate is below ``floor``
      - its current rate is more than ``delta`` worse than the
        baseline rate for the same category
    """
    fails: list[str] = []
    current = report["summary"]["category_rates"]
    base = baseline.get("category_rates", {})

    for category, rate in current.items():
        if rate < floor:
            fails.append(
                f"{category}: rate {rate:.0%} below floor {floor:.0%}"
            )
        baseline_rate = base.get(category)
        if baseline_rate is not None and (baseline_rate - rate) > delta:
            fails.append(
                f"{category}: regressed from {baseline_rate:.0%} to {rate:.0%} "
                f"(>{delta:.0%} drop)"
            )
    return fails


# ─── CLI ───────────────────────────────────────────────────────────────


def main() -> int:
    parser = argparse.ArgumentParser(prog="evals.w2.runner")
    parser.add_argument(
        "--json",
        type=Path,
        help="Write the full JSON report to this path (also prints markdown).",
    )
    parser.add_argument(
        "--compare",
        type=Path,
        help="Path to a baseline.json. Exit nonzero if any category "
             "regresses by more than --regression-delta or drops below --floor.",
    )
    parser.add_argument(
        "--save-baseline",
        type=Path,
        help="Write the current pass rates as the new baseline.json.",
    )
    parser.add_argument(
        "--floor", type=float, default=DEFAULT_FLOOR,
        help="Absolute pass-rate floor (default 0.90).",
    )
    parser.add_argument(
        "--regression-delta", type=float, default=DEFAULT_REGRESSION_DELTA,
        help="Allowed drop vs baseline (default 0.05 = 5 percentage points).",
    )
    args = parser.parse_args()

    cases = cases_module.all_cases()
    print(f"# running {len(cases)} W2 case(s)…", file=sys.stderr)
    results = [run_case(c) for c in cases]
    report = summarize(results)

    print(render_markdown(report))

    if args.json:
        args.json.write_text(json.dumps(report, indent=2))
        print(f"# wrote {args.json}", file=sys.stderr)

    if args.save_baseline:
        baseline_blob = {
            "category_rates": report["summary"]["category_rates"],
            "rubric_rates": report["summary"]["rubric_rates"],
            "saved_at": int(time.time()),
        }
        args.save_baseline.write_text(json.dumps(baseline_blob, indent=2))
        print(f"# wrote baseline to {args.save_baseline}", file=sys.stderr)

    if args.compare:
        try:
            baseline = json.loads(args.compare.read_text())
        except FileNotFoundError:
            print(
                f"# baseline {args.compare} not found — treating as PASS "
                "(first run on this branch)",
                file=sys.stderr,
            )
            return 0
        fails = compare_baseline(
            report, baseline, floor=args.floor, delta=args.regression_delta
        )
        if fails:
            print("\n# REGRESSION DETECTED:", file=sys.stderr)
            for f in fails:
                print(f"  - {f}", file=sys.stderr)
            return 1
        print("\n# no regressions vs baseline.", file=sys.stderr)

    return 0


if __name__ == "__main__":
    sys.exit(main())
