.DEFAULT_GOAL := help
SHELL := /bin/bash
OPS := bash scripts/email-filter.sh
LAMBDA_OPS := bash scripts/lambda-deploy.sh
MAILBOX_OPS := bash scripts/mailbox-cleanup.sh
MAILBOX_CONTINUOUS := bash scripts/mailbox-apply-stage-all.sh
MAILBOX_HIGH_LEVEL := bash scripts/mailbox-ops.sh
MAILBOX_EXPORT := bash scripts/mailbox-export.sh
JUNK_BACKFILL := bash scripts/junk-backfill.sh
TEST_OPS := bash scripts/test.sh

.PHONY: help bootstrap doctor test status microsoft-login setup-webhook deploy-webhook upgrade-runtime logs-webhook mailbox-check mailbox-analyze mailbox-clean mailbox-audit mailbox-report mailbox-review mailbox-export mailbox-prepare-apply mailbox-plan mailbox-apply-stage mailbox-apply-stage-all mailbox-apply mailbox-reset junk-backfill-audit junk-backfill-report junk-backfill-apply junk-backfill-reset

help:
	@$(MAILBOX_HIGH_LEVEL) help
	@echo
	@$(LAMBDA_OPS) help
	@echo
	@$(MAILBOX_OPS) help
	@echo
	@echo "Analysis export"
	@echo "  make mailbox-export  Build uploadable JSON, CSV and Excel analysis files"
	@echo
	@$(JUNK_BACKFILL) help

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

mailbox-check:
	@$(MAILBOX_HIGH_LEVEL) check

mailbox-analyze:
	@$(MAILBOX_HIGH_LEVEL) analyze

mailbox-clean:
	@$(MAILBOX_HIGH_LEVEL) clean

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

mailbox-apply-stage-all:
	@$(MAILBOX_CONTINUOUS)

mailbox-apply:
	@$(MAILBOX_OPS) apply

mailbox-reset:
	@$(MAILBOX_OPS) reset

junk-backfill-audit:
	@$(JUNK_BACKFILL) audit

junk-backfill-report:
	@$(JUNK_BACKFILL) report

junk-backfill-apply:
	@$(JUNK_BACKFILL) apply

junk-backfill-reset:
	@$(JUNK_BACKFILL) reset
