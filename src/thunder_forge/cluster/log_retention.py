"""Shared local log retention helpers for Thunder Forge.

Retention is intentionally file-based and lightweight:
- JSONL logs are trimmed by per-record timestamp when available.
- Plain text and archive logs are pruned by file mtime.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path

from thunder_forge.cluster.config import ClusterConfig


def _parse_timestamp(raw: object) -> datetime | None:
    if not isinstance(raw, str):
        return None
    value = raw.strip()
    if not value:
        return None

    if value.endswith("Z"):
        value = f"{value[:-1]}+00:00"
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError:
        parsed = None

    if parsed is None:
        for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%dT%H:%M:%S"):
            try:
                parsed = datetime.strptime(value, fmt).replace(tzinfo=UTC)
                break
            except ValueError:
                continue

    if parsed is None:
        return None
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=UTC)
    return parsed.astimezone(UTC)


@dataclass(frozen=True)
class JsonlTrimResult:
    path: str
    before_lines: int
    after_lines: int
    removed_lines: int


@dataclass(frozen=True)
class FilePruneResult:
    path: str
    removed: bool


@dataclass(frozen=True)
class LogTrimSummary:
    retention_days: int
    cutoff: str
    jsonl_results: list[JsonlTrimResult]
    file_results: list[FilePruneResult]


def trim_jsonl_by_age(
    path: Path,
    *,
    retention_days: int,
    timestamp_key: str = "timestamp",
    now: datetime | None = None,
) -> JsonlTrimResult:
    if not path.exists():
        return JsonlTrimResult(path=str(path), before_lines=0, after_lines=0, removed_lines=0)

    current_now = now or datetime.now(UTC)
    cutoff = current_now - timedelta(days=retention_days)
    original_lines = path.read_text().splitlines()
    kept_lines: list[str] = []

    for line in original_lines:
        stripped = line.strip()
        if not stripped:
            continue
        try:
            payload = json.loads(stripped)
        except json.JSONDecodeError:
            kept_lines.append(line)
            continue

        if not isinstance(payload, dict):
            kept_lines.append(line)
            continue

        timestamp = _parse_timestamp(payload.get(timestamp_key))
        if timestamp is None or timestamp >= cutoff:
            kept_lines.append(line)

    if kept_lines != original_lines:
        path.write_text("\n".join(kept_lines) + ("\n" if kept_lines else ""))

    before = len(original_lines)
    after = len(kept_lines)
    return JsonlTrimResult(path=str(path), before_lines=before, after_lines=after, removed_lines=before - after)


def prune_file_if_older_than(
    path: Path,
    *,
    retention_days: int,
    now: datetime | None = None,
) -> FilePruneResult:
    if not path.exists() or not path.is_file():
        return FilePruneResult(path=str(path), removed=False)

    current_now = now or datetime.now(UTC)
    cutoff = current_now - timedelta(days=retention_days)
    modified = datetime.fromtimestamp(path.stat().st_mtime, tz=UTC)
    if modified < cutoff:
        path.unlink(missing_ok=True)
        return FilePruneResult(path=str(path), removed=True)
    return FilePruneResult(path=str(path), removed=False)


def trim_local_logs(
    *,
    repo_root: Path,
    config: ClusterConfig,
    node_metrics_log_path: Path,
    retention_days: int,
    now: datetime | None = None,
) -> LogTrimSummary:
    current_now = now or datetime.now(UTC)
    cutoff = current_now - timedelta(days=retention_days)

    edge_access_path = Path(config.services.edge_access_log)
    if not edge_access_path.is_absolute():
        edge_access_path = repo_root / edge_access_path

    jsonl_targets: list[tuple[Path, str]] = [
        (edge_access_path, "timestamp"),
        (node_metrics_log_path, "timestamp"),
        (repo_root / "logs" / "olla.log", "timestamp"),
    ]

    jsonl_results: list[JsonlTrimResult] = []
    for path, timestamp_key in jsonl_targets:
        jsonl_results.append(
            trim_jsonl_by_age(
                path,
                retention_days=retention_days,
                timestamp_key=timestamp_key,
                now=current_now,
            )
        )

    file_candidates: set[Path] = set()
    logs_dir = repo_root / "logs"
    for pattern in ("*.stdout.log", "*.stderr.log", "*.log.gz"):
        file_candidates.update(logs_dir.glob(pattern))

    file_results: list[FilePruneResult] = []
    for path in sorted(file_candidates):
        file_results.append(prune_file_if_older_than(path, retention_days=retention_days, now=current_now))

    return LogTrimSummary(
        retention_days=retention_days,
        cutoff=cutoff.isoformat(),
        jsonl_results=jsonl_results,
        file_results=file_results,
    )