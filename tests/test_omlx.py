"""Tests for oMLX node-level runtime helpers."""

import httpx

from thunder_forge.cluster.config import Node, NodeRuntime
from thunder_forge.cluster.omlx import build_omlx_serve_command, check_omlx_health


def test_build_omlx_serve_command_omits_default_model_dir() -> None:
    node = Node(
        host="msm3-wifi.lan",
        ram_gb=128,
        user="shag",
        role="node",
        runtime=NodeRuntime(type="omlx", port=8018),
        home_dir="/Users/shag",
    )

    command = build_omlx_serve_command(node)

    assert command == "/Users/shag/.local/bin/omlx serve --host 0.0.0.0 --port 8018"
    assert "--model-dir" not in command


def test_build_omlx_serve_command_includes_explicit_model_dir_only_when_configured() -> None:
    node = Node(
        host="msm3-wifi.lan",
        ram_gb=128,
        user="shag",
        role="node",
        runtime=NodeRuntime(type="omlx", port=8018, model_dir="/Volumes/cache/omlx-models"),
        home_dir="/Users/shag",
    )

    command = build_omlx_serve_command(node)

    assert command == (
        "/Users/shag/.local/bin/omlx serve --host 0.0.0.0 --port 8018 "
        "--model-dir /Volumes/cache/omlx-models"
    )


def test_check_omlx_health_collects_models_and_optional_status() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/health":
            return httpx.Response(200, json={"status": "ok"})
        if request.url.path == "/v1/models":
            return httpx.Response(200, json={"data": [{"id": "mlx-community/test-model"}]})
        if request.url.path == "/v1/models/status":
            return httpx.Response(200, json={"ready": True})
        return httpx.Response(404)

    result = check_omlx_health("http://msm3-wifi.lan:8018", transport=httpx.MockTransport(handler))

    assert result.base_url == "http://msm3-wifi.lan:8018"
    assert result.health_ok is True
    assert result.models_ok is True
    assert result.status_ok is True
    assert result.models == ["mlx-community/test-model"]
    assert result.errors == []


def test_check_omlx_health_keeps_health_when_optional_status_fails() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/health":
            return httpx.Response(200, json={"status": "ok"})
        if request.url.path == "/v1/models":
            return httpx.Response(200, json={"data": []})
        if request.url.path == "/v1/models/status":
            return httpx.Response(404)
        return httpx.Response(404)

    result = check_omlx_health("http://msm3-wifi.lan:8018/", transport=httpx.MockTransport(handler))

    assert result.health_ok is True
    assert result.models_ok is True
    assert result.status_ok is False
    assert result.errors == []


def test_check_omlx_health_reports_failed_required_probe() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/health":
            return httpx.Response(503, json={"status": "starting"})
        if request.url.path == "/v1/models":
            raise httpx.ConnectError("connection refused", request=request)
        return httpx.Response(404)

    result = check_omlx_health("http://msm3-wifi.lan:8018", transport=httpx.MockTransport(handler))

    assert result.health_ok is False
    assert result.models_ok is False
    assert result.status_ok is False
    assert result.models == []
    assert "GET /health returned 503" in result.errors
    assert "GET /v1/models failed: connection refused" in result.errors
