"""Daily usage aggregation helpers for Thunder Forge edge accounting.

The first implementation stays file-backed and JSONL-friendly so operators can
summarize usage with the Thunder Forge CLI or a downstream CLI like DuckDB.
"""

from __future__ import annotations

import json
import math
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from pathlib import Path

_DEFAULT_MODELS_EXCLUDED_FROM_USAGE = {"markitdown"}
# Sliding window of Olla sticky sessions. The generated Olla config writes this number and cold/warm
# accounting reads it, so the constant lives here: the aggregator stays importable without config.
DEFAULT_STICKY_IDLE_TTL_SECONDS = 600


def resolve_usage_period(raw: str) -> str:
    """Normalize a usage period argument to a local date or 'all'."""
    value = (raw or "").strip()
    if not value or value == "today":
        return datetime.now().astimezone().date().isoformat()
    if value == "all":
        return "all"
    return datetime.strptime(value, "%Y-%m-%d").date().isoformat()


def _in_period(timestamp: datetime | None, period: str | None) -> bool:
    """True when a record belongs to the requested period; 'all' accepts everything."""
    if period is None or period == "all":
        return True
    return timestamp is not None and timestamp.date().isoformat() == period


def select_usage_events(log_path: Path, *, period: str | None = None) -> list[dict[str, object]]:
    """Return raw log records for one period, preserving log order."""
    records, _ = _load_jsonl_lines(log_path)
    return [payload for payload in records if _in_period(_parse_timestamp(payload.get("timestamp")), period)]


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


def _ordered_counter_by_weight(counter: Counter[str], weights: dict[str, int]) -> dict[str, int]:
    """Order entries by aggregate weight desc, then key asc; ties keep key order."""
    return dict(sorted(counter.items(), key=lambda item: (-weights.get(item[0], item[1]), item[0])))


def nearest_rank_percentile(values: list[int], percentile: float) -> int:
    """Return the nearest-rank percentile of integer samples."""
    if not values:
        return 0
    ordered = sorted(values)
    rank = max(1, math.ceil((percentile / 100) * len(ordered)))
    return ordered[rank - 1]


def peak_concurrency(intervals: list[tuple[datetime, datetime]]) -> int:
    """Return the maximum number of overlapping request intervals."""
    events: list[tuple[datetime, int]] = []
    for started, finished in intervals:
        events.append((started, 1))
        events.append((finished, -1))
    events.sort()
    current = 0
    peak = 0
    for _, delta in events:
        current += delta
        peak = max(peak, current)
    return peak


def _empty_cache_latency() -> dict[str, list[int]]:
    return {"cold": [], "warm": []}


@dataclass(frozen=True)
class CacheBucket:
    """Cold/warm split of one scope; the share is derived, never stored."""

    cold: int = 0
    warm: int = 0

    @classmethod
    def from_counts(cls, counts: Counter[str]) -> CacheBucket:
        return cls(cold=counts.get("cold", 0), warm=counts.get("warm", 0))

    def to_json_dict(self) -> dict[str, object]:
        total = self.cold + self.warm
        return {
            "cold": self.cold,
            "warm": self.warm,
            "cold_share": round(self.cold / total, 4) if total else 0.0,
        }


@dataclass(frozen=True)
class CacheLatency:
    """Latency percentiles of the cold and the warm turns; both kinds are always present."""

    cold: dict[str, int] = field(default_factory=dict)
    warm: dict[str, int] = field(default_factory=dict)

    @classmethod
    def from_samples(cls, samples: dict[str, list[int]]) -> CacheLatency:
        return cls(cold=_latency_core(samples["cold"]), warm=_latency_core(samples["warm"]))

    def to_json_dict(self) -> dict[str, dict[str, int]]:
        return {"cold": self.cold, "warm": self.warm}


def _latency_core(values: list[int]) -> dict[str, int]:
    return {
        "count": len(values),
        "p50": nearest_rank_percentile(values, 50),
        "p95": nearest_rank_percentile(values, 95),
    }


def _request_start_key(payload: dict[str, object]) -> tuple[bool, datetime]:
    """Sort key placing a request at its start time; an unreadable record sorts last."""
    timestamp = _parse_timestamp(payload.get("timestamp"))
    latency_ms = payload.get("latency_ms")
    if timestamp is None or not isinstance(latency_ms, int):
        return True, datetime.min.replace(tzinfo=UTC)
    return False, timestamp - timedelta(milliseconds=latency_ms)


def _is_cold(started: datetime, previous: datetime | None) -> bool:
    """True when a session turn cannot reuse the node's prefix cache.

    No previous turn in the period means no gap to rely on; a gap longer than the sticky TTL means
    affinity already expired.
    """
    return previous is None or (started - previous).total_seconds() > DEFAULT_STICKY_IDLE_TTL_SECONDS


def _as_non_negative_int(raw: object) -> int | None:
    """Accept ints and finite floats; oMLX reports sizes as either."""
    if isinstance(raw, bool) or not isinstance(raw, (int, float)):
        return None
    if isinstance(raw, float) and not math.isfinite(raw):
        return None
    value = int(raw)
    return value if value >= 0 else None


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
    consumed_ms_by_node: dict[str, int] = field(default_factory=dict)
    requests_by_model: dict[str, int] = field(default_factory=dict)
    consumed_ms_by_model: dict[str, int] = field(default_factory=dict)
    requests_by_hour: dict[str, dict[str, object]] = field(default_factory=dict)
    requests_by_node_hour: dict[str, dict[str, int]] = field(default_factory=dict)
    latency_ms_by_hour: dict[str, dict[str, int]] = field(default_factory=dict)
    cache_total: CacheBucket = field(default_factory=CacheBucket)
    cache_by_hour: dict[str, CacheBucket] = field(default_factory=dict)
    cache_by_client: dict[str, CacheBucket] = field(default_factory=dict)
    cache_latency_total: CacheLatency = field(default_factory=CacheLatency)
    cache_latency_by_hour: dict[str, CacheLatency] = field(default_factory=dict)
    peak_concurrency_by_hour: dict[str, int] = field(default_factory=dict)
    node_metrics_present: bool = False
    node_hot_loaded_models: dict[str, list[str]] = field(default_factory=dict)
    node_hot_loaded_count: dict[str, int] = field(default_factory=dict)
    node_load_by_hour: dict[str, dict[str, dict[str, float | None]]] = field(default_factory=dict)
    node_model_memory: dict[str, dict[str, dict[str, int]]] = field(default_factory=dict)
    node_model_memory_sampled_at: dict[str, str] = field(default_factory=dict)
    available_users: list[str] = field(default_factory=list)
    selected_users: list[str] = field(default_factory=list)
    known_users: list[str] = field(default_factory=list)

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
            "users": {
                "available": self.available_users,
                "selected": self.selected_users,
                "known": self.known_users,
            },
            "latency_ms": {
                "by_hour": self.latency_ms_by_hour,
            },
            "cache": {
                "ttl_seconds": DEFAULT_STICKY_IDLE_TTL_SECONDS,
                "total": self.cache_total.to_json_dict(),
                "by_hour": {hour: bucket.to_json_dict() for hour, bucket in self.cache_by_hour.items()},
                "by_client": {client: bucket.to_json_dict() for client, bucket in self.cache_by_client.items()},
                "latency": {
                    "total": self.cache_latency_total.to_json_dict(),
                    "hour": {hour: latency.to_json_dict() for hour, latency in self.cache_latency_by_hour.items()},
                },
            },
            "concurrency": {"peak_by_hour": self.peak_concurrency_by_hour},
            "invalid_lines": self.invalid_lines,
        }
        if self.node_metrics_present:
            payload["node_metrics"] = {
                "hot_loaded_models": self.node_hot_loaded_models,
                "hot_loaded_count": self.node_hot_loaded_count,
                "load_by_hour": self.node_load_by_hour,
                "model_memory": self.node_model_memory,
                "model_memory_sampled_at": self.node_model_memory_sampled_at,
            }
        return payload


def summarize_daily_usage(
    access_log_path: Path,
    *,
    period: str | None = None,
    node_metrics_path: Path | None = None,
    excluded_models: list[str] | set[str] | None = None,
    exclude_unauthenticated: bool = False,
    client_ids: list[str] | set[str] | None = None,
    known_client_ids: list[str] | None = None,
) -> DailyUsageSummary:
    """Summarize daily TF usage from JSONL request logs and optional node samples."""
    if period == "all":
        period = None

    requests_by_user: Counter[str] = Counter()
    consumed_ms_by_user: Counter[str] = Counter()
    requests_by_user_model: dict[str, Counter[str]] = defaultdict(Counter)
    requests_by_node: Counter[str] = Counter()
    consumed_ms_by_node: Counter[str] = Counter()
    requests_by_model: Counter[str] = Counter()
    consumed_ms_by_model: Counter[str] = Counter()
    requests_by_hour: Counter[str] = Counter()
    consumed_ms_by_hour: Counter[str] = Counter()
    requests_by_hour_model: dict[str, Counter[str]] = defaultdict(Counter)
    requests_by_hour_user: dict[str, Counter[str]] = defaultdict(Counter)
    requests_by_node_hour: dict[str, Counter[str]] = defaultdict(Counter)
    requests_total = 0
    invalid_lines = 0
    consumed_ms_total = 0
    latency_values_by_hour: dict[str, list[int]] = defaultdict(list)
    intervals_by_hour: dict[str, list[tuple[datetime, datetime]]] = defaultdict(list)
    cache_by_hour: dict[str, Counter[str]] = defaultdict(Counter)
    cache_by_client: dict[str, Counter[str]] = defaultdict(Counter)
    cache_latency_values: dict[str, list[int]] = _empty_cache_latency()
    cache_latency_values_by_hour: dict[str, dict[str, list[int]]] = defaultdict(_empty_cache_latency)
    # Tracked after the period and client filters, so the first kept request of a session is cold.
    last_seen_by_session: dict[str, datetime] = {}
    excluded_model_names = _normalize_excluded_models(excluded_models)
    selected_clients = {item.strip() for item in client_ids or [] if item and item.strip()} or None
    seen_clients: set[str] = set()

    records, invalid = _load_jsonl_lines(access_log_path)
    invalid_lines += invalid
    # The edge writes a record when the request finishes, so log order is completion order: a long
    # cold turn can be logged after the short follow-up that follows it. Classify in start order.
    records.sort(key=_request_start_key)
    for payload in records:
        timestamp = _parse_timestamp(payload.get("timestamp"))
        if not _in_period(timestamp, period):
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
        seen_clients.add(client_id)
        if selected_clients is not None and client_id not in selected_clients:
            continue

        requests_total += 1
        consumed_ms_total += latency_ms
        node_id = _derive_node_id_from_olla_endpoint(payload.get("olla_endpoint")).strip()

        model = payload.get("model")
        model_name = model.strip() if isinstance(model, str) else ""
        hour_bucket = _hour_bucket(timestamp)
        latency_values_by_hour[hour_bucket].append(latency_ms)
        # The TTL window Olla keeps starts at request arrival, not at completion.
        started_at = timestamp - timedelta(milliseconds=latency_ms) if timestamp is not None else None
        if timestamp is not None:
            # An interval belongs to the hour the request finished in.
            intervals_by_hour[hour_bucket].append((started_at, timestamp))

        requests_by_user[client_id] += 1
        consumed_ms_by_user[client_id] += latency_ms
        requests_by_hour[hour_bucket] += 1
        consumed_ms_by_hour[hour_bucket] += latency_ms
        if model_name and _include_model_in_usage(model_name, excluded_model_names):
            requests_by_user_model[client_id][model_name] += 1
            requests_by_hour_model[hour_bucket][model_name] += 1
        requests_by_hour_user[hour_bucket][client_id] += 1

        if node_id:
            requests_by_node[node_id] += 1
            consumed_ms_by_node[node_id] += latency_ms
            if timestamp is not None:
                requests_by_node_hour[node_id][hour_bucket] += 1

        if model_name and _include_model_in_usage(model_name, excluded_model_names):
            requests_by_model[model_name] += 1
            consumed_ms_by_model[model_name] += latency_ms

        session_id = payload.get("sticky_id")
        session_key = session_id.strip() if isinstance(session_id, str) else ""
        if started_at is None:
            # No start time: the turn cannot be placed in time, so it stays outside the split.
            continue
        if not session_key:
            # Rule 1: least-connections may pick another node on every turn.
            cold = True
        else:
            previous_started_at = last_seen_by_session.get(session_key)
            cold = _is_cold(started_at, previous_started_at)
            # Records arrive sorted by start, so the window only moves forward.
            last_seen_by_session[session_key] = started_at
        cache_kind = "cold" if cold else "warm"
        cache_by_hour[hour_bucket][cache_kind] += 1
        cache_by_client[client_id][cache_kind] += 1
        cache_latency_values[cache_kind].append(latency_ms)
        cache_latency_values_by_hour[hour_bucket][cache_kind].append(latency_ms)

    requests_by_hour_payload: dict[str, dict[str, object]] = {
        hour: {
            "requests": count,
            "consumed_ms": consumed_ms_by_hour.get(hour, 0),
            "by_model": _ordered_counter_by_weight(requests_by_hour_model.get(hour, Counter()), requests_by_model),
            "by_user": _ordered_counter_by_weight(requests_by_hour_user.get(hour, Counter()), requests_by_user),
        }
        for hour, count in requests_by_hour.items()
    }

    latency_ms_by_hour: dict[str, dict[str, int]] = {
        hour: {**_latency_core(values), "max": max(values)}
        for hour, values in sorted(latency_values_by_hour.items())
    }
    peak_concurrency_by_hour: dict[str, int] = {
        hour: peak_concurrency(intervals) for hour, intervals in sorted(intervals_by_hour.items())
    }
    cache_total_counts: Counter[str] = Counter()
    for hour_counts in cache_by_hour.values():
        cache_total_counts.update(hour_counts)
    cache_by_client_payload = {
        client: CacheBucket.from_counts(counts)
        for client, counts in sorted(cache_by_client.items(), key=lambda item: (-sum(item[1].values()), item[0]))
    }

    node_metrics_path_str = ""
    node_metrics_present = False
    node_hot_loaded_models: dict[str, list[str]] = {}
    node_hot_loaded_count: dict[str, int] = {}
    node_load_active: dict[str, dict[str, list[int]]] = defaultdict(lambda: defaultdict(list))
    node_load_waiting: dict[str, dict[str, int]] = defaultdict(dict)
    node_model_memory: dict[str, dict[str, dict[str, int]]] = {}
    node_model_memory_sampled_at: dict[str, str] = {}
    latest_node_metric_timestamp: dict[str, datetime | None] = {}
    if node_metrics_path is not None:
        node_metrics_path_str = str(node_metrics_path)
        node_records, invalid_node_lines = _load_jsonl_lines(node_metrics_path)
        invalid_lines += invalid_node_lines
        for payload in node_records:
            timestamp = _parse_timestamp(payload.get("timestamp"))
            if not _in_period(timestamp, period):
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

            active_requests = _as_non_negative_int(payload.get("active_requests"))
            if active_requests is not None:
                node_load_active[_hour_bucket(timestamp)][node_id].append(active_requests)
            waiting_requests = _as_non_negative_int(payload.get("waiting_requests"))
            if waiting_requests is not None:
                waiting_per_node = node_load_waiting[_hour_bucket(timestamp)]
                waiting_per_node[node_id] = max(waiting_per_node.get(node_id, 0), waiting_requests)

            raw_memory = payload.get("loaded_models")
            if isinstance(raw_memory, list):
                memory_per_node: dict[str, dict[str, int]] = {}
                for item in raw_memory:
                    if not isinstance(item, dict):
                        continue
                    model_id = item.get("id")
                    actual_size = _as_non_negative_int(item.get("actual_size"))
                    if not isinstance(model_id, str) or not model_id.strip() or actual_size is None:
                        continue
                    if not _include_model_in_usage(model_id, excluded_model_names):
                        continue
                    memory_per_node[model_id.strip()] = {"actual_size": actual_size}
                    idle_seconds = _as_non_negative_int(item.get("idle_seconds"))
                    if idle_seconds is not None:
                        # Absent means the node never reported it; do not invent 0.
                        memory_per_node[model_id.strip()]["idle_seconds"] = idle_seconds
                # A failed probe reports no models; it must not wipe a real footprint.
                if payload.get("status_ok") is False:
                    continue
                # Ordering is already enforced by latest_node_metric_timestamp above.
                # A newer sample with nothing loaded must clear the stale footprint.
                if memory_per_node:
                    node_model_memory[node_id] = dict(sorted(memory_per_node.items()))
                    node_model_memory_sampled_at[node_id] = timestamp.isoformat() if timestamp is not None else ""
                else:
                    node_model_memory.pop(node_id, None)
                    node_model_memory_sampled_at.pop(node_id, None)

    node_load_by_hour: dict[str, dict[str, dict[str, float | None]]] = {
        hour: {
            node_id: {
                "samples": len(values),
                "active_max": max(values),
                "active_avg": round(sum(values) / len(values), 2),
                "waiting_max": node_load_waiting.get(hour, {}).get(node_id),
            }
            for node_id, values in sorted(nodes.items())
        }
        for hour, nodes in sorted(node_load_active.items())
    }

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
        consumed_ms_by_node=_ordered_counter(consumed_ms_by_node),
        requests_by_model=_ordered_counter(requests_by_model),
        consumed_ms_by_model=_ordered_counter(consumed_ms_by_model),
        requests_by_hour=requests_by_hour_payload,
        requests_by_node_hour=_ordered_nested_counter(requests_by_node_hour),
        latency_ms_by_hour=latency_ms_by_hour,
        cache_total=CacheBucket.from_counts(cache_total_counts),
        cache_by_hour={hour: CacheBucket.from_counts(counts) for hour, counts in sorted(cache_by_hour.items())},
        cache_by_client=cache_by_client_payload,
        cache_latency_total=CacheLatency.from_samples(cache_latency_values),
        cache_latency_by_hour={
            hour: CacheLatency.from_samples(samples) for hour, samples in sorted(cache_latency_values_by_hour.items())
        },
        peak_concurrency_by_hour=peak_concurrency_by_hour,
        node_metrics_present=node_metrics_present,
        node_hot_loaded_models=_ordered_list_map(node_hot_loaded_models),
        node_hot_loaded_count=dict(sorted(node_hot_loaded_count.items())),
        node_load_by_hour=node_load_by_hour,
        node_model_memory=dict(sorted(node_model_memory.items())),
        node_model_memory_sampled_at=dict(sorted(node_model_memory_sampled_at.items())),
        available_users=sorted(seen_clients),
        selected_users=sorted(selected_clients or []),
        known_users=sorted(set(known_client_ids or [])),
    )
