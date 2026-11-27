"""Shared local log retention helpers for Thunder Forge.

Retention is intentionally file-based and lightweight:
- Structured JSONL logs are trimmed by per-record timestamp, and the rewrite is skipped when
  the file changed since it was read, so a run never overwrites records it did not see. The
  check-then-swap still leaves a sub-second window for a writer that already holds the file open:
  if that matters, coordinate the writer instead of trusting this helper.
- Blank lines are not counted as removed records, and a file with no expired record is
  not rewritten at all.
- Plain text and archive service logs (`*.stdout.log`, `*.stderr.log`, `*.log.gz`) are
  pruned whole by file mtime.
- `logs/olla.log` is plain text without record timestamps, so it is not a retention
  target at all: this module never rewrites or deletes it.

Trimming runs only from the operator command (`thunder-forge usage trim-logs`); the
serving process (`edge serve`) never trims logs while requests are being appended.
"""

from __future__ import annotations

import json
import os
import tempfile
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
    skipped: bool = False


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
    now: datetime | None = None,
) -> JsonlTrimResult:
    if not path.exists():
        return JsonlTrimResult(path=str(path), before_lines=0, after_lines=0, removed_lines=0)

    current_now = now or datetime.now(UTC)
    cutoff = current_now - timedelta(days=retention_days)
    before_stat = path.stat()
    original_lines = path.read_text().splitlines()
    kept_lines: list[str] = []
    before = 0

    for line in original_lines:
        stripped = line.strip()
        if not stripped:
            continue
        before += 1
        try:
            payload = json.loads(stripped)
        except json.JSONDecodeError:
            kept_lines.append(line)
            continue

        if not isinstance(payload, dict):
            kept_lines.append(line)
            continue

        timestamp = _parse_timestamp(payload.get("timestamp"))
        if timestamp is None or timestamp >= cutoff:
            kept_lines.append(line)

    after = len(kept_lines)
    if after == before:
        # Blank lines are dropped from neither the file nor the counters: a live file is not
        # rewritten just to strip them.
        return JsonlTrimResult(path=str(path), before_lines=before, after_lines=after, removed_lines=0)

    if not _replace_if_unchanged(path, kept_lines, before_stat=before_stat):
        return JsonlTrimResult(path=str(path), before_lines=before, after_lines=before, removed_lines=0, skipped=True)
    return JsonlTrimResult(path=str(path), before_lines=before, after_lines=after, removed_lines=before - after)


def _replace_if_unchanged(path: Path, kept_lines: list[str], *, before_stat: os.stat_result) -> bool:
    """Swap kept_lines in place of path, unless the file changed or vanished since before_stat."""
    temp_path: Path | None = None
    try:
        fd, temp_name = tempfile.mkstemp(dir=path.parent, prefix=f".{path.name}.", suffix=".tmp")
        temp_path = Path(temp_name)
        with os.fdopen(fd, "w") as handle:
            handle.write("\n".join(kept_lines) + ("\n" if kept_lines else ""))
        os.chmod(temp_path, before_stat.st_mode & 0o777)
        try:
            current_stat = path.stat()
        except OSError:
            return False
        if (current_stat.st_size, current_stat.st_mtime_ns) != (before_stat.st_size, before_stat.st_mtime_ns):
            return False
        os.replace(temp_path, path)
        return True
    finally:
        if temp_path is not None:
            temp_path.unlink(missing_ok=True)


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

    jsonl_targets = (edge_access_path, node_metrics_log_path)

    jsonl_results: list[JsonlTrimResult] = []
    for path in jsonl_targets:
        jsonl_results.append(trim_jsonl_by_age(path, retention_days=retention_days, now=current_now))

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