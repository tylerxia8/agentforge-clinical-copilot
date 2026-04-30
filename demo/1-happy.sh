#!/usr/bin/env bash
# Demo beat 1 — happy path. Farrah Rolle has 2 active meds on file.
# Expected: agent surfaces both with [MedicationRequest#…] citations,
# no refusal, ~5-7s round trip.

set -euo pipefail
cd "$(dirname "$0")"
source ./_lib.sh

post_chat "$FARRAH_UUID" "What active medications is this patient on?"
