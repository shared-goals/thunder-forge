# AGENTS.md

## Stack

- Language / runtime: Python >= 3.12 (`pyproject.toml`)
- Package manager: `uv` (`.venv` in repo; the edge systemd unit runs `.venv/bin/thunder-forge` directly)
- Run / check / lint: `uv run pytest -q`, `uv run ruff check src/thunder_forge tests`, `make status` (live cluster), `make dev-test` / `make dev-check`
- Owner: shag (runtime), shared with gnezim; production lives on rock at `/mnt/samsung/thunder-forge`
- Access limits: rock has no broad sudo for agents — only the unit install/restart commands granted in sudoers; secrets live in the repo `.env` (loaded by `edge serve`), never in unit files
- Justified deviations: none (skeleton Conventions are restated in "Pre-MVP Principles" and "Commit Naming" below in this file's own words)

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
- Usage endpoints (`/usage`, `/usage/report`, `/usage/events`) are disabled (404) until `TF_EDGE_ADMIN_TOKEN` is set, then the page is anonymous while data paths require the bearer token; client API keys (`TF_USER_*`) authorize inference only, never usage.

## Live Cluster Operations

These rules apply to any change on the running cluster (nodes, edge, Olla, tfconfig, deployment) as opposed to in-repo development.

- Load your operational runbook or skill for this cluster before acting on live infrastructure; do not guess hosts, ports, paths, or deployment conventions.
- Diagnose before acting: read the file-backed evidence (`logs/tf-edge-access.jsonl`, `logs/tf-node-metrics.jsonl`, Olla logs) and attribute the failing layer before proposing a fix.
- Propose options with trade-offs and wait for the operator's decision; do not execute irreversible steps (restarts, kills, model placement changes) on your own.
- Prefer hostname conventions (`.lan` FQDNs) over raw IPs in configs and probes.
- Secrets live only in the repo `.env` (loaded by `edge serve` automatically); never in unit files, configs, commits, or chat output.
- After any cluster change, verify through the real path: `cluster smoke`, one alias chat through the edge, and a live `cluster status` — self-reported success without verification is not done.
- Keep rollback cheap: old config entries stay as comments for one-edit rollback, and node settings edits get a backup copy first.

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
