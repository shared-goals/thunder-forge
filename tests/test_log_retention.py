from __future__ import annotations

import json
import os
from datetime import UTC, datetime
from pathlib import Path

from thunder_forge.cluster.config import ClusterConfig, ServiceConfig
from thunder_forge.cluster.log_retention import prune_file_if_older_than, trim_jsonl_by_age, trim_local_logs


def _set_mtime(path: Path, dt: datetime) -> None:
    ts = dt.timestamp()
    os.utime(path, (ts, ts))


def test_trim_jsonl_by_age_filters_old_timestamped_records(tmp_path: Path) -> None:
    path = tmp_path / "events.jsonl"
    path.write_text(
        "\n".join(
            [
                json.dumps({"timestamp": "2026-06-01T00:00:00+00:00", "id": "old"}),
                json.dumps({"timestamp": "2026-06-04T00:00:00+00:00", "id": "new"}),
                "not-json",
            ]
        )
        + "\n"
    )

    result = trim_jsonl_by_age(
        path,
        retention_days=3,
        now=datetime(2026, 6, 5, tzinfo=UTC),
    )

    assert result.before_lines == 3
    assert result.after_lines == 2
    assert result.removed_lines == 1
    output_lines = path.read_text().splitlines()
    assert len(output_lines) == 2
    assert json.loads(output_lines[0])["id"] == "new"
    assert output_lines[1] == "not-json"


def test_prune_file_if_older_than_uses_mtime(tmp_path: Path) -> None:
    path = tmp_path / "old.stderr.log"
    path.write_text("noise\n")
    _set_mtime(path, datetime(2026, 5, 30, tzinfo=UTC))

    result = prune_file_if_older_than(
        path,
        retention_days=3,
        now=datetime(2026, 6, 5, tzinfo=UTC),
    )

    assert result.removed is True
    assert not path.exists()


def test_trim_local_logs_covers_jsonl_and_service_logs(tmp_path: Path) -> None:
    repo = tmp_path
    logs = repo / "logs"
    logs.mkdir()

    edge_access = logs / "tf-edge-access.jsonl"
    edge_access.write_text(
        json.dumps({"timestamp": "2026-06-01T00:00:00+00:00", "id": "old"})
        + "\n"
        + json.dumps({"timestamp": "2026-06-05T00:00:00+00:00", "id": "new"})
        + "\n"
    )

    node_metrics = logs / "tf-node-metrics.jsonl"
    node_metrics.write_text(json.dumps({"timestamp": "2026-06-05T00:00:00+00:00", "node_name": "msm1"}) + "\n")

    old_service_log = logs / "edge-40116.stderr.log"
    old_service_log.write_text("traceback\n")
    _set_mtime(old_service_log, datetime(2026, 5, 31, tzinfo=UTC))

    config = ClusterConfig(services=ServiceConfig(edge_access_log="logs/tf-edge-access.jsonl", log_retention_days=3))
    summary = trim_local_logs(
        repo_root=repo,
        config=config,
        node_metrics_log_path=node_metrics,
        retention_days=3,
        now=datetime(2026, 6, 5, tzinfo=UTC),
    )

    edge_result = next(result for result in summary.jsonl_results if result.path.endswith("tf-edge-access.jsonl"))
    assert edge_result.removed_lines == 1
    assert old_service_log.exists() is False
