"""Tests for artifact readiness planning."""

from thunder_forge.cluster.artifacts import (
    ArtifactPresence,
    ArtifactReadinessAction,
    build_artifact_readiness_plan,
    hf_cache_dir_name,
)


def test_hf_cache_dir_name_uses_huggingface_hub_layout() -> None:
    assert hf_cache_dir_name("mlx-community/gpt-oss-20b-MXFP4-Q8") == "models--mlx-community--gpt-oss-20b-MXFP4-Q8"


def test_artifact_plan_downloads_to_studio_when_studio_cache_missing() -> None:
    plan = build_artifact_readiness_plan(
        repo_id="mlx-community/gpt-oss-20b-MXFP4-Q8",
        node="msm3",
        node_home_dir="/Users/shag",
        presence=ArtifactPresence(studio_hf_cache=False, node_hf_cache=False, node_omlx_model_dir=False),
    )

    assert plan.ready is False
    assert plan.actions == [ArtifactReadinessAction.DOWNLOAD_TO_STUDIO]
    assert plan.studio_hf_cache_path.endswith("/models--mlx-community--gpt-oss-20b-MXFP4-Q8")


def test_artifact_plan_syncs_to_node_when_studio_cache_exists() -> None:
    plan = build_artifact_readiness_plan(
        repo_id="mlx-community/gpt-oss-20b-MXFP4-Q8",
        node="msm3",
        node_home_dir="/Users/shag",
        presence=ArtifactPresence(studio_hf_cache=True, node_hf_cache=False, node_omlx_model_dir=False),
    )

    assert plan.ready is False
    assert plan.actions == [ArtifactReadinessAction.SYNC_TO_NODE]
    assert plan.node_hf_cache_path == "/Users/shag/.cache/huggingface/hub/models--mlx-community--gpt-oss-20b-MXFP4-Q8"


def test_artifact_plan_prepares_omlx_model_dir_when_node_cache_exists() -> None:
    plan = build_artifact_readiness_plan(
        repo_id="mlx-community/gpt-oss-20b-MXFP4-Q8",
        node="msm3",
        node_home_dir="/Users/shag",
        presence=ArtifactPresence(studio_hf_cache=True, node_hf_cache=True, node_omlx_model_dir=False),
    )

    assert plan.ready is False
    assert plan.actions == [ArtifactReadinessAction.PREPARE_OMLX_MODEL_DIR]
    assert plan.node_omlx_model_dir == "/Users/shag/.omlx/models/mlx-community/gpt-oss-20b-MXFP4-Q8"


def test_artifact_plan_ready_when_all_artifacts_exist() -> None:
    plan = build_artifact_readiness_plan(
        repo_id="mlx-community/gpt-oss-20b-MXFP4-Q8",
        node="msm3",
        node_home_dir="/Users/shag",
        presence=ArtifactPresence(studio_hf_cache=True, node_hf_cache=True, node_omlx_model_dir=True),
    )

    assert plan.ready is True
    assert plan.actions == []
