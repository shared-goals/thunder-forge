"""Tests for the daily usage aggregation helpers."""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from thunder_forge.cluster.usage import (
    extract_hot_loaded_models,
    nearest_rank_percentile,
    peak_concurrency,
    resolve_usage_period,
    select_usage_events,
    summarize_daily_usage,
)


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
                        "olla_endpoint": "msm1-omlx-live",
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
                json.dumps({"timestamp": "2026-06-02T08:00:00+00:00", "node_id": "msm1", "hot_loaded_count": 2}),
                json.dumps({"timestamp": "2026-06-02T08:05:00+00:00", "node_id": "msm1", "hot_loaded_count": 1}),
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
    assert summary.requests_by_model == {"agent": 1, "coder": 2}
    assert summary.requests_by_hour["08"] == {
        "requests": 2,
        "consumed_ms": 300,
        "by_model": {"coder": 2},
        "by_user": {"alice": 2},
    }
    assert summary.requests_by_hour["09"] == {
        "requests": 1,
        "consumed_ms": 150,
        "by_model": {"agent": 1},
        "by_user": {"bob": 1},
    }
    assert len(summary.requests_by_hour) == 24
    assert summary.requests_by_hour["04"] == {"requests": 0, "consumed_ms": 0, "by_model": {}, "by_user": {}}
    assert len(summary.requests_by_node_hour["msm1"]) == 24
    assert summary.requests_by_node_hour["msm1"]["04"] == 0


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

    node_metrics = tmp_path / "tf-node-metrics.jsonl"
    node_metrics.write_text(
        json.dumps(
            {
                "timestamp": "2026-06-02T08:17:00+00:00",
                "node_id": "msm1",
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
    assert summary.requests_by_hour["08"]["by_model"] == {"coder": 1}
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
                "olla_endpoint": "msm1-omlx-live",
            }
        )
        + "\n"
    )

    summary = summarize_daily_usage(access_log, period="2026-06-02", excluded_models=[])

    assert summary.requests_by_model == {"MarkItDown": 1}
    assert summary.consumed_ms_by_model == {"MarkItDown": 100}
    assert summary.requests_by_user_model == {"alice": {"MarkItDown": 1}}


def test_summarize_daily_usage_excludes_unauthenticated_when_configured(tmp_path: Path) -> None:
    access_log = tmp_path / "tf-edge-access.jsonl"
    access_log.write_text(
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

    summary = summarize_daily_usage(access_log, period="2026-06-02", exclude_unauthenticated=True)

    assert summary.requests_total == 1
    assert summary.requests_by_user == {"alice": 1}
    assert summary.consumed_ms_total == 120


def test_percentile_and_peak_concurrency_helpers() -> None:
    assert nearest_rank_percentile([], 95) == 0
    assert nearest_rank_percentile([10, 20, 30, 40], 50) == 20
    assert nearest_rank_percentile([10, 20, 30, 40], 95) == 40

    start = datetime(2026, 6, 2, 8, 0, tzinfo=UTC)
    intervals = [
        (start, start + timedelta(seconds=30)),
        (start + timedelta(seconds=10), start + timedelta(seconds=40)),
        (start + timedelta(seconds=35), start + timedelta(seconds=50)),
    ]
    assert peak_concurrency(intervals) == 2
    assert peak_concurrency([]) == 0


def test_resolve_usage_period_and_select_usage_events(tmp_path: Path) -> None:
    access_log = tmp_path / "tf-edge-access.jsonl"
    access_log.write_text(
        "\n".join(
            [
                json.dumps({"timestamp": "2026-06-02T08:00:00+00:00", "client_id": "alice", "latency_ms": 10}),
                json.dumps({"timestamp": "2026-06-03T09:00:00+00:00", "client_id": "bob", "latency_ms": 20}),
            ]
        )
        + "\n"
    )

    assert resolve_usage_period("all") == "all"
    assert resolve_usage_period("2026-06-02") == "2026-06-02"
    with pytest.raises(ValueError):
        resolve_usage_period("02.06.2026")

    selected = select_usage_events(access_log, period="2026-06-02")
    assert [record["client_id"] for record in selected] == ["alice"]
    assert len(select_usage_events(access_log, period="all")) == 2


def test_summarize_daily_usage_accepts_float_sizes_and_clears_unloaded_nodes(tmp_path: Path) -> None:
    access_log = tmp_path / "tf-edge-access.jsonl"
    access_log.write_text("")
    node_metrics = tmp_path / "tf-node-metrics.jsonl"
    base = {"health_ok": True, "models_ok": True, "status_ok": True, "errors": []}
    node_metrics.write_text(
        "\n".join(
            [
                json.dumps(
                    {
                        **base,
                        "timestamp": "2026-06-02T08:00:00+00:00",
                        "node_id": "msm1",
                        "hot_loaded_models": ["agent"],
                        "hot_loaded_count": 1,
                        # oMLX may report the footprint as a float.
                        "loaded_models": [{"id": "agent", "actual_size": 1073741824.9, "idle_seconds": 5}],
                    }
                ),
                json.dumps(
                    {
                        **base,
                        "timestamp": "2026-06-02T08:00:00+00:00",
                        "node_id": "msm2",
                        "hot_loaded_models": ["agent"],
                        "hot_loaded_count": 1,
                        "loaded_models": [{"id": "agent", "actual_size": 2048, "idle_seconds": 1}],
                    }
                ),
                json.dumps(
                    {
                        **base,
                        "timestamp": "2026-06-02T09:00:00+00:00",
                        "node_id": "msm2",
                        "hot_loaded_models": [],
                        "hot_loaded_count": 0,
                        "loaded_models": [],
                    }
                ),
            ]
        )
        + "\n"
    )

    summary = summarize_daily_usage(access_log, period="2026-06-02", node_metrics_path=node_metrics)

    assert summary.node_model_memory["msm1"]["agent"]["actual_size"] == 1073741824
    # idle_seconds is reported here, so it must survive aggregation.
    assert summary.node_model_memory["msm1"]["agent"]["idle_seconds"] == 5
    assert summary.node_model_memory_sampled_at["msm1"] == "2026-06-02T08:00:00+00:00"
    # The later msm2 sample has nothing loaded, so its footprint must not survive.
    assert "msm2" not in summary.node_model_memory


def test_summarize_daily_usage_does_not_invent_idle_time(tmp_path: Path) -> None:
    access_log = tmp_path / "tf-edge-access.jsonl"
    access_log.write_text("")
    node_metrics = tmp_path / "tf-node-metrics.jsonl"
    node_metrics.write_text(
        json.dumps(
            {
                "timestamp": "2026-06-02T08:00:00+00:00",
                "node_id": "msm1",
                "health_ok": True,
                "models_ok": True,
                "status_ok": True,
                "hot_loaded_models": ["agent"],
                "hot_loaded_count": 1,
                "loaded_models": [{"id": "agent", "actual_size": 2048}],
                "errors": [],
            }
        )
        + "\n"
    )

    summary = summarize_daily_usage(access_log, period="2026-06-02", node_metrics_path=node_metrics)

    # The node reported no idle time, so the aggregate must not claim 0 seconds.
    assert summary.node_model_memory["msm1"]["agent"] == {"actual_size": 2048}


def test_summarize_daily_usage_keeps_memory_when_the_node_probe_failed(tmp_path: Path) -> None:
    access_log = tmp_path / "tf-edge-access.jsonl"
    access_log.write_text("")
    node_metrics = tmp_path / "tf-node-metrics.jsonl"
    good = {
        "timestamp": "2026-06-02T08:00:00+00:00",
        "node_id": "msm1",
        "health_ok": True,
        "models_ok": True,
        "status_ok": True,
        "hot_loaded_models": ["agent"],
        "hot_loaded_count": 1,
        "active_requests": 1,
        "waiting_requests": 2,
        "loaded_models": [{"id": "agent", "actual_size": 2048, "idle_seconds": 7}],
        "errors": [],
    }
    failed = {
        **good,
        "timestamp": "2026-06-02T09:00:00+00:00",
        "health_ok": False,
        "models_ok": False,
        "status_ok": False,
        "hot_loaded_models": [],
        "hot_loaded_count": 0,
        "active_requests": None,
        "waiting_requests": None,
        "loaded_models": [],
        "errors": ["GET /health failed"],
    }
    # A node that answers but only reports active requests.
    no_queue = {
        **good,
        "timestamp": "2026-06-02T09:00:00+00:00",
        "node_id": "msm2",
        "waiting_requests": None,
        "active_requests": 3,
        "loaded_models": [],
        "hot_loaded_models": [],
        "hot_loaded_count": 0,
    }
    node_metrics.write_text("\n".join(json.dumps(item) for item in (good, failed, no_queue)) + "\n")

    summary = summarize_daily_usage(access_log, period="2026-06-02", node_metrics_path=node_metrics)

    # A transient probe failure reports no models; it must not erase a real footprint.
    assert summary.node_model_memory["msm1"]["agent"]["actual_size"] == 2048
    assert summary.node_model_memory_sampled_at["msm1"] == "2026-06-02T08:00:00+00:00"
    # The failed probe contributes no load sample for that node.
    assert "msm1" not in summary.node_load_by_hour["09"]
    assert summary.node_load_by_hour["08"]["msm1"]["waiting_max"] == 2
    # A node that never reports a queue must read as unknown, not as an empty queue.
    assert summary.node_load_by_hour["09"]["msm2"]["active_max"] == 3
    assert summary.node_load_by_hour["09"]["msm2"]["waiting_max"] is None


def test_summarize_daily_usage_filters_by_client_id(tmp_path: Path) -> None:
    access_log = tmp_path / "tf-edge-access.jsonl"
    access_log.write_text(
        "\n".join(
            json.dumps(item)
            for item in (
                {"timestamp": "2026-06-02T08:00:00+00:00", "client_id": "gnezim", "model": "agent", "latency_ms": 1000},
                {"timestamp": "2026-06-02T08:10:00+00:00", "client_id": "hermes", "model": "agent", "latency_ms": 9000},
                {"timestamp": "2026-06-02T08:20:00+00:00", "client_id": "dirge", "model": "coder", "latency_ms": 3000},
            )
        )
        + "\n"
    )

    everyone = summarize_daily_usage(access_log, period="2026-06-02")
    only_hermes = summarize_daily_usage(access_log, period="2026-06-02", client_ids=["hermes"])

    assert everyone.available_users == ["dirge", "gnezim", "hermes"]
    assert everyone.selected_users == []
    assert everyone.requests_total == 3

    # The filter narrows the numbers but never hides who else exists in the period.
    assert only_hermes.selected_users == ["hermes"]
    assert only_hermes.available_users == ["dirge", "gnezim", "hermes"]
    assert only_hermes.requests_total == 1
    assert only_hermes.consumed_ms_total == 9000
    assert only_hermes.latency_ms_by_hour["08"] == {"count": 1, "p50": 9000, "p95": 9000, "max": 9000}
    assert only_hermes.requests_by_user == {"hermes": 1}


def test_summarize_daily_usage_counts_status_codes_and_failures(tmp_path: Path) -> None:
    access_log = tmp_path / "tf-edge-access.jsonl"
    access_log.write_text(
        "\n".join(
            json.dumps(item)
            for item in (
                {"timestamp": "2026-11-27T16:56:06+04:00", "client_id": "shag", "model": "agent",
                 "latency_ms": 60, "olla_endpoint": "", "status_code": 502},
                {"timestamp": "2026-11-27T16:57:10+04:00", "client_id": "gnezim", "model": "coder",
                 "latency_ms": 30, "olla_endpoint": "msm2-omlx-live", "status_code": 502},
                {"timestamp": "2026-11-27T17:00:00+04:00", "client_id": "alice", "model": "coder",
                 "latency_ms": 100, "olla_endpoint": "msm1-omlx-live", "status_code": 200},
                {"timestamp": "2026-11-27T17:01:00+04:00", "client_id": "unauthenticated", "model": "",
                 "latency_ms": 5, "olla_endpoint": "", "status_code": 401},
            )
        )
        + "\n"
    )

    summary = summarize_daily_usage(access_log, period="2026-11-27")

    assert summary.requests_total == 4
    assert summary.requests_by_status == {"200": 1, "401": 1, "502": 2}
    assert summary.failures_total == 3
    # Every response lands in the histogram, including rows without a status code.
    assert sum(summary.requests_by_status.values()) == summary.requests_total


def test_summarize_daily_usage_reconciles_breakdowns_with_residual_buckets(tmp_path: Path) -> None:
    access_log = tmp_path / "tf-edge-access.jsonl"
    access_log.write_text(
        "\n".join(
            json.dumps(item)
            for item in (
                # No endpoint attribution: counted, but nothing to attribute it to.
                {"timestamp": "2026-06-02T08:00:00+00:00", "client_id": "alice", "model": "coder",
                 "latency_ms": 100, "olla_endpoint": "", "status_code": 200},
                # Model listing: a real request with no model name.
                {"timestamp": "2026-06-02T08:10:00+00:00", "client_id": "alice", "model": "",
                 "latency_ms": 100, "olla_endpoint": "msm1-omlx-live", "status_code": 200},
                # Rejected key: a pseudo-identity, not a client.
                {"timestamp": "2026-06-02T08:20:00+00:00", "client_id": "unauthenticated", "model": "",
                 "latency_ms": 100, "olla_endpoint": "", "status_code": 401},
            )
        )
        + "\n"
    )

    summary = summarize_daily_usage(access_log, period="2026-06-02")

    assert summary.requests_total == 3
    assert summary.requests_by_node == {"msm1": 1, "unknown": 2}
    assert summary.requests_by_model == {"coder": 1, "unknown": 2}
    assert summary.requests_unauthenticated == 1
    assert sum(summary.requests_by_node.values()) == summary.requests_total
    assert sum(summary.requests_by_model.values()) == summary.requests_total
    assert sum(summary.requests_by_user.values()) == summary.requests_total
    assert sum(int(bucket["requests"]) for bucket in summary.requests_by_hour.values()) == 3
    assert sum(sum(row.values()) for row in summary.requests_by_node_hour.values()) == summary.requests_total


def test_summarize_daily_usage_zero_fills_an_idle_day(tmp_path: Path) -> None:
    access_log = tmp_path / "tf-edge-access.jsonl"
    access_log.write_text(
        json.dumps(
            {"timestamp": "2026-06-03T08:00:00+00:00", "client_id": "alice", "model": "coder",
             "latency_ms": 100, "olla_endpoint": "msm1-omlx-live", "status_code": 200}
        )
        + "\n"
    )

    summary = summarize_daily_usage(access_log, period="2026-06-02")

    assert summary.requests_total == 0
    assert set(summary.requests_by_hour) == {f"{hour:02d}" for hour in range(24)}
    assert all(
        bucket == {"requests": 0, "consumed_ms": 0, "by_model": {}, "by_user": {}}
        for bucket in summary.requests_by_hour.values()
    )
    assert summary.latency_ms_by_hour == {}
    assert summary.requests_by_node_hour == {}
    assert summary.coverage_first_record_at is None


def test_summarize_daily_usage_flags_a_truncated_oldest_day(tmp_path: Path) -> None:
    access_log = tmp_path / "tf-edge-access.jsonl"
    access_log.write_text(
        "\n".join(
            json.dumps(item)
            for item in (
                # Retention cut the file at 04:00 local on the oldest day it kept.
                {"timestamp": "2026-11-26T04:00:12+04:00", "client_id": "alice", "model": "coder",
                 "latency_ms": 100, "olla_endpoint": "msm1-omlx-live", "status_code": 200},
                {"timestamp": "2026-11-26T23:59:00+04:00", "client_id": "alice", "model": "coder",
                 "latency_ms": 100, "olla_endpoint": "msm1-omlx-live", "status_code": 200},
                {"timestamp": "2026-11-27T00:00:00+04:00", "client_id": "alice", "model": "coder",
                 "latency_ms": 100, "olla_endpoint": "msm1-omlx-live", "status_code": 200},
            )
        )
        + "\n"
    )

    truncated = summarize_daily_usage(access_log, period="2026-11-26")
    complete = summarize_daily_usage(access_log, period="2026-11-27")
    everything = summarize_daily_usage(access_log, period="all")

    assert truncated.partial is True
    assert truncated.coverage_first_record_at == "2026-11-26T04:00:12+04:00"
    assert truncated.coverage_last_record_at == "2026-11-26T23:59:00+04:00"
    # A later day is not the oldest in the file, so retention cannot have cut its start.
    assert complete.partial is False
    assert complete.coverage_first_record_at == "2026-11-27T00:00:00+04:00"
    # "all" merges days, so there is no oldest-day boundary to warn about.
    assert everything.partial is False
    assert everything.coverage_first_record_at == "2026-11-26T04:00:12+04:00"
