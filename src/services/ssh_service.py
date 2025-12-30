from __future__ import annotations

import subprocess
from dataclasses import dataclass

from services.config_service import Node, SSHSettings


@dataclass(frozen=True)
class SSHResult:
    returncode: int
    stdout: str
    stderr: str


def _ssh_base_args(settings: SSHSettings) -> list[str]:
    args = [
        "ssh",
        "-o",
        f"ConnectTimeout={settings.connect_timeout_seconds}",
        "-o",
        "ServerAliveInterval=5",
        "-o",
        "ServerAliveCountMax=1",
        "-o",
        "StrictHostKeyChecking=accept-new",
    ]
    if settings.batch_mode:
        args += ["-o", "BatchMode=yes"]
    return args


def run_ssh(
    *,
    node: Node,
    settings: SSHSettings,
    remote_command: str,
    check: bool = True,
) -> SSHResult:
    host = node.ssh_host or node.wifi_ip
    target = f"{node.ssh_user}@{host}"
    cmd = _ssh_base_args(settings) + [target, remote_command]

    proc = subprocess.run(cmd, capture_output=True, text=True)
    result = SSHResult(proc.returncode, proc.stdout, proc.stderr)
    if check and proc.returncode != 0:
        raise RuntimeError(
            f"SSH failed for {node.name} ({target}): rc={proc.returncode}\n{proc.stderr.strip()}"
        )
    return result


def run_ssh_sudo(
    *,
    node: Node,
    settings: SSHSettings,
    remote_command: str,
    check: bool = True,
) -> SSHResult:
    # -n: non-interactive; fails fast if sudo needs a password.
    return run_ssh(node=node, settings=settings, remote_command=f"sudo -n {remote_command}", check=check)
