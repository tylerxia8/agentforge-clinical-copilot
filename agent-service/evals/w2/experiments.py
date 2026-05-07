"""A/B experiment harness (stage 5 of the prod-evals cookbook).

Workflow the operator runs:

    # Capture variant A (current prod config)
    python -m evals.w2.runner --record exp/baseline.jsonl

    # Switch one knob (e.g. set MODEL_ID=claude-haiku-4-5-20251001
    # in the deployed agent's env, redeploy)

    # Capture variant B
    python -m evals.w2.runner --record exp/haiku.jsonl

    # Diff
    python -m evals.w2.experiments \
        --a exp/baseline.jsonl   --a-name "Sonnet 4.6" \
        --b exp/haiku.jsonl      --b-name "Haiku 4.5"

The diff renders three views:

1. **Per-category pass-rate delta** — which categories a variant
   wins / loses / ties on, with the percentage-point gap.
2. **Per-case verdict diff** — exactly which cases flipped between
   A and B, so operator review goes straight to the interesting
   rows. (No "case 17 of 63 flipped" — it's "boundary_named_other_patient
   went from PASS to FAIL".)
3. **Cost / latency proxy** — sum of recorded ``elapsed_seconds``
   per variant. Doesn't include $ since we don't currently record
   cost on the response (Langfuse holds the authoritative number);
   wall-clock is the cheap-but-useful directional signal.

Single-file, small dependency surface: this module imports the
existing replay/grading bits and produces a markdown report.
"""

from __future__ import annotations

import argparse
import json
import sys
from collections import defaultdict
from pathlib import Path
from typing import Any

from evals.w2 import cases as cases_module
from evals.w2.replay import load_recorded_responses
from evals.w2.runner import _grade_case, summarize


def diff_recordings(
    a_path: Path,
    a_name: str,
    b_path: Path,
    b_name: str,
) -> dict[str, Any]:
    """Compare two recorded eval runs. Returns a dict with three
    sub-reports: per-category, per-case, and aggregate timing.

    Both recordings must have been produced from the same case
    manifest — case_ids are matched by exact equality. Cases present
    in only one recording get a clear "missing on side X" marker
    rather than being silently dropped."""
    cases = {c.case_id: c for c in cases_module.all_cases()}
    a_recs = load_recorded_responses(a_path)
    b_recs = load_recorded_responses(b_path)

    a_results = _grade_recordings(cases, a_recs)
    b_results = _grade_recordings(cases, b_recs)

    a_summary = summarize(a_results)["summary"]
    b_summary = summarize(b_results)["summary"]

    cat_diff: list[dict[str, Any]] = []
    cats = sorted(set(a_summary["category_rates"]) | set(b_summary["category_rates"]))
    for cat in cats:
        ar = a_summary["category_rates"].get(cat, 0.0)
        br = b_summary["category_rates"].get(cat, 0.0)
        cat_diff.append({
            "category": cat,
            "a_rate": ar,
            "b_rate": br,
            "delta_pp": round((br - ar) * 100, 1),
        })

    rubric_diff: list[dict[str, Any]] = []
    rubrics = sorted(set(a_summary["rubric_rates"]) | set(b_summary["rubric_rates"]))
    for r in rubrics:
        ar = a_summary["rubric_rates"].get(r, 0.0)
        br = b_summary["rubric_rates"].get(r, 0.0)
        rubric_diff.append({
            "rubric": r,
            "a_rate": ar,
            "b_rate": br,
            "delta_pp": round((br - ar) * 100, 1),
        })

    case_flips: list[dict[str, Any]] = []
    a_pass = {r.case_id: _all_passed(r) for r in a_results}
    b_pass = {r.case_id: _all_passed(r) for r in b_results}
    for cid in sorted(set(a_pass) | set(b_pass)):
        ap = a_pass.get(cid)
        bp = b_pass.get(cid)
        if ap != bp:
            case_flips.append({
                "case_id": cid,
                "a": _verdict(ap),
                "b": _verdict(bp),
            })

    a_seconds = sum(r.elapsed_seconds for r in a_results)
    b_seconds = sum(r.elapsed_seconds for r in b_results)

    return {
        "a": {"name": a_name, "path": str(a_path), "n": len(a_recs)},
        "b": {"name": b_name, "path": str(b_path), "n": len(b_recs)},
        "category_diff": cat_diff,
        "rubric_diff": rubric_diff,
        "case_flips": case_flips,
        "timing": {
            "a_total_seconds": round(a_seconds, 2),
            "b_total_seconds": round(b_seconds, 2),
            "delta_seconds": round(b_seconds - a_seconds, 2),
        },
    }


def _grade_recordings(cases: dict, recordings: dict) -> list:
    """Apply each case's rubrics to its recorded response."""
    out = []
    for cid, case in cases.items():
        rec = recordings.get(cid)
        if rec is None:
            # Mark the case as missing-on-this-side so the diff sees
            # it. Same shape as runner._replay_one's missing-recording
            # branch — keeps the summarize() math honest.
            out.append(_grade_case_missing(case))
            continue
        out.append(_grade_case(case, rec.response, rec.elapsed_seconds))
    return out


def _grade_case_missing(case):
    from evals.w2.runner import CaseResult
    rubric_results = {
        name: {"passed": False, "reason": "no recording"}
        for name in case.rubrics
    }
    return CaseResult(
        case_id=case.case_id,
        category=case.category,
        description=case.description,
        rubric_results=rubric_results,
        elapsed_seconds=0.0,
    )


def _all_passed(result) -> bool:
    return all(o["passed"] for o in result.rubric_results.values())


def _verdict(p: bool | None) -> str:
    if p is None:
        return "—"  # missing on this side
    return "PASS" if p else "FAIL"


def render_markdown(diff: dict[str, Any]) -> str:
    a = diff["a"]
    b = diff["b"]
    out: list[str] = []
    out.append("# W2 eval A/B comparison\n")
    out.append(f"- **A** = {a['name']} (`{a['path']}`, {a['n']} recordings)")
    out.append(f"- **B** = {b['name']} (`{b['path']}`, {b['n']} recordings)\n")

    out.append("## Per-category pass-rate Δ\n")
    out.append("| Category | A | B | Δ (pp) |")
    out.append("|---|---|---|---|")
    for row in diff["category_diff"]:
        arrow = _arrow(row["delta_pp"])
        out.append(
            f"| {row['category']} | {row['a_rate']:.0%} | {row['b_rate']:.0%} | "
            f"{row['delta_pp']:+.1f} {arrow} |"
        )
    out.append("")

    out.append("## Per-rubric pass-rate Δ\n")
    out.append("| Rubric | A | B | Δ (pp) |")
    out.append("|---|---|---|---|")
    for row in diff["rubric_diff"]:
        arrow = _arrow(row["delta_pp"])
        out.append(
            f"| {row['rubric']} | {row['a_rate']:.0%} | {row['b_rate']:.0%} | "
            f"{row['delta_pp']:+.1f} {arrow} |"
        )
    out.append("")

    flips = diff["case_flips"]
    if flips:
        out.append(f"## Case-level flips ({len(flips)})\n")
        out.append("| Case | A | B |")
        out.append("|---|---|---|")
        for row in flips:
            out.append(f"| {row['case_id']} | {row['a']} | {row['b']} |")
        out.append("")
    else:
        out.append("## Case-level flips\n_None — A and B agreed on every case._\n")

    t = diff["timing"]
    out.append("## Wall-clock\n")
    out.append(
        f"- A total: **{t['a_total_seconds']}s** · B total: **{t['b_total_seconds']}s** "
        f"· Δ: **{t['delta_seconds']:+.2f}s**"
    )

    return "\n".join(out)


def _arrow(delta_pp: float) -> str:
    if delta_pp > 0.5:
        return "↑"
    if delta_pp < -0.5:
        return "↓"
    return "·"


def main() -> int:
    parser = argparse.ArgumentParser(prog="evals.w2.experiments")
    parser.add_argument("--a", type=Path, required=True, help="Recording A (baseline / control).")
    parser.add_argument("--b", type=Path, required=True, help="Recording B (variant / treatment).")
    parser.add_argument("--a-name", default="A", help="Display name for variant A.")
    parser.add_argument("--b-name", default="B", help="Display name for variant B.")
    parser.add_argument(
        "--json",
        type=Path,
        help="Also write the diff as JSON to this path.",
    )
    args = parser.parse_args()

    diff = diff_recordings(args.a, args.a_name, args.b, args.b_name)
    print(render_markdown(diff))
    if args.json:
        args.json.write_text(json.dumps(diff, indent=2))
        print(f"\n# wrote {args.json}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
