UV ?= uv run

_TARGETS := help cli-help dev-sync dev-test dev-lint dev-check bootstrap restart smoke status sync config opencode-config
NODE ?= $(word 2,$(MAKECMDGOALS))
OPENCODE_PROVIDER_ID ?= thunder-forge
OPENCODE_PROVIDER_NAME ?= Thunder Forge
OPENCODE_API_KEY_ENV ?= TF_USER_OPENCODE
OPENCODE_BASE_URL ?=
OPENCODE_MODEL ?=
OPENCODE_SMALL_MODEL ?=
OPENCODE_OUTPUT ?=
OPENCODE_CONFIG_FORMAT ?= jsonc
OPENCODE_CONFIG_ARGS := --provider-id "$(OPENCODE_PROVIDER_ID)" --provider-name "$(OPENCODE_PROVIDER_NAME)" --api-key-env "$(OPENCODE_API_KEY_ENV)" --format "$(OPENCODE_CONFIG_FORMAT)"
ifneq ($(strip $(OPENCODE_BASE_URL)),)
OPENCODE_CONFIG_ARGS += --base-url "$(OPENCODE_BASE_URL)"
endif
ifneq ($(strip $(OPENCODE_MODEL)),)
OPENCODE_CONFIG_ARGS += --model "$(OPENCODE_MODEL)"
endif
ifneq ($(strip $(OPENCODE_SMALL_MODEL)),)
OPENCODE_CONFIG_ARGS += --small-model "$(OPENCODE_SMALL_MODEL)"
endif

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
	@printf "  %-24s %s\n" "bootstrap [node]" "setup gateway/cache/inference daemons"
	@printf "  %-24s %s\n" "restart [node]" "restart gateway and inference daemons"
	@printf "  %-24s %s\n" "smoke [node]" "smoke runtime, Olla, and edge"
	@printf "  %-24s %s\n" "status [node]" "check oMLX health on inference nodes"
	@printf "  %-24s %s\n" "sync [node]" "sync configured models and restart node runtime"
	@printf "  %-24s %s\n" "config" "generate configs/olla-config.yaml"
	@printf "  %-24s %s\n" "opencode-config" "print OpenCode provider config from tfconfig.yaml"
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
	$(UV) thunder-forge cluster prepare $(NODE) --apply

restart:
	$(UV) thunder-forge cluster restart $(NODE) --apply

smoke:
	$(UV) thunder-forge cluster smoke $(NODE)

status:
	$(UV) thunder-forge cluster status $(NODE)

sync:
	@if [ -z "$(NODE)" ]; then echo 'usage: make sync <node>'; exit 2; fi
	$(UV) thunder-forge cluster sync "$(NODE)" --apply

config:
	$(UV) thunder-forge generate-olla-config

opencode-config:
	@if [ -n "$(OPENCODE_OUTPUT)" ]; then \
		mkdir -p "$$(dirname "$(OPENCODE_OUTPUT)")"; \
		$(UV) thunder-forge edge opencode-config $(OPENCODE_CONFIG_ARGS) > "$(OPENCODE_OUTPUT)"; \
		echo "wrote $(OPENCODE_OUTPUT)"; \
	else \
		$(UV) thunder-forge edge opencode-config $(OPENCODE_CONFIG_ARGS); \
	fi

.PHONY: $(_TARGETS)
.DEFAULT_GOAL := help
