"""Tests for oMLX node-level runtime helpers."""

import plistlib
import shlex
from types import SimpleNamespace

import httpx
import pytest

from thunder_forge.cluster.config import Node, NodeRuntime, parse_cluster_config
from thunder_forge.cluster.omlx import (
    build_omlx_auth_settings_command,
    build_omlx_serve_command,
    check_omlx_health,
    ensure_omlx_auth_settings,
    generate_launchd_plist,
    resolve_latest_omlx_release,
    smoke_omlx_chat,
)


def test_build_omlx_serve_command_omits_default_model_dir() -> None:
    node = Node(
        host="infer-03.lan",
        ram_gb=128,
        user="shag",
        roles=["inference"],
        runtime=NodeRuntime(type="omlx", port=8018),
        home_dir="/Users/shag",
    )

    command = build_omlx_serve_command(node)

    assert command == "/Users/shag/.local/bin/omlx serve --host 0.0.0.0 --port 8018"
    assert "--model-dir" not in command


@pytest.mark.parametrize("options,expected", [
    ({}, []),
    ({"memory_guard": "off"}, ["--memory-guard", "off"]),
    ({"memory_guard": "safe"}, ["--memory-guard", "safe"]),
    ({"memory_guard": "balanced"}, ["--memory-guard", "balanced"]),
    ({"memory_guard": "aggressive"}, ["--memory-guard", "aggressive"]),
    ({"memory_guard_gb": 90.5}, ["--memory-guard-gb", "90.5"]),
    ({"memory_guard": "safe", "memory_guard_gb": 90},
     ["--memory-guard", "safe", "--memory-guard-gb", "90.0"]),
])
def test_memory_settings_reach_process_and_launchd(options: dict, expected: list[str]) -> None:
    config = parse_cluster_config({"nodes": {"infer-03": {
        "host": "infer-03.lan", "ram_gb": 128, "user": "shag",
        "runtime": {"type": "omlx", **options},
    }}})
    node = config.nodes["infer-03"]
    node.home_dir = "/Users/shag"
    arguments = ["/Users/shag/.local/bin/omlx", "serve", "--host", "0.0.0.0", "--port", "8018", *expected]
    assert shlex.split(build_omlx_serve_command(node)) == arguments
    for system_daemon in (False, True):
        plist = plistlib.loads(generate_launchd_plist(node, system_daemon=system_daemon).encode())
        if system_daemon:
            # PR #5 wraps system-daemon arguments in a root sh -c GPU-memory setup script.
            assert plist["ProgramArguments"][:2] == ["/bin/sh", "-c"]
            assert shlex.join(arguments) in plist["ProgramArguments"][2]
        else:
            assert plist["ProgramArguments"] == arguments


def test_build_omlx_serve_command_includes_explicit_model_dir_only_when_configured() -> None:
    node = Node(
        host="infer-03.lan",
        ram_gb=128,
        user="shag",
        roles=["inference"],
        runtime=NodeRuntime(type="omlx", port=8018, model_dir="/Volumes/cache/omlx-models"),
        home_dir="/Users/shag",
    )

    command = build_omlx_serve_command(node)

    assert command == (
        "/Users/shag/.local/bin/omlx serve --host 0.0.0.0 --port 8018 --model-dir /Volumes/cache/omlx-models"
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

    result = check_omlx_health("http://infer-03.lan:8018", transport=httpx.MockTransport(handler))

    assert result.base_url == "http://infer-03.lan:8018"
    assert result.health_ok is True
    assert result.models_ok is True
    assert result.status_ok is True
    assert result.models == ["mlx-community/test-model"]
    assert result.errors == []


def test_check_omlx_health_can_probe_service_only() -> None:
    paths: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        paths.append(request.url.path)
        if request.url.path == "/health":
            return httpx.Response(200, json={"status": "ok"})
        return httpx.Response(500)

    result = check_omlx_health(
        "http://infer-03.lan:8018",
        include_models=False,
        transport=httpx.MockTransport(handler),
    )

    assert result.health_ok is True
    assert result.models_ok is False
    assert result.status_ok is None
    assert result.errors == []
    assert paths == ["/health"]


def test_check_omlx_health_parses_model_statuses() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/health":
            return httpx.Response(200, json={"status": "ok"})
        if request.url.path == "/v1/models":
            return httpx.Response(200, json={"data": [{"id": "Qwen3-1.7B-4bit"}]})
        if request.url.path == "/v1/models/status":
            return httpx.Response(
                200,
                json={
                    "models": [
                        {
                            "id": "Qwen3-1.7B-4bit",
                            "loaded": True,
                            "is_loading": False,
                            "estimated_size": 123,
                        }
                    ]
                },
            )
        if request.url.path == "/api/status":
            return httpx.Response(200, json={"active_requests": 1, "waiting_requests": 0})
        return httpx.Response(404)

    result = check_omlx_health("http://infer-03.lan:8018", transport=httpx.MockTransport(handler))

    assert result.status_ok is True
    assert result.model_statuses["Qwen3-1.7B-4bit"]["loaded"] is True
    assert result.model_statuses["Qwen3-1.7B-4bit"]["is_loading"] is False
    assert result.active_requests == 1
    assert result.waiting_requests == 0


def test_check_omlx_health_keeps_health_when_optional_status_fails() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/health":
            return httpx.Response(200, json={"status": "ok"})
        if request.url.path == "/v1/models":
            return httpx.Response(200, json={"data": []})
        if request.url.path == "/v1/models/status":
            return httpx.Response(404)
        return httpx.Response(404)

    result = check_omlx_health("http://infer-03.lan:8018/", transport=httpx.MockTransport(handler))

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

    result = check_omlx_health("http://infer-03.lan:8018", transport=httpx.MockTransport(handler))

    assert result.health_ok is False
    assert result.models_ok is False
    assert result.status_ok is False
    assert result.models == []
    assert "GET /health returned 503" in result.errors
    assert "GET /v1/models failed: connection refused" in result.errors


def test_smoke_omlx_chat_skips_chat_when_model_is_still_loading() -> None:
    paths: list[tuple[str, str]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        paths.append((request.method, request.url.path))
        if request.url.path == "/health":
            return httpx.Response(200, json={"status": "ok"})
        if request.url.path == "/v1/models":
            return httpx.Response(200, json={"data": [{"id": "Qwen3-1.7B-4bit"}]})
        if request.url.path == "/v1/models/status":
            return httpx.Response(
                200,
                json={"models": [{"id": "Qwen3-1.7B-4bit", "loaded": False, "is_loading": True}]},
            )
        if request.url.path == "/v1/chat/completions":
            return httpx.Response(200, json={"unexpected": True})
        return httpx.Response(404)

    result = smoke_omlx_chat(
        "http://infer-03.lan:8018",
        model="Qwen3-1.7B-4bit",
        transport=httpx.MockTransport(handler),
    )

    assert result.model_visible is True
    assert result.chat_ok is False
    assert "model 'Qwen3-1.7B-4bit' is still loading" in result.errors
    assert ("POST", "/v1/chat/completions") not in paths


def test_check_omlx_health_sends_bearer_when_api_key_set(monkeypatch) -> None:
    """0.7 management probes (/v1/models/status) need the key on non-loopback binds."""
    monkeypatch.delenv("OMLX_API_KEY", raising=False)
    auth_headers: list[str | None] = []

    def handler(request: httpx.Request) -> httpx.Response:
        auth_headers.append(request.headers.get("authorization"))
        if request.url.path == "/health":
            return httpx.Response(200, json={"status": "ok"})
        if request.url.path == "/v1/models":
            return httpx.Response(200, json={"data": []})
        if request.url.path == "/v1/models/status":
            return httpx.Response(200, json={"ready": True})
        return httpx.Response(404)

    result = check_omlx_health(
        "http://infer-03.lan:8018",
        transport=httpx.MockTransport(handler),
        api_key="secret-key",
    )

    assert result.health_ok is True
    assert result.status_ok is True
    assert auth_headers and set(auth_headers) == {"Bearer secret-key"}


def test_check_omlx_health_falls_back_to_env_api_key(monkeypatch) -> None:
    monkeypatch.setenv("OMLX_API_KEY", "env-key-42")

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/health":
            assert request.headers.get("authorization") == "Bearer env-key-42"
            return httpx.Response(200, json={"status": "ok"})
        return httpx.Response(404)

    result = check_omlx_health("http://infer-03.lan:8018", transport=httpx.MockTransport(handler), include_models=False)

    assert result.health_ok is True


def test_check_omlx_health_omits_header_without_api_key(monkeypatch) -> None:
    monkeypatch.delenv("OMLX_API_KEY", raising=False)

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/health":
            assert "authorization" not in request.headers
            return httpx.Response(200, json={"status": "ok"})
        return httpx.Response(404)

    result = check_omlx_health("http://infer-03.lan:8018", transport=httpx.MockTransport(handler), include_models=False)

    assert result.health_ok is True


def test_smoke_omlx_chat_sends_bearer_when_api_key_set(monkeypatch) -> None:
    monkeypatch.delenv("OMLX_API_KEY", raising=False)

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/health":
            return httpx.Response(200, json={"status": "ok"})
        if request.url.path == "/v1/models":
            return httpx.Response(200, json={"data": [{"id": "mlx-community/test-model"}]})
        if request.url.path == "/v1/chat/completions":
            assert request.headers.get("authorization") == "Bearer chat-key"
            return httpx.Response(200, json={"choices": [{"message": {"content": "pong"}}]})
        return httpx.Response(404)

    result = smoke_omlx_chat(
        "http://infer-03.lan:8018",
        model="mlx-community/test-model",
        transport=httpx.MockTransport(handler),
        api_key="chat-key",
    )

    assert result.chat_ok is True


def test_resolve_latest_omlx_release_returns_tag() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert str(request.url) == "https://api.github.com/repos/jundot/omlx/releases/latest"
        return httpx.Response(200, json={"tag_name": " v0.7.1.dev1 "})

    assert resolve_latest_omlx_release(transport=httpx.MockTransport(handler)) == "v0.7.1.dev1"


def test_resolve_latest_omlx_release_rejects_missing_tag() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={})

    import pytest

    with pytest.raises(ValueError, match="no tag_name"):
        resolve_latest_omlx_release(transport=httpx.MockTransport(handler))


def test_resolve_latest_omlx_release_propagates_http_errors() -> None:
    import pytest

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(502, json={"message": "bad gateway"})

    with pytest.raises(httpx.HTTPStatusError):
        resolve_latest_omlx_release(transport=httpx.MockTransport(handler))


def _auth_node() -> Node:
    return Node(
        host="infer-03.lan",
        ram_gb=128,
        user="shag",
        roles=["inference"],
        runtime=NodeRuntime(type="omlx", port=8018, trusted_network=True),
        home_dir="/Users/shag",
    )


def test_build_omlx_auth_settings_command_merges_and_locks_down() -> None:
    command = build_omlx_auth_settings_command(_auth_node(), api_key="cluster-key-99")

    assert "NODE_HOME=/Users/shag" in command
    assert "SETTINGS_PATH=/Users/shag/.omlx/settings.json" in command
    assert 'auth["api_key"] = os.environ["OMLX_API_KEY"]' in command
    assert 'auth["allow_unauthenticated_inference"] = True' in command
    assert 'auth["skip_api_key_verification"] = False' in command
    assert "os.chmod(temp_path, 0o600)" in command
    assert "os.replace(temp_path, path)" in command
    assert "export OMLX_API_KEY=cluster-key-99" in command
    # Verification step re-reads the file and checks both settings.
    assert "auth settings verification failed" in command


def test_ensure_omlx_auth_settings_applies_and_verifies(monkeypatch) -> None:
    sent: dict = {}

    def fake_ssh_run(user, host, command, *, timeout=60, shell="auto"):
        sent.update(user=user, host=host, command=command, shell=shell)
        return SimpleNamespace(returncode=0, stdout="omlx-auth: verified\n", stderr="")

    monkeypatch.setattr("thunder_forge.cluster.omlx.ssh_run", fake_ssh_run)

    result = ensure_omlx_auth_settings(_auth_node(), api_key="cluster-key-99")

    assert result.ok is True
    assert result.applied is True
    assert result.verified is True
    assert result.errors == []
    assert sent["user"] == "shag"
    assert sent["host"] == "infer-03.lan"
    assert "cluster-key-99" in sent["command"]


def test_ensure_omlx_auth_settings_reports_remote_failure(monkeypatch) -> None:
    def fake_ssh_run(user, host, command, *, timeout=60, shell="auto"):
        return SimpleNamespace(returncode=1, stdout="", stderr="settings.json root must be a JSON object")

    monkeypatch.setattr("thunder_forge.cluster.omlx.ssh_run", fake_ssh_run)

    result = ensure_omlx_auth_settings(_auth_node(), api_key="cluster-key-99")

    assert result.ok is False
    assert result.applied is True
    assert result.verified is False
    assert any("exit code 1" in error for error in result.errors)


def test_ensure_omlx_auth_settings_dry_run_builds_command() -> None:
    result = ensure_omlx_auth_settings(_auth_node(), api_key="cluster-key-99", apply=False)

    assert result.applied is False
    assert result.ok is False
    assert result.command == build_omlx_auth_settings_command(_auth_node(), api_key="cluster-key-99")
