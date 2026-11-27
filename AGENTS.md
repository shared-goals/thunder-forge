# AGENTS.md

## What this repository is

Thunder Forge is the control plane for a private inference cluster, not a library: TF edge (an OpenAI-compatible gateway behind the Caddy reverse proxy), Olla (router) and oMLX runtimes on four Apple-Silicon nodes — one gateway/cache node and three inference nodes. Read `README.md` before changing behaviour; `docs/operations/` holds the operations notes (usage contract, DuckDB SQL, cache fabric, release steps); `tfconfig.example.yaml` is the committed config surface. `tfconfig.yaml`, `.env` and `logs/` are gitignored, so the live config, the secrets and the logs stay out of Git.

- Language / runtime: Python >= 3.12 (`pyproject.toml`); package manager `uv` (`.venv` in the repo; the edge LaunchAgent prefers `.venv/bin/thunder-forge` and falls back to `uv run thunder-forge`).
- Owner: shag (runtime), shared with gnezim; production lives on rock at `/mnt/samsung/thunder-forge`.
- Access limits: rock grants agents no broad sudo — only the install/restart commands listed in sudoers. Secrets live in the repo `.env`, loaded by `edge serve`; the edge LaunchAgent holds none.

Local agents run against this cluster every day, so a broken main is not a theoretical risk.

## Pre-MVP Principles

- Follow DRY, KISS, and YAGNI by default.
- Prefer a single clear code path over layered fallback logic.
- Use `uv` for Python dependency management and command execution (for example: `uv sync`, `uv run pytest`).
- Prefer existing `make` targets for routine workflows when available (for example: `make dev-test`, `make dev-lint`, `make dev-check`).
- Do not add backward-compatibility shims unless explicitly requested.
- If a path is broken, fix the primary path directly instead of adding alternate execution paths.

## Status Endpoints

- Keep `/health` and `/status` anonymously accessible during pre-MVP.
- Anonymous `/status` must be a liveness and version check: node health, readiness and the gateway and runtime versions. No live request identities or client ids, token-like material, node hosts or admin URLs, model memory (`actual_size`, `idle_seconds`, `last_access`) or probe errors may appear in an anonymous response; those belong behind `TF_EDGE_ADMIN_TOKEN`. Where a build still exposes one of them, that is a defect to fix, not a permission.
- Treat CLI status as an HTTP consumer of edge status APIs, not a secondary status implementation.
- Usage endpoints (`/usage`, `/usage/report`, `/usage/events`) are disabled (404) until `TF_EDGE_ADMIN_TOKEN` is set, then the page is anonymous while data paths require the bearer token; client API keys (`TF_USER_*`) authorize inference only, never usage.

## Work selection and tracker

- GitHub issues and pull requests in this repository are the tracker. Tracker mutations outside this repository need an explicit instruction.
- One PR is one purpose: a fix, one behaviour change, or one documentation/rule change. Mixed PRs make the deploy-versus-merge question unanswerable.
- Branch names follow the type of work: `feat/…`, `fix/…`, `docs/…`, `refactor/…`.
- Do not merge your own PR, do not enable auto-merge, and never force-push a branch someone else may have pulled. Merge is the owner's decision.

## Authority, safety and the live cluster

These rules apply to any change on the running cluster (nodes, edge, Olla, tfconfig, deployment) as opposed to in-repo development.

- The cluster serves live traffic. Default to read-only: `make status`, `make usage`, `/health`, `/status`, `/usage/report` and `/usage/events` with the admin token, and reading `logs/*.jsonl`.
- Mutating actions need an explicit instruction for that exact target: `make restart`, `make sync`, `make prune`, `make bootstrap`, `make model`, `make usage-trim` (it deletes log records), installing or removing a runtime, changing `tfconfig.yaml` on a host, killing a process, changing model placement, deleting cached models. Propose options with trade-offs and wait for the operator's decision instead of executing them.
- Load the operational runbook or skill for this cluster before acting on live infrastructure; do not guess hosts, ports, paths or deployment conventions.
- Diagnose before acting: read the file-backed evidence (`logs/tf-edge-access.jsonl`, `logs/tf-node-metrics.jsonl`, Olla logs) and attribute the failing layer before proposing a fix.
- Prefer hostname conventions (`.lan` FQDNs) over raw IPs in configs and probes.
- After a cluster change, verify through the real path: `cluster smoke`, one alias chat through the edge, and a live `cluster status`. Self-reported success is not verification.
- Keep rollback cheap: leave old config entries as comments for a one-edit rollback, and copy a node settings file before editing it.
- Merged code is not a deployed cluster. Keep four separate statements: implemented, published (PR merged), deployed (which commit is running), observed (what the logs and `/status` show). Never report the first as evidence of the last.
- Secrets: API keys, `TF_USER_*` values, `TF_EDGE_ADMIN_TOKEN`, SSH keys and `.env` content stay out of configs, commits, chat output, tests, docs, logs and PR bodies. Use synthetic fixtures.
- Model aliases are a public contract. Adding an alias is a compatible change; removing or renaming one needs a stated reason and one change that updates `tfconfig.yaml`, docs, every client configuration you know about, and a check that traffic to the old name has stopped. An alias that is merely broken for a client is worse than an alias that was never published.
- Access and node-metric logs are personal data: client ids, client IPs and usage patterns. `logs/*.jsonl` never enters Git, raw log lines do not leave the owner's machines, and no field may carry prompt content, keys or headers.

## Checks

- Gate before publishing: `uv run pytest -q` and `uv run ruff check .` (`make dev-check` runs the tests plus `make dev-lint`, which covers `src/thunder_forge` and `tests`).
- A change to a config key, a payload field, a log field or a CLI flag updates the documentation that describes it in the same PR. An undocumented field drifts within days.
- New non-trivial logic — a branch, a loop, a parser, a report aggregation, anything about money, privacy or data retention — ships with a test, or with an assert-based self-check when a test is impractical.
- Anything that touches the serving path is exercised from a worktree, never from the serving checkout.
- Say in the PR body what was verified, how it was verified, and what was not verified. Unverified claims state their limit instead of being dropped.

## Deployment provenance

- The edge must publish the build it executes: the package version and the git commit, visible in `/status` and in one startup log line. Removing, bypassing or defaulting that is a regression; an empty value must read as a visible "unknown", not a plausible-looking string. A build that publishes neither has an unknown build, and that is the honest answer until it does.
- After a deploy, compare the published commit with the merged commit. If it differs, deploy is not finished and the reason is unknown — say exactly that rather than assuming.

## Versioned publication

- Every change published to `main` carries a version: a bump in both `__version__` (`src/thunder_forge/__init__.py`) and `version` (`pyproject.toml`), a `CHANGELOG.md` entry, an annotated tag `v<version>` on that commit, and a GitHub release with the same notes. The four must agree.
- PATCH by default, for fixes, tests, documentation and rules that do not extend or deprecate the public contract. MINOR for a compatible addition or deprecation of that contract — a new model alias, a new endpoint, a changed payload field — or a substantial functional slice. MAJOR only after 1.0; until then an incompatible change needs an explicit contract decision and the next MINOR.
- Group related commits into one published release; each later published change gets its own version and tag. Preserve published tags.
- Read `docs/operations/release.md` when publishing a version: it holds the commands and the verification.

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
