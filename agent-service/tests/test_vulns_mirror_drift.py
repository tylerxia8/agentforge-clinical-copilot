"""Drift check — agent-service/vulns/ must mirror repo-root vulns/.

The /adversarial visibility page reads the deploy-mirror at
``agent-service/vulns/`` (because the Dockerfile's build context
is agent-service/ — see commit 062f27e). The canonical
Documentation Agent output continues to land at repo-root
``/vulns/``. These two must stay in sync, or the deployed page
shows stale or missing reports.

This test enforces the invariant in CI. It runs in the same
pytest sweep that the eval-gate.yml workflow invokes before the
eval suite. Drift fails the merge.

To recover from a failure:

    rm -rf agent-service/vulns
    cp -r vulns agent-service/vulns

That single command realigns the mirror. Commit the result.

See ``agent-service/vulns/.MIRROR_README.md`` for the sync
workflow doc.
"""

from __future__ import annotations

import hashlib
import os
from pathlib import Path

# A few of the mirror-only metadata files we want the test to
# IGNORE — the canonical repo-root vulns/ doesn't have these
# (because they're the mirror's notes about being a mirror).
MIRROR_ONLY = {".MIRROR_README.md"}


def _agent_service_root() -> Path:
    """agent-service/ — three levels up from this test file."""
    here = Path(__file__).resolve()
    # agent-service/tests/test_vulns_mirror_drift.py → agent-service/
    return here.parents[1]


def _repo_root() -> Path:
    return _agent_service_root().parent


def _hash_file(p: Path) -> str:
    """Content hash of one file, for comparing across the mirror."""
    return hashlib.sha256(p.read_bytes()).hexdigest()


def _scan(dir_: Path) -> dict[str, str]:
    """Map of relative-path → content-hash for every regular file
    under ``dir_`` (recursive). Ignores mirror-only metadata."""
    out: dict[str, str] = {}
    if not dir_.exists():
        return out
    for p in dir_.rglob("*"):
        if not p.is_file():
            continue
        rel = p.relative_to(dir_).as_posix()
        # Skip mirror-only metadata. Compare path-tail since the
        # file is in a subdir we want to descend into.
        if any(part in MIRROR_ONLY for part in p.relative_to(dir_).parts):
            continue
        out[rel] = _hash_file(p)
    return out


def test_agent_service_vulns_mirror_matches_canonical() -> None:
    """``agent-service/vulns/`` must mirror ``<repo-root>/vulns/``
    exactly (modulo the ``.MIRROR_README.md`` metadata file).
    """
    canonical = _scan(_repo_root() / "vulns")
    mirror = _scan(_agent_service_root() / "vulns")

    canonical_keys = set(canonical)
    mirror_keys = set(mirror)

    missing_in_mirror = canonical_keys - mirror_keys
    extra_in_mirror = mirror_keys - canonical_keys

    msg_parts: list[str] = []
    if missing_in_mirror:
        msg_parts.append(
            f"\nFiles in canonical vulns/ but missing from agent-service/vulns/:\n  - "
            + "\n  - ".join(sorted(missing_in_mirror))
        )
    if extra_in_mirror:
        msg_parts.append(
            f"\nFiles in agent-service/vulns/ but missing from canonical vulns/:\n  - "
            + "\n  - ".join(sorted(extra_in_mirror))
        )

    content_diffs = []
    for k in canonical_keys & mirror_keys:
        if canonical[k] != mirror[k]:
            content_diffs.append(k)
    if content_diffs:
        msg_parts.append(
            f"\nFiles whose content differs between canonical and mirror:\n  - "
            + "\n  - ".join(sorted(content_diffs))
        )

    if msg_parts:
        recover = (
            "\n\nTo recover, run from the repo root:\n"
            "    rm -rf agent-service/vulns\n"
            "    cp -r vulns agent-service/vulns\n"
            "\nThen `git add agent-service/vulns` and commit."
        )
        raise AssertionError(
            "agent-service/vulns/ has drifted from canonical vulns/:"
            + "".join(msg_parts)
            + recover
        )
