"""Helpers for mapping runtime model ids to configured short aliases."""

from __future__ import annotations

from thunder_forge.cluster.config import ClusterConfig, Node


def runtime_model_aliases(config: ClusterConfig, node: Node) -> dict[str, list[str]]:
    """Return runtime model id -> configured aliases for a node."""
    runtime_to_aliases: dict[str, list[str]] = {}
    for alias in node.models:
        model = config.models.get(alias)
        if model is None:
            continue
        runtime_id = model.runtime_model_id.strip()
        if not runtime_id:
            continue
        runtime_to_aliases.setdefault(runtime_id, []).append(alias)
    return runtime_to_aliases


def map_runtime_models_to_aliases(
    config: ClusterConfig,
    node: Node,
    runtime_model_ids: list[str],
    *,
    include_unmanaged: bool = True,
) -> list[str]:
    """Map runtime model ids to configured aliases while preserving order and uniqueness."""
    runtime_to_aliases = runtime_model_aliases(config, node)

    aliases: list[str] = []
    seen: set[str] = set()
    for model_id in runtime_model_ids:
        mapped = runtime_to_aliases.get(model_id)
        if mapped:
            for alias in mapped:
                if alias not in seen:
                    aliases.append(alias)
                    seen.add(alias)
            continue
        if include_unmanaged and model_id not in seen:
            aliases.append(model_id)
            seen.add(model_id)
    return aliases
