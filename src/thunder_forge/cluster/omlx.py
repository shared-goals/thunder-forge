"""oMLX node-level runtime helpers."""

from __future__ import annotations

import shlex
import subprocess
import textwrap
import time
from dataclasses import dataclass, field

import httpx

from thunder_forge.cluster.config import Node, RuntimeType
from thunder_forge.cluster.ssh import ssh_run

LAUNCHD_LABEL_PREFIX = "com.thunder-forge.omlx"


def launchd_label_for_node(node: Node) -> str:
    """Build the launchd service label for a node runtime."""
    if node.runtime is None:
        msg = "Node has no runtime configured"
        raise ValueError(msg)
    return f"{LAUNCHD_LABEL_PREFIX}-{node.runtime.port}"


@dataclass
class OmlxStartResult:
    returncode: int
    pid: str = ""
    stdout: str = ""
    stderr: str = ""


@dataclass
class OmlxHealthResult:
    base_url: str
    health_ok: bool = False
    models_ok: bool = False
    status_ok: bool | None = None
    models: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)


@dataclass
class OmlxSmokeResult:
    base_url: str
    model: str
    health_ok: bool = False
    models_ok: bool = False
    model_visible: bool = False
    chat_ok: bool = False
    models: list[str] = field(default_factory=list)
    answer: str = ""
    latency_ms: int = 0
    errors: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return self.health_ok and self.models_ok and self.model_visible and self.chat_ok


def build_omlx_serve_command(node: Node) -> str:
    """Build the remote command that starts an oMLX server for a runtime node.

    oMLX's default model directory is intentionally represented as
    ``node.runtime.model_dir is None``. In that normal case, omit
    ``--model-dir`` and let oMLX use its own default ``~/.omlx/models``.
    """
    if node.runtime is None:
        msg = "Node has no runtime configured"
        raise ValueError(msg)
    if node.runtime.type != RuntimeType.OMLX:
        msg = f"Unsupported runtime type: {node.runtime.type}"
        raise ValueError(msg)
    if node.home_dir is None:
        msg = "node.home_dir is None — run pre-flight first or provide resolved home_dir"
        raise ValueError(msg)

    args = [
        f"{node.home_dir}/.local/bin/omlx",
        "serve",
        "--host",
        "0.0.0.0",
        "--port",
        str(node.runtime.port),
    ]
    if node.runtime.model_dir is not None:
        args.extend(["--model-dir", node.runtime.model_dir])
    return " ".join(shlex.quote(arg) for arg in args)


def run_omlx_runtime_start(node: Node, *, timeout: int = 30) -> OmlxStartResult:
    """Start a remote oMLX server under the node user with bounded SSH."""
    if node.home_dir is None:
        msg = "node.home_dir is None — run pre-flight first or provide resolved home_dir"
        raise ValueError(msg)
    if node.runtime is None:
        msg = "Node has no runtime configured"
        raise ValueError(msg)

    command = build_omlx_serve_command(node)
    log_path = f"/tmp/thunder-forge-omlx-{node.runtime.port}.log"
    remote_command = f"nohup {command} > {shlex.quote(log_path)} 2>&1 & echo $!"
    completed = subprocess.run(
        [
            "ssh",
            "-o",
            "BatchMode=yes",
            "-o",
            "ConnectTimeout=8",
            f"{node.user}@{node.host}",
            remote_command,
        ],
        check=False,
        text=True,
        capture_output=True,
        timeout=timeout,
    )
    return OmlxStartResult(
        returncode=completed.returncode,
        pid=completed.stdout.strip(),
        stdout=completed.stdout,
        stderr=completed.stderr,
    )


def _model_ids(payload: dict) -> list[str]:
    data = payload.get("data", [])
    if not isinstance(data, list):
        return []
    return [item["id"] for item in data if isinstance(item, dict) and isinstance(item.get("id"), str)]


def check_omlx_health(
    base_url: str,
    *,
    timeout: float = 5.0,
    transport: httpx.BaseTransport | None = None,
) -> OmlxHealthResult:
    """Probe an oMLX server directly."""
    normalized_base_url = base_url.rstrip("/")
    result = OmlxHealthResult(base_url=normalized_base_url)

    with httpx.Client(base_url=normalized_base_url, timeout=timeout, transport=transport, trust_env=False) as client:
        try:
            response = client.get("/health")
            result.health_ok = response.is_success
            if not response.is_success:
                result.errors.append(f"GET /health returned {response.status_code}")
        except httpx.HTTPError as exc:
            result.errors.append(f"GET /health failed: {exc}")

        try:
            response = client.get("/v1/models")
            result.models_ok = response.is_success
            if response.is_success:
                result.models = _model_ids(response.json())
            else:
                result.errors.append(f"GET /v1/models returned {response.status_code}")
        except (httpx.HTTPError, ValueError) as exc:
            result.errors.append(f"GET /v1/models failed: {exc}")

        try:
            response = client.get("/v1/models/status")
            result.status_ok = response.is_success
        except httpx.HTTPError:
            result.status_ok = False

    return result


def smoke_omlx_chat(
    base_url: str,
    *,
    model: str,
    prompt: str = "Reply with one short word: pong.",
    timeout: float = 30.0,
    transport: httpx.BaseTransport | None = None,
) -> OmlxSmokeResult:
    """Run a minimal direct oMLX chat smoke test."""
    normalized_base_url = base_url.rstrip("/")
    result = OmlxSmokeResult(base_url=normalized_base_url, model=model)

    with httpx.Client(base_url=normalized_base_url, timeout=timeout, transport=transport, trust_env=False) as client:
        try:
            response = client.get("/health")
            result.health_ok = response.is_success
            if not response.is_success:
                result.errors.append(f"GET /health returned {response.status_code}")
        except httpx.HTTPError as exc:
            result.errors.append(f"GET /health failed: {exc}")

        try:
            response = client.get("/v1/models")
            result.models_ok = response.is_success
            if response.is_success:
                result.models = _model_ids(response.json())
                result.model_visible = model in result.models
                if not result.model_visible:
                    result.errors.append(f"model '{model}' is not visible")
            else:
                result.errors.append(f"GET /v1/models returned {response.status_code}")
        except (httpx.HTTPError, ValueError) as exc:
            result.errors.append(f"GET /v1/models failed: {exc}")

        if not result.health_ok or not result.models_ok or not result.model_visible:
            return result

        started = time.perf_counter()
        try:
            response = client.post(
                "/v1/chat/completions",
                json={
                    "model": model,
                    "messages": [{"role": "user", "content": prompt}],
                    "max_tokens": 16,
                    "temperature": 0,
                    "stream": False,
                },
            )
            result.latency_ms = int((time.perf_counter() - started) * 1000)
            result.chat_ok = response.is_success
            if response.is_success:
                result.answer = _chat_completion_answer(response.json())
                if not result.answer:
                    result.chat_ok = False
                    result.errors.append("POST /v1/chat/completions returned an empty answer")
            else:
                result.errors.append(f"POST /v1/chat/completions returned {response.status_code}: {response.text}")
        except (httpx.HTTPError, ValueError) as exc:
            result.latency_ms = int((time.perf_counter() - started) * 1000)
            result.errors.append(f"POST /v1/chat/completions failed: {exc}")

    return result


def _chat_completion_answer(payload: dict) -> str:
    choices = payload.get("choices", [])
    if not isinstance(choices, list) or not choices:
        return ""
    first = choices[0]
    if not isinstance(first, dict):
        return ""
    message = first.get("message", {})
    if not isinstance(message, dict):
        return ""
    content = message.get("content", "")
    if isinstance(content, str) and content:
        return content
    # Reasoning models (e.g. gpt-oss) return reasoning_content instead of content
    reasoning = message.get("reasoning_content", "")
    return reasoning if isinstance(reasoning, str) else ""


@dataclass
class OmlxInstallResult:
    node: str
    plist_path: str
    label: str
    plist_content: str = ""
    commands: list[str] = field(default_factory=list)
    applied: bool = False
    service_label_verified: bool = False
    health_ok: bool = False
    errors: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return self.applied and self.service_label_verified and self.health_ok and not self.errors


def _omlx_binary_path(node: Node) -> str:
    if node.home_dir is None:
        msg = "node.home_dir is None — run pre-flight first or provide resolved home_dir"
        raise ValueError(msg)
    return f"{node.home_dir}/.local/bin/omlx"


def generate_launchd_plist(node: Node) -> str:
    """Generate a macOS launchd plist for a node-level oMLX daemon."""
    if node.runtime is None:
        msg = "Node has no runtime configured"
        raise ValueError(msg)
    if node.home_dir is None:
        msg = "node.home_dir is None — run pre-flight first or provide resolved home_dir"
        raise ValueError(msg)

    label = launchd_label_for_node(node)
    log_dir = f"{node.home_dir}/Library/Logs"
    stdout_log = f"{log_dir}/omlx-{node.runtime.port}.stdout.log"
    stderr_log = f"{log_dir}/omlx-{node.runtime.port}.stderr.log"

    program_arguments = [
        _omlx_binary_path(node),
        "serve",
        "--host",
        "0.0.0.0",
        "--port",
        str(node.runtime.port),
    ]
    if node.runtime.model_dir is not None:
        program_arguments.extend(["--model-dir", node.runtime.model_dir])

    program_arguments_xml = "\n".join(f"        <string>{arg}</string>" for arg in program_arguments)

    plist = textwrap.dedent(
        f"""\
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>Label</key>
    <string>{label}</string>
    <key>ProgramArguments</key>
    <array>
{program_arguments_xml}
    </array>
    <key>RunAtLoad</key>
    <true/>
    <key>KeepAlive</key>
    <true/>
    <key>StandardOutPath</key>
    <string>{stdout_log}</string>
    <key>StandardErrorPath</key>
    <string>{stderr_log}</string>
    <key>WorkingDirectory</key>
    <string>{node.home_dir}</string>
</dict>
</plist>
"""
    )
    return plist


def _install_commands(label: str, plist_path: str, port: int) -> list[str]:
    """The ordered command sequence for a safe launchd install."""
    return [
        "mkdir -p ~/Library/LaunchAgents ~/Library/Logs",
        f"launchctl bootout gui/$(id -u)/{label} 2>/dev/null || true",
        f"rm -f {plist_path}",
        (
            f"pkill -f '^.*omlx serve --host 0\\.0\\.0\\.0 --port {port}.*$' 2>/dev/null || true"
        ),
        f"launchctl bootstrap gui/$(id -u) {plist_path}",
        f"launchctl list {label} 2>/dev/null | grep -q {label}",
    ]


def run_omlx_install(node: Node, *, apply: bool = True, timeout: int = 60) -> OmlxInstallResult:
    """Generate plist and, if apply, install/update the launchd daemon on the node."""
    if node.runtime is None:
        msg = "Node has no runtime configured"
        raise ValueError(msg)
    runtime = node.runtime
    label = launchd_label_for_node(node)
    plist_path = f"~/Library/LaunchAgents/{label}.plist"
    result = OmlxInstallResult(
        node=f"{node.host}",
        plist_path=plist_path,
        label=label,
    )
    # Capture the generated plist content regardless of apply/dry-run for CLI output.
    try:
        result.plist_content = generate_launchd_plist(node)
    except (ValueError, AttributeError) as exc:
        result.errors.append(str(exc))
        return result

    result.commands = _install_commands(label, plist_path, runtime.port)
    if not apply:
        return result

    # Apply path: run each command via SSH in order, stop on hard failures.
    for cmd in result.commands[:-1]:  # all except the verify step
        run_res = ssh_run(node.user, node.host, cmd, timeout=timeout)
        if run_res.returncode != 0:
            result.errors.append(f"Command failed: {cmd}: {(run_res.stderr or '').strip()}")
            return result

    # Final verify step: confirm launchctl list shows the label.
    verify_cmd = result.commands[-1]
    verify_res = ssh_run(node.user, node.host, verify_cmd, timeout=timeout)
    result.service_label_verified = verify_res.returncode == 0
    if not result.service_label_verified:
        result.errors.append(f"Service label not found after install: {label}")

    # Health check: probe the daemon port
    try:
        health = check_omlx_health(
            f"http://{node.host}:{runtime.port}",
            timeout=10.0,
        )
        result.health_ok = health.health_ok and health.models_ok
        if not result.health_ok:
            result.errors.extend(health.errors)
    except Exception as exc:  # noqa: BLE001
        result.errors.append(f"Health check failed: {exc}")

    result.applied = True
    return result
