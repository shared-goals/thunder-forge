"""CLI tests for Thunder Forge edge smoke commands."""

import json
import os
import threading
from pathlib import Path

from typer.testing import CliRunner

from thunder_forge.cli import app
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
from thunder_forge.cluster.edge import EdgeProxyConfig, EdgeSmokeResult

runner = CliRunner()


def _parse_jsonc(text: str) -> dict[str, object]:
    json_only = "\n".join(line for line in text.splitlines() if not line.lstrip().startswith("//"))
    return json.loads(json_only)


def _parse_json(text: str) -> object:
    return json.loads(text)


def test_osc52_clipboard_sequence_encodes_payload() -> None:
    from thunder_forge.cli import _multiplexer_aware_osc52_sequence

    assert _multiplexer_aware_osc52_sequence("hello", env={}) == "\033]52;c;aGVsbG8=\a"


def test_osc52_clipboard_sequence_wraps_tmux_passthrough() -> None:
    from thunder_forge.cli import _multiplexer_aware_osc52_sequence

    assert (
        _multiplexer_aware_osc52_sequence("hello", env={"TMUX": "/tmp/tmux-501/default,1,0"})
        == "\033Ptmux;\033\033]52;c;aGVsbG8=\a\033\\"
    )


def test_osc52_clipboard_sequence_wraps_tmux_when_su_keeps_only_term() -> None:
    from thunder_forge.cli import _multiplexer_aware_osc52_sequence

    assert (
        _multiplexer_aware_osc52_sequence("hello", env={"TERM": "screen-256color"})
        == "\033Ptmux;\033\033]52;c;aGVsbG8=\a\033\\"
    )


def test_osc52_clipboard_sequence_wraps_screen_passthrough() -> None:
    from thunder_forge.cli import _multiplexer_aware_osc52_sequence

    assert (
        _multiplexer_aware_osc52_sequence("hello", env={"TERM": "screen-256color", "STY": "123.session"})
        == "\033P\033]52;c;aGVsbG8=\a\033\\"
    )


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

    monkeypatch.setenv("TF_USER_CLIENT_DASH_A", "dev-secret")
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
            olla_endpoint="infer-03-omlx-live",
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
            "qwen3-1.7b-omlx-infer-03-test",
        ],
    )

    assert result.exit_code == 0
    assert "base_url: http://127.0.0.1:40116" in result.stdout
    assert "model: qwen3-1.7b-omlx-infer-03-test" in result.stdout
    assert "missing_auth_401: yes" in result.stdout
    assert "invalid_auth_401: yes" in result.stdout
    assert "models: ok" in result.stdout
    assert "chat: ok" in result.stdout
    assert "session: ok" in result.stdout
    assert "olla_endpoint: infer-03-omlx-live" in result.stdout
    assert "dev-secret" not in result.stdout


def test_edge_smoke_cli_fails_when_client_api_key_is_missing(monkeypatch) -> None:
    import thunder_forge.cli as cli_module

    monkeypatch.delenv("TF_USER_CLIENT_DASH_A", raising=False)
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
            "qwen3-1.7b-omlx-infer-03-test",
        ],
    )

    assert result.exit_code == 1
    assert "Error: TF_USER_CLIENT_DASH_A is not set" in result.stderr


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


def test_edge_serve_cli_exposes_tf_alias_model_catalog(monkeypatch) -> None:
    import thunder_forge.cli as cli_module

    captured: dict[str, object] = {}
    monkeypatch.setenv("TF_USER_CLIENT_A", "secret-a")
    monkeypatch.setattr(
        cli_module,
        "_load_config",
        lambda: (
            ClusterConfig(
                services=ServiceConfig(edge_port=45116, olla_port=45115),
                models={
                    "coder": Model(
                        source=ModelSource(type="huggingface", repo="mlx-community/Qwen3-Coder-Next-4bit"),
                        runtime_model_id="Qwen3-Coder-Next-4bit",
                        max_context=262144,
                        notes="Coding role.",
                    )
                },
                nodes={
                    "infer-03": Node(
                        host="infer-03.lan",
                        ram_gb=128,
                        roles=[NodeRole.INFERENCE],
                        runtime=NodeRuntime(type=RuntimeType.OMLX, port=8018),
                        models=["coder"],
                    )
                },
            ),
            Path.cwd(),
        ),
        raising=False,
    )

    def fake_serve_edge_proxy(*, host: str, port: int, config: EdgeProxyConfig) -> None:
        captured["config"] = config

    monkeypatch.setattr(cli_module, "serve_edge_proxy", fake_serve_edge_proxy, raising=False)

    result = runner.invoke(app, ["edge", "serve"])

    assert result.exit_code == 0
    config = captured["config"]
    assert isinstance(config, EdgeProxyConfig)
    assert [model.id for model in config.model_catalog] == ["coder"]
    assert config.model_catalog[0].name == "coder"
    assert "mlx-community/Qwen3-Coder-Next-4bit" in config.model_catalog[0].description
    assert config.model_catalog[0].runtime_model_id == "Qwen3-Coder-Next-4bit"


def test_edge_serve_cli_reports_node_metrics_collector_errors(monkeypatch) -> None:
    import thunder_forge.cli as cli_module

    observed = threading.Event()
    monkeypatch.setenv("TF_USER_CLIENT_A", "secret-a")
    monkeypatch.setattr(cli_module, "_load_config", lambda: (_cluster_config(), Path.cwd()), raising=False)

    def fake_collect_node_metric_snapshots(*args, **kwargs):
        observed.set()
        raise RuntimeError("metrics unavailable")

    def fake_serve_edge_proxy(*, host: str, port: int, config: EdgeProxyConfig) -> None:
        assert observed.wait(timeout=1.0)

    monkeypatch.setattr(cli_module, "_collect_node_metric_snapshots", fake_collect_node_metric_snapshots)
    monkeypatch.setattr(cli_module, "serve_edge_proxy", fake_serve_edge_proxy, raising=False)

    result = runner.invoke(app, ["edge", "serve", "--metrics-interval-seconds", "60"])

    assert result.exit_code == 0
    assert "node_metrics_collector_error: metrics unavailable" in result.stdout


def test_edge_client_config_opencode_prints_assigned_aliases(monkeypatch) -> None:
    import thunder_forge.cli as cli_module

    monkeypatch.setattr(
        cli_module,
        "_load_config",
        lambda: (
            ClusterConfig(
                services=ServiceConfig(edge_port=45116, olla_port=45115),
                models={
                    "memory": Model(
                        source=ModelSource(type="huggingface", repo="mlx-community/gpt-oss-20b-MXFP4-Q8"),
                        runtime_model_id="gpt-oss-20b-MXFP4-Q8",
                        max_context=131072,
                    ),
                    "memory-bf16": Model(
                        source=ModelSource(type="huggingface", repo="mlx-community/gpt-oss-20b-mxfp4-bf16"),
                        runtime_model_id="gpt-oss-20b-mxfp4-bf16",
                        max_context=131072,
                        benchmark_only=True,
                    ),
                    "unassigned": Model(
                        source=ModelSource(type="huggingface", repo="mlx-community/unassigned"),
                        runtime_model_id="unassigned",
                    ),
                },
                nodes={
                    "gateway-cache-01": Node(
                        host="gateway-cache-01.lan",
                        ram_gb=128,
                        roles=[NodeRole.GATEWAY, NodeRole.CACHE],
                    ),
                    "infer-03": Node(
                        host="infer-03.lan",
                        ram_gb=128,
                        roles=[NodeRole.INFERENCE],
                        runtime=NodeRuntime(type=RuntimeType.OMLX, port=8018),
                        models=["memory", "memory-bf16"],
                    ),
                },
            ),
            Path.cwd(),
        ),
        raising=False,
    )

    result = runner.invoke(
        app,
        [
            "edge",
            "client-config",
            "opencode",
            "--base-url",
            "http://studio:40116/v1",
            "--model",
            "memory",
        ],
    )

    assert result.exit_code == 0
    payload = _parse_jsonc(result.stdout)
    provider = payload["provider"]["thunder-forge"]
    assert payload["model"] == "thunder-forge/memory"
    assert provider["options"]["baseURL"] == "http://studio:40116/v1"
    assert provider["options"]["apiKey"] == "{env:TF_USER_OPENCODE}"
    assert sorted(provider["models"]) == ["memory", "memory-bf16"]
    assert provider["models"]["memory"]["name"] == "memory"
    assert provider["models"]["memory"]["limit"]["context"] == 131072
    assert provider["models"]["memory-bf16"]["name"] == "memory-bf16"
    assert provider["models"]["memory-bf16"]["status"] == "beta"
    assert provider["models"]["memory-bf16"]["limit"]["context"] == 131072
    assert "headers" not in provider["models"]["memory"]
    assert "headers" not in provider["models"]["memory-bf16"]


def test_edge_client_config_opencode_client_uses_env_placeholder(monkeypatch) -> None:
    import thunder_forge.cli as cli_module

    monkeypatch.setattr(
        cli_module,
        "_load_config",
        lambda: (
            ClusterConfig(
                models={
                    "memory": Model(
                        source=ModelSource(type="huggingface", repo="mlx-community/gpt-oss-20b-MXFP4-Q8"),
                        runtime_model_id="gpt-oss-20b-MXFP4-Q8",
                    )
                },
                nodes={
                    "infer-03": Node(
                        host="infer-03.lan",
                        ram_gb=128,
                        roles=[NodeRole.INFERENCE],
                        runtime=NodeRuntime(type=RuntimeType.OMLX, port=8018),
                        models=["memory"],
                    )
                },
            ),
            Path.cwd(),
        ),
        raising=False,
    )

    result = runner.invoke(app, ["edge", "client-config", "opencode", "shag"])

    assert result.exit_code == 0
    payload = _parse_jsonc(result.stdout)
    provider = payload["provider"]["thunder-forge"]
    assert provider["options"]["apiKey"] == "{env:TF_USER_SHAG}"
    assert provider["models"]["memory"]["headers"]["X-Olla-Session-ID"] == "memory-shag"


def test_edge_client_config_opencode_jsonc_comments_api_key_client(monkeypatch) -> None:
    import thunder_forge.cli as cli_module

    monkeypatch.setattr(
        cli_module,
        "_load_config",
        lambda: (
            ClusterConfig(
                models={
                    "memory": Model(
                        source=ModelSource(type="huggingface", repo="mlx-community/gpt-oss-20b-MXFP4-Q8"),
                        runtime_model_id="gpt-oss-20b-MXFP4-Q8",
                    )
                },
                nodes={
                    "infer-03": Node(
                        host="infer-03.lan",
                        ram_gb=128,
                        roles=[NodeRole.INFERENCE],
                        runtime=NodeRuntime(type=RuntimeType.OMLX, port=8018),
                        models=["memory"],
                    )
                },
            ),
            Path.cwd(),
        ),
        raising=False,
    )

    result = runner.invoke(app, ["edge", "client-config", "opencode", "shag"])

    assert result.exit_code == 0
    payload = _parse_jsonc(result.stdout)
    provider = payload["provider"]["thunder-forge"]
    assert provider["options"]["apiKey"] == "{env:TF_USER_SHAG}"
    assert "// TF_USER_SHAG: check .env" in result.stdout
    assert 'X-Olla-Session-ID' in result.stdout
    assert 'memory-shag' in result.stdout


def test_edge_client_config_opencode_injects_created_key(monkeypatch, tmp_path) -> None:
    import thunder_forge.cli as cli_module

    env_file = tmp_path / ".env"
    monkeypatch.delenv("TF_USER_SHAG", raising=False)
    monkeypatch.setattr(
        cli_module,
        "_load_repo_dotenv",
        lambda: (tmp_path, env_file),
        raising=False,
    )
    monkeypatch.setattr(
        cli_module,
        "_load_config",
        lambda: (
            ClusterConfig(
                models={
                    "memory": Model(
                        source=ModelSource(type="huggingface", repo="mlx-community/gpt-oss-20b-MXFP4-Q8"),
                        runtime_model_id="gpt-oss-20b-MXFP4-Q8",
                    )
                },
                nodes={
                    "infer-03": Node(
                        host="infer-03.lan",
                        ram_gb=128,
                        roles=[NodeRole.INFERENCE],
                        runtime=NodeRuntime(type=RuntimeType.OMLX, port=8018),
                        models=["memory"],
                    )
                },
            ),
            tmp_path,
        ),
        raising=False,
    )

    result = runner.invoke(
        app,
        [
            "edge",
            "client-config",
            "opencode",
            "shag",
            "--inject-api-key",
            "--create-missing-key",
            "--yes",
        ],
    )

    assert result.exit_code == 0
    env_text = env_file.read_text()
    env_prefix = "TF_USER_SHAG="
    assert env_prefix in env_text
    api_key = next(line.removeprefix(env_prefix) for line in env_text.splitlines() if line.startswith(env_prefix))
    payload = _parse_jsonc(result.stdout)
    provider = payload["provider"]["thunder-forge"]
    assert provider["options"]["apiKey"] == api_key
    assert "{env:" not in provider["options"]["apiKey"]
    assert provider["models"]["memory"]["headers"]["X-Olla-Session-ID"] == "memory-shag"


def test_edge_client_config_copy_uses_generated_payload(monkeypatch) -> None:
    import thunder_forge.cli as cli_module

    copied: list[str] = []
    monkeypatch.setattr(cli_module, "_copy_to_clipboard", copied.append, raising=False)
    monkeypatch.setattr(
        cli_module,
        "_load_config",
        lambda: (
            ClusterConfig(
                models={
                    "memory": Model(
                        source=ModelSource(type="huggingface", repo="mlx-community/gpt-oss-20b-MXFP4-Q8"),
                        runtime_model_id="gpt-oss-20b-MXFP4-Q8",
                    )
                },
                nodes={
                    "infer-03": Node(
                        host="infer-03.lan",
                        ram_gb=128,
                        roles=[NodeRole.INFERENCE],
                        runtime=NodeRuntime(type=RuntimeType.OMLX, port=8018),
                        models=["memory"],
                    )
                },
            ),
            Path.cwd(),
        ),
        raising=False,
    )

    result = runner.invoke(app, ["edge", "client-config", "opencode", "--copy"])

    assert result.exit_code == 0
    assert copied == [result.stdout]
    assert "copied OpenCode config to clipboard" in result.stderr


def test_edge_client_config_vscode_copy_prints_api_key_update_hint(monkeypatch) -> None:
    import thunder_forge.cli as cli_module

    copied: list[str] = []
    monkeypatch.setattr(cli_module, "_copy_to_clipboard", copied.append, raising=False)
    monkeypatch.setattr(
        cli_module,
        "_load_config",
        lambda: (
            ClusterConfig(
                models={
                    "memory": Model(
                        source=ModelSource(type="huggingface", repo="mlx-community/gpt-oss-20b-MXFP4-Q8"),
                        runtime_model_id="gpt-oss-20b-MXFP4-Q8",
                        max_context=131072,
                    )
                },
                nodes={
                    "infer-03": Node(
                        host="infer-03.lan",
                        ram_gb=128,
                        roles=[NodeRole.INFERENCE],
                        runtime=NodeRuntime(type=RuntimeType.OMLX, port=8018),
                        models=["memory"],
                    )
                },
            ),
            Path.cwd(),
        ),
        raising=False,
    )

    result = runner.invoke(app, ["edge", "client-config", "vscode", "--copy"])

    assert result.exit_code == 0
    assert copied == [result.stdout]
    assert "copied VS Code config to clipboard" in result.stderr
    assert "Update API Key with VS Code menu item in Language models management panel" in result.stderr


def test_edge_client_config_opencode_jsonc_comments_show_backing_models(monkeypatch) -> None:
    import thunder_forge.cli as cli_module

    monkeypatch.setattr(
        cli_module,
        "_load_config",
        lambda: (
            ClusterConfig(
                models={
                    "memory-bf16": Model(
                        source=ModelSource(type="huggingface", repo="mlx-community/gpt-oss-20b-mxfp4-bf16"),
                        runtime_model_id="gpt-oss-20b-mxfp4-bf16",
                        disk_gb=13.0,
                        benchmark_only=True,
                    )
                },
                nodes={
                    "infer-03": Node(
                        host="infer-03.lan",
                        ram_gb=128,
                        roles=[NodeRole.INFERENCE],
                        runtime=NodeRuntime(type=RuntimeType.OMLX, port=8018),
                        models=["memory-bf16"],
                    )
                },
            ),
            Path.cwd(),
        ),
        raising=False,
    )

    result = runner.invoke(app, ["edge", "client-config", "opencode"])

    assert result.exit_code == 0
    payload = _parse_jsonc(result.stdout)
    provider = payload["provider"]["thunder-forge"]
    assert "memory-bf16" in provider["models"]
    assert "// mlx-community/gpt-oss-20b-mxfp4-bf16; disk_gb: 13.0" in result.stdout
    assert '"memory-bf16": {' in result.stdout
    assert '"name": "memory-bf16"' in result.stdout
    assert '"status": "beta"' in result.stdout


def test_edge_client_config_opencode_jsonc_comments_show_disk_gb_when_configured(monkeypatch) -> None:
    import thunder_forge.cli as cli_module

    monkeypatch.setattr(
        cli_module,
        "_load_config",
        lambda: (
            ClusterConfig(
                models={
                    "memory": Model(
                        source=ModelSource(type="huggingface", repo="mlx-community/gpt-oss-20b-mxfp4-bf16"),
                        runtime_model_id="gpt-oss-20b-mxfp4-bf16",
                        disk_gb=13.0,
                    )
                },
                nodes={
                    "infer-03": Node(
                        host="infer-03.lan",
                        ram_gb=128,
                        roles=[NodeRole.INFERENCE],
                        runtime=NodeRuntime(type=RuntimeType.OMLX, port=8018),
                        models=["memory"],
                    )
                },
            ),
            Path.cwd(),
        ),
        raising=False,
    )

    result = runner.invoke(app, ["edge", "client-config", "opencode"])

    assert result.exit_code == 0
    assert "// mlx-community/gpt-oss-20b-mxfp4-bf16; disk_gb: 13.0" in result.stdout


def test_edge_client_config_vscode_prints_model_config_with_token_budget_and_secret_injection(
    monkeypatch,
    tmp_path,
) -> None:
    import thunder_forge.cli as cli_module

    env_file = tmp_path / ".env"
    monkeypatch.delenv("TF_USER_SHAG", raising=False)
    monkeypatch.setattr(cli_module, "_load_repo_dotenv", lambda: (tmp_path, env_file), raising=False)
    monkeypatch.setattr(
        cli_module,
        "_load_config",
        lambda: (
            ClusterConfig(
                models={
                    "memory": Model(
                        source=ModelSource(type="huggingface", repo="mlx-community/gpt-oss-20b-MXFP4-Q8"),
                        runtime_model_id="gpt-oss-20b-MXFP4-Q8",
                        max_context=131072,
                    )
                },
                nodes={
                    "infer-03": Node(
                        host="infer-03.lan",
                        ram_gb=128,
                        roles=[NodeRole.INFERENCE],
                        runtime=NodeRuntime(type=RuntimeType.OMLX, port=8018),
                        models=["memory"],
                    )
                },
            ),
            tmp_path,
        ),
        raising=False,
    )

    result = runner.invoke(
        app,
        [
            "edge",
            "client-config",
            "vscode",
            "shag",
            "--inject-api-key",
            "--create-missing-key",
            "--yes",
        ],
    )

    assert result.exit_code == 0
    payload = _parse_json(result.stdout)
    assert isinstance(payload, list)
    assert len(payload) == 1
    provider = payload[0]
    api_key_prefix = "TF_USER_SHAG="
    api_key = next(
        line.removeprefix(api_key_prefix)
        for line in env_file.read_text().splitlines()
        if line.startswith(api_key_prefix)
    )
    assert provider["name"] == "Thunder Forge"
    assert provider["vendor"] == "customendpoint"
    assert provider["apiType"] == "chat-completions"
    assert provider["apiKey"] == api_key
    assert provider["models"][0]["id"] == "memory"
    assert provider["models"][0]["name"] == "memory"
    assert provider["models"][0]["url"] == "http://127.0.0.1:40116/v1"
    assert provider["models"][0]["toolCalling"] is True
    assert provider["models"][0]["vision"] is True
    assert provider["models"][0]["maxInputTokens"] == 117965
    assert provider["models"][0]["maxOutputTokens"] == 13107
    assert provider["models"][0]["requestHeaders"]["X-Olla-Session-ID"] == "memory-shag"


def test_edge_client_config_vscode_uses_placeholder_without_client_id(monkeypatch) -> None:
    import thunder_forge.cli as cli_module

    monkeypatch.setattr(
        cli_module,
        "_load_config",
        lambda: (
            ClusterConfig(
                models={
                    "memory": Model(
                        source=ModelSource(type="huggingface", repo="mlx-community/gpt-oss-20b-MXFP4-Q8"),
                        runtime_model_id="gpt-oss-20b-MXFP4-Q8",
                        max_context=131072,
                    )
                },
                nodes={
                    "infer-03": Node(
                        host="infer-03.lan",
                        ram_gb=128,
                        roles=[NodeRole.INFERENCE],
                        runtime=NodeRuntime(type=RuntimeType.OMLX, port=8018),
                        models=["memory"],
                    )
                },
            ),
            Path.cwd(),
        ),
        raising=False,
    )

    result = runner.invoke(app, ["edge", "client-config", "vscode"])

    assert result.exit_code == 0
    payload = _parse_json(result.stdout)
    assert payload[0]["apiKey"] == "<API-ID-VALUE>"
    assert payload[0]["models"][0]["maxInputTokens"] == 117965
    assert payload[0]["models"][0]["maxOutputTokens"] == 13107
    assert "requestHeaders" not in payload[0]["models"][0]


def test_edge_client_config_opencode_rejects_unassigned_default_model(monkeypatch) -> None:
    import thunder_forge.cli as cli_module

    monkeypatch.setattr(
        cli_module,
        "_load_config",
        lambda: (
            ClusterConfig(
                models={
                    "memory": Model(
                        source=ModelSource(type="huggingface", repo="mlx-community/gpt-oss-20b-MXFP4-Q8"),
                        runtime_model_id="gpt-oss-20b-MXFP4-Q8",
                    )
                },
                nodes={
                    "infer-03": Node(
                        host="infer-03.lan",
                        ram_gb=128,
                        roles=[NodeRole.INFERENCE],
                        runtime=NodeRuntime(type=RuntimeType.OMLX, port=8018),
                        models=["memory"],
                    )
                },
            ),
            Path.cwd(),
        ),
        raising=False,
    )

    result = runner.invoke(app, ["edge", "client-config", "opencode", "--model", "coder"])

    assert result.exit_code == 1
    assert "Error: --model alias 'coder' is not assigned to an inference node" in result.stderr


def test_edge_client_config_hermes_prints_yaml_with_key_env(monkeypatch) -> None:
    import yaml

    import thunder_forge.cli as cli_module

    monkeypatch.setattr(
        cli_module,
        "_load_config",
        lambda: (
            ClusterConfig(
                models={
                    "memory": Model(
                        source=ModelSource(type="huggingface", repo="mlx-community/gpt-oss-20b-MXFP4-Q8"),
                        runtime_model_id="gpt-oss-20b-MXFP4-Q8",
                        max_context=131072,
                    ),
                    "memory-bf16": Model(
                        source=ModelSource(type="huggingface", repo="mlx-community/gpt-oss-20b-mxfp4-bf16"),
                        runtime_model_id="gpt-oss-20b-mxfp4-bf16",
                        max_context=131072,
                        benchmark_only=True,
                    ),
                },
                nodes={
                    "infer-03": Node(
                        host="infer-03.lan",
                        ram_gb=128,
                        roles=[NodeRole.INFERENCE],
                        runtime=NodeRuntime(type=RuntimeType.OMLX, port=8018),
                        models=["memory", "memory-bf16"],
                    )
                },
            ),
            Path.cwd(),
        ),
        raising=False,
    )

    result = runner.invoke(
        app,
        ["edge", "client-config", "hermes", "shag", "--base-url", "http://studio:40116/v1"],
    )

    assert result.exit_code == 0
    payload = yaml.safe_load(result.stdout)
    provider = payload["custom_providers"][0]
    assert provider["name"] == "thunder-forge"
    assert provider["base_url"] == "http://studio:40116/v1"
    assert provider["key_env"] == "TF_USER_SHAG"
    assert provider["api_mode"] == "chat_completions"
    assert sorted(provider["models"]) == ["memory", "memory-bf16"]
    assert provider["models"]["memory"]["context_length"] == 131072
    assert provider["models"]["memory-bf16"]["context_length"] == 131072


def test_edge_client_config_hermes_creates_missing_key_without_printing_secret(monkeypatch, tmp_path) -> None:
    import thunder_forge.cli as cli_module

    env_file = tmp_path / ".env"
    monkeypatch.delenv("TF_USER_SHAG", raising=False)
    monkeypatch.setattr(cli_module, "_load_repo_dotenv", lambda: (tmp_path, env_file), raising=False)
    monkeypatch.setattr(
        cli_module,
        "_load_config",
        lambda: (
            ClusterConfig(
                models={
                    "memory": Model(
                        source=ModelSource(type="huggingface", repo="mlx-community/gpt-oss-20b-MXFP4-Q8"),
                        runtime_model_id="gpt-oss-20b-MXFP4-Q8",
                    )
                },
                nodes={
                    "infer-03": Node(
                        host="infer-03.lan",
                        ram_gb=128,
                        roles=[NodeRole.INFERENCE],
                        runtime=NodeRuntime(type=RuntimeType.OMLX, port=8018),
                        models=["memory"],
                    )
                },
            ),
            tmp_path,
        ),
        raising=False,
    )

    result = runner.invoke(app, ["edge", "client-config", "hermes", "shag", "--create-missing-key", "--yes"])

    assert result.exit_code == 0
    env_text = env_file.read_text()
    env_prefix = "TF_USER_SHAG="
    assert env_prefix in env_text
    api_key = next(line.removeprefix(env_prefix) for line in env_text.splitlines() if line.startswith(env_prefix))
    assert "key_env: TF_USER_SHAG" in result.stdout
    assert api_key not in result.stdout
    assert api_key not in result.stderr


def test_edge_client_config_hermes_skips_prompt_when_key_exists(monkeypatch, tmp_path) -> None:
    import thunder_forge.cli as cli_module

    env_file = tmp_path / ".env"
    env_file.write_text("TF_USER_SHAG=already-there\n")
    monkeypatch.setattr(cli_module, "_load_repo_dotenv", lambda: (tmp_path, env_file), raising=False)
    monkeypatch.setattr(
        cli_module,
        "_load_config",
        lambda: (
            ClusterConfig(
                models={
                    "memory": Model(
                        source=ModelSource(type="huggingface", repo="mlx-community/gpt-oss-20b-MXFP4-Q8"),
                        runtime_model_id="gpt-oss-20b-MXFP4-Q8",
                    )
                },
                nodes={
                    "infer-03": Node(
                        host="infer-03.lan",
                        ram_gb=128,
                        roles=[NodeRole.INFERENCE],
                        runtime=NodeRuntime(type=RuntimeType.OMLX, port=8018),
                        models=["memory"],
                    )
                },
            ),
            tmp_path,
        ),
        raising=False,
    )

    called = {"prompt": False}

    def fail_prompt(*args, **kwargs):
        called["prompt"] = True
        raise AssertionError("prompt should not be called when key already exists")

    monkeypatch.setattr(cli_module, "_confirm_create_edge_key", fail_prompt, raising=False)

    result = runner.invoke(app, ["edge", "client-config", "hermes", "shag", "--create-missing-key"])

    assert result.exit_code == 0
    assert called["prompt"] is False
    assert "TF_USER_SHAG: present" in result.stderr
    assert "key_env: TF_USER_SHAG" in result.stdout


def test_edge_smoke_cli_uses_config_default(monkeypatch) -> None:
    import thunder_forge.cli as cli_module

    captured: dict[str, str] = {}
    monkeypatch.setenv("TF_USER_CLIENT_DASH_A", "dev-secret")
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
    assert "TF_USER_CLIENT_DASH_A=" in content
    assert "TF_USER_CLIENT_DASH_B=" in content
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
                "olla_endpoint": "infer-03-omlx-live",
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
    assert "infer-03-omlx-live: 1" in result.stdout
