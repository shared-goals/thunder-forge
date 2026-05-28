help:
	@echo "Usage: make <target>"
	@echo ""
	@echo "  sync        Update uv environment"
	@echo "  test        Run pytest"
	@echo "  lint        Run ruff"
	@echo "  check       Run tests + lint"
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

.DEFAULT_GOAL := help
