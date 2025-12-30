# thunder-forge (th) Copilot Instructions

DRY/KISS/YAGNI are main principles! Follow repo conventions exactly; if something conflicts or is unclear, stop and ask (don’t guess).

## Big Picture
- Stack: FastAPI backend + Telegram Mini App (python-telegram-bot) + Telegram Mini App (vanilla JS).
- Key dirs: `src/api/` (FastAPI + webhook + Mini App API + MCP), `src/bot/` (handlers/config), `src/services/` (business logic), `src/static/mini_app/` (Mini App assets + `translations.json`), `tests/{unit,contract,integration}`.

## Runtime & Dev Workflows (use these)
- Use: `make sync`, `make serve`, `make stop`, `make test`, `make format`, `make coverage`, `make check-i18n`.
- Use `uv` for all Python execution.
	- Targeted tests: `uv run pytest tests/path::test_name -v`
	- One-off scripts: `uv run python -c "..."`
- Don’t run `uvicorn`/`python`/`pytest` directly for app lifecycle; use the Make targets (which should use `uv run`).
- Don’t kill ports manually; use `make stop`.

## Reference & Docs
- For library/framework documentation and examples, prefer using the `#context7` MCP tools.
- Use `#githubrepo https://github.com/shared-goals/sosenki` as the primary reference for architecture and project layout decisions.

## HTTP/App Layout
- FastAPI app is in `src/api/webhook.py` for DB lifecycle.
- Routes/mounts:
	- `POST /webhook/telegram` → converts JSON to `telegram.Update` and calls `_bot_app.process_update()`.
	- `GET /health`.
	- `GET /mini-app/*` → serves static Mini App from `src/static/mini_app/`.
	- `POST /api/mini-app/*` → Mini App API (auth + context + data).

## Auth & Authorization (Mini App)
- Telegram init data transport order (see `src/services/auth_service.py`):
	- `Authorization: tma <raw>`
	- `X-Telegram-Init-Data`
	- request body fields `initDataRaw|initData|init_data_raw|init_data`
- Signature verification is centralized; endpoints should call `verify_telegram_auth()` → `get_authenticated_user()`.
- Target-user resolution rules: admin context switch (`selected_user_id`) > representation (`representative_id`) > self.
- For account-scoped endpoints, enforce `authorize_account_access*()` instead of hand-rolled checks.

## Service-Layer Conventions
- Keep business logic in `src/services/*_service.py`; handlers/endpoints should be thin.
- Audit logging lives in the service layer: call `AuditService` after `session.flush()` (not in bot handlers/routes).
- Formatting/parsing: use `src/services/locale_service.py` and `src/utils/parsers.py` (no custom currency/decimal parsing).

## i18n (Mini App + API)
- Single source of truth: `src/static/mini_app/translations.json` (flat keys with prefixes like `btn_`, `err_`, `msg_`, etc.).
- Python uses `src/services/localizer.py` `t(key, **kwargs)`; Mini App JS uses `t()` + `data-i18n` in HTML.
- After changing user-facing strings, run `make check-i18n`.


<!-- MANUAL ADDITIONS START -->
<!-- MANUAL ADDITIONS END -->
