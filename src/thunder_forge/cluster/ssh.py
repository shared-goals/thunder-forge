"""Shared SSH and SCP helpers for remote operations."""

from __future__ import annotations

import functools
import os
import platform
import shlex
import socket
import subprocess


def _login_shell() -> str:
    """Return the login shell for the local machine: zsh on macOS, bash on Linux."""
    return "zsh" if platform.system() == "Darwin" else "bash"


@functools.lru_cache(maxsize=1)
def _ssh_key_args() -> tuple[str, ...]:
    """Return -i <key> args if GATEWAY_SSH_KEY is set, otherwise empty."""
    key = os.environ.get("GATEWAY_SSH_KEY")
    if key:
        key = os.path.expanduser(key)
        if os.path.isfile(key):
            return ("-i", key)
    return ()


@functools.lru_cache(maxsize=32)
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
    tty: bool = False,
) -> subprocess.CompletedProcess[str]:
    """Run a command on a remote node via SSH, or locally if the target is this machine."""
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
    ssh_cmd = [
        "ssh",
        *_ssh_key_args(),
        "-o",
        "ConnectTimeout=10",
        "-o",
        "StrictHostKeyChecking=no",
    ]
    if tty:
        ssh_cmd.append("-tt")
    ssh_cmd.extend([f"{user}@{ip}", wrapped])
    return subprocess.run(
        ssh_cmd,
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
    ssh_cmd = [
        "ssh",
        *_ssh_key_args(),
        "-o",
        "ConnectTimeout=10",
        "-o",
        "StrictHostKeyChecking=no",
        "-o",
        "BatchMode=yes",
        f"{user}@{ip}",
        f"cat > {remote_path}",
    ]
    return subprocess.run(
        ssh_cmd,
        input=content,
        capture_output=True,
        text=True,
        timeout=30,
    )
