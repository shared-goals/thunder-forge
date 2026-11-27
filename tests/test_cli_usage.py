"""CLI tests for the top-level usage report command."""

from __future__ import annotations

import json
import time
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
    import thunder_forge.cli as cli_module
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
                "status_code": 200,
            }
        )
        + "\n"
    )
    (repo / "logs" / "tf-node-metrics.jsonl").write_text(
        json.dumps(
            {
                "timestamp": "2026-06-02T08:16:00+00:00",
                "node_id": "msm1",
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
    monkeypatch.setattr(cli_module, "resolve_usage_period", lambda _raw: "2026-06-02")

    result = runner.invoke(app, ["usage", "report", "--json"])

    assert result.exit_code == 0
    payload = json.loads(result.stdout)
    assert payload["period"] == "2026-06-02"
    assert payload["requests"]["total"] == 1
    assert payload["requests"]["by_user"] == {"alice": 1}
    assert payload["requests"]["by_user_model"] == {"alice": {"coder": 1}}
    assert payload["requests"]["by_node"] == {"msm1": 1}
    by_node_hour = payload["requests"]["by_node_hour"]
    assert len(by_node_hour["msm1"]) == 24
    assert by_node_hour["msm1"]["08"] == 1
    assert by_node_hour["msm1"]["04"] == 0
    assert payload["requests"]["by_model"] == {"coder": 1}
    assert payload["requests"]["by_status"] == {"200": 1}
    assert payload["failures_total"] == 0
    assert payload["requests"]["unauthenticated"] == 0
    assert payload["coverage"] == {
        "first_record_at": "2026-06-02T08:15:00+00:00",
        "last_record_at": "2026-06-02T08:15:00+00:00",
    }
    assert payload["partial"] is True
    by_hour = payload["requests"]["by_hour"]
    assert len(by_hour) == 24
    assert by_hour["08"] == {
        "requests": 1, "consumed_ms": 100, "by_model": {"coder": 1}, "by_user": {"alice": 1}
    }
    assert by_hour["04"] == {"requests": 0, "consumed_ms": 0, "by_model": {}, "by_user": {}}
    assert payload["consumed_ms"]["total"] == 100
    assert payload["node_metrics"]["hot_loaded_models"] == {"msm1": ["coder", "agent"]}
    assert payload["node_metrics"]["hot_loaded_count"] == {"msm1": 2}


def test_usage_report_cli_fails_when_the_access_log_is_missing(tmp_path: Path, monkeypatch) -> None:
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

    import thunder_forge.cluster.config as config_module

    monkeypatch.setattr(config_module, "find_repo_root", lambda: repo)

    for args in (["usage", "report", "--json"], ["usage", "report"]):
        result = runner.invoke(app, args)

        # A missing log is an error, not a quiet day with zero requests.
        assert result.exit_code == 2, result.output
        assert "access log not found" in result.output
        assert str(repo / "logs" / "tf-edge-access.jsonl") in result.output


def test_usage_report_cli_accepts_all_period(tmp_path: Path, monkeypatch) -> None:
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
            }
        )
        + "\n"
    )

    import thunder_forge.cluster.config as config_module

    monkeypatch.setattr(config_module, "find_repo_root", lambda: repo)

    result = runner.invoke(app, ["usage", "report", "--period", "all", "--json"])

    assert result.exit_code == 0
    payload = json.loads(result.stdout)
    assert payload["period"] == "all"
    assert payload["requests"]["total"] == 1


def test_usage_report_cli_excludes_unauthenticated_from_tfconfig(tmp_path: Path, monkeypatch) -> None:
    import thunder_forge.cli as cli_module

    repo = tmp_path
    (repo / "tfconfig.yaml").write_text(
        dedent(
            """\
            services:
              edge:
                access_log: logs/tf-edge-access.jsonl
            operations:
              usage:
                exclude_unauthenticated: true
            models: {}
            nodes: {}
        """
        )
    )
    (repo / "logs").mkdir()
    (repo / "logs" / "tf-edge-access.jsonl").write_text(
        "\n".join(
            [
                json.dumps(
                    {
                        "timestamp": "2026-06-02T08:15:00+00:00",
                        "client_id": "unauthenticated",
                        "model": "agent",
                        "latency_ms": 100,
                        "olla_endpoint": "msm1-omlx-live",
                    }
                ),
                json.dumps(
                    {
                        "timestamp": "2026-06-02T08:16:00+00:00",
                        "client_id": "alice",
                        "model": "coder",
                        "latency_ms": 120,
                        "olla_endpoint": "msm1-omlx-live",
                    }
                ),
            ]
        )
        + "\n"
    )

    import thunder_forge.cluster.config as config_module

    monkeypatch.setattr(config_module, "find_repo_root", lambda: repo)
    monkeypatch.setattr(cli_module, "resolve_usage_period", lambda _raw: "2026-06-02")

    result = runner.invoke(app, ["usage", "report", "--json"])

    assert result.exit_code == 0
    payload = json.loads(result.stdout)
    assert payload["requests"]["total"] == 1
    assert payload["requests"]["by_user"] == {"alice": 1}


def test_usage_report_cli_text_formats_numbers_and_omits_consumed_breakdowns(tmp_path: Path, monkeypatch) -> None:
    import thunder_forge.cli as cli_module

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
        "\n".join(
            [
                json.dumps(
                    {
                        "timestamp": "2026-06-02T08:15:00+00:00",
                        "client_id": "alice",
                        "model": "coder",
                        "latency_ms": 1234,
                        "olla_endpoint": "msm1-omlx-live",
                    }
                ),
                json.dumps(
                    {
                        "timestamp": "2026-06-02T08:20:00+00:00",
                        "client_id": "alice",
                        "model": "coder",
                        "latency_ms": 2000,
                        "olla_endpoint": "msm1-omlx-live",
                    }
                ),
            ]
        )
        + "\n"
    )

    import thunder_forge.cluster.config as config_module

    monkeypatch.setattr(config_module, "find_repo_root", lambda: repo)
    monkeypatch.setattr(cli_module, "resolve_usage_period", lambda _raw: "2026-06-02")

    result = runner.invoke(app, ["usage", "report"])

    assert result.exit_code == 0
    assert "requests_total: 2" in result.stdout
    assert "consumed_ms_total: 3,234" in result.stdout
    assert "requests_by_hour:" in result.stdout
    assert "  - 08:" in result.stdout
    assert "consumed_ms: 3,234" in result.stdout
    assert "by_model: coder: 2" in result.stdout
    assert "by_user: alice: 2" in result.stdout
    assert "requests_by_node_user:" not in result.stdout
    assert "requests_by_node_model:" not in result.stdout
    assert "consumed_ms_by_node:" not in result.stdout
    assert "consumed_ms_by_model:" not in result.stdout


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
                    "last_access": time.time() - 30,
                    "actual_size": 96680000000.4,
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
    assert payload["node_id"] == "msm1"
    assert "host" not in payload
    assert "base_url" not in payload
    assert "gpu_utilization" not in payload
    assert payload["health_ok"] is True
    assert payload["models_ok"] is True
    # idle_seconds is derived from last_access: oMLX never sends it.
    assert 29 <= payload["loaded_models"][0]["idle_seconds"] <= 120
    assert payload["loaded_models"][0]["actual_size"] == 96680000000
    assert payload["active_requests"] is None
    assert payload["waiting_requests"] is None
    assert payload["hot_loaded_models"] == ["coder"]
    assert payload["hot_loaded_count"] == 1


def test_usage_trim_logs_warns_when_a_live_log_moved_under_it(tmp_path: Path, monkeypatch) -> None:
    import thunder_forge.cli as cli_module
    from thunder_forge.cluster.log_retention import JsonlTrimResult, LogTrimSummary

    repo = tmp_path
    (repo / "logs").mkdir()
    config = ClusterConfig(services=ServiceConfig(edge_access_log="logs/tf-edge-access.jsonl"), nodes={})
    path = str(repo / "logs" / "tf-edge-access.jsonl")
    summary = LogTrimSummary(
        retention_days=3,
        cutoff="2026-06-02T00:00:00+00:00",
        jsonl_results=[JsonlTrimResult(path=path, before_lines=5, after_lines=5, removed_lines=0, skipped=True)],
        file_results=[],
    )

    monkeypatch.setattr(cli_module, "_load_config", lambda: (config, repo), raising=False)
    monkeypatch.setattr(cli_module, "trim_local_logs", lambda **kwargs: summary)

    result = runner.invoke(app, ["usage", "trim-logs"])

    assert result.exit_code == 0, result.output
    assert "skipped_jsonl_files: 1" in result.stdout
    assert "Warning: left these logs intact" in result.stderr
    assert path in result.stderr


def test_usage_collect_node_metrics_never_trims_logs(tmp_path: Path, monkeypatch) -> None:
    """Retention has one entry point: the collector must not rewrite a log the edge writes."""
    import thunder_forge.cli as cli_module

    repo = tmp_path
    (repo / "logs").mkdir()
    config = ClusterConfig(
        services=ServiceConfig(edge_access_log="logs/tf-edge-access.jsonl"),
        nodes={},
    )

    def fake_trim(*args: object, **kwargs: object) -> object:
        raise AssertionError("usage collect-node-metrics must not trim logs")

    monkeypatch.setattr(cli_module, "_load_config", lambda: (config, repo), raising=False)
    monkeypatch.setattr(cli_module, "trim_local_logs", fake_trim)

    result = runner.invoke(app, ["usage", "collect-node-metrics", "--once"])

    assert result.exit_code == 0, result.output
    assert "trimmed_jsonl_records" not in result.stdout
    assert "retention_days" not in result.stdout


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
