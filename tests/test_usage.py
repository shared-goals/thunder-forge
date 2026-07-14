"""Tests for the daily usage aggregation helpers."""

from __future__ import annotations

import json
from pathlib import Path

from thunder_forge.cluster.usage import extract_hot_loaded_models, summarize_daily_usage


def test_summarize_daily_usage_groups_by_user_node_model_and_hour(tmp_path: Path) -> None:
    access_log = tmp_path / "tf-edge-access.jsonl"
    access_log.write_text(
        "\n".join(
            [
                json.dumps(
                    {
                        "timestamp": "2026-06-02T08:15:00+00:00",
                        "client_id": "alice",
                        "model": "coder",
                        "latency_ms": 100,
                        "olla_endpoint": "msm1-omlx-live",
                    }
                ),
                json.dumps(
                    {
                        "timestamp": "2026-06-02T08:45:00+00:00",
                        "client_id": "alice",
                        "model": "coder",
                        "latency_ms": 200,
                        "node_name": "msm1",
                    }
                ),
                json.dumps(
                    {
                        "timestamp": "2026-06-02T09:00:00+00:00",
                        "client_id": "bob",
                        "model": "agent",
                        "latency_ms": 150,
                        "olla_endpoint": "msm2-omlx-live",
                    }
                ),
                "not json",
            ]
        )
        + "\n"
    )

    node_metrics = tmp_path / "tf-node-metrics.jsonl"
    node_metrics.write_text(
        "\n".join(
            [
                json.dumps({"timestamp": "2026-06-02T08:00:00+00:00", "node_name": "msm1", "hot_loaded_count": 2}),
                json.dumps({"timestamp": "2026-06-02T08:05:00+00:00", "node_name": "msm1", "hot_loaded_count": 1}),
            ]
        )
        + "\n"
    )

    summary = summarize_daily_usage(access_log, period="2026-06-02", node_metrics_path=node_metrics)

    assert summary.requests_total == 3
    assert summary.invalid_lines == 1
    assert summary.requests_by_user == {"alice": 2, "bob": 1}
    assert summary.consumed_ms_by_user == {"alice": 300, "bob": 150}
    assert summary.requests_by_user_model == {"alice": {"coder": 2}, "bob": {"agent": 1}}
    assert summary.requests_by_node == {"msm1": 2, "msm2": 1}
    assert summary.requests_by_node_user == {"msm1": {"alice": 2}, "msm2": {"bob": 1}}
    assert summary.requests_by_node_model == {"msm1": {"coder": 2}, "msm2": {"agent": 1}}
    assert summary.requests_by_model == {"agent": 1, "coder": 2}
    assert summary.requests_by_hour == {"08": 2, "09": 1}


def test_extract_hot_loaded_models_orders_by_last_access() -> None:
    model_statuses = {
        "cold": {"loaded": False, "last_access": 200.0},
        "warm": {"loaded": True, "last_access": 100.0},
        "hot": {"loaded": True, "last_access": 300.0},
    }

    assert extract_hot_loaded_models(model_statuses) == ["hot", "warm"]


def test_summarize_daily_usage_excludes_tool_models_from_model_metrics(tmp_path: Path) -> None:
    access_log = tmp_path / "tf-edge-access.jsonl"
    access_log.write_text(
        "\n".join(
            [
                json.dumps(
                    {
                        "timestamp": "2026-06-02T08:15:00+00:00",
                        "client_id": "alice",
                        "model": "MarkItDown",
                        "latency_ms": 100,
                        "node_name": "msm1",
                    }
                ),
                json.dumps(
                    {
                        "timestamp": "2026-06-02T08:16:00+00:00",
                        "client_id": "alice",
                        "model": "coder",
                        "latency_ms": 120,
                        "node_name": "msm1",
                    }
                ),
            ]
        )
        + "\n"
    )

    node_metrics = tmp_path / "tf-node-metrics.jsonl"
    node_metrics.write_text(
        json.dumps(
            {
                "timestamp": "2026-06-02T08:17:00+00:00",
                "node_name": "msm1",
                "hot_loaded_models": ["coder", "MarkItDown"],
                "hot_loaded_count": 2,
            }
        )
        + "\n"
    )

    summary = summarize_daily_usage(access_log, period="2026-06-02", node_metrics_path=node_metrics)

    assert summary.requests_total == 2
    assert summary.requests_by_model == {"coder": 1}
    assert summary.consumed_ms_by_model == {"coder": 120}
    assert summary.requests_by_user_model == {"alice": {"coder": 1}}
    assert summary.requests_by_node_model == {"msm1": {"coder": 1}}
    assert summary.node_hot_loaded_models == {"msm1": ["coder"]}
    assert summary.node_hot_loaded_count == {"msm1": 1}


def test_summarize_daily_usage_include_tool_models_when_exclusion_is_empty(tmp_path: Path) -> None:
    access_log = tmp_path / "tf-edge-access.jsonl"
    access_log.write_text(
        json.dumps(
            {
                "timestamp": "2026-06-02T08:15:00+00:00",
                "client_id": "alice",
                "model": "MarkItDown",
                "latency_ms": 100,
                "node_name": "msm1",
            }
        )
        + "\n"
    )

    summary = summarize_daily_usage(access_log, period="2026-06-02", excluded_models=[])

    assert summary.requests_by_model == {"MarkItDown": 1}
    assert summary.consumed_ms_by_model == {"MarkItDown": 100}
    assert summary.requests_by_user_model == {"alice": {"MarkItDown": 1}}
