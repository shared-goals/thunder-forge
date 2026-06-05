"""Tests for Olla smoke helpers."""

import json

import httpx

from thunder_forge.cluster.olla import OllaSmokeResult, smoke_olla_router


def test_smoke_olla_router_reports_green_paths_and_expected_root_v1_absence() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/internal/health":
            return httpx.Response(200, json={"status": "healthy"})
        if request.url.path == "/internal/status/endpoints":
            return httpx.Response(
                200,
                json={
                    "endpoints": [
                        {
                            "name": "infer-03-omlx-live",
                            "status": "healthy",
                        }
                    ]
                },
            )
        if request.url.path == "/olla/openai-compatible/v1/models":
            return httpx.Response(
                200,
                json={
                    "object": "list",
                    "data": [{"id": "Qwen3-1.7B-4bit", "object": "model"}],
                },
            )
        if request.url.path == "/olla/openai-compatible/v1/chat/completions":
            payload = request.read().decode()
            if "qwen3-1.7b-omlx-infer-03-test" in payload:
                response_model = "Qwen3-1.7B-4bit"
            else:
                response_model = "Qwen3-1.7B-4bit"
            return httpx.Response(
                200,
                headers={"X-Olla-Endpoint": "infer-03-omlx-live"},
                json={
                    "id": "chatcmpl-1",
                    "model": response_model,
                    "choices": [{"message": {"content": "pong"}}],
                },
            )
        if request.url.path == "/v1/models":
            return httpx.Response(404, text="not found")
        return httpx.Response(500, text=f"unexpected path: {request.url.path}")

    result = smoke_olla_router(
        base_url="http://127.0.0.1:40115",
        model="Qwen3-1.7B-4bit",
        alias="qwen3-1.7b-omlx-infer-03-test",
        transport=httpx.MockTransport(handler),
    )

    assert isinstance(result, OllaSmokeResult)
    assert result.ok is True
    assert result.health_ok is True
    assert result.endpoints_ok is True
    assert result.models_ok is True
    assert result.chat_ok is True
    assert result.alias_ok is True
    assert result.session_ok is True
    assert result.root_v1_absent is True
    assert result.olla_endpoint == "infer-03-omlx-live"
    assert result.errors == []


def test_smoke_olla_router_accepts_alias_on_different_healthy_endpoint() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/internal/health":
            return httpx.Response(200, json={"status": "healthy"})
        if request.url.path == "/internal/status/endpoints":
            return httpx.Response(
                200,
                json={
                    "endpoints": [
                        {"name": "infer-01-omlx-live", "status": "healthy"},
                        {"name": "infer-03-omlx-live", "status": "healthy"},
                    ]
                },
            )
        if request.url.path == "/olla/openai-compatible/v1/models":
            return httpx.Response(
                200,
                json={"object": "list", "data": [{"id": "gpt-oss-20b-MXFP4-Q8", "object": "model"}]},
            )
        if request.url.path == "/olla/openai-compatible/v1/chat/completions":
            payload = json.loads(request.read().decode())
            endpoint = "infer-03-omlx-live" if payload["model"] == "memory" else "infer-01-omlx-live"
            return httpx.Response(
                200,
                headers={"X-Olla-Endpoint": endpoint},
                json={"id": "chatcmpl-1", "choices": [{"message": {"content": "pong"}}]},
            )
        if request.url.path == "/v1/models":
            return httpx.Response(404, text="not found")
        return httpx.Response(500, text=f"unexpected path: {request.url.path}")

    result = smoke_olla_router(
        base_url="http://127.0.0.1:40115",
        model="gpt-oss-20b-MXFP4-Q8",
        alias="memory",
        transport=httpx.MockTransport(handler),
    )

    assert result.ok is True
    assert result.olla_endpoint == "infer-01-omlx-live"
    assert result.alias_endpoint == "infer-03-omlx-live"
    assert result.alias_ok is True
    assert result.errors == []


def test_smoke_olla_router_fails_when_same_session_routes_to_different_endpoints() -> None:
    backend_endpoints = ["infer-01-omlx-live", "infer-02-omlx-live"]
    backend_count = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal backend_count
        if request.url.path == "/internal/health":
            return httpx.Response(200, json={"status": "healthy"})
        if request.url.path == "/internal/status/endpoints":
            return httpx.Response(
                200,
                json={
                    "endpoints": [
                        {"name": "infer-01-omlx-live", "status": "healthy"},
                        {"name": "infer-02-omlx-live", "status": "healthy"},
                    ]
                },
            )
        if request.url.path == "/olla/openai-compatible/v1/models":
            return httpx.Response(
                200,
                json={"object": "list", "data": [{"id": "Qwen3-1.7B-4bit", "object": "model"}]},
            )
        if request.url.path == "/olla/openai-compatible/v1/chat/completions":
            payload = json.loads(request.read().decode())
            if payload["model"] == "Qwen3-1.7B-4bit":
                assert request.headers.get("X-Olla-Session-ID") == "tf-olla-smoke-session"
                endpoint = backend_endpoints[min(backend_count, len(backend_endpoints) - 1)]
                backend_count += 1
            else:
                endpoint = "infer-01-omlx-live"
            return httpx.Response(
                200,
                headers={"X-Olla-Endpoint": endpoint},
                json={"id": "chatcmpl-1", "choices": [{"message": {"content": "pong"}}]},
            )
        if request.url.path == "/v1/models":
            return httpx.Response(404, text="not found")
        return httpx.Response(500, text=f"unexpected path: {request.url.path}")

    result = smoke_olla_router(
        base_url="http://127.0.0.1:40115",
        model="Qwen3-1.7B-4bit",
        alias="memory",
        transport=httpx.MockTransport(handler),
    )

    assert result.chat_ok is True
    assert result.session_ok is False
    assert backend_count == 2
    assert "same session routed to different endpoints" in result.errors
