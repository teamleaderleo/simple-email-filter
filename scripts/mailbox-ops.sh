#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT_DIR"

PROVISIONAL_STATE_DIR="${MAILBOX_STATE_DIR:-$ROOT_DIR/.mailbox-cleanup/inbox}"
CONFIG_FILE="${MAILBOX_CONFIG_FILE:-$PROVISIONAL_STATE_DIR/config.env}"
if [[ -f "$CONFIG_FILE" ]]; then
  set -a
  # shellcheck disable=SC1090
  source "$CONFIG_FILE"
  set +a
fi

STATE_DIR="${MAILBOX_STATE_DIR:-$ROOT_DIR/.mailbox-cleanup/inbox}"
AWS_PROFILE="${AWS_PROFILE:-email}"
AWS_REGION="${AWS_REGION:-us-east-2}"
AWS_DEFAULT_REGION="${AWS_DEFAULT_REGION:-$AWS_REGION}"
AWS_PAGER=""
WEBHOOK_FUNCTION="${WEBHOOK_FUNCTION:-email-webhook-handler}"
PRIVATE_POLICY_PATH="${MAILBOX_PRIVATE_POLICY_PATH:-policies/personal.json}"
EXPORT_DIR="${MAILBOX_EXPORT_DIR:-$STATE_DIR/export}"
CLEAN_STAGES="${MAILBOX_CLEAN_STAGES:-bulk,newsletters,operations}"
CLEAN_MAX_PASSES="${MAILBOX_CLEAN_MAX_PASSES:-20}"
OPEN_EXPORT="${MAILBOX_OPEN_EXPORT:-0}"
FORCE_TESTS="${MAILBOX_FORCE_TESTS:-0}"
TEST_STAMP="$STATE_DIR/.tested-commit"
AUTH_CHECKED=0

export AWS_PROFILE AWS_REGION AWS_DEFAULT_REGION AWS_PAGER
export MAILBOX_STATE_DIR="$STATE_DIR"
export MAILBOX_EXPORT_DIR="$EXPORT_DIR"
export MAILBOX_STAGE_RUN_LIMIT="${MAILBOX_STAGE_RUN_LIMIT:-50000}"

note() {
  printf '\n==> %s\n' "$*"
}

warn() {
  printf '\nWARNING: %s\n' "$*" >&2
}

die() {
  printf '\nERROR: %s\n' "$*" >&2
  exit 1
}

has_command() {
  command -v "$1" >/dev/null 2>&1
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

ensure_client_id() {
  if [[ -f .env ]] && grep -Eq '^[[:space:]]*CLIENT_ID=' .env; then
    return
  fi

  local client_id
  client_id="$(aws_cmd lambda get-function-configuration \
    --function-name "$WEBHOOK_FUNCTION" \
    --query 'Environment.Variables.CLIENT_ID' \
    --output text)"
  [[ -n "$client_id" && "$client_id" != "None" ]] \
    || die "Could not recover CLIENT_ID from $WEBHOOK_FUNCTION"
  touch .env
  printf '\nCLIENT_ID=%s\n' "$client_id" >> .env
  chmod 600 .env 2>/dev/null || true
  note "Added the non-secret Microsoft application client ID to .env"
}

check_auth() {
  (( AUTH_CHECKED == 0 )) || return
  has_command aws || die "AWS CLI v2 is required."
  local arn
  arn="$(aws_cmd sts get-caller-identity --query Arn --output text 2>/dev/null)" \
    || die "AWS login is unavailable. Run: aws login --profile $AWS_PROFILE"
  printf 'AWS identity: %s\n' "$arn"
  [[ "$arn" != *':root' ]] \
    || warn "The AWS profile is signed in as root; use a non-root administrator for routine work."

  ensure_client_id
  if ! .venv/bin/python setup_token_interactive.py --check; then
    note "Microsoft authentication needs a browser refresh"
    bash scripts/email-filter.sh microsoft-login
    .venv/bin/python setup_token_interactive.py --check \
      || die "Microsoft authentication still failed after browser login."
  fi
  AUTH_CHECKED=1
}

run_cached_tests() {
  mkdir -p "$STATE_DIR"
  chmod 700 "$STATE_DIR" 2>/dev/null || true
  local head tested="" dirty=""
  head="$(git rev-parse HEAD)"
  [[ -f "$TEST_STAMP" ]] && tested="$(cat "$TEST_STAMP")"
  dirty="$(git status --porcelain --untracked-files=normal)"

  if [[ -n "$dirty" ]]; then
    note "The working tree has uncommitted changes; running tests without caching the result"
    bash scripts/test.sh
    return
  fi

  if [[ "$FORCE_TESTS" != "1" && "$tested" == "$head" ]]; then
    note "Tests already passed for commit ${head:0:12}; skipping duplicate run"
    return
  fi

  bash scripts/test.sh
  printf '%s\n' "$head" > "$TEST_STAMP"
  chmod 600 "$TEST_STAMP" 2>/dev/null || true
}

run_local_check() {
  local command_name
  for command_name in git make; do
    has_command "$command_name" || die "Missing required command: $command_name"
  done
  ensure_local_python
  run_cached_tests
}

run_check() {
  run_local_check
  check_auth
  note "Mailbox checks passed"
}

checkpoint_complete() {
  .venv/bin/python - "$STATE_DIR" <<'PY'
import json
import pathlib
import sys

path = pathlib.Path(sys.argv[1]) / "checkpoint.json"
if not path.exists():
    raise SystemExit(1)
try:
    payload = json.loads(path.read_text(encoding="utf-8"))
except Exception:
    raise SystemExit(1)
raise SystemExit(0 if payload.get("complete") else 1)
PY
}

apply_started() {
  [[ -e "$STATE_DIR/apply-results.jsonl" ]]
}

ensure_snapshot() {
  if checkpoint_complete; then
    note "Using the complete saved mailbox snapshot"
    return
  fi
  apply_started && die "Apply results exist but the saved scan is incomplete. Do not replace this state directory."
  check_auth
  note "No complete snapshot exists; starting or resuming the non-destructive Inbox audit"
  bash scripts/mailbox-cleanup.sh audit
  checkpoint_complete || die "The mailbox audit did not finish. Rerun make mailbox-analyze to resume it."
}

ensure_apply_plan() {
  if apply_started; then
    note "Apply has already started; preserving and resuming the existing plan"
    return
  fi
  note "Preparing the ignored private policy and reviewed apply plan"
  MAILBOX_PRIVATE_POLICY_PATH="$PRIVATE_POLICY_PATH" \
    bash scripts/mailbox-cleanup.sh prepare-apply
}

run_export() {
  note "Refreshing the privacy-minimised analysis and apply-progress exports"
  if apply_started; then
    MAILBOX_POLICY_PATH="" MAILBOX_STATE_DIR="$STATE_DIR" MAILBOX_EXPORT_DIR="$EXPORT_DIR" \
      bash scripts/mailbox-export.sh
  else
    MAILBOX_STATE_DIR="$STATE_DIR" MAILBOX_EXPORT_DIR="$EXPORT_DIR" \
      bash scripts/mailbox-export.sh
  fi
}

open_export_if_requested() {
  if [[ "$OPEN_EXPORT" == "1" ]] && has_command open; then
    open "$EXPORT_DIR"
  fi
}

plan_for_stage() {
  local stage="$1"
  .venv/bin/python mailbox_cleanup.py \
    --state-dir "$STATE_DIR" \
    plan \
    --stage "$stage"
}

pending_from_json() {
  .venv/bin/python -c 'import json,sys; print(int(json.load(sys.stdin)["selection"]["pending"]))'
}

stage_list() {
  local normalized="${CLEAN_STAGES//,/ }"
  local stage
  for stage in $normalized; do
    case "$stage" in
      bulk|newsletters|operations|all) printf '%s\n' "$stage" ;;
      *) die "Unknown stage in MAILBOX_CLEAN_STAGES: $stage" ;;
    esac
  done
}

run_analyze() {
  run_local_check
  ensure_snapshot
  run_export
  open_export_if_requested
  note "Analysis is ready under $EXPORT_DIR"
}

run_clean() {
  run_check
  ensure_snapshot
  ensure_apply_plan
  run_export
  require_integer_between MAILBOX_CLEAN_MAX_PASSES "$CLEAN_MAX_PASSES" 1 100

  local all_preview all_pending confirmation stage pass before after
  local -a stages=()
  all_preview="$(plan_for_stage all)"
  printf '%s\n' "$all_preview"
  all_pending="$(printf '%s' "$all_preview" | pending_from_json)"
  if [[ "$all_pending" == "0" ]]; then
    note "The reviewed cleanup plan is already complete"
    open_export_if_requested
    return
  fi

  while IFS= read -r stage; do
    [[ -n "$stage" ]] && stages+=("$stage")
  done < <(stage_list)
  [[ ${#stages[@]} -gt 0 ]] || die "MAILBOX_CLEAN_STAGES did not contain a stage."
  printf '\nReviewed stages: %s\n' "${stages[*]}"
  printf 'Type MOVE_REVIEWED_MAIL_TO_DELETED_ITEMS to resume and complete up to %s reviewed messages: ' "$all_pending"
  read -r confirmation
  [[ "$confirmation" == "MOVE_REVIEWED_MAIL_TO_DELETED_ITEMS" ]] \
    || die "Cleanup cancelled. No messages were moved."

  final_export() {
    local status=$?
    trap - EXIT
    set +e
    run_export
    open_export_if_requested
    set -e
    exit "$status"
  }
  trap final_export EXIT

  for stage in "${stages[@]}"; do
    pass=0
    while (( pass < CLEAN_MAX_PASSES )); do
      before="$(plan_for_stage "$stage" | pending_from_json)"
      if [[ "$before" == "0" ]]; then
        note "Stage $stage is complete"
        break
      fi

      pass=$((pass + 1))
      note "Running stage $stage, pass $pass ($before pending)"
      MAILBOX_APPLY_STAGE="$stage" \
      MAILBOX_APPLY_CONFIRMATION="MOVE_TO_DELETED_ITEMS" \
      bash scripts/mailbox-apply-stage-all.sh

      after="$(plan_for_stage "$stage" | pending_from_json)"
      (( after < before )) \
        || die "Stage $stage made no progress. Its saved outcomes remain resumable."
    done

    after="$(plan_for_stage "$stage" | pending_from_json)"
    [[ "$after" == "0" ]] \
      || die "Stage $stage still has $after pending after $CLEAN_MAX_PASSES passes."
  done

  all_preview="$(plan_for_stage all)"
  printf '%s\n' "$all_preview"
  all_pending="$(printf '%s' "$all_preview" | pending_from_json)"
  if [[ "$all_pending" == "0" ]]; then
    note "Every reviewed message in the saved plan is complete"
  else
    warn "$all_pending reviewed messages remain outside the configured stage list. Change MAILBOX_CLEAN_STAGES or inspect make mailbox-plan."
  fi
}

help_text() {
  cat <<'EOF'
One-command mailbox operations

  make mailbox-check    Repair the local Python environment, verify AWS/Microsoft auth, and run tests once per commit
  make mailbox-analyze  Ensure a complete snapshot and refresh JSON/CSV/XLSX plus apply-progress exports
  make mailbox-clean    Confirm once, resume every reviewed stage, adapt Graph pressure, and export on exit

Optional private configuration:
  .mailbox-cleanup/inbox/config.env

Useful settings:
  MAILBOX_CLEAN_STAGES=bulk,newsletters,operations
  MAILBOX_CLEAN_MAX_PASSES=20
  MAILBOX_STAGE_RUN_LIMIT=50000
  MAILBOX_GRAPH_WORKERS=4
  MAILBOX_OPEN_EXPORT=1
  MAILBOX_FORCE_TESTS=1

The low-level mailbox-audit, mailbox-plan, mailbox-apply-stage and reset commands remain available for troubleshooting.
EOF
}

case "${1:-help}" in
  help) help_text ;;
  check) run_check ;;
  analyze|analyse) run_analyze ;;
  clean) run_clean ;;
  *) die "Unknown command: ${1:-}. Run make help." ;;
esac
