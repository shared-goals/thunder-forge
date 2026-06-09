"""Olla router smoke helpers for TF v2 MVP."""

from __future__ import annotations

import platform
import re
import subprocess
import time
from collections.abc import Callable, Iterator, Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import httpx

from thunder_forge.cluster.config import ClusterConfig
from thunder_forge.cluster.ports import DEFAULT_OLLA_PORT, local_base_url, resolve_port
from thunder_forge.cluster.services import (
    LaunchdServiceResult,
    LaunchdServiceSpec,
    generate_launchd_plist,
    generate_systemd_unit,
    launch_agent_path,
    launch_daemon_path,
    run_local_commands,
    system_launchd_commands,
    systemd_commands,
    systemd_service_name,
    systemd_unit_path,
    user_launchd_commands,
    write_local_file,
)

OLLA_OMLX_PREFIX = "/olla/omlx/v1"
OLLA_DEFAULT_PORT = DEFAULT_OLLA_PORT
OLLA_HEALTH_RETRIES = 30
OLLA_HEALTH_RETRY_INTERVAL = 1.0
ENDPOINT_HEALTHY_VALUES = {"healthy", "routable", "ready", "online", "up", "ok"}
ENDPOINT_HEALTH_FIELDS = ("status", "state", "health")
ENDPOINT_BOOLEAN_HEALTH_FIELDS = ("healthy", "routable", "ready", "available")
ENDPOINT_NAME_FIELDS = ("name", "id", "endpoint")


def _value_indicates_endpoint_health(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        return value.strip().lower() in ENDPOINT_HEALTHY_VALUES
    return False


def _record_has_healthy_endpoint_indicator(record: Mapping[str, Any]) -> bool:
    for key in (*ENDPOINT_HEALTH_FIELDS, *ENDPOINT_BOOLEAN_HEALTH_FIELDS):
        if key in record and _value_indicates_endpoint_health(record[key]):
            return True

    return any(_record_has_healthy_endpoint_indicator(value) for value in record.values() if isinstance(value, Mapping))


def _endpoint_records_from_container(container: Any) -> Iterator[Mapping[str, Any]]:
    if isinstance(container, list):
        for item in container:
            yield from _endpoint_records_from_container(item)
        return

    if not isinstance(container, Mapping):
        return

    if any(field in container for field in ENDPOINT_NAME_FIELDS):
        yield container
        return

    for name, value in container.items():
        if isinstance(value, Mapping):
            record = dict(value)
            record.setdefault("name", str(name))
            yield record
        else:
            yield {"name": str(name), "status": value}


def _iter_endpoint_records(payload: Any) -> Iterator[Mapping[str, Any]]:
    if isinstance(payload, list):
        for item in payload:
            yield from _iter_endpoint_records(item)
        return

    if not isinstance(payload, Mapping):
        return

    if "endpoints" in payload:
        yield from _endpoint_records_from_container(payload["endpoints"])
        return

    for name, value in payload.items():
        if isinstance(value, Mapping):
            record = dict(value)
            record.setdefault("name", str(name))
            if _record_has_healthy_endpoint_indicator(record):
                yield record
            yield from _iter_endpoint_records(value)


def _endpoint_record_name(record: Mapping[str, Any]) -> str:
    for key in ENDPOINT_NAME_FIELDS:
        value = record.get(key)
        if isinstance(value, str):
            return value
    return ""


def _payload_has_generic_endpoint_health(payload: Any) -> bool:
    if isinstance(payload, list):
        return any(_payload_has_generic_endpoint_health(item) for item in payload)

    if isinstance(payload, Mapping):
        if _record_has_healthy_endpoint_indicator(payload):
            return True
        return any(_payload_has_generic_endpoint_health(value) for value in payload.values())

    return False


def _text_has_generic_endpoint_health(text: str) -> bool:
    tokens = set(re.split(r"[^a-z0-9_-]+", text.lower()))
    return bool(tokens & {"healthy", "routable"})


def _endpoint_status_indicates_health(
    response: httpx.Response,
    *,
    expected_endpoint: str | None,
) -> bool:
    try:
        payload = response.json()
    except ValueError:
        if expected_endpoint is not None:
            return expected_endpoint in response.text and _text_has_generic_endpoint_health(response.text)
        return _text_has_generic_endpoint_health(response.text)

    if expected_endpoint is None:
        return _payload_has_generic_endpoint_health(payload)

    return any(
        _endpoint_record_name(record) == expected_endpoint and _record_has_healthy_endpoint_indicator(record)
        for record in _iter_endpoint_records(payload)
    )


OLLA_LAUNCHD_LABEL_PREFIX = "com.thunder-forge.olla"


def olla_launchd_label(*, port: int = OLLA_DEFAULT_PORT) -> str:
    return f"{OLLA_LAUNCHD_LABEL_PREFIX}-{port}"


@dataclass
class OllaSmokeResult:
    base_url: str
    model: str
    alias: str
    health_ok: bool = False
    endpoints_ok: bool = False
    models_ok: bool = False
    chat_ok: bool = False
    alias_ok: bool = False
    session_ok: bool = False
    root_v1_absent: bool = False
    latency_ms: int = 0
    olla_endpoint: str = ""
    alias_endpoint: str = ""
    errors: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return (
            self.health_ok
            and self.endpoints_ok
            and self.models_ok
            and self.chat_ok
            and self.alias_ok
            and self.session_ok
            and self.root_v1_absent
        )


def smoke_olla_router(
    *,
    base_url: str,
    model: str,
    alias: str,
    expected_endpoint: str | None = None,
    prompt: str = "Reply with one short word: pong.",
    timeout: float = 30.0,
    transport: httpx.BaseTransport | None = None,
) -> OllaSmokeResult:
    """Run a black-box smoke against a running Olla router."""
    normalized_base_url = base_url.rstrip("/")
    result = OllaSmokeResult(base_url=normalized_base_url, model=model, alias=alias)
    fixed_session_id = "tf-olla-smoke-session"

    with httpx.Client(base_url=normalized_base_url, timeout=timeout, transport=transport, trust_env=False) as client:
        try:
            response = client.get("/internal/health")
            result.health_ok = response.is_success
            if not result.health_ok:
                result.errors.append(f"GET /internal/health returned {response.status_code}: {response.text}")
        except httpx.HTTPError as exc:
            result.errors.append(f"GET /internal/health failed: {exc}")

        try:
            response = client.get("/internal/status/endpoints")
            result.endpoints_ok = response.is_success and _endpoint_status_indicates_health(
                response,
                expected_endpoint=expected_endpoint,
            )
            if not result.endpoints_ok:
                expected_message = f" for expected endpoint '{expected_endpoint}'" if expected_endpoint else ""
                result.errors.append(
                    f"GET /internal/status/endpoints unexpected body{expected_message}: {response.text}"
                )
        except httpx.HTTPError as exc:
            result.errors.append(f"GET /internal/status/endpoints failed: {exc}")

        try:
            response = client.get(f"{OLLA_OMLX_PREFIX}/models")
            result.models_ok = response.is_success and model in response.text
            if not result.models_ok:
                result.errors.append(f"GET {OLLA_OMLX_PREFIX}/models missing model '{model}'")
        except httpx.HTTPError as exc:
            result.errors.append(f"GET {OLLA_OMLX_PREFIX}/models failed: {exc}")

        auth_headers = {"X-Olla-Session-ID": fixed_session_id}
        started = time.perf_counter()
        backend_chat_payload = {
            "model": model,
            "messages": [{"role": "user", "content": prompt}],
            "max_tokens": 16,
            "temperature": 0,
            "stream": False,
        }
        try:
            response = client.post(
                f"{OLLA_OMLX_PREFIX}/chat/completions",
                headers=auth_headers,
                json=backend_chat_payload,
            )
            result.latency_ms = int((time.perf_counter() - started) * 1000)
            result.chat_ok = response.is_success
            result.olla_endpoint = response.headers.get("X-Olla-Endpoint", "")
            if not result.chat_ok:
                result.errors.append(f"POST {OLLA_OMLX_PREFIX}/chat/completions returned {response.status_code}")
            elif not result.olla_endpoint:
                result.errors.append("backend-model response did not include X-Olla-Endpoint for session check")
            else:
                repeat = client.post(
                    f"{OLLA_OMLX_PREFIX}/chat/completions",
                    headers=auth_headers,
                    json=backend_chat_payload,
                )
                repeat_endpoint = repeat.headers.get("X-Olla-Endpoint", "")
                result.session_ok = repeat.is_success and repeat_endpoint == result.olla_endpoint
                if not repeat.is_success:
                    result.errors.append(
                        f"repeat POST {OLLA_OMLX_PREFIX}/chat/completions returned {repeat.status_code}"
                    )
                elif repeat_endpoint != result.olla_endpoint:
                    result.errors.append("same session routed to different endpoints")
        except httpx.HTTPError as exc:
            result.latency_ms = int((time.perf_counter() - started) * 1000)
            result.errors.append(f"POST {OLLA_OMLX_PREFIX}/chat/completions failed: {exc}")

        try:
            response = client.post(
                f"{OLLA_OMLX_PREFIX}/chat/completions",
                headers=auth_headers,
                json={
                    "model": alias,
                    "messages": [{"role": "user", "content": prompt}],
                    "max_tokens": 16,
                    "temperature": 0,
                    "stream": False,
                },
            )
            result.alias_endpoint = response.headers.get("X-Olla-Endpoint", "")
            result.alias_ok = response.is_success and bool(result.alias_endpoint)
            if not result.alias_ok:
                if not response.is_success:
                    result.errors.append(f"alias POST returned {response.status_code}: {response.text}")
                else:
                    result.errors.append(f"alias request did not return an Olla endpoint for '{alias}'")
        except httpx.HTTPError as exc:
            result.errors.append(f"alias POST failed: {exc}")

        try:
            response = client.get("/v1/models")
            result.root_v1_absent = response.status_code == 404
            if not result.root_v1_absent:
                result.errors.append(f"root /v1/models returned {response.status_code}, expected 404 for raw Olla")
        except httpx.HTTPError as exc:
            result.errors.append(f"GET /v1/models failed: {exc}")

    return result


@dataclass
class OllaDevSmokeResult:
    """Result of a full dev-smoke: generate config, spawn Olla, smoke, teardown."""

    config_generated: bool = False
    config_path: str = ""
    olla_started: bool = False
    olla_healthy: bool = False
    smoke_result: OllaSmokeResult | None = None
    olla_terminated: bool = False
    errors: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return (
            self.config_generated
            and self.olla_started
            and self.olla_healthy
            and self.smoke_result is not None
            and self.smoke_result.ok
            and self.olla_terminated
        )


def _generate_olla_config_to_file(*, port: int | None = None) -> tuple[Path, ClusterConfig] | None:
    """Generate olla-config.yaml from TF desired state. Returns (path, config) or None on failure."""
    from thunder_forge.cluster.config import (
        default_cluster_config_path,
        find_repo_root,
        generate_olla_config,
        generated_olla_config_path,
        load_cluster_config,
    )

    repo_root = find_repo_root()
    cluster_config_path = default_cluster_config_path(repo_root)
    if not cluster_config_path.exists():
        return None
    config = load_cluster_config(cluster_config_path)
    content = generate_olla_config(config, port=port)
    config_path = generated_olla_config_path(repo_root)
    config_path.parent.mkdir(parents=True, exist_ok=True)
    config_path.write_text(content)
    return config_path, config


def _wait_olla_healthy(
    base_url: str,
    *,
    retries: int = OLLA_HEALTH_RETRIES,
    interval: float = OLLA_HEALTH_RETRY_INTERVAL,
    timeout: float = 5.0,
    progress: Callable[[str], None] | None = None,
) -> bool:
    """Poll Olla /internal/health until it responds or retries are exhausted."""
    for attempt in range(1, retries + 1):
        try:
            with httpx.Client(base_url=base_url, timeout=timeout, trust_env=False) as client:
                response = client.get("/internal/health")
                if response.is_success:
                    return True
        except httpx.HTTPError:
            pass
        if progress and (attempt == 1 or attempt % 5 == 0):
            progress(f"health: waiting for Olla at {base_url} ({attempt}/{retries})")
        time.sleep(interval)
    return False


def _olla_service_spec(
    *,
    repo_root: Path,
    binary: Path,
    config_path: Path,
    port: int,
    user: str,
    working_directory: Path | None = None,
) -> LaunchdServiceSpec:
    log_dir = repo_root / "logs"
    user_home = _service_home_for_user(user)
    binary_path = binary.expanduser()
    config_file = config_path.expanduser()
    if not binary_path.is_absolute():
        binary_path = repo_root / binary_path
    if not config_file.is_absolute():
        config_file = repo_root / config_file
    process_pattern = f"^.*olla -config {config_file}.*$"
    return LaunchdServiceSpec(
        name="olla",
        label=olla_launchd_label(port=port),
        program_arguments=[str(binary_path), "-config", str(config_file)],
        working_directory=str(working_directory or repo_root),
        stdout_log=str(log_dir / f"olla-{port}.stdout.log"),
        stderr_log=str(log_dir / f"olla-{port}.stderr.log"),
        environment={
            "HOME": str(user_home),
            "PATH": f"{user_home}/.local/bin:/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin",
        },
        process_pattern=process_pattern,
        user=user,
    )


def _service_home_for_user(user: str) -> Path:
    try:
        import pwd

        return Path(pwd.getpwnam(user).pw_dir)
    except (ImportError, KeyError):
        return Path.home()


def _build_olla_launchd_result(
    *,
    repo_root: Path,
    binary: Path,
    config_path: Path,
    port: int,
    user: str,
    manager: str,
    interactive_sudo: bool = False,
    admin_user: str = "",
    working_directory: Path | None = None,
) -> tuple[LaunchdServiceResult, str]:
    spec = _olla_service_spec(
        repo_root=repo_root,
        binary=binary,
        config_path=config_path,
        port=port,
        user=user,
        working_directory=working_directory,
    )
    system_daemon = manager == "daemon"
    plist_path = launch_daemon_path(spec.label) if system_daemon else launch_agent_path(spec.label)
    staging_plist_path = str(repo_root / ".tmp" / "run" / f"{spec.label}.plist") if system_daemon else ""
    result = LaunchdServiceResult(
        service=spec.name,
        label=spec.label,
        plist_path=plist_path,
        staging_plist_path=staging_plist_path,
        process_pattern=spec.process_pattern,
    )
    result.plist_content = generate_launchd_plist(spec, system_daemon=system_daemon)
    log_dir = sh_quote(str(repo_root / "logs"))
    if system_daemon:
        result.commands = system_launchd_commands(
            label=spec.label,
            staging_plist_path=staging_plist_path,
            plist_path=plist_path,
            process_pattern=spec.process_pattern,
            setup_command=f"mkdir -p {sh_quote(str(repo_root / '.tmp' / 'run'))} {log_dir}",
            interactive_sudo=interactive_sudo,
            admin_user=admin_user,
        )
    else:
        result.commands = user_launchd_commands(
            label=spec.label,
            plist_path=plist_path,
            process_pattern=spec.process_pattern,
            setup_command=f"mkdir -p ~/Library/LaunchAgents {log_dir}",
            domain="gui/$(id -u)",
            kickstart=True,
        )
    return result, f"http://127.0.0.1:{port}"


def _build_olla_systemd_result(
    *,
    repo_root: Path,
    binary: Path,
    config_path: Path,
    port: int,
    user: str,
    interactive_sudo: bool = False,
    admin_user: str = "",
    working_directory: Path | None = None,
) -> tuple[LaunchdServiceResult, str]:
    spec = _olla_service_spec(
        repo_root=repo_root,
        binary=binary,
        config_path=config_path,
        port=port,
        user=user,
        working_directory=working_directory,
    )
    service_name = systemd_service_name(spec.label)
    unit_path = systemd_unit_path(service_name)
    staging_unit_path = str(repo_root / ".tmp" / "run" / service_name)
    result = LaunchdServiceResult(
        service=spec.name,
        label=service_name,
        plist_path=unit_path,
        staging_plist_path=staging_unit_path,
        process_pattern=spec.process_pattern,
    )
    result.plist_content = generate_systemd_unit(spec)
    log_dir = sh_quote(str(repo_root / "logs"))
    result.commands = systemd_commands(
        service_name=service_name,
        staging_unit_path=staging_unit_path,
        unit_path=unit_path,
        setup_command=f"mkdir -p {sh_quote(str(repo_root / '.tmp' / 'run'))} {log_dir}",
        interactive_sudo=interactive_sudo,
        admin_user=admin_user,
    )
    return result, f"http://127.0.0.1:{port}"


def sh_quote(value: str) -> str:
    import shlex

    return shlex.quote(value)


def _apply_local_olla_service(
    result: LaunchdServiceResult,
    *,
    base_url: str,
    timeout: int,
    stream: bool = False,
) -> LaunchdServiceResult:
    if not result.commands:
        result.errors.append("No service commands generated")
        return result
    system_daemon = bool(result.staging_plist_path)
    setup_commands = result.commands[:1] if system_daemon else result.commands[:3]
    start_commands = result.commands[1:-1] if system_daemon else result.commands[3:-1]
    verify_command = result.commands[-1]
    ok, error = run_local_commands(setup_commands, timeout=timeout, stream=stream)
    if not ok:
        result.errors.append(error)
        return result

    target_plist_path = result.staging_plist_path or result.plist_path
    write_local_file(target_plist_path, result.plist_content)

    ok, error = run_local_commands(start_commands, timeout=timeout, stream=stream)
    if not ok:
        result.errors.append(error)
        return result

    ok, error = run_local_commands([verify_command], timeout=timeout, stream=stream)
    result.service_label_verified = ok
    if not ok:
        result.errors.append(error)

    result.health_ok = _wait_olla_healthy(base_url, retries=30, interval=1.0, timeout=5.0)
    if not result.health_ok:
        result.errors.append(f"Olla health check failed at {base_url}")
    result.applied = True
    return result


def run_olla_service_restart(
    *,
    repo_root: Path,
    binary: Path = Path(".tmp/olla-bin/olla"),
    config_path: Path = Path("configs/olla-config.yaml"),
    port: int | None = None,
    user: str | None = None,
    manager: str = "launchd",
    apply: bool = True,
    timeout: int = 60,
    interactive_sudo: bool = False,
    admin_user: str = "",
    working_directory: Path | None = None,
) -> LaunchdServiceResult:
    """Install/update and restart Olla as a frontend service."""
    normalized_manager = manager.lower()
    if normalized_manager not in {"launchd", "daemon", "systemd"}:
        msg = "Olla service manager must be 'launchd', 'daemon', or 'systemd'"
        raise ValueError(msg)
    host_os = platform.system()
    if normalized_manager == "systemd" and host_os != "Linux":
        msg = "Olla service manager 'systemd' is only supported on Linux hosts"
        raise ValueError(msg)
    effective_manager = "systemd" if normalized_manager == "daemon" and host_os != "Darwin" else normalized_manager
    resolved_port = resolve_port(port, default=OLLA_DEFAULT_PORT)
    if effective_manager == "systemd":
        result, base_url = _build_olla_systemd_result(
            repo_root=repo_root,
            binary=binary,
            config_path=config_path,
            port=resolved_port,
            user=user or "shag",
            interactive_sudo=interactive_sudo,
            admin_user=admin_user,
            working_directory=working_directory,
        )
    else:
        result, base_url = _build_olla_launchd_result(
            repo_root=repo_root,
            binary=binary,
            config_path=config_path,
            port=resolved_port,
            user=user or "shag",
            manager=effective_manager,
            interactive_sudo=interactive_sudo,
            admin_user=admin_user,
            working_directory=working_directory,
        )
    if not apply:
        return result
    return _apply_local_olla_service(result, base_url=base_url, timeout=timeout, stream=interactive_sudo)


def dev_smoke_olla(
    *,
    binary: str,
    model: str,
    alias: str,
    expected_endpoint: str | None = None,
    prompt: str = "Reply with one short word: pong.",
    smoke_timeout: float = 30.0,
    port: int | None = None,
    health_retries: int = OLLA_HEALTH_RETRIES,
    health_interval: float = OLLA_HEALTH_RETRY_INTERVAL,
) -> OllaDevSmokeResult:
    """Generate Olla config, spawn Olla, wait for healthy, smoke, teardown.

    This is the single-command dev-smoke orchestration that replaces manual
    /tmp-based smoke workflows.
    """
    result = OllaDevSmokeResult()

    # Step 1: generate config
    generated = _generate_olla_config_to_file(port=port)
    if generated is None:
        result.errors.append("Failed to generate olla-config.yaml: TF cluster config not found")
        return result
    config_path, config = generated
    result.config_generated = True
    result.config_path = str(config_path)

    # Step 2: spawn Olla
    resolved_port = resolve_port(port, default=config.services.olla_port)
    base_url = local_base_url(resolved_port)
    proc: subprocess.Popen | None = None
    try:
        proc = subprocess.Popen(
            [binary, "-config", str(config_path)],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
        result.olla_started = True

        # Step 3: wait for healthy
        result.olla_healthy = _wait_olla_healthy(
            base_url,
            retries=health_retries,
            interval=health_interval,
        )
        if not result.olla_healthy:
            result.errors.append("Olla did not become healthy within retry window")
            return result

        # Step 4: run smoke
        result.smoke_result = smoke_olla_router(
            base_url=base_url,
            model=model,
            alias=alias,
            expected_endpoint=expected_endpoint,
            prompt=prompt,
            timeout=smoke_timeout,
        )

    finally:
        # Step 5: teardown
        if proc is not None and proc.poll() is None:
            proc.terminate()
            try:
                proc.wait(timeout=10)
            except subprocess.TimeoutExpired:
                proc.kill()
                proc.wait()
            result.olla_terminated = True
        elif proc is not None:
            result.olla_terminated = True

    return result
