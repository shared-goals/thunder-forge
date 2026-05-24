"""SSH execution, deploy orchestration, deploy locking, and output streaming."""

from __future__ import annotations

import os
import shlex
import threading
import time
from datetime import UTC, datetime

import paramiko

from thunder_admin import db

_UV_RUN_FUNCTION = (
    "uv_run() { "
    'if command -v uv >/dev/null 2>&1; then uv "$@"; return; fi; '
    'for candidate in "$HOME/.local/bin/uv" "$HOME/.cargo/bin/uv" '
    '"/opt/homebrew/bin/uv" "/usr/local/bin/uv"; do '
    'if [ -x "$candidate" ]; then "$candidate" "$@"; return; fi; '
    "done; "
    'if command -v python3 >/dev/null 2>&1 && python3 -m uv --version >/dev/null 2>&1; then '
    'python3 -m uv "$@"; return; '
    "fi; "
    'if command -v python >/dev/null 2>&1 && python -m uv --version >/dev/null 2>&1; then '
    'python -m uv "$@"; return; '
    "fi; "
    'echo "uv not found: install uv or add it to PATH on the gateway" >&2; '
    "return 127; "
    "}"
)


def build_gateway_command(*args: str, tf_dir: str | None = None) -> str:
    """Build a gateway command that runs thunder-forge through the available uv."""
    repo_dir = tf_dir or os.environ["THUNDER_FORGE_DIR"]
    quoted_args = " ".join(shlex.quote(arg) for arg in ("run", "thunder-forge", *args))
    return (
        f"cd {shlex.quote(repo_dir)} && "
        f"set -a && [ -f .env ] && . ./.env && set +a && "
        f"{_UV_RUN_FUNCTION} && uv_run {quoted_args}"
    )


def _get_ssh_client() -> paramiko.SSHClient:
    """Create a paramiko SSH client configured for the gateway."""
    client = paramiko.SSHClient()
    client.set_missing_host_key_policy(paramiko.AutoAddPolicy())

    host = os.environ["GATEWAY_SSH_HOST"]
    port = int(os.environ.get("GATEWAY_SSH_PORT", "22"))
    user = os.environ["GATEWAY_SSH_USER"]
    key_path = os.environ.get("GATEWAY_SSH_KEY", "/ssh/id_ed25519")

    local_key = "/tmp/ssh_key"
    if os.path.exists(local_key):
        key_path = local_key

    pkey = paramiko.PKey.from_path(key_path)
    client.connect(
        hostname=host,
        port=port,
        username=user,
        pkey=pkey,
        timeout=10,
        look_for_keys=False,
        allow_agent=False,
    )
    return client


def check_gateway_connectivity() -> tuple[bool, str]:
    """Test SSH connectivity to the gateway. Returns (ok, message)."""
    try:
        client = _get_ssh_client()
        _, stdout, _ = client.exec_command("echo ok", timeout=10)
        result = stdout.read().decode().strip()
        client.close()
        return result == "ok", "Connected"
    except Exception as e:
        return False, str(e)


def ssh_exec(command: str, timeout: int = 600) -> tuple[int, str]:
    """Execute a command on the gateway via SSH. Returns (exit_code, output)."""
    client = _get_ssh_client()
    _, stdout, stderr = client.exec_command(command, timeout=timeout)
    out = stdout.read().decode()
    err = stderr.read().decode()
    exit_code = stdout.channel.recv_exit_status()
    client.close()
    return exit_code, out + err


def read_gateway_lock() -> dict | None:
    """Read the gateway deploy lock file. Returns dict with PID/HEARTBEAT or None."""
    exit_code, output = ssh_exec("cat /tmp/thunder-forge-deploy.lock 2>/dev/null", timeout=10)
    if exit_code != 0 or not output.strip():
        return None

    lock = {}
    for line in output.strip().split("\n"):
        if ":" in line:
            key, val = line.split(":", 1)
            lock[key.strip()] = val.strip()
    return lock


def check_gateway_lock_alive(lock: dict) -> str:
    """Check if a gateway lock is alive. Returns: 'alive', 'stale', or 'dead'."""
    pid = lock.get("PID")
    if not pid:
        return "dead"

    exit_code, _ = ssh_exec(f"kill -0 {pid} 2>/dev/null", timeout=10)
    if exit_code != 0:
        return "dead"

    heartbeat = lock.get("HEARTBEAT")
    if heartbeat:
        try:
            hb_time = int(heartbeat)
            age_seconds = time.time() - hb_time
            if age_seconds > 300:
                return "stale"
        except ValueError:
            pass

    return "alive"


def clear_gateway_lock() -> None:
    """Remove the gateway deploy lock file."""
    ssh_exec("rm -f /tmp/thunder-forge-deploy.lock", timeout=10)


def kill_gateway_deploy(pid: str) -> bool:
    """Kill a running deploy process on the gateway. Returns success."""
    exit_code, _ = ssh_exec(f"kill {pid} 2>/dev/null", timeout=10)
    if exit_code == 0:
        time.sleep(2)
        check_code, _ = ssh_exec(f"kill -0 {pid} 2>/dev/null", timeout=10)
        if check_code != 0:
            ssh_exec("rm -f /tmp/thunder-forge-deploy.lock", timeout=10)
            return True
        ssh_exec(f"kill -9 {pid} 2>/dev/null", timeout=10)
        time.sleep(2)
        ssh_exec("rm -f /tmp/thunder-forge-deploy.lock", timeout=10)
        return True
    return False


def run_deploy_ssh(deploy_id: int, config_yaml: str) -> None:
    """Run a full deploy via SSH in a background thread."""
    tf_dir = os.environ["THUNDER_FORGE_DIR"]

    try:
        client = _get_ssh_client()

        sftp = client.open_sftp()
        remote_config = f"{tf_dir}/configs/node-assignments.yaml"
        with sftp.open(remote_config, "w") as f:
            f.write(config_yaml)
        sftp.close()

        command = " && ".join(
            [
                build_gateway_command("generate-config", tf_dir=tf_dir),
                build_gateway_command("ensure-models", tf_dir=tf_dir),
                build_gateway_command("deploy", tf_dir=tf_dir),
            ]
        )

        channel = client.get_transport().open_session()
        channel.exec_command(command)

        output_parts: list[str] = []
        while not channel.exit_status_ready() or channel.recv_ready() or channel.recv_stderr_ready():
            if channel.recv_ready():
                chunk = channel.recv(4096).decode(errors="replace")
                output_parts.append(chunk)
                db.update_deploy(deploy_id, output="".join(output_parts))
            if channel.recv_stderr_ready():
                chunk = channel.recv_stderr(4096).decode(errors="replace")
                output_parts.append(chunk)
                db.update_deploy(deploy_id, output="".join(output_parts))
            time.sleep(0.5)

        while channel.recv_ready():
            output_parts.append(channel.recv(4096).decode(errors="replace"))
        while channel.recv_stderr_ready():
            output_parts.append(channel.recv_stderr(4096).decode(errors="replace"))

        exit_code = channel.recv_exit_status()
        final_output = "".join(output_parts)
        status = "success" if exit_code == 0 else "failed"

        db.update_deploy(deploy_id, status=status, output=final_output, finished_at=datetime.now(UTC))
        channel.close()
        client.close()

    except Exception as e:
        db.update_deploy(deploy_id, status="failed", output=f"SSH error: {e}", finished_at=datetime.now(UTC))


def run_service_op_ssh(op_id: int, op_type: str, target_node: str | None, skip_gateway: bool) -> None:
    """Run a service operation (restart/stop) via SSH in a background thread."""
    tf_dir = os.environ["THUNDER_FORGE_DIR"]

    try:
        client = _get_ssh_client()

        args = [op_type, "--skip-preflight"]
        if target_node:
            args.extend(["--node", target_node])
        if skip_gateway:
            args.append("--skip-gateway")
        command = build_gateway_command(*args, tf_dir=tf_dir)

        channel = client.get_transport().open_session()
        channel.exec_command(command)

        output_parts: list[str] = []
        while not channel.exit_status_ready() or channel.recv_ready() or channel.recv_stderr_ready():
            if channel.recv_ready():
                chunk = channel.recv(4096).decode(errors="replace")
                output_parts.append(chunk)
                db.update_service_op(op_id, output="".join(output_parts))
            if channel.recv_stderr_ready():
                chunk = channel.recv_stderr(4096).decode(errors="replace")
                output_parts.append(chunk)
                db.update_service_op(op_id, output="".join(output_parts))
            time.sleep(0.5)

        while channel.recv_ready():
            output_parts.append(channel.recv(4096).decode(errors="replace"))
        while channel.recv_stderr_ready():
            output_parts.append(channel.recv_stderr(4096).decode(errors="replace"))

        exit_code = channel.recv_exit_status()
        final_output = "".join(output_parts)
        status = "success" if exit_code == 0 else "failed"

        db.update_service_op(op_id, status=status, output=final_output, finished_at=datetime.now(UTC))
        channel.close()
        client.close()

    except Exception as e:
        db.update_service_op(op_id, status="failed", output=f"SSH error: {e}", finished_at=datetime.now(UTC))


def start_service_op(
    op_type: str, user_id: int, target_node: str | None = None, skip_gateway: bool = False,
) -> tuple[int | None, str]:
    """Start a service operation. Returns (op_id, error_message)."""
    running = db.get_running_service_op()
    if running:
        return None, f"Operation already in progress (started by {running.get('triggered_by_name', '?')})"

    op_id = db.create_service_op(op_type, user_id, target_node, skip_gateway)
    if op_id is None:
        return None, "Operation already in progress"

    thread = threading.Thread(
        target=run_service_op_ssh, args=(op_id, op_type, target_node, skip_gateway), daemon=True,
    )
    thread.start()
    return op_id, ""


def start_deploy(config_id: int, user_id: int, config_yaml: str) -> tuple[int | None, str]:
    """Start a deploy. Returns (deploy_id, error_message)."""
    running = db.get_running_deploy()
    if running:
        started = running["started_at"]
        if started and (datetime.now(UTC) - started).total_seconds() > 7200:
            lock = read_gateway_lock()
            if lock is None or check_gateway_lock_alive(lock) == "dead":
                db.update_deploy(
                    running["id"],
                    status="failed",
                    finished_at=datetime.now(UTC),
                    output=(running.get("output", "") or "")
                    + "\n[Marked as failed — process no longer running on gateway]",
                )
            else:
                return (
                    None,
                    f"Deploy already in progress (started by {running.get('triggered_by_name', '?')} at {started})",
                )
        else:
            return (
                None,
                f"Deploy already in progress (started by {running.get('triggered_by_name', '?')} at {started})",
            )

    deploy_id = db.create_deploy(config_id, user_id)
    if deploy_id is None:
        return None, "Deploy already in progress"

    thread = threading.Thread(target=run_deploy_ssh, args=(deploy_id, config_yaml), daemon=True)
    thread.start()

    return deploy_id, ""
