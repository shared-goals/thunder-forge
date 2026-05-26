"""CLI tests for Thunder Forge edge smoke commands."""

from typer.testing import CliRunner

from thunder_forge.cli import app
from thunder_forge.cluster.edge import EdgeProxyConfig, EdgeSmokeResult

runner = CliRunner()


def test_edge_smoke_cli_reads_api_key_from_env_and_prints_summary(monkeypatch) -> None:
    import thunder_forge.cli as cli_module

    monkeypatch.setenv("TF_DEV_EDGE_KEY", "dev-secret")
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
            "--api-key-env",
            "TF_DEV_EDGE_KEY",
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


def test_edge_smoke_cli_fails_when_api_key_env_is_missing(monkeypatch) -> None:
    monkeypatch.delenv("TF_DEV_EDGE_KEY", raising=False)

    result = runner.invoke(
        app,
        [
            "edge",
            "smoke",
            "--base-url",
            "http://127.0.0.1:40116",
            "--api-key-env",
            "TF_DEV_EDGE_KEY",
            "--model",
            "qwen3-1.7b-omlx-msm3-test",
        ],
    )

    assert result.exit_code == 1
    assert "Error: TF_DEV_EDGE_KEY is not set" in result.stderr


def test_edge_serve_cli_builds_proxy_config_from_env_without_printing_key(monkeypatch) -> None:
    import thunder_forge.cli as cli_module

    captured: dict[str, object] = {}
    monkeypatch.setenv("TF_DEV_EDGE_KEY", "dev-secret")

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
            "--api-key-env",
            "TF_DEV_EDGE_KEY",
            "--client-id",
            "shag-dev",
        ],
    )

    assert result.exit_code == 0
    assert captured["host"] == "127.0.0.1"
    assert captured["port"] == 40116
    config = captured["config"]
    assert isinstance(config, EdgeProxyConfig)
    assert config.olla_base_url == "http://127.0.0.1:40115"
    assert config.clients_by_key["dev-secret"].client_id == "shag-dev"
    assert "dev-secret" not in result.stdout
