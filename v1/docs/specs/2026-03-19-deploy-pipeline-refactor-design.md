# Deploy Pipeline Refactor — Design Spec

**Date:** 2026-03-19
**Status:** Approved

## Context

Testing the deploy pipeline on a MacBook Air revealed multiple issues preventing successful service deployment. This spec captures all fixes and improvements identified during testing.

## 1. `.env` in project root

**Goal:** Single place for operational config, loaded automatically by CLI.

**Files:**
- `.env` — working file, gitignored
- `.env.example` — template with comments, tracked in git

**Variables:**
```
TF_SSH_USER=admin
TF_SSH_KEY=~/.ssh/id_ed25519
HF_HOME=~/.cache/huggingface
```

**Loading:** `load_cluster_config()` in `config.py` loads `.env` from project root (found via `find_repo_root()`) as its first step, using `python-dotenv`. Environment variables take precedence — dotenv does not overwrite existing vars.

**Separation of concerns:**
- `node-assignments.yaml` — topology (what to deploy where, which models)
- `.env` — operational config (how to connect, where to store)

Per-node overrides (e.g., `user`) remain in `node-assignments.yaml`. If per-node env vars are needed in the future, add an `env:` field to node definitions in YAML.

## 2. deploy.py fixes

### 2a. Remove `--max-model-len`

vllm-mlx does not support this argument. Remove it from plist generation. *Already implemented locally.*

**Note:** `tests/test_deploy.py` currently asserts `--max-model-len` is present. These assertions must be updated as a first step to avoid broken tests.

### 2b. `no_proxy: "*"` in plist env

Add unconditionally for all inference nodes. vllm-mlx serves locally and never needs a proxy. *Already implemented locally.*

### 2c. `bootout` before `bootstrap`

Deploy always attempts `launchctl bootout` before `launchctl bootstrap`. Bootout errors (service not loaded) are silently ignored.

### 2d. LiteLLM restart — warning instead of failure with `--node`

When `--node` is specified, LiteLLM restart failure is a warning, not a fatal error. The change happens inside `run_deploy()`: when `target_node` is set, `restart_litellm()` failure does not set `all_ok = False`. Deploy succeeds (exit 0) if plist deployed and inference service is healthy.

### 2e. `uv tool upgrade --all` before service restart

Deploy runs `uv tool upgrade --all` on target node via SSH before restarting the service.

**uv binary path resolution:** Use the same pattern as vllm-mlx path — derive from `user_home` which is already computed per-node in `generate_plist()`. For macOS inference nodes: `/opt/homebrew/bin/uv` (Homebrew install). For Linux infra nodes: `/home/{user}/.local/bin/uv` (uv installer default). Determine via `node.role`: inference nodes are macOS, infra nodes are Linux.

**Failure handling:** If `uv tool upgrade --all` fails (network issue, uv bug), log a warning and continue deployment. The existing binaries still work — upgrade is best-effort.

## 3. cli.py

### `--skip-models` flag

Allows skipping `ensure-models` step during deploy. Useful when models are already on node or rock is unreachable. *Already implemented locally.*

## 4. setup-node.sh

### 4a. PATH in shell config files

**Inference nodes (macOS/zsh):** Write `export PATH="$HOME/.local/bin:$PATH"` to both `~/.zshenv` (non-interactive SSH) and `~/.zshrc` (interactive sessions). Check with `grep` before writing to avoid duplicates.

**Infra nodes (zsh):** Write to both `~/.zshenv` (non-interactive SSH) and `~/.zshrc` (interactive sessions). All machines in the cluster use zsh.

### 4b. Remove hardcoded IPs from "Next steps"

Replace `for ip in 192.168.1.{101,102,103,104}` with a generic placeholder that doesn't assume specific IPs.

### 4c. `uv tool upgrade --all` after install

Add at the end of setup for both roles to guarantee latest versions of all tool packages.

## 5. Tests

Update tests for changed behavior:
- Plist does not contain `--max-model-len`
- Plist env contains `no_proxy`
- `--skip-models` flag works

Do not modify tests unrelated to these changes.

## Files affected

| File | Changes |
|------|---------|
| `.env.example` | New file |
| `.gitignore` | Add `.env` |
| `pyproject.toml` | Add `python-dotenv` dependency |
| `src/thunder_forge/cluster/config.py` | Load `.env` from project root |
| `src/thunder_forge/cluster/deploy.py` | 2a, 2b, 2c, 2d, 2e |
| `src/thunder_forge/cli.py` | `--skip-models` flag |
| `scripts/setup-node.sh` | 4a, 4b, 4c |
| `tests/test_deploy.py` | Updated assertions |
