#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT_DIR"

if [[ ! -x .venv/bin/python ]]; then
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
  setup_webhook.py \
  setup_token_interactive.py

printf '\n==> Checking shell syntax\n'
bash -n \
  scripts/email-filter.sh \
  scripts/lambda-deploy.sh \
  scripts/mailbox-cleanup.sh \
  scripts/test.sh

printf '\n==> Tests passed\n'
