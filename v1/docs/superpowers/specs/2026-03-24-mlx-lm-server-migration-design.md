# Design: Migrate from vllm-mlx to mlx_lm.server

**Date:** 2026-03-24
**Status:** Approved

## Context

vllm-mlx is unstable: Qwen3-Coder crashes the engine, nodes fail to start reliably.
mlx_lm.server (Apple's official mlx-lm package) was tested locally on MacBook Air M3 24GB
and works correctly through LiteLLM proxy. It supports automatic continuous batching,
tool calling auto-detection, LRU prompt caching, and speculative decoding.

## Decision

Full replacement of vllm-mlx with mlx_lm.server. No dual-backend support.
Deploy handles migration cleanup (old plists, old package removal) automatically.

## Changes

### 1. Plist generation (`deploy.py`)

**Command change:**

```
# Was:
vllm-mlx serve <repo> --port 8000 --host 0.0.0.0 --continuous-batching [extra_args]

# Now:
mlx_lm.server --model <repo> --port 8000 --host 0.0.0.0 [extra_args]
```

- Label: `com.mlx-lm-{port}` (was `com.vllm-mlx-{port}`)
- Binary: `{home}/.local/bin/mlx_lm.server`
- Logs: `~/logs/mlx-lm-{port}.log` and `~/logs/mlx-lm-{port}.err` (was `/tmp/vllm-mlx-*`)
- `--host 0.0.0.0` and `--port 8000` explicit (mlx_lm.server defaults to 127.0.0.1:8080)
- No `serve` subcommand, no `--continuous-batching` (batching is automatic)
- `extra_args` from model config still appended (for future use)

### 2. Migration at deploy time (`deploy.py`)

`deploy_node()` performs these steps:

1. Find and stop all `com.vllm-mlx-*.plist` services via `launchctl bootout`, delete plist files
2. `uv tool uninstall vllm-mlx` (if installed, ignore errors)
3. `uv tool install --force mlx-lm --with 'httpx[socks]'` (replaces `uv tool upgrade vllm-mlx`)
4. Deploy new `com.mlx-lm-*.plist` services

Migration is idempotent: steps 1-2 are no-ops if nothing to clean up.

### 3. Stale service cleanup (`deploy.py`)

Update `deploy_node()` stale plist detection:
- Was: glob `com.vllm-mlx-*.plist`
- Now: glob `com.mlx-lm-*.plist`

Old vllm-mlx plists are cleaned in the migration step (2.1), not in stale detection.

### 4. Log rotation (`deploy.py`)

Newsyslog config patterns:
- Was: `vllm-mlx-*.log` and `vllm-mlx-*.err`
- Now: `mlx-lm-*.log` and `mlx-lm-*.err`
- Log dir: `~/logs/` (was `/tmp/`)

### 5. Preflight checks (`preflight.py`)

- Check `mlx-lm` in `uv tool list` output (was `vllm`)
- Error message: "mlx-lm not installed" (was "vllm-mlx not installed")

### 6. Config generation (`config.py`)

- Comment update: "mlx_lm.server is fully OpenAI-compatible" (was "vllm-mlx")
- No functional changes: provider stays `openai/`, api_base unchanged

### 7. node-assignments.yaml

- Remove `extra_args` from qwen3-30b model (vllm-mlx-specific flags: `--enable-auto-tool-choice`, `--reasoning-parser`)
- `extra_args` field remains in schema for future use
- Comment update: "One mlx_lm.server process per entry" (was "One vllm-mlx process")

### 8. Health checks (`health.py`)

No changes. Both servers expose `/v1/models` endpoint.

## What does NOT change

- LiteLLM config generation (same OpenAI-compatible provider)
- Model download/sync (`models.py`) — backend-agnostic
- Health check endpoints (`/v1/models`)
- Memory budget validation
- CLI commands and their interfaces
- Docker compose (LiteLLM, OpenWebUI, Postgres)
- External endpoints support

## Key mlx_lm.server defaults (no flags needed)

| Feature | Default | Notes |
|---------|---------|-------|
| Decode concurrency | 32 | Automatic batching |
| Prompt concurrency | 8 | Parallel prefill |
| Prompt cache size | 10 | LRU entries |
| Prompt cache bytes | unlimited | Fine for 128GB nodes |
| Tool calling | auto-detect | Qwen3 parser included |
| Speculative decoding | off | Available via `--draft-model` |

## Installation command

```bash
uv tool install --force mlx-lm --with 'httpx[socks]'
```

`--force` ensures correct variant with socks proxy support.
`httpx[socks]` prevents crashes when SOCKS proxy env vars are set.
