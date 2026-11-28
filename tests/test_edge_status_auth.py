"""Anonymous /status is a liveness payload; the admin token unlocks the detail."""

from __future__ import annotations

import json
import socket
import threading
from pathlib import Path
from types import SimpleNamespace

import httpx
import pytest

from thunder_forge.cluster import edge
from thunder_forge.cluster.config import (
    ClusterConfig,
    Model,
    ModelSource,
    Node,
    NodeRole,
    NodeRuntime,
    RuntimeType,
    ServiceConfig,
)
from thunder_forge.cluster.omlx import OmlxHealthResult

FORBIDDEN_ANONYMOUS_KEYS = (
    "active_edge_requests",
    "model_statuses",
    "actual_size",
    "idle_seconds",
    "last_access",
    "host",
    "public_host",
    "admin_url",
    "errors",
    # live load and memory turnover, gated behind the admin token
    "active_requests",
    "waiting_requests",
    "traffic_state",
    "recently_used_models",
    "stale_loaded_models",
    "recent_idle_threshold_seconds",
    # ordered by last_access, i.e. alias-level activity
    "hot_loaded_models",
)


@pytest.fixture
def status_config_ssh_calls() -> list[str]:
    return []


@pytest.fixture
def status_config(monkeypatch, tmp_path, status_config_ssh_calls) -> edge.EdgeProxyConfig:
    """One healthy inference node behind a gateway, with every live probe faked."""
    cluster_config = ClusterConfig(
        services=ServiceConfig(edge_port=40116, olla_port=40115),
        models={
            "memory": Model(
                source=ModelSource(type="huggingface", repo="mlx-community/gpt-oss-20b-mxfp4-bf16"),
                runtime_model_id="gpt-oss-20b-mxfp4-bf16",
            )
        },
        nodes={
            "rock": Node(host="rock.lan", ram_gb=32, roles=[NodeRole.GATEWAY], user="shag"),
            "msm1": Node(
                host="msm1.lan",
                ram_gb=128,
                roles=[NodeRole.INFERENCE],
                user="shag",
                runtime=NodeRuntime(type=RuntimeType.OMLX, port=8018),
                models=["memory"],
            ),
        },
    )
    config = edge.EdgeProxyConfig(
        olla_base_url="http://rock.lan:40115",
        clients_by_key={},
        cluster_config=cluster_config,
        repo_root=tmp_path,
    )
    config.active_requests["req-1"] = ("alice", "memory")
    monkeypatch.setattr(edge, "_latest_olla_release_version", lambda *, timeout=5.0: "v0.0.28")
    monkeypatch.setattr(edge, "_latest_omlx_release_version", lambda *, timeout=5.0: "v0.4.2")
    monkeypatch.setattr(edge, "_gateway_olla_version", lambda _config: "v0.0.27")
    def fake_macos_version(node, *, timeout):
        status_config_ssh_calls.append(node.host)
        return "15.6.1"

    monkeypatch.setattr(edge, "_macos_version", fake_macos_version)
    monkeypatch.setattr(edge, "_omlx_version", lambda _node, *, timeout: "0.4.2.dev2")
    monkeypatch.setattr(
        edge,
        "check_omlx_health",
        lambda base_url, **kwargs: OmlxHealthResult(
            base_url=base_url,
            health_ok=True,
            models_ok=True,
            models=["gpt-oss-20b-mxfp4-bf16"],
            model_statuses={
                "gpt-oss-20b-mxfp4-bf16": {
                    "id": "gpt-oss-20b-mxfp4-bf16",
                    "loaded": True,
                    "last_access": 200.0,
                    "actual_size": 13 * 1024**3,
                }
            },
        ),
    )
    return config


@pytest.fixture
def get_status(monkeypatch):
    def run(config: edge.EdgeProxyConfig, *, token: str | None = None) -> tuple[int, dict[str, object]]:
        handlers: list[object] = []

        def server(_address, handler):
            handlers.append(handler)
            return SimpleNamespace(serve_forever=lambda: None)

        monkeypatch.setattr(edge, "ThreadingHTTPServer", server)
        edge.serve_edge_proxy(host="127.0.0.1", port=0, config=config)
        auth = f"Authorization: Bearer {token}\r\n" if token else ""
        request = f"GET /status HTTP/1.0\r\n{auth}\r\n".encode()
        client, connection = socket.socketpair()
        errors: list[BaseException] = []

        def handle() -> None:
            try:
                handlers[0](connection, ("127.0.0.1", 1234), None)
            except Exception as exc:  # noqa: BLE001 - surfaced below
                errors.append(exc)
            finally:
                connection.close()

        with client:
            client.settimeout(5)
            client.sendall(request)
            client.shutdown(socket.SHUT_WR)
            thread = threading.Thread(target=handle, daemon=True)
            thread.start()
            try:
                with client.makefile("rb") as response:
                    wire = response.read()
            finally:
                thread.join(timeout=5)
        assert not thread.is_alive()
        assert not errors
        head, _, body = wire.partition(b"\r\n\r\n")
        return int(head.split(b" ")[1]), json.loads(body or b"{}")

    return run


def _walk_keys(value: object) -> list[str]:
    if isinstance(value, dict):
        return [str(key) for key in value] + [
            nested for item in value.values() for nested in _walk_keys(item)
        ]
    if isinstance(value, list):
        return [nested for item in value for nested in _walk_keys(item)]
    return []


def _assert_no_detail(payload: dict[str, object]) -> None:
    keys = _walk_keys(payload)
    for forbidden in FORBIDDEN_ANONYMOUS_KEYS:
        assert forbidden not in keys, f"anonymous /status leaked {forbidden}"


def test_anonymous_status_hides_clients_memory_layout_and_node_addressing(
    status_config, get_status, status_config_ssh_calls
) -> None:
    status, payload = get_status(status_config)

    assert status == 200
    _assert_no_detail(payload)
    assert payload["ok"] is True
    assert payload["target"] == "all"
    assert payload["inference"][0]["name"] == "msm1"
    assert payload["inference"][0]["health"] == "ok"
    assert payload["inference"][0]["models"] == "ok"
    assert payload["inference"][0]["omlx_version"] == "0.4.2.dev2"
    # An anonymous answer skips the SSH probe that fills macos_version.
    assert payload["inference"][0]["macos_version"] == "unknown"
    assert status_config_ssh_calls == []
    assert payload["inference"][0]["served_models"] == ["memory"]
    assert payload["gateway"]["name"] == "rock"
    assert payload["gateway"]["olla_version"] == "v0.0.27"
    assert set(payload["summary"]) == {
        "inference_total",
        "inference_healthy",
        "loaded_models",
        "readiness",
        "latest_omlx_version",
        "omlx_upgrade_hint",
    }
    assert payload["summary"]["loaded_models"] == 1
    assert payload["build"] == {"version": "unknown", "commit": "unknown", "dirty": "unknown"}


def test_status_with_admin_token_returns_the_full_detail(
    status_config, get_status, status_config_ssh_calls
) -> None:
    status_config.usage_admin_token = "admin-secret"

    status, payload = get_status(status_config, token="admin-secret")

    assert status == 200
    assert payload["active_edge_requests"] == [{"client_id": "alice", "model": "memory"}]
    node = payload["inference"][0]
    assert node["macos_version"] == "15.6.1"
    assert status_config_ssh_calls == ["msm1.lan"]
    assert node["host"] == "msm1.lan"
    assert node["admin_url"] == "http://msm1.lan:8018/admin"
    assert node["errors"] == []
    assert node["model_statuses"][0]["runtime_id"] == "gpt-oss-20b-mxfp4-bf16"
    assert node["model_statuses"][0]["actual_size"] == 13 * 1024**3
    assert node["model_statuses"][0]["idle_seconds"] >= 0


def test_status_with_wrong_token_is_not_elevated_and_never_errors(status_config, get_status) -> None:
    status_config.usage_admin_token = "admin-secret"

    missing_status, missing_payload = get_status(status_config)
    wrong_status, wrong_payload = get_status(status_config, token="wrong")

    assert missing_status == wrong_status == 200
    _assert_no_detail(missing_payload)
    _assert_no_detail(wrong_payload)
    assert wrong_payload["ok"] is True
    assert wrong_payload["inference"][0]["name"] == "msm1"


def test_status_stays_anonymous_when_the_edge_has_no_admin_token(
    status_config, get_status
) -> None:
    """A missing edge token must not turn /status into a 401 or a 500."""
    status, payload = get_status(status_config, token="stale-local-token")

    assert status == 200
    _assert_no_detail(payload)
    assert payload["ok"] is True


def test_anonymous_status_does_not_name_the_failing_host(monkeypatch, status_config, get_status) -> None:
    """A /status failure must not hand an anonymous caller a node host or port."""

    def boom(*, config, target, detail=True):
        raise OSError("ssh: Could not resolve hostname msm1.lan")

    monkeypatch.setattr(edge, "build_edge_status_payload", boom)

    status, anonymous = get_status(status_config)
    assert status == 503
    assert anonymous == {"error": "status_unavailable"}

    status_config.usage_admin_token = "admin-secret"
    status, elevated = get_status(status_config, token="admin-secret")
    assert status == 503
    assert elevated == {"error": "status_unavailable: ssh: Could not resolve hostname msm1.lan"}


def test_cluster_status_cli_sends_the_admin_token_when_the_env_has_one(
    status_config, monkeypatch
) -> None:
    from thunder_forge import cli as cli_module

    seen: list[dict[str, str] | None] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(dict(request.headers) if "authorization" in request.headers else None)
        return httpx.Response(200, json={"ok": True, "target": "all", "summary": {}, "inference": []})

    monkeypatch.setenv("TF_EDGE_ADMIN_TOKEN", "admin-secret")
    real_client = httpx.Client
    monkeypatch.setattr(
        cli_module.httpx,
        "Client",
        lambda **kwargs: real_client(transport=httpx.MockTransport(handler), **kwargs),
    )

    cli_module._fetch_cluster_status_payload(status_config.cluster_config, target=None)

    assert seen[0] is not None
    assert seen[0]["authorization"] == "Bearer admin-secret"


def test_cluster_status_cli_degrades_on_the_reduced_anonymous_payload(
    status_config, monkeypatch, get_status
) -> None:
    """The reduced payload still prints instead of failing the command."""
    from typer.testing import CliRunner

    from thunder_forge import cli as cli_module

    _, anonymous = get_status(status_config)
    monkeypatch.delenv("TF_EDGE_ADMIN_TOKEN", raising=False)
    monkeypatch.setattr(cli_module, "_load_config", lambda: (status_config.cluster_config, Path(".")))
    real_client = httpx.Client
    monkeypatch.setattr(
        cli_module.httpx,
        "Client",
        lambda **kwargs: real_client(
            transport=httpx.MockTransport(lambda request: httpx.Response(200, json=anonymous)),
            **kwargs,
        ),
    )
    monkeypatch.setattr(cli_module, "_enrich_status_with_tf_users", lambda *args, **kwargs: None)

    result = CliRunner().invoke(cli_module.app, ["cluster", "status", "--plain"])

    assert result.exit_code == 0, result.output
    assert "Thunder Forge cluster status" in result.stdout
    assert "msm1" in result.stdout
