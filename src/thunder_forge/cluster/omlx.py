"""oMLX node-level runtime helpers."""

from __future__ import annotations

import shlex
from dataclasses import dataclass, field

import httpx

from thunder_forge.cluster.config import Node, RuntimeType


@dataclass
class OmlxHealthResult:
    base_url: str
    health_ok: bool = False
    models_ok: bool = False
    status_ok: bool | None = None
    models: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)


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
    """Probe an oMLX server directly, without going through LiteLLM."""
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
