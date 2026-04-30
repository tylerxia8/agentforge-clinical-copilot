#!/usr/bin/env bash
# Demo beat 3 — full integration eval suite. Runs the same six cases
# as agent-service/evals/run.py against the live deployment, prints a
# pass/fail markdown table.
#
# Uses Docker so we don't need a local Python install.

set -euo pipefail
cd "$(dirname "$0")"
source ./_lib.sh

cd ../agent-service/evals

MSYS_NO_PATHCONV=1 docker run --rm -i \
  -e AGENT_URL="$AGENT_URL" \
  -e AGENT_SHARED_SECRET="$AGENT_SHARED_SECRET" \
  -v "$PWD:/work" -w /work \
  python:3.12-slim python run.py
