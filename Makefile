help:
	@echo "Usage: make <target>"
	@echo ""
	@echo "  sync        Update uv environment"
	@echo "  test        Run pytest"
	@echo "  lint        Run ruff"
	@echo "  check       Run tests + lint"
	@echo "  config      Generate LiteLLM config from node-assignments"
	@echo "  olla-config Generate Olla config from node-assignments"

sync:
	uv sync --upgrade

test:
	uv run pytest --tb=short -q

lint:
	uv run ruff check .

check: test lint

config:
	uv run thunder-forge generate-config --apply

olla-config:
	uv run thunder-forge generate-olla-config --apply

.DEFAULT_GOAL := help
