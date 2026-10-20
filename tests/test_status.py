"""Tests for shared cluster status normalization."""

from thunder_forge.cluster.status import normalize_model_statuses


def test_normalize_model_statuses_maps_aliases_and_idle_time() -> None:
    result = normalize_model_statuses(
        {
            "runtime-model": {
                "loaded": True,
                "is_loading": False,
                "last_access": 90.0,
                "actual_size": 1024,
            }
        },
        map_aliases=lambda model_ids: ["memory"] if model_ids == ["runtime-model"] else [],
        now=100.0,
    )

    assert result == [
        {
            "id": "memory",
            "aliases": ["memory"],
            "runtime_id": "runtime-model",
            "loaded": True,
            "is_loading": False,
            "last_access": 90.0,
            "idle_seconds": 10.0,
            "actual_size": 1024,
            "state": "loaded",
        }
    ]


def test_normalize_model_statuses_exposes_full_alias_group_for_shared_runtime_model() -> None:
    """Unified layout: several aliases share one runtime model; status must show them all."""
    result = normalize_model_statuses(
        {"Qwen3.8-Flash-Next-oQ4e-mtp": {"loaded": True, "is_loading": False}},
        map_aliases=lambda _model_ids: ["agent", "coder", "memory"],
        now=100.0,
    )

    assert result == [
        {
            "id": "agent",
            "aliases": ["agent", "coder", "memory"],
            "runtime_id": "Qwen3.8-Flash-Next-oQ4e-mtp",
            "loaded": True,
            "is_loading": False,
            "state": "loaded",
        }
    ]


def test_normalize_model_statuses_preserves_missing_optional_fields() -> None:
    result = normalize_model_statuses(
        {"runtime-model": {"loaded": False, "is_loading": True}},
        map_aliases=lambda _model_ids: ["memory"],
        now=100.0,
    )

    assert result == [
        {
            "id": "memory",
            "aliases": ["memory"],
            "runtime_id": "runtime-model",
            "loaded": False,
            "is_loading": True,
            "state": "loading",
        }
    ]
