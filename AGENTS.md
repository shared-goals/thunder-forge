# AGENTS.md

## Pre-MVP Principles

- Follow DRY, KISS, and YAGNI by default.
- Prefer a single clear code path over layered fallback logic.
- Use `uv` for Python dependency management and command execution (for example: `uv sync`, `uv run pytest`).
- Prefer existing `make` targets for routine workflows when available (for example: `make dev-test`, `make dev-lint`, `make dev-check`).
- Do not add backward-compatibility shims unless explicitly requested.
- If a path is broken, fix the primary path directly instead of adding alternate execution paths.

## Status Endpoints

- Keep `/health` and `/status` anonymously accessible during pre-MVP.
- Treat CLI status as an HTTP consumer of edge status APIs, not a secondary status implementation.

## Commit Naming

When creating commits in this repository, use this subject format:

- `<type>: <summary>`

Preferred `type` values (aligned with current branch history):

- `feat`
- `fix`
- `refactor`
- `docs`
- `test`
- `chore`

Subject line rules:

- Use lowercase `type`.
- Write summary in imperative mood.
- Keep summary concise and specific.
- Avoid trailing punctuation.
- Prefer 72 characters or fewer.

Examples:

- `feat: add linux systemd frontend and gateway manager support`
- `fix: handle gateway verify commands on linux systemd`
- `refactor: simplify frontend manager dispatch`
- `docs: add rock vscode implementation runbook`
