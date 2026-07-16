"""Daily usage aggregation helpers for Thunder Forge edge accounting.

The first implementation stays file-backed and JSONL-friendly so operators can
summarize usage with the Thunder Forge CLI or a downstream CLI like DuckDB.
"""

from __future__ import annotations

import json
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path


_DEFAULT_MODELS_EXCLUDED_FROM_USAGE = {"markitdown"}


def _parse_timestamp(raw: object) -> datetime | None:
    if not isinstance(raw, str) or not raw.strip():
        return None
    value = raw.strip()
    if value.endswith("Z"):
        value = f"{value[:-1]}+00:00"
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        local_tz = datetime.now().astimezone().tzinfo
        return parsed.replace(tzinfo=local_tz)
    return parsed


def _hour_bucket(timestamp: datetime | None) -> str:
    if timestamp is None:
        return "unknown"
    return f"{timestamp.hour:02d}"


def _derive_node_id_from_olla_endpoint(raw: object) -> str:
    if not isinstance(raw, str):
        return ""
    endpoint = raw.strip()
    if not endpoint:
        return ""
    if "-omlx-" in endpoint:
        return endpoint.split("-omlx-", 1)[0]
    return endpoint


def _load_jsonl_lines(path: Path) -> tuple[list[dict[str, object]], int]:
    records: list[dict[str, object]] = []
    invalid_lines = 0
    if not path.exists():
        return records, invalid_lines

    for line in path.read_text().splitlines():
        if not line.strip():
            continue
        try:
            payload = json.loads(line)
        except json.JSONDecodeError:
            invalid_lines += 1
            continue
        if not isinstance(payload, dict):
            invalid_lines += 1
            continue
        records.append(payload)
    return records, invalid_lines


def _ordered_counter(counter: Counter[str]) -> dict[str, int]:
    return dict(sorted(counter.items()))


def _ordered_nested_counter(counter: dict[str, Counter[str]]) -> dict[str, dict[str, int]]:
    return {key: dict(sorted(value.items())) for key, value in sorted(counter.items())}


def _as_non_negative_int(raw: object) -> int | None:
    if isinstance(raw, bool) or not isinstance(raw, int):
        return None
    return raw if raw >= 0 else None


def _ordered_list_map(values: dict[str, list[str]]) -> dict[str, list[str]]:
    return {key: values[key] for key in sorted(values)}


def _normalize_excluded_models(excluded_models: list[str] | set[str] | None) -> set[str]:
    if excluded_models is None:
        return set(_DEFAULT_MODELS_EXCLUDED_FROM_USAGE)
    normalized = {item.strip().lower() for item in excluded_models if isinstance(item, str) and item.strip()}
    return normalized


def _include_model_in_usage(model_name: str, excluded_models: set[str]) -> bool:
    return model_name.strip().lower() not in excluded_models


def extract_hot_loaded_models(model_statuses: dict[str, dict[str, object]]) -> list[str]:
    """Return hot-loaded model ids sorted by latest access first."""

    ranked: list[tuple[float, str]] = []
    for model_id, status in model_statuses.items():
        if not isinstance(status, dict):
            continue
        loaded = status.get("loaded")
        if loaded is not True:
            continue
        last_access = status.get("last_access")
        if isinstance(last_access, (int, float)):
            rank = float(last_access)
        else:
            rank = 0.0
        ranked.append((rank, model_id))

    ranked.sort(key=lambda item: (-item[0], item[1]))
    return [model_id for _, model_id in ranked]


@dataclass(frozen=True)
class DailyUsageSummary:
    period: str
    generated_at: str
    access_log_path: str
    node_metrics_path: str
    requests_total: int
    invalid_lines: int
    consumed_ms_total: int
    requests_by_user: dict[str, int] = field(default_factory=dict)
    consumed_ms_by_user: dict[str, int] = field(default_factory=dict)
    requests_by_user_model: dict[str, dict[str, int]] = field(default_factory=dict)
    requests_by_node: dict[str, int] = field(default_factory=dict)
    requests_by_node_user: dict[str, dict[str, int]] = field(default_factory=dict)
    consumed_ms_by_node: dict[str, int] = field(default_factory=dict)
    requests_by_node_model: dict[str, dict[str, int]] = field(default_factory=dict)
    requests_by_model: dict[str, int] = field(default_factory=dict)
    consumed_ms_by_model: dict[str, int] = field(default_factory=dict)
    requests_by_hour: dict[str, int] = field(default_factory=dict)
    requests_by_node_hour: dict[str, dict[str, int]] = field(default_factory=dict)
    node_metrics_present: bool = False
    node_hot_loaded_models: dict[str, list[str]] = field(default_factory=dict)
    node_hot_loaded_count: dict[str, int] = field(default_factory=dict)

    def to_json_dict(self) -> dict[str, object]:
        payload: dict[str, object] = {
            "period": self.period,
            "generated_at": self.generated_at,
            "access_log_path": self.access_log_path,
            "node_metrics_path": self.node_metrics_path,
            "requests": {
                "total": self.requests_total,
                "by_user": self.requests_by_user,
                "by_user_model": self.requests_by_user_model,
                "by_node": self.requests_by_node,
                "by_node_user": self.requests_by_node_user,
                "by_node_model": self.requests_by_node_model,
                "by_model": self.requests_by_model,
                "by_hour": self.requests_by_hour,
                "by_node_hour": self.requests_by_node_hour,
            },
            "consumed_ms": {
                "total": self.consumed_ms_total,
                "by_user": self.consumed_ms_by_user,
                "by_node": self.consumed_ms_by_node,
                "by_model": self.consumed_ms_by_model,
            },
            "invalid_lines": self.invalid_lines,
        }
        if self.node_metrics_present:
            payload["node_metrics"] = {
                "hot_loaded_models": self.node_hot_loaded_models,
                "hot_loaded_count": self.node_hot_loaded_count,
            }
        return payload


def summarize_daily_usage(
    access_log_path: Path,
    *,
    period: str | None = None,
    node_metrics_path: Path | None = None,
    excluded_models: list[str] | set[str] | None = None,
    exclude_unauthenticated: bool = False,
) -> DailyUsageSummary:
    """Summarize daily TF usage from JSONL request logs and optional node samples."""
    if period == "all":
        period = None

    requests_by_user: Counter[str] = Counter()
    consumed_ms_by_user: Counter[str] = Counter()
    requests_by_user_model: dict[str, Counter[str]] = defaultdict(Counter)
    requests_by_node: Counter[str] = Counter()
    requests_by_node_user: dict[str, Counter[str]] = defaultdict(Counter)
    consumed_ms_by_node: Counter[str] = Counter()
    requests_by_model: Counter[str] = Counter()
    consumed_ms_by_model: Counter[str] = Counter()
    requests_by_hour: Counter[str] = Counter()
    requests_by_node_model: dict[str, Counter[str]] = defaultdict(Counter)
    requests_by_node_hour: dict[str, Counter[str]] = defaultdict(Counter)
    requests_total = 0
    invalid_lines = 0
    consumed_ms_total = 0
    excluded_model_names = _normalize_excluded_models(excluded_models)

    records, invalid = _load_jsonl_lines(access_log_path)
    invalid_lines += invalid
    for payload in records:
        timestamp = _parse_timestamp(payload.get("timestamp"))
        if period is not None and (timestamp is None or timestamp.date().isoformat() != period):
            continue

        client_id = payload.get("client_id")
        if not isinstance(client_id, str) or not client_id.strip():
            invalid_lines += 1
            continue
        client_id = client_id.strip()
        if exclude_unauthenticated and client_id == "unauthenticated":
            continue

        latency_ms = payload.get("latency_ms")
        if not isinstance(latency_ms, int):
            invalid_lines += 1
            continue

        requests_total += 1
        consumed_ms_total += latency_ms
        node_id = _derive_node_id_from_olla_endpoint(payload.get("olla_endpoint")).strip()

        model = payload.get("model")
        model_name = model.strip() if isinstance(model, str) else ""
        hour_bucket = _hour_bucket(timestamp)

        requests_by_user[client_id] += 1
        consumed_ms_by_user[client_id] += latency_ms
        if model_name and _include_model_in_usage(model_name, excluded_model_names):
            requests_by_user_model[client_id][model_name] += 1

        if node_id:
            requests_by_node[node_id] += 1
            requests_by_node_user[node_id][client_id] += 1
            consumed_ms_by_node[node_id] += latency_ms
            if model_name and _include_model_in_usage(model_name, excluded_model_names):
                requests_by_node_model[node_id][model_name] += 1
            if timestamp is not None:
                requests_by_node_hour[node_id][hour_bucket] += 1

        if model_name and _include_model_in_usage(model_name, excluded_model_names):
            requests_by_model[model_name] += 1
            consumed_ms_by_model[model_name] += latency_ms

        if timestamp is not None:
            requests_by_hour[hour_bucket] += 1

    node_metrics_path_str = ""
    node_metrics_present = False
    node_hot_loaded_models: dict[str, list[str]] = {}
    node_hot_loaded_count: dict[str, int] = {}
    latest_node_metric_timestamp: dict[str, datetime | None] = {}
    if node_metrics_path is not None:
        node_metrics_path_str = str(node_metrics_path)
        node_records, invalid_node_lines = _load_jsonl_lines(node_metrics_path)
        invalid_lines += invalid_node_lines
        for payload in node_records:
            timestamp = _parse_timestamp(payload.get("timestamp"))
            if period is not None and (timestamp is None or timestamp.date().isoformat() != period):
                continue
            node_id = payload.get("node_id")
            if not isinstance(node_id, str) or not node_id.strip():
                invalid_lines += 1
                continue
            node_id = node_id.strip()

            raw_models = payload.get("hot_loaded_models")
            hot_loaded_models: list[str] = []
            if isinstance(raw_models, list):
                hot_loaded_models = [
                    item.strip()
                    for item in raw_models
                    if isinstance(item, str)
                    and item.strip()
                    and _include_model_in_usage(item, excluded_model_names)
                ]
            hot_loaded_count = _as_non_negative_int(payload.get("hot_loaded_count"))
            if isinstance(raw_models, list):
                hot_loaded_count = len(hot_loaded_models)
            if hot_loaded_count is None and not hot_loaded_models:
                continue

            previous_timestamp = latest_node_metric_timestamp.get(node_id)
            if previous_timestamp is not None and timestamp is not None and timestamp < previous_timestamp:
                continue

            node_metrics_present = True
            latest_node_metric_timestamp[node_id] = timestamp
            if hot_loaded_models:
                node_hot_loaded_models[node_id] = hot_loaded_models
            if hot_loaded_count is not None:
                node_hot_loaded_count[node_id] = hot_loaded_count

    return DailyUsageSummary(
        period=period or "all",
        generated_at=datetime.now(UTC).isoformat(),
        access_log_path=str(access_log_path),
        node_metrics_path=node_metrics_path_str,
        requests_total=requests_total,
        invalid_lines=invalid_lines,
        consumed_ms_total=consumed_ms_total,
        requests_by_user=_ordered_counter(requests_by_user),
        consumed_ms_by_user=_ordered_counter(consumed_ms_by_user),
        requests_by_user_model=_ordered_nested_counter(requests_by_user_model),
        requests_by_node=_ordered_counter(requests_by_node),
        requests_by_node_user=_ordered_nested_counter(requests_by_node_user),
        consumed_ms_by_node=_ordered_counter(consumed_ms_by_node),
        requests_by_node_model=_ordered_nested_counter(requests_by_node_model),
        requests_by_model=_ordered_counter(requests_by_model),
        consumed_ms_by_model=_ordered_counter(consumed_ms_by_model),
        requests_by_hour=_ordered_counter(requests_by_hour),
        requests_by_node_hour=_ordered_nested_counter(requests_by_node_hour),
        node_metrics_present=node_metrics_present,
        node_hot_loaded_models=_ordered_list_map(node_hot_loaded_models),
        node_hot_loaded_count=dict(sorted(node_hot_loaded_count.items())),
    )
