"""Smoke runner for the cohort 5 W2 asset pack.

Posts every file under ``fixtures/cohort-test-pack/`` at the deployed
agent service's ``/agent/extract`` endpoint and prints a per-file
pass/fail line. Exits non-zero if any file failed to parse cleanly.

Reads ``AGENT_URL`` and ``AGENT_SHARED_SECRET`` from the
environment — same vars the W2 eval suite uses, so a
``railway run --service copilot-agent`` invocation Just Works.

Usage:

    cd agent-service
    AGENT_URL=https://copilot-agent-production-ba87.up.railway.app \
    AGENT_SHARED_SECRET=... \
    python -m fixtures.cohort_smoke

The runner does NOT write anything back to the chart — it only
checks that the extraction endpoint accepts each format and returns
a structurally valid response. Full writeback testing is part of
the W2 eval suite.
"""

from __future__ import annotations

import argparse
import base64
import hashlib
import hmac
import json
import mimetypes
import os
import secrets
import sys
import time
import urllib.error
import urllib.request
import uuid
from pathlib import Path

ASSET_DIR = Path(__file__).parent / "cohort-test-pack"
DEFAULT_AGENT_URL = "https://copilot-agent-production-ba87.up.railway.app"

# Patient UUID we send the test pack on behalf of. The cohort
# fixtures use synthetic identities (chen, whitaker, ...) but the
# extraction endpoint only needs *some* authenticated chart
# context — we pick Farrah from the AgentForge demo seed because
# every code path expects her to exist.
DEFAULT_PATIENT_UUID = "a1ab5594-20c8-4363-be30-75d287be735d"


# (suffix-or-stem-pattern, doc_type, mime) — first match wins.
DISPATCH = [
    (".hl7",   "hl7v2",          "text/plain"),
    (".docx",  "docx_referral",  "application/vnd.openxmlformats-officedocument.wordprocessingml.document"),
    (".xlsx",  "xlsx_workbook",  "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"),
    (".tiff",  "tiff_fax",       "image/tiff"),
    (".tif",   "tiff_fax",       "image/tiff"),
]


def mint_token(secret: str, patient_uuid: str) -> str:
    payload = {
        "user_id": 1,
        "patient_uuid": patient_uuid,
        "encounter_uuid": None,
        "issued_at": int(time.time()),
        "nonce": secrets.token_hex(8),
    }
    payload_b64 = (
        base64.urlsafe_b64encode(json.dumps(payload, separators=(",", ":")).encode())
        .rstrip(b"=").decode()
    )
    sig = hmac.new(secret.encode(), payload_b64.encode(), hashlib.sha256).hexdigest()
    return f"{payload_b64}.{sig}"


def build_multipart(
    *, doc_type: str, document_reference_id: str, file_path: Path, mime: str,
) -> tuple[bytes, str]:
    boundary = "----cohort-smoke-" + uuid.uuid4().hex
    body_parts: list[bytes] = []
    for k, v in (("doc_type", doc_type), ("document_reference_id", document_reference_id)):
        body_parts.append(
            f"--{boundary}\r\nContent-Disposition: form-data; name=\"{k}\"\r\n\r\n{v}\r\n".encode()
        )
    body_parts.append(
        f"--{boundary}\r\nContent-Disposition: form-data; name=\"file\"; "
        f"filename=\"{file_path.name}\"\r\nContent-Type: {mime}\r\n\r\n".encode()
    )
    body_parts.append(file_path.read_bytes())
    body_parts.append(f"\r\n--{boundary}--\r\n".encode())
    return b"".join(body_parts), boundary


def post_extract(
    *, agent_url: str, secret: str, patient_uuid: str, file_path: Path,
    doc_type: str, mime: str, timeout: int,
) -> tuple[int, dict]:
    body, boundary = build_multipart(
        doc_type=doc_type,
        document_reference_id=f"smoke-{file_path.stem}",
        file_path=file_path,
        mime=mime,
    )
    req = urllib.request.Request(
        f"{agent_url}/agent/extract",
        data=body,
        method="POST",
        headers={
            "Content-Type": f"multipart/form-data; boundary={boundary}",
            "Authorization": f"Bearer {mint_token(secret, patient_uuid)}",
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return resp.status, json.loads(resp.read())
    except urllib.error.HTTPError as e:
        try:
            return e.code, json.loads(e.read())
        except Exception:  # noqa: BLE001
            return e.code, {"error": str(e)}
    except Exception as e:  # noqa: BLE001
        return -1, {"error": f"{type(e).__name__}: {e}"}


def dispatch(path: Path) -> tuple[str, str] | None:
    """Return (doc_type, mime) for ``path``, or None if we don't know
    how to ingest it. Falls back to mimetypes.guess_type for the MIME
    when our hard-coded table doesn't have it."""
    name = path.name.lower()
    for ext, doc_type, mime in DISPATCH:
        if name.endswith(ext):
            return doc_type, mime or (mimetypes.guess_type(str(path))[0] or "application/octet-stream")
    return None


def summarize(payload: dict) -> str:
    """One-line summary of an /agent/extract response — what the
    runner prints next to the pass/fail."""
    fmt = payload.get("format")
    mt = payload.get("message_type")
    ext = payload.get("extraction") or {}
    if isinstance(ext, dict):
        results = ext.get("results")
        if isinstance(results, list):
            return f"{fmt or '?'} ({mt or '-'}) → {len(results)} result(s)"
        meds = ext.get("medications")
        if isinstance(meds, list):
            return f"{fmt or '?'} → {len(meds)} med(s)"
        if "demographics" in ext:
            d = ext["demographics"] or {}
            return f"{fmt or '?'} ({mt or '-'}) → {d.get('family_name','?')}, {d.get('given_name','?')}"
    return fmt or "?"


def main() -> int:
    parser = argparse.ArgumentParser(prog="cohort_smoke")
    parser.add_argument("--agent-url", default=os.environ.get("AGENT_URL", DEFAULT_AGENT_URL))
    parser.add_argument("--patient-uuid", default=os.environ.get("DEMO_PATIENT_UUID", DEFAULT_PATIENT_UUID))
    parser.add_argument("--timeout", type=int, default=120)
    parser.add_argument("--only", help="Substring filter on filename (e.g. 'p01').", default=None)
    args = parser.parse_args()

    secret = os.environ.get("AGENT_SHARED_SECRET")
    if not secret:
        print("AGENT_SHARED_SECRET env var is required", file=sys.stderr)
        return 2

    files = sorted(p for p in ASSET_DIR.rglob("*") if p.is_file() and not p.name.startswith("."))
    if args.only:
        files = [p for p in files if args.only in p.name]
    if not files:
        print(f"no files found under {ASSET_DIR}", file=sys.stderr)
        return 2

    pass_count = 0
    fail_count = 0
    skipped = 0
    for path in files:
        d = dispatch(path)
        if d is None:
            skipped += 1
            continue
        doc_type, mime = d
        status, payload = post_extract(
            agent_url=args.agent_url,
            secret=secret,
            patient_uuid=args.patient_uuid,
            file_path=path,
            doc_type=doc_type,
            mime=mime,
            timeout=args.timeout,
        )
        ok = 200 <= status < 300
        rel = path.relative_to(ASSET_DIR)
        if ok:
            pass_count += 1
            print(f"  ✅ {rel}  [{doc_type}]  {summarize(payload)}")
        else:
            fail_count += 1
            err = payload.get("error") or payload.get("detail") or payload
            print(f"  ❌ {rel}  [{doc_type}]  HTTP {status} — {str(err)[:200]}")

    print()
    total = pass_count + fail_count
    print(f"== cohort smoke: {pass_count}/{total} pass, {fail_count} fail, {skipped} skipped ==")
    return 0 if fail_count == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
