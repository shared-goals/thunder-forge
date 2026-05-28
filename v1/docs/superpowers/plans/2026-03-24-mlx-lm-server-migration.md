# mlx_lm.server Migration Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace vllm-mlx with mlx_lm.server as the inference backend across all deploy, preflight, and config code.

**Architecture:** Swap binary/args in plist generation, add vllm-mlx cleanup to deploy pipeline, update preflight checks. Health checks and LiteLLM config generation are unchanged (both servers are OpenAI-compatible).

**Tech Stack:** Python 3.12, pytest, launchd plists, uv tool management

**Spec:** `docs/superpowers/specs/2026-03-24-mlx-lm-server-migration-design.md`

---

### Task 1: Update plist generation — tests first

**Files:**
- Modify: `tests/test_deploy.py`
- Modify: `src/thunder_forge/cluster/deploy.py:18-110`

- [ ] **Step 1: Update test assertions for mlx_lm.server**

In `tests/test_deploy.py`, update all vllm-mlx references:

```python
# test_generate_plist_uses_resolved_fields (line 52)
# Change:
assert "/Users/admin/.local/bin/vllm-mlx" in xml_str
# To:
assert "/Users/admin/.local/bin/mlx_lm.server" in xml_str

# test_generate_plist_no_homebrew (line 87)
# Change:
assert "/home/admin/.local/bin/vllm-mlx" in xml_str
# To:
assert "/home/admin/.local/bin/mlx_lm.server" in xml_str

# test_generate_plist_basic (lines 114-120)
# Change:
assert "com.vllm-mlx-8000" in xml_str
assert "--continuous-batching" in xml_str
assert "no_proxy" in xml_str
# To:
assert "com.mlx-lm-8000" in xml_str
assert "--model" in xml_str
assert "--host" in xml_str
assert "0.0.0.0" in xml_str
assert "--continuous-batching" not in xml_str
assert "HF_HUB_OFFLINE" in xml_str

# test_generate_plist_with_embedding (lines 139-141)
# vllm-mlx had --embedding-model flag; mlx_lm.server does not.
# Replace entire test with one that verifies extra_args passthrough:
def test_generate_plist_with_extra_args(config_path: Path) -> None:
    config = load_cluster_config(config_path)
    model = config.models["coder"]
    model.extra_args = ["--trust-remote-code", "--log-level", "DEBUG"]
    slot = config.assignments["msm1"][0]
    node = config.nodes["msm1"]
    node.home_dir = "/Users/admin"
    node.homebrew_prefix = "/opt/homebrew"
    xml_str = generate_plist(model, slot, node)
    assert "--trust-remote-code" in xml_str
    assert "DEBUG" in xml_str
```

Also add a new test for log paths:

```python
def test_generate_plist_log_paths() -> None:
    """Logs go to ~/logs/mlx-lm-{port}.log, not /tmp/."""
    node = Node(
        ip="192.168.1.101", ram_gb=128, user="admin", role="node",
        home_dir="/Users/admin", homebrew_prefix="/opt/homebrew",
    )
    model = Model(source=ModelSource(type="huggingface", repo="test/model"), disk_gb=10)
    slot = Assignment(model="test", port=8000)
    xml_str = generate_plist(model, slot, node)
    assert "/Users/admin/logs/mlx-lm-8000.log" in xml_str
    assert "/Users/admin/logs/mlx-lm-8000.err" in xml_str
    assert "vllm" not in xml_str
```

- [ ] **Step 2: Run tests — verify they fail**

Run: `uv run pytest tests/test_deploy.py -v`
Expected: Multiple FAIL (assertions expect mlx_lm.server but code still generates vllm-mlx)

- [ ] **Step 3: Update `generate_plist()` in deploy.py**

Changes to `src/thunder_forge/cluster/deploy.py`:

```python
# Line 27: label
label = f"com.mlx-lm-{slot.port}"

# Line 28: binary path
server_path = f"{home}/.local/bin/mlx_lm.server"

# Lines 30-38: program_args — replace entire block
program_args = [
    server_path,
    "--model",
    model.source.repo,
    "--port",
    str(slot.port),
    "--host",
    "0.0.0.0",
]

# Lines 41-42: Remove embedding_model handling (mlx_lm.server doesn't support it)
# Delete:
#   if slot.embedding and embedding_model:
#       program_args.extend(["--embedding-model", embedding_model.source.repo])

# Lines 44-45: Keep extra_args passthrough
if model.extra_args:
    program_args.extend(model.extra_args)

# Lines 51-55: env_vars — replace no_proxy with HF_HUB_OFFLINE
env_vars = {
    "PATH": ":".join(path_parts),
    "HOME": home,
    "HF_HUB_OFFLINE": "1",
}

# Lines 96-97: log paths
add_key_value(d, "StandardOutPath", make_string(f"{home}/logs/mlx-lm-{slot.port}.log"))
add_key_value(d, "StandardErrorPath", make_string(f"{home}/logs/mlx-lm-{slot.port}.err"))
```

Also update the function signature — remove `embedding_model` parameter:

```python
def generate_plist(
    model: Model,
    slot: Assignment,
    node: Node,
) -> str:
```

- [ ] **Step 4: Run tests — verify they pass**

Run: `uv run pytest tests/test_deploy.py -v`
Expected: All PASS

- [ ] **Step 5: Commit**

```bash
git add src/thunder_forge/cluster/deploy.py tests/test_deploy.py
git commit -m "refactor: replace vllm-mlx with mlx_lm.server in plist generation"
```

---

### Task 2: Update deploy pipeline — migration + cleanup

**Files:**
- Modify: `src/thunder_forge/cluster/deploy.py:113-219`

- [ ] **Step 1: Update NEWSYSLOG_CONF**

```python
NEWSYSLOG_CONF = """\
# logfilename                             [owner:group] mode count size(KB) when  flags
{home}/logs/mlx-lm-*.log            {user}:staff     644  7     102400   *     CNJ
{home}/logs/mlx-lm-*.err            {user}:staff     644  7     102400   *     CNJ
"""
```

- [ ] **Step 2: Replace `upgrade_node_tools()` with `install_node_tools()`**

```python
def install_node_tools(node: Node) -> None:
    """Install mlx-lm (with socks proxy support) and remove legacy vllm-mlx."""
    # Remove legacy vllm-mlx if present
    ssh_run(node.user, node.ip, "uv tool uninstall vllm-mlx 2>/dev/null || true", timeout=30, shell=node.shell)
    # Install/reinstall mlx-lm with httpx[socks]
    result = ssh_run(
        node.user, node.ip,
        "uv tool install --force mlx-lm --with 'httpx[socks]'",
        timeout=120, shell=node.shell,
    )
    if result.returncode != 0:
        stderr = (result.stderr or "").strip()
        print(f"  Warning: mlx-lm install failed: {stderr or 'unknown error'} (continuing)")
    else:
        print("  mlx-lm installed")
```

- [ ] **Step 3: Update `deploy_node()` — labels, plist names, migration cleanup**

All `com.vllm-mlx-{port}` references become `com.mlx-lm-{port}`:

```python
# Line 150 (dry_run output):
print(f"    [upload] com.mlx-lm-{slot.port}.plist ({slot.model}, port {slot.port})")

# Line 160: call install_node_tools instead of upgrade_node_tools
install_node_tools(node)

# Line 166: remove embedding_model from generate_plist call
plist_xml = generate_plist(model, slot, node)

# Line 169: plist filename
plist_name = f"com.mlx-lm-{slot.port}.plist"

# Line 177: label
label = f"com.mlx-lm-{slot.port}"

# Line 198: newsyslog remote path
scp_content(node.user, node.ip, newsyslog, "/tmp/mlx-lm-newsyslog.conf", shell=node.shell)
ssh_run(node.user, node.ip, "sudo mv /tmp/mlx-lm-newsyslog.conf /etc/newsyslog.d/mlx-lm.conf", shell=node.shell)
```

Add vllm-mlx plist cleanup BEFORE deploying new plists (after uid_result, before install_node_tools):

```python
# Clean up legacy vllm-mlx plists
legacy_ls = ssh_run(
    node.user, node.ip,
    "ls ~/Library/LaunchAgents/com.vllm-mlx-*.plist 2>/dev/null || true",
    shell=node.shell,
)
if legacy_ls.stdout.strip():
    print("  Removing legacy vllm-mlx services...")
    for line in legacy_ls.stdout.strip().splitlines():
        filename = line.strip().split("/")[-1]
        try:
            port = int(filename.replace("com.vllm-mlx-", "").replace(".plist", ""))
            stale_label = f"com.vllm-mlx-{port}"
            cmd = f"launchctl bootout gui/{uid}/{stale_label} 2>/dev/null; rm ~/Library/LaunchAgents/{stale_label}.plist"
            ssh_run(node.user, node.ip, cmd, shell=node.shell)
        except ValueError:
            continue
    # Also remove old newsyslog conf
    ssh_run(node.user, node.ip, "sudo rm -f /etc/newsyslog.d/vllm-mlx.conf", shell=node.shell)
```

Update stale plist detection (lines 202-217) to use `com.mlx-lm-*`:

```python
ls_result = ssh_run(
    node.user, node.ip, "ls ~/Library/LaunchAgents/com.mlx-lm-*.plist 2>/dev/null || true", shell=node.shell
)
if ls_result.stdout.strip():
    for line in ls_result.stdout.strip().splitlines():
        filename = line.strip().split("/")[-1]
        try:
            port = int(filename.replace("com.mlx-lm-", "").replace(".plist", ""))
            if port not in deployed_ports:
                print(f"  Removing stale plist for port {port}")
                stale = f"com.mlx-lm-{port}"
                cmd = f"launchctl bootout gui/{uid}/{stale} 2>/dev/null; rm ~/Library/LaunchAgents/{stale}.plist"
                ssh_run(node.user, node.ip, cmd, shell=node.shell)
        except ValueError:
            continue
```

- [ ] **Step 4: Run tests**

Run: `uv run pytest tests/test_deploy.py -v`
Expected: All PASS

- [ ] **Step 5: Commit**

```bash
git add src/thunder_forge/cluster/deploy.py
git commit -m "feat: deploy pipeline migrates from vllm-mlx to mlx-lm"
```

---

### Task 3: Update preflight checks

**Files:**
- Modify: `src/thunder_forge/cluster/preflight.py:28-29,122-124`
- Modify: `tests/test_preflight.py`

- [ ] **Step 1: Update test assertions**

In `tests/test_preflight.py`:

```python
# test_node_includes_vllm_check (line 55-56)
# Change:
def test_node_includes_mlx_lm_check(self) -> None:
    script = build_probe_script(role="node")
    assert "mlx-lm" in script

# test_parses_valid_output (line 25) and all probe_output fixtures:
# Change all VLLM_OK=1 to MLX_LM_OK=1

# test_missing_uv_reported (line 147):
# Change VLLM_OK=0 to MLX_LM_OK=0
```

- [ ] **Step 2: Run tests — verify they fail**

Run: `uv run pytest tests/test_preflight.py -v`
Expected: FAIL

- [ ] **Step 3: Update preflight.py**

```python
# Line 29: change probe script check
# Was:
lines.append('uv tool list 2>/dev/null | grep -q vllm && echo "VLLM_OK=1" || echo "VLLM_OK=0"')
# Now:
lines.append('uv tool list 2>/dev/null | grep -q mlx-lm && echo "MLX_LM_OK=1" || echo "MLX_LM_OK=0"')

# Lines 122-124: change validation
# Was:
if data.get("VLLM_OK") != "1":
    errors.append(f"{name}: vllm-mlx not installed — run: setup-node.sh node")
# Now:
if data.get("MLX_LM_OK") != "1":
    errors.append(f"{name}: mlx-lm not installed — run: uv tool install --force mlx-lm --with 'httpx[socks]'")
```

- [ ] **Step 4: Run tests — verify they pass**

Run: `uv run pytest tests/test_preflight.py -v`
Expected: All PASS

- [ ] **Step 5: Commit**

```bash
git add src/thunder_forge/cluster/preflight.py tests/test_preflight.py
git commit -m "refactor: preflight checks for mlx-lm instead of vllm-mlx"
```

---

### Task 4: Update config and node-assignments

**Files:**
- Modify: `src/thunder_forge/cluster/config.py:228` (comment)
- Modify: `configs/node-assignments.yaml`

- [ ] **Step 1: Update config.py comment**

```python
# Line 228 (in generate_litellm_config):
# Change:
# Use "openai" provider — vllm-mlx is fully OpenAI-compatible.
# To:
# Use "openai" provider — mlx_lm.server is fully OpenAI-compatible.
```

- [ ] **Step 2: Update node-assignments.yaml**

Remove `extra_args` from qwen3-30b model definition (lines 53+, if present — the friend added it):

```yaml
  qwen3-30b:
    source:
      type: huggingface
      repo: "mlx-community/Qwen3-30B-A3B-4bit"
      revision: "main"
    disk_gb: 18
    ram_gb: 15
    kv_per_32k_gb: 0
    active_params: "3B of 30B"
    max_context: 131072
    notes: "MoE model, benchmarked 124 tok/s on M4 Max. Confirmed working with vllm-mlx continuous batching."
```

Update comment on line 121:

```yaml
# One mlx_lm.server process per entry.
```

- [ ] **Step 3: Run full test suite**

Run: `uv run pytest tests/ -v`
Expected: All PASS

- [ ] **Step 4: Run linter**

Run: `uv run ruff check src/ tests/`
Expected: No errors

- [ ] **Step 5: Commit**

```bash
git add src/thunder_forge/cluster/config.py configs/node-assignments.yaml
git commit -m "chore: update references from vllm-mlx to mlx_lm.server"
```

---

### Task 5: Verify generate-config still works

**Files:** None (validation only)

- [ ] **Step 1: Regenerate LiteLLM config**

Run: `FINN_LITELLM_KEY=test uv run thunder-forge generate-config`
Expected: Success, generates `configs/litellm-config.yaml`

- [ ] **Step 2: Validate config with --check**

Run: `FINN_LITELLM_KEY=test uv run thunder-forge generate-config --check`
Expected: Config in sync

- [ ] **Step 3: Run full test suite one final time**

Run: `uv run pytest tests/ -v && uv run ruff check src/ tests/`
Expected: All PASS, no lint errors

- [ ] **Step 4: Final commit with spec and plan**

```bash
git add docs/superpowers/
git commit -m "docs: mlx_lm.server migration spec and plan"
```
