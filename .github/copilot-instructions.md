# Copilot Instructions for Thunder Forge

## Project Principles

See [AGENTS.md](../../AGENTS.md) for the authoritative project guidelines on code quality, tooling, and commit conventions.

**Quick reference:**
- Follow DRY, KISS, YAGNI
- Use `uv` for Python dependencies and command execution
- Prefer `make` targets for routine workflows
- Fix primary paths, don't add backward-compatibility shims
- Single clear code path over layered fallback logic

## Agents

### `@tf-operator` — Cluster Architecture & Optimization

**Use when:** Planning cluster behavior changes, optimizing load-balancing, proposing routing improvements, or deciding whether work belongs in Thunder Forge vs. upstream (Olla/oMLX).

**Key principle:** This agent applies "upstream improvement + thin integration" — it will inspect whether features already exist in [oMLX](https://github.com/jundot/omlx) or [Olla](https://github.com/thushan/olla), and propose upstream contributions before adding local code.

**Input format:**
```
@tf-operator: <objective>, constraints: <constraints>, scope: <scope>
```

Example: `@tf-operator: reduce idle+queue overlap for memory model, constraints: no breaking changes to config, scope: routing behavior and metrics`.

## Skills

### Thunder Forge Skill

**When to use:** Working on oMLX/Olla integration, node operations, model placement, daemon setup, runtime configuration, vscode client setup, or production migration planning.

Triggers automatically in Thunder Forge v2 contexts (oMLX runtime, Olla routing, TF edge proxy, cluster operations). Contains standard commands, model selection guidance, production workflows, and troubleshooting.

## Commands & Workflows

All work should use implemented Thunder Forge commands or `make` targets for production operations. Avoid manual `ssh`, `rsync`, `launchctl`, or direct file moves unless the TF command does not exist.

Key workflows:
- `make bootstrap [node]` — Bootstrap oMLX daemon and verify health
- `make restart [node]` — Restart inference runtime and validation
- `make smoke [node]` — Run runtime smoke test
- `make sync [node]` — Sync model cache and reload
- `make status [node]` — Check node and model health
- `uv run thunder-forge --help` — Full command reference

## Code Quality

- **Tests:** Run `make dev-check` before commit; this covers linting, type checking, and unit tests
- **Architecture:** Prefer reuse of Olla routing and oMLX runtime behavior; minimize Thunder Forge-specific logic
- **Logs:** Normalize output for operators — suppress transient OS noise, contextualize errors, avoid raw stderr in user-facing messages

## Common Patterns

**Daemon setup and health checking:**
- Setup is lightweight (just script execution); health validation deferred to explicit restart
- Bootstrap flow: setup → explicit restart → validation

**Cluster restart resilience:**
- Single node failures are retried once before failing the whole operation
- Errors from multiple attempts are merged and labeled with attempt number

**Model routing:**
- `memory`/hindsight → prefer most-idle capable node with warm model cache
- `opencode`/`vscode` → maintain sticky session affinity
- Session keys should be scoped to conversation/session, not account (avoid pinning unrelated sessions to one node)
