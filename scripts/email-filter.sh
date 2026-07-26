#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT_DIR"

AWS_PROFILE="${AWS_PROFILE:-email}"
AWS_REGION="${AWS_REGION:-us-east-2}"
AWS_DEFAULT_REGION="${AWS_DEFAULT_REGION:-$AWS_REGION}"
AWS_PAGER=""
export AWS_PROFILE AWS_REGION AWS_DEFAULT_REGION AWS_PAGER

PYTHON_SERIES="${PYTHON_SERIES:-3.14}"
VENV_DIR="${VENV_DIR:-$ROOT_DIR/.venv}"
WEBHOOK_FUNCTION="${WEBHOOK_FUNCTION:-email-webhook-handler}"
SUBSCRIPTION_FUNCTION="${SUBSCRIPTION_FUNCTION:-email-subscription-manager}"
TOKEN_TABLE_NAME="${TOKEN_TABLE_NAME:-email-filter-tokens}"
API_NAME="${API_NAME:-email-webhook-api}"
TARGET_RUNTIME="${TARGET_RUNTIME:-python3.14}"

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

python_version_matches() {
  "$1" -c "import sys; raise SystemExit(0 if sys.version_info[:2] == tuple(map(int, '$PYTHON_SERIES'.split('.'))) else 1)" >/dev/null 2>&1
}

find_python() {
  local candidate
  for candidate in \
    "python$PYTHON_SERIES" \
    "/opt/homebrew/bin/python$PYTHON_SERIES" \
    "/usr/local/bin/python$PYTHON_SERIES" \
    python3; do
    if has_command "$candidate" && python_version_matches "$candidate"; then
      command -v "$candidate"
      return 0
    fi
  done
  return 1
}

bootstrap() {
  local python_bin=""

  if ! python_bin="$(find_python)"; then
    if ! has_command brew; then
      die "Python $PYTHON_SERIES is required. Install it, then rerun make bootstrap."
    fi

    if [[ ! -t 0 ]]; then
      die "Python $PYTHON_SERIES is missing. Run: brew install python@$PYTHON_SERIES"
    fi

    printf 'Python %s is missing. Install it with Homebrew now? [y/N] ' "$PYTHON_SERIES"
    read -r answer
    case "$answer" in
      y|Y|yes|YES)
        brew install "python@$PYTHON_SERIES"
        ;;
      *)
        die "Bootstrap stopped. Install Python with: brew install python@$PYTHON_SERIES"
        ;;
    esac

    python_bin="$(find_python)" || die "Python $PYTHON_SERIES is still unavailable after installation."
  fi

  if [[ -x "$VENV_DIR/bin/python" ]] && ! python_version_matches "$VENV_DIR/bin/python"; then
    note "Replacing the existing virtual environment with Python $PYTHON_SERIES"
    rm -rf "$VENV_DIR"
  fi

  if [[ ! -x "$VENV_DIR/bin/python" ]]; then
    note "Creating $VENV_DIR with $python_bin"
    "$python_bin" -m venv "$VENV_DIR"
  fi

  note "Installing local development and AWS-login dependencies"
  "$VENV_DIR/bin/python" -m pip install --upgrade pip
  "$VENV_DIR/bin/python" -m pip install -r requirements-dev.txt

  note "Bootstrap complete"
  "$VENV_DIR/bin/python" --version
}

ensure_venv() {
  if [[ ! -x "$VENV_DIR/bin/python" ]] \
    || ! python_version_matches "$VENV_DIR/bin/python" \
    || ! "$VENV_DIR/bin/python" -c 'import awscrt, boto3, dotenv, msal, requests' >/dev/null 2>&1; then
    bootstrap
  fi
}

require_aws_login() {
  has_command aws || die "AWS CLI v2 is required. Install it with: brew install awscli"

  local arn
  if ! arn="$(aws_cmd sts get-caller-identity --query Arn --output text 2>/dev/null)"; then
    die "AWS login is unavailable. Run: aws login --profile $AWS_PROFILE"
  fi

  printf 'AWS identity: %s\n' "$arn"
  if [[ "$arn" == *':root' ]]; then
    warn "This profile is signed in as the AWS root user. It works, but create a non-root administrator for routine deployments."
  fi
}

require_resource() {
  local kind="$1"
  local name="$2"

  case "$kind" in
    lambda)
      aws_cmd lambda get-function-configuration --function-name "$name" >/dev/null \
        || die "Missing Lambda function: $name"
      ;;
    dynamodb)
      aws_cmd dynamodb describe-table --table-name "$name" >/dev/null \
        || die "Missing DynamoDB table: $name"
      ;;
    *)
      die "Unknown resource type: $kind"
      ;;
  esac
}

api_id() {
  aws_cmd apigateway get-rest-apis \
    --query "items[?name=='$API_NAME'].id | [0]" \
    --output text
}

webhook_url() {
  local id
  id="$(api_id)"
  if [[ -z "$id" || "$id" == "None" ]]; then
    die "Could not find API Gateway named $API_NAME in $AWS_REGION"
  fi
  printf 'https://%s.execute-api.%s.amazonaws.com/prod\n' "$id" "$AWS_REGION"
}

doctor() {
  note "Checking local commands"
  local command_name
  for command_name in git aws docker zip curl make; do
    has_command "$command_name" || die "Missing required command: $command_name"
  done

  docker info >/dev/null 2>&1 \
    || die "Docker is installed but not running. Start Docker Desktop and rerun the command."

  ensure_venv

  note "Checking AWS login and deployed resources"
  require_aws_login
  require_resource lambda "$WEBHOOK_FUNCTION"
  require_resource lambda "$SUBSCRIPTION_FUNCTION"
  require_resource dynamodb "$TOKEN_TABLE_NAME"

  local url
  url="$(webhook_url)"
  printf 'Webhook URL: %s\n' "$url"

  note "Checking required Lambda environment-variable names"
  local variable_names
  variable_names="$(aws_cmd lambda get-function-configuration \
    --function-name "$WEBHOOK_FUNCTION" \
    --query "join(' ', keys(Environment.Variables))" \
    --output text)"

  local required_name
  for required_name in CLIENT_ID CLOUDFLARE_ACCOUNT_ID CLOUDFLARE_API_TOKEN CLOUDFLARE_MODEL; do
    case " $variable_names " in
      *" $required_name "*) ;;
      *) die "$WEBHOOK_FUNCTION is missing environment variable $required_name" ;;
    esac
  done

  note "Doctor checks passed"
}

ensure_client_id_file() {
  if [[ -f .env ]] && grep -Eq '^[[:space:]]*CLIENT_ID=' .env; then
    return 0
  fi

  local client_id
  client_id="$(aws_cmd lambda get-function-configuration \
    --function-name "$WEBHOOK_FUNCTION" \
    --query 'Environment.Variables.CLIENT_ID' \
    --output text)"

  if [[ -z "$client_id" || "$client_id" == "None" ]]; then
    die "Could not recover CLIENT_ID from $WEBHOOK_FUNCTION"
  fi

  touch .env
  printf '\nCLIENT_ID=%s\n' "$client_id" >> .env
  note "Added the non-secret Microsoft application client ID to .env"
}

test_project() {
  ensure_venv

  note "Running unit tests"
  "$VENV_DIR/bin/python" -m unittest discover -s tests -v

  note "Checking Python and shell syntax"
  "$VENV_DIR/bin/python" -m compileall -q \
    email_filter handlers webhook_handler.py setup_webhook.py setup_token_interactive.py
  bash -n scripts/email-filter.sh

  note "Tests passed"
}

microsoft_login() {
  ensure_venv
  require_aws_login
  ensure_client_id_file

  note "Opening Microsoft authentication in the browser"
  "$VENV_DIR/bin/python" setup_token_interactive.py
}

ensure_microsoft_token() {
  ensure_venv
  ensure_client_id_file

  if "$VENV_DIR/bin/python" setup_token_interactive.py --check; then
    return 0
  fi

  warn "The cached Microsoft token needs a browser refresh."
  microsoft_login
  "$VENV_DIR/bin/python" setup_token_interactive.py --check \
    || die "Microsoft authentication still failed after the browser login."
}

setup_webhook_subscription() {
  require_aws_login
  ensure_microsoft_token

  local url
  url="$(webhook_url)"

  note "Creating a secured Microsoft Graph subscription for $url"
  "$VENV_DIR/bin/python" setup_webhook.py --webhook-url "$url"

  note "Verifying the stored subscription record"
  aws_cmd dynamodb get-item \
    --table-name "$TOKEN_TABLE_NAME" \
    --key '{"id":{"S":"webhook-subscription"}}' \
    --projection-expression 'subscription_id, client_state' \
    --query '{SubscriptionId: Item.subscription_id.S, ClientStatePresent: Item.client_state.S != `null`}'
}

backup_lambda() {
  local function_name="$1"
  local stamp backup_dir download_url
  stamp="$(date +%Y%m%d-%H%M%S)"
  backup_dir="$ROOT_DIR/backups/$stamp"
  mkdir -p "$backup_dir"

  download_url="$(aws_cmd lambda get-function \
    --function-name "$function_name" \
    --query 'Code.Location' \
    --output text)"

  note "Backing up $function_name to $backup_dir/$function_name.zip"
  curl -fLsS "$download_url" -o "$backup_dir/$function_name.zip"
}

lambda_platform() {
  local architecture="$1"
  case "$architecture" in
    x86_64) printf 'linux/amd64\n' ;;
    arm64) printf 'linux/arm64\n' ;;
    *) die "Unsupported Lambda architecture: $architecture" ;;
  esac
}

build_webhook_package() {
  local runtime="$1"
  local architecture="$2"
  local python_version platform package_dir zip_path

  case "$runtime" in
    python3.11|python3.12|python3.13|python3.14) ;;
    *) die "Unsupported Lambda runtime for this build script: $runtime" ;;
  esac

  python_version="${runtime#python}"
  platform="$(lambda_platform "$architecture")"
  package_dir="$ROOT_DIR/webhook-package"
  zip_path="$ROOT_DIR/webhook-lambda.zip"

  rm -rf "$package_dir" "$zip_path"
  mkdir -p "$package_dir"

  note "Building webhook dependencies for $runtime on $architecture"
  docker run --rm \
    --platform "$platform" \
    -e HOME=/tmp \
    -v "$ROOT_DIR":/var/task:ro \
    -v "$package_dir":/asset-output \
    "python:${python_version}-slim" \
    sh -lc 'python -m pip install --no-cache-dir -r /var/task/requirements-webhook.txt -t /asset-output'

  cp webhook_handler.py "$package_dir/lambda_function.py"

  (
    cd "$package_dir"
    zip -qr "$zip_path" .
  )

  [[ -s "$zip_path" ]] || die "Webhook ZIP was not created."
  ls -lh "$zip_path"
}

deploy_webhook() {
  doctor
  test_project

  local runtime architecture
  runtime="$(aws_cmd lambda get-function-configuration \
    --function-name "$WEBHOOK_FUNCTION" \
    --query Runtime --output text)"
  architecture="$(aws_cmd lambda get-function-configuration \
    --function-name "$WEBHOOK_FUNCTION" \
    --query 'Architectures[0]' --output text)"

  backup_lambda "$WEBHOOK_FUNCTION"
  build_webhook_package "$runtime" "$architecture"

  note "Updating webhook code only; Lambda environment variables are untouched"
  aws_cmd lambda update-function-code \
    --function-name "$WEBHOOK_FUNCTION" \
    --zip-file fileb://webhook-lambda.zip >/dev/null
  aws_cmd lambda wait function-updated --function-name "$WEBHOOK_FUNCTION"

  setup_webhook_subscription
  status

  note "Webhook deployment complete"
}

upgrade_runtime() {
  doctor
  test_project

  local current_runtime architecture
  current_runtime="$(aws_cmd lambda get-function-configuration \
    --function-name "$WEBHOOK_FUNCTION" \
    --query Runtime --output text)"
  architecture="$(aws_cmd lambda get-function-configuration \
    --function-name "$WEBHOOK_FUNCTION" \
    --query 'Architectures[0]' --output text)"

  if [[ "$current_runtime" == "$TARGET_RUNTIME" ]]; then
    note "$WEBHOOK_FUNCTION already uses $TARGET_RUNTIME"
    return 0
  fi

  printf 'Upgrade %s from %s to %s? [y/N] ' "$WEBHOOK_FUNCTION" "$current_runtime" "$TARGET_RUNTIME"
  read -r answer
  case "$answer" in
    y|Y|yes|YES) ;;
    *) die "Runtime upgrade cancelled." ;;
  esac

  backup_lambda "$WEBHOOK_FUNCTION"
  build_webhook_package "$TARGET_RUNTIME" "$architecture"

  note "Updating Lambda runtime to $TARGET_RUNTIME"
  aws_cmd lambda update-function-configuration \
    --function-name "$WEBHOOK_FUNCTION" \
    --runtime "$TARGET_RUNTIME" >/dev/null
  aws_cmd lambda wait function-updated --function-name "$WEBHOOK_FUNCTION"

  note "Uploading the matching $TARGET_RUNTIME package"
  aws_cmd lambda update-function-code \
    --function-name "$WEBHOOK_FUNCTION" \
    --zip-file fileb://webhook-lambda.zip >/dev/null
  aws_cmd lambda wait function-updated --function-name "$WEBHOOK_FUNCTION"

  status
  note "Runtime upgrade complete"
}

status() {
  require_aws_login

  note "Webhook Lambda"
  aws_cmd lambda get-function-configuration \
    --function-name "$WEBHOOK_FUNCTION" \
    --query '{Runtime:Runtime,Architecture:Architectures[0],Handler:Handler,Timeout:Timeout,Memory:MemorySize,LastUpdate:LastUpdateStatus,Modified:LastModified}' \
    --output table

  note "Subscription manager Lambda"
  aws_cmd lambda get-function-configuration \
    --function-name "$SUBSCRIPTION_FUNCTION" \
    --query '{Runtime:Runtime,Architecture:Architectures[0],LastUpdate:LastUpdateStatus,Modified:LastModified}' \
    --output table

  note "Microsoft Graph subscription record"
  aws_cmd dynamodb get-item \
    --table-name "$TOKEN_TABLE_NAME" \
    --key '{"id":{"S":"webhook-subscription"}}' \
    --projection-expression 'subscription_id, client_state' \
    --query '{SubscriptionId: Item.subscription_id.S, ClientStatePresent: Item.client_state.S != `null`}'
}

logs_webhook() {
  require_aws_login
  aws_cmd logs tail "/aws/lambda/$WEBHOOK_FUNCTION" --follow
}

help_text() {
  cat <<'EOF'
Simple Email Filter operations

Routine commands:
  make bootstrap        Create a Python 3.14 virtual environment and install tools
  make doctor           Check AWS login, Docker, resources and Lambda configuration
  make test             Run unit tests and syntax checks
  make deploy-webhook   Backup, build, deploy and recreate the Graph subscription
  make setup-webhook    Refresh auth when needed and recreate only the subscription
  make microsoft-login  Force a Microsoft browser login and refresh the token cache
  make status           Show deployment and subscription status without secrets
  make logs-webhook     Follow webhook Lambda logs
  make upgrade-runtime  Explicitly move the webhook Lambda to Python 3.14

Defaults:
  AWS_PROFILE=email
  AWS_REGION=us-east-2

Override them for one command, for example:
  AWS_PROFILE=other AWS_REGION=ca-central-1 make doctor
EOF
}

command_name="${1:-help}"
case "$command_name" in
  help) help_text ;;
  bootstrap) bootstrap ;;
  doctor) doctor ;;
  test) test_project ;;
  status) status ;;
  microsoft-login) microsoft_login ;;
  setup-webhook) setup_webhook_subscription ;;
  deploy-webhook) deploy_webhook ;;
  upgrade-runtime) upgrade_runtime ;;
  logs-webhook) logs_webhook ;;
  *) die "Unknown command: $command_name. Run make help." ;;
esac
