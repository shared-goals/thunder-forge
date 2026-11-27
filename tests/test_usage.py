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


def _access_log(tmp_path: Path, records: list[object]) -> Path:
    """Write one JSONL access log; a raw string record is kept verbatim as a broken line."""
    path = tmp_path / "tf-edge-access.jsonl"
    path.write_text("\n".join(json.dumps(record) if isinstance(record, dict) else str(record) for record in records))
    return path


def test_summarize_daily_usage_groups_by_user_node_model_and_hour(tmp_path: Path) -> None:
    access_log = _access_log(
        tmp_path,
        [
            {
                "timestamp": "2026-06-02T08:15:00+00:00",
                "client_id": "alice",
                "model": "coder",
                "latency_ms": 100,
                "olla_endpoint": "msm1-omlx-live",
            },
            {
                "timestamp": "2026-06-02T08:45:00+00:00",
                "client_id": "alice",
                "model": "coder",
                "latency_ms": 200,
                "olla_endpoint": "msm1-omlx-live",
            },
            {
                "timestamp": "2026-06-02T09:00:00+00:00",
                "client_id": "bob",
                "model": "agent",
                "latency_ms": 150,
                "olla_endpoint": "msm2-omlx-live",
            },
            "not json",
        ],
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
    assert summary.requests_by_hour == {
        "08": {"requests": 2, "consumed_ms": 300, "by_model": {"coder": 2}, "by_user": {"alice": 2}},
        "09": {"requests": 1, "consumed_ms": 150, "by_model": {"agent": 1}, "by_user": {"bob": 1}},
    }


def test_extract_hot_loaded_models_orders_by_last_access() -> None:
    model_statuses = {
        "cold": {"loaded": False, "last_access": 200.0},
        "warm": {"loaded": True, "last_access": 100.0},
        "hot": {"loaded": True, "last_access": 300.0},
    }

    assert extract_hot_loaded_models(model_statuses) == ["hot", "warm"]


def test_summarize_daily_usage_excludes_tool_models_from_model_metrics(tmp_path: Path) -> None:
    access_log = _access_log(
        tmp_path,
        [
            {
                "timestamp": "2026-06-02T08:15:00+00:00",
                "client_id": "alice",
                "model": "MarkItDown",
                "latency_ms": 100,
                "olla_endpoint": "msm1-omlx-live",
            },
            {
                "timestamp": "2026-06-02T08:16:00+00:00",
                "client_id": "alice",
                "model": "coder",
                "latency_ms": 120,
                "olla_endpoint": "msm1-omlx-live",
            },
        ],
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
    access_log = _access_log(
        tmp_path,
        [
            {
                "timestamp": "2026-06-02T08:15:00+00:00",
                "client_id": "alice",
                "model": "MarkItDown",
                "latency_ms": 100,
                "olla_endpoint": "msm1-omlx-live",
            },
        ],
    )

    summary = summarize_daily_usage(access_log, period="2026-06-02", excluded_models=[])

    assert summary.requests_by_model == {"MarkItDown": 1}
    assert summary.consumed_ms_by_model == {"MarkItDown": 100}
    assert summary.requests_by_user_model == {"alice": {"MarkItDown": 1}}


def test_summarize_daily_usage_excludes_unauthenticated_when_configured(tmp_path: Path) -> None:
    access_log = _access_log(
        tmp_path,
        [
            {
                "timestamp": "2026-06-02T08:15:00+00:00",
                "client_id": "unauthenticated",
                "model": "agent",
                "latency_ms": 100,
                "olla_endpoint": "msm1-omlx-live",
            },
            {
                "timestamp": "2026-06-02T08:16:00+00:00",
                "client_id": "alice",
                "model": "coder",
                "latency_ms": 120,
                "olla_endpoint": "msm1-omlx-live",
            },
        ],
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
    access_log = _access_log(
        tmp_path,
        [
            {"timestamp": "2026-06-02T08:00:00+00:00", "client_id": "alice", "latency_ms": 10},
            {"timestamp": "2026-06-03T09:00:00+00:00", "client_id": "bob", "latency_ms": 20},
        ],
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
    access_log = _access_log(
        tmp_path,
        [
            {"timestamp": "2026-06-02T08:00:00+00:00", "client_id": "gnezim", "model": "agent", "latency_ms": 1000},
            {"timestamp": "2026-06-02T08:10:00+00:00", "client_id": "hermes", "model": "agent", "latency_ms": 9000},
            {"timestamp": "2026-06-02T08:20:00+00:00", "client_id": "dirge", "model": "coder", "latency_ms": 3000},
        ],
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


def test_summarize_daily_usage_counts_a_cold_cache_on_the_first_session_turn(tmp_path: Path) -> None:
    access_log = _access_log(
        tmp_path,
        [
            # No session id: least-connections can send each turn to another node.
            {"timestamp": "2026-06-02T08:00:00+00:00", "client_id": "alice", "sticky_id": "", "latency_ms": 5000},
            # First turn of a session pays the prefill.
            {"timestamp": "2026-06-02T08:01:00+00:00", "client_id": "alice", "sticky_id": "s1", "latency_ms": 60000},
            # Same session, still inside the sticky window: warm.
            {"timestamp": "2026-06-02T08:06:00+00:00", "client_id": "alice", "sticky_id": "s1", "latency_ms": 8000},
            # A second session starts in another hour.
            {"timestamp": "2026-06-02T09:00:00+00:00", "client_id": "alice", "sticky_id": "s2", "latency_ms": 7000},
        ],
    )

    summary = summarize_daily_usage(access_log, period="2026-06-02")
    payload = summary.to_json_dict()

    assert payload["cache"]["ttl_seconds"] == 600
    assert payload["cache"]["total"] == {"cold": 3, "warm": 1, "cold_share": 0.75}
    assert payload["cache"]["by_hour"] == {
        "08": {"cold": 2, "warm": 1, "cold_share": 0.6667},
        "09": {"cold": 1, "warm": 0, "cold_share": 1.0},
    }
    assert payload["cache"]["by_client"] == {"alice": {"cold": 3, "warm": 1, "cold_share": 0.75}}
    # The point of the metric: cold turns are read against their own tail, not the overall one.
    assert payload["cache"]["latency"]["total"] == {
        "cold": {"count": 3, "p50": 7000, "p95": 60000},
        "warm": {"count": 1, "p50": 8000, "p95": 8000},
    }
    assert payload["cache"]["latency"]["hour"]["08"] == {
        "cold": {"count": 2, "p50": 5000, "p95": 60000},
        "warm": {"count": 1, "p50": 8000, "p95": 8000},
    }


def test_summarize_daily_usage_marks_a_gap_beyond_the_sticky_ttl_cold(tmp_path: Path) -> None:
    access_log = _access_log(
        tmp_path,
        [
            # The gap runs between request starts (timestamp - latency_ms), like Olla's window.
            {"timestamp": "2026-06-02T08:00:00+00:00", "client_id": "alice", "sticky_id": "s1", "latency_ms": 1000},
            # 599 s after the first start: still inside the window.
            {"timestamp": "2026-06-02T08:10:00+00:00", "client_id": "alice", "sticky_id": "s1", "latency_ms": 2000},
            # Exactly 600 s: the TTL is a lower bound, not an expiry.
            {"timestamp": "2026-06-02T08:20:00+00:00", "client_id": "alice", "sticky_id": "s1", "latency_ms": 2000},
            # 601 s: the next turn may land on another node.
            {"timestamp": "2026-06-02T08:30:01+00:00", "client_id": "alice", "sticky_id": "s1", "latency_ms": 2000},
        ],
    )

    summary = summarize_daily_usage(access_log, period="2026-06-02")

    assert summary.cache_total.to_json_dict() == {"cold": 2, "warm": 2, "cold_share": 0.5}


def test_summarize_daily_usage_keeps_the_window_when_the_log_is_out_of_order(tmp_path: Path) -> None:
    access_log = _access_log(
        tmp_path,
        [
            {"timestamp": "2026-06-02T08:10:00+00:00", "client_id": "alice", "sticky_id": "s1", "latency_ms": 1000},
            # An older line arriving later must not rewind the window.
            {"timestamp": "2026-06-02T08:00:00+00:00", "client_id": "alice", "sticky_id": "s1", "latency_ms": 1000},
            {"timestamp": "2026-06-02T08:11:00+00:00", "client_id": "alice", "sticky_id": "s1", "latency_ms": 1000},
        ],
    )

    summary = summarize_daily_usage(access_log, period="2026-06-02")

    # Out of order, only the first line of the session is cold; a negative gap never appears.
    assert summary.cache_total.to_json_dict() == {"cold": 1, "warm": 2, "cold_share": 0.3333}


def test_summarize_daily_usage_classifies_overlapping_requests_by_start(tmp_path: Path) -> None:
    access_log = _access_log(
        tmp_path,
        [
            # The edge logs at completion, so the 54 s cold prefill lands *after* its own follow-up.
            {"timestamp": "2026-06-02T08:00:02+00:00", "client_id": "alice", "sticky_id": "s1", "latency_ms": 1000},
            {"timestamp": "2026-06-02T08:00:54+00:00", "client_id": "alice", "sticky_id": "s1", "latency_ms": 54000},
        ],
    )

    summary = summarize_daily_usage(access_log, period="2026-06-02")

    # Classified in start order: the prefill is the session's cold turn, the answer is warm.
    assert summary.cache_total.to_json_dict() == {"cold": 1, "warm": 1, "cold_share": 0.5}
    assert summary.cache_latency_total.cold["p50"] == 54000
    assert summary.cache_latency_total.warm["p50"] == 1000


def test_summarize_daily_usage_counts_a_record_without_a_timestamp_cold(tmp_path: Path) -> None:
    access_log = _access_log(
        tmp_path,
        [
            # Only reachable with period=all: a day period drops the record earlier.
            {"timestamp": "not-a-timestamp", "client_id": "alice", "sticky_id": "s1", "latency_ms": 100},
            {"timestamp": "2026-06-02T08:00:00+00:00", "client_id": "alice", "sticky_id": "s1", "latency_ms": 200},
        ],
    )

    summary = summarize_daily_usage(access_log, period="all")

    # Without a start time there is no gap to prove affinity, and the hour is unknown.
    assert summary.cache_total.to_json_dict() == {"cold": 2, "warm": 0, "cold_share": 1.0}
    assert summary.cache_by_hour["unknown"].to_json_dict() == {"cold": 1, "warm": 0, "cold_share": 1.0}


def test_summarize_daily_usage_counts_cache_inside_the_client_filter(tmp_path: Path) -> None:
    access_log = _access_log(
        tmp_path,
        [
            {"timestamp": "2026-06-02T08:00:00+00:00", "client_id": "alice", "sticky_id": "s1", "latency_ms": 1000},
            {"timestamp": "2026-06-02T08:01:00+00:00", "client_id": "bob", "sticky_id": "s1", "latency_ms": 2000},
            {"timestamp": "2026-06-02T08:02:00+00:00", "client_id": "bob", "sticky_id": "s1", "latency_ms": 3000},
        ],
    )

    everything = summarize_daily_usage(access_log, period="2026-06-02")
    only_bob = summarize_daily_usage(access_log, period="2026-06-02", client_ids=["bob"])

    assert everything.cache_total.to_json_dict() == {"cold": 1, "warm": 2, "cold_share": 0.3333}
    # The filter runs before counting, so bob's first kept turn is the cold one.
    assert only_bob.cache_total.to_json_dict() == {"cold": 1, "warm": 1, "cold_share": 0.5}
    assert only_bob.cache_by_client["bob"].to_json_dict() == {"cold": 1, "warm": 1, "cold_share": 0.5}
    assert only_bob.cache_by_hour["08"].to_json_dict() == {"cold": 1, "warm": 1, "cold_share": 0.5}
