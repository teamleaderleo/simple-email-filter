.DEFAULT_GOAL := help
SHELL := /bin/bash
OPS := bash scripts/email-filter.sh
LAMBDA_OPS := bash scripts/lambda-deploy.sh
MAILBOX_OPS := bash scripts/mailbox-cleanup.sh
TEST_OPS := bash scripts/test.sh

.PHONY: help bootstrap doctor test status microsoft-login setup-webhook deploy-webhook upgrade-runtime logs-webhook mailbox-audit mailbox-report mailbox-review mailbox-apply mailbox-reset

help:
	@$(LAMBDA_OPS) help
	@echo
	@$(MAILBOX_OPS) help

bootstrap:
	@$(OPS) bootstrap

doctor:
	@$(OPS) doctor

test:
	@$(TEST_OPS)

status:
	@$(OPS) status

microsoft-login:
	@$(OPS) microsoft-login

setup-webhook:
	@$(OPS) setup-webhook

deploy-webhook:
	@$(LAMBDA_OPS) deploy-webhook

upgrade-runtime:
	@$(LAMBDA_OPS) upgrade-runtime

logs-webhook:
	@$(OPS) logs-webhook

mailbox-audit:
	@$(MAILBOX_OPS) audit

mailbox-report:
	@$(MAILBOX_OPS) report

mailbox-review:
	@$(MAILBOX_OPS) review

mailbox-apply:
	@$(MAILBOX_OPS) apply

mailbox-reset:
	@$(MAILBOX_OPS) reset
