#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT_DIR"

AWS_PROFILE="${AWS_PROFILE:-email}"
AWS_REGION="${AWS_REGION:-us-east-2}"
AWS_DEFAULT_REGION="${AWS_DEFAULT_REGION:-$AWS_REGION}"
AWS_PAGER=""
export AWS_PROFILE AWS_REGION AWS_DEFAULT_REGION AWS_PAGER

STATE_DIR="${MAILBOX_STATE_DIR:-$ROOT_DIR/.mailbox-cleanup/inbox}"
POLICY_PATH="${MAILBOX_POLICY_PATH:-policies/personal.example.json}"
FOLDER="${MAILBOX_FOLDER:-inbox}"
PAGE_SIZE="${MAILBOX_PAGE_SIZE:-999}"
TOP_COUNT="${MAILBOX_TOP_COUNT:-25}"
APPLY_LIMIT="${MAILBOX_APPLY_LIMIT:-500}"
REVIEW_SENDER="${MAILBOX_REVIEW_SENDER:-}"
REVIEW_DOMAIN="${MAILBOX_REVIEW_DOMAIN:-}"
REVIEW_TOP="${MAILBOX_REVIEW_TOP:-25}"
REVIEW_SAMPLES="${MAILBOX_REVIEW_SAMPLES:-4}"
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
    note "The cached Microsoft token needs a browser refresh"
    bash scripts/email-filter.sh microsoft-login
    .venv/bin/python setup_token_interactive.py --check \
      || die "Microsoft authentication still failed after browser login."
  fi
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
  printf 'Inspect unmatched subject patterns with: make mailbox-review\n'
}

run_report() {
  ensure_local_python
  .venv/bin/python mailbox_cleanup.py \
    --state-dir "$STATE_DIR" \
    report
}

run_review() {
  ensure_local_python
  local args=(
    --state-dir "$STATE_DIR"
    review
    --top "$REVIEW_TOP"
    --samples "$REVIEW_SAMPLES"
  )
  if [[ -n "$REVIEW_SENDER" ]]; then
    args+=(--sender "$REVIEW_SENDER")
  fi
  if [[ -n "$REVIEW_DOMAIN" ]]; then
    args+=(--domain "$REVIEW_DOMAIN")
  fi

  note "Reviewing unmatched mail from the private local snapshot; Microsoft is not contacted"
  .venv/bin/python mailbox_cleanup.py "${args[@]}"
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

  ensure_local_python
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
  make mailbox-review  Inspect unmatched senders and redacted subject patterns locally
  make mailbox-apply   Move up to 500 reviewed candidates to Deleted Items
  make mailbox-reset   Delete only the private local scan state

Defaults:
  MAILBOX_FOLDER=inbox
  MAILBOX_PAGE_SIZE=999
  MAILBOX_APPLY_LIMIT=500
  MAILBOX_POLICY_PATH=policies/personal.example.json
  MAILBOX_STATE_DIR=.mailbox-cleanup/inbox
  MAILBOX_REVIEW_TOP=25
  MAILBOX_REVIEW_SAMPLES=4

Review one sender or domain:
  MAILBOX_REVIEW_SENDER=store-news@amazon.ca make mailbox-review
  MAILBOX_REVIEW_DOMAIN=linkedin.com make mailbox-review

Apply refuses incomplete scans and checked-in example policy files.
Override a value for one command, for example:
  MAILBOX_APPLY_LIMIT=1000 make mailbox-apply
EOF
}

case "${1:-help}" in
  help) help_text ;;
  audit) shift; run_audit "$@" ;;
  report) run_report ;;
  review) run_review ;;
  apply) run_apply ;;
  reset) run_reset ;;
  *) die "Unknown command: ${1:-}. Run make help." ;;
esac
