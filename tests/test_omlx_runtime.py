"""Tests for oMLX runtime helpers."""

import subprocess

import httpx

from thunder_forge.cluster.config import Node, NodeRuntime, RuntimeType
from thunder_forge.cluster.omlx import run_omlx_runtime_start, smoke_omlx_chat


def test_run_omlx_runtime_start_executes_remote_nohup_command(monkeypatch) -> None:
    calls = []

    def fake_run(args, **kwargs):
        calls.append((args, kwargs))
        return subprocess.CompletedProcess(args=args, returncode=0, stdout="4242\n", stderr="")

    import thunder_forge.cluster.omlx as omlx_module

    monkeypatch.setattr(omlx_module.subprocess, "run", fake_run)
    node = Node(
        host="msm3-wifi.lan",
        user="shag",
        ram_gb=128,
        home_dir="/Users/shag",
        runtime=NodeRuntime(type=RuntimeType.OMLX, port=8018),
    )

    result = run_omlx_runtime_start(node)

    assert result.returncode == 0
    assert result.pid == "4242"
    assert calls[0][0] == [
        "ssh",
        "-o",
        "BatchMode=yes",
        "-o",
        "ConnectTimeout=8",
        "shag@msm3-wifi.lan",
        (
            "nohup /Users/shag/.local/bin/omlx serve --host 0.0.0.0 --port 8018 "
            "> /tmp/thunder-forge-omlx-8018.log 2>&1 & echo $!"
        ),
    ]
    assert calls[0][1]["check"] is False
    assert calls[0][1]["text"] is True
    assert calls[0][1]["timeout"] == 30


def test_smoke_omlx_chat_passes_when_model_is_visible_and_chat_answers() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.method == "GET" and request.url.path == "/health":
            return httpx.Response(200, json={"status": "ok"})
        if request.method == "GET" and request.url.path == "/v1/models":
            return httpx.Response(200, json={"data": [{"id": "Qwen3-1.7B-4bit"}]})
        if request.method == "POST" and request.url.path == "/v1/chat/completions":
            return httpx.Response(
                200,
                json={
                    "choices": [
                        {
                            "message": {
                                "role": "assistant",
                                "content": "pong",
                            }
                        }
                    ]
                },
            )
        return httpx.Response(404)

    result = smoke_omlx_chat(
        "http://msm3-wifi.lan:8018",
        model="Qwen3-1.7B-4bit",
        transport=httpx.MockTransport(handler),
    )

    assert result.ok is True
    assert result.health_ok is True
    assert result.models_ok is True
    assert result.model_visible is True
    assert result.chat_ok is True
    assert result.answer == "pong"
    assert result.latency_ms >= 0
    assert result.errors == []


def test_smoke_omlx_chat_fails_when_requested_model_is_not_visible() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.method == "GET" and request.url.path == "/health":
            return httpx.Response(200, json={"status": "ok"})
        if request.method == "GET" and request.url.path == "/v1/models":
            return httpx.Response(200, json={"data": [{"id": "other-model"}]})
        return httpx.Response(500)

    result = smoke_omlx_chat(
        "http://msm3-wifi.lan:8018",
        model="Qwen3-1.7B-4bit",
        transport=httpx.MockTransport(handler),
    )

    assert result.ok is False
    assert result.health_ok is True
    assert result.models_ok is True
    assert result.model_visible is False
    assert result.chat_ok is False
    assert result.models == ["other-model"]
    assert "model 'Qwen3-1.7B-4bit' is not visible" in result.errors
