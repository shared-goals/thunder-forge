"""Minimal Thunder Forge edge contract helpers.

The runtime HTTP service will use these pure helpers for auth, OpenAI root-path
mapping, sticky Olla sessions, and accounting logs. Keeping the contract here
makes the edge behavior testable before wiring an ASGI/proxy process.
"""

from __future__ import annotations

import json
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from uuid import uuid4

import httpx

OLLA_OPENAI_PREFIX = "/olla/openai-compatible/v1"


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


def _extract_model(body: bytes) -> str:
    if not body:
        return ""
    try:
        payload = json.loads(body.decode())
    except (UnicodeDecodeError, json.JSONDecodeError):
        return ""
    if not isinstance(payload, dict):
        return ""
    model = payload.get("model", "")
    return model if isinstance(model, str) else ""


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
