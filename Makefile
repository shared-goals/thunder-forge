SHELL := /bin/zsh

UV ?= uv

PORT ?= 8000
BIND ?= 127.0.0.1

.PHONY: sync serve stop test format coverage check-i18n setup-env hosts push-hosts

sync:
	$(UV) sync --extra dev

serve:
	$(MAKE) stop
	$(UV) run thunder-forge serve

stop:
	@pids=$$(lsof -ti tcp:$(PORT) 2>/dev/null || true); \
	if [[ -n "$$pids" ]]; then \
		echo "Stopping processes on port $(PORT): $$pids"; \
		kill $$pids 2>/dev/null || true; \
		sleep 0.2; \
		kill -9 $$pids 2>/dev/null || true; \
	else \
		echo "No process on port $(PORT)"; \
	fi

test:
	$(UV) run pytest -q

format:
	$(UV) run ruff format .

coverage:
	$(UV) run pytest --cov

check-i18n:
	$(UV) run python -c "import json; from pathlib import Path; p=Path('src/static/mini_app/translations.json'); data=json.loads(p.read_text(encoding='utf-8')); assert isinstance(data, dict); [(_ for _ in ()).throw(AssertionError()) for k,v in data.items() if not (isinstance(k,str) and k and isinstance(v,str))]; print('ok')"

# Configure Thunderbolt networking and hosts from inventory
setup-env:
	$(UV) run python scripts/setup_env.py tbnet

hosts:
	$(UV) run python scripts/setup_env.py hosts

push-hosts:
	$(UV) run python scripts/setup_env.py push-hosts
