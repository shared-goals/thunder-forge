"""Minimal Thunder Forge edge contract helpers.

The runtime HTTP service will use these pure helpers for auth, OpenAI root-path
mapping, sticky Olla sessions, and accounting logs. Keeping the contract here
makes the edge behavior testable before wiring an ASGI/proxy process.
"""

from __future__ import annotations

import json
import math
import os
import re
import secrets
import shutil
import time
from collections import Counter, defaultdict
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from uuid import uuid4

import httpx

from thunder_forge.cluster.ports import DEFAULT_EDGE_PORT, DEFAULT_OLLA_PORT, local_base_url, resolve_port
from thunder_forge.cluster.services import (
    LaunchdServiceResult,
    LaunchdServiceSpec,
    generate_launchd_plist,
    launch_agent_path,
    launch_daemon_path,
    run_local_commands,
    system_launchd_commands,
    user_launchd_commands,
    write_local_file,
)

OLLA_OPENAI_PREFIX = "/olla/openai-compatible/v1"
EDGE_USERS_ENV = "TF_USERS"
EDGE_DEFAULT_PORT = DEFAULT_EDGE_PORT
EDGE_LAUNCHD_LABEL_PREFIX = "com.thunder-forge.edge"

_ENV_LINE_RE = re.compile(
    r"^(?P<prefix>\s*)(?P<name>[A-Za-z_][A-Za-z0-9_]*)(?P<sep>\s*=\s*)(?P<value>.*?)(?P<suffix>\s*)$"
)


def edge_launchd_label(*, port: int = EDGE_DEFAULT_PORT) -> str:
    return f"{EDGE_LAUNCHD_LABEL_PREFIX}-{port}"


@dataclass(frozen=True)
class EdgeClient:
    """Configured client allowed to call the TF edge."""

    client_id: str


@dataclass(frozen=True)
class EdgeAuthResult:
    """Result of static API-key authentication."""

    allowed: bool
    status_code: int
    client_id: str = ""


@dataclass(frozen=True)
class EdgeSessionID:
    """Olla sticky-session id chosen for an edge request."""

    value: str
    generated: bool


@dataclass
class EdgeProxyConfig:
    """Runtime config for the minimal TF edge proxy."""

    olla_base_url: str
    clients_by_key: dict[str, EdgeClient]
    access_log_sink: Callable[[str], None] | None = None
    timeout: float = 60.0


@dataclass(frozen=True)
class EdgeProxyResponse:
    """Serializable proxy response used by the stdlib HTTP server."""

    status_code: int
    headers: dict[str, str]
    body: bytes


@dataclass
class EdgeSmokeResult:
    """Result of a black-box smoke test against a running TF edge."""

    base_url: str
    model: str
    missing_auth_401: bool = False
    invalid_auth_401: bool = False
    models_ok: bool = False
    chat_ok: bool = False
    session_ok: bool = False
    latency_ms: int = 0
    olla_endpoint: str = ""
    errors: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return self.missing_auth_401 and self.invalid_auth_401 and self.models_ok and self.chat_ok and self.session_ok


@dataclass(frozen=True)
class EdgeKeyStatus:
    """Status for one client key in a local dotenv file."""

    client_id: str
    env_name: str
    status: str


@dataclass(frozen=True)
class EdgeKeySetupResult:
    """Result of ensuring MVP edge API keys exist in .env."""

    env_file: str
    keys: list[EdgeKeyStatus]


@dataclass(frozen=True)
class EdgeUsageClientSummary:
    """Per-client summary of TF edge JSONL accounting records."""

    client_id: str
    requests: int
    failures: int
    models: dict[str, int]
    endpoints: dict[str, int]
    latency_ms_p50: int
    latency_ms_p95: int


@dataclass(frozen=True)
class EdgeUsageSummary:
    """Aggregate summary of a TF edge access log."""

    access_log_path: str
    requests_total: int
    invalid_lines: int
    clients: list[EdgeUsageClientSummary]


@dataclass(frozen=True)
class EdgeAccessLog:
    """Minimal JSONL accounting record.

    Deliberately excludes API keys and request/response bodies. Prompt content is
    private; MVP accounting only needs client/model/path/status/latency and the
    Olla endpoint attribution header when available.
    """

    timestamp: str
    request_id: str
    client_id: str
    path: str
    model: str
    status_code: int
    latency_ms: int
    olla_endpoint: str = ""

    def to_json_dict(self) -> dict[str, str | int]:
        payload: dict[str, str | int] = {
            "timestamp": self.timestamp,
            "request_id": self.request_id,
            "client_id": self.client_id,
            "path": self.path,
            "model": self.model,
            "status_code": self.status_code,
            "latency_ms": self.latency_ms,
        }
        if self.olla_endpoint:
            payload["olla_endpoint"] = self.olla_endpoint
        return payload


def _bearer_token(authorization: str | None) -> str:
    if not authorization:
        return ""
    scheme, _, token = authorization.partition(" ")
    if scheme.lower() != "bearer" or not token:
        return ""
    return token.strip()


def validate_edge_client_id(client_id: str) -> str:
    """Return a stripped client id after validating it is safe for logs and JSON env storage."""
    normalized = client_id.strip()
    if not normalized:
        msg = "client id must contain at least one alphanumeric character"
        raise ValueError(msg)
    if not re.fullmatch(r"[A-Za-z0-9._-]+", normalized):
        msg = "client id may contain only letters, numbers, dots, underscores, and dashes"
        raise ValueError(msg)
    return normalized


def parse_edge_users_json(value: str) -> dict[str, str]:
    """Parse the TF_USERS client-id -> API-key JSON hash."""
    if not value.strip():
        return {}
    try:
        payload = json.loads(value)
    except json.JSONDecodeError as exc:
        msg = f"{EDGE_USERS_ENV} must be a JSON object mapping client ids to API keys"
        raise ValueError(msg) from exc
    if not isinstance(payload, dict):
        msg = f"{EDGE_USERS_ENV} must be a JSON object mapping client ids to API keys"
        raise ValueError(msg)

    users: dict[str, str] = {}
    for raw_client_id, raw_api_key in payload.items():
        if not isinstance(raw_client_id, str) or not isinstance(raw_api_key, str):
            msg = f"{EDGE_USERS_ENV} keys and values must be strings"
            raise ValueError(msg)
        client_id = validate_edge_client_id(raw_client_id)
        api_key = raw_api_key.strip()
        if api_key:
            users[client_id] = api_key
    return users


def _encode_edge_users_json(users: dict[str, str]) -> str:
    return json.dumps(dict(sorted(users.items())), separators=(",", ":"))


def _quote_dotenv_value(value: str) -> str:
    return f"'{value}'"


def _decode_dotenv_value(value: str) -> str:
    stripped = value.strip()
    if len(stripped) >= 2 and stripped[0] == stripped[-1] and stripped[0] in {'"', "'"}:
        return stripped[1:-1]
    return stripped


def load_edge_user_keys_from_env(
    *,
    env: dict[str, str] | None = None,
    users_env: str = EDGE_USERS_ENV,
) -> dict[str, str]:
    """Load the TF edge client-id -> API-key hash from the environment."""
    source = env if env is not None else os.environ
    return parse_edge_users_json(source.get(users_env, ""))


def edge_api_key_from_env(
    *,
    env: dict[str, str] | None = None,
    client_id: str,
    users_env: str = EDGE_USERS_ENV,
) -> tuple[str, str]:
    """Return (env_name, api_key) for one configured client id from the TF_USERS hash."""
    users = load_edge_user_keys_from_env(env=env, users_env=users_env)
    return users_env, users.get(validate_edge_client_id(client_id), "")


def load_edge_clients_from_env(
    *,
    env: dict[str, str] | None = None,
    users_env: str = EDGE_USERS_ENV,
) -> dict[str, EdgeClient]:
    """Load all TF edge clients from the TF_USERS client-id -> API-key hash."""
    clients: dict[str, EdgeClient] = {}
    for client_id, api_key in load_edge_user_keys_from_env(env=env, users_env=users_env).items():
        clients[api_key] = EdgeClient(client_id=client_id)
    return clients


def build_edge_clients_from_env(
    *,
    env: dict[str, str] | None = None,
    users_env: str = EDGE_USERS_ENV,
) -> dict[str, EdgeClient]:
    """Build edge auth mapping from the configured TF_USERS hash."""
    return load_edge_clients_from_env(env=env, users_env=users_env)


def _env_raw_value_is_empty(raw_value: str) -> bool:
    value = raw_value.strip()
    if value in {'""', "''"}:
        return True
    return value == ""


def _get_env_value_from_lines(lines: list[str], env_name: str) -> str:
    for line in lines:
        match = _ENV_LINE_RE.match(line)
        if match is not None and match.group("name") == env_name:
            return _decode_dotenv_value(match.group("value"))
    return ""


def _set_or_append_env_value(lines: list[str], env_name: str, value: str, *, overwrite: bool = False) -> str:
    replacement = f"{env_name}={value}"
    for index, line in enumerate(lines):
        match = _ENV_LINE_RE.match(line)
        if match is None or match.group("name") != env_name:
            continue
        if overwrite:
            lines[index] = replacement
            return "updated"
        if _env_raw_value_is_empty(match.group("value")):
            lines[index] = replacement
            return "created"
        return "present"
    lines.append(replacement)
    return "created"


def ensure_edge_api_keys(
    *,
    env_file: Path,
    clients: list[str] | tuple[str, ...],
    users_env: str = EDGE_USERS_ENV,
) -> EdgeKeySetupResult:
    """Create missing local TF edge API keys in a single dotenv JSON hash without printing secrets."""
    env_file.parent.mkdir(parents=True, exist_ok=True)
    if env_file.exists():
        lines = env_file.read_text().splitlines()
    else:
        lines = []

    users = parse_edge_users_json(_get_env_value_from_lines(lines, users_env))

    statuses: list[EdgeKeyStatus] = []
    seen_clients: set[str] = set()
    for client_id in clients:
        normalized_client_id = validate_edge_client_id(client_id)
        if not normalized_client_id or normalized_client_id in seen_clients:
            continue
        seen_clients.add(normalized_client_id)
        if normalized_client_id in users and users[normalized_client_id].strip():
            status = "present"
        else:
            users[normalized_client_id] = secrets.token_urlsafe(32)
            status = "created"
        statuses.append(EdgeKeyStatus(client_id=normalized_client_id, env_name=users_env, status=status))

    _set_or_append_env_value(
        lines,
        users_env,
        _quote_dotenv_value(_encode_edge_users_json(users)),
        overwrite=True,
    )

    env_file.write_text("\n".join(lines) + "\n")
    env_file.chmod(0o600)

    return EdgeKeySetupResult(
        env_file=str(env_file),
        keys=statuses,
    )


def authenticate_edge_request(
    authorization: str | None,
    clients_by_key: dict[str, EdgeClient],
) -> EdgeAuthResult:
    """Validate a static bearer token and map it to a client identity."""
    token = _bearer_token(authorization)
    client = clients_by_key.get(token)
    if client is None:
        return EdgeAuthResult(allowed=False, status_code=401)
    return EdgeAuthResult(allowed=True, status_code=200, client_id=client.client_id)


def _nearest_rank_percentile(values: list[int], percentile: float) -> int:
    if not values:
        return 0
    sorted_values = sorted(values)
    rank = max(1, math.ceil((percentile / 100) * len(sorted_values)))
    return sorted_values[rank - 1]


def summarize_edge_usage(access_log_path: Path) -> EdgeUsageSummary:
    """Summarize TF edge JSONL access logs by authenticated client id."""
    records: dict[str, dict[str, object]] = defaultdict(
        lambda: {
            "requests": 0,
            "failures": 0,
            "models": Counter(),
            "endpoints": Counter(),
            "latencies": [],
        }
    )
    requests_total = 0
    invalid_lines = 0

    if not access_log_path.exists():
        return EdgeUsageSummary(
            access_log_path=str(access_log_path),
            requests_total=0,
            invalid_lines=0,
            clients=[],
        )

    for line in access_log_path.read_text().splitlines():
        if not line.strip():
            continue
        try:
            payload = json.loads(line)
        except json.JSONDecodeError:
            invalid_lines += 1
            continue
        if not isinstance(payload, dict):
            invalid_lines += 1
            continue
        client_id = payload.get("client_id")
        if not isinstance(client_id, str) or not client_id:
            invalid_lines += 1
            continue

        requests_total += 1
        record = records[client_id]
        record["requests"] = int(record["requests"]) + 1

        status_code = payload.get("status_code")
        if isinstance(status_code, int) and status_code >= 400:
            record["failures"] = int(record["failures"]) + 1

        model = payload.get("model")
        if isinstance(model, str) and model:
            models = record["models"]
            assert isinstance(models, Counter)
            models[model] += 1

        endpoint = payload.get("olla_endpoint")
        if isinstance(endpoint, str) and endpoint:
            endpoints = record["endpoints"]
            assert isinstance(endpoints, Counter)
            endpoints[endpoint] += 1

        latency_ms = payload.get("latency_ms")
        if isinstance(latency_ms, int):
            latencies = record["latencies"]
            assert isinstance(latencies, list)
            latencies.append(latency_ms)

    clients = []
    for client_id, record in sorted(records.items()):
        latencies = record["latencies"]
        assert isinstance(latencies, list)
        models = record["models"]
        endpoints = record["endpoints"]
        assert isinstance(models, Counter)
        assert isinstance(endpoints, Counter)
        clients.append(
            EdgeUsageClientSummary(
                client_id=client_id,
                requests=int(record["requests"]),
                failures=int(record["failures"]),
                models=dict(models.most_common()),
                endpoints=dict(endpoints.most_common()),
                latency_ms_p50=_nearest_rank_percentile(latencies, 50),
                latency_ms_p95=_nearest_rank_percentile(latencies, 95),
            )
        )

    return EdgeUsageSummary(
        access_log_path=str(access_log_path),
        requests_total=requests_total,
        invalid_lines=invalid_lines,
        clients=clients,
    )


def rewrite_openai_path(path: str) -> str:
    """Map TF edge root /v1 OpenAI paths to Olla's provider path."""
    if not path.startswith("/v1/") and path != "/v1":
        msg = "TF edge only /v1/* paths can be proxied to Olla"
        raise ValueError(msg)
    suffix = path.removeprefix("/v1")
    return f"{OLLA_OPENAI_PREFIX}{suffix}"


def ensure_olla_session_id(headers: dict[str, str], *, request_id: str, client_id: str) -> EdgeSessionID:
    """Preserve caller-provided sticky session id or create a stable edge id."""
    for name, value in headers.items():
        if name.lower() == "x-olla-session-id" and value.strip():
            return EdgeSessionID(value=value.strip(), generated=False)
    safe_client_id = "".join(ch if ch.isalnum() or ch in {"-", "_"} else "-" for ch in client_id)
    safe_request_id = "".join(ch if ch.isalnum() or ch in {"-", "_"} else "-" for ch in request_id)
    return EdgeSessionID(value=f"tf-{safe_client_id}-{safe_request_id}", generated=True)


def build_edge_access_log(
    *,
    request_id: str,
    client_id: str,
    path: str,
    model: str,
    status_code: int,
    latency_ms: int,
    olla_endpoint: str = "",
    api_key: str = "",
) -> EdgeAccessLog:
    """Build a secret-free access/accounting log record."""
    _ = api_key  # Accepted only so callers cannot accidentally include it in the record.
    return EdgeAccessLog(
        timestamp=datetime.now(UTC).isoformat(),
        request_id=request_id,
        client_id=client_id,
        path=path,
        model=model,
        status_code=status_code,
        latency_ms=latency_ms,
        olla_endpoint=olla_endpoint,
    )


def _header_value(headers: dict[str, str], name: str) -> str | None:
    for header_name, value in headers.items():
        if header_name.lower() == name.lower():
            return value
    return None


def _decode_json_object(body: bytes) -> dict[str, object] | None:
    if not body:
        return None
    try:
        payload = json.loads(body.decode())
    except (UnicodeDecodeError, json.JSONDecodeError):
        return None
    return payload if isinstance(payload, dict) else None


def _extract_model(body: bytes) -> str:
    payload = _decode_json_object(body)
    if payload is None:
        return ""
    model = payload.get("model", "")
    return model if isinstance(model, str) else ""


def _requests_streaming_response(method: str, body: bytes) -> bool:
    if method.upper() != "POST":
        return False
    payload = _decode_json_object(body)
    return payload is not None and payload.get("stream") is True


def _json_response(status_code: int, payload: dict[str, str]) -> EdgeProxyResponse:
    return EdgeProxyResponse(
        status_code=status_code,
        headers={"Content-Type": "application/json"},
        body=json.dumps(payload, separators=(",", ":")).encode(),
    )


def proxy_edge_request(
    *,
    method: str,
    path: str,
    headers: dict[str, str],
    body: bytes,
    config: EdgeProxyConfig,
    transport: httpx.BaseTransport | None = None,
) -> EdgeProxyResponse:
    """Authenticate, rewrite, and proxy a single non-streaming edge request."""
    started = time.perf_counter()
    auth = authenticate_edge_request(_header_value(headers, "Authorization"), config.clients_by_key)
    if not auth.allowed:
        return _json_response(401, {"error": "unauthorized"})

    try:
        olla_path = rewrite_openai_path(path)
    except ValueError:
        return _json_response(404, {"error": "not_found"})

    if _requests_streaming_response(method, body):
        return _json_response(
            501,
            {
                "error": "streaming_not_implemented",
                "message": "TF edge is a non-streaming proxy; send stream=false or omit stream.",
            },
        )

    request_id = _header_value(headers, "X-Request-ID") or uuid4().hex
    session = ensure_olla_session_id(headers, request_id=request_id, client_id=auth.client_id)
    forwarded_headers = {
        "X-Olla-Session-ID": session.value,
        "X-Request-ID": request_id,
    }
    content_type = _header_value(headers, "Content-Type")
    if content_type:
        forwarded_headers["Content-Type"] = content_type

    normalized_base_url = config.olla_base_url.rstrip("/")
    with httpx.Client(
        base_url=normalized_base_url,
        timeout=config.timeout,
        transport=transport,
        trust_env=False,
    ) as client:
        try:
            response = client.request(method, olla_path, headers=forwarded_headers, content=body)
        except httpx.HTTPError as exc:
            return _json_response(502, {"error": f"upstream_failed: {exc}"})

    proxy_headers: dict[str, str] = {}
    if content_type := response.headers.get("Content-Type"):
        proxy_headers["Content-Type"] = content_type
    if olla_endpoint := response.headers.get("X-Olla-Endpoint"):
        proxy_headers["X-Olla-Endpoint"] = olla_endpoint
    latency_ms = int((time.perf_counter() - started) * 1000)
    model = _extract_model(body)
    log_record = build_edge_access_log(
        request_id=request_id,
        client_id=auth.client_id,
        path=path,
        model=model,
        status_code=response.status_code,
        latency_ms=latency_ms,
        olla_endpoint=response.headers.get("X-Olla-Endpoint", ""),
    )
    if config.access_log_sink is not None:
        config.access_log_sink(json.dumps(log_record.to_json_dict(), separators=(",", ":")))

    return EdgeProxyResponse(status_code=response.status_code, headers=proxy_headers, body=response.content)


def serve_edge_proxy(*, host: str, port: int, config: EdgeProxyConfig) -> None:
    """Serve the minimal non-streaming TF edge with stdlib HTTP server."""

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self) -> None:  # noqa: N802
            self._handle_edge_request()

        def do_POST(self) -> None:  # noqa: N802
            self._handle_edge_request()

        def log_message(self, format: str, *args: object) -> None:
            return

        def _handle_edge_request(self) -> None:
            length = int(self.headers.get("Content-Length", "0") or "0")
            body = self.rfile.read(length) if length else b""
            result = proxy_edge_request(
                method=self.command,
                path=self.path,
                headers=dict(self.headers.items()),
                body=body,
                config=config,
            )
            self.send_response(result.status_code)
            for name, value in result.headers.items():
                self.send_header(name, value)
            self.send_header("Content-Length", str(len(result.body)))
            self.end_headers()
            self.wfile.write(result.body)

    ThreadingHTTPServer((host, port), Handler).serve_forever()


def _thunder_forge_command(repo_root: Path) -> list[str]:
    console_script = repo_root / ".venv" / "bin" / "thunder-forge"
    if console_script.exists():
        return [str(console_script)]
    return [shutil.which("uv") or "/opt/homebrew/bin/uv", "run", "thunder-forge"]


def _edge_service_spec(
    *,
    repo_root: Path,
    host: str,
    port: int,
    olla_base_url: str,
    users_env: str,
    access_log_path: Path,
    user: str,
) -> LaunchdServiceSpec:
    log_dir = repo_root / "logs"
    resolved_access_log_path = access_log_path.expanduser()
    if not resolved_access_log_path.is_absolute():
        resolved_access_log_path = repo_root / resolved_access_log_path
    program_arguments = [
        *_thunder_forge_command(repo_root),
        "edge",
        "serve",
        "--host",
        host,
        "--port",
        str(port),
        "--olla-base-url",
        olla_base_url,
        "--users-env",
        users_env,
        "--access-log",
        str(resolved_access_log_path),
    ]
    process_pattern = f"^.*thunder-forge edge serve .*--port {port}.*$"
    return LaunchdServiceSpec(
        name="edge",
        label=edge_launchd_label(port=port),
        program_arguments=program_arguments,
        working_directory=str(repo_root),
        stdout_log=str(log_dir / f"edge-{port}.stdout.log"),
        stderr_log=str(log_dir / f"edge-{port}.stderr.log"),
        environment={
            "HOME": str(Path.home()),
            "USER": user,
            "PATH": ":".join(
                [
                    f"{repo_root}/.venv/bin",
                    f"{Path.home()}/.local/bin",
                    "/opt/homebrew/bin",
                    "/usr/local/bin",
                    "/usr/bin",
                    "/bin",
                    "/usr/sbin",
                    "/sbin",
                ]
            ),
        },
        process_pattern=process_pattern,
        user=user,
    )


def _build_edge_launchd_result(
    *,
    repo_root: Path,
    host: str,
    port: int,
    olla_base_url: str,
    users_env: str,
    access_log_path: Path,
    user: str,
    manager: str,
    interactive_sudo: bool = False,
    admin_user: str = "",
) -> tuple[LaunchdServiceResult, str]:
    spec = _edge_service_spec(
        repo_root=repo_root,
        host=host,
        port=port,
        olla_base_url=olla_base_url,
        users_env=users_env,
        access_log_path=access_log_path,
        user=user,
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
    log_dir = _sh_quote(str(repo_root / "logs"))
    if system_daemon:
        result.commands = system_launchd_commands(
            label=spec.label,
            staging_plist_path=staging_plist_path,
            plist_path=plist_path,
            process_pattern=spec.process_pattern,
            setup_command=f"mkdir -p {_sh_quote(str(repo_root / '.tmp' / 'run'))} {log_dir}",
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
    return result, local_base_url(port)


def _sh_quote(value: str) -> str:
    import shlex

    return shlex.quote(value)


def _wait_edge_healthy(
    base_url: str,
    *,
    retries: int = 30,
    interval: float = 1.0,
    timeout: float = 5.0,
) -> bool:
    for _ in range(retries):
        try:
            with httpx.Client(base_url=base_url, timeout=timeout, trust_env=False) as client:
                response = client.get("/v1/models")
                if response.status_code == 401:
                    return True
        except httpx.HTTPError:
            pass
        time.sleep(interval)
    return False


def _apply_local_edge_service(
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

    result.health_ok = _wait_edge_healthy(base_url, retries=30, interval=1.0, timeout=5.0)
    if not result.health_ok:
        result.errors.append(f"TF edge health check failed at {base_url}")
    result.applied = True
    return result


def run_edge_service_restart(
    *,
    repo_root: Path,
    host: str = "127.0.0.1",
    port: int | None = None,
    olla_base_url: str | None = None,
    users_env: str = EDGE_USERS_ENV,
    access_log_path: Path = Path("logs/tf-edge-access.jsonl"),
    user: str | None = None,
    manager: str = "launchd",
    apply: bool = True,
    timeout: int = 60,
    interactive_sudo: bool = False,
    admin_user: str = "",
) -> LaunchdServiceResult:
    """Install/update and restart TF edge as a frontend launchd service."""
    normalized_manager = manager.lower()
    if normalized_manager not in {"launchd", "daemon"}:
        msg = "TF edge service manager must be 'launchd' or 'daemon'"
        raise ValueError(msg)
    resolved_port = resolve_port(port, default=EDGE_DEFAULT_PORT)
    result, base_url = _build_edge_launchd_result(
        repo_root=repo_root,
        host=host,
        port=resolved_port,
        olla_base_url=olla_base_url or local_base_url(DEFAULT_OLLA_PORT),
        users_env=users_env,
        access_log_path=access_log_path,
        user=user or os.environ.get("USER", "shag"),
        manager=normalized_manager,
        interactive_sudo=interactive_sudo,
        admin_user=admin_user,
    )
    if not apply:
        return result
    return _apply_local_edge_service(result, base_url=base_url, timeout=timeout, stream=interactive_sudo)


def smoke_edge_contract(
    *,
    base_url: str,
    api_key: str,
    model: str,
    prompt: str = "Reply with one short word: pong.",
    timeout: float = 30.0,
    transport: httpx.BaseTransport | None = None,
) -> EdgeSmokeResult:
    """Run the Task 6 black-box edge contract smoke against a live edge."""
    normalized_base_url = base_url.rstrip("/")
    result = EdgeSmokeResult(base_url=normalized_base_url, model=model)
    fixed_session_id = "tf-smoke-session"

    with httpx.Client(base_url=normalized_base_url, timeout=timeout, transport=transport, trust_env=False) as client:
        try:
            response = client.get("/v1/models")
            result.missing_auth_401 = response.status_code == 401
            if not result.missing_auth_401:
                result.errors.append(f"missing API key returned {response.status_code}, expected 401")
        except httpx.HTTPError as exc:
            result.errors.append(f"missing API key probe failed: {exc}")

        try:
            response = client.get("/v1/models", headers={"Authorization": "Bearer invalid-tf-edge-smoke-key"})
            result.invalid_auth_401 = response.status_code == 401
            if not result.invalid_auth_401:
                result.errors.append(f"invalid API key returned {response.status_code}, expected 401")
        except httpx.HTTPError as exc:
            result.errors.append(f"invalid API key probe failed: {exc}")

        auth_headers = {"Authorization": f"Bearer {api_key}", "X-Olla-Session-ID": fixed_session_id}
        try:
            response = client.get("/v1/models", headers=auth_headers)
            result.models_ok = response.is_success
            if not result.models_ok:
                result.errors.append(f"GET /v1/models returned {response.status_code}: {response.text}")
        except httpx.HTTPError as exc:
            result.errors.append(f"GET /v1/models failed: {exc}")

        started = time.perf_counter()
        try:
            response = client.post(
                "/v1/chat/completions",
                headers=auth_headers,
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
            result.session_ok = response.request.headers.get("X-Olla-Session-ID") == fixed_session_id
            result.olla_endpoint = response.headers.get("X-Olla-Endpoint", "")
            if not result.chat_ok:
                result.errors.append(f"POST /v1/chat/completions returned {response.status_code}: {response.text}")
            if not result.session_ok:
                result.errors.append("X-Olla-Session-ID was not preserved on chat request")
        except httpx.HTTPError as exc:
            result.latency_ms = int((time.perf_counter() - started) * 1000)
            result.errors.append(f"POST /v1/chat/completions failed: {exc}")

    return result
