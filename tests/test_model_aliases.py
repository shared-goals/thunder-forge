"""Tests for runtime model id to alias mapping helpers."""

from __future__ import annotations

from thunder_forge.cluster.config import ClusterConfig, Model, ModelSource, Node, NodeRole
from thunder_forge.cluster.model_aliases import map_runtime_models_to_aliases


def test_map_runtime_models_to_aliases_preserves_order_and_uniqueness() -> None:
    config = ClusterConfig(
        models={
            "agent": Model(
                source=ModelSource(type="huggingface", repo="mlx-community/Qwen3.6-35B-A3B-4bit"),
                runtime_model_id="Qwen3.6-35B-A3B-4bit",
            ),
            "agent-better": Model(
                source=ModelSource(type="huggingface", repo="mlx-community/Qwen3.6-35B-A3B-mxfp8"),
                runtime_model_id="Qwen3.6-35B-A3B-mxfp8",
            ),
            "memory": Model(
                source=ModelSource(type="huggingface", repo="mlx-community/gpt-oss-20b-MXFP4-Q8"),
                runtime_model_id="gpt-oss-20b-MXFP4-Q8",
            ),
        },
        nodes={
            "msm1": Node(
                host="msm1-wifi.lan",
                ram_gb=128,
                roles=[NodeRole.INFERENCE],
                user="shag",
                models=["agent", "agent-better", "memory"],
            )
        },
    )
    node = config.nodes["msm1"]

    mapped = map_runtime_models_to_aliases(
        config,
        node,
        [
            "Qwen3.6-35B-A3B-mxfp8",
            "unknown-runtime-id",
            "Qwen3.6-35B-A3B-4bit",
            "Qwen3.6-35B-A3B-mxfp8",
        ],
    )

    assert mapped == ["agent-better", "unknown-runtime-id", "agent"]
