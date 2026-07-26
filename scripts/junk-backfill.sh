#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT_DIR"

AWS_PROFILE="${AWS_PROFILE:-email}"
AWS_REGION="${AWS_REGION:-us-east-2}"
AWS_DEFAULT_REGION="${AWS_DEFAULT_REGION:-$AWS_REGION}"
AWS_PAGER=""
export AWS_PROFILE AWS_REGION AWS_DEFAULT_REGION AWS_PAGER

STATE_DIR="${JUNK_BACKFILL_STATE_DIR:-$ROOT_DIR/.junk-backfill}"
START="${JUNK_BACKFILL_START:-}"
END="${JUNK_BACKFILL_END:-}"
MAX_MESSAGES="${JUNK_BACKFILL_MAX_MESSAGES:-500}"
PAGE_SIZE="${JUNK_BACKFILL_PAGE_SIZE:-100}"
APPLY_LIMIT="${JUNK_BACKFILL_APPLY_LIMIT:-250}"
WEBHOOK_FUNCTION="${WEBHOOK_FUNCTION:-email-webhook-handler}"

note() {
  printf '\n==> %s\n' "$*"
}

die() {
  printf '\nERROR: %s\n' "$*" >&2
  exit 1
}

aws_cmd() {
  aws --profile "$AWS_PROFILE" --region "$AWS_REGION" "$@"
}

ensure_local_python() {
  if [[ ! -x .venv/bin/python ]] \
    || ! .venv/bin/python -c 'import sys, awscrt, boto3, dotenv, msal, requests; raise SystemExit(0 if sys.version_info[:2] == (3, 14) else 1)' >/dev/null 2>&1; then
    bash scripts/email-filter.sh bootstrap
  fi
}

prepare_auth() {
  ensure_local_python

  aws_cmd sts get-caller-identity --query Arn --output text >/dev/null \
    || die "AWS login is unavailable. Run: aws login --profile $AWS_PROFILE"

  if [[ ! -f .env ]] || ! grep -Eq '^[[:space:]]*CLIENT_ID=' .env; then
    local client_id
    client_id="$(aws_cmd lambda get-function-configuration \
      --function-name "$WEBHOOK_FUNCTION" \
      --query 'Environment.Variables.CLIENT_ID' \
      --output text)"
    [[ -n "$client_id" && "$client_id" != "None" ]] \
      || die "Could not recover CLIENT_ID from $WEBHOOK_FUNCTION"
    touch .env
    printf '\nCLIENT_ID=%s\n' "$client_id" >> .env
    note "Added the non-secret Microsoft application client ID to .env"
  fi

  if ! .venv/bin/python setup_token_interactive.py --check; then
    note "The Microsoft token needs a browser refresh"
    bash scripts/email-filter.sh microsoft-login
    .venv/bin/python setup_token_interactive.py --check \
      || die "Microsoft authentication still failed after browser login."
  fi
}

require_window() {
  [[ -n "$START" ]] || die "Set JUNK_BACKFILL_START to an ISO timestamp with a UTC offset."
  [[ -n "$END" ]] || die "Set JUNK_BACKFILL_END to an ISO timestamp with a UTC offset."
}

run_audit() {
  require_window
  prepare_auth
  note "Auditing messages still in Junk from $START through $END"
  note "The live $WEBHOOK_FUNCTION Gemma configuration will be used; no messages will be deleted"
  .venv/bin/python junk_backfill.py \
    --state-dir "$STATE_DIR" \
    audit \
    --start "$START" \
    --end "$END" \
    --max-messages "$MAX_MESSAGES" \
    --page-size "$PAGE_SIZE" \
    --function-name "$WEBHOOK_FUNCTION"

  printf '\nReview the saved result with: make junk-backfill-report\n'
  printf 'Apply only DELETE decisions with: make junk-backfill-apply\n'
}

run_report() {
  ensure_local_python
  .venv/bin/python junk_backfill.py \
    --state-dir "$STATE_DIR" \
    report
}

run_apply() {
  prepare_auth
  local report_json pending start end
  report_json="$(.venv/bin/python junk_backfill.py --state-dir "$STATE_DIR" report)"
  printf '%s\n' "$report_json"
  pending="$(printf '%s' "$report_json" | .venv/bin/python -c 'import json,sys; print(json.load(sys.stdin)["apply"]["pending"])')"
  start="$(printf '%s' "$report_json" | .venv/bin/python -c 'import json,sys; print(json.load(sys.stdin)["apply"]["start"])')"
  end="$(printf '%s' "$report_json" | .venv/bin/python -c 'import json,sys; print(json.load(sys.stdin)["apply"]["end"])')"

  if [[ "$pending" == "0" ]]; then
    note "This Junk backfill plan is already complete"
    return
  fi

  printf '\nType DELETE_JUNK_WINDOW to delete up to %s saved DELETE decisions from %s through %s (%s pending): ' \
    "$APPLY_LIMIT" "$start" "$end" "$pending"
  read -r confirmation
  [[ "$confirmation" == "DELETE_JUNK_WINDOW" ]] \
    || die "Apply cancelled. No messages were deleted."

  .venv/bin/python junk_backfill.py \
    --state-dir "$STATE_DIR" \
    apply \
    --limit "$APPLY_LIMIT" \
    --confirm "$confirmation"

  printf '\nRerun make junk-backfill-apply to continue the saved plan.\n'
}

run_reset() {
  printf 'Type RESET_JUNK_BACKFILL to delete only %s: ' "$STATE_DIR"
  read -r confirmation
  [[ "$confirmation" == "RESET_JUNK_BACKFILL" ]] \
    || die "Reset cancelled."

  ensure_local_python
  .venv/bin/python junk_backfill.py \
    --state-dir "$STATE_DIR" \
    reset \
    --confirm "$confirmation"
}

help_text() {
  cat <<'EOF'
Junk notification gap backfill

  make junk-backfill-audit   Run the live Junk Guard rules and Gemma over one time window; delete nothing
  make junk-backfill-report  Print the saved private audit and apply status
  make junk-backfill-apply   Delete only saved DELETE decisions, in resumable bounded batches
  make junk-backfill-reset   Delete only the private local backfill state

Required for audit:
  JUNK_BACKFILL_START=ISO timestamp with UTC offset
  JUNK_BACKFILL_END=ISO timestamp with UTC offset

Example:
  JUNK_BACKFILL_START=2026-07-25T08:00:00-07:00 \
  JUNK_BACKFILL_END=2026-07-25T11:00:00-07:00 \
  make junk-backfill-audit

Defaults:
  JUNK_BACKFILL_STATE_DIR=.junk-backfill
  JUNK_BACKFILL_MAX_MESSAGES=500
  JUNK_BACKFILL_PAGE_SIZE=100
  JUNK_BACKFILL_APPLY_LIMIT=250
  WEBHOOK_FUNCTION=email-webhook-handler

The audit fetches only messages still in Junk inside the bounded window. It uses
exact immutable Graph ids, the deployed Lambda's current Cloudflare model and
credentials, and the same deterministic and Gemma decision path as live Junk
Guard. The private plan stores message ids, senders, timestamps and decisions,
but no subjects, previews, bodies or attachments.
EOF
}

case "${1:-help}" in
  help) help_text ;;
  audit) run_audit ;;
  report) run_report ;;
  apply) run_apply ;;
  reset) run_reset ;;
  *) die "Unknown command: ${1:-}. Run make help." ;;
esac
