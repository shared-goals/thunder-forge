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


def test_trim_jsonl_by_age_keeps_records_appended_by_a_live_writer(tmp_path: Path, monkeypatch) -> None:
    path = tmp_path / "tf-edge-access.jsonl"
    old_record = json.dumps({"timestamp": "2026-06-01T00:00:00+00:00", "id": "old"})
    live_record = json.dumps({"timestamp": "2026-06-05T00:00:00+00:00", "id": "live"})
    path.write_text(old_record + "\n")
    original_read_text = Path.read_text

    def read_text_then_append(self: Path, *args: object, **kwargs: object) -> str:
        text = original_read_text(self, *args, **kwargs)
        if self == path:
            with self.open("a") as handle:
                handle.write(live_record + "\n")
        return text

    monkeypatch.setattr(Path, "read_text", read_text_then_append)

    result = trim_jsonl_by_age(path, retention_days=3, now=datetime(2026, 6, 5, tzinfo=UTC))

    # The append happened after the read: it must survive the swap, and the old record must not.
    assert result.skipped is False
    assert result.removed_lines == 1
    assert original_read_text(path).splitlines() == [live_record]


def test_trim_jsonl_by_age_does_not_count_blank_lines_as_removed(tmp_path: Path) -> None:
    path = tmp_path / "events.jsonl"
    path.write_text(
        "\n".join(
            [
                json.dumps({"timestamp": "2026-06-01T00:00:00+00:00", "id": "old"}),
                "",
                json.dumps({"timestamp": "2026-06-05T00:00:00+00:00", "id": "new"}),
                "   ",
            ]
        )
        + "\n"
    )

    result = trim_jsonl_by_age(path, retention_days=3, now=datetime(2026, 6, 5, tzinfo=UTC))

    assert result.before_lines == 2
    assert result.after_lines == 1
    assert result.removed_lines == 1
    assert result.skipped is False


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


def test_trim_jsonl_by_age_follows_a_symlinked_log(tmp_path: Path) -> None:
    real = tmp_path / "real.jsonl"
    real.write_text(
        json.dumps({"timestamp": "2026-06-01T00:00:00+00:00", "id": "old"})
        + "\n"
        + json.dumps({"timestamp": "2026-06-05T00:00:00+00:00", "id": "new"})
        + "\n"
    )
    link = tmp_path / "logs" / "tf-edge-access.jsonl"
    link.parent.mkdir()
    link.symlink_to(real)

    result = trim_jsonl_by_age(link, retention_days=3, now=datetime(2026, 6, 5, tzinfo=UTC))

    assert result.removed_lines == 1
    assert link.is_symlink()
    assert json.loads(real.read_text().splitlines()[0])["id"] == "new"


def test_trim_jsonl_by_age_leaves_the_log_when_it_cannot_keep_the_owner(
    tmp_path: Path, monkeypatch
) -> None:
    import thunder_forge.cluster.log_retention as retention

    path = tmp_path / "events.jsonl"
    content = json.dumps({"timestamp": "2026-06-01T00:00:00+00:00", "id": "old"}) + "\n"
    path.write_text(content)

    def deny(*args: object, **kwargs: object) -> None:
        raise PermissionError("not the owner")

    monkeypatch.setattr(retention.os, "chown", deny)
    result = trim_jsonl_by_age(path, retention_days=3, now=datetime(2026, 6, 5, tzinfo=UTC))

    assert result.skipped is True
    assert path.read_text() == content


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
    node_metrics.write_text(json.dumps({"timestamp": "2026-06-05T00:00:00+00:00", "node_id": "msm1"}) + "\n")

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
    metrics_paths = [result.path for result in summary.jsonl_results]
    assert str(node_metrics) in metrics_paths
    assert old_service_log.exists() is False


def test_trim_jsonl_by_age_leaves_a_file_with_nothing_to_trim_untouched(tmp_path: Path) -> None:
    path = tmp_path / "events.jsonl"
    content = "\n" + json.dumps({"timestamp": "2026-06-05T00:00:00+00:00", "id": "new"}) + "\n\n"
    path.write_text(content)

    result = trim_jsonl_by_age(path, retention_days=3, now=datetime(2026, 6, 5, tzinfo=UTC))

    assert result.removed_lines == 0
    assert path.read_text() == content


def test_trim_local_logs_leaves_plain_text_olla_log_alone(tmp_path: Path) -> None:
    repo = tmp_path
    logs = repo / "logs"
    logs.mkdir()

    olla_log = logs / "olla.log"
    olla_log.write_text("INFO proxy request routed\n\n2026-06-01T00:00:00Z still plain text\n")
    _set_mtime(olla_log, datetime(2026, 5, 1, tzinfo=UTC))

    config = ClusterConfig(services=ServiceConfig(edge_access_log="logs/tf-edge-access.jsonl", log_retention_days=3))
    summary = trim_local_logs(
        repo_root=repo,
        config=config,
        node_metrics_log_path=logs / "tf-node-metrics.jsonl",
        retention_days=3,
        now=datetime(2026, 6, 5, tzinfo=UTC),
    )

    assert str(olla_log) not in [result.path for result in summary.jsonl_results]
    assert str(olla_log) not in [result.path for result in summary.file_results]
    assert olla_log.read_text() == "INFO proxy request routed\n\n2026-06-01T00:00:00Z still plain text\n"


def test_trim_jsonl_by_age_keeps_mode_and_owner_when_asked(tmp_path: Path) -> None:
    path = tmp_path / "events.jsonl"
    path.write_text(json.dumps({"timestamp": "2026-06-01T00:00:00+00:00"}) + "\n")
    os.chmod(path, 0o640)

    result = trim_jsonl_by_age(path, retention_days=3, now=datetime(2026, 6, 5, tzinfo=UTC))

    assert result.skipped is False
    assert path.stat().st_mode & 0o777 == 0o640


def test_trim_jsonl_by_age_skips_a_directory_it_cannot_write(tmp_path: Path) -> None:
    log_dir = tmp_path / "logs"
    log_dir.mkdir()
    path = log_dir / "events.jsonl"
    path.write_text(json.dumps({"timestamp": "2026-06-01T00:00:00+00:00"}) + "\n")
    os.chmod(log_dir, 0o500)

    try:
        result = trim_jsonl_by_age(path, retention_days=3, now=datetime(2026, 6, 5, tzinfo=UTC))
    finally:
        os.chmod(log_dir, 0o700)

    assert result.skipped is True
    assert result.removed_lines == 0


def test_prune_file_if_older_than_removes_a_symlinked_logs_target(tmp_path: Path) -> None:
    real = tmp_path / "real.log.gz"
    real.write_text("x")
    _set_mtime(real, datetime(2026, 5, 1, tzinfo=UTC))
    link = tmp_path / "link.log.gz"
    link.symlink_to(real)

    result = prune_file_if_older_than(link, retention_days=3, now=datetime(2026, 6, 5, tzinfo=UTC))

    assert result.removed is True
    assert not real.exists(), "the target must go, not just the link"
