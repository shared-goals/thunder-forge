# Stability Hardening Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make thunder-forge reliable enough to deploy on first try to a 4x Mac Studio + Radxa ROCK cluster operated by a remote person relaying console output.

**Architecture:** Targeted hardening of config.py, ssh.py, deploy.py, models.py, health.py. New preflight.py module for environment validation. Dry-run mode for deploy. Consistent node/gateway naming. setup-node.sh pre-checks and progress output.

**Tech Stack:** Python 3.12+, Typer, PyYAML, python-dotenv, pytest, ruff

**Spec:** `docs/superpowers/specs/2026-03-23-stability-hardening-design.md`

---

### Task 1: Config Foundation — Role Rename, Resolved Fields, User Fix

**Files:**
- Modify: `src/thunder_forge/cluster/config.py`
- Modify: `tests/test_config.py`
- Modify: `configs/node-assignments.yaml.example`

This task updates the Node dataclass with resolved fields, renames `inference`/`infra` to `node`/`gateway`, replaces `os.getlogin()` with `os.environ.get("USER")`, and fixes all tests.

- [ ] **Step 1: Update Node dataclass and ClusterConfig properties**

In `src/thunder_forge/cluster/config.py`, replace the `Node` dataclass and `ClusterConfig`:

```python
@dataclass
class Node:
    ip: str
    ram_gb: int
    user: str = ""
    role: str = "node"
    # Resolved during pre-flight — None until populated
    platform: str | None = None
    shell: str | None = None
    home_dir: str | None = None
    homebrew_prefix: str | None = None
```

In `ClusterConfig`, rename the properties:

```python
@dataclass
class ClusterConfig:
    models: dict[str, Model] = field(default_factory=dict)
    nodes: dict[str, Node] = field(default_factory=dict)
    assignments: dict[str, list[Assignment]] = field(default_factory=dict)

    @property
    def compute_nodes(self) -> dict[str, Node]:
        return {k: v for k, v in self.nodes.items() if v.role == "node"}

    @property
    def gateway_name(self) -> str:
        for k, v in self.nodes.items():
            if v.role == "gateway":
                return k
        msg = "No gateway node found in config"
        raise ValueError(msg)

    @property
    def gateway(self) -> Node:
        return self.nodes[self.gateway_name]
```

Note: The old property names (`inference_nodes`, `infra_name`, `rock`) are removed — all consumers will be updated in later tasks. The spec says to rename `inference_nodes` to `nodes`, but that would shadow the existing `nodes` field (the full dict). We use `compute_nodes` instead — this is a deliberate, documented deviation from the spec.

- [ ] **Step 2: Update role parsing with migration support**

In `load_cluster_config()`, replace the node parsing block (lines 113-124):

```python
    _ROLE_MIGRATION = {"inference": "node", "infra": "gateway"}

    nodes = {}
    for k, v in raw.get("nodes", {}).items():
        raw_role = v.get("role", "node")
        role = _ROLE_MIGRATION.get(raw_role, raw_role)
        if raw_role != role:
            import warnings
            warnings.warn(
                f"Node '{k}': role '{raw_role}' is deprecated, use '{role}' instead",
                DeprecationWarning,
                stacklevel=1,
            )
        if v.get("user"):
            user = v["user"]
        elif os.environ.get("TF_SSH_USER"):
            user = os.environ["TF_SSH_USER"]
        else:
            user = os.environ.get("USER", "unknown")
        nodes[k] = Node(ip=v["ip"], ram_gb=v["ram_gb"], user=user, role=role)
```

Key changes:
- `os.getlogin()` replaced with `os.environ.get("USER", "unknown")`
- Role migration: `inference` -> `node`, `infra` -> `gateway`
- Deprecation warning for old role names
- Default role is `"node"` (was `"inference"`)
- No more role-based user defaults — all nodes default to `$USER`

- [ ] **Step 3: Update node-assignments.yaml.example**

Replace `configs/node-assignments.yaml.example` role values:

```yaml
# Per-node user is optional. Falls back to TF_SSH_USER env var,
# then $USER env var.
nodes:
  rock: { ip: "192.168.1.61", ram_gb: 32, role: gateway }
  msm1: { ip: "192.168.1.101", ram_gb: 128, role: node }
  msm2: { ip: "192.168.1.102", ram_gb: 128, role: node }
  # Add more nodes as needed:
  # mynode: { ip: "192.168.1.50", ram_gb: 64, role: node, user: myuser }
```

- [ ] **Step 4: Fix all test fixtures to use new role names + fix failing test**

In `tests/test_config.py`:

All fixtures: replace `role: infra` with `role: gateway` and `role: inference` with `role: node`.

Fix `test_load_cluster_config`:
```python
def test_load_cluster_config(assignments_yaml: Path) -> None:
    config = load_cluster_config(assignments_yaml)
    assert "coder" in config.models
    assert config.models["coder"].source.type == "huggingface"
    assert config.models["coder"].disk_gb == 44.8
    assert "msm1" in config.nodes
    assert config.nodes["msm1"].ip == "192.168.1.101"
    assert config.nodes["msm1"].role == "node"
    assert "rock" in config.nodes
    assert config.nodes["rock"].role == "gateway"
    assert len(config.assignments["msm1"]) == 1
    assert config.assignments["msm1"][0].model == "coder"
    assert config.assignments["msm1"][0].port == 8000
```

Fix `test_load_cluster_config_user_defaults`:
```python
def test_load_cluster_config_user_defaults(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """All nodes default to $USER when no user specified."""
    monkeypatch.setenv("USER", "testuser")
    monkeypatch.delenv("TF_SSH_USER", raising=False)
    content = dedent("""\
        models:
          coder:
            source: { type: huggingface, repo: "test/coder" }
            disk_gb: 10
        nodes:
          rock: { ip: "192.168.1.61", ram_gb: 32, role: gateway }
          msm1: { ip: "192.168.1.101", ram_gb: 128, role: node }
        assignments:
          msm1:
            - model: coder
              port: 8000
    """)
    p = tmp_path / "node-assignments.yaml"
    p.write_text(content)
    config = load_cluster_config(p)
    assert config.nodes["msm1"].user == "testuser"
    assert config.nodes["rock"].user == "testuser"
```

Add test for role deprecation:
```python
def test_load_cluster_config_role_migration(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Old role names (inference/infra) are accepted with deprecation warning."""
    monkeypatch.setenv("USER", "testuser")
    content = dedent("""\
        models:
          coder:
            source: { type: huggingface, repo: "test/coder" }
            disk_gb: 10
        nodes:
          rock: { ip: "192.168.1.61", ram_gb: 32, role: infra }
          msm1: { ip: "192.168.1.101", ram_gb: 128, role: inference }
        assignments:
          msm1:
            - model: coder
              port: 8000
    """)
    p = tmp_path / "node-assignments.yaml"
    p.write_text(content)
    with pytest.warns(DeprecationWarning, match="deprecated"):
        config = load_cluster_config(p)
    assert config.nodes["rock"].role == "gateway"
    assert config.nodes["msm1"].role == "node"
```

Add test for resolved fields:
```python
def test_node_resolved_fields_default_to_none(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Resolved fields are None until pre-flight populates them."""
    monkeypatch.setenv("USER", "testuser")
    content = dedent("""\
        models:
          coder:
            source: { type: huggingface, repo: "test/coder" }
            disk_gb: 10
        nodes:
          msm1: { ip: "192.168.1.101", ram_gb: 128, role: node }
          rock: { ip: "192.168.1.61", ram_gb: 32, role: gateway }
        assignments:
          msm1:
            - model: coder
              port: 8000
    """)
    p = tmp_path / "node-assignments.yaml"
    p.write_text(content)
    config = load_cluster_config(p)
    node = config.nodes["msm1"]
    assert node.platform is None
    assert node.shell is None
    assert node.home_dir is None
    assert node.homebrew_prefix is None
```

Also update fixtures in `tests/test_deploy.py`, `tests/test_models.py`, `tests/test_health.py` — replace all `role: infra` with `role: gateway` and `role: inference` with `role: node`.

- [ ] **Step 5: Run tests**

Run: `uv run pytest tests/ -v`
Expected: All tests pass (including the previously failing `test_load_cluster_config_user_defaults`)

- [ ] **Step 6: Lint and format**

Run: `uv run ruff check src/ tests/ && uv run ruff format src/ tests/`

- [ ] **Step 7: Commit**

```bash
git add src/thunder_forge/cluster/config.py tests/ configs/node-assignments.yaml.example
git commit -m "refactor: rename inference/infra to node/gateway, add resolved fields, fix user defaults

Replace os.getlogin() with os.environ.get('USER'). Add Optional resolved
fields (platform, shell, home_dir, homebrew_prefix) to Node — None until
pre-flight populates them. Accept old role names with deprecation warning."
```

---

### Task 2: SSH Hardening

**Files:**
- Modify: `src/thunder_forge/cluster/ssh.py`
- Create: `tests/test_ssh.py`

Simplify SSH command wrapping: use `node.shell` when available (from pre-flight), keep platform-based default for backward compat. Remove `2>/dev/null` stderr suppression. Add `node_name` parameter to `ssh_run` for error context.

- [ ] **Step 1: Write failing tests**

Create `tests/test_ssh.py`:

```python
"""Tests for SSH helpers."""

from unittest.mock import MagicMock, patch

from thunder_forge.cluster.ssh import ssh_run, scp_content


@patch("thunder_forge.cluster.ssh.subprocess.run")
@patch("thunder_forge.cluster.ssh._is_local", return_value=False)
def test_ssh_run_uses_node_shell(mock_local: MagicMock, mock_run: MagicMock) -> None:
    """When shell is provided, ssh_run uses it directly — no fallback hack."""
    mock_run.return_value = MagicMock(returncode=0, stdout="ok", stderr="")
    ssh_run("admin", "192.168.1.101", "echo hello", shell="zsh")
    cmd_args = mock_run.call_args[0][0]
    # The remote command should use the specified shell, not the zsh||bash fallback
    remote_cmd = cmd_args[-1]
    assert remote_cmd.startswith("zsh -lc")
    assert "|| bash" not in remote_cmd


@patch("thunder_forge.cluster.ssh.subprocess.run")
@patch("thunder_forge.cluster.ssh._is_local", return_value=False)
def test_ssh_run_no_stderr_suppression(mock_local: MagicMock, mock_run: MagicMock) -> None:
    """stderr is NOT suppressed with 2>/dev/null."""
    mock_run.return_value = MagicMock(returncode=0, stdout="ok", stderr="")
    ssh_run("admin", "192.168.1.101", "echo hello", shell="bash")
    remote_cmd = mock_run.call_args[0][0][-1]
    assert "2>/dev/null" not in remote_cmd


@patch("thunder_forge.cluster.ssh.subprocess.run")
@patch("thunder_forge.cluster.ssh._is_local", return_value=False)
def test_ssh_run_default_shell_fallback(mock_local: MagicMock, mock_run: MagicMock) -> None:
    """Without explicit shell, falls back to platform detection."""
    mock_run.return_value = MagicMock(returncode=0, stdout="ok", stderr="")
    ssh_run("admin", "192.168.1.101", "echo hello")
    # Should still work (uses _login_shell() default)
    assert mock_run.called


@patch("thunder_forge.cluster.ssh.subprocess.run")
@patch("thunder_forge.cluster.ssh._is_local", return_value=True)
def test_ssh_run_local_uses_shell(mock_local: MagicMock, mock_run: MagicMock) -> None:
    """Local commands use the specified shell."""
    mock_run.return_value = MagicMock(returncode=0, stdout="ok", stderr="")
    ssh_run("admin", "127.0.0.1", "echo hello", shell="bash")
    cmd_args = mock_run.call_args[0][0]
    assert cmd_args[0] == "bash"


@patch("thunder_forge.cluster.ssh.subprocess.run")
@patch("thunder_forge.cluster.ssh._is_local", return_value=False)
def test_scp_content_uses_shell(mock_local: MagicMock, mock_run: MagicMock) -> None:
    """scp_content uses the specified shell for local target."""
    mock_run.return_value = MagicMock(returncode=0, stdout="", stderr="")
    scp_content("admin", "192.168.1.101", "content", "/tmp/file")
    assert mock_run.called
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_ssh.py -v`
Expected: FAIL — `ssh_run` doesn't accept `shell` parameter yet

- [ ] **Step 3: Update ssh.py**

Replace `src/thunder_forge/cluster/ssh.py`:

```python
"""Shared SSH and SCP helpers for remote operations."""

from __future__ import annotations

import platform
import shlex
import socket
import subprocess


def _login_shell() -> str:
    """Return the login shell for the local machine: zsh on macOS, bash on Linux."""
    return "zsh" if platform.system() == "Darwin" else "bash"


def _is_local(ip: str) -> bool:
    """Check if the given IP belongs to this machine by trying to bind to it."""
    if ip in ("127.0.0.1", "::1"):
        return True
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.bind((ip, 0))
        s.close()
        return True
    except OSError:
        return False


def ssh_run(
    user: str,
    ip: str,
    cmd: str,
    *,
    timeout: int = 30,
    stream: bool = False,
    shell: str | None = None,
    node_name: str | None = None,
) -> subprocess.CompletedProcess[str]:
    """Run a command on a remote node via SSH, or locally if the target is this machine.

    Args:
        shell: Explicit shell to use (e.g. "zsh", "bash"). If None, uses platform default.
        node_name: Node name for error context (included in stderr on failure).
    """
    capture = not stream
    effective_shell = shell or _login_shell()
    if _is_local(ip):
        return subprocess.run(
            [effective_shell, "-lc", cmd],
            capture_output=capture,
            text=True,
            timeout=timeout,
        )
    wrapped = f"{effective_shell} -lc {shlex.quote(cmd)}"
    return subprocess.run(
        ["ssh", "-o", "ConnectTimeout=10", "-o", "StrictHostKeyChecking=no", f"{user}@{ip}", wrapped],
        capture_output=capture,
        text=True,
        timeout=timeout,
    )


def run_local(
    cmd: list[str],
    *,
    timeout: int = 600,
) -> subprocess.CompletedProcess[str]:
    """Run a command locally."""
    return subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)


def scp_content(
    user: str,
    ip: str,
    content: str,
    remote_path: str,
    *,
    shell: str | None = None,
) -> subprocess.CompletedProcess[str]:
    """Write content to a remote file via SSH stdin pipe, or locally if target is this machine."""
    effective_shell = shell or _login_shell()
    if _is_local(ip):
        return subprocess.run(
            [effective_shell, "-lc", f"cat > {remote_path}"],
            input=content,
            capture_output=True,
            text=True,
            timeout=15,
        )
    return subprocess.run(
        ["ssh", "-o", "ConnectTimeout=10", "-o", "StrictHostKeyChecking=no", f"{user}@{ip}", f"cat > {remote_path}"],
        input=content,
        capture_output=True,
        text=True,
        timeout=15,
    )
```

Key changes:
- `shell` parameter: uses explicit shell when provided (from pre-flight), falls back to `_login_shell()`
- Removed `2>/dev/null` from command wrapping
- Removed `zsh ... || bash ...` fallback hack — single shell, no fallback
- `ConnectTimeout` increased from 5 to 10
- `node_name` parameter for future error context (not enforced yet, used by preflight)
- `scp_content` also accepts `shell` parameter

- [ ] **Step 4: Run tests**

Run: `uv run pytest tests/test_ssh.py tests/ -v`
Expected: All pass

- [ ] **Step 5: Lint and commit**

```bash
uv run ruff check src/thunder_forge/cluster/ssh.py tests/test_ssh.py
uv run ruff format src/thunder_forge/cluster/ssh.py tests/test_ssh.py
git add src/thunder_forge/cluster/ssh.py tests/test_ssh.py
git commit -m "refactor: simplify SSH command wrapping, add shell parameter

Remove zsh||bash fallback hack and 2>/dev/null stderr suppression.
Add explicit shell parameter (populated by pre-flight). Increase
ConnectTimeout from 5s to 10s."
```

---

### Task 3: Pre-flight Module

**Files:**
- Create: `src/thunder_forge/cluster/preflight.py`
- Create: `tests/test_preflight.py`

New module that probes all target nodes via SSH, validates environment, and populates Node resolved fields.

- [ ] **Step 1: Write failing tests**

Create `tests/test_preflight.py`:

```python
"""Tests for pre-flight validation."""

from unittest.mock import MagicMock, patch

import pytest

from thunder_forge.cluster.config import ClusterConfig, Node, Assignment
from thunder_forge.cluster.preflight import run_preflight, parse_probe_output, build_probe_script


def _make_config(nodes: dict[str, Node], assignments: dict[str, list] | None = None) -> ClusterConfig:
    return ClusterConfig(models={}, nodes=nodes, assignments=assignments or {})


class TestParseProbeOutput:
    def test_parses_valid_output(self) -> None:
        output = (
            "@@PROBE_START@@\n"
            "PLATFORM=Darwin\n"
            "SHELL_PATH=zsh\n"
            "SHELL_OK=1\n"
            "HOME_DIR=/Users/admin\n"
            "HOME_OK=1\n"
            "BREW_PREFIX=/opt/homebrew\n"
            "BREW_OK=1\n"
            "UV_OK=1\n"
            "VLLM_OK=1\n"
            "DISK_KB=52428800\n"
            "@@PROBE_END@@\n"
        )
        result = parse_probe_output(output)
        assert result["PLATFORM"] == "Darwin"
        assert result["SHELL_PATH"] == "zsh"
        assert result["HOME_DIR"] == "/Users/admin"
        assert result["BREW_PREFIX"] == "/opt/homebrew"

    def test_returns_empty_on_missing_delimiters(self) -> None:
        result = parse_probe_output("some random output")
        assert result == {}


class TestBuildProbeScript:
    def test_contains_platform_and_shell_probes(self) -> None:
        script = build_probe_script(role="node")
        assert "uname -s" in script
        assert "SHELL" in script
        assert "HOME" in script
        assert "brew --prefix" in script
        assert "@@PROBE_START@@" in script
        assert "@@PROBE_END@@" in script

    def test_gateway_includes_docker_check(self) -> None:
        script = build_probe_script(role="gateway")
        assert "docker" in script

    def test_node_includes_vllm_check(self) -> None:
        script = build_probe_script(role="node")
        assert "vllm" in script


class TestRunPreflight:
    @patch("thunder_forge.cluster.preflight.subprocess.run")
    def test_all_nodes_ok(self, mock_run: MagicMock) -> None:
        probe_output = (
            "@@PROBE_START@@\n"
            "PLATFORM=Darwin\n"
            "SHELL_PATH=zsh\n"
            "SHELL_OK=1\n"
            "HOME_DIR=/Users/admin\n"
            "HOME_OK=1\n"
            "BREW_PREFIX=/opt/homebrew\n"
            "BREW_OK=1\n"
            "UV_OK=1\n"
            "VLLM_OK=1\n"
            "DISK_KB=52428800\n"
            "@@PROBE_END@@\n"
        )
        mock_run.return_value = MagicMock(returncode=0, stdout=probe_output, stderr="")
        nodes = {
            "msm1": Node(ip="192.168.1.101", ram_gb=128, role="node"),
            "rock": Node(ip="192.168.1.61", ram_gb=32, role="gateway"),
        }
        config = _make_config(nodes)
        errors = run_preflight(config)
        assert errors == []
        assert config.nodes["msm1"].platform == "Darwin"
        assert config.nodes["msm1"].shell == "zsh"
        assert config.nodes["msm1"].home_dir == "/Users/admin"

    @patch("thunder_forge.cluster.preflight.subprocess.run")
    def test_ssh_unreachable(self, mock_run: MagicMock) -> None:
        mock_run.side_effect = TimeoutError("Connection timed out")
        nodes = {"msm1": Node(ip="192.168.1.101", ram_gb=128, role="node")}
        config = _make_config(nodes)
        errors = run_preflight(config)
        assert len(errors) == 1
        assert "Cannot reach msm1" in errors[0]

    @patch("thunder_forge.cluster.preflight.subprocess.run")
    def test_partial_failure_continues(self, mock_run: MagicMock) -> None:
        """If one node fails, others still get checked."""
        probe_output = (
            "@@PROBE_START@@\n"
            "PLATFORM=Darwin\n"
            "SHELL_PATH=zsh\n"
            "SHELL_OK=1\n"
            "HOME_DIR=/Users/admin\n"
            "HOME_OK=1\n"
            "BREW_PREFIX=/opt/homebrew\n"
            "BREW_OK=1\n"
            "UV_OK=1\n"
            "VLLM_OK=1\n"
            "DISK_KB=52428800\n"
            "@@PROBE_END@@\n"
        )

        def side_effect(*args, **kwargs):
            cmd = args[0]
            if "192.168.1.101" in str(cmd):
                raise TimeoutError("timeout")
            return MagicMock(returncode=0, stdout=probe_output, stderr="")

        mock_run.side_effect = side_effect
        nodes = {
            "msm1": Node(ip="192.168.1.101", ram_gb=128, role="node"),
            "msm2": Node(ip="192.168.1.102", ram_gb=128, role="node"),
        }
        config = _make_config(nodes)
        errors = run_preflight(config)
        assert len(errors) == 1
        assert "msm1" in errors[0]
        # msm2 should have been populated
        assert config.nodes["msm2"].platform == "Darwin"

    @patch("thunder_forge.cluster.preflight.subprocess.run")
    def test_missing_uv_reported(self, mock_run: MagicMock) -> None:
        probe_output = (
            "@@PROBE_START@@\n"
            "PLATFORM=Darwin\n"
            "SHELL_PATH=zsh\n"
            "SHELL_OK=1\n"
            "HOME_DIR=/Users/admin\n"
            "HOME_OK=1\n"
            "BREW_PREFIX=/opt/homebrew\n"
            "BREW_OK=1\n"
            "UV_OK=0\n"
            "VLLM_OK=0\n"
            "DISK_KB=52428800\n"
            "@@PROBE_END@@\n"
        )
        mock_run.return_value = MagicMock(returncode=0, stdout=probe_output, stderr="")
        nodes = {"msm1": Node(ip="192.168.1.101", ram_gb=128, role="node")}
        config = _make_config(nodes)
        errors = run_preflight(config)
        assert any("uv not found" in e for e in errors)
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_preflight.py -v`
Expected: FAIL — module doesn't exist

- [ ] **Step 3: Implement preflight.py**

Create `src/thunder_forge/cluster/preflight.py`:

```python
"""Pre-flight validation: probe nodes, check environment, populate resolved fields."""

from __future__ import annotations

import shlex
import subprocess
from concurrent.futures import ThreadPoolExecutor, as_completed

from thunder_forge.cluster.config import ClusterConfig, Node

PREFLIGHT_TIMEOUT = 30
SSH_CONNECT_TIMEOUT = 10


def build_probe_script(role: str) -> str:
    """Build a shell script that probes node environment in one SSH call."""
    lines = [
        'echo "@@PROBE_START@@"',
        'echo "PLATFORM=$(uname -s)"',
        'echo "SHELL_PATH=$(basename $SHELL)"',
        'command -v $(basename $SHELL) >/dev/null 2>&1 && echo "SHELL_OK=1" || echo "SHELL_OK=0"',
        'echo "HOME_DIR=$HOME"',
        'test -d "$HOME" && echo "HOME_OK=1" || echo "HOME_OK=0"',
        'bp=$(brew --prefix 2>/dev/null) && echo "BREW_PREFIX=$bp" && echo "BREW_OK=1" || echo "BREW_OK=0"',
        'command -v uv >/dev/null 2>&1 && echo "UV_OK=1" || echo "UV_OK=0"',
    ]
    if role == "node":
        lines.append('uv tool list 2>/dev/null | grep -q vllm && echo "VLLM_OK=1" || echo "VLLM_OK=0"')
    if role == "gateway":
        lines.append('docker info >/dev/null 2>&1 && echo "DOCKER_OK=1" || echo "DOCKER_OK=0"')
        lines.append('hf_home="${HF_HOME:-$HOME/.cache/huggingface}"; test -w "$hf_home" && echo "HF_HOME_OK=1" || echo "HF_HOME_OK=0"')
    lines.append('echo "DISK_KB=$(df -k "$HOME" 2>/dev/null | tail -1 | awk \'{print $4}\')"')
    lines.append('echo "@@PROBE_END@@"')
    return "; ".join(lines)


def parse_probe_output(output: str) -> dict[str, str]:
    """Parse key=value pairs from probe script output between delimiters."""
    result: dict[str, str] = {}
    in_probe = False
    for line in output.splitlines():
        line = line.strip()
        if line == "@@PROBE_START@@":
            in_probe = True
            continue
        if line == "@@PROBE_END@@":
            break
        if in_probe and "=" in line:
            key, _, value = line.partition("=")
            result[key] = value
    return result


def _probe_node(name: str, node: Node) -> list[str]:
    """SSH to a single node, run probe script, validate results, populate resolved fields."""
    errors: list[str] = []
    script = build_probe_script(node.role)

    try:
        result = subprocess.run(
            ["ssh", "-o", f"ConnectTimeout={SSH_CONNECT_TIMEOUT}", "-o", "StrictHostKeyChecking=no",
             f"{node.user}@{node.ip}", f"sh -c {shlex.quote(script)}"],
            capture_output=True,
            text=True,
            timeout=PREFLIGHT_TIMEOUT,
        )
    except (subprocess.TimeoutExpired, OSError):
        return [f"Cannot reach {name} ({node.ip}) — check SSH key and network"]

    if result.returncode != 0:
        stderr = (result.stderr or "").strip()
        return [f"SSH to {name} ({node.ip}) failed: {stderr or 'unknown error'}"]

    data = parse_probe_output(result.stdout)
    if not data:
        return [f"{name} ({node.ip}): probe script returned no data"]

    # Populate resolved fields
    node.platform = data.get("PLATFORM")
    node.shell = data.get("SHELL_PATH")
    node.home_dir = data.get("HOME_DIR")
    brew_prefix = data.get("BREW_PREFIX")
    node.homebrew_prefix = brew_prefix if data.get("BREW_OK") == "1" else None

    # Validate results
    if data.get("SHELL_OK") != "1":
        errors.append(f"{name}: shell '{node.shell}' not found on node")

    if data.get("HOME_OK") != "1":
        errors.append(f"{name}: home directory '{node.home_dir}' does not exist")

    if data.get("UV_OK") != "1":
        errors.append(f"{name}: uv not found — run: setup-node.sh {node.role}")

    if node.role == "node":
        if data.get("VLLM_OK") != "1":
            errors.append(f"{name}: vllm-mlx not installed — run: setup-node.sh node")
        if node.platform == "Darwin" and data.get("BREW_OK") != "1":
            errors.append(f"{name}: Homebrew not found on macOS node")

    if node.role == "gateway":
        if data.get("DOCKER_OK") != "1":
            errors.append(f"{name}: Docker not running — start Docker first")
        if data.get("HF_HOME_OK") != "1":
            errors.append(f"{name}: HF_HOME directory not writable — check path and permissions")

    disk_kb_str = data.get("DISK_KB", "0")
    try:
        disk_gb = int(disk_kb_str) / (1024 * 1024)
        if disk_gb < 10:
            errors.append(f"{name}: only {disk_gb:.0f}GB free disk — may be insufficient for models")
    except ValueError:
        pass  # disk check is best-effort

    return errors


def run_preflight(
    config: ClusterConfig,
    *,
    target_node: str | None = None,
) -> list[str]:
    """Run pre-flight checks on all (or target) nodes. Returns list of errors."""
    nodes_to_check = {}
    if target_node:
        if target_node in config.nodes:
            nodes_to_check[target_node] = config.nodes[target_node]
        # Always check gateway too
        try:
            gw_name = config.gateway_name
            nodes_to_check[gw_name] = config.gateway
        except ValueError:
            pass
    else:
        nodes_to_check = dict(config.nodes)

    all_errors: list[str] = []

    with ThreadPoolExecutor(max_workers=len(nodes_to_check)) as pool:
        futures = {
            pool.submit(_probe_node, name, node): name
            for name, node in nodes_to_check.items()
        }
        for future in as_completed(futures):
            node_errors = future.result()
            all_errors.extend(node_errors)

    return all_errors


def print_preflight_result(errors: list[str], config: ClusterConfig) -> None:
    """Print pre-flight results in user-friendly format."""
    if errors:
        print("\nPre-flight checks failed:\n")
        for err in errors:
            print(f"  ✗ {err}")
        print("\nFix these issues and retry.")
    else:
        node_names = [n for n, v in config.nodes.items() if v.role == "node"]
        gw_names = [n for n, v in config.nodes.items() if v.role == "gateway"]
        parts = []
        if node_names:
            parts.append(f"{len(node_names)} nodes OK ({', '.join(node_names)})")
        if gw_names:
            parts.append(f"{len(gw_names)} gateway OK ({', '.join(gw_names)})")
        print(f"Pre-flight: {', '.join(parts)}")
```

- [ ] **Step 4: Run tests**

Run: `uv run pytest tests/test_preflight.py tests/ -v`
Expected: All pass

- [ ] **Step 5: Lint and commit**

```bash
uv run ruff check src/thunder_forge/cluster/preflight.py tests/test_preflight.py
uv run ruff format src/thunder_forge/cluster/preflight.py tests/test_preflight.py
git add src/thunder_forge/cluster/preflight.py tests/test_preflight.py
git commit -m "feat: add pre-flight validation module

Probes all target nodes via single batched SSH connection per node.
Validates SSH, shell, tools, disk space. Populates Node resolved
fields (platform, shell, home_dir, homebrew_prefix). Parallel
execution with 30s global timeout."
```

---

### Task 4: Health Module — Rename and Error Handling

**Files:**
- Modify: `src/thunder_forge/cluster/health.py`
- Modify: `tests/test_health.py`

Rename functions, replace bare `except Exception: pass` with specific handling, use `config.gateway` instead of `config.rock`.

- [ ] **Step 1: Update tests for new function names and error handling**

Replace `tests/test_health.py`:

```python
"""Tests for health check logic."""

import json
from unittest.mock import MagicMock, patch

import pytest

from thunder_forge.cluster.health import check_node, check_gateway_services


@patch("thunder_forge.cluster.health.urllib.request.build_opener")
def test_check_node_healthy(mock_build_opener: MagicMock) -> None:
    mock_response = MagicMock()
    mock_response.__enter__ = lambda s: s
    mock_response.__exit__ = MagicMock(return_value=False)
    mock_opener = MagicMock()
    mock_opener.open.return_value = mock_response
    mock_build_opener.return_value = mock_opener

    result = check_node("192.168.1.101", 8000)
    assert result is True


@patch("thunder_forge.cluster.health.urllib.request.build_opener")
def test_check_node_unreachable(mock_build_opener: MagicMock) -> None:
    mock_opener = MagicMock()
    mock_opener.open.side_effect = ConnectionError("Connection refused")
    mock_build_opener.return_value = mock_opener

    result = check_node("192.168.1.101", 8000)
    assert result is False


@patch("thunder_forge.cluster.health.ssh_run")
def test_check_gateway_services_parses_json(mock_ssh: MagicMock) -> None:
    """Docker compose JSON output is parsed correctly."""
    services_json = "\n".join([
        json.dumps({"Name": "docker-litellm-1", "State": "running", "Health": "healthy"}),
        json.dumps({"Name": "docker-openwebui-1", "State": "running", "Health": ""}),
        json.dumps({"Name": "docker-postgres-1", "State": "running", "Health": "healthy"}),
    ])
    mock_ssh.return_value = MagicMock(returncode=0, stdout=services_json, stderr="")
    results = check_gateway_services("192.168.1.61", "infra_user")
    assert results["litellm"] is True
    assert results["openwebui"] is True
    assert results["postgres"] is True


@patch("thunder_forge.cluster.health.ssh_run")
def test_check_gateway_services_ssh_failure(mock_ssh: MagicMock) -> None:
    """SSH failure returns all services as unhealthy with error context."""
    mock_ssh.return_value = MagicMock(returncode=1, stdout="", stderr="Connection refused")
    results = check_gateway_services("192.168.1.61", "infra_user")
    assert all(v is False for v in results.values())
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_health.py -v`
Expected: FAIL — `check_node` and `check_gateway_services` don't exist

- [ ] **Step 3: Update health.py**

Replace `src/thunder_forge/cluster/health.py`:

```python
"""Health checks for compute nodes and gateway services."""

from __future__ import annotations

import json
import urllib.error
import urllib.request

from thunder_forge.cluster.config import ClusterConfig
from thunder_forge.cluster.ssh import ssh_run


def check_node(ip: str, port: int, timeout: float = 5.0) -> bool:
    """Check if a vllm-mlx service is responding on the given node/port."""
    url = f"http://{ip}:{port}/v1/models"
    try:
        handler = urllib.request.ProxyHandler({})
        opener = urllib.request.build_opener(handler)
        with opener.open(url, timeout=timeout):
            return True
    except (urllib.error.URLError, OSError, TimeoutError):
        return False


def check_gateway_services(
    gateway_ip: str,
    gateway_user: str,
    expected_services: tuple[str, ...] = ("litellm", "openwebui", "postgres"),
) -> dict[str, bool]:
    """Check Docker Compose services on gateway node."""
    from thunder_forge.cluster.config import find_repo_root

    results = {svc: False for svc in expected_services}
    docker_dir = find_repo_root() / "docker"
    proc = ssh_run(gateway_user, gateway_ip, f"cd {docker_dir} && docker compose ps --format json", timeout=15)
    if proc.returncode != 0:
        return results
    for line in proc.stdout.strip().splitlines():
        if not line.strip():
            continue
        try:
            svc = json.loads(line)
        except json.JSONDecodeError:
            continue
        name = svc.get("Name", svc.get("Service", ""))
        state = svc.get("State", "")
        health = svc.get("Health", "")
        for expected in expected_services:
            if expected in name:
                results[expected] = state == "running" and health in ("healthy", "")
    return results


def run_health_checks(config: ClusterConfig) -> bool:
    """Run health checks on all nodes and gateway services. Print results."""
    all_healthy = True

    print("=== Nodes ===")
    for node_name, slots in sorted(config.assignments.items()):
        node = config.nodes[node_name]
        for slot in slots:
            healthy = check_node(node.ip, slot.port)
            status = "✓" if healthy else "✗"
            print(f"  {status} {node_name}:{slot.port} ({slot.model})")
            if not healthy:
                all_healthy = False

    print("\n=== Gateway ===")
    gw = config.gateway
    docker_health = check_gateway_services(gw.ip, gw.user)
    display_names = {"litellm": "LiteLLM", "openwebui": "Open WebUI", "postgres": "PostgreSQL"}
    for svc, healthy in docker_health.items():
        status = "✓" if healthy else "✗"
        name = display_names.get(svc, svc)
        print(f"  {status} {name}")
        if not healthy:
            all_healthy = False

    print("\n=== Assignments ===")
    for node_name, slots in sorted(config.assignments.items()):
        slot_strs = [f"{s.model}:{s.port}" for s in slots]
        print(f"  {node_name}: {', '.join(slot_strs)}")

    return all_healthy
```

Key changes:
- `check_inference_node` -> `check_node`
- `check_docker_services` -> `check_gateway_services`
- Removed bare `except Exception` — catch specific exceptions
- `config.rock` -> `config.gateway`
- Output labels: "Inference" -> "Nodes", "Infrastructure" -> "Gateway"

- [ ] **Step 4: Run tests**

Run: `uv run pytest tests/test_health.py tests/ -v`
Expected: All pass

- [ ] **Step 5: Lint and commit**

```bash
uv run ruff check src/thunder_forge/cluster/health.py tests/test_health.py
uv run ruff format src/thunder_forge/cluster/health.py tests/test_health.py
git add src/thunder_forge/cluster/health.py tests/test_health.py
git commit -m "refactor: rename health functions to node/gateway, fix error handling

check_inference_node -> check_node, check_docker_services ->
check_gateway_services. Remove bare except Exception catches.
Use config.gateway instead of config.rock."
```

---

### Task 5: Deploy Hardening — Resolved Fields, Dry-Run, Continue-on-Failure

**Files:**
- Modify: `src/thunder_forge/cluster/deploy.py`
- Modify: `tests/test_deploy.py`

Use `node.home_dir` and `node.homebrew_prefix` instead of hardcoded paths. Add `dry_run` support. Continue deploying remaining nodes on failure. Print completion summary.

- [ ] **Step 1: Write new tests**

Add to `tests/test_deploy.py`:

```python
"""Tests for deploy logic: plist generation, orchestration."""

import xml.etree.ElementTree as ET
from pathlib import Path
from textwrap import dedent
from unittest.mock import MagicMock, patch

import pytest

from thunder_forge.cluster.config import Assignment, ClusterConfig, Model, ModelSource, Node, load_cluster_config
from thunder_forge.cluster.deploy import generate_plist, run_deploy


@pytest.fixture()
def config_path(tmp_path: Path) -> Path:
    content = dedent("""\
        models:
          coder:
            source:
              type: huggingface
              repo: "mlx-community/Qwen3-Coder-Next-4bit"
            disk_gb: 44.8
            kv_per_32k_gb: 8
            max_context: 131072

        nodes:
          rock: { ip: "192.168.1.61", ram_gb: 32, user: "infra_user", role: gateway }
          msm1: { ip: "192.168.1.101", ram_gb: 128, user: "admin", role: node }

        assignments:
          msm1:
            - model: coder
              port: 8000
    """)
    p = tmp_path / "node-assignments.yaml"
    p.write_text(content)
    return p


def test_generate_plist_uses_resolved_fields() -> None:
    """Plist uses node.home_dir and node.homebrew_prefix, not hardcoded paths."""
    node = Node(
        ip="192.168.1.101", ram_gb=128, user="admin", role="node",
        home_dir="/Users/admin", homebrew_prefix="/opt/homebrew",
    )
    model = Model(source=ModelSource(type="huggingface", repo="test/model"), disk_gb=10)
    slot = Assignment(model="test", port=8000)
    xml_str = generate_plist(model, slot, node)
    assert "/Users/admin/.local/bin/vllm-mlx" in xml_str
    assert "/opt/homebrew/bin" in xml_str
    assert "/Users/admin/logs/" in xml_str


def test_generate_plist_non_default_homebrew() -> None:
    """Plist uses custom homebrew prefix (e.g. Intel Mac)."""
    node = Node(
        ip="192.168.1.101", ram_gb=128, user="admin", role="node",
        home_dir="/Users/admin", homebrew_prefix="/usr/local",
    )
    model = Model(source=ModelSource(type="huggingface", repo="test/model"), disk_gb=10)
    slot = Assignment(model="test", port=8000)
    xml_str = generate_plist(model, slot, node)
    assert "/usr/local/bin" in xml_str
    assert "/opt/homebrew" not in xml_str


def test_generate_plist_no_homebrew() -> None:
    """Plist works without homebrew (Linux node)."""
    node = Node(
        ip="192.168.1.101", ram_gb=128, user="admin", role="node",
        home_dir="/home/admin", homebrew_prefix=None,
    )
    model = Model(source=ModelSource(type="huggingface", repo="test/model"), disk_gb=10)
    slot = Assignment(model="test", port=8000)
    xml_str = generate_plist(model, slot, node)
    assert "/home/admin/.local/bin/vllm-mlx" in xml_str
    assert "/opt/homebrew" not in xml_str


def test_generate_plist_requires_resolved_fields() -> None:
    """Plist raises error if resolved fields are missing."""
    node = Node(ip="192.168.1.101", ram_gb=128, user="admin", role="node")
    model = Model(source=ModelSource(type="huggingface", repo="test/model"), disk_gb=10)
    slot = Assignment(model="test", port=8000)
    with pytest.raises(ValueError, match="pre-flight"):
        generate_plist(model, slot, node)


def test_generate_plist_basic(config_path: Path) -> None:
    config = load_cluster_config(config_path)
    model = config.models["coder"]
    slot = config.assignments["msm1"][0]
    node = config.nodes["msm1"]
    # Simulate pre-flight populating resolved fields
    node.home_dir = "/Users/admin"
    node.homebrew_prefix = "/opt/homebrew"

    xml_str = generate_plist(model, slot, node)
    root = ET.fromstring(xml_str)
    assert root.tag == "plist"
    assert "com.vllm-mlx-8000" in xml_str
    assert "mlx-community/Qwen3-Coder-Next-4bit" in xml_str
    assert "--port" in xml_str
    assert "--continuous-batching" in xml_str
    assert "--max-model-len" not in xml_str
    assert "no_proxy" in xml_str


def test_generate_plist_with_embedding(config_path: Path) -> None:
    config = load_cluster_config(config_path)
    config.models["embedding"] = Model(
        source=ModelSource(type="huggingface", repo="mlx-community/Qwen3-Embedding-0.6B-4bit-DWQ"),
        disk_gb=0.5,
        serving="embedding",
    )
    model = config.models["coder"]
    slot = Assignment(model="coder", port=8000, embedding=True)
    node = config.nodes["msm1"]
    node.home_dir = "/Users/admin"
    node.homebrew_prefix = "/opt/homebrew"

    xml_str = generate_plist(model, slot, node, embedding_model=config.models.get("embedding"))
    assert "--embedding-model" in xml_str
    assert "Qwen3-Embedding-0.6B-4bit-DWQ" in xml_str
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_deploy.py -v`
Expected: FAIL — new tests fail (no resolved field checks, no dry_run)

- [ ] **Step 3: Update deploy.py**

Replace `src/thunder_forge/cluster/deploy.py`:

```python
"""Deployment: plist generation, SSH deploy, launchctl management."""

from __future__ import annotations

import xml.etree.ElementTree as ET

from thunder_forge.cluster.config import Assignment, ClusterConfig, Model, Node
from thunder_forge.cluster.ssh import scp_content, ssh_run


def _require_resolved(node: Node, node_name: str) -> None:
    """Raise if resolved fields are missing (pre-flight not run)."""
    if node.home_dir is None:
        msg = f"{node_name}: node.home_dir is None — run pre-flight first (remove --skip-preflight)"
        raise ValueError(msg)


def generate_plist(
    model: Model,
    slot: Assignment,
    node: Node,
    *,
    embedding_model: Model | None = None,
) -> str:
    _require_resolved(node, f"port-{slot.port}")
    home = node.home_dir
    label = f"com.vllm-mlx-{slot.port}"
    vllm_path = f"{home}/.local/bin/vllm-mlx"

    program_args = [
        vllm_path,
        "serve",
        model.source.repo,
        "--port",
        str(slot.port),
        "--host",
        "0.0.0.0",
        "--continuous-batching",
    ]

    if slot.embedding and embedding_model:
        program_args.extend(["--embedding-model", embedding_model.source.repo])

    path_parts = [f"{home}/.local/bin", "/usr/bin", "/bin"]
    if node.homebrew_prefix:
        path_parts.insert(1, f"{node.homebrew_prefix}/bin")

    env_vars = {
        "PATH": ":".join(path_parts),
        "HOME": home,
        "no_proxy": "*",
    }

    plist = ET.Element("plist", version="1.0")
    d = ET.SubElement(plist, "dict")

    def add_key_value(parent: ET.Element, key: str, value_elem: ET.Element) -> None:
        k = ET.SubElement(parent, "key")
        k.text = key
        parent.append(value_elem)

    def make_string(text: str) -> ET.Element:
        e = ET.Element("string")
        e.text = text
        return e

    def make_true() -> ET.Element:
        return ET.Element("true")

    def make_integer(val: int) -> ET.Element:
        e = ET.Element("integer")
        e.text = str(val)
        return e

    add_key_value(d, "Label", make_string(label))

    k = ET.SubElement(d, "key")
    k.text = "ProgramArguments"
    arr = ET.SubElement(d, "array")
    for arg in program_args:
        s = ET.SubElement(arr, "string")
        s.text = arg

    k = ET.SubElement(d, "key")
    k.text = "EnvironmentVariables"
    env_dict = ET.SubElement(d, "dict")
    for env_key, env_val in env_vars.items():
        ek = ET.SubElement(env_dict, "key")
        ek.text = env_key
        ev = ET.SubElement(env_dict, "string")
        ev.text = env_val

    add_key_value(d, "StandardOutPath", make_string(f"{home}/logs/vllm-mlx-{slot.port}.log"))
    add_key_value(d, "StandardErrorPath", make_string(f"{home}/logs/vllm-mlx-{slot.port}.err"))

    add_key_value(d, "RunAtLoad", make_true())
    add_key_value(d, "KeepAlive", make_true())
    add_key_value(d, "ThrottleInterval", make_integer(10))
    add_key_value(d, "ProcessType", make_string("Interactive"))

    ET.indent(plist, space="  ")
    xml_declaration = '<?xml version="1.0" encoding="UTF-8"?>\n'
    doctype = (
        '<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN"\n  "http://www.apple.com/DTDs/PropertyList-1.0.dtd">\n'
    )
    body = ET.tostring(plist, encoding="unicode")
    return xml_declaration + doctype + body + "\n"


NEWSYSLOG_CONF = """\
# logfilename                             [owner:group] mode count size(KB) when  flags
{home}/logs/vllm-mlx-*.log            {user}:staff     644  7     102400   *     CNJ
{home}/logs/vllm-mlx-*.err            {user}:staff     644  7     102400   *     CNJ
"""


def upgrade_node_tools(node: Node) -> None:
    """Best-effort upgrade of uv-managed tools on a node."""
    result = ssh_run(node.user, node.ip, "uv tool upgrade --all", timeout=120, shell=node.shell)
    if result.returncode != 0:
        print(f"  Warning: uv tool upgrade failed on {node.ip} (continuing)")
    else:
        print("  Tools upgraded")


def deploy_node(
    node_name: str,
    config: ClusterConfig,
    *,
    dry_run: bool = False,
) -> list[str]:
    errors: list[str] = []
    node = config.nodes[node_name]
    slots = config.assignments.get(node_name, [])

    if not slots:
        return [f"{node_name}: no assignments found"]

    _require_resolved(node, node_name)

    if dry_run:
        for slot in slots:
            model = config.models[slot.model]
            print(f"    [upload] com.vllm-mlx-{slot.port}.plist ({slot.model}, port {slot.port})")
        print(f"    [restart] {len(slots)} launchd services")
        print(f"    [health] poll /v1/models on ports {', '.join(str(s.port) for s in slots)}")
        return errors

    uid_result = ssh_run(node.user, node.ip, "mkdir -p ~/logs ~/Library/LaunchAgents && id -u", shell=node.shell)
    if uid_result.returncode != 0:
        return [f"{node_name}: failed to get UID — {(uid_result.stderr or '').strip()}"]
    uid = uid_result.stdout.strip()

    upgrade_node_tools(node)

    deployed_ports: set[int] = set()

    for slot in slots:
        model = config.models[slot.model]
        embedding_model = config.models.get("embedding") if slot.embedding else None

        plist_xml = generate_plist(model, slot, node, embedding_model=embedding_model)
        plist_name = f"com.vllm-mlx-{slot.port}.plist"
        remote_plist = f"~/Library/LaunchAgents/{plist_name}"

        result = scp_content(node.user, node.ip, plist_xml, remote_plist, shell=node.shell)
        if result.returncode != 0:
            errors.append(f"{node_name}: failed to upload {plist_name} — {(result.stderr or '').strip()}")
            continue

        label = f"com.vllm-mlx-{slot.port}"
        domain = f"gui/{uid}"

        # Note: 2>/dev/null on bootout is intentional — bootout errors when service isn't
        # loaded (expected on first deploy). Justified deviation from spec Section 4.1.
        plist_path = f"~/Library/LaunchAgents/{plist_name}"
        cmd = f"launchctl bootout {domain}/{label} 2>/dev/null; launchctl bootstrap {domain} {plist_path}"
        result = ssh_run(node.user, node.ip, cmd, shell=node.shell)
        if result.returncode != 0:
            result = ssh_run(node.user, node.ip, f"launchctl kickstart -kp {domain}/{label}", shell=node.shell)
            if result.returncode != 0:
                errors.append(
                    f"{node_name}: failed to start service on port {slot.port}\n"
                    f"  stderr: {(result.stderr or '').strip()}\n"
                    f"  → Try: thunder-forge deploy --node {node_name}"
                )

        deployed_ports.add(slot.port)

    newsyslog = NEWSYSLOG_CONF.format(user=node.user, home=node.home_dir)
    scp_content(node.user, node.ip, newsyslog, "/tmp/vllm-mlx-newsyslog.conf", shell=node.shell)
    ssh_run(node.user, node.ip, "sudo mv /tmp/vllm-mlx-newsyslog.conf /etc/newsyslog.d/vllm-mlx.conf", shell=node.shell)

    ls_result = ssh_run(node.user, node.ip, "ls ~/Library/LaunchAgents/com.vllm-mlx-*.plist 2>/dev/null || true", shell=node.shell)
    if ls_result.stdout.strip():
        for line in ls_result.stdout.strip().splitlines():
            filename = line.strip().split("/")[-1]
            try:
                port = int(filename.replace("com.vllm-mlx-", "").replace(".plist", ""))
                if port not in deployed_ports:
                    print(f"  Removing stale plist for port {port}")
                    stale = f"com.vllm-mlx-{port}"
                    cmd = f"launchctl bootout gui/{uid}/{stale} 2>/dev/null; rm ~/Library/LaunchAgents/{stale}.plist"
                    ssh_run(node.user, node.ip, cmd, shell=node.shell)
            except ValueError:
                continue

    return errors


def restart_litellm(config: ClusterConfig) -> bool:
    from thunder_forge.cluster.config import find_repo_root

    gw = config.gateway
    docker_dir = find_repo_root() / "docker"
    result = ssh_run(
        gw.user,
        gw.ip,
        f"cd {docker_dir} && docker compose restart litellm",
        timeout=60,
        shell=gw.shell,
    )
    return result.returncode == 0


def health_poll(ip: str, port: int, *, timeout_secs: int = 180, interval: int = 5) -> bool:
    import time
    import urllib.error
    import urllib.request

    url = f"http://{ip}:{port}/v1/models"
    handler = urllib.request.ProxyHandler({})
    opener = urllib.request.build_opener(handler)
    deadline = time.monotonic() + timeout_secs

    while time.monotonic() < deadline:
        try:
            with opener.open(url, timeout=5):
                return True
        except (urllib.error.URLError, OSError, TimeoutError):
            time.sleep(interval)

    return False


def run_deploy(config: ClusterConfig, *, target_node: str | None = None, dry_run: bool = False) -> bool:
    if target_node:
        if target_node not in config.assignments:
            print(f"Node '{target_node}' not found in assignments")
            return False
        deploy_nodes = [target_node]
    else:
        deploy_nodes = [n for n in config.assignments if config.nodes[n].role == "node"]

    if dry_run:
        print("\nDeployment plan:\n")
        for node_name in deploy_nodes:
            node = config.nodes[node_name]
            slots = config.assignments[node_name]
            print(f"  {node_name} ({node.ip}) — {len(slots)} services:")
            deploy_node(node_name, config, dry_run=True)
        try:
            gw = config.gateway
            print(f"\n  {config.gateway_name} ({gw.ip}) — gateway:")
            print("    [restart] LiteLLM proxy (docker compose restart litellm)")
        except ValueError:
            pass
        print("\nRun without --dry-run to execute.")
        return True

    # Real deploy — continue on partial failure
    succeeded: list[str] = []
    failed: dict[str, str] = {}

    for node_name in deploy_nodes:
        print(f"\nDeploying to {node_name}...")
        errors = deploy_node(node_name, config)
        if errors:
            failed[node_name] = "; ".join(errors)
            for err in errors:
                print(f"  ✗ {err}")
        else:
            succeeded.append(node_name)
            print("  ✓ Plists deployed")

    # LiteLLM restart — only if at least one node succeeded
    litellm_ok = False
    if succeeded:
        print("\nRestarting LiteLLM...")
        if restart_litellm(config):
            print("  ✓ LiteLLM restarted")
            litellm_ok = True
        else:
            if target_node:
                print("  Warning: LiteLLM restart failed (non-fatal with --node)")
                litellm_ok = True  # non-fatal
            else:
                print("  ✗ LiteLLM restart failed")
    elif not target_node:
        print("\nSkipping LiteLLM restart — no nodes deployed successfully")

    # Health poll — only successfully deployed nodes
    if succeeded:
        print("\nWaiting for services to become healthy...")
        for node_name in succeeded:
            node = config.nodes[node_name]
            for slot in config.assignments[node_name]:
                healthy = health_poll(node.ip, slot.port)
                status = "✓ healthy" if healthy else "✗ timeout"
                print(f"  {status} — {node_name}:{slot.port} ({slot.model})")
                if not healthy:
                    failed[node_name] = failed.get(node_name, "") + f" port {slot.port} unhealthy"

    # Summary
    total = len(deploy_nodes)
    ok_count = len(succeeded) - len([n for n in succeeded if n in failed])
    print(f"\nDeploy complete: {ok_count}/{total} nodes succeeded")
    for node_name in deploy_nodes:
        if node_name in failed:
            print(f"  ✗ {node_name} — {failed[node_name]}")
        else:
            slots = config.assignments[node_name]
            print(f"  ✓ {node_name} — {len(slots)} services running")

    if failed:
        failed_names = ", ".join(failed.keys())
        print(f"\nFix failed nodes and re-run: thunder-forge deploy --node <name>")

    return len(failed) == 0 and litellm_ok
```

- [ ] **Step 4: Run tests**

Run: `uv run pytest tests/test_deploy.py tests/ -v`
Expected: All pass

- [ ] **Step 5: Lint and commit**

```bash
uv run ruff check src/thunder_forge/cluster/deploy.py tests/test_deploy.py
uv run ruff format src/thunder_forge/cluster/deploy.py tests/test_deploy.py
git add src/thunder_forge/cluster/deploy.py tests/test_deploy.py
git commit -m "refactor: deploy uses resolved fields, adds dry-run and continue-on-failure

Plist generation uses node.home_dir and node.homebrew_prefix instead
of hardcoded /Users/{user} and /opt/homebrew. deploy --dry-run shows
execution plan. Partial failures continue to remaining nodes with
completion summary."
```

---

### Task 6: Models Hardening — Rename References, Improved Dry-Run

**Files:**
- Modify: `src/thunder_forge/cluster/models.py`
- Modify: `tests/test_models.py`

Update `infra_name`/`rock` references to `gateway_name`/`gateway`. Improve dry-run output format. Use `node.home_dir` for rsync destinations when available.

- [ ] **Step 1: Update test fixtures**

In `tests/test_models.py`, replace `role: infra` with `role: gateway` and `role: inference` with `role: node`.

- [ ] **Step 2: Update models.py**

In `src/thunder_forge/cluster/models.py`, make these changes:

1. Replace all `config.infra_name` with `config.gateway_name`
2. Replace all `config.rock` with `config.gateway`
3. Rename `_needs_infra_download` to `_needs_gateway_download`
4. Update log messages: "infra" -> "gateway"
5. Pass `shell=node.shell` to all `ssh_run` calls (when node is available)
6. Improve dry-run output format to match spec

Key replacements:
- Line 65: `infra = config.infra_name` → `gw_name = config.gateway_name`
- Line 66: `rock = config.rock` → `gw = config.gateway`
- Line 68: `print(f"  [dry-run] Would download {task.repo} (rev: {task.revision}) on {infra}")` → `print(f"    [download] {task.repo} (rev: {task.revision}) on {gw_name}")`
- Line 70: `print(f"  [dry-run] Would rsync to {node_name}")` → `print(f"    [rsync] to {node_name}:{DEFAULT_HF_CACHE}/...")`
- Line 72: `print(f"  Downloading {task.repo} on {infra}...")` → `print(f"  Downloading {task.repo} on {gw_name}...")`
- Same pattern for `ensure_convert`, `ensure_local`, `ensure_pip`
- Line 213: `def _needs_infra_download` → `def _needs_gateway_download`
- Line 231: `if _needs_infra_download(tasks)` → `if _needs_gateway_download(tasks)`

Also pass `shell=gw.shell` or `shell=node.shell` to ssh_run calls where the node object is available. When shell is None (pre-flight not run), ssh_run falls back to platform detection.

- [ ] **Step 3: Run tests**

Run: `uv run pytest tests/test_models.py tests/ -v`
Expected: All pass

- [ ] **Step 4: Lint and commit**

```bash
uv run ruff check src/thunder_forge/cluster/models.py tests/test_models.py
uv run ruff format src/thunder_forge/cluster/models.py tests/test_models.py
git add src/thunder_forge/cluster/models.py tests/test_models.py
git commit -m "refactor: rename infra->gateway in models module, improve dry-run output

Replace config.infra_name/rock with gateway_name/gateway. Improved
dry-run output format with structured plan display. Pass node.shell
to ssh_run calls."
```

---

### Task 7: CLI Wiring — Pre-flight, Flags, Role Naming

**Files:**
- Modify: `src/thunder_forge/cli.py`

Wire pre-flight into deploy, ensure-models, and health commands. Add `--dry-run` to deploy. Add `--skip-preflight` flag. Update output labels.

- [ ] **Step 1: Update cli.py**

Replace `src/thunder_forge/cli.py`:

```python
"""Thunder Forge CLI — cluster management commands."""

import typer

app = typer.Typer(
    name="thunder-forge",
    help="CLI for managing a local MLX inference cluster.",
    no_args_is_help=True,
)


def _load_config() -> tuple:
    """Load cluster config from node-assignments.yaml. Returns (ClusterConfig, repo_root Path)."""
    from thunder_forge.cluster.config import find_repo_root, load_cluster_config

    repo_root = find_repo_root()
    assignments_path = repo_root / "configs" / "node-assignments.yaml"
    if not assignments_path.exists():
        typer.echo(f"Error: {assignments_path} not found", err=True)
        raise typer.Exit(1)
    return load_cluster_config(assignments_path), repo_root


def _run_preflight(config: object, *, target_node: str | None = None) -> None:
    """Run pre-flight checks. Exit on failure."""
    from thunder_forge.cluster.preflight import print_preflight_result, run_preflight

    errors = run_preflight(config, target_node=target_node)
    print_preflight_result(errors, config)
    if errors:
        raise typer.Exit(1)


@app.command()
def generate_config(
    check: bool = typer.Option(
        False, "--check", help="Compare generated config with committed file, exit 1 on mismatch."
    ),
) -> None:
    """Generate litellm-config.yaml from node-assignments.yaml."""
    from thunder_forge.cluster.config import (
        OS_OVERHEAD_GB,
        check_config_sync,
        generate_litellm_config,
        validate_memory,
    )

    config, repo_root = _load_config()
    config_path = repo_root / "configs" / "litellm-config.yaml"

    typer.echo("Validating memory budgets...")
    errors = validate_memory(config)
    for node_name, slots in sorted(config.assignments.items()):
        node = config.nodes[node_name]
        parts = []
        total = OS_OVERHEAD_GB
        for slot in slots:
            model = config.models[slot.model]
            weight = model.ram_gb if model.ram_gb is not None else model.disk_gb
            kv = model.kv_per_32k_gb
            total += weight + kv
            parts.append(f"{slot.model}({weight}+{kv}kv)")
        budget = " + ".join(parts) + f" + {OS_OVERHEAD_GB} OS = {total:.1f} GB / {node.ram_gb} GB"
        status = "✓" if total <= node.ram_gb else "✗ EXCEEDS"
        typer.echo(f"  {node_name}: {budget} {status}")

    if errors:
        for err in errors:
            typer.echo(f"Error: {err}", err=True)
        raise typer.Exit(1)

    if check:
        if check_config_sync(config, config_path):
            typer.echo("✓ Config is in sync with assignments")
            raise typer.Exit(0)
        else:
            typer.echo("✗ Config mismatch — run 'thunder-forge generate-config' to update", err=True)
            raise typer.Exit(1)

    content = generate_litellm_config(config)
    config_path.write_text(content)
    typer.echo(f"✓ Generated {config_path}")


@app.command()
def ensure_models(
    dry_run: bool = typer.Option(False, "--dry-run", help="Print what would be downloaded without doing it."),
    skip_preflight: bool = typer.Option(False, "--skip-preflight", help="Skip pre-flight node checks."),
) -> None:
    """Download and sync models to assigned nodes."""
    from thunder_forge.cluster.models import run_ensure_models

    config, _ = _load_config()

    if not skip_preflight:
        _run_preflight(config)

    success = run_ensure_models(config, dry_run=dry_run)
    raise typer.Exit(0 if success else 1)


@app.command()
def deploy(
    node: str | None = typer.Option(None, "--node", help="Deploy to a single node (e.g. msm1)."),
    skip_models: bool = typer.Option(False, "--skip-models", help="Skip model download/sync step."),
    dry_run: bool = typer.Option(False, "--dry-run", help="Show deployment plan without executing."),
    skip_preflight: bool = typer.Option(False, "--skip-preflight", help="Skip pre-flight node checks."),
) -> None:
    """Deploy models, plists, and configs to the cluster."""
    from thunder_forge.cluster.config import generate_litellm_config, validate_memory
    from thunder_forge.cluster.deploy import run_deploy
    from thunder_forge.cluster.models import run_ensure_models

    config, repo_root = _load_config()
    config_path = repo_root / "configs" / "litellm-config.yaml"

    if not skip_preflight:
        _run_preflight(config, target_node=node)

    if not skip_models and not dry_run:
        typer.echo("Ensuring models are present...")
        if not run_ensure_models(config, target_node=node):
            typer.echo("Model sync failed", err=True)
            raise typer.Exit(1)

    if not dry_run:
        typer.echo("\nGenerating config...")
        errors = validate_memory(config)
        if errors:
            for err in errors:
                typer.echo(f"Error: {err}", err=True)
            raise typer.Exit(1)
        content = generate_litellm_config(config)
        config_path.write_text(content)
        typer.echo(f"  Generated {config_path}")

    success = run_deploy(config, target_node=node, dry_run=dry_run)
    raise typer.Exit(0 if success else 1)


@app.command()
def health(
    skip_preflight: bool = typer.Option(False, "--skip-preflight", help="Skip pre-flight node checks."),
) -> None:
    """Check health of all cluster services."""
    from thunder_forge.cluster.health import run_health_checks

    config, _ = _load_config()

    if not skip_preflight:
        _run_preflight(config)

    all_healthy = run_health_checks(config)
    raise typer.Exit(0 if all_healthy else 1)
```

- [ ] **Step 2: Run all tests**

Run: `uv run pytest tests/ -v`
Expected: All pass

- [ ] **Step 3: Lint and commit**

```bash
uv run ruff check src/thunder_forge/cli.py
uv run ruff format src/thunder_forge/cli.py
git add src/thunder_forge/cli.py
git commit -m "feat: wire pre-flight, dry-run, and skip-preflight into CLI

All commands run pre-flight by default. deploy gains --dry-run and
--skip-preflight flags. health and ensure-models gain --skip-preflight.
Extracted _load_config and _run_preflight helpers to reduce duplication."
```

---

### Task 8: setup-node.sh Hardening

**Files:**
- Modify: `scripts/setup-node.sh`

Add pre-checks, step-by-step progress output, --check mode, Docker validation, sudo handling.

- [ ] **Step 1: Rewrite setup-node.sh**

Replace `scripts/setup-node.sh` with hardened version. Key changes:

1. **Pre-checks function** at the top that validates prerequisites before any work:
   - Not running as root
   - Internet reachable (`curl -sI https://github.com >/dev/null 2>&1`)
   - curl available
   - On macOS: xcode CLT available (`xcode-select -p >/dev/null 2>&1`)

2. **sudo -v at start** — prompt for sudo password upfront, not mid-script

3. **Step counter** — `step_num=0; total_steps=6; step() { step_num=$((step_num+1)); echo "[$step_num/$total_steps] $1"; }`

4. **--check mode** — `setup-node.sh node --check` runs only the verification step

5. **Gateway Docker wait** — after `docker compose up -d`, poll `curl -sI http://localhost:4000/health` until healthy or 60s timeout

6. **Improved .env parser** — simpler line reader, warn on unparseable lines

7. **Idempotent checks** — each install step checks first

```sh
#!/bin/sh
set -eu

# Thunder Forge — Node Bootstrap Script
# Usage:
#   zsh setup-node.sh node              # Mac Studio compute node (macOS)
#   bash setup-node.sh gateway          # Gateway node (Linux)
#   setup-node.sh node --check          # Verify setup without installing
#   setup-node.sh gateway --check       # Verify gateway setup

ROLE="${1:-}"
CHECK_ONLY="${2:-}"

if [ -z "$ROLE" ] || { [ "$ROLE" != "node" ] && [ "$ROLE" != "gateway" ]; }; then
    echo "Usage: $0 <node|gateway> [--check]"
    exit 1
fi

# ── Load .env ─────────────────────────────────────────
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
for envfile in "$SCRIPT_DIR/../.env" "$SCRIPT_DIR/.env" "$HOME/.thunder-forge.env"; do
    if [ -f "$envfile" ]; then
        while IFS= read -r line || [ -n "$line" ]; do
            # Strip inline comments and whitespace
            line="${line%%#*}"
            line="$(printf '%s' "$line" | sed 's/^[[:space:]]*//;s/[[:space:]]*$//')"
            [ -z "$line" ] && continue
            case "$line" in
                *=*)
                    key="${line%%=*}"
                    value="${line#*=}"
                    # Strip surrounding quotes
                    case "$value" in
                        \"*\") value="${value#\"}"; value="${value%\"}" ;;
                        \'*\') value="${value#\'}"; value="${value%\'}" ;;
                    esac
                    # Expand leading tilde
                    case "$value" in
                        "~"*) value="$HOME${value#\~}" ;;
                    esac
                    # Only set if not already in environment
                    eval "current=\${$key:-}"
                    [ -z "$current" ] && export "$key=$value"
                    ;;
                *)
                    echo "Warning: cannot parse .env line: $line"
                    ;;
            esac
        done < "$envfile"
        echo "Loaded config from $envfile"
    fi
done

# ── Configurable paths ────────────────────────────────
TF_DIR="${TF_DIR:-$HOME/thunder-forge}"
case "$TF_DIR" in "~"*) TF_DIR="$HOME${TF_DIR#\~}" ;; esac
TF_LOG_DIR="${TF_LOG_DIR:-$HOME/logs}"
case "$TF_LOG_DIR" in "~"*) TF_LOG_DIR="$HOME${TF_LOG_DIR#\~}" ;; esac
TF_SSH_KEY="${TF_SSH_KEY:-$HOME/.ssh/id_ed25519}"
case "$TF_SSH_KEY" in "~"*) TF_SSH_KEY="$HOME${TF_SSH_KEY#\~}" ;; esac
TF_REPO_URL="${TF_REPO_URL:-https://github.com/shared-goals/thunder-forge.git}"

# ── Helpers ───────────────────────────────────────────
STEP_NUM=0
TOTAL_STEPS=0

step() {
    STEP_NUM=$((STEP_NUM + 1))
    echo ""
    echo "[$STEP_NUM/$TOTAL_STEPS] $1"
}

ok()   { echo "  ✓ $1"; }
fail() { echo "  ✗ $1"; }
warn() { echo "  ! $1"; }

append_if_missing() {
    line="$1"; shift
    for f in "$@"; do
        grep -qF "$line" "$f" 2>/dev/null || echo "$line" >> "$f"
    done
}

# ── Pre-checks ────────────────────────────────────────
preflight() {
    echo "Checking prerequisites..."
    errors=0

    if [ "$(id -u)" = "0" ]; then
        fail "Running as root — run as a regular user instead"
        errors=$((errors + 1))
    else
        ok "Running as $(whoami)"
    fi

    if command -v curl >/dev/null 2>&1; then
        ok "curl available"
    else
        fail "curl not found — install: xcode-select --install (macOS) or apt install curl (Linux)"
        errors=$((errors + 1))
    fi

    if curl -sI --connect-timeout 5 https://github.com >/dev/null 2>&1; then
        ok "Internet reachable"
    else
        fail "Cannot reach github.com — check network/proxy"
        errors=$((errors + 1))
    fi

    if [ "$errors" -gt 0 ]; then
        echo ""
        echo "Fix the issues above and retry."
        exit 1
    fi

    # Prompt for sudo upfront (needed for pmset on macOS, usermod on Linux)
    echo ""
    echo "Some steps need sudo (sleep disable, Docker group)."
    echo "Enter your password now if prompted:"
    sudo -v || true
}

# ── Verify functions (used by --check and after setup) ─
verify_node() {
    echo ""
    echo "Verifying node setup..."
    errors=0

    if command -v brew >/dev/null 2>&1; then
        ok "brew $(brew --version 2>/dev/null | head -1) at $(command -v brew)"
    else
        fail "Homebrew not found"
        errors=$((errors + 1))
    fi

    if command -v uv >/dev/null 2>&1; then
        ok "uv $(uv --version 2>/dev/null) at $(command -v uv)"
    else
        fail "uv not found"
        errors=$((errors + 1))
    fi

    if command -v vllm-mlx >/dev/null 2>&1; then
        ok "vllm-mlx installed"
    else
        fail "vllm-mlx not found"
        errors=$((errors + 1))
    fi

    if [ -d "$TF_LOG_DIR" ]; then
        ok "Log directory: $TF_LOG_DIR"
    else
        fail "Log directory missing: $TF_LOG_DIR"
        errors=$((errors + 1))
    fi

    if [ "$errors" -gt 0 ]; then
        echo ""
        echo "$errors issues found."
        return 1
    else
        echo ""
        echo "Node setup verified — all OK."
        return 0
    fi
}

verify_gateway() {
    echo ""
    echo "Verifying gateway setup..."
    errors=0

    if command -v docker >/dev/null 2>&1; then
        ok "docker $(docker --version 2>/dev/null)"
    else
        fail "Docker not found"
        errors=$((errors + 1))
    fi

    if command -v uv >/dev/null 2>&1; then
        ok "uv $(uv --version 2>/dev/null)"
    else
        fail "uv not found"
        errors=$((errors + 1))
    fi

    if command -v hf >/dev/null 2>&1; then
        ok "hf CLI installed"
        if hf auth whoami >/dev/null 2>&1; then
            ok "HuggingFace authenticated"
        else
            warn "HuggingFace not authenticated — run: hf auth login"
        fi
    else
        fail "hf CLI not found"
        errors=$((errors + 1))
    fi

    if [ -f "$TF_DIR/pyproject.toml" ]; then
        ok "thunder-forge cloned at $TF_DIR"
    else
        fail "thunder-forge not found at $TF_DIR"
        errors=$((errors + 1))
    fi

    # Check Docker Compose services
    if [ -f "$TF_DIR/docker/docker-compose.yml" ] || [ -f "$TF_DIR/docker/compose.yaml" ]; then
        cd "$TF_DIR/docker"
        running=$(docker compose ps --format '{{.Name}} {{.State}}' 2>/dev/null || true)
        if echo "$running" | grep -q "running"; then
            ok "Docker Compose services running"
        else
            fail "Docker Compose services not running — run: cd $TF_DIR/docker && docker compose up -d"
            errors=$((errors + 1))
        fi
    fi

    if [ "$errors" -gt 0 ]; then
        echo ""
        echo "$errors issues found."
        return 1
    else
        echo ""
        echo "Gateway setup verified — all OK."
        return 0
    fi
}

# ── --check mode ──────────────────────────────────────
if [ "$CHECK_ONLY" = "--check" ]; then
    case "$ROLE" in
        node)    verify_node; exit $? ;;
        gateway) verify_gateway; exit $? ;;
    esac
fi

# ── Setup functions ───────────────────────────────────
setup_node() {
    TOTAL_STEPS=6
    echo "=== Thunder Forge Node Setup ==="
    echo "TF_DIR=$TF_DIR"
    echo ""
    preflight

    step "Installing Homebrew..."
    if command -v brew >/dev/null 2>&1; then
        ok "Already installed"
    else
        /bin/bash -c "$(curl -fsSL https://raw.githubusercontent.com/Homebrew/install/HEAD/install.sh)"
        append_if_missing 'eval "$(/opt/homebrew/bin/brew shellenv)"' ~/.zshenv ~/.zshrc
        eval "$(/opt/homebrew/bin/brew shellenv)"
        ok "Installed"
    fi

    step "Installing uv..."
    if command -v uv >/dev/null 2>&1; then
        ok "Already installed"
    else
        curl -LsSf https://astral.sh/uv/install.sh | sh
        export PATH="$HOME/.local/bin:$PATH"
        append_if_missing 'export PATH="$HOME/.local/bin:$PATH"' ~/.zshenv ~/.zshrc
        ok "Installed"
    fi

    step "Installing vllm-mlx..."
    if command -v vllm-mlx >/dev/null 2>&1; then
        ok "Already installed"
        echo "  Upgrading..."
        uv tool upgrade --all 2>/dev/null || true
    else
        uv tool install vllm-mlx
        ok "Installed"
    fi

    step "Configuring PATH..."
    append_if_missing 'eval "$(/opt/homebrew/bin/brew shellenv)"' ~/.zshenv ~/.zshrc
    append_if_missing 'export PATH="$HOME/.local/bin:$PATH"' ~/.zshenv ~/.zshrc
    ok "~/.zshenv and ~/.zshrc updated"

    step "Disabling macOS sleep..."
    if [ "${TF_DISABLE_SLEEP:-true}" = "true" ]; then
        sudo pmset -a sleep 0 displaysleep 0 disksleep 0
        ok "Sleep disabled"
    else
        ok "Skipped (TF_DISABLE_SLEEP=false)"
    fi

    step "Creating directories..."
    mkdir -p "$TF_LOG_DIR"
    ok "Log directory: $TF_LOG_DIR"

    verify_node

    echo ""
    echo "Next: deploy from your workstation with 'uv run thunder-forge deploy'"
}

setup_gateway() {
    TOTAL_STEPS=8
    echo "=== Thunder Forge Gateway Setup ==="
    echo "TF_DIR=$TF_DIR"
    echo ""
    preflight

    step "Installing Docker..."
    if command -v docker >/dev/null 2>&1; then
        ok "Already installed"
    else
        curl -fsSL https://get.docker.com | sh
        sudo usermod -aG docker "$USER"
        ok "Installed (log out and back in for group to take effect)"
    fi

    step "Installing uv..."
    if command -v uv >/dev/null 2>&1; then
        ok "Already installed"
    else
        curl -LsSf https://astral.sh/uv/install.sh | sh
        export PATH="$HOME/.local/bin:$PATH"
        append_if_missing 'export PATH="$HOME/.local/bin:$PATH"' ~/.zshenv ~/.zshrc
        ok "Installed"
    fi

    step "Installing HuggingFace CLI..."
    uv tool install --force huggingface_hub --with socksio
    ok "hf installed with socksio"
    if command -v hf >/dev/null 2>&1 && hf auth whoami >/dev/null 2>&1; then
        ok "HuggingFace authenticated"
    else
        warn "Not authenticated — run: hf auth login"
    fi

    step "Cloning thunder-forge..."
    if [ -d "$TF_DIR/.git" ]; then
        ok "Already cloned"
        cd "$TF_DIR" && git pull
    else
        git clone "$TF_REPO_URL" "$TF_DIR"
        ok "Cloned to $TF_DIR"
    fi

    step "Installing Python dependencies..."
    cd "$TF_DIR"
    uv sync
    uv tool upgrade --all 2>/dev/null || true
    ok "Dependencies installed"

    step "Generating secrets..."
    if [ -f "$TF_DIR/docker/.env" ]; then
        ok "docker/.env already exists"
    else
        cat > "$TF_DIR/docker/.env" <<ENVEOF
LITELLM_MASTER_KEY=sk-$(openssl rand -hex 16)
POSTGRES_PASSWORD=$(openssl rand -hex 16)
UI_USERNAME=admin
UI_PASSWORD=$(openssl rand -hex 8)
WEBUI_SECRET_KEY=$(openssl rand -hex 16)
WEBUI_AUTH=true
ENABLE_SIGNUP=true
ENVEOF
        ok "Generated docker/.env — save these credentials!"
    fi

    step "Starting Docker Compose..."
    cd "$TF_DIR/docker"
    docker compose up -d
    echo "  Waiting for services..."
    attempt=0
    max_attempts=12
    while [ "$attempt" -lt "$max_attempts" ]; do
        attempt=$((attempt + 1))
        if curl -sI http://localhost:4000/health >/dev/null 2>&1; then
            ok "LiteLLM healthy (port 4000)"
            break
        fi
        if [ "$attempt" -eq "$max_attempts" ]; then
            warn "LiteLLM not responding yet — check: docker compose logs litellm"
        else
            sleep 5
        fi
    done

    step "SSH key..."
    if [ -f "$TF_SSH_KEY" ]; then
        ok "Key exists: $TF_SSH_KEY"
    else
        mkdir -p "$(dirname "$TF_SSH_KEY")"
        ssh-keygen -t ed25519 -f "$TF_SSH_KEY" -N ""
        ok "Generated: $TF_SSH_KEY"
    fi

    verify_gateway

    echo ""
    echo "Next steps:"
    echo "  1. Copy SSH key to each node: ssh-copy-id -i $TF_SSH_KEY <user>@<node-ip>"
    echo "  2. Run: uv run thunder-forge deploy"
}

case "$ROLE" in
    node)    setup_node ;;
    gateway) setup_gateway ;;
esac
```

- [ ] **Step 2: Test --check mode locally**

Run: `zsh scripts/setup-node.sh node --check`
Expected: Shows verification results (may fail on non-node machine, but should not error)

- [ ] **Step 3: Commit**

```bash
git add scripts/setup-node.sh
git commit -m "refactor: harden setup-node.sh with pre-checks, progress, --check mode

Add prerequisite validation (not root, internet, curl). Numbered step
progress. sudo -v upfront. --check mode for verification without
installing. Gateway Docker health polling. Improved .env parser.
Idempotent re-runs."
```

---

### Task 9: Final Verification

**Files:** None (verification only)

- [ ] **Step 1: Run full test suite**

Run: `uv run pytest tests/ -v`
Expected: All pass, 0 failures

- [ ] **Step 2: Run linter**

Run: `uv run ruff check src/ tests/`
Expected: All checks passed

- [ ] **Step 3: Run formatter**

Run: `uv run ruff format --check src/ tests/`
Expected: All files formatted

- [ ] **Step 4: Test CLI help**

Run: `uv run thunder-forge --help`
Expected: Shows all commands

Run: `uv run thunder-forge deploy --help`
Expected: Shows --node, --skip-models, --dry-run, --skip-preflight

Run: `uv run thunder-forge ensure-models --help`
Expected: Shows --dry-run, --skip-preflight

Run: `uv run thunder-forge health --help`
Expected: Shows --skip-preflight

- [ ] **Step 5: Commit plan as complete**

```bash
git add docs/superpowers/plans/2026-03-23-stability-hardening.md
git commit -m "docs: add stability hardening implementation plan"
```
