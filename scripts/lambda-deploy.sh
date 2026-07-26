#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT_DIR"

AWS_PROFILE="${AWS_PROFILE:-email}"
AWS_REGION="${AWS_REGION:-us-east-2}"
AWS_DEFAULT_REGION="${AWS_DEFAULT_REGION:-$AWS_REGION}"
AWS_PAGER=""
export AWS_PROFILE AWS_REGION AWS_DEFAULT_REGION AWS_PAGER

WEBHOOK_FUNCTION="${WEBHOOK_FUNCTION:-email-webhook-handler}"
SUBSCRIPTION_FUNCTION="${SUBSCRIPTION_FUNCTION:-email-subscription-manager}"
TARGET_RUNTIME="${TARGET_RUNTIME:-python3.14}"
CACHE_ROOT="${CACHE_ROOT:-$ROOT_DIR/.build-cache}"

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

aws_cmd() {
  aws --profile "$AWS_PROFILE" --region "$AWS_REGION" "$@"
}

run_checks() {
  make doctor
  make test
}

lambda_runtime() {
  aws_cmd lambda get-function-configuration \
    --function-name "$1" \
    --query Runtime \
    --output text
}

lambda_architecture() {
  aws_cmd lambda get-function-configuration \
    --function-name "$1" \
    --query 'Architectures[0]' \
    --output text
}

lambda_platform() {
  case "$1" in
    x86_64) printf 'linux/amd64\n' ;;
    arm64) printf 'linux/arm64\n' ;;
    *) die "Unsupported Lambda architecture: $1" ;;
  esac
}

validate_runtime() {
  case "$1" in
    python3.11|python3.12|python3.13|python3.14) ;;
    *) die "Unsupported Lambda runtime: $1" ;;
  esac
}

BACKUP_DIR=""
prepare_backup_dir() {
  if [[ -z "$BACKUP_DIR" ]]; then
    BACKUP_DIR="$ROOT_DIR/backups/$(date +%Y%m%d-%H%M%S)"
    mkdir -p "$BACKUP_DIR"
  fi
}

backup_lambda() {
  local function_name="$1"
  local download_url

  prepare_backup_dir
  download_url="$(aws_cmd lambda get-function \
    --function-name "$function_name" \
    --query 'Code.Location' \
    --output text)"

  note "Backing up $function_name to $BACKUP_DIR/$function_name.zip"
  curl -fLsS "$download_url" -o "$BACKUP_DIR/$function_name.zip"
}

DEPENDENCY_CACHE_DIR=""
prepare_dependency_cache() {
  local runtime="$1"
  local architecture="$2"
  local python_version platform dependency_hash cache_key marker

  validate_runtime "$runtime"
  python_version="${runtime#python}"
  platform="$(lambda_platform "$architecture")"

  dependency_hash="$("$ROOT_DIR/.venv/bin/python" - \
    "$runtime" "$architecture" "$ROOT_DIR/requirements-webhook.txt" <<'PY'
import hashlib
import pathlib
import sys

runtime, architecture, requirements_path = sys.argv[1:]
digest = hashlib.sha256()
digest.update(runtime.encode())
digest.update(b"\0")
digest.update(architecture.encode())
digest.update(b"\0")
digest.update(pathlib.Path(requirements_path).read_bytes())
print(digest.hexdigest()[:20])
PY
)"

  cache_key="$CACHE_ROOT/lambda/$runtime-$architecture-$dependency_hash"
  DEPENDENCY_CACHE_DIR="$cache_key/deps"
  marker="$cache_key/.complete"

  if [[ -f "$marker" ]]; then
    note "Using cached Lambda dependencies for $runtime on $architecture"
    return 0
  fi

  rm -rf "$cache_key"
  mkdir -p "$DEPENDENCY_CACHE_DIR" "$CACHE_ROOT/pip"

  note "Installing Lambda dependencies once for $runtime on $architecture"
  docker run --rm \
    --platform "$platform" \
    -e PIP_CACHE_DIR=/pip-cache \
    -v "$ROOT_DIR":/var/task:ro \
    -v "$CACHE_ROOT/pip":/pip-cache \
    -v "$DEPENDENCY_CACHE_DIR":/asset-output \
    "python:${python_version}-slim" \
    sh -lc 'python -m pip install --disable-pip-version-check --root-user-action=ignore --no-compile --quiet -r /var/task/requirements-webhook.txt -t /asset-output'

  touch "$marker"
}

build_lambda_package() {
  local source_file="$1"
  local package_name="$2"
  local runtime="$3"
  local architecture="$4"
  local package_dir="$ROOT_DIR/${package_name}-package"
  local zip_path="$ROOT_DIR/${package_name}-lambda.zip"

  prepare_dependency_cache "$runtime" "$architecture"

  rm -rf "$package_dir" "$zip_path"
  mkdir -p "$package_dir"
  cp -R "$DEPENDENCY_CACHE_DIR"/. "$package_dir"/
  cp "$source_file" "$package_dir/lambda_function.py"

  (
    cd "$package_dir"
    zip -qr "$zip_path" .
  )

  [[ -s "$zip_path" ]] || die "Lambda ZIP was not created: $zip_path"
  ls -lh "$zip_path"
}

deploy_code_only() {
  local function_name="$1"
  local zip_path="$2"

  note "Updating $function_name code; environment variables are untouched"
  aws_cmd lambda update-function-code \
    --function-name "$function_name" \
    --zip-file "fileb://$zip_path" >/dev/null
  aws_cmd lambda wait function-updated --function-name "$function_name"
}

set_runtime_and_code() {
  local function_name="$1"
  local current_runtime="$2"
  local zip_path="$3"

  if [[ "$current_runtime" != "$TARGET_RUNTIME" ]]; then
    note "Updating $function_name runtime from $current_runtime to $TARGET_RUNTIME"
    aws_cmd lambda update-function-configuration \
      --function-name "$function_name" \
      --runtime "$TARGET_RUNTIME" >/dev/null
    aws_cmd lambda wait function-updated --function-name "$function_name"
  fi

  deploy_code_only "$function_name" "$zip_path"
}

deploy_webhook() {
  local runtime architecture

  run_checks
  runtime="$(lambda_runtime "$WEBHOOK_FUNCTION")"
  architecture="$(lambda_architecture "$WEBHOOK_FUNCTION")"

  backup_lambda "$WEBHOOK_FUNCTION"
  build_lambda_package webhook_handler.py webhook "$runtime" "$architecture"
  deploy_code_only "$WEBHOOK_FUNCTION" "$ROOT_DIR/webhook-lambda.zip"

  make setup-webhook
  make status
  note "Webhook deployment complete"
}

upgrade_runtimes() {
  local webhook_runtime webhook_arch subscription_runtime subscription_arch answer

  run_checks
  webhook_runtime="$(lambda_runtime "$WEBHOOK_FUNCTION")"
  webhook_arch="$(lambda_architecture "$WEBHOOK_FUNCTION")"
  subscription_runtime="$(lambda_runtime "$SUBSCRIPTION_FUNCTION")"
  subscription_arch="$(lambda_architecture "$SUBSCRIPTION_FUNCTION")"

  if [[ "$webhook_runtime" == "$TARGET_RUNTIME" && "$subscription_runtime" == "$TARGET_RUNTIME" ]]; then
    note "Both Lambdas already use $TARGET_RUNTIME"
    return 0
  fi

  printf 'Upgrade %s (%s) and %s (%s) to %s? [y/N] ' \
    "$WEBHOOK_FUNCTION" "$webhook_runtime" \
    "$SUBSCRIPTION_FUNCTION" "$subscription_runtime" \
    "$TARGET_RUNTIME"
  read -r answer
  case "$answer" in
    y|Y|yes|YES) ;;
    *) die "Runtime upgrade cancelled." ;;
  esac

  backup_lambda "$WEBHOOK_FUNCTION"
  backup_lambda "$SUBSCRIPTION_FUNCTION"

  build_lambda_package webhook_handler.py webhook "$TARGET_RUNTIME" "$webhook_arch"
  build_lambda_package subscription_manager.py subscription "$TARGET_RUNTIME" "$subscription_arch"

  set_runtime_and_code \
    "$WEBHOOK_FUNCTION" "$webhook_runtime" "$ROOT_DIR/webhook-lambda.zip"
  set_runtime_and_code \
    "$SUBSCRIPTION_FUNCTION" "$subscription_runtime" "$ROOT_DIR/subscription-lambda.zip"

  make setup-webhook
  make status
  note "Both Lambda runtimes are now $TARGET_RUNTIME"
}

help_text() {
  cat <<'EOF'
Simple Email Filter operations

Routine commands:
  make bootstrap        Create a Python 3.14 virtual environment and install tools
  make doctor           Check AWS login, Docker, resources and Lambda configuration
  make test             Run unit tests and syntax checks
  make deploy-webhook   Cached build, backup, deploy and Graph subscription setup
  make setup-webhook    Refresh auth when needed and recreate only the subscription
  make microsoft-login  Force a Microsoft browser login and refresh the token cache
  make status           Show deployment and subscription status without secrets
  make logs-webhook     Follow webhook Lambda logs
  make upgrade-runtime  Upgrade both email Lambdas to Python 3.14

Build dependencies are cached under .build-cache and reused until the runtime,
architecture or requirements-webhook.txt changes.
EOF
}

case "${1:-help}" in
  help) help_text ;;
  deploy-webhook) deploy_webhook ;;
  upgrade-runtime) upgrade_runtimes ;;
  *) die "Unknown command: ${1:-}. Run make help." ;;
esac
