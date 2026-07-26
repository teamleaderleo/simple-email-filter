#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT_DIR"

STATE_DIR="${MAILBOX_STATE_DIR:-$ROOT_DIR/.mailbox-cleanup/inbox}"
OUTPUT_DIR="${MAILBOX_EXPORT_DIR:-$STATE_DIR/export}"
POLICY_PATH="${MAILBOX_POLICY_PATH:-}"
SAMPLES="${MAILBOX_EXPORT_SAMPLES:-6}"
TOP="${MAILBOX_EXPORT_TOP:-100}"

note() {
  printf '\n==> %s\n' "$*"
}

ensure_local_python() {
  if [[ ! -x .venv/bin/python ]] \
    || ! .venv/bin/python -c 'import sys, dotenv; raise SystemExit(0 if sys.version_info[:2] == (3, 14) else 1)' >/dev/null 2>&1; then
    bash scripts/email-filter.sh bootstrap
  fi
}

ensure_local_python

args=(
  --state-dir "$STATE_DIR"
  --output-dir "$OUTPUT_DIR"
  --samples "$SAMPLES"
  --top "$TOP"
)
if [[ -n "$POLICY_PATH" ]]; then
  args+=(--policy "$POLICY_PATH")
fi

note "Building JSON, CSV and Excel analysis files from the private local snapshot"
.venv/bin/python mailbox_export.py "${args[@]}"

note "Export complete"
printf 'Upload for analysis:\n  %s/mailbox-analysis.xlsx\n  %s/mailbox-summary.json\n' \
  "$OUTPUT_DIR" "$OUTPUT_DIR"
printf '\nDo not upload %s/messages.jsonl or other message-level state files.\n' "$STATE_DIR"
