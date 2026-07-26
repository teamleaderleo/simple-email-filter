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
STAGE_RUN_LIMIT="${MAILBOX_STAGE_RUN_LIMIT:-30000}"
GRAPH_WORKERS="${MAILBOX_GRAPH_WORKERS:-4}"
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

require_integer_between() {
  local label="$1" value="$2" minimum="$3" maximum="$4"
  [[ "$value" =~ ^[0-9]+$ ]] \
    || die "$label must be an integer between $minimum and $maximum."
  (( value >= minimum && value <= maximum )) \
    || die "$label must be between $minimum and $maximum."
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

selection_label() {
  if [[ -n "$APPLY_POLICIES" ]]; then
    printf '%s' "$APPLY_POLICIES"
  else
    printf '%s' "$APPLY_STAGE"
  fi
}

plan_json() {
  .venv/bin/python mailbox_cleanup.py \
    --state-dir "$STATE_DIR" \
    plan \
    "${SELECTION_ARGS[@]}"
}

json_value() {
  local expression="$1"
  .venv/bin/python -c "import json,sys; print($expression)"
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
  plan_json
}

run_apply_stage() {
  prepare_auth
  selection_args
  require_integer_between MAILBOX_STAGE_LIMIT "$STAGE_LIMIT" 1 5000
  require_integer_between MAILBOX_GRAPH_WORKERS "$GRAPH_WORKERS" 1 8

  local preview pending label
  preview="$(plan_json)"
  printf '%s\n' "$preview"
  pending="$(printf '%s' "$preview" | json_value 'json.load(sys.stdin)["selection"]["pending"]')"
  if [[ "$pending" == "0" ]]; then
    note "This apply selection is already complete"
    return
  fi

  label="$(selection_label)"
  printf '\nType MOVE_TO_DELETED_ITEMS to move up to %s messages from %s using %s workers (%s pending): ' \
    "$STAGE_LIMIT" "$label" "$GRAPH_WORKERS" "$pending"
  read -r confirmation
  [[ "$confirmation" == "MOVE_TO_DELETED_ITEMS" ]] \
    || die "Apply cancelled. No messages were moved."

  .venv/bin/python mailbox_cleanup.py \
    --state-dir "$STATE_DIR" \
    apply \
    --limit "$STAGE_LIMIT" \
    --workers "$GRAPH_WORKERS" \
    --confirm "$confirmation" \
    "${SELECTION_ARGS[@]}"

  printf '\nRerun make mailbox-apply-stage to continue this same resumable selection.\n'
}

run_apply_stage_all() {
  prepare_auth
  selection_args
  require_integer_between MAILBOX_STAGE_LIMIT "$STAGE_LIMIT" 1 5000
  require_integer_between MAILBOX_STAGE_RUN_LIMIT "$STAGE_RUN_LIMIT" 1 50000
  require_integer_between MAILBOX_GRAPH_WORKERS "$GRAPH_WORKERS" 1 8

  local preview pending label target confirmation attempted
  preview="$(plan_json)"
  printf '%s\n' "$preview"
  pending="$(printf '%s' "$preview" | json_value 'json.load(sys.stdin)["selection"]["pending"]')"
  if [[ "$pending" == "0" ]]; then
    note "This apply selection is already complete"
    return
  fi

  label="$(selection_label)"
  target="$pending"
  (( target > STAGE_RUN_LIMIT )) && target="$STAGE_RUN_LIMIT"
  printf '\nType MOVE_TO_DELETED_ITEMS to process up to %s messages from %s in checkpointed chunks of %s using %s workers (%s pending): ' \
    "$target" "$label" "$STAGE_LIMIT" "$GRAPH_WORKERS" "$pending"
  read -r confirmation
  [[ "$confirmation" == "MOVE_TO_DELETED_ITEMS" ]] \
    || die "Apply cancelled. No messages were moved."

  attempted=0
  while (( attempted < STAGE_RUN_LIMIT )); do
    local current current_pending allowance chunk result rc requested moved missing failed remaining progressed
    current="$(plan_json)"
    current_pending="$(printf '%s' "$current" | json_value 'json.load(sys.stdin)["selection"]["pending"]')"
    if [[ "$current_pending" == "0" ]]; then
      note "The $label selection is complete"
      return
    fi

    allowance=$((STAGE_RUN_LIMIT - attempted))
    chunk="$STAGE_LIMIT"
    (( chunk > current_pending )) && chunk="$current_pending"
    (( chunk > allowance )) && chunk="$allowance"
    note "Processing the next $chunk messages from $label ($current_pending pending before this chunk)"

    set +e
    result="$(.venv/bin/python mailbox_cleanup.py \
      --state-dir "$STATE_DIR" \
      apply \
      --limit "$chunk" \
      --workers "$GRAPH_WORKERS" \
      --confirm "$confirmation" \
      "${SELECTION_ARGS[@]}")"
    rc=$?
    set -e
    printf '%s\n' "$result"
    (( rc == 0 )) || die "The continuous apply stopped after a failed chunk. Rerun the same command after reviewing the result."

    requested="$(printf '%s' "$result" | json_value 'json.load(sys.stdin)["requested"]')"
    moved="$(printf '%s' "$result" | json_value 'json.load(sys.stdin)["moved"]')"
    missing="$(printf '%s' "$result" | json_value 'json.load(sys.stdin)["missing"]')"
    failed="$(printf '%s' "$result" | json_value 'json.load(sys.stdin)["failed"]')"
    remaining="$(printf '%s' "$result" | json_value 'json.load(sys.stdin)["remaining"]')"
    attempted=$((attempted + requested))
    progressed=$((moved + missing))

    (( failed == 0 )) || die "The continuous apply stopped because $failed messages failed in the last chunk."
    (( requested > 0 && progressed > 0 )) \
      || die "The continuous apply made no progress and stopped to avoid an endless loop."
    if [[ "$remaining" == "0" ]]; then
      note "The $label selection is complete after attempting $attempted messages in this run"
      return
    fi
    sleep 1
  done

  note "Stopped at MAILBOX_STAGE_RUN_LIMIT=$STAGE_RUN_LIMIT with work still pending. Rerun make mailbox-apply-stage-all to continue."
}

run_apply() {
  prepare_auth
  require_integer_between MAILBOX_APPLY_LIMIT "$APPLY_LIMIT" 1 5000
  require_integer_between MAILBOX_GRAPH_WORKERS "$GRAPH_WORKERS" 1 8
  printf 'Type MOVE_TO_DELETED_ITEMS to move up to %s reviewed messages from the whole plan using %s workers: ' \
    "$APPLY_LIMIT" "$GRAPH_WORKERS"
  read -r confirmation
  [[ "$confirmation" == "MOVE_TO_DELETED_ITEMS" ]] \
    || die "Apply cancelled. No messages were moved."

  .venv/bin/python mailbox_cleanup.py \
    --state-dir "$STATE_DIR" \
    apply \
    --limit "$APPLY_LIMIT" \
    --workers "$GRAPH_WORKERS" \
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

  make mailbox-audit            Scan/resume Inbox and build a non-destructive report
  make mailbox-report           Print the latest report without contacting Microsoft
  make mailbox-review           Inspect unmatched senders and redacted subject patterns locally
  make mailbox-prepare-apply    Create the ignored private policy and rebuild the local plan
  make mailbox-plan             Preview a named stage or explicit policies without Microsoft
  make mailbox-apply-stage      Move one checkpointed chunk from a reviewed stage
  make mailbox-apply-stage-all  Confirm once and continue checkpointed chunks until done or capped
  make mailbox-apply            Legacy whole-plan bounded apply
  make mailbox-reset            Delete only the private local scan state

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
  MAILBOX_STAGE_RUN_LIMIT=30000
  MAILBOX_GRAPH_WORKERS=4
  MAILBOX_APPLY_LIMIT=500
  MAILBOX_REVIEW_TOP=25
  MAILBOX_REVIEW_SAMPLES=4

Examples:
  make mailbox-apply-stage-all
  MAILBOX_STAGE_RUN_LIMIT=10000 make mailbox-apply-stage-all
  MAILBOX_GRAPH_WORKERS=2 make mailbox-apply-stage-all
  MAILBOX_APPLY_STAGE=newsletters make mailbox-apply-stage-all
  MAILBOX_APPLY_POLICIES=shipment-tracking,uber-order-notifications make mailbox-plan

Continuous apply checkpoints after every chunk, stops on failures or no progress,
and never processes more than MAILBOX_STAGE_RUN_LIMIT in one invocation. Apply
refuses incomplete scans and checked-in example policy files.
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
  apply-stage-all) run_apply_stage_all ;;
  apply) run_apply ;;
  reset) run_reset ;;
  *) die "Unknown command: ${1:-}. Run make help." ;;
esac
