"""Artifact readiness planning for model cache and oMLX runtime dirs."""

from __future__ import annotations

import subprocess
from dataclasses import dataclass, field
from enum import StrEnum
from pathlib import Path


class ArtifactReadinessAction(StrEnum):
    DOWNLOAD_TO_STUDIO = "download_to_studio"
    SYNC_TO_NODE = "sync_to_node"
    PREPARE_OMLX_MODEL_DIR = "prepare_omlx_model_dir"


@dataclass(frozen=True)
class ArtifactPresence:
    studio_hf_cache: bool
    node_hf_cache: bool
    node_omlx_model_dir: bool


@dataclass(frozen=True)
class ArtifactReadinessPlan:
    repo_id: str
    node: str
    studio_hf_cache_path: str
    node_hf_cache_path: str
    node_omlx_model_dir: str
    ready: bool
    actions: list[ArtifactReadinessAction] = field(default_factory=list)


def hf_cache_dir_name(repo_id: str) -> str:
    """Return the Hugging Face Hub cache directory name for a model repo id."""
    return f"models--{repo_id.replace('/', '--')}"


def build_artifact_readiness_plan(
    *,
    repo_id: str,
    node: str,
    node_home_dir: str,
    presence: ArtifactPresence,
    studio_hf_home: str = "~/.cache/huggingface",
) -> ArtifactReadinessPlan:
    """Build a read-only plan for making a model artifact ready for oMLX on a node."""
    cache_dir = hf_cache_dir_name(repo_id)
    studio_hf_cache_path = f"{studio_hf_home}/hub/{cache_dir}"
    node_hf_cache_path = f"{node_home_dir}/.cache/huggingface/hub/{cache_dir}"
    node_omlx_model_dir = f"{node_home_dir}/.omlx/models/{repo_id}"

    actions: list[ArtifactReadinessAction] = []
    if not presence.studio_hf_cache:
        actions.append(ArtifactReadinessAction.DOWNLOAD_TO_STUDIO)
    elif not presence.node_hf_cache:
        actions.append(ArtifactReadinessAction.SYNC_TO_NODE)
    elif not presence.node_omlx_model_dir:
        actions.append(ArtifactReadinessAction.PREPARE_OMLX_MODEL_DIR)

    return ArtifactReadinessPlan(
        repo_id=repo_id,
        node=node,
        studio_hf_cache_path=studio_hf_cache_path,
        node_hf_cache_path=node_hf_cache_path,
        node_omlx_model_dir=node_omlx_model_dir,
        ready=not actions,
        actions=actions,
    )


def _remote_path_exists(host: str, path: str) -> bool:
    result = subprocess.run(
        ["ssh", host, "test", "-e", path],
        check=False,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    return result.returncode == 0


def probe_artifact_presence(
    *,
    repo_id: str,
    node_host: str,
    node_home_dir: str,
    studio_hf_home: str = "~/.cache/huggingface",
) -> ArtifactPresence:
    """Check artifact presence on studio and a node using existing SSH access."""
    cache_dir = hf_cache_dir_name(repo_id)
    studio_hf_cache_path = Path(studio_hf_home).expanduser() / "hub" / cache_dir
    node_hf_cache_path = f"{node_home_dir}/.cache/huggingface/hub/{cache_dir}"
    node_omlx_model_dir = f"{node_home_dir}/.omlx/models/{repo_id}"

    return ArtifactPresence(
        studio_hf_cache=studio_hf_cache_path.exists(),
        node_hf_cache=_remote_path_exists(node_host, node_hf_cache_path),
        node_omlx_model_dir=_remote_path_exists(node_host, node_omlx_model_dir),
    )
