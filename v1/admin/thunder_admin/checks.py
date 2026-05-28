"""Pre-deploy status checks for each assignment slot."""

from __future__ import annotations

import os
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Literal

import httpx
import paramiko

from thunder_admin.config import validate_config
from thunder_forge.cluster.config import (
    Assignment,
    ClusterConfig,
    Node,
    parse_cluster_config,
)

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
        client.close()
        return ("error", f"unexpected response: {out[:60]}"), None
    except (TimeoutError, OSError) as e:
        client.close()
        if "timed out" in str(e).lower():
            return ("error", "SSH timeout"), None
        return ("error", str(e)[:120]), None
    except Exception as e:
        client.close()
        return ("error", str(e)[:120]), None


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


def fetch_logs(node: Node, port: int, tail_lines: int = 100) -> dict[str, str]:
    """Fetch recent stderr and stdout logs for a service via SSH.

    Returns {"stderr": ..., "stdout": ...} with log content or error messages.
    """
    result = {"stderr": "", "stdout": ""}
    try:
        client = paramiko.SSHClient()
        client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
        pkey = _resolve_ssh_key()
        client.connect(
            hostname=node.ip,
            username=node.user,
            pkey=pkey,
            timeout=_SSH_TIMEOUT,
            look_for_keys=False,
            allow_agent=False,
        )
        for key, suffix in [("stderr", "err"), ("stdout", "log")]:
            path = f"~/logs/mlx-lm-{port}.{suffix}"
            _, stdout, _ = client.exec_command(f"tail -n {tail_lines} {path} 2>&1", timeout=_SSH_TIMEOUT)
            result[key] = stdout.read().decode()
        client.close()
    except Exception as e:
        result["stderr"] = f"Failed to fetch logs: {e}"
    return result


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
        user = node.user or os.environ.get("GATEWAY_SSH_USER", "")
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
