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

AWS_PROFILE="${AWS_PROFILE:-email}"
AWS_REGION="${AWS_REGION:-us-east-2}"
AWS_DEFAULT_REGION="${AWS_DEFAULT_REGION:-$AWS_REGION}"
AWS_PAGER=""
export AWS_PROFILE AWS_REGION AWS_DEFAULT_REGION AWS_PAGER

STATE_DIR="${MAILBOX_STATE_DIR:-$ROOT_DIR/.mailbox-cleanup/inbox}"
APPLY_STAGE="${MAILBOX_APPLY_STAGE:-bulk}"
APPLY_POLICIES="${MAILBOX_APPLY_POLICIES:-}"
STAGE_LIMIT="${MAILBOX_STAGE_LIMIT:-5000}"
STAGE_RUN_LIMIT="${MAILBOX_STAGE_RUN_LIMIT:-30000}"
GRAPH_WORKERS="${MAILBOX_GRAPH_WORKERS:-4}"
COOLDOWN_SECONDS="${MAILBOX_GRAPH_COOLDOWN_SECONDS:-20}"
MIN_ADAPTIVE_CHUNK="${MAILBOX_MIN_ADAPTIVE_CHUNK:-500}"
SUCCESS_STREAK_TARGET="${MAILBOX_GRAPH_SUCCESS_STREAK:-2}"
PROVIDED_CONFIRMATION="${MAILBOX_APPLY_CONFIRMATION:-}"
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
    chmod 600 .env 2>/dev/null || true
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
    [[ ${#SELECTION_ARGS[@]} -gt 0 ]] \
      || die "MAILBOX_APPLY_POLICIES did not contain a policy id."
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

json_pending() {
  .venv/bin/python -c 'import json,sys; print(int(json.load(sys.stdin)["selection"]["pending"]))'
}

result_metrics() {
  .venv/bin/python -c '
import json, sys
payload = json.load(sys.stdin)
diagnostics = payload.get("graphDiagnostics") or {}
retry_keys = (
    "topLevelRetries",
    "retriedMessages",
    "workerExceptions",
    "exhaustedRetryMessages",
    "missingSubresponses",
)
retry_pressure = sum(int(diagnostics.get(key, 0)) for key in retry_keys)
keys = ("requested", "moved", "missing", "failed", "remaining")
values = [int(payload.get(key, 0)) for key in keys]
values.append(retry_pressure)
print("\t".join(str(value) for value in values))
'
}

prepare_auth
selection_args
require_integer_between MAILBOX_STAGE_LIMIT "$STAGE_LIMIT" 1 5000
require_integer_between MAILBOX_STAGE_RUN_LIMIT "$STAGE_RUN_LIMIT" 1 50000
require_integer_between MAILBOX_GRAPH_WORKERS "$GRAPH_WORKERS" 1 8
require_integer_between MAILBOX_GRAPH_COOLDOWN_SECONDS "$COOLDOWN_SECONDS" 1 120
require_integer_between MAILBOX_MIN_ADAPTIVE_CHUNK "$MIN_ADAPTIVE_CHUNK" 100 5000
require_integer_between MAILBOX_GRAPH_SUCCESS_STREAK "$SUCCESS_STREAK_TARGET" 1 10
(( MIN_ADAPTIVE_CHUNK <= STAGE_LIMIT )) \
  || die "MAILBOX_MIN_ADAPTIVE_CHUNK cannot exceed MAILBOX_STAGE_LIMIT."

preview="$(plan_json)"
printf '%s\n' "$preview"
pending="$(printf '%s' "$preview" | json_pending)"
if [[ "$pending" == "0" ]]; then
  note "This apply selection is already complete"
  exit 0
fi

label="$(selection_label)"
target="$pending"
(( target > STAGE_RUN_LIMIT )) && target="$STAGE_RUN_LIMIT"
if [[ -n "$PROVIDED_CONFIRMATION" ]]; then
  confirmation="$PROVIDED_CONFIRMATION"
else
  printf '\nType MOVE_TO_DELETED_ITEMS to process up to %s messages from %s with adaptive checkpointed chunks (starting at %s per chunk and %s workers; %s pending): ' \
    "$target" "$label" "$STAGE_LIMIT" "$GRAPH_WORKERS" "$pending"
  read -r confirmation
fi
[[ "$confirmation" == "MOVE_TO_DELETED_ITEMS" ]] \
  || die "Apply cancelled. No messages were moved."

attempted=0
current_workers="$GRAPH_WORKERS"
current_chunk="$STAGE_LIMIT"
stable_chunks=0

while (( attempted < STAGE_RUN_LIMIT )); do
  current="$(plan_json)"
  current_pending="$(printf '%s' "$current" | json_pending)"
  if [[ "$current_pending" == "0" ]]; then
    note "The $label selection is complete"
    exit 0
  fi

  allowance=$((STAGE_RUN_LIMIT - attempted))
  chunk="$current_chunk"
  (( chunk > current_pending )) && chunk="$current_pending"
  (( chunk > allowance )) && chunk="$allowance"
  note "Processing the next $chunk messages from $label with $current_workers workers ($current_pending pending before this chunk)"

  set +e
  result="$(.venv/bin/python mailbox_cleanup.py \
    --state-dir "$STATE_DIR" \
    apply \
    --limit "$chunk" \
    --workers "$current_workers" \
    --confirm "$confirmation" \
    "${SELECTION_ARGS[@]}")"
  rc=$?
  set -e
  printf '%s\n' "$result"

  if ! metrics="$(printf '%s' "$result" | result_metrics 2>/dev/null)"; then
    die "The apply command failed before returning a structured result. The saved plan and prior outcomes remain resumable."
  fi
  IFS=$'\t' read -r requested moved missing failed remaining retry_pressure <<< "$metrics"

  attempted=$((attempted + requested))
  progressed=$((moved + missing))

  if [[ "$remaining" == "0" ]]; then
    note "The $label selection is complete after attempting $attempted messages in this run"
    exit 0
  fi

  if (( failed > 0 )); then
    stable_chunks=0
    if (( current_workers > 1 )); then
      next_workers=$((current_workers / 2))
      (( next_workers < 1 )) && next_workers=1
      current_workers="$next_workers"
    elif (( current_chunk > MIN_ADAPTIVE_CHUNK )); then
      next_chunk=$((current_chunk / 2))
      (( next_chunk < MIN_ADAPTIVE_CHUNK )) && next_chunk="$MIN_ADAPTIVE_CHUNK"
      current_chunk="$next_chunk"
    else
      die "$failed messages still failed at one worker and the minimum adaptive chunk. They remain pending for another run."
    fi

    note "Microsoft rejected $failed messages in this chunk. Successful outcomes were saved. Cooling down for ${COOLDOWN_SECONDS}s, then retrying pending items with $current_workers worker(s) and chunks of up to $current_chunk."
    sleep "$COOLDOWN_SECONDS"
    continue
  fi

  (( rc == 0 )) \
    || die "The apply command returned an error even though no per-message failures were reported."
  (( requested > 0 && progressed > 0 )) \
    || die "The continuous apply made no progress and stopped to avoid an endless loop."

  if (( retry_pressure > 0 )); then
    stable_chunks=0
    note "Microsoft throttled or retried $retry_pressure operations internally; keeping current pressure for the next chunk."
    sleep 2
  else
    stable_chunks=$((stable_chunks + 1))
    if (( stable_chunks >= SUCCESS_STREAK_TARGET )); then
      if (( current_chunk < STAGE_LIMIT )); then
        next_chunk=$((current_chunk * 2))
        (( next_chunk > STAGE_LIMIT )) && next_chunk="$STAGE_LIMIT"
        current_chunk="$next_chunk"
        note "Two clean chunks completed; increasing the checkpoint chunk to $current_chunk."
      elif (( current_workers < GRAPH_WORKERS )); then
        current_workers=$((current_workers + 1))
        note "Two clean chunks completed; increasing Graph workers to $current_workers."
      fi
      stable_chunks=0
    fi
    sleep 1
  fi
done

note "Stopped at MAILBOX_STAGE_RUN_LIMIT=$STAGE_RUN_LIMIT with work still pending. The one-command cleanup runner will start another resumable pass automatically."
