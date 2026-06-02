"""Tests for the daily usage aggregation helpers."""

from __future__ import annotations

import json
from pathlib import Path

from thunder_forge.cluster.usage import extract_hot_loaded_models, summarize_daily_usage


def test_summarize_daily_usage_groups_by_user_node_model_hour_and_tokens(tmp_path: Path) -> None:
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
                        "total_tokens": 12,
                    }
                ),
                json.dumps(
                    {
                        "timestamp": "2026-06-02T08:45:00+00:00",
                        "client_id": "alice",
                        "model": "coder",
                        "latency_ms": 200,
                        "node_name": "msm1",
                        "usage": {"prompt_tokens": 3, "completion_tokens": 7},
                    }
                ),
                json.dumps(
                    {
                        "timestamp": "2026-06-02T09:00:00+00:00",
                        "client_id": "bob",
                        "model": "agent",
                        "latency_ms": 150,
                        "olla_endpoint": "msm2-omlx-live",
                        "usage": {"total_tokens": 20},
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
    assert summary.tokens_by_user == {"alice": 22, "bob": 20}
    assert summary.requests_by_node == {"msm1": 2, "msm2": 1}
    assert summary.requests_by_node_model == {"msm1": {"coder": 2}, "msm2": {"agent": 1}}
    assert summary.requests_by_node_hour == {"msm1": {"08": 2}, "msm2": {"09": 1}}
    assert summary.requests_by_model == {"agent": 1, "coder": 2}
    assert summary.requests_by_hour == {"08": 2, "09": 1}


def test_extract_hot_loaded_models_orders_by_last_access() -> None:
    model_statuses = {
        "cold": {"loaded": False, "last_access": 200.0},
        "warm": {"loaded": True, "last_access": 100.0},
        "hot": {"loaded": True, "last_access": 300.0},
    }

    assert extract_hot_loaded_models(model_statuses) == ["hot", "warm"]
