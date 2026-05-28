COMPOSE = docker compose -f docker/docker-compose.yml --env-file .env

help:
	@echo "Usage: make <target>"
	@echo ""
	@echo "  sync            Update local uv environment"
	@echo "  up              Build and start all services, removing orphaned containers"
	@echo "  down            Stop all services and remove orphaned containers"
	@echo "  restart         Stop and restart all services, removing orphaned containers"
	@echo "  ps              Show service status"
	@echo "  logs            Show logs (optional: s=<service>)"
	@echo "  setup-gateway   Bootstrap this machine as gateway node"
	@echo "  setup-node      Bootstrap this machine as compute node"
	@echo "  config          Regenerate LiteLLM config from node-assignments.yaml"
	@echo "  check           Verify gateway setup and service health"
	@echo "  check-docker    Test Docker network connectivity to PyPI"

sync:
	uv sync --upgrade

up: sync
	$(COMPOSE) up -d --build --remove-orphans

down:
	$(COMPOSE) down --remove-orphans

restart: sync
	$(COMPOSE) down --remove-orphans && $(COMPOSE) up -d --build --remove-orphans

ps:
	$(COMPOSE) ps

logs:
	$(COMPOSE) logs --tail=50 $(s)

setup-gateway:
	zsh scripts/setup-node.sh gateway

setup-node:
	zsh scripts/setup-node.sh node

config:
	uv run thunder-forge generate-config

check:
	zsh scripts/setup-node.sh gateway --check

check-docker:
	@echo "==> Proxy config inside container..."
	@docker run --rm python:3.12-slim env | grep -iE '^(https?_proxy|no_proxy)=' || echo "  (none — if behind a proxy, configure ~/.docker/config.json proxies)"
	@echo "==> Testing DNS resolution..."
	@docker run --rm python:3.12-slim python -c "import socket; ip = socket.getaddrinfo('pypi.org', 443)[0][4][0]; print(f'  pypi.org -> {ip}')" || echo "  FAIL: DNS resolution failed"
	@echo "==> Testing HTTPS connectivity to PyPI (timeout 30s)..."
	@docker run --rm python:3.12-slim pip install --dry-run --timeout 30 hatchling 2>&1 | tail -5
	@echo "==> Done. If DNS or HTTPS failed, check Docker DNS config (daemon.json) or firewall/VPN settings."

.PHONY: help sync up down restart ps logs config setup-gateway setup-node check check-docker
.DEFAULT_GOAL := help
