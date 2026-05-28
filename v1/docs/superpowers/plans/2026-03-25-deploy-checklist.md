# Deploy Checklist Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a "Check Status" button to the Deploy page that runs a 5-step pipeline check (config, ssh, model, service, port) for each assignment slot and renders compact status rows before the Deploy button.

**Architecture:** All check logic lives in a new `admin/thunder_admin/checks.py`. `run_all_checks` runs per-slot checks in parallel via `ThreadPoolExecutor`, reusing the SSH connection opened by `check_ssh` for subsequent SSH checks. Results are stored in `st.session_state` and invalidated when the config version changes. The Deploy page calls `run_all_checks` on button click and renders one row per slot using `st.columns`.

**Tech Stack:** paramiko (already in `admin/requirements.txt`), httpx (already in `admin/requirements.txt`), `concurrent.futures.ThreadPoolExecutor`, Streamlit session state.

**Spec:** `docs/superpowers/specs/2026-03-25-deploy-checklist-design.md`

---

## File Structure

### New files

- `admin/thunder_admin/checks.py` — All 5 check functions + `run_all_checks` entry point
- `tests/test_admin_checks.py` — Unit tests for all check functions and `run_all_checks`

### Modified files

- `admin/thunder_admin/pages/deploy.py` — Add Check Status button, session state handling, and per-slot result rows

---

## Task 1: `check_config` — static config validation

**Files:**
- Create: `admin/thunder_admin/checks.py`
- Create: `tests/test_admin_checks.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_admin_checks.py
"""Tests for admin deploy checks."""
from __future__ import annotations

from unittest.mock import MagicMock, patch


# --- check_config ---

def _valid_config() -> dict:
    return {
        "models": {
            "llama": {
                "source": {"type": "huggingface", "repo": "mlx-community/Llama-3.2-3B-Instruct-4bit"},
                "disk_gb": 2.0,
                "ram_gb": None,
            }
        },
        "nodes": {"msm1": {"ip": "10.0.0.1", "ram_gb": 64, "role": "node", "user": "admin"}},
        "assignments": {"msm1": [{"model": "llama", "port": 8000, "embedding": False}]},
        "external_endpoints": [],
    }


def test_check_config_ok():
    from thunder_admin.checks import check_config

    status, detail = check_config(_valid_config())
    assert status == "ok"
    assert detail == ""


def test_check_config_error_missing_model():
    from thunder_admin.checks import check_config

    config = _valid_config()
    config["assignments"]["msm1"][0]["model"] = "nonexistent"
    status, detail = check_config(config)
    assert status == "error"
    assert "nonexistent" in detail


def test_check_config_error_message_capped_at_120_chars():
    from thunder_admin.checks import check_config

    config = _valid_config()
    # Create many errors: reference non-existent models on many ports
    config["assignments"]["msm1"] = [{"model": f"missing_{i}", "port": 8000 + i} for i in range(10)]
    status, detail = check_config(config)
    assert status == "error"
    assert len(detail) <= 120
```

- [ ] **Step 2: Run test to verify it fails**

```bash
uv run pytest tests/test_admin_checks.py -v
```
Expected: `ImportError: cannot import name 'check_config' from 'thunder_admin.checks'` (file doesn't exist yet)

- [ ] **Step 3: Create `checks.py` with `check_config`**

```python
# admin/thunder_admin/checks.py
"""Pre-deploy status checks for each assignment slot."""
from __future__ import annotations

import os
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Literal

import paramiko

from thunder_admin.config import validate_config
from thunder_forge.cluster.config import Assignment, ClusterConfig, Node, parse_cluster_config

CheckResult = tuple[Literal["ok", "warn", "error", "skip"], str]
SlotChecks = dict[str, CheckResult]

_SSH_TIMEOUT = 10


def check_config(config: dict) -> CheckResult:
    """Static config validation — no I/O. Returns all errors joined, capped at 120 chars."""
    errors = validate_config(config)
    if not errors:
        return ("ok", "")
    joined = "; ".join(errors)
    return ("error", joined[:120])
```

- [ ] **Step 4: Run test to verify it passes**

```bash
uv run pytest tests/test_admin_checks.py -v
```
Expected: all 3 tests PASS

- [ ] **Step 5: Commit**

```bash
git add admin/thunder_admin/checks.py tests/test_admin_checks.py
git commit -m "feat: add checks.py with check_config (static validation)"
```

---

## Task 2: `check_ssh` — paramiko connectivity to compute node

**Files:**
- Modify: `admin/thunder_admin/checks.py`
- Modify: `tests/test_admin_checks.py`

`check_ssh` opens a paramiko connection to `node.ip` using `node.user` and the SSH key available in the container. Returns `(CheckResult, SSHClient | None)` — the open client is returned on success for reuse by subsequent SSH checks.

SSH key resolution (same pattern as `admin/thunder_admin/deploy.py`):
1. Use `/tmp/ssh_key` if it exists
2. Otherwise use `GATEWAY_SSH_KEY` env var
3. Otherwise fall back to `~/.ssh/id_ed25519`

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_admin_checks.py`:

```python
# --- check_ssh ---

def test_check_ssh_ok():
    from thunder_admin.checks import check_ssh
    from thunder_forge.cluster.config import Node

    node = Node(ip="10.0.0.1", ram_gb=64, user="admin")
    mock_client = MagicMock()
    mock_stdout = MagicMock()
    mock_stdout.read.return_value = b"ok\n"
    mock_client.exec_command.return_value = (MagicMock(), mock_stdout, MagicMock())

    with patch("thunder_admin.checks.paramiko.SSHClient", return_value=mock_client):
        with patch("thunder_admin.checks._resolve_ssh_key", return_value=MagicMock()):
            result, conn = check_ssh(node)

    assert result == ("ok", "")
    assert conn is mock_client


def test_check_ssh_timeout():
    from thunder_admin.checks import check_ssh
    from thunder_forge.cluster.config import Node

    node = Node(ip="10.0.0.1", ram_gb=64, user="admin")
    mock_client = MagicMock()
    mock_client.connect.side_effect = TimeoutError("timed out")

    with patch("thunder_admin.checks.paramiko.SSHClient", return_value=mock_client):
        with patch("thunder_admin.checks._resolve_ssh_key", return_value=MagicMock()):
            result, conn = check_ssh(node)

    assert result == ("error", "SSH timeout")
    assert conn is None


def test_check_ssh_unexpected_exception():
    from thunder_admin.checks import check_ssh
    from thunder_forge.cluster.config import Node

    node = Node(ip="10.0.0.1", ram_gb=64, user="admin")
    mock_client = MagicMock()
    mock_client.connect.side_effect = Exception("host key mismatch")

    with patch("thunder_admin.checks.paramiko.SSHClient", return_value=mock_client):
        with patch("thunder_admin.checks._resolve_ssh_key", return_value=MagicMock()):
            result, conn = check_ssh(node)

    assert result[0] == "error"
    assert "host key mismatch" in result[1]
    assert conn is None
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
uv run pytest tests/test_admin_checks.py::test_check_ssh_ok tests/test_admin_checks.py::test_check_ssh_timeout tests/test_admin_checks.py::test_check_ssh_unexpected_exception -v
```
Expected: FAIL — `check_ssh` not defined yet

- [ ] **Step 3: Add `_resolve_ssh_key` and `check_ssh` to `checks.py`**

Append to `admin/thunder_admin/checks.py`:

```python
def _resolve_ssh_key() -> paramiko.PKey:
    """Resolve SSH private key from container paths, in priority order."""
    local_key = "/tmp/ssh_key"
    if os.path.exists(local_key):
        return paramiko.PKey.from_path(local_key)
    env_key = os.environ.get("GATEWAY_SSH_KEY")
    if env_key and os.path.exists(env_key):
        return paramiko.PKey.from_path(env_key)
    default = os.path.expanduser("~/.ssh/id_ed25519")
    return paramiko.PKey.from_path(default)


def check_ssh(node: Node) -> tuple[CheckResult, paramiko.SSHClient | None]:
    """Open a paramiko SSH connection to the compute node. Returns (result, client).

    The returned SSHClient should be passed to check_model and check_service for
    connection reuse. Caller is responsible for closing the client.
    """
    client = paramiko.SSHClient()
    client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    try:
        pkey = _resolve_ssh_key()
        client.connect(
            hostname=node.ip,
            username=node.user,
            pkey=pkey,
            timeout=_SSH_TIMEOUT,
            look_for_keys=False,
            allow_agent=False,
        )
        _, stdout, _ = client.exec_command("echo ok", timeout=_SSH_TIMEOUT)
        out = stdout.read().decode().strip()
        if out == "ok":
            return ("ok", ""), client
        return ("error", f"unexpected response: {out[:60]}"), None
    except (TimeoutError, OSError) as e:
        if "timed out" in str(e).lower() or isinstance(e, TimeoutError):
            return ("error", "SSH timeout"), None
        return ("error", str(e)[:120]), None
    except Exception as e:
        client.close()
        return ("error", str(e)[:120]), None
```

- [ ] **Step 4: Run tests to verify they pass**

```bash
uv run pytest tests/test_admin_checks.py -v
```
Expected: all tests PASS

- [ ] **Step 5: Commit**

```bash
git add admin/thunder_admin/checks.py tests/test_admin_checks.py
git commit -m "feat: add check_ssh with paramiko connection and key resolution"
```

---

## Task 3: `check_model` — HF cache presence via SSH

**Files:**
- Modify: `admin/thunder_admin/checks.py`
- Modify: `tests/test_admin_checks.py`

For `huggingface` source: run `ls ~/.cache/huggingface/hub/models--{slug}/` where slug = `source.repo` with `/` replaced by `--`. For all other source types: return `("warn", "non-HF source; skipping model check")`.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_admin_checks.py`:

```python
# --- check_model ---

def _make_ssh_conn(stdout_output: bytes, exit_code: int = 0) -> MagicMock:
    """Return a mock SSHClient whose exec_command returns the given stdout."""
    mock_client = MagicMock()
    mock_stdout = MagicMock()
    mock_stdout.read.return_value = stdout_output
    mock_channel = MagicMock()
    mock_channel.recv_exit_status.return_value = exit_code
    mock_stdout.channel = mock_channel
    mock_client.exec_command.return_value = (MagicMock(), mock_stdout, MagicMock())
    return mock_client


def test_check_model_hf_found():
    from thunder_admin.checks import check_model
    from thunder_forge.cluster.config import Assignment, ClusterConfig, Model, ModelSource, Node

    node = Node(ip="10.0.0.1", ram_gb=64, user="admin")
    slot = Assignment(model="llama", port=8000)
    cluster = ClusterConfig(
        models={"llama": Model(source=ModelSource(type="huggingface", repo="mlx-community/Llama-3.2-3B"))},
        nodes={"msm1": node},
        assignments={"msm1": [slot]},
    )
    conn = _make_ssh_conn(b"snapshots\nrefs\n", exit_code=0)

    result = check_model(conn, node, slot, cluster)
    assert result == ("ok", "")
    # Verify the correct path was checked
    call_args = conn.exec_command.call_args[0][0]
    assert "models--mlx-community--Llama-3.2-3B" in call_args


def test_check_model_hf_not_found():
    from thunder_admin.checks import check_model
    from thunder_forge.cluster.config import Assignment, ClusterConfig, Model, ModelSource, Node

    node = Node(ip="10.0.0.1", ram_gb=64, user="admin")
    slot = Assignment(model="llama", port=8000)
    cluster = ClusterConfig(
        models={"llama": Model(source=ModelSource(type="huggingface", repo="mlx-community/Llama-3.2-3B"))},
        nodes={"msm1": node},
        assignments={"msm1": [slot]},
    )
    conn = _make_ssh_conn(b"", exit_code=2)  # ls returns 2 = no such file

    result = check_model(conn, node, slot, cluster)
    assert result[0] == "error"
    assert "not found" in result[1]


def test_check_model_non_hf_source_returns_warn():
    from thunder_admin.checks import check_model
    from thunder_forge.cluster.config import Assignment, ClusterConfig, Model, ModelSource, Node

    node = Node(ip="10.0.0.1", ram_gb=64, user="admin")
    slot = Assignment(model="local_m", port=8000)
    cluster = ClusterConfig(
        models={"local_m": Model(source=ModelSource(type="local", path="/models/llama"))},
        nodes={"msm1": node},
        assignments={"msm1": [slot]},
    )
    conn = MagicMock()

    for source_type in ("local", "pip", "convert"):
        cluster.models["local_m"].source.type = source_type
        result = check_model(conn, node, slot, cluster)
        assert result == ("warn", "non-HF source; skipping model check")
    conn.exec_command.assert_not_called()


def test_check_model_ssh_exception():
    from thunder_admin.checks import check_model
    from thunder_forge.cluster.config import Assignment, ClusterConfig, Model, ModelSource, Node

    node = Node(ip="10.0.0.1", ram_gb=64, user="admin")
    slot = Assignment(model="llama", port=8000)
    cluster = ClusterConfig(
        models={"llama": Model(source=ModelSource(type="huggingface", repo="mlx-community/Llama"))},
        nodes={"msm1": node},
        assignments={"msm1": [slot]},
    )
    conn = MagicMock()
    conn.exec_command.side_effect = Exception("channel closed")

    result = check_model(conn, node, slot, cluster)
    assert result[0] == "error"
    assert "channel closed" in result[1]
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
uv run pytest tests/test_admin_checks.py::test_check_model_hf_found tests/test_admin_checks.py::test_check_model_hf_not_found tests/test_admin_checks.py::test_check_model_non_hf_source_returns_warn tests/test_admin_checks.py::test_check_model_ssh_exception -v
```
Expected: FAIL — `check_model` not defined yet

- [ ] **Step 3: Add `check_model` to `checks.py`**

Append to `admin/thunder_admin/checks.py`:

```python
def check_model(ssh_conn: paramiko.SSHClient, node: Node, slot: Assignment, cluster: ClusterConfig) -> CheckResult:
    """Check HF model cache presence via SSH. Skips for non-HF source types."""
    model = cluster.models.get(slot.model)
    if model is None:
        return ("error", f"model '{slot.model}' not in config")

    if model.source.type != "huggingface":
        return ("warn", "non-HF source; skipping model check")

    slug = model.source.repo.replace("/", "--")
    path = f"~/.cache/huggingface/hub/models--{slug}"
    try:
        _, stdout, _ = ssh_conn.exec_command(f"ls {path}", timeout=_SSH_TIMEOUT)
        exit_code = stdout.channel.recv_exit_status()
        if exit_code == 0:
            return ("ok", "")
        return ("error", f"not found: {path}")
    except Exception as e:
        return ("error", str(e)[:120])
```

- [ ] **Step 4: Run tests to verify they pass**

```bash
uv run pytest tests/test_admin_checks.py -v
```
Expected: all tests PASS

- [ ] **Step 5: Commit**

```bash
git add admin/thunder_admin/checks.py tests/test_admin_checks.py
git commit -m "feat: add check_model with HF cache path detection"
```

---

## Task 4: `check_service` — launchctl (macOS) and systemctl (Linux)

**Files:**
- Modify: `admin/thunder_admin/checks.py`
- Modify: `tests/test_admin_checks.py`

Detect platform by running `uname -s`. macOS: `launchctl list com.mlx-lm-{port}` — grep for `"PID"` key (present = running). Linux: `systemctl is-active thunder-forge-{port}` — `active` = running.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_admin_checks.py`:

```python
# --- check_service ---

def _make_ssh_for_service(uname_output: bytes, service_stdout: bytes, service_exit: int) -> MagicMock:
    """Return a mock SSH client that returns uname output, then service output."""
    mock_client = MagicMock()
    call_count = [0]

    def exec_command(cmd, timeout=None):
        call_count[0] += 1
        mock_stdout = MagicMock()
        mock_channel = MagicMock()
        mock_stdout.channel = mock_channel
        if "uname" in cmd:
            mock_stdout.read.return_value = uname_output
            mock_channel.recv_exit_status.return_value = 0
        else:
            mock_stdout.read.return_value = service_stdout
            mock_channel.recv_exit_status.return_value = service_exit
        return MagicMock(), mock_stdout, MagicMock()

    mock_client.exec_command.side_effect = exec_command
    return mock_client


def test_check_service_macos_running():
    from thunder_admin.checks import check_service
    from thunder_forge.cluster.config import Assignment, Node

    node = Node(ip="10.0.0.1", ram_gb=64, user="admin")
    slot = Assignment(model="llama", port=8000)
    launchctl_output = b'{\n\t"PID" = 12345;\n\t"Label" = "com.mlx-lm-8000";\n}\n'
    conn = _make_ssh_for_service(b"Darwin\n", launchctl_output, 0)

    result = check_service(conn, node, slot)
    assert result == ("ok", "")
    # Verify launchctl was used
    calls = [str(c) for c in conn.exec_command.call_args_list]
    assert any("launchctl" in c for c in calls)
    assert any("com.mlx-lm-8000" in c for c in calls)


def test_check_service_macos_not_running():
    from thunder_admin.checks import check_service
    from thunder_forge.cluster.config import Assignment, Node

    node = Node(ip="10.0.0.1", ram_gb=64, user="admin")
    slot = Assignment(model="llama", port=8000)
    # launchctl output without PID = not running
    launchctl_output = b'{\n\t"Label" = "com.mlx-lm-8000";\n}\n'
    conn = _make_ssh_for_service(b"Darwin\n", launchctl_output, 0)

    result = check_service(conn, node, slot)
    assert result[0] == "error"
    assert "com.mlx-lm-8000" in result[1]


def test_check_service_macos_not_found():
    from thunder_admin.checks import check_service
    from thunder_forge.cluster.config import Assignment, Node

    node = Node(ip="10.0.0.1", ram_gb=64, user="admin")
    slot = Assignment(model="llama", port=8000)
    conn = _make_ssh_for_service(b"Darwin\n", b"Could not find service\n", 1)

    result = check_service(conn, node, slot)
    assert result[0] == "error"
    assert "com.mlx-lm-8000" in result[1]


def test_check_service_linux_active():
    from thunder_admin.checks import check_service
    from thunder_forge.cluster.config import Assignment, Node

    node = Node(ip="10.0.0.2", ram_gb=64, user="ubuntu")
    slot = Assignment(model="llama", port=9000)
    conn = _make_ssh_for_service(b"Linux\n", b"active\n", 0)

    result = check_service(conn, node, slot)
    assert result == ("ok", "")
    calls = [str(c) for c in conn.exec_command.call_args_list]
    assert any("systemctl" in c for c in calls)
    assert any("thunder-forge-9000" in c for c in calls)


def test_check_service_linux_inactive():
    from thunder_admin.checks import check_service
    from thunder_forge.cluster.config import Assignment, Node

    node = Node(ip="10.0.0.2", ram_gb=64, user="ubuntu")
    slot = Assignment(model="llama", port=9000)
    conn = _make_ssh_for_service(b"Linux\n", b"inactive\n", 3)

    result = check_service(conn, node, slot)
    assert result[0] == "error"
    assert "thunder-forge-9000" in result[1]
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
uv run pytest tests/test_admin_checks.py::test_check_service_macos_running tests/test_admin_checks.py::test_check_service_macos_not_running tests/test_admin_checks.py::test_check_service_macos_not_found tests/test_admin_checks.py::test_check_service_linux_active tests/test_admin_checks.py::test_check_service_linux_inactive -v
```
Expected: FAIL — `check_service` not defined yet

- [ ] **Step 3: Add `check_service` to `checks.py`**

Append to `admin/thunder_admin/checks.py`:

```python
def check_service(ssh_conn: paramiko.SSHClient, node: Node, slot: Assignment) -> CheckResult:
    """Check if the mlx_lm.server service is running on the node."""
    try:
        _, uname_out, _ = ssh_conn.exec_command("uname -s", timeout=_SSH_TIMEOUT)
        platform = uname_out.read().decode().strip()

        if platform == "Darwin":
            label = f"com.mlx-lm-{slot.port}"
            _, stdout, _ = ssh_conn.exec_command(f"launchctl list {label}", timeout=_SSH_TIMEOUT)
            output = stdout.read().decode()
            exit_code = stdout.channel.recv_exit_status()
            if exit_code != 0 or '"PID"' not in output:
                return ("error", f"{label} not found or not running")
            return ("ok", "")
        else:
            svc = f"thunder-forge-{slot.port}"
            _, stdout, _ = ssh_conn.exec_command(f"systemctl is-active {svc}", timeout=_SSH_TIMEOUT)
            output = stdout.read().decode().strip()
            exit_code = stdout.channel.recv_exit_status()
            if output == "active" and exit_code == 0:
                return ("ok", "")
            return ("error", f"{svc} is {output or 'not active'}")
    except Exception as e:
        return ("error", str(e)[:120])
```

- [ ] **Step 4: Run tests to verify they pass**

```bash
uv run pytest tests/test_admin_checks.py -v
```
Expected: all tests PASS

- [ ] **Step 5: Commit**

```bash
git add admin/thunder_admin/checks.py tests/test_admin_checks.py
git commit -m "feat: add check_service with platform detection (launchctl/systemctl)"
```

---

## Task 5: `check_port` — HTTP GET /v1/models

**Files:**
- Modify: `admin/thunder_admin/checks.py`
- Modify: `tests/test_admin_checks.py`

Uses `httpx.get` with a 3s timeout. Returns ok if HTTP 200 received, error on timeout or non-200.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_admin_checks.py`:

```python
# --- check_port ---

def test_check_port_ok():
    from thunder_admin.checks import check_port
    from thunder_forge.cluster.config import Assignment, Node

    node = Node(ip="10.0.0.1", ram_gb=64, user="admin")
    slot = Assignment(model="llama", port=8000)

    mock_response = MagicMock()
    mock_response.status_code = 200

    with patch("thunder_admin.checks.httpx.get", return_value=mock_response) as mock_get:
        result = check_port(node, slot)

    assert result == ("ok", "")
    mock_get.assert_called_once_with("http://10.0.0.1:8000/v1/models", timeout=3)


def test_check_port_non_200():
    from thunder_admin.checks import check_port
    from thunder_forge.cluster.config import Assignment, Node

    node = Node(ip="10.0.0.1", ram_gb=64, user="admin")
    slot = Assignment(model="llama", port=8000)
    mock_response = MagicMock()
    mock_response.status_code = 503

    with patch("thunder_admin.checks.httpx.get", return_value=mock_response):
        result = check_port(node, slot)

    assert result[0] == "error"
    assert "503" in result[1]


def test_check_port_timeout():
    import httpx

    from thunder_admin.checks import check_port
    from thunder_forge.cluster.config import Assignment, Node

    node = Node(ip="10.0.0.1", ram_gb=64, user="admin")
    slot = Assignment(model="llama", port=8000)

    with patch("thunder_admin.checks.httpx.get", side_effect=httpx.TimeoutException("timed out")):
        result = check_port(node, slot)

    assert result == ("error", "timeout")


def test_check_port_connection_error():
    import httpx

    from thunder_admin.checks import check_port
    from thunder_forge.cluster.config import Assignment, Node

    node = Node(ip="10.0.0.1", ram_gb=64, user="admin")
    slot = Assignment(model="llama", port=8000)

    with patch("thunder_admin.checks.httpx.get", side_effect=httpx.ConnectError("refused")):
        result = check_port(node, slot)

    assert result[0] == "error"
    assert "refused" in result[1]
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
uv run pytest tests/test_admin_checks.py::test_check_port_ok tests/test_admin_checks.py::test_check_port_non_200 tests/test_admin_checks.py::test_check_port_timeout tests/test_admin_checks.py::test_check_port_connection_error -v
```
Expected: FAIL — `check_port` not defined / `httpx` not imported

- [ ] **Step 3: Add `httpx` import and `check_port` to `checks.py`**

At top of `admin/thunder_admin/checks.py`, add `import httpx` after `import paramiko`.

Then append:

```python
def check_port(node: Node, slot: Assignment) -> CheckResult:
    """HTTP GET /v1/models with 3s timeout."""
    url = f"http://{node.ip}:{slot.port}/v1/models"
    try:
        response = httpx.get(url, timeout=3)
        if response.status_code == 200:
            return ("ok", "")
        return ("error", f"HTTP {response.status_code}")
    except httpx.TimeoutException:
        return ("error", "timeout")
    except Exception as e:
        return ("error", str(e)[:120])
```

- [ ] **Step 4: Run tests to verify they pass**

```bash
uv run pytest tests/test_admin_checks.py -v
```
Expected: all tests PASS

- [ ] **Step 5: Commit**

```bash
git add admin/thunder_admin/checks.py tests/test_admin_checks.py
git commit -m "feat: add check_port with httpx 3s timeout"
```

---

## Task 6: `run_all_checks` — parallel execution with skip logic

**Files:**
- Modify: `admin/thunder_admin/checks.py`
- Modify: `tests/test_admin_checks.py`

`run_all_checks(config)` iterates over `config["assignments"]`, runs each slot in a `ThreadPoolExecutor`. Skip logic:
- `model`, `service`, `port` are skipped (grey `–`) if `ssh` fails
- `port` is also skipped if `service` result is not `"ok"`
- `config` errors are non-blocking (remaining checks still run)

Node user resolution: use `node.user` from parsed config, fallback to `TF_SSH_USER` env var; if still empty return `("error", "node user not configured")` for ssh and skip all downstream checks.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_admin_checks.py`:

```python
# --- run_all_checks ---

def _full_config() -> dict:
    return {
        "models": {
            "llama": {
                "source": {"type": "huggingface", "repo": "mlx-community/Llama-3.2-3B"},
                "disk_gb": 2.0,
            }
        },
        "nodes": {"msm1": {"ip": "10.0.0.1", "ram_gb": 64, "role": "node", "user": "admin"}},
        "assignments": {"msm1": [{"model": "llama", "port": 8000, "embedding": False}]},
        "external_endpoints": [],
    }


def test_run_all_checks_happy_path():
    from thunder_admin.checks import run_all_checks

    with patch("thunder_admin.checks.check_config", return_value=("ok", "")):
        with patch("thunder_admin.checks.check_ssh", return_value=(("ok", ""), MagicMock())):
            with patch("thunder_admin.checks.check_model", return_value=("ok", "")):
                with patch("thunder_admin.checks.check_service", return_value=("ok", "")):
                    with patch("thunder_admin.checks.check_port", return_value=("ok", "")):
                        results = run_all_checks(_full_config())

    assert ("msm1", 8000) in results
    slot = results[("msm1", 8000)]
    assert slot["config"] == ("ok", "")
    assert slot["ssh"] == ("ok", "")
    assert slot["model"] == ("ok", "")
    assert slot["service"] == ("ok", "")
    assert slot["port"] == ("ok", "")


def test_run_all_checks_ssh_fail_skips_downstream():
    from thunder_admin.checks import run_all_checks

    with patch("thunder_admin.checks.check_config", return_value=("ok", "")):
        with patch("thunder_admin.checks.check_ssh", return_value=(("error", "SSH timeout"), None)):
            with patch("thunder_admin.checks.check_model") as mock_model:
                with patch("thunder_admin.checks.check_service") as mock_service:
                    with patch("thunder_admin.checks.check_port") as mock_port:
                        results = run_all_checks(_full_config())

    slot = results[("msm1", 8000)]
    assert slot["ssh"] == ("error", "SSH timeout")
    assert slot["model"] == ("skip", "")
    assert slot["service"] == ("skip", "")
    assert slot["port"] == ("skip", "")
    mock_model.assert_not_called()
    mock_service.assert_not_called()
    mock_port.assert_not_called()


def test_run_all_checks_service_not_ok_skips_port():
    from thunder_admin.checks import run_all_checks

    mock_ssh_conn = MagicMock()
    with patch("thunder_admin.checks.check_config", return_value=("ok", "")):
        with patch("thunder_admin.checks.check_ssh", return_value=(("ok", ""), mock_ssh_conn)):
            with patch("thunder_admin.checks.check_model", return_value=("ok", "")):
                with patch("thunder_admin.checks.check_service", return_value=("error", "not running")):
                    with patch("thunder_admin.checks.check_port") as mock_port:
                        results = run_all_checks(_full_config())

    slot = results[("msm1", 8000)]
    assert slot["service"] == ("error", "not running")
    assert slot["port"] == ("skip", "")
    mock_port.assert_not_called()


def test_run_all_checks_config_error_does_not_block_ssh():
    from thunder_admin.checks import run_all_checks

    mock_ssh_conn = MagicMock()
    with patch("thunder_admin.checks.check_config", return_value=("error", "RAM too low")):
        with patch("thunder_admin.checks.check_ssh", return_value=(("ok", ""), mock_ssh_conn)) as mock_ssh:
            with patch("thunder_admin.checks.check_model", return_value=("ok", "")):
                with patch("thunder_admin.checks.check_service", return_value=("ok", "")):
                    with patch("thunder_admin.checks.check_port", return_value=("ok", "")):
                        results = run_all_checks(_full_config())

    slot = results[("msm1", 8000)]
    assert slot["config"] == ("error", "RAM too low")
    assert slot["ssh"] == ("ok", "")  # SSH still ran
    mock_ssh.assert_called_once()


def test_run_all_checks_no_user_returns_error():
    from thunder_admin.checks import run_all_checks

    config = _full_config()
    config["nodes"]["msm1"]["user"] = ""  # no user in config

    with patch.dict("os.environ", {}, clear=True):  # no TF_SSH_USER
        with patch("thunder_admin.checks.check_config", return_value=("ok", "")):
            results = run_all_checks(config)

    slot = results[("msm1", 8000)]
    assert slot["ssh"][0] == "error"
    assert "user not configured" in slot["ssh"][1]
    assert slot["model"] == ("skip", "")
    assert slot["service"] == ("skip", "")
    assert slot["port"] == ("skip", "")


def test_run_all_checks_user_fallback_to_env():
    from thunder_admin.checks import run_all_checks

    config = _full_config()
    config["nodes"]["msm1"]["user"] = ""  # no user in config

    mock_ssh_conn = MagicMock()
    with patch.dict("os.environ", {"TF_SSH_USER": "fallback_user"}):
        with patch("thunder_admin.checks.check_config", return_value=("ok", "")):
            with patch("thunder_admin.checks.check_ssh", return_value=(("ok", ""), mock_ssh_conn)) as mock_ssh:
                with patch("thunder_admin.checks.check_model", return_value=("ok", "")):
                    with patch("thunder_admin.checks.check_service", return_value=("ok", "")):
                        with patch("thunder_admin.checks.check_port", return_value=("ok", "")):
                            results = run_all_checks(config)

    # check_ssh was called with a node that has user="fallback_user"
    called_node = mock_ssh.call_args[0][0]
    assert called_node.user == "fallback_user"
    assert results[("msm1", 8000)]["ssh"] == ("ok", "")


def test_run_all_checks_empty_assignments_returns_empty():
    from thunder_admin.checks import run_all_checks

    config = _full_config()
    config["assignments"] = {}
    results = run_all_checks(config)
    assert results == {}
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
uv run pytest tests/test_admin_checks.py::test_run_all_checks_happy_path tests/test_admin_checks.py::test_run_all_checks_ssh_fail_skips_downstream tests/test_admin_checks.py::test_run_all_checks_service_not_ok_skips_port tests/test_admin_checks.py::test_run_all_checks_config_error_does_not_block_ssh tests/test_admin_checks.py::test_run_all_checks_no_user_returns_error tests/test_admin_checks.py::test_run_all_checks_user_fallback_to_env tests/test_admin_checks.py::test_run_all_checks_empty_assignments_returns_empty -v
```
Expected: FAIL — `run_all_checks` not defined yet

- [ ] **Step 3: Add `run_all_checks` to `checks.py`**

Append to `admin/thunder_admin/checks.py`:

```python
def run_all_checks(config: dict) -> dict[tuple[str, int], SlotChecks]:
    """Run all checks for all assignment slots in parallel. Returns (node_name, port) → SlotChecks.

    Internally parses raw config dict via parse_cluster_config() to get Node/Assignment dataclasses.
    Config check runs once globally. SSH checks run per-slot in a ThreadPoolExecutor.
    """
    assignments_raw = config.get("assignments", {})
    if not assignments_raw:
        return {}

    cluster = parse_cluster_config(config)
    config_result = check_config(config)

    def check_slot(node_name: str, slot: Assignment) -> tuple[tuple[str, int], SlotChecks]:
        node = cluster.nodes[node_name]
        user = node.user or os.environ.get("TF_SSH_USER", "")
        if not user:
            return (node_name, slot.port), {
                "config": config_result,
                "ssh": ("error", "node user not configured"),
                "model": ("skip", ""),
                "service": ("skip", ""),
                "port": ("skip", ""),
            }

        # Clone node with resolved user
        resolved_node = Node(ip=node.ip, ram_gb=node.ram_gb, user=user, role=node.role)

        ssh_result, ssh_conn = check_ssh(resolved_node)
        if ssh_result[0] != "ok" or ssh_conn is None:
            return (node_name, slot.port), {
                "config": config_result,
                "ssh": ssh_result,
                "model": ("skip", ""),
                "service": ("skip", ""),
                "port": ("skip", ""),
            }

        try:
            model_result = check_model(ssh_conn, resolved_node, slot, cluster)
            service_result = check_service(ssh_conn, resolved_node, slot)
        finally:
            ssh_conn.close()

        port_result: CheckResult = ("skip", "") if service_result[0] != "ok" else check_port(resolved_node, slot)

        return (node_name, slot.port), {
            "config": config_result,
            "ssh": ssh_result,
            "model": model_result,
            "service": service_result,
            "port": port_result,
        }

    # Build flat list of (node_name, Assignment) pairs
    slots: list[tuple[str, Assignment]] = []
    for node_name, node_slots in cluster.assignments.items():
        for slot in node_slots:
            slots.append((node_name, slot))

    results: dict[tuple[str, int], SlotChecks] = {}
    with ThreadPoolExecutor() as executor:
        futures = {executor.submit(check_slot, node_name, slot): (node_name, slot) for node_name, slot in slots}
        for future in as_completed(futures):
            key, slot_checks = future.result()
            results[key] = slot_checks

    return results
```

- [ ] **Step 4: Run all tests to verify they pass**

```bash
uv run pytest tests/test_admin_checks.py -v
```
Expected: all tests PASS

- [ ] **Step 5: Lint**

```bash
uv run ruff check admin/thunder_admin/checks.py tests/test_admin_checks.py
```
Fix any issues, then:

- [ ] **Step 6: Commit**

```bash
git add admin/thunder_admin/checks.py tests/test_admin_checks.py
git commit -m "feat: add run_all_checks with parallel execution and skip logic"
```

---

## Task 7: Deploy page integration

**Files:**
- Modify: `admin/thunder_admin/pages/deploy.py`

Add "Check Status" button between "Changes to Deploy" and the Deploy button. On click: run `run_all_checks`, store in `st.session_state`. Invalidate when `current["id"]` changes. Render one row per slot. Show `st.info` if no assignments.

Status icon mapping: `"ok"` → `":green[✓]"`, `"error"` → `":red[✗]"`, `"warn"` → `":orange[⚠]"`, `"skip"` → `":grey[–]"`.

Row label format: `"{node_name} / {slot_model}:{port}"`.

- [ ] **Step 1: Read current `deploy.py` to locate insertion point**

The section between "Changes to Deploy" diff block and "Deploy button or running status" (line 56) is where the checklist goes. The "Check Status" button and results render between these two sections.

- [ ] **Step 2: Add imports to `deploy.py`**

```python
# Add after existing imports at the top of admin/thunder_admin/pages/deploy.py
from thunder_admin.checks import run_all_checks
```

- [ ] **Step 3: Add the checklist section to `deploy.py`**

Insert after line 54 (`st.code(current_yaml, language="yaml")` — the `else` branch of `last_deploy`) and before line 57 (`# Deploy button or running status`):

```python
    # Check Status section
    assignments = current["config"].get("assignments", {})
    if not assignments:
        st.info("No assignments to check")
    else:
        # Invalidate cached checks when config version changes
        if st.session_state.get("deploy_checks_config_id") != current["id"]:
            st.session_state.pop("deploy_checks", None)
            st.session_state.pop("deploy_checks_config_id", None)

        if st.button("Check Status"):
            with st.spinner("Running checks..."):
                st.session_state["deploy_checks"] = run_all_checks(current["config"])
                st.session_state["deploy_checks_config_id"] = current["id"]

        check_results: dict = st.session_state.get("deploy_checks", {})
        if check_results:
            _render_check_results(check_results, current["config"])
```

- [ ] **Step 4: Add `_render_check_results` function to `deploy.py`**

Add this function before `render()`:

```python
_STATUS_ICONS = {
    "ok": ":green[✓]",
    "error": ":red[✗]",
    "warn": ":orange[⚠]",
    "skip": ":grey[–]",
}
_CHECK_LABELS = ["config", "ssh", "model", "service", "port"]


def _render_check_results(results: dict, config: dict) -> None:
    """Render one compact status row per assignment slot."""
    assignments = config.get("assignments", {})
    for node_name, slots in assignments.items():
        for slot_dict in slots:
            port = slot_dict["port"]
            model = slot_dict.get("model", "?")
            key = (node_name, port)
            slot_checks = results.get(key)
            if slot_checks is None:
                continue

            cols = st.columns([3, 1, 1, 1, 1, 1])
            cols[0].markdown(f"**{node_name} / {model}:{port}**")
            for i, check_name in enumerate(_CHECK_LABELS):
                status, _ = slot_checks.get(check_name, ("skip", ""))
                cols[i + 1].markdown(f"{_STATUS_ICONS.get(status, '?')} {check_name}")

            # Error/warn detail lines
            for check_name in _CHECK_LABELS:
                status, detail = slot_checks.get(check_name, ("skip", ""))
                if detail and status in ("error", "warn"):
                    st.caption(f"{check_name}: {detail}")
```

- [ ] **Step 5: Run the full test suite**

```bash
uv run pytest tests/ -v
```
Expected: all tests PASS (no regressions)

- [ ] **Step 6: Lint**

```bash
uv run ruff check admin/thunder_admin/pages/deploy.py admin/thunder_admin/checks.py
```
Fix any issues.

- [ ] **Step 7: Commit**

```bash
git add admin/thunder_admin/pages/deploy.py
git commit -m "feat: add Check Status button and slot results to Deploy page"
```

---

## Spec Coverage Check

| Spec requirement | Task |
|---|---|
| "Check Status" button on Deploy page, between diff and Deploy button | Task 7 |
| One row per slot, 5 status columns | Task 7 (`_render_check_results`) |
| Status icons ✓/✗/⚠/– with colour | Task 7 (`_STATUS_ICONS`) |
| Error caption/detail below row | Task 7 (detail lines loop) |
| Invalidate when config version changes | Task 7 (session state check) |
| `st.info("No assignments to check")` when empty | Task 7 |
| config check: delegates to `validate_config()`, global errors, 120-char cap | Task 1 |
| ssh check: paramiko echo ok, 10s timeout | Task 2 |
| model check: HF ls path, non-HF warn | Task 3 |
| service check: launchctl PID grep (macOS), systemctl (Linux) | Task 4 |
| port check: GET /v1/models, 3s timeout | Task 5 |
| Parallel via ThreadPoolExecutor | Task 6 |
| SSH connection reuse (model + service reuse check_ssh conn) | Tasks 3, 4, 6 |
| model/service/port skip if ssh fails | Task 6 |
| port skip if service not running | Task 6 |
| config errors non-blocking | Task 6 |
| node user fallback to `TF_SSH_USER` | Task 6 |
| node user missing → error, skip downstream | Task 6 |
| Unexpected exceptions → `("error", str(e)[:120])` | Tasks 2–5 |
| SSH timeout → `("error", "SSH timeout")` | Task 2 |
| HTTP timeout → `("error", "timeout")` | Task 5 |
| No auto-refresh / no polling | Task 7 (button only, no `time.sleep`) |
| No blocking Deploy button | Task 7 (check button and deploy button are independent) |
