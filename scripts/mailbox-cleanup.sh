#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT_DIR"

STATE_DIR="${MAILBOX_STATE_DIR:-$ROOT_DIR/.mailbox-cleanup/inbox}"
POLICY_PATH="${MAILBOX_POLICY_PATH:-policies/personal.example.json}"
FOLDER="${MAILBOX_FOLDER:-inbox}"
PAGE_SIZE="${MAILBOX_PAGE_SIZE:-999}"
TOP_COUNT="${MAILBOX_TOP_COUNT:-25}"
APPLY_LIMIT="${MAILBOX_APPLY_LIMIT:-500}"

note() {
  printf '\n==> %s\n' "$*"
}

die() {
  printf '\nERROR: %s\n' "$*" >&2
  exit 1
}

prepare_auth() {
  bash scripts/email-filter.sh auth-check
}

run_audit() {
  prepare_auth
  note "Scanning or resuming $FOLDER; no messages will be moved"
  .venv/bin/python mailbox_cleanup.py \
    --state-dir "$STATE_DIR" \
    audit \
    --folder "$FOLDER" \
    --policy "$POLICY_PATH" \
    --page-size "$PAGE_SIZE" \
    --top "$TOP_COUNT" \
    "$@"

  note "Audit files are private local files under $STATE_DIR"
  printf 'Review again with: make mailbox-report\n'
}

run_report() {
  [[ -x .venv/bin/python ]] || bash scripts/email-filter.sh bootstrap
  .venv/bin/python mailbox_cleanup.py \
    --state-dir "$STATE_DIR" \
    report
}

run_apply() {
  prepare_auth
  printf 'Type MOVE_TO_DELETED_ITEMS to move up to %s reviewed messages: ' "$APPLY_LIMIT"
  read -r confirmation
  [[ "$confirmation" == "MOVE_TO_DELETED_ITEMS" ]] \
    || die "Apply cancelled. No messages were moved."

  .venv/bin/python mailbox_cleanup.py \
    --state-dir "$STATE_DIR" \
    apply \
    --limit "$APPLY_LIMIT" \
    --confirm "$confirmation"

  printf '\nRepeat make mailbox-apply to continue the reviewed plan in bounded runs.\n'
}

run_reset() {
  printf 'Type RESET_LOCAL_STATE to delete only %s: ' "$STATE_DIR"
  read -r confirmation
  [[ "$confirmation" == "RESET_LOCAL_STATE" ]] \
    || die "Reset cancelled."

  [[ -x .venv/bin/python ]] || bash scripts/email-filter.sh bootstrap
  .venv/bin/python mailbox_cleanup.py \
    --state-dir "$STATE_DIR" \
    reset \
    --confirm "$confirmation"
}

help_text() {
  cat <<'EOF'
Historical mailbox cleanup

  make mailbox-audit   Scan/resume Inbox and build a non-destructive report
  make mailbox-report  Print the latest report without contacting Microsoft
  make mailbox-apply   Move up to 500 reviewed candidates to Deleted Items
  make mailbox-reset   Delete only the private local scan state

Defaults:
  MAILBOX_FOLDER=inbox
  MAILBOX_PAGE_SIZE=999
  MAILBOX_APPLY_LIMIT=500
  MAILBOX_POLICY_PATH=policies/personal.example.json
  MAILBOX_STATE_DIR=.mailbox-cleanup/inbox

Apply refuses incomplete scans and checked-in example policy files.
Override a value for one command, for example:
  MAILBOX_APPLY_LIMIT=1000 make mailbox-apply
EOF
}

case "${1:-help}" in
  help) help_text ;;
  audit) shift; run_audit "$@" ;;
  report) run_report ;;
  apply) run_apply ;;
  reset) run_reset ;;
  *) die "Unknown command: ${1:-}. Run make help." ;;
esac
