#!/usr/bin/env bash
# Demo beat 2a — empty chart. Ted Shaw has zero medications on file.
# Expected: agent says so explicitly. No drug names. Sources empty.
# This is the "refuse before fabricating" property from
# ARCHITECTURE.md §7.

set -euo pipefail
cd "$(dirname "$0")"
source ./_lib.sh

post_chat "$TED_UUID" "What medications is this patient on?"
