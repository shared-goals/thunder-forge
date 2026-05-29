OLLA_VERSION ?= v0.0.27
OLLA_OS ?= macos
OLLA_ARCH ?= arm64
OLLA_BIN_DIR ?= .tmp/olla-bin
OLLA_BIN ?= $(OLLA_BIN_DIR)/olla
DAEMON_NODES ?= msm3
DAEMON_ADMIN_USER ?=
SMOKE_MODEL ?= gpt-oss-20b-MXFP4-Q8
SMOKE_ALIAS ?= memory
OLLA_RELEASE_BASE := https://github.com/thushan/olla/releases/download/$(OLLA_VERSION)
OLLA_ASSET := olla_$(OLLA_VERSION)_$(OLLA_OS)_$(OLLA_ARCH).zip

help:
	@echo "Usage: make <target>"
	@echo ""
	@echo "  sync        Update uv environment"
	@echo "  test        Run pytest"
	@echo "  lint        Run ruff"
	@echo "  check       Run tests + lint"
	@echo "  olla-install Install pinned Olla release into $(OLLA_BIN)"
	@echo "  olla-update  Alias for olla-install"
	@echo "  olla-restart Install Olla binary and restart local launchd service"
	@echo "  daemon-bootstrap First-time admin bootstrap for gateway + node daemon sudoers"
	@echo "  daemon-reinstall Reinstall/restart gateway + node daemons via narrow sudoers"
	@echo "  full-daemon-test Reinstall daemons and run runtime/Olla/edge smoke"
	@echo "  config      Generate Olla config from TF cluster config"
	@echo "  olla-config Generate Olla config from TF cluster config"
	@echo "  edge-keys   Generate local TF edge API keys in .env"
	@echo "  edge-usage  Summarize TF edge access log by client"

sync:
	uv sync --upgrade

test:
	uv run pytest --tb=short -q

lint:
	uv run ruff check .

check: test lint

olla-install:
	@set -eu; \
		mkdir -p "$(OLLA_BIN_DIR)"; \
		cd "$(OLLA_BIN_DIR)"; \
		asset="$(OLLA_ASSET)"; \
		base="$(OLLA_RELEASE_BASE)"; \
		echo "Installing Olla $(OLLA_VERSION) to $(OLLA_BIN)"; \
		curl -fsSLO "$$base/$$asset"; \
		curl -fsSLO "$$base/checksums.txt"; \
		expected=$$(awk -v asset="$$asset" '{ name=$$NF; sub("^\\./", "", name); if (name == asset) { print $$1; exit } }' checksums.txt); \
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

olla-update: olla-install

olla-restart: olla-install
	uv run thunder-forge service restart --service olla --binary "$(OLLA_BIN)" --config configs/olla-config.yaml --apply

daemon-bootstrap: olla-install config
	@set -eu; \
		echo "Bootstrapping gateway daemons through services.frontend.admin_user when configured"; \
		uv run thunder-forge service setup-daemon --binary "$(OLLA_BIN)" --config configs/olla-config.yaml --timeout 300 --allow-sudo-prompt --apply; \
		for node in $(DAEMON_NODES); do \
			if [ -n "$(DAEMON_ADMIN_USER)" ]; then \
				echo "Bootstrapping oMLX daemon on $$node (su to admin user $(DAEMON_ADMIN_USER)); expect two labeled password prompts: su then sudo"; \
				uv run thunder-forge runtime setup-daemon --node "$$node" --admin-user "$(DAEMON_ADMIN_USER)" --via-su --apply; \
			else \
				echo "Bootstrapping oMLX daemon on $$node (su to configured node admin_user); expect two labeled password prompts: su then sudo"; \
				uv run thunder-forge runtime setup-daemon --node "$$node" --via-su --apply; \
			fi; \
		done

daemon-reinstall: olla-install config
	@set -eu; \
		echo "Reinstalling/restarting frontend system daemons using preinstalled narrow sudoers; no password prompt expected"; \
		uv run thunder-forge service restart --service olla --manager daemon --binary "$(OLLA_BIN)" --config configs/olla-config.yaml --timeout 300 --apply; \
		uv run thunder-forge service restart --service edge --manager daemon --timeout 300 --apply; \
		for node in $(DAEMON_NODES); do \
			echo "Reinstalling/restarting existing oMLX system daemon on $$node using preinstalled narrow sudoers; no password prompt expected"; \
			uv run thunder-forge service restart --service omlx --node "$$node" --manager daemon --apply; \
		done

daemon-smoke:
	@if [ -z "$(EDGE_CLIENT)" ]; then \
		echo 'Usage: make daemon-smoke EDGE_CLIENT=<client-id>'; \
		exit 2; \
	fi
	@set -eu; \
		for node in $(DAEMON_NODES); do \
			uv run thunder-forge runtime status --node "$$node"; \
		done; \
		uv run thunder-forge olla smoke --model "$(SMOKE_MODEL)" --alias "$(SMOKE_ALIAS)"; \
		uv run thunder-forge edge smoke --client-id "$(EDGE_CLIENT)" --model "$(SMOKE_ALIAS)"

full-daemon-test:
	@if [ -z "$(EDGE_CLIENT)" ]; then \
		echo 'Usage: make full-daemon-test EDGE_CLIENT=<client-id>'; \
		exit 2; \
	fi
	$(MAKE) daemon-reinstall
	$(MAKE) daemon-smoke EDGE_CLIENT="$(EDGE_CLIENT)"

config:
	uv run thunder-forge generate-olla-config

olla-config:
	uv run thunder-forge generate-olla-config

edge-keys:
	@if [ -z "$(EDGE_CLIENTS)" ]; then \
		echo 'Usage: make edge-keys EDGE_CLIENTS="client-a client-b"'; \
		exit 2; \
	fi
	uv run thunder-forge edge keys $(foreach client,$(EDGE_CLIENTS),--client $(client))

edge-usage:
	uv run thunder-forge edge usage

.PHONY: help sync test lint check olla-install olla-update olla-restart daemon-bootstrap daemon-reinstall daemon-smoke full-daemon-test config olla-config edge-keys edge-usage

.DEFAULT_GOAL := help
