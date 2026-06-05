"""CLI tests for the top-level usage report command."""

from __future__ import annotations

import json
from pathlib import Path
from textwrap import dedent

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
from thunder_forge.cluster.omlx import OmlxHealthResult

runner = CliRunner()


def test_usage_report_cli_emits_json_summary(tmp_path: Path, monkeypatch) -> None:
    repo = tmp_path
    (repo / "tfconfig.yaml").write_text(
        dedent(
            """\
            services:
              edge:
                access_log: logs/tf-edge-access.jsonl
            models: {}
            nodes: {}
        """
        )
    )
    (repo / "logs").mkdir()
    (repo / "logs" / "tf-edge-access.jsonl").write_text(
        json.dumps(
            {
                "timestamp": "2026-06-02T08:15:00+00:00",
                "client_id": "alice",
                "model": "coder",
                "latency_ms": 100,
                "olla_endpoint": "msm1-omlx-live",
                "prompt_tokens": 12,
                "completion_tokens": 8,
                "total_tokens": 20,
            }
        )
        + "\n"
    )
    (repo / "logs" / "tf-node-metrics.jsonl").write_text(
        json.dumps(
            {
                "timestamp": "2026-06-02T08:16:00+00:00",
                "node_name": "msm1",
                "health_ok": True,
                "models_ok": True,
                "hot_loaded_models": ["coder", "agent"],
                "hot_loaded_count": 2,
            }
        )
        + "\n"
    )

    import thunder_forge.cluster.config as config_module

    monkeypatch.setattr(config_module, "find_repo_root", lambda: repo)

    result = runner.invoke(app, ["usage", "report", "--period", "2026-06-02", "--json"])

    assert result.exit_code == 0
    payload = json.loads(result.stdout)
    assert payload["period"] == "2026-06-02"
    assert payload["requests"]["total"] == 1
    assert payload["requests"]["by_user"] == {"alice": 1}
    assert payload["requests"]["by_user_model"] == {"alice": {"coder": 1}}
    assert payload["requests"]["by_node"] == {"msm1": 1}
    assert payload["requests"]["by_node_hour"] == {"msm1": {"08": 1}}
    assert payload["requests"]["by_model"] == {"coder": 1}
    assert payload["requests"]["by_hour"] == {"08": 1}
    assert payload["consumed_ms"]["total"] == 100
    assert payload["tokens"] == {
        "total": 20,
        "by_user": {"alice": 20},
        "by_node": {"msm1": 20},
        "by_model": {"coder": 20},
    }
    assert payload["node_metrics"]["hot_loaded_models"] == {"msm1": ["coder", "agent"]}
    assert payload["node_metrics"]["hot_loaded_count"] == {"msm1": 2}


def test_usage_collect_node_metrics_writes_snapshot_jsonl(tmp_path: Path, monkeypatch) -> None:
    import thunder_forge.cli as cli_module

    repo = tmp_path
    logs_dir = repo / "logs"
    logs_dir.mkdir()

    config = ClusterConfig(
        services=ServiceConfig(edge_access_log="logs/tf-edge-access.jsonl"),
        nodes={
            "msm1": Node(
                host="msm1-wifi.lan",
                ram_gb=128,
                roles=[NodeRole.INFERENCE],
                user="shag",
                runtime=NodeRuntime(type=RuntimeType.OMLX, port=8018),
            )
        },
    )

    monkeypatch.setattr(cli_module, "_load_config", lambda: (config, repo), raising=False)
    monkeypatch.setattr(
        cli_module,
        "check_omlx_health",
        lambda *args, **kwargs: OmlxHealthResult(
            base_url="http://msm1-wifi.lan:8018",
            health_ok=True,
            models_ok=True,
            status_ok=True,
            models=["coder"],
            model_statuses={
                "coder": {
                    "loaded": True,
                    "last_access": 200.0,
                }
            },
        ),
        raising=False,
    )

    result = runner.invoke(app, ["usage", "collect-node-metrics"])

    assert result.exit_code == 0
    output_path = logs_dir / "tf-node-metrics.jsonl"
    assert output_path.exists()
    lines = output_path.read_text().splitlines()
    assert len(lines) == 1
    payload = json.loads(lines[0])
    assert payload["node_name"] == "msm1"
    assert "host" not in payload
    assert "base_url" not in payload
    assert "gpu_utilization" not in payload
    assert payload["health_ok"] is True
    assert payload["models_ok"] is True
    assert payload["hot_loaded_models"] == ["coder"]
    assert payload["hot_loaded_count"] == 1


def test_usage_collect_node_metrics_maps_hot_loaded_runtime_ids_to_aliases(tmp_path: Path, monkeypatch) -> None:
    import thunder_forge.cli as cli_module

    repo = tmp_path
    logs_dir = repo / "logs"
    logs_dir.mkdir()

    config = ClusterConfig(
        services=ServiceConfig(edge_access_log="logs/tf-edge-access.jsonl"),
        models={
            "agent": Model(
                source=ModelSource(type="huggingface", repo="mlx-community/Qwen3.6-35B-A3B-4bit"),
                runtime_model_id="Qwen3.6-35B-A3B-4bit",
            ),
            "agent-better": Model(
                source=ModelSource(type="huggingface", repo="mlx-community/Qwen3.6-35B-A3B-mxfp8"),
                runtime_model_id="Qwen3.6-35B-A3B-mxfp8",
            ),
        },
        nodes={
            "msm1": Node(
                host="msm1-wifi.lan",
                ram_gb=128,
                roles=[NodeRole.INFERENCE],
                user="shag",
                runtime=NodeRuntime(type=RuntimeType.OMLX, port=8018),
                models=["agent", "agent-better"],
            )
        },
    )

    monkeypatch.setattr(cli_module, "_load_config", lambda: (config, repo), raising=False)
    monkeypatch.setattr(
        cli_module,
        "check_omlx_health",
        lambda *args, **kwargs: OmlxHealthResult(
            base_url="http://msm1-wifi.lan:8018",
            health_ok=True,
            models_ok=True,
            status_ok=True,
            models=["Qwen3.6-35B-A3B-4bit", "Qwen3.6-35B-A3B-mxfp8"],
            model_statuses={
                "Qwen3.6-35B-A3B-4bit": {"loaded": True, "last_access": 200.0},
                "Qwen3.6-35B-A3B-mxfp8": {"loaded": True, "last_access": 100.0},
            },
        ),
        raising=False,
    )

    result = runner.invoke(app, ["usage", "collect-node-metrics"])

    assert result.exit_code == 0
    output_path = logs_dir / "tf-node-metrics.jsonl"
    lines = output_path.read_text().splitlines()
    assert len(lines) == 1
    payload = json.loads(lines[0])
    assert payload["hot_loaded_models"] == ["agent", "agent-better"]
    assert payload["hot_loaded_count"] == 2
