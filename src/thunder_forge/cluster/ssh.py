"""Shared SSH and SCP helpers for remote operations."""

from __future__ import annotations

import functools
import json
import os
import platform
import shlex
import socket
import subprocess
from dataclasses import dataclass

from thunder_forge.cluster.config import Node

SSH_STRICT_HOST_KEY_CHECKING_ENV = "TF_SSH_STRICT_HOST_KEY_CHECKING"
DEFAULT_SSH_STRICT_HOST_KEY_CHECKING = "yes"
_ALLOWED_STRICT_HOST_KEY_VALUES = {"yes", "no", "ask", "accept-new"}
REMOTE_NODE_FACTS_COMMAND = (
    "python3 - <<'PY'\n"
    "import json\n"
    "import os\n"
    "import platform\n"
    "import shutil\n"
    "shell = os.environ.get('SHELL', '')\n"
    "brew = shutil.which('brew') or ''\n"
    "homebrew_prefix = os.path.dirname(os.path.dirname(brew)) if brew else ''\n"
    "print(json.dumps({\n"
    "    'platform': platform.system(),\n"
    "    'shell': os.path.basename(shell) if shell else '',\n"
    "    'home_dir': os.path.expanduser('~'),\n"
    "    'homebrew_prefix': homebrew_prefix,\n"
    "}, separators=(',', ':')))\n"
    "PY"
)


@dataclass(frozen=True)
class RemoteNodeFacts:
    """Resolved execution facts for a remote cluster node."""

    platform: str | None
    shell: str | None
    home_dir: str | None
    homebrew_prefix: str | None


def _login_shell() -> str:
    """Return the login shell for the local machine: zsh on macOS, bash on Linux."""
    return "zsh" if platform.system() == "Darwin" else "bash"


def _remote_shell_wrapper(cmd: str, shell: str | None) -> str:
    if shell:
        return f"{shell} -lc {shlex.quote(cmd)}"
    quoted_cmd = shlex.quote(cmd)
    return (
        f"if command -v zsh >/dev/null 2>&1; then exec zsh -lc {quoted_cmd}; "
        f"elif command -v bash >/dev/null 2>&1; then exec bash -lc {quoted_cmd}; "
        f"else exec sh -lc {quoted_cmd}; fi"
    )


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


def _strict_host_key_checking() -> str:
    value = os.environ.get(SSH_STRICT_HOST_KEY_CHECKING_ENV, DEFAULT_SSH_STRICT_HOST_KEY_CHECKING).strip()
    return value if value in _ALLOWED_STRICT_HOST_KEY_VALUES else DEFAULT_SSH_STRICT_HOST_KEY_CHECKING


def ssh_run(
    user: str,
    ip: str,
    cmd: str,
    *,
    timeout: int = 30,
    stream: bool = False,
    shell: str | None = None,
    node_id: str | None = None,
    tty: bool = False,
) -> subprocess.CompletedProcess[str]:
    """Run a command on a remote node via SSH, or locally if the target is this machine."""
    capture = not stream
    if _is_local(ip):
        effective_shell = shell or _login_shell()
        return subprocess.run(
            [effective_shell, "-lc", cmd],
            capture_output=capture,
            text=True,
            timeout=timeout,
        )
    wrapped = _remote_shell_wrapper(cmd, shell)
    ssh_cmd = [
        "ssh",
        *_ssh_key_args(),
        "-o",
        "ConnectTimeout=10",
        "-o",
        f"StrictHostKeyChecking={_strict_host_key_checking()}",
        "-o",
        "BatchMode=yes",
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


def _string_or_none(raw: object) -> str | None:
    if not isinstance(raw, str):
        return None
    value = raw.strip()
    return value or None


def resolve_remote_node_facts(node: Node, *, timeout: int = 30) -> RemoteNodeFacts:
    """Populate unset remote execution facts on a node via a lightweight SSH probe."""
    result = ssh_run(
        node.user,
        node.host,
        REMOTE_NODE_FACTS_COMMAND,
        timeout=timeout,
        shell=node.shell or "sh",
    )
    if result.returncode != 0:
        stderr = (result.stderr or "").strip()
        detail = f": {stderr}" if stderr else ""
        msg = f"failed to resolve remote node facts for {node.host}{detail}"
        raise RuntimeError(msg)

    lines = [line.strip() for line in (result.stdout or "").splitlines() if line.strip()]
    if not lines:
        msg = f"failed to resolve remote node facts for {node.host}: empty response"
        raise RuntimeError(msg)
    try:
        payload = json.loads(lines[-1])
    except json.JSONDecodeError as exc:
        msg = f"failed to parse remote node facts for {node.host}: {exc}"
        raise RuntimeError(msg) from exc
    if not isinstance(payload, dict):
        msg = f"failed to parse remote node facts for {node.host}: expected object"
        raise RuntimeError(msg)

    remote_platform = _string_or_none(payload.get("platform"))
    remote_shell = _string_or_none(payload.get("shell"))
    remote_home_dir = _string_or_none(payload.get("home_dir"))
    remote_homebrew_prefix = _string_or_none(payload.get("homebrew_prefix"))

    if node.platform is None:
        node.platform = remote_platform
    if node.shell is None:
        node.shell = remote_shell
    if node.home_dir is None:
        node.home_dir = remote_home_dir
    if node.homebrew_prefix is None:
        node.homebrew_prefix = remote_homebrew_prefix

    return RemoteNodeFacts(
        platform=node.platform,
        shell=node.shell,
        home_dir=node.home_dir,
        homebrew_prefix=node.homebrew_prefix,
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
            [effective_shell, "-lc", f"cat > {shlex.quote(remote_path)}"],
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
        f"StrictHostKeyChecking={_strict_host_key_checking()}",
        "-o",
        "BatchMode=yes",
        f"{user}@{ip}",
        f"cat > {shlex.quote(remote_path)}",
    ]
    return subprocess.run(
        ssh_cmd,
        input=content,
        capture_output=True,
        text=True,
        timeout=30,
    )
