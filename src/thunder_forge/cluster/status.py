"""Shared status payload normalization helpers."""

from __future__ import annotations

import time
from collections.abc import Callable


def normalize_model_statuses(
    model_statuses: dict[str, dict[str, object]],
    *,
    map_aliases: Callable[[list[str]], list[str]],
    now: float | None = None,
) -> list[dict[str, object]]:
    """Convert oMLX model status records into the public status shape."""
    timestamp = time.time() if now is None else now
    normalized: list[dict[str, object]] = []
    for runtime_model_id, raw_status in sorted(model_statuses.items()):
        if not isinstance(raw_status, dict):
            continue
        aliases = map_aliases([runtime_model_id])
        if not aliases:
            continue
        loaded = raw_status.get("loaded") is True
        is_loading = raw_status.get("is_loading") is True
        status: dict[str, object] = {
            "id": aliases[0],
            "runtime_id": runtime_model_id,
            "loaded": loaded,
            "is_loading": is_loading,
        }
        last_access = raw_status.get("last_access")
        if isinstance(last_access, (int, float)) and not isinstance(last_access, bool):
            status["last_access"] = last_access
            status["idle_seconds"] = max(0.0, timestamp - float(last_access))
        actual_size = raw_status.get("actual_size")
        if isinstance(actual_size, (int, float)) and not isinstance(actual_size, bool):
            status["actual_size"] = actual_size
        status["state"] = "loading" if is_loading else "loaded" if loaded else "cold"
        normalized.append(status)
    return normalized
