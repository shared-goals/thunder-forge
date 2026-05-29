OLLA_VERSION ?= v0.0.27
OLLA_OS ?= macos
OLLA_ARCH ?= arm64
OLLA_BIN_DIR ?= .tmp/olla-bin
OLLA_BIN ?= $(OLLA_BIN_DIR)/olla
GATEWAY_NODE ?= studio
DAEMON_NODES ?= msm3
DAEMON_ADMIN_USER ?=
TF_USER ?= admin
SMOKE_MODEL ?= gpt-oss-20b-MXFP4-Q8
SMOKE_ALIAS ?= memory
OLLA_RELEASE_BASE := https://github.com/thushan/olla/releases/download/$(OLLA_VERSION)
OLLA_ASSET := olla_$(OLLA_VERSION)_$(OLLA_OS)_$(OLLA_ARCH).zip

# Known targets -- used to extract positional node name from command line
_KNOWN_TARGETS := help sync test lint check daemon-bootstrap daemon-restart daemon-smoke runtime-status config _olla-install

# Positional node arg: make <target> <node>  e.g.  make daemon-smoke msm3
NODE ?= $(filter-out $(_KNOWN_TARGETS), $(MAKECMDGOALS))

# Absorb positional node-name goals so Make doesn't error on unknown targets
ifneq ($(filter-out $(_KNOWN_TARGETS), $(MAKECMDGOALS)),)
$(filter-out $(_KNOWN_TARGETS), $(MAKECMDGOALS)):
	@:
endif

help:
	@echo "Usage: make <target> [<node>] [TF_USER=$(TF_USER)]"
	@echo ""
	@echo "  sync             Update uv environment"
	@echo "  test             Run pytest"
	@echo "  lint             Run ruff"
	@echo "  check            Run tests + lint"
	@echo "  daemon-bootstrap First-time setup: $(GATEWAY_NODE) (gateway) + inference node daemons"
	@echo "  daemon-restart   Update Olla binary + restart all daemons"
	@echo "  daemon-smoke     Smoke all layers (runtime/Olla/edge). TF_USER=$(TF_USER)"
	@echo "  runtime-status   Check oMLX health on inference nodes"
	@echo "  config           Generate Olla config from TF cluster config"
	@echo ""
	@echo "  No node: $(GATEWAY_NODE) + $(DAEMON_NODES)"
	@echo "  $(GATEWAY_NODE): gateway only (Olla + edge)"
	@echo "  <node>: inference node only"

sync:
	uv sync --upgrade

test:
	uv run pytest --tb=short -q

lint:
	uv run ruff check .

check: test lint

_olla-install:
	@set -eu; \
		mkdir -p "$(OLLA_BIN_DIR)"; \
		cd "$(OLLA_BIN_DIR)"; \
		asset="$(OLLA_ASSET)"; \
		base="$(OLLA_RELEASE_BASE)"; \
		echo "Installing Olla $(OLLA_VERSION) to $(OLLA_BIN)"; \
		curl -fsSLO "$$base/$$asset"; \
		curl -fsSLO "$$base/checksums.txt"; \
		expected=$$(awk -v asset="$$asset" '{ name=$$NF; sub("^\\\\./", "", name); if (name == asset) { print $$1; exit } }' checksums.txt); \
		if [ -z "$$expected" ]; then \
			echo "Error: checksum entry not found for $$asset" >&2; \
			exit 1; \
		fi; \
		actual=$$(shasum -a 256 "$$asset" | awk '{ print $$1 }'); \
		if [ "$$expected" != "$$actual" ]; then \
			echo "Error: checksum mismatch for $$asset" >&2; \
			exit 1; \
		fi; \
		tmpdir=$$(mktemp -d); \
		trap 'rm -rf "$$tmpdir"' EXIT INT TERM; \
		unzip -o -q "$$asset" -d "$$tmpdir"; \
		install -m 755 "$$tmpdir/olla" olla.new; \
		mv -f olla.new olla; \
		echo "Olla installed: $(OLLA_BIN)"

daemon-bootstrap:
	@set -eu; \
		node_arg="$(NODE)"; \
		if [ -z "$$node_arg" ] || [ "$$node_arg" = "$(GATEWAY_NODE)" ]; then \
			$(MAKE) -s _olla-install; \
			$(MAKE) -s config; \
			echo "Bootstrapping gateway daemons on $(GATEWAY_NODE); expect password prompts"; \
			uv run thunder-forge service setup-daemon --binary "$(OLLA_BIN)" --config configs/olla-config.yaml --timeout 300 --allow-sudo-prompt --apply; \
		fi; \
		if [ -z "$$node_arg" ] || [ "$$node_arg" != "$(GATEWAY_NODE)" ]; then \
			if [ -n "$$node_arg" ]; then infer_nodes="$$node_arg"; else infer_nodes="$(DAEMON_NODES)"; fi; \
			for n in $$infer_nodes; do \
				if [ -n "$(DAEMON_ADMIN_USER)" ]; then \
					echo "Bootstrapping oMLX daemon on $$n (su to $(DAEMON_ADMIN_USER)); expect password prompts"; \
					uv run thunder-forge runtime setup-daemon --node "$$n" --admin-user "$(DAEMON_ADMIN_USER)" --via-su --apply; \
				else \
					echo "Bootstrapping oMLX daemon on $$n (sudo as shag); expect shag sudo password prompt"; \
					uv run thunder-forge runtime setup-daemon --node "$$n" --ssh-admin --apply; \
				fi; \
			done; \
		fi

daemon-restart:
	@set -eu; \
		node_arg="$(NODE)"; \
		if [ -z "$$node_arg" ] || [ "$$node_arg" = "$(GATEWAY_NODE)" ]; then \
			$(MAKE) -s _olla-install; \
			$(MAKE) -s config; \
			echo "Restarting gateway services (olla + edge) on $(GATEWAY_NODE)"; \
			uv run thunder-forge service restart --service olla --manager daemon --binary "$(OLLA_BIN)" --config configs/olla-config.yaml --timeout 300 --apply; \
			uv run thunder-forge service restart --service edge --manager daemon --timeout 300 --apply; \
		fi; \
		if [ -z "$$node_arg" ] || [ "$$node_arg" != "$(GATEWAY_NODE)" ]; then \
			if [ -n "$$node_arg" ]; then infer_nodes="$$node_arg"; else infer_nodes="$(DAEMON_NODES)"; fi; \
			for n in $$infer_nodes; do \
				echo "Restarting oMLX daemon on $$n"; \
				uv run thunder-forge service restart --service omlx --node "$$n" --manager daemon --apply; \
			done; \
		fi

daemon-smoke:
	@set -eu; \
		node_arg="$(NODE)"; \
		if [ -z "$$node_arg" ] || [ "$$node_arg" != "$(GATEWAY_NODE)" ]; then \
			if [ -n "$$node_arg" ]; then infer_nodes="$$node_arg"; else infer_nodes="$(DAEMON_NODES)"; fi; \
			for n in $$infer_nodes; do \
				uv run thunder-forge runtime status --node "$$n"; \
			done; \
		fi; \
		uv run thunder-forge olla smoke --model "$(SMOKE_MODEL)" --alias "$(SMOKE_ALIAS)"; \
		uv run thunder-forge edge smoke --client-id "$(TF_USER)" --model "$(SMOKE_ALIAS)"

config:
	uv run thunder-forge generate-olla-config

runtime-status:
	@set -eu; \
		node_arg="$(NODE)"; \
		if [ -z "$$node_arg" ] || [ "$$node_arg" != "$(GATEWAY_NODE)" ]; then \
			if [ -n "$$node_arg" ]; then infer_nodes="$$node_arg"; else infer_nodes="$(DAEMON_NODES)"; fi; \
			for n in $$infer_nodes; do \
				uv run thunder-forge runtime status --node "$$n"; \
			done; \
		fi

.PHONY: help sync test lint check _olla-install daemon-bootstrap daemon-restart daemon-smoke runtime-status config

.DEFAULT_GOAL := help
