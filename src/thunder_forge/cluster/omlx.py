"""oMLX node-level runtime helpers."""

from __future__ import annotations

import shlex
import subprocess
import time
from collections.abc import Callable
from dataclasses import dataclass, field

import httpx

from thunder_forge.cluster.config import Node, RuntimeType
from thunder_forge.cluster.services import (
    LaunchdServiceSpec,
    daemon_sudoers_path,
    launch_daemon_path,
    system_launchd_bootstrap_script,
    system_launchd_commands,
    system_launchd_stop_wait_script,
    user_launchd_commands,
)
from thunder_forge.cluster.services import (
    generate_daemon_sudoers as generate_service_daemon_sudoers,
)
from thunder_forge.cluster.services import (
    generate_launchd_plist as generate_service_launchd_plist,
)
from thunder_forge.cluster.ssh import scp_content, ssh_run

LAUNCHD_LABEL_PREFIX = "com.thunder-forge.omlx"
DEFAULT_OMLX_TOOL_SPEC = "git+https://github.com/jundot/omlx.git"


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
    model_statuses: dict[str, dict[str, object]] = field(default_factory=dict)
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

    args = _omlx_program_arguments(node)
    return " ".join(shlex.quote(arg) for arg in args)


def _omlx_program_arguments(node: Node) -> list[str]:
    if node.runtime is None:
        msg = "Node has no runtime configured"
        raise ValueError(msg)
    if node.home_dir is None:
        msg = "node.home_dir is None — run pre-flight first or provide resolved home_dir"
        raise ValueError(msg)

    runtime = node.runtime
    args = [
        f"{node.home_dir}/.local/bin/omlx",
        "serve",
        "--host",
        runtime.bind_host,
        "--port",
        str(runtime.port),
    ]
    if runtime.model_dir is not None:
        args.extend(["--model-dir", runtime.model_dir])
    if runtime.base_path is not None:
        args.extend(["--base-path", runtime.base_path])
    if runtime.log_level is not None:
        args.extend(["--log-level", runtime.log_level])
    if runtime.max_model_memory is not None:
        args.extend(["--max-model-memory", runtime.max_model_memory])
    if runtime.max_process_memory is not None:
        args.extend(["--max-process-memory", runtime.max_process_memory])
    if runtime.max_concurrent_requests is not None:
        args.extend(["--max-concurrent-requests", str(runtime.max_concurrent_requests)])
    if runtime.paged_ssd_cache_dir is not None:
        args.extend(["--paged-ssd-cache-dir", runtime.paged_ssd_cache_dir])
    if runtime.paged_ssd_cache_max_size is not None:
        args.extend(["--paged-ssd-cache-max-size", runtime.paged_ssd_cache_max_size])
    if runtime.hot_cache_max_size is not None:
        args.extend(["--hot-cache-max-size", runtime.hot_cache_max_size])
    if runtime.no_cache:
        args.append("--no-cache")
    if runtime.mcp_config is not None:
        args.extend(["--mcp-config", runtime.mcp_config])
    if runtime.hf_endpoint is not None:
        args.extend(["--hf-endpoint", runtime.hf_endpoint])
    return args


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


def _model_statuses(payload: dict) -> dict[str, dict[str, object]]:
    models = payload.get("models", [])
    if not isinstance(models, list):
        return {}
    statuses: dict[str, dict[str, object]] = {}
    for item in models:
        if not isinstance(item, dict):
            continue
        model_id = item.get("id")
        if isinstance(model_id, str):
            statuses[model_id] = item
    return statuses


def check_omlx_health(
    base_url: str,
    *,
    timeout: float = 5.0,
    include_models: bool = True,
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

        if not include_models:
            return result

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
            if response.is_success:
                result.model_statuses = _model_statuses(response.json())
        except httpx.HTTPError:
            result.status_ok = False
        except ValueError:
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

        try:
            response = client.get("/v1/models/status")
            if response.is_success:
                model_status = _model_statuses(response.json()).get(model)
                if model_status is not None and model_status.get("is_loading") is True:
                    result.errors.append(f"model '{model}' is still loading")
                    return result
        except (httpx.HTTPError, ValueError):
            pass

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
    staging_plist_path: str = ""
    plist_content: str = ""
    commands: list[str] = field(default_factory=list)
    applied: bool = False
    service_label_verified: bool = False
    health_ok: bool = False
    errors: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return self.applied and self.service_label_verified and self.health_ok and not self.errors


@dataclass
class OmlxProcessResult:
    node: str
    command: str
    pid_path: str
    stdout_log: str
    stderr_log: str
    commands: list[str] = field(default_factory=list)
    pid: str = ""
    applied: bool = False
    health_ok: bool = False
    errors: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return self.applied and self.health_ok and not self.errors


@dataclass
class OmlxDaemonSetupResult:
    node: str
    label: str
    plist_path: str
    staging_plist_path: str
    sudoers_path: str
    script_path: str
    admin_user: str
    ssh_user: str
    via_su: bool = False
    script_content: str = ""
    commands: list[str] = field(default_factory=list)
    applied: bool = False
    sudoers_verified: bool = False
    service_label_verified: bool = False
    health_ok: bool = False
    errors: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return (
            self.applied
            and self.sudoers_verified
            and self.service_label_verified
            and self.health_ok
            and not self.errors
        )


@dataclass
class OmlxToolingResult:
    node: str
    uv_path: str
    omlx_path: str
    tool_spec: str
    command: str = ""
    applied: bool = False
    verified: bool = False
    errors: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return self.applied and self.verified and not self.errors


def _omlx_binary_path(node: Node) -> str:
    if node.home_dir is None:
        msg = "node.home_dir is None — run pre-flight first or provide resolved home_dir"
        raise ValueError(msg)
    return f"{node.home_dir}/.local/bin/omlx"


def _uv_binary_path(node: Node) -> str:
    if node.home_dir is None:
        msg = "node.home_dir is None — run pre-flight first or provide resolved home_dir"
        raise ValueError(msg)
    return f"{node.home_dir}/.local/bin/uv"


def _omlx_tooling_command(node: Node, *, tool_spec: str = DEFAULT_OMLX_TOOL_SPEC) -> str:
    if node.home_dir is None:
        msg = "node.home_dir is None — run pre-flight first or provide resolved home_dir"
        raise ValueError(msg)

    uv_binary = _uv_binary_path(node)
    omlx_binary = _omlx_binary_path(node)
    return f"""set -euo pipefail

NODE_HOME={shlex.quote(node.home_dir)}
UV_BINARY={shlex.quote(uv_binary)}
OMLX_BINARY={shlex.quote(omlx_binary)}
OMLX_TOOL_SPEC={shlex.quote(tool_spec)}

export HOME="$NODE_HOME"
export PATH="$NODE_HOME/.local/bin:/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin"

/bin/mkdir -p "$NODE_HOME/.local/bin"

if [[ ! -x "$UV_BINARY" ]]; then
    echo "uv: installing user-local uv at $UV_BINARY"
    /usr/bin/curl -LsSf https://astral.sh/uv/install.sh | /bin/sh
else
    echo "uv: already installed at $UV_BINARY"
fi

if [[ ! -x "$UV_BINARY" ]]; then
    echo "uv binary is missing or not executable: $UV_BINARY" >&2
    exit 1
fi

if [[ ! -x "$OMLX_BINARY" ]]; then
    echo "oMLX: installing $OMLX_TOOL_SPEC"
    "$UV_BINARY" tool install "$OMLX_TOOL_SPEC"
else
    echo "oMLX: already installed at $OMLX_BINARY"
fi

if [[ ! -x "$OMLX_BINARY" ]]; then
    echo "oMLX binary is missing or not executable after install: $OMLX_BINARY" >&2
    exit 1
fi

"$OMLX_BINARY" --help >/dev/null
echo "oMLX: ready at $OMLX_BINARY"
"""


def ensure_omlx_tooling(
    node: Node,
    *,
    apply: bool = True,
    timeout: int = 300,
    tool_spec: str = DEFAULT_OMLX_TOOL_SPEC,
    progress: Callable[[str], None] | None = None,
) -> OmlxToolingResult:
    """Ensure the node user has uv and the oMLX CLI before daemon setup."""
    if node.home_dir is None:
        msg = "node.home_dir is None — run pre-flight first or provide resolved home_dir"
        raise ValueError(msg)

    result = OmlxToolingResult(
        node=node.host,
        uv_path=_uv_binary_path(node),
        omlx_path=_omlx_binary_path(node),
        tool_spec=tool_spec,
        command=_omlx_tooling_command(node, tool_spec=tool_spec),
    )
    if not apply:
        return result

    if progress:
        progress(f"tooling: ensuring uv + oMLX for {node.user}@{node.host}")
    run_res = ssh_run(node.user, node.host, result.command, timeout=timeout, stream=True, shell=node.shell)
    result.applied = True
    if run_res.returncode != 0:
        err_output = ((run_res.stdout or "") + (run_res.stderr or "")).strip()
        suffix = f": {err_output}" if err_output else ""
        result.errors.append(f"oMLX tooling setup failed with exit code {run_res.returncode}{suffix}")
        return result

    result.verified = True
    if progress:
        progress(f"tooling: oMLX CLI ready at {result.omlx_path}")
    return result


def generate_launchd_plist(node: Node, *, system_daemon: bool = False) -> str:
    """Generate a macOS launchd plist for a node-level oMLX daemon."""
    if node.runtime is None:
        msg = "Node has no runtime configured"
        raise ValueError(msg)
    if node.home_dir is None:
        msg = "node.home_dir is None — run pre-flight first or provide resolved home_dir"
        raise ValueError(msg)

    log_dir = f"{node.home_dir}/Library/Logs"
    program_arguments = _omlx_program_arguments(node)
    environment = {}
    if system_daemon:
        environment = {
            "HOME": node.home_dir,
            "PATH": f"{node.home_dir}/.local/bin:/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin",
        }
    spec = LaunchdServiceSpec(
        name="omlx",
        label=launchd_label_for_node(node),
        program_arguments=program_arguments,
        working_directory=node.home_dir,
        stdout_log=f"{log_dir}/omlx-{node.runtime.port}.stdout.log",
        stderr_log=f"{log_dir}/omlx-{node.runtime.port}.stderr.log",
        environment=environment,
        user=node.user,
    )
    return generate_service_launchd_plist(spec, system_daemon=system_daemon)


def _install_commands(label: str, plist_path: str, port: int) -> list[str]:
    """The ordered command sequence for a safe launchd install."""
    process_pattern = f"^.*omlx serve --host 0\\.0\\.0\\.0 --port {port}.*$"
    return user_launchd_commands(label=label, plist_path=plist_path, process_pattern=process_pattern)


def _daemon_commands(label: str, staging_plist_path: str, plist_path: str, port: int) -> list[str]:
    """The ordered command sequence for a sudo-backed system LaunchDaemon."""
    process_pattern = f"^.*omlx serve --host 0\\.0\\.0\\.0 --port {port}.*$"
    return system_launchd_commands(
        label=label,
        staging_plist_path=staging_plist_path,
        plist_path=plist_path,
        process_pattern=process_pattern,
        setup_command="mkdir -p ~/.omlx/run ~/Library/Logs",
    )


def daemon_sudoers_path_for_node(node: Node) -> str:
    """Return the sudoers include path used for a node's oMLX daemon manager."""
    if node.runtime is None:
        msg = "Node has no runtime configured"
        raise ValueError(msg)
    return daemon_sudoers_path()


def generate_daemon_sudoers(node: Node) -> str:
    """Generate the narrow sudoers rule needed by the oMLX system daemon manager."""
    if node.runtime is None:
        msg = "Node has no runtime configured"
        raise ValueError(msg)
    if node.home_dir is None:
        msg = "node.home_dir is None — run pre-flight first or provide resolved home_dir"
        raise ValueError(msg)

    port = node.runtime.port
    label = launchd_label_for_node(node)
    plist_path = launch_daemon_path(label)
    staging_plist_path = f"{node.home_dir}/.omlx/run/{label}.plist"
    alias_prefix = f"TF_OMLX_{port}"
    return generate_service_daemon_sudoers(
        user=node.user,
        alias_prefix=alias_prefix,
        staging_plist_path=staging_plist_path,
        plist_path=plist_path,
        label=label,
    )


def _process_restart_commands(node: Node) -> tuple[OmlxProcessResult, int | None]:
    if node.runtime is None:
        msg = "Node has no runtime configured"
        raise ValueError(msg)
    if node.home_dir is None:
        msg = "node.home_dir is None — run pre-flight first or provide resolved home_dir"
        raise ValueError(msg)

    port = node.runtime.port
    label = launchd_label_for_node(node)
    command = build_omlx_serve_command(node)
    pid_path = f"{node.home_dir}/.omlx/run/omlx-{port}.pid"
    stdout_log = f"{node.home_dir}/Library/Logs/omlx-{port}.stdout.log"
    stderr_log = f"{node.home_dir}/Library/Logs/omlx-{port}.stderr.log"
    process_pattern = f"^.*omlx serve --host 0\\.0\\.0\\.0 --port {port}.*$"

    stop_command = " ; ".join(
        [
            f"launchctl bootout user/$(id -u)/{label} 2>/dev/null || true",
            f"launchctl bootout gui/$(id -u)/{label} 2>/dev/null || true",
            (
                f"if [ -f {shlex.quote(pid_path)} ]; then "
                f"existing_pid=$(cat {shlex.quote(pid_path)} 2>/dev/null || true); "
                'if [ -n "$existing_pid" ] && kill -0 "$existing_pid" 2>/dev/null; then '
                'kill "$existing_pid" 2>/dev/null || true; '
                "fi; "
                f"rm -f {shlex.quote(pid_path)}; "
                "fi"
            ),
            f"pkill -f {shlex.quote(process_pattern)} 2>/dev/null || true",
        ]
    )
    start_command = (
        f"mkdir -p {shlex.quote(node.home_dir + '/.omlx/run')} {shlex.quote(node.home_dir + '/Library/Logs')} && "
        f"nohup {command} > {shlex.quote(stdout_log)} 2> {shlex.quote(stderr_log)} < /dev/null & "
        f"echo $! > {shlex.quote(pid_path)} && "
        f"cat {shlex.quote(pid_path)}"
    )
    result = OmlxProcessResult(
        node=node.host,
        command=command,
        pid_path=pid_path,
        stdout_log=stdout_log,
        stderr_log=stderr_log,
        commands=[stop_command, start_command],
    )
    return result, port


def _wait_for_omlx_health(
    base_url: str,
    *,
    wait_seconds: int,
    require_models: bool = True,
    progress: Callable[[str], None] | None = None,
) -> OmlxHealthResult:
    def ready(result: OmlxHealthResult) -> bool:
        return result.health_ok and (result.models_ok if require_models else True)

    deadline = time.monotonic() + wait_seconds
    attempt = 1
    health_label = "oMLX" if require_models else "oMLX service"
    if progress:
        progress(f"health: waiting for {health_label} at {base_url}")
    last_result = check_omlx_health(base_url, timeout=10.0, include_models=require_models)
    while not ready(last_result) and time.monotonic() < deadline:
        if progress and attempt % 5 == 0:
            progress(f"health: still waiting for {health_label} at {base_url} ({attempt}s)")
        time.sleep(1)
        attempt += 1
        last_result = check_omlx_health(base_url, timeout=10.0, include_models=require_models)
    if progress and ready(last_result):
        progress(f"health: {health_label} ok ({base_url})")
    return last_result


def _build_omlx_launchd_result(node: Node) -> tuple[OmlxInstallResult, int | None]:
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
        return result, None

    result.commands = _install_commands(label, plist_path, runtime.port)
    return result, runtime.port


def _build_omlx_daemon_result(node: Node) -> tuple[OmlxInstallResult, int | None]:
    if node.runtime is None:
        msg = "Node has no runtime configured"
        raise ValueError(msg)
    if node.home_dir is None:
        msg = "node.home_dir is None — run pre-flight first or provide resolved home_dir"
        raise ValueError(msg)
    runtime = node.runtime
    label = launchd_label_for_node(node)
    plist_path = f"/Library/LaunchDaemons/{label}.plist"
    staging_plist_path = f"{node.home_dir}/.omlx/run/{label}.plist"
    result = OmlxInstallResult(
        node=f"{node.host}",
        plist_path=plist_path,
        staging_plist_path=staging_plist_path,
        label=label,
    )
    try:
        result.plist_content = generate_launchd_plist(node, system_daemon=True)
    except (ValueError, AttributeError) as exc:
        result.errors.append(str(exc))
        return result, None

    result.commands = _daemon_commands(label, staging_plist_path, plist_path, runtime.port)
    return result, runtime.port


def generate_daemon_setup_script(node: Node, *, sudoers_path: str | None = None) -> str:
    """Generate the node-side admin script that installs the system daemon and sudoers rule."""
    if node.runtime is None:
        msg = "Node has no runtime configured"
        raise ValueError(msg)
    if node.home_dir is None:
        msg = "node.home_dir is None — run pre-flight first or provide resolved home_dir"
        raise ValueError(msg)

    daemon_result, _ = _build_omlx_daemon_result(node)
    if daemon_result.errors:
        msg = "; ".join(daemon_result.errors)
        raise ValueError(msg)

    label = daemon_result.label
    sudoers_content = generate_daemon_sudoers(node).rstrip()
    sudoers_path = sudoers_path or daemon_sudoers_path_for_node(node)
    process_pattern = f"^.*omlx serve --host 0\\.0\\.0\\.0 --port {node.runtime.port}.*$"

    return f"""#!/bin/zsh
set -euo pipefail

NODE_USER={shlex.quote(node.user)}
NODE_HOME={shlex.quote(node.home_dir)}
OMLX_HOME="$NODE_HOME/.omlx"
OMLX_RUN_DIR="$OMLX_HOME/run"
OMLX_CACHE_DIR="$OMLX_HOME/cache"
OMLX_MODELS_DIR="$OMLX_HOME/models"
LOG_DIR="$NODE_HOME/Library/Logs"
OMLX_BINARY={shlex.quote(_omlx_binary_path(node))}
LABEL={shlex.quote(label)}
PLIST_PATH={shlex.quote(daemon_result.plist_path)}
STAGING_PLIST_PATH={shlex.quote(daemon_result.staging_plist_path)}
SUDOERS_PATH={shlex.quote(sudoers_path)}
PROCESS_PATTERN={shlex.quote(process_pattern)}

run_root() {{
    if [[ "$(/usr/bin/id -u)" -eq 0 ]]; then
        "$@"
    else
        /usr/bin/sudo "$@"
    fi
}}

if [[ "$(/usr/bin/uname -s)" != "Darwin" ]]; then
    echo "Thunder Forge oMLX daemon setup currently supports macOS nodes only" >&2
    exit 1
fi

if ! /usr/bin/id -u "$NODE_USER" >/dev/null 2>&1; then
    echo "Node user does not exist: $NODE_USER" >&2
    exit 1
fi

if [[ ! -x "$OMLX_BINARY" ]]; then
    echo "oMLX binary is missing or not executable: $OMLX_BINARY" >&2
    exit 1
fi

SUDOERS_DIR="$(/usr/bin/dirname "$SUDOERS_PATH")"
TMP_PLIST="$(/usr/bin/mktemp "/tmp/$LABEL.plist.XXXXXX")"
TMP_SUDOERS="$(/usr/bin/mktemp "/tmp/thunder-forge-sudoers.XXXXXX")"
cleanup() {{
    /bin/rm -f "$TMP_PLIST" "$TMP_SUDOERS"
}}
trap cleanup EXIT

/bin/cat > "$TMP_PLIST" <<'THUNDER_FORGE_PLIST'
{daemon_result.plist_content.rstrip()}
THUNDER_FORGE_PLIST

/bin/cat > "$TMP_SUDOERS" <<'THUNDER_FORGE_SUDOERS'
{sudoers_content}
THUNDER_FORGE_SUDOERS

run_root /bin/mkdir -p "$OMLX_RUN_DIR" "$OMLX_CACHE_DIR" "$OMLX_MODELS_DIR" "$LOG_DIR" "$SUDOERS_DIR"
run_root /usr/sbin/chown "$NODE_USER":staff "$OMLX_HOME" "$OMLX_RUN_DIR" "$OMLX_CACHE_DIR" "$OMLX_MODELS_DIR" "$LOG_DIR"
run_root /usr/sbin/chown -R "$NODE_USER":staff "$OMLX_RUN_DIR" "$OMLX_CACHE_DIR" "$LOG_DIR"
run_root /usr/sbin/visudo -cf "$TMP_SUDOERS"

NODE_UID="$(/usr/bin/id -u "$NODE_USER")"
echo "launchd: stopping $LABEL"
{system_launchd_stop_wait_script(uid_var="NODE_UID")}

echo "launchd: installing $LABEL"
run_root /usr/bin/install -o "$NODE_USER" -g staff -m 644 "$TMP_PLIST" "$STAGING_PLIST_PATH"
run_root /usr/bin/install -o root -g wheel -m 644 "$TMP_PLIST" "$PLIST_PATH"
run_root /usr/bin/install -o root -g wheel -m 440 "$TMP_SUDOERS" "$SUDOERS_PATH"

echo "launchd: starting $LABEL"
{system_launchd_bootstrap_script()}

echo "installed: $PLIST_PATH"
echo "sudoers: $SUDOERS_PATH"
echo "label: $LABEL"
"""


def _daemon_setup_run_command(
    script_path: str,
    *,
    admin_user: str,
    via_su: bool,
    label: str = "",
    host: str = "",
) -> str:
    quoted_script = shlex.quote(script_path)
    target = f"Thunder Forge oMLX daemon {label}" if label else "Thunder Forge oMLX daemon"
    host_tag = f"[{host}] " if host else ""
    sudo_prompt = f"[%h] password: user={admin_user or '%p'} reason=install {target}: "
    if not via_su:
        notice = f"{host_tag}password prompt: method=sudo user={admin_user or '%p'} reason=install {target}"
        sudo_command = f"/usr/bin/sudo -p {shlex.quote(sudo_prompt)} /bin/zsh {quoted_script}"
        return f"chmod 700 {quoted_script} && printf '%s\\n' {shlex.quote(notice)} && {sudo_command}"
    su_notice = f"{host_tag}password prompt: method=su user={admin_user} reason=bootstrap {target}"
    sudo_notice = f"{host_tag}password prompt: method=sudo user={admin_user} reason=install {target}"
    admin_shell = "; ".join(
        [
            f"printf '%s\\n' {shlex.quote(sudo_notice)}",
            f"/usr/bin/sudo -p {shlex.quote(sudo_prompt)} /bin/zsh {quoted_script}",
        ]
    )
    return (
        f"chmod 700 {quoted_script} && printf '%s\\n' {shlex.quote(su_notice)} && "
        f"/usr/bin/su - {shlex.quote(admin_user)} -c {shlex.quote(admin_shell)}"
    )


def _build_omlx_daemon_setup_result(
    node: Node,
    *,
    admin_user: str | None,
    via_su: bool,
    script_path: str | None,
) -> OmlxDaemonSetupResult:
    daemon_result, _ = _build_omlx_daemon_result(node)
    if node.runtime is None:
        msg = "Node has no runtime configured"
        raise ValueError(msg)

    resolved_admin_user = admin_user or node.user
    # Always SSH/SCP as the operator user (node.user); escalation (su/sudo) is handled on the remote.
    resolved_ssh_user = node.user
    resolved_script_path = script_path or f"/tmp/thunder-forge-setup-{daemon_result.label}.sh"
    sudoers_path = daemon_sudoers_path_for_node(node)
    setup_result = OmlxDaemonSetupResult(
        node=node.host,
        label=daemon_result.label,
        plist_path=daemon_result.plist_path,
        staging_plist_path=daemon_result.staging_plist_path,
        sudoers_path=sudoers_path,
        script_path=resolved_script_path,
        admin_user=resolved_admin_user,
        ssh_user=resolved_ssh_user,
        via_su=via_su,
    )
    if daemon_result.errors:
        setup_result.errors.extend(daemon_result.errors)
        return setup_result

    setup_result.script_content = generate_daemon_setup_script(node, sudoers_path=sudoers_path)
    prompt_user = resolved_admin_user if via_su else node.user
    run_command = _daemon_setup_run_command(
        resolved_script_path,
        admin_user=prompt_user,
        via_su=via_su,
        label=daemon_result.label,
        host=node.host,
    )
    setup_result.commands = [
        f"copy setup script to {resolved_ssh_user}@{node.host}:{resolved_script_path}",
        f"ssh -tt {resolved_ssh_user}@{node.host} {shlex.quote(run_command)}",
        f"ssh {node.user}@{node.host} /usr/bin/sudo -n /bin/launchctl print system/{daemon_result.label}",
    ]
    return setup_result


def _apply_omlx_launchd_update(
    node: Node,
    result: OmlxInstallResult,
    *,
    timeout: int,
    operation: str,
) -> OmlxInstallResult:
    if not result.commands:
        result.errors.append(f"No launchd commands generated for {operation}")
        return result

    # Apply path: prepare dirs, unload any old daemon, remove stale plist, copy the generated plist, then bootstrap.
    setup_commands = result.commands[:3]
    start_commands = result.commands[3:-1]
    verify_cmd = result.commands[-1]

    for cmd in setup_commands:
        run_res = ssh_run(node.user, node.host, cmd, timeout=timeout)
        if run_res.returncode != 0:
            result.errors.append(f"Command failed: {cmd}: {(run_res.stderr or '').strip()}")
            return result

    copy_res = scp_content(node.user, node.host, result.plist_content, result.plist_path, shell=node.shell)
    if copy_res.returncode != 0:
        result.errors.append(f"Failed to write launchd plist: {(copy_res.stderr or '').strip()}")
        return result

    for cmd in start_commands:
        run_res = ssh_run(node.user, node.host, cmd, timeout=timeout)
        if run_res.returncode != 0:
            result.errors.append(f"Command failed: {cmd}: {(run_res.stderr or '').strip()}")
            return result

    # Final verify step: confirm launchctl list shows the label.
    verify_res = ssh_run(node.user, node.host, verify_cmd, timeout=timeout)
    result.service_label_verified = verify_res.returncode == 0
    if not result.service_label_verified:
        result.errors.append(f"Service label not found after {operation}: {result.label}")

    # Health check: probe the daemon port.
    if node.runtime is not None:
        try:
            health = _wait_for_omlx_health(
                f"http://{node.host}:{node.runtime.port}",
                wait_seconds=min(max(timeout, 10), 60),
                require_models=False,
            )
            result.health_ok = health.health_ok
            if not result.health_ok:
                result.errors.extend(health.errors)
        except Exception as exc:  # noqa: BLE001
            result.errors.append(f"Health check failed: {exc}")

    result.applied = True
    return result


def _apply_omlx_daemon_update(
    node: Node,
    result: OmlxInstallResult,
    *,
    timeout: int,
) -> OmlxInstallResult:
    if not result.commands:
        result.errors.append("No daemon commands generated for restart")
        return result

    setup_command = result.commands[0]
    run_res = ssh_run(node.user, node.host, setup_command, timeout=timeout)
    if run_res.returncode != 0:
        result.errors.append(f"Command failed: {setup_command}: {(run_res.stderr or '').strip()}")
        return result

    copy_res = scp_content(node.user, node.host, result.plist_content, result.staging_plist_path, shell=node.shell)
    if copy_res.returncode != 0:
        result.errors.append(f"Failed to stage launchd plist: {(copy_res.stderr or '').strip()}")
        return result

    for cmd in result.commands[1:-1]:
        run_res = ssh_run(node.user, node.host, cmd, timeout=timeout)
        if run_res.returncode != 0:
            result.errors.append(f"Command failed: {cmd}: {(run_res.stderr or '').strip()}")
            return result

    verify_cmd = result.commands[-1]
    verify_res = ssh_run(node.user, node.host, verify_cmd, timeout=timeout)
    result.service_label_verified = verify_res.returncode == 0
    if not result.service_label_verified:
        result.errors.append(f"Service label not found after daemon restart: {result.label}")

    if node.runtime is not None:
        try:
            health = _wait_for_omlx_health(
                f"http://{node.host}:{node.runtime.port}",
                wait_seconds=min(max(timeout, 10), 60),
                require_models=False,
            )
            result.health_ok = health.health_ok
            if not result.health_ok:
                result.errors.extend(health.errors)
        except Exception as exc:  # noqa: BLE001
            result.errors.append(f"Health check failed: {exc}")

    result.applied = True
    return result


def run_omlx_install(node: Node, *, apply: bool = True, timeout: int = 60) -> OmlxInstallResult:
    """Generate plist and, if apply, install/update the launchd daemon on the node."""
    result, _ = _build_omlx_launchd_result(node)
    if not apply:
        return result
    return _apply_omlx_launchd_update(node, result, timeout=timeout, operation="install")


def run_omlx_runtime_restart(node: Node, *, apply: bool = True, timeout: int = 60) -> OmlxInstallResult:
    """Restart/update the node-level oMLX launchd daemon on the node."""
    result, _ = _build_omlx_launchd_result(node)
    if not apply:
        return result
    return _apply_omlx_launchd_update(node, result, timeout=timeout, operation="restart")


def run_omlx_daemon_restart(node: Node, *, apply: bool = True, timeout: int = 60) -> OmlxInstallResult:
    """Install/update and restart oMLX as a sudo-backed system LaunchDaemon."""
    result, _ = _build_omlx_daemon_result(node)
    if not apply:
        return result
    return _apply_omlx_daemon_update(node, result, timeout=timeout)


def run_omlx_daemon_setup(
    node: Node,
    *,
    admin_user: str | None = None,
    via_su: bool = False,
    script_path: str | None = None,
    apply: bool = True,
    timeout: int = 300,
    progress: Callable[[str], None] | None = None,
) -> OmlxDaemonSetupResult:
    """Install the sudoers rule and system LaunchDaemon using an admin-side setup script."""
    result = _build_omlx_daemon_setup_result(
        node,
        admin_user=admin_user,
        via_su=via_su,
        script_path=script_path,
    )
    if not apply:
        return result
    if via_su and not admin_user:
        result.errors.append("--via-su requires --admin-user")
        return result

    copy_res = scp_content(result.ssh_user, node.host, result.script_content, result.script_path, shell=node.shell)
    if copy_res.returncode != 0:
        err = (copy_res.stderr or "").strip()
        if "Permission denied" in err or "publickey" in err or "authentication" in err.lower():
            hint = (
                f"SSH key auth failed for {result.ssh_user}@{node.host}. Run: ssh-copy-id {result.ssh_user}@{node.host}"
            )
            result.errors.append(hint)
        else:
            result.errors.append(f"Failed to copy setup script to {result.ssh_user}@{node.host}: {err}")
        return result

    prompt_user = result.admin_user if result.via_su else node.user
    run_command = _daemon_setup_run_command(
        result.script_path,
        admin_user=prompt_user,
        via_su=result.via_su,
        label=result.label,
        host=node.host,
    )
    run_res = ssh_run(
        result.ssh_user,
        node.host,
        run_command,
        timeout=timeout,
        stream=True,
        shell=node.shell,
        tty=True,
    )
    result.applied = True
    if run_res.returncode != 0:
        err_output = (run_res.stdout or "") + (run_res.stderr or "")
        if "is not allowed to execute" in err_output or "not in the sudoers file" in err_output:
            node_label = node.name if hasattr(node, "name") else ""
            node_ref = node_label or "<node>"
            escalation = (
                f"su: {result.admin_user}'s password (set nodes.{node_ref}.admin_user in tfconfig.yaml)"
                if not result.via_su
                else f"su to {result.admin_user}: ensure {node.user} is in the wheel or admin group"
            )
            result.errors.append(
                f"{node.user} cannot sudo on {node.host}. "
                f"Set nodes.{node_ref}.admin_user in tfconfig.yaml, then run make bootstrap {node_ref} "
                f"({escalation})"
            )
        elif "su: Sorry" in err_output:
            result.errors.append(
                f"{node.user} is not in the wheel/admin group on {node.host} — su to {result.admin_user} denied. "
                f"Add {node.user} to the admin group on {node.host}: "
                f"sudo dseditgroup -o edit -a {node.user} -t user admin"
            )
        else:
            result.errors.append(f"Setup script failed with exit code {run_res.returncode}")
        return result

    verify_cmd = f"/usr/bin/sudo -n /bin/launchctl print system/{result.label} >/dev/null"
    verify_res = ssh_run(node.user, node.host, verify_cmd, timeout=timeout, shell=node.shell)
    result.sudoers_verified = verify_res.returncode == 0
    result.service_label_verified = verify_res.returncode == 0
    if verify_res.returncode != 0:
        result.errors.append(f"Daemon sudoers verification failed: {(verify_res.stderr or '').strip()}")

    if node.runtime is not None:
        try:
            health = _wait_for_omlx_health(
                f"http://{node.host}:{node.runtime.port}",
                wait_seconds=min(max(timeout, 10), 60),
                require_models=False,
                progress=progress,
            )
            result.health_ok = health.health_ok
            if not result.health_ok:
                result.errors.extend(health.errors)
        except Exception as exc:  # noqa: BLE001
            result.errors.append(f"Health check failed: {exc}")

    return result


def run_omlx_process_restart(node: Node, *, apply: bool = True, timeout: int = 60) -> OmlxProcessResult:
    """Restart a node-level oMLX process without launchd, sudo, or a GUI session."""
    result, port = _process_restart_commands(node)
    if not apply:
        return result
    if port is None:
        result.errors.append("No runtime port available")
        return result

    for index, cmd in enumerate(result.commands):
        run_res = ssh_run(node.user, node.host, cmd, timeout=timeout)
        if run_res.returncode != 0:
            result.errors.append(f"Command failed: {cmd}: {(run_res.stderr or '').strip()}")
            return result
        if index == 1:
            result.pid = run_res.stdout.strip()

    try:
        health = _wait_for_omlx_health(
            f"http://{node.host}:{port}",
            wait_seconds=min(max(timeout, 10), 60),
            require_models=False,
        )
        result.health_ok = health.health_ok
        if not result.health_ok:
            result.errors.extend(health.errors)
    except Exception as exc:  # noqa: BLE001
        result.errors.append(f"Health check failed: {exc}")

    result.applied = True
    return result
