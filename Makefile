.DEFAULT_GOAL := help
SHELL := /bin/bash
OPS := bash scripts/email-filter.sh
LAMBDA_OPS := bash scripts/lambda-deploy.sh
MAILBOX_OPS := bash scripts/mailbox-cleanup.sh
MAILBOX_EXPORT := bash scripts/mailbox-export.sh
TEST_OPS := bash scripts/test.sh

.PHONY: help bootstrap doctor test status microsoft-login setup-webhook deploy-webhook upgrade-runtime logs-webhook mailbox-audit mailbox-report mailbox-review mailbox-export mailbox-prepare-apply mailbox-plan mailbox-apply-stage mailbox-apply mailbox-reset

help:
	@$(LAMBDA_OPS) help
	@echo
	@$(MAILBOX_OPS) help
	@echo
	@echo "Analysis export"
	@echo "  make mailbox-export  Build uploadable JSON, CSV and Excel analysis files"

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

mailbox-export:
	@$(MAILBOX_EXPORT)

mailbox-prepare-apply:
	@$(MAILBOX_OPS) prepare-apply

mailbox-plan:
	@$(MAILBOX_OPS) plan

mailbox-apply-stage:
	@$(MAILBOX_OPS) apply-stage

mailbox-apply:
	@$(MAILBOX_OPS) apply

mailbox-reset:
	@$(MAILBOX_OPS) reset
