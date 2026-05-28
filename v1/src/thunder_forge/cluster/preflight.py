"""Pre-flight validation: probe nodes, check environment, populate resolved fields."""

from __future__ import annotations

import shlex
import subprocess
from concurrent.futures import ThreadPoolExecutor, as_completed

from thunder_forge.cluster.config import ClusterConfig, Node
from thunder_forge.cluster.ssh import _is_local, _ssh_key_args

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
        lines.append('uv tool list 2>/dev/null | grep -q mlx-lm && echo "MLX_LM_OK=1" || echo "MLX_LM_OK=0"')
    if role == "gateway":
        lines.append('docker info >/dev/null 2>&1 && echo "DOCKER_OK=1" || echo "DOCKER_OK=0"')
        hf_check = 'hf_home="${HF_HOME:-$HOME/.cache/huggingface}"; hf_home="${hf_home/#~/$HOME}"; test -w "$hf_home"'
        lines.append(f'{hf_check} && echo "HF_HOME_OK=1" || echo "HF_HOME_OK=0"')
    # Check disk space on the path that matters: HF_HOME for gateway (models), $HOME for nodes (services)
    if role == "gateway":
        lines.append('disk_path="${HF_HOME:-$HOME/.cache/huggingface}"; disk_path="${disk_path/#~/$HOME}"')
    else:
        lines.append('disk_path="$HOME"')
    lines.append('echo "DISK_KB=$(df -k "$disk_path" 2>/dev/null | tail -1 | awk \'{print $4}\')"')
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

    probe_shell = "zsh" if node.role == "node" else "bash"
    try:
        if _is_local(node.ip):
            # Run probe locally — no SSH needed for the gateway when running on it
            result = subprocess.run(
                [probe_shell, "-lc", script],
                capture_output=True,
                text=True,
                timeout=PREFLIGHT_TIMEOUT,
            )
        else:
            result = subprocess.run(
                [
                    "ssh",
                    *_ssh_key_args(),
                    "-o",
                    f"ConnectTimeout={SSH_CONNECT_TIMEOUT}",
                    "-o",
                    "StrictHostKeyChecking=no",
                    "-o",
                    "BatchMode=yes",
                    f"{node.user}@{node.ip}",
                    f"{probe_shell} -lc {shlex.quote(script)}",
                ],
                capture_output=True,
                text=True,
                timeout=PREFLIGHT_TIMEOUT,
            )
    except (subprocess.TimeoutExpired, OSError, TimeoutError):
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
        if data.get("MLX_LM_OK") != "1":
            print(f"  ⚠ {name}: mlx-lm not installed — deploy will install it")
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
    nodes_to_check: dict[str, Node] = {}
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
        futures = {pool.submit(_probe_node, name, node): name for name, node in nodes_to_check.items()}
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
