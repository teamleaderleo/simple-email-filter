#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT_DIR"

if [[ ! -x .venv/bin/python ]] \
  || ! .venv/bin/python -c 'import sys, awscrt, boto3, dotenv, msal, requests; raise SystemExit(0 if sys.version_info[:2] == (3, 14) else 1)' >/dev/null 2>&1; then
  bash scripts/email-filter.sh bootstrap
fi

printf '\n==> Running unit tests\n'
.venv/bin/python -m unittest discover -s tests -v

printf '\n==> Checking Python syntax\n'
.venv/bin/python -m compileall -q \
  email_filter \
  handlers \
  webhook_handler.py \
  mailbox_cleanup.py \
  mailbox_export.py \
  junk_backfill.py \
  setup_webhook.py \
  setup_token_interactive.py

printf '\n==> Checking shell syntax\n'
bash -n \
  scripts/email-filter.sh \
  scripts/lambda-deploy.sh \
  scripts/mailbox-cleanup.sh \
  scripts/mailbox-apply-stage-all.sh \
  scripts/mailbox-export.sh \
  scripts/junk-backfill.sh \
  scripts/test.sh

printf '\n==> Tests passed\n'
