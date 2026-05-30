UV ?= uv run

_TARGETS := help cli-help dev-sync dev-test dev-lint dev-check bootstrap restart smoke status sync config client
ARG ?= $(word 2,$(MAKECMDGOALS))

ifneq ($(ARG),)
$(ARG):
	@:
endif

help:
	@echo "Thunder Forge make targets"
	@echo ""
	@echo "Usage:"
	@echo "  make <target> [node|client]"
	@echo ""
	@echo "Cluster:"
	@printf "  %-24s %s\n" "bootstrap [node]" "setup gateway/cache/inference daemons"
	@printf "  %-24s %s\n" "restart [node]" "restart gateway and inference daemons"
	@printf "  %-24s %s\n" "smoke [node]" "smoke runtime, Olla, and edge"
	@printf "  %-24s %s\n" "status [node]" "check oMLX health on inference nodes"
	@printf "  %-24s %s\n" "sync [node]" "sync configured models and restart node runtime"
	@printf "  %-24s %s\n" "config" "generate configs/olla-config.yaml"
	@printf "  %-24s %s\n" "client [id]" "create client key and print/copy OpenCode config"
	@echo ""
	@echo "Developer:"
	@printf "  %-24s %s\n" "dev-sync" "update the uv environment"
	@printf "  %-24s %s\n" "dev-test" "run pytest"
	@printf "  %-24s %s\n" "dev-lint" "run ruff"
	@printf "  %-24s %s\n" "dev-check" "run tests and lint"
	@echo ""
	@echo "Reference:"
	@printf "  %-24s %s\n" "cli-help" "show thunder-forge CLI help"
	@echo ""
	@echo "Common variables:"
	@printf "  %-24s %s\n" "UV" "$(UV)"
	@echo ""
	@echo "Operator defaults live in tfconfig.yaml under operations. Secrets live in .env."

cli-help:
	@$(UV) thunder-forge --help

dev-sync:
	uv sync --upgrade

dev-test:
	$(UV) pytest --tb=short -q

dev-lint:
	$(UV) ruff check .

dev-check: dev-test dev-lint

bootstrap:
	$(UV) thunder-forge cluster prepare $(ARG) --apply

restart:
	$(UV) thunder-forge cluster restart $(ARG) --apply

smoke:
	$(UV) thunder-forge cluster smoke $(ARG)

status:
	$(UV) thunder-forge cluster status $(ARG)

sync:
	@if [ -z "$(ARG)" ]; then echo 'usage: make sync <node>'; exit 2; fi
	$(UV) thunder-forge cluster sync "$(ARG)" --apply

config:
	$(UV) thunder-forge generate-olla-config

client:
	@$(UV) thunder-forge edge opencode-config --copy $(if $(strip $(ARG)),--inject-api-key --create-missing-key --yes "$(ARG)")

.PHONY: $(_TARGETS)
.DEFAULT_GOAL := help
