"""Artifact readiness planning for oMLX model directories."""

from __future__ import annotations

import re
import shlex
import subprocess
from dataclasses import dataclass, field
from enum import StrEnum
from pathlib import Path

DEFAULT_OMLX_MODELS_DIR = "~/.omlx/models"


class ArtifactReadinessAction(StrEnum):
    DOWNLOAD_TO_STUDIO_OMLX = "download_to_studio_omlx"
    SYNC_TO_NODE_OMLX = "sync_to_node_omlx"


@dataclass(frozen=True)
class ArtifactIdentity:
    repo_id: str
    namespace: str
    repo_name: str
    model_dir_name: str
    runtime_model_id: str


@dataclass(frozen=True)
class ArtifactPresence:
    """Completeness state for TF-managed oMLX artifact directories."""

    studio_omlx_model_dir: bool
    node_omlx_model_dir: bool


@dataclass(frozen=True)
class ArtifactReadinessPlan:
    repo_id: str
    model_dir_name: str
    runtime_model_id: str
    node: str
    studio_omlx_model_dir: str
    node_omlx_model_dir: str
    ready: bool
    actions: list[ArtifactReadinessAction] = field(default_factory=list)


@dataclass(frozen=True)
class ArtifactSyncPlan:
    repo_id: str
    model_dir_name: str
    runtime_model_id: str
    source_path: str
    destination: str
    command: str
    mkdir_args: list[str]
    rsync_args: list[str]


@dataclass(frozen=True)
class ArtifactDownloadPlan:
    repo_id: str
    model_dir_name: str
    runtime_model_id: str
    destination: str
    command: str
    args: list[str]


def omlx_model_dir_name(repo_id: str) -> str:
    """Return the TF-managed oMLX model directory name for a HF repo id.

    oMLX discovers direct children of ``~/.omlx/models`` and uses the
    subdirectory name as the runtime model id. Thunder Forge preserves the
    Hugging Face namespace in that direct-child name to avoid collisions while
    keeping oMLX discovery simple.
    """
    return build_artifact_identity(repo_id).model_dir_name


def build_artifact_identity(repo_id: str) -> ArtifactIdentity:
    """Build the canonical TF artifact identity for a Hugging Face repo id."""
    _validate_repo_id(repo_id)
    namespace, repo_name = repo_id.split("/", maxsplit=1)
    model_dir_name = f"hf--{namespace}--{repo_name}"
    return ArtifactIdentity(
        repo_id=repo_id,
        namespace=namespace,
        repo_name=repo_name,
        model_dir_name=model_dir_name,
        runtime_model_id=model_dir_name,
    )


def build_artifact_readiness_plan(
    *,
    repo_id: str,
    node: str,
    node_home_dir: str,
    presence: ArtifactPresence,
    studio_omlx_models_dir: str = DEFAULT_OMLX_MODELS_DIR,
) -> ArtifactReadinessPlan:
    """Build a read-only plan for making a model ready for oMLX on a node.

    TF v2/oMLX product state uses only the oMLX default model directory shape:
    ``~/.omlx/models/<model-dir>``. Hugging Face cache layout is intentionally
    not part of the product flow.
    """
    identity = build_artifact_identity(repo_id)
    studio_omlx_model_dir = f"{studio_omlx_models_dir}/{identity.model_dir_name}"
    node_omlx_model_dir = f"{node_home_dir}/.omlx/models/{identity.model_dir_name}"

    actions: list[ArtifactReadinessAction] = []
    if not presence.studio_omlx_model_dir:
        actions.append(ArtifactReadinessAction.DOWNLOAD_TO_STUDIO_OMLX)
    elif not presence.node_omlx_model_dir:
        actions.append(ArtifactReadinessAction.SYNC_TO_NODE_OMLX)

    return ArtifactReadinessPlan(
        repo_id=repo_id,
        model_dir_name=identity.model_dir_name,
        runtime_model_id=identity.runtime_model_id,
        node=node,
        studio_omlx_model_dir=studio_omlx_model_dir,
        node_omlx_model_dir=node_omlx_model_dir,
        ready=not actions,
        actions=actions,
    )


def build_artifact_sync_plan(
    *,
    repo_id: str,
    node_user: str,
    node_host: str,
    node_home_dir: str,
    studio_omlx_models_dir: str = DEFAULT_OMLX_MODELS_DIR,
) -> ArtifactSyncPlan:
    """Build a studio-to-node oMLX model-directory rsync plan."""
    _validate_user_host_path(node_user=node_user, node_host=node_host, node_home_dir=node_home_dir)
    identity = build_artifact_identity(repo_id)
    source_path = f"{Path(f'{studio_omlx_models_dir}/{identity.model_dir_name}').expanduser()}/"
    remote_omlx_models_dir = f"{node_home_dir}/.omlx/models"
    destination = f"{node_user}@{node_host}:{remote_omlx_models_dir}/{identity.model_dir_name}/"
    mkdir_args = [
        "ssh",
        "-o",
        "BatchMode=yes",
        "-o",
        "ConnectTimeout=8",
        f"{node_user}@{node_host}",
        "mkdir",
        "-p",
        remote_omlx_models_dir,
    ]
    rsync_args = [
        "rsync",
        "-a",
        "--progress",
        "--partial-dir=.rsync-partial",
        "-e",
        "ssh -o BatchMode=yes -o ConnectTimeout=8",
        source_path,
        destination,
    ]
    command = " ".join(shlex.quote(arg) for arg in rsync_args)
    return ArtifactSyncPlan(
        repo_id=repo_id,
        model_dir_name=identity.model_dir_name,
        runtime_model_id=identity.runtime_model_id,
        source_path=source_path,
        destination=destination,
        command=command,
        mkdir_args=mkdir_args,
        rsync_args=rsync_args,
    )


def build_artifact_download_plan(
    *,
    repo_id: str,
    studio_omlx_models_dir: str = DEFAULT_OMLX_MODELS_DIR,
) -> ArtifactDownloadPlan:
    """Build a plan to download a model directly into studio's oMLX model directory."""
    identity = build_artifact_identity(repo_id)
    destination = f"{studio_omlx_models_dir}/{identity.model_dir_name}"
    destination_arg = str(Path(destination).expanduser())
    args = [
        "uvx",
        "--from",
        "huggingface_hub",
        "hf",
        "download",
        repo_id,
        "--local-dir",
        destination_arg,
    ]
    command = " ".join(shlex.quote(arg) for arg in args)
    return ArtifactDownloadPlan(
        repo_id=repo_id,
        model_dir_name=identity.model_dir_name,
        runtime_model_id=identity.runtime_model_id,
        destination=destination,
        command=command,
        args=args,
    )


def _env_without_socks_proxy() -> dict[str, str]:
    """Return process env safe for httpx tools when SOCKS extras are unavailable."""
    import os

    env = os.environ.copy()
    env.pop("ALL_PROXY", None)
    env.pop("all_proxy", None)
    return env


def run_artifact_download(plan: ArtifactDownloadPlan, *, timeout: int = 7200) -> subprocess.CompletedProcess[str]:
    """Execute a previously built direct-to-oMLX download plan."""
    return subprocess.run(
        plan.args,
        check=False,
        text=True,
        timeout=timeout,
        env=_env_without_socks_proxy(),
    )


def run_artifact_sync(plan: ArtifactSyncPlan, *, timeout: int = 7200) -> subprocess.CompletedProcess[str]:
    """Execute a previously built rsync plan."""
    mkdir_result = subprocess.run(
        plan.mkdir_args,
        check=False,
        text=True,
        timeout=60,
    )
    if mkdir_result.returncode != 0:
        return mkdir_result
    return subprocess.run(
        plan.rsync_args,
        check=False,
        text=True,
        timeout=timeout,
    )


def is_local_artifact_complete(model_dir: Path) -> bool:
    """Return True when a local oMLX model directory looks complete enough to serve."""
    if not model_dir.is_dir():
        return False
    if not (model_dir / "config.json").is_file():
        return False
    if any(model_dir.rglob("*.incomplete")):
        return False
    if (model_dir / ".rsync-partial").exists():
        return False
    return any(model_dir.rglob("*.safetensors")) or any(model_dir.rglob("*.bin"))


def _remote_artifact_complete(host: str, path: str) -> bool:
    quoted_path = shlex.quote(path)
    quoted_config = shlex.quote(f"{path}/config.json")
    command = " && ".join(
        [
            f"test -d {quoted_path}",
            f"test -f {quoted_config}",
            f"test -z \"$(find {quoted_path} -name '*.incomplete' -print -quit)\"",
            f"test ! -e {shlex.quote(f'{path}/.rsync-partial')}",
            (
                f"test -n \"$(find {quoted_path} "
                "\\( -name '*.safetensors' -o -name '*.bin' \\) -type f -print -quit)\""
            ),
        ]
    )
    result = subprocess.run(
        ["ssh", "-o", "BatchMode=yes", "-o", "ConnectTimeout=8", host, command],
        check=False,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    return result.returncode == 0


def _validate_repo_id(repo_id: str) -> None:
    if not re.fullmatch(r"[A-Za-z0-9._-]+/[A-Za-z0-9._-]+", repo_id):
        msg = f"Invalid repo_id for Hugging Face model: {repo_id!r}"
        raise ValueError(msg)


def _validate_user_host_path(*, node_user: str, node_host: str, node_home_dir: str) -> None:
    if not re.fullmatch(r"[A-Za-z0-9._-]+", node_user):
        msg = f"Invalid node_user: {node_user!r}"
        raise ValueError(msg)
    if not re.fullmatch(r"[A-Za-z0-9._-]+", node_host):
        msg = f"Invalid node_host: {node_host!r}"
        raise ValueError(msg)
    if not node_home_dir.startswith("/Users/"):
        msg = f"Invalid node_home_dir: {node_home_dir!r}"
        raise ValueError(msg)
    if not re.fullmatch(r"/[A-Za-z0-9._/-]+", node_home_dir):
        msg = f"Invalid node_home_dir: {node_home_dir!r}"
        raise ValueError(msg)


def probe_artifact_presence(
    *,
    repo_id: str,
    node_host: str,
    node_home_dir: str,
) -> ArtifactPresence:
    """Check oMLX model-directory completeness on studio and a node."""
    identity = build_artifact_identity(repo_id)
    studio_omlx_model_dir = Path(DEFAULT_OMLX_MODELS_DIR).expanduser() / identity.model_dir_name
    node_omlx_model_dir = f"{node_home_dir}/.omlx/models/{identity.model_dir_name}"

    return ArtifactPresence(
        studio_omlx_model_dir=is_local_artifact_complete(studio_omlx_model_dir),
        node_omlx_model_dir=_remote_artifact_complete(node_host, node_omlx_model_dir),
    )
