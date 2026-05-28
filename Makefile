help:
	@echo "Usage: make <target>"
	@echo ""
	@echo "  sync        Update uv environment"
	@echo "  test        Run pytest"
	@echo "  lint        Run ruff"
	@echo "  check       Run tests + lint"
	@echo "  config      Generate Olla config from TF cluster config"
	@echo "  olla-config Generate Olla config from TF cluster config"

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

.DEFAULT_GOAL := help
