"""CLI tests for Thunder Forge edge smoke commands."""

import json
import os
from pathlib import Path

from typer.testing import CliRunner

from thunder_forge.cli import app
from thunder_forge.cluster.config import ClusterConfig, ServiceConfig
from thunder_forge.cluster.edge import EdgeProxyConfig, EdgeSmokeResult

runner = CliRunner()


def _cluster_config(
    *,
    edge_port: int = 40116,
    olla_port: int = 40115,
    access_log: str = "logs/tf-edge-access.jsonl",
) -> ClusterConfig:
    return ClusterConfig(
        services=ServiceConfig(
            edge_port=edge_port,
            olla_port=olla_port,
            edge_access_log=access_log,
        )
    )


def test_edge_smoke_cli_reads_api_key_from_env_and_prints_summary(monkeypatch) -> None:
    import thunder_forge.cli as cli_module

    monkeypatch.setenv("TF_USER_CLIENT_A", "dev-secret")
    monkeypatch.setattr(cli_module, "_load_config", lambda: (_cluster_config(), Path.cwd()), raising=False)
    monkeypatch.setattr(
        cli_module,
        "smoke_edge_contract",
        lambda *, base_url, api_key, model, prompt, timeout: EdgeSmokeResult(
            base_url=base_url,
            model=model,
            missing_auth_401=True,
            invalid_auth_401=True,
            models_ok=True,
            chat_ok=True,
            session_ok=True,
            latency_ms=37,
            olla_endpoint="msm3-omlx-live",
        ),
        raising=False,
    )

    result = runner.invoke(
        app,
        [
            "edge",
            "smoke",
            "--base-url",
            "http://127.0.0.1:40116",
            "--client-id",
            "client-a",
            "--model",
            "qwen3-1.7b-omlx-msm3-test",
        ],
    )

    assert result.exit_code == 0
    assert "base_url: http://127.0.0.1:40116" in result.stdout
    assert "model: qwen3-1.7b-omlx-msm3-test" in result.stdout
    assert "missing_auth_401: yes" in result.stdout
    assert "invalid_auth_401: yes" in result.stdout
    assert "models: ok" in result.stdout
    assert "chat: ok" in result.stdout
    assert "session: ok" in result.stdout
    assert "olla_endpoint: msm3-omlx-live" in result.stdout
    assert "dev-secret" not in result.stdout


def test_edge_smoke_cli_fails_when_client_api_key_is_missing(monkeypatch) -> None:
    import thunder_forge.cli as cli_module

    monkeypatch.delenv("TF_USER_CLIENT_A", raising=False)
    monkeypatch.setattr(cli_module, "_load_config", lambda: (_cluster_config(), Path.cwd()), raising=False)

    result = runner.invoke(
        app,
        [
            "edge",
            "smoke",
            "--base-url",
            "http://127.0.0.1:40116",
            "--client-id",
            "client-a",
            "--model",
            "qwen3-1.7b-omlx-msm3-test",
        ],
    )

    assert result.exit_code == 1
    assert "Error: TF_USER_CLIENT_A is not set" in result.stderr


def test_edge_serve_cli_builds_proxy_config_from_env_without_printing_key(monkeypatch) -> None:
    import thunder_forge.cli as cli_module

    captured: dict[str, object] = {}
    for env_name in list(os.environ):
        if env_name.startswith("TF_USER_"):
            monkeypatch.delenv(env_name, raising=False)
    monkeypatch.setenv("TF_USER_CLIENT_A", "secret-a")
    monkeypatch.setenv("TF_USER_CLIENT_B", "secret-b")
    monkeypatch.setattr(cli_module, "_load_config", lambda: (_cluster_config(), Path.cwd()), raising=False)
    monkeypatch.setattr(cli_module, "_load_repo_dotenv", lambda: (Path.cwd(), Path.cwd() / ".env"), raising=False)

    def fake_serve_edge_proxy(*, host: str, port: int, config: EdgeProxyConfig) -> None:
        captured["host"] = host
        captured["port"] = port
        captured["config"] = config

    monkeypatch.setattr(cli_module, "serve_edge_proxy", fake_serve_edge_proxy, raising=False)

    result = runner.invoke(
        app,
        [
            "edge",
            "serve",
            "--host",
            "127.0.0.1",
            "--port",
            "40116",
            "--olla-base-url",
            "http://127.0.0.1:40115",
        ],
    )

    assert result.exit_code == 0
    assert captured["host"] == "127.0.0.1"
    assert captured["port"] == 40116
    config = captured["config"]
    assert isinstance(config, EdgeProxyConfig)
    assert config.olla_base_url == "http://127.0.0.1:40115"
    assert config.clients_by_key["secret-a"].client_id == "client_a"
    assert config.clients_by_key["secret-b"].client_id == "client_b"
    assert "clients: client_a, client_b" in result.stdout
    assert "api_key_count: 2" in result.stdout
    assert "secret-a" not in result.stdout
    assert "secret-b" not in result.stdout


def test_edge_serve_cli_uses_config_defaults(monkeypatch) -> None:
    import thunder_forge.cli as cli_module

    captured: dict[str, object] = {}
    monkeypatch.setenv("TF_USER_CLIENT_A", "secret-a")
    monkeypatch.setattr(
        cli_module,
        "_load_config",
        lambda: (_cluster_config(edge_port=45116, olla_port=45115), Path.cwd()),
        raising=False,
    )

    def fake_serve_edge_proxy(*, host: str, port: int, config: EdgeProxyConfig) -> None:
        captured["host"] = host
        captured["port"] = port
        captured["config"] = config

    monkeypatch.setattr(cli_module, "serve_edge_proxy", fake_serve_edge_proxy, raising=False)

    result = runner.invoke(app, ["edge", "serve"])

    assert result.exit_code == 0
    assert captured["host"] == "127.0.0.1"
    assert captured["port"] == 45116
    config = captured["config"]
    assert isinstance(config, EdgeProxyConfig)
    assert config.olla_base_url == "http://127.0.0.1:45115"
    assert "serving_edge: http://127.0.0.1:45116" in result.stdout
    assert "olla_base_url: http://127.0.0.1:45115" in result.stdout


def test_edge_smoke_cli_uses_config_default(monkeypatch) -> None:
    import thunder_forge.cli as cli_module

    captured: dict[str, str] = {}
    monkeypatch.setenv("TF_USER_CLIENT_A", "dev-secret")
    monkeypatch.setattr(
        cli_module,
        "_load_config",
        lambda: (_cluster_config(edge_port=45116), Path.cwd()),
        raising=False,
    )

    def fake_smoke_edge_contract(*, base_url, api_key, model, prompt, timeout):
        captured["base_url"] = base_url
        return EdgeSmokeResult(
            base_url=base_url,
            model=model,
            missing_auth_401=True,
            invalid_auth_401=True,
            models_ok=True,
            chat_ok=True,
            session_ok=True,
        )

    monkeypatch.setattr(cli_module, "smoke_edge_contract", fake_smoke_edge_contract, raising=False)

    result = runner.invoke(app, ["edge", "smoke", "--client-id", "client-a", "--model", "memory"])

    assert result.exit_code == 0
    assert captured["base_url"] == "http://127.0.0.1:45116"
    assert "base_url: http://127.0.0.1:45116" in result.stdout


def test_edge_keys_cli_generates_user_hash_without_printing_secrets(tmp_path, monkeypatch) -> None:
    import thunder_forge.cli as cli_module

    env_file = tmp_path / ".env"
    monkeypatch.setattr(cli_module, "_load_repo_dotenv", lambda: (tmp_path, env_file), raising=False)

    result = runner.invoke(app, ["edge", "keys", "--client", "client-a", "--client", "client-b"])

    assert result.exit_code == 0
    assert "users_env: TF_USER_" in result.stdout
    assert "client: client-a" in result.stdout
    assert "client: client-b" in result.stdout
    assert "secrets_printed: no" in result.stdout
    content = env_file.read_text()
    assert "TF_USER_CLIENT_A=" in content
    assert "TF_USER_CLIENT_B=" in content
    for line in content.splitlines():
        if line.startswith("TF_USER_"):
            _, _, api_key = line.partition("=")
            assert api_key not in result.stdout


def test_edge_usage_cli_summarizes_access_log_by_client(tmp_path, monkeypatch) -> None:
    import thunder_forge.cli as cli_module

    log_path = tmp_path / "access.jsonl"
    log_path.write_text(
        json.dumps(
            {
                "client_id": "client-b",
                "path": "/v1/chat/completions",
                "model": "memory",
                "status_code": 200,
                "latency_ms": 25,
                "olla_endpoint": "msm3-omlx-live",
            }
        )
        + "\n"
    )
    monkeypatch.setattr(cli_module, "_load_config", lambda: (_cluster_config(), tmp_path), raising=False)

    result = runner.invoke(app, ["edge", "usage", "--access-log", str(log_path)])

    assert result.exit_code == 0
    assert f"access_log: {log_path}" in result.stdout
    assert "requests_total: 1" in result.stdout
    assert "client_id: client-b" in result.stdout
    assert "memory: 1" in result.stdout
    assert "msm3-omlx-live: 1" in result.stdout
