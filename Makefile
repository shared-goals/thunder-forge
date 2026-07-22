UV ?= uv run

_TARGETS := help cli-help dev dev-sync dev-test dev-lint dev-check bootstrap restart smoke status sync prune config model usage usage-json usage-trim usage-duckdb edge-keys edge-usage opencode hermes vscode
ARG ?= $(word 2,$(MAKECMDGOALS))
EDGE_CLIENTS ?=

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
	@printf "  %-24s %s\n" "model <hf-repo>" "verify HF source, download to cache hub, add unassigned tfconfig model entry"
	@printf "  %-24s %s\n" "prune [node]" "sync, prune unassigned node cache models, and restart runtime"
	@printf "  %-24s %s\n" "config" "generate config/olla-config.yaml"
	@printf "  %-24s %s\n" "usage [day]" "print usage summary (day: YYYY-MM-DD)"
	@printf "  %-24s %s\n" "usage-json [day]" "print usage summary as JSON"
	@printf "  %-24s %s\n" "usage-trim [days]" "trim local TF logs (default 3 days)"
	@printf "  %-24s %s\n" "usage-duckdb [day]" "run DuckDB daily usage SQL over JSONL logs"
	@printf "  %-24s %s\n" "edge-keys [clients]" "create missing TF edge client keys"
	@printf "  %-24s %s\n" "edge-usage" "summarize TF edge access logs"
	@printf "  %-24s %s\n" "opencode [id]" "create client key and print/copy OpenCode config"
	@printf "  %-24s %s\n" "hermes [id]" "create client key and print/copy Hermes config"
	@printf "  %-24s %s\n" "vscode [id]" "create client key and print/copy VS Code model config"
	@echo ""
	@echo "Developer:"
	@printf "  %-24s %s\n" "dev-sync" "update the uv environment"
	@printf "  %-24s %s\n" "dev-test" "run pytest"
	@printf "  %-24s %s\n" "dev-lint" "run ruff"
	@printf "  %-24s %s\n" "dev-check" "run tests and lint"
	@printf "  %-24s %s\n" "dev" "run dev-sync then dev-check"
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
	$(UV) ruff check src/thunder_forge tests

dev-check: dev-test dev-lint

dev: dev-sync dev-check

bootstrap:
	$(UV) thunder-forge cluster prepare $(ARG) --apply

restart:
	$(UV) thunder-forge cluster restart $(ARG) --apply

smoke:
	$(UV) thunder-forge cluster smoke $(ARG)

status:
	$(UV) thunder-forge cluster status $(ARG)

sync:
	@if [ -n "$(ARG)" ]; then \
		$(UV) thunder-forge cluster sync "$(ARG)" --apply; \
	else \
		nodes="$$($(UV) python -c 'from thunder_forge.cluster.config import NodeRole, load_config; config, _ = load_config(); print(" ".join(node_id for node_id, node in config.nodes.items() if node.has_role(NodeRole.INFERENCE)))')"; \
		if [ -z "$$nodes" ]; then echo 'no inference nodes configured in tfconfig.yaml'; exit 2; fi; \
		echo "sync scope: all inference nodes"; \
		for node in $$nodes; do \
			echo "== sync $$node =="; \
			$(UV) thunder-forge cluster sync "$$node" --apply || exit $$?; \
		done; \
	fi

model:
	@if [ -z "$(ARG)" ]; then echo 'usage: make model <huggingface-repo-id>'; exit 2; fi
	$(UV) thunder-forge config add-model --repo "$(ARG)"
	$(UV) thunder-forge artifact download --model "$(ARG)" --apply
	$(UV) thunder-forge config add-model --repo "$(ARG)" --apply

prune:
	@if [ -z "$(ARG)" ]; then echo 'usage: make prune <node>'; exit 2; fi
	$(UV) thunder-forge cluster sync "$(ARG)" --apply --prune

config:
	$(UV) thunder-forge generate-olla-config

usage:
	$(UV) thunder-forge usage report $(if $(strip $(ARG)),--period "$(ARG)")

usage-json:
	$(UV) thunder-forge usage report --json $(if $(strip $(ARG)),--period "$(ARG)")

usage-trim:
	$(UV) thunder-forge usage trim-logs $(if $(strip $(ARG)),--retention-days "$(ARG)")

usage-duckdb:
	@if ! command -v duckdb >/dev/null 2>&1; then \
		echo 'duckdb is not installed. Install duckdb CLI first.'; \
		exit 2; \
	fi
	duckdb -readonly -cmd ".mode table" -cmd ".headers on" -cmd ".param set period $(if $(strip $(ARG)),$(ARG),$(shell date -u +%F))" -f docs/operations/daily-usage-duckdb.sql

edge-keys:
	@clients="$(if $(strip $(EDGE_CLIENTS)),$(EDGE_CLIENTS),$(ARG))"; \
	if [ -z "$$clients" ]; then echo 'usage: make edge-keys EDGE_CLIENTS="client-a client-b"'; exit 2; fi; \
	args=""; \
	for client in $$clients; do args="$$args --client $$client"; done; \
	$(UV) thunder-forge edge keys $$args

edge-usage:
	$(UV) thunder-forge edge usage

opencode:
	@$(UV) thunder-forge edge client-config opencode --copy $(if $(strip $(ARG)),--inject-api-key --create-missing-key "$(ARG)")

hermes:
	@$(UV) thunder-forge edge client-config hermes --copy $(if $(strip $(ARG)),--create-missing-key "$(ARG)")

vscode:
	@$(UV) thunder-forge edge client-config vscode --copy $(if $(strip $(ARG)),--inject-api-key --create-missing-key "$(ARG)")

.PHONY: $(_TARGETS)
.DEFAULT_GOAL := help
