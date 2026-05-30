UV ?= uv run
OLLA_VERSION ?= v0.0.27
OLLA_OS ?= macos
OLLA_ARCH ?= arm64
OLLA_BIN_DIR ?= .tmp/olla-bin
OLLA_BIN ?= $(OLLA_BIN_DIR)/olla
DAEMON_ADMIN_USER ?=
TF_USER ?= admin
SMOKE_MODEL ?= gpt-oss-20b-MXFP4-Q8
SMOKE_ALIAS ?= memory

_TARGETS := help cli-help sync test lint check prepare daemon-bootstrap daemon-restart daemon-smoke runtime-status config
NODE ?= $(filter-out $(_TARGETS),$(MAKECMDGOALS))
ADMIN_ARG := $(if $(DAEMON_ADMIN_USER),--admin-user $(DAEMON_ADMIN_USER),)

ifneq ($(NODE),)
$(NODE):
	@:
endif

help:
	@echo "Thunder Forge make targets"
	@echo ""
	@echo "Usage:"
	@echo "  make <target> [node]"
	@echo ""
	@echo "Cluster:"
	@printf "  %-24s %s\n" "prepare [node]" "setup gateway/cache/inference daemons"
	@printf "  %-24s %s\n" "daemon-bootstrap [node]" "alias for prepare"
	@printf "  %-24s %s\n" "daemon-restart [node]" "restart gateway and inference daemons"
	@printf "  %-24s %s\n" "daemon-smoke [node]" "smoke runtime, Olla, and edge"
	@printf "  %-24s %s\n" "runtime-status [node]" "check oMLX health on inference nodes"
	@printf "  %-24s %s\n" "config" "generate configs/olla-config.yaml"
	@echo ""
	@echo "Developer:"
	@printf "  %-24s %s\n" "sync" "update the uv environment"
	@printf "  %-24s %s\n" "test" "run pytest"
	@printf "  %-24s %s\n" "lint" "run ruff"
	@printf "  %-24s %s\n" "check" "run tests and lint"
	@echo ""
	@echo "Reference:"
	@printf "  %-24s %s\n" "cli-help" "show thunder-forge CLI help"
	@echo ""
	@echo "Common variables:"
	@printf "  %-24s %s\n" "TF_USER" "$(TF_USER)"
	@printf "  %-24s %s\n" "SMOKE_MODEL" "$(SMOKE_MODEL)"
	@printf "  %-24s %s\n" "SMOKE_ALIAS" "$(SMOKE_ALIAS)"

cli-help:
	@$(UV) thunder-forge --help

sync:
	uv sync --upgrade

test:
	$(UV) pytest --tb=short -q

lint:
	$(UV) ruff check .

check: test lint

prepare daemon-bootstrap:
	$(UV) thunder-forge cluster prepare $(NODE) --apply $(ADMIN_ARG) --timeout 300 --olla-version "$(OLLA_VERSION)" --olla-os "$(OLLA_OS)" --olla-arch "$(OLLA_ARCH)" --olla-bin-dir "$(OLLA_BIN_DIR)"

daemon-restart:
	$(UV) thunder-forge cluster restart $(NODE) --apply --timeout 300 --binary "$(OLLA_BIN)"

daemon-smoke:
	$(UV) thunder-forge cluster smoke $(NODE) --model "$(SMOKE_MODEL)" --alias "$(SMOKE_ALIAS)" --client-id "$(TF_USER)"

runtime-status:
	$(UV) thunder-forge cluster status $(NODE)

config:
	$(UV) thunder-forge generate-olla-config

.PHONY: $(_TARGETS)
.DEFAULT_GOAL := help
