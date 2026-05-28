# Deploy Pipeline Refactor — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Fix deploy pipeline issues found during testing — broken vllm-mlx args, proxy interference, missing .env support, setup-node.sh PATH issues.

**Architecture:** Operational config moves to `.env` in project root (loaded via python-dotenv). deploy.py gets bootout-before-bootstrap, best-effort uv upgrade, and LiteLLM warning behavior. setup-node.sh writes PATH to correct shell config files per OS.

**Tech Stack:** Python 3.12+, python-dotenv, Typer, pytest, bash

**Spec:** `docs/specs/2026-03-19-deploy-pipeline-refactor-design.md`

---

## File Structure

| File | Action | Responsibility |
|------|--------|---------------|
| `.env.example` | Create | Template for operational env vars |
| `pyproject.toml` | Modify | Add python-dotenv dependency |
| `src/thunder_forge/cluster/config.py` | Modify | Load .env at start of load_cluster_config() |
| `src/thunder_forge/cluster/deploy.py` | Modify | Remove --max-model-len, add no_proxy, add uv upgrade, fix LiteLLM warning |
| `src/thunder_forge/cli.py` | Modify | --skip-models flag (already done, verify) |
| `scripts/setup-node.sh` | Modify | PATH in zshenv+zshrc, remove hardcoded IPs, add uv upgrade |
| `tests/test_deploy.py` | Modify | Update assertions for removed --max-model-len, added no_proxy |
| `tests/test_config.py` | Modify | Test .env loading |

---

### Task 1: Fix test assertions for already-changed deploy.py

**Files:**
- Modify: `tests/test_deploy.py:55-56`

- [ ] **Step 1: Run tests to confirm current failure**

Run: `uv run pytest tests/test_deploy.py -v`
Expected: FAIL on `test_generate_plist_basic` — `--max-model-len` not in XML

- [ ] **Step 2: Update test assertions**

In `tests/test_deploy.py`, replace lines 55-56:
```python
    assert "--max-model-len" in xml_str
    assert "131072" in xml_str
```
with:
```python
    assert "--max-model-len" not in xml_str
    assert "no_proxy" in xml_str
```

- [ ] **Step 3: Run tests to verify pass**

Run: `uv run pytest tests/test_deploy.py -v`
Expected: All PASS

- [ ] **Step 4: Commit**

```bash
git add tests/test_deploy.py
git commit -m "test: update plist assertions for removed --max-model-len and added no_proxy"
```

---

### Task 2: Add python-dotenv and .env support

**Files:**
- Modify: `pyproject.toml:7` (dependencies)
- Modify: `src/thunder_forge/cluster/config.py:95-100` (load_cluster_config)
- Create: `.env.example`

- [ ] **Step 1: Write failing test for .env loading**

Add to `tests/test_config.py`:
```python
def test_load_cluster_config_loads_dotenv(assignments_yaml: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """load_cluster_config loads .env from repo root."""
    monkeypatch.delenv("HF_HOME", raising=False)

    # Create .env next to configs/ (find_repo_root() will find this)
    repo_root = assignments_yaml.parent.parent
    (repo_root / ".git").mkdir(exist_ok=True)  # find_repo_root() needs a git marker
    dotenv_path = repo_root / ".env"
    dotenv_path.write_text("HF_HOME=/test/hf/cache\n")

    load_cluster_config(assignments_yaml)

    import os
    assert os.environ.get("HF_HOME") == "/test/hf/cache"

```

- [ ] **Step 2: Run test to verify failure**

Run: `uv run pytest tests/test_config.py::test_load_cluster_config_loads_dotenv -v`
Expected: FAIL — HF_HOME not set

- [ ] **Step 3: Add python-dotenv dependency**

In `pyproject.toml`, add `"python-dotenv>=1.0"` to the dependencies list (lines 6-9):
```toml
dependencies = [
    "python-dotenv>=1.0",
    "pyyaml>=6.0",
    "typer>=0.15",
]
```

Run: `uv sync`

- [ ] **Step 4: Add .env loading to load_cluster_config**

In `src/thunder_forge/cluster/config.py`, add import at top (after existing imports):
```python
from dotenv import load_dotenv
```

At the start of `load_cluster_config()` (after the docstring, before YAML loading), add:
```python
    # Load .env from repo root (env vars take precedence)
    repo_root = find_repo_root()
    env_file = repo_root / ".env"
    if env_file.exists():
        load_dotenv(env_file, override=False)
```

Note: `find_repo_root()` is already defined in `config.py` (line 217).

- [ ] **Step 5: Run test to verify pass**

Run: `uv run pytest tests/test_config.py::test_load_cluster_config_loads_dotenv -v`
Expected: PASS

- [ ] **Step 6: Create .env.example**

Create `.env.example` at project root:
```
# Thunder Forge — operational config
# Copy to .env and adjust values. Environment variables take precedence.

# Default SSH user for inference nodes (overridden by per-node 'user' in node-assignments.yaml)
TF_SSH_USER=admin

# SSH key path
TF_SSH_KEY=~/.ssh/id_ed25519

# HuggingFace cache directory (must have enough space for model weights)
HF_HOME=~/.cache/huggingface
```

- [ ] **Step 7: Run full test suite**

Run: `uv run pytest tests/ -v`
Expected: All PASS

- [ ] **Step 8: Commit**

```bash
git add pyproject.toml src/thunder_forge/cluster/config.py .env.example tests/test_config.py uv.lock
git commit -m "feat: add .env support for operational config"
```

---

### Task 3: Add uv tool upgrade to deploy

**Files:**
- Modify: `src/thunder_forge/cluster/deploy.py:106-167` (deploy_node function)

- [ ] **Step 1: Add upgrade_node_tools function**

In `src/thunder_forge/cluster/deploy.py`, add before `deploy_node()`:
```python
def upgrade_node_tools(node: Node) -> None:
    """Best-effort upgrade of uv-managed tools on a node."""
    if node.role == "inference":
        uv_path = "/opt/homebrew/bin/uv"
    else:
        user_home = f"/home/{node.user}"
        uv_path = f"{user_home}/.local/bin/uv"

    result = ssh_run(node.user, node.ip, f"{uv_path} tool upgrade --all", timeout=120)
    if result.returncode != 0:
        print(f"  Warning: uv tool upgrade failed on {node.ip} (continuing)")
    else:
        print(f"  Tools upgraded")
```

- [ ] **Step 2: Call upgrade_node_tools in deploy_node**

In `deploy_node()`, after `mkdir` commands (line 118) and before `uid_result` (line 120), add:
```python
    upgrade_node_tools(node)
```

- [ ] **Step 3: Run tests**

Run: `uv run pytest tests/ -v`
Expected: All PASS (deploy_node is not called in unit tests)

- [ ] **Step 4: Commit**

```bash
git add src/thunder_forge/cluster/deploy.py
git commit -m "feat: upgrade uv tools on node before service restart"
```

---

### Task 4: LiteLLM restart — warning when --node specified

**Files:**
- Modify: `src/thunder_forge/cluster/deploy.py:198-236` (run_deploy function)

- [ ] **Step 1: Update run_deploy signature and logic**

In `run_deploy()`, change the LiteLLM restart block (lines 219-224) from:
```python
    print("\nRestarting LiteLLM...")
    if restart_litellm(config):
        print("  LiteLLM restarted")
    else:
        print("  LiteLLM restart failed")
        all_ok = False
```
to:
```python
    print("\nRestarting LiteLLM...")
    if restart_litellm(config):
        print("  LiteLLM restarted")
    else:
        if target_node:
            print("  Warning: LiteLLM restart failed (non-fatal with --node)")
        else:
            print("  LiteLLM restart failed")
            all_ok = False
```

Also update the health poll loop (lines 227-234) to only check deployed nodes:
```python
    print("\nWaiting for services to become healthy...")
    for node_name in deploy_nodes:
        node = config.nodes[node_name]
        for slot in config.assignments[node_name]:
            healthy = health_poll(node.ip, slot.port)
            status = "healthy" if healthy else "timeout"
            print(f"  {node_name}:{slot.port} ({slot.model}): {status}")
            if not healthy:
                all_ok = False
```

- [ ] **Step 2: Run tests**

Run: `uv run pytest tests/ -v`
Expected: All PASS

- [ ] **Step 3: Commit**

```bash
git add src/thunder_forge/cluster/deploy.py
git commit -m "fix: LiteLLM restart failure is warning when --node specified"
```

---

### Task 5: Fix setup-node.sh

**Files:**
- Modify: `scripts/setup-node.sh`

- [ ] **Step 1: Fix PATH writing for inference (macOS)**

Replace the uv PATH block in `setup_inference()` (around line 81):
```bash
        echo 'export PATH="$HOME/.local/bin:$PATH"' >> ~/.zshrc
```
with:
```bash
        PATH_LINE='export PATH="$HOME/.local/bin:$PATH"'
        grep -qF "$PATH_LINE" ~/.zshenv 2>/dev/null || echo "$PATH_LINE" >> ~/.zshenv
        grep -qF "$PATH_LINE" ~/.zshrc 2>/dev/null || echo "$PATH_LINE" >> ~/.zshrc
```

- [ ] **Step 2: Fix PATH writing for infra (Linux)**

Replace the uv PATH block in `setup_infra()` (around line 133):
```bash
        echo 'export PATH="$HOME/.local/bin:$PATH"' >> ~/.zshrc
```
with:
```bash
        PATH_LINE='export PATH="$HOME/.local/bin:$PATH"'
        grep -qF "$PATH_LINE" ~/.profile 2>/dev/null || echo "$PATH_LINE" >> ~/.profile
        grep -qF "$PATH_LINE" ~/.bashrc 2>/dev/null || echo "$PATH_LINE" >> ~/.bashrc
```

- [ ] **Step 3: Remove hardcoded IPs from "Next steps"**

Replace lines 219-222:
```bash
    echo "  1. Copy SSH public key to inference nodes:"
    echo "     for ip in 192.168.1.{101,102,103,104}; do"
    echo "       ssh-copy-id -i $TF_SSH_KEY \$USER@\$ip"
    echo "     done"
```
with:
```bash
    echo "  1. Copy SSH public key to each inference node:"
    echo "     ssh-copy-id -i $TF_SSH_KEY <user>@<inference-node-ip>"
```

- [ ] **Step 4: Add uv tool upgrade --all at end of setup_inference**

Before the "Inference node setup complete" echo block, add:
```bash
    # 6. Upgrade all uv tools to latest
    echo "Upgrading uv tools..."
    uv tool upgrade --all 2>/dev/null || true
```

- [ ] **Step 5: Add uv tool upgrade --all at end of setup_infra**

After `uv sync` (line 173) and before docker/.env generation, add:
```bash
    # Upgrade all uv tools to latest
    echo "Upgrading uv tools..."
    uv tool upgrade --all 2>/dev/null || true
```

- [ ] **Step 6: Commit**

```bash
git add scripts/setup-node.sh
git commit -m "fix: PATH in zshenv+zshrc, remove hardcoded IPs, add uv upgrade"
```

---

### Task 6: Verify --skip-models flag and run full suite

**Files:**
- Verify: `src/thunder_forge/cli.py` (already has --skip-models)

- [ ] **Step 1: Verify --skip-models exists in cli.py**

Check that `deploy()` in `cli.py` has `skip_models` parameter and conditional logic.

- [ ] **Step 2: Run lint**

Run: `uv run ruff check src/ tests/`
Expected: No errors

- [ ] **Step 3: Run format**

Run: `uv run ruff format src/ tests/`

- [ ] **Step 4: Run full test suite**

Run: `uv run pytest tests/ -v`
Expected: All PASS

- [ ] **Step 5: Final commit (if any formatting changes)**

```bash
git add -u
git commit -m "chore: lint and format"
```
