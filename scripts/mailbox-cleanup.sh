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
PRIVATE_POLICY_PATH="${MAILBOX_PRIVATE_POLICY_PATH:-policies/personal.json}"
FOLDER="${MAILBOX_FOLDER:-inbox}"
PAGE_SIZE="${MAILBOX_PAGE_SIZE:-999}"
TOP_COUNT="${MAILBOX_TOP_COUNT:-25}"
APPLY_LIMIT="${MAILBOX_APPLY_LIMIT:-500}"
APPLY_STAGE="${MAILBOX_APPLY_STAGE:-bulk}"
APPLY_POLICIES="${MAILBOX_APPLY_POLICIES:-}"
STAGE_LIMIT="${MAILBOX_STAGE_LIMIT:-5000}"
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

selection_args() {
  SELECTION_ARGS=()
  if [[ -n "$APPLY_POLICIES" ]]; then
    [[ -z "${MAILBOX_APPLY_STAGE:-}" ]] \
      || die "Set MAILBOX_APPLY_STAGE or MAILBOX_APPLY_POLICIES, not both."
    local policy
    IFS=',' read -r -a policies <<< "$APPLY_POLICIES"
    for policy in "${policies[@]}"; do
      policy="${policy//[[:space:]]/}"
      [[ -n "$policy" ]] && SELECTION_ARGS+=(--policy-id "$policy")
    done
    [[ ${#SELECTION_ARGS[@]} -gt 0 ]] || die "MAILBOX_APPLY_POLICIES did not contain a policy id."
  else
    SELECTION_ARGS+=(--stage "$APPLY_STAGE")
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

run_prepare_apply() {
  ensure_local_python
  if [[ ! -f "$PRIVATE_POLICY_PATH" ]]; then
    cp policies/personal.example.json "$PRIVATE_POLICY_PATH"
    chmod 600 "$PRIVATE_POLICY_PATH" 2>/dev/null || true
    note "Created the ignored private apply policy at $PRIVATE_POLICY_PATH"
  else
    note "Using the existing private apply policy at $PRIVATE_POLICY_PATH"
  fi

  local summary_json
  note "Rebuilding the plan locally from the saved snapshot; Microsoft is not contacted"
  summary_json="$(.venv/bin/python mailbox_cleanup.py \
    --state-dir "$STATE_DIR" \
    replan \
    --policy "$PRIVATE_POLICY_PATH" \
    --top "$TOP_COUNT")"
  printf '%s' "$summary_json" | .venv/bin/python -c '
import json, sys
summary = json.load(sys.stdin)
print(json.dumps({
    "policyPath": summary.get("policyPath"),
    "scanned": summary.get("scanned", 0),
    "protectedForever": summary.get("protectedForever", 0),
    "keptByRetention": summary.get("keptByRetention", 0),
    "selected": summary.get("selected", 0),
    "unmatched": summary.get("unmatched", 0),
}, indent=2, sort_keys=True))
'

  run_plan
}

run_plan() {
  ensure_local_python
  selection_args
  note "Previewing the reviewed apply selection; Microsoft is not contacted"
  .venv/bin/python mailbox_cleanup.py \
    --state-dir "$STATE_DIR" \
    plan \
    "${SELECTION_ARGS[@]}"
}

run_apply_stage() {
  prepare_auth
  selection_args

  local plan_json pending selection_label
  plan_json="$(.venv/bin/python mailbox_cleanup.py \
    --state-dir "$STATE_DIR" \
    plan \
    "${SELECTION_ARGS[@]}")"
  printf '%s\n' "$plan_json"
  pending="$(printf '%s' "$plan_json" | .venv/bin/python -c 'import json,sys; print(json.load(sys.stdin)["selection"]["pending"])')"
  if [[ "$pending" == "0" ]]; then
    note "This apply selection is already complete"
    return
  fi

  selection_label="$APPLY_STAGE"
  [[ -z "$APPLY_POLICIES" ]] || selection_label="$APPLY_POLICIES"
  printf '\nType MOVE_TO_DELETED_ITEMS to move up to %s messages from %s (%s pending): ' \
    "$STAGE_LIMIT" "$selection_label" "$pending"
  read -r confirmation
  [[ "$confirmation" == "MOVE_TO_DELETED_ITEMS" ]] \
    || die "Apply cancelled. No messages were moved."

  .venv/bin/python mailbox_cleanup.py \
    --state-dir "$STATE_DIR" \
    apply \
    --limit "$STAGE_LIMIT" \
    --confirm "$confirmation" \
    "${SELECTION_ARGS[@]}"

  printf '\nRerun make mailbox-apply-stage to continue this same resumable selection.\n'
}

run_apply() {
  prepare_auth
  printf 'Type MOVE_TO_DELETED_ITEMS to move up to %s reviewed messages from the whole plan: ' "$APPLY_LIMIT"
  read -r confirmation
  [[ "$confirmation" == "MOVE_TO_DELETED_ITEMS" ]] \
    || die "Apply cancelled. No messages were moved."

  .venv/bin/python mailbox_cleanup.py \
    --state-dir "$STATE_DIR" \
    apply \
    --limit "$APPLY_LIMIT" \
    --confirm "$confirmation"

  printf '\nRepeat make mailbox-apply to continue the whole reviewed plan in bounded runs.\n'
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

  make mailbox-audit          Scan/resume Inbox and build a non-destructive report
  make mailbox-report         Print the latest report without contacting Microsoft
  make mailbox-review         Inspect unmatched senders and redacted subject patterns locally
  make mailbox-prepare-apply  Create the ignored private policy and rebuild the local plan
  make mailbox-plan           Preview a named stage or explicit policies without Microsoft
  make mailbox-apply-stage    Move up to 5,000 messages from one reviewed stage
  make mailbox-apply          Legacy whole-plan apply, bounded to 500 by default
  make mailbox-reset          Delete only the private local scan state

Named stages:
  bulk         promotions, job alerts, social notifications and short-lived digests
  newsletters  reviewed newsletters, entertainment, career and financial marketing
  operations   delivery, building and deployment notifications
  all          every selected policy in the plan

Defaults:
  MAILBOX_FOLDER=inbox
  MAILBOX_PAGE_SIZE=999
  MAILBOX_POLICY_PATH=policies/personal.example.json
  MAILBOX_PRIVATE_POLICY_PATH=policies/personal.json
  MAILBOX_STATE_DIR=.mailbox-cleanup/inbox
  MAILBOX_APPLY_STAGE=bulk
  MAILBOX_STAGE_LIMIT=5000
  MAILBOX_APPLY_LIMIT=500
  MAILBOX_REVIEW_TOP=25
  MAILBOX_REVIEW_SAMPLES=4

Examples:
  MAILBOX_APPLY_STAGE=newsletters make mailbox-plan
  MAILBOX_APPLY_STAGE=newsletters make mailbox-apply-stage
  MAILBOX_APPLY_POLICIES=shipment-tracking,uber-order-notifications make mailbox-plan

Apply refuses incomplete scans and checked-in example policy files. Run
make mailbox-prepare-apply once before the first apply run.
EOF
}

case "${1:-help}" in
  help) help_text ;;
  audit) shift; run_audit "$@" ;;
  report) run_report ;;
  review) run_review ;;
  prepare-apply) run_prepare_apply ;;
  plan) run_plan ;;
  apply-stage) run_apply_stage ;;
  apply) run_apply ;;
  reset) run_reset ;;
  *) die "Unknown command: ${1:-}. Run make help." ;;
esac
