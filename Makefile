.DEFAULT_GOAL := help
SHELL := /bin/bash
OPS := bash scripts/email-filter.sh
LAMBDA_OPS := bash scripts/lambda-deploy.sh

.PHONY: help bootstrap doctor test status microsoft-login setup-webhook deploy-webhook upgrade-runtime logs-webhook

help:
	@$(LAMBDA_OPS) help

bootstrap:
	@$(OPS) bootstrap

doctor:
	@$(OPS) doctor

test:
	@$(OPS) test

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
