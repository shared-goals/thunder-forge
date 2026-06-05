"""Artifact readiness planning for oMLX model directories."""

from __future__ import annotations

import re
import secrets
import shlex
import subprocess
import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from enum import StrEnum
from pathlib import Path

import httpx

from thunder_forge.cluster.ssh import ssh_run

DEFAULT_OMLX_MODELS_DIR = "~/.omlx/models"
CACHE_OMLX_MODELS_DIR_ENV = "TF_CACHE_OMLX_MODELS_DIR"


def cache_omlx_models_dir_from_env(env: Mapping[str, str] | None = None) -> str:
    """Return the cache-side oMLX models directory used by artifact commands."""
    import os

    source = os.environ if env is None else env
    return source.get(CACHE_OMLX_MODELS_DIR_ENV) or DEFAULT_OMLX_MODELS_DIR


class ArtifactReadinessAction(StrEnum):
    DOWNLOAD_TO_CACHE_OMLX = "download_to_cache_omlx"
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

    cache_omlx_model_dir: bool
    node_omlx_model_dir: bool


@dataclass(frozen=True)
class ArtifactReadinessPlan:
    repo_id: str
    model_dir_name: str
    runtime_model_id: str
    node: str
    cache_omlx_model_dir: str
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
    base_url: str
    hf_token_env: str = "HF_TOKEN"


def omlx_model_dir_name(repo_id: str) -> str:
    """Return the oMLX-native model directory path for a Hugging Face repo id."""
    return build_artifact_identity(repo_id).model_dir_name


def build_artifact_identity(repo_id: str) -> ArtifactIdentity:
    """Build the oMLX-native artifact identity for a Hugging Face repo id."""
    _validate_repo_id(repo_id)
    namespace, repo_name = repo_id.split("/", maxsplit=1)
    return ArtifactIdentity(
        repo_id=repo_id,
        namespace=namespace,
        repo_name=repo_name,
        model_dir_name=repo_id,
        runtime_model_id=repo_name,
    )


def build_artifact_readiness_plan(
    *,
    repo_id: str,
    node: str,
    node_home_dir: str,
    presence: ArtifactPresence,
    cache_omlx_models_dir: str = DEFAULT_OMLX_MODELS_DIR,
) -> ArtifactReadinessPlan:
    """Build a read-only plan for making a model ready for oMLX on a node.

    TF uses oMLX's native download layout under ``~/.omlx/models/<owner>/<repo>``.
    oMLX discovers those nested directories and exposes the repo directory name
    as the runtime model id.
    """
    identity = build_artifact_identity(repo_id)
    cache_omlx_model_dir = f"{cache_omlx_models_dir}/{identity.model_dir_name}"
    node_omlx_model_dir = f"{node_home_dir}/.omlx/models/{identity.model_dir_name}"

    actions: list[ArtifactReadinessAction] = []
    if not presence.cache_omlx_model_dir:
        actions.append(ArtifactReadinessAction.DOWNLOAD_TO_CACHE_OMLX)
    elif not presence.node_omlx_model_dir:
        actions.append(ArtifactReadinessAction.SYNC_TO_NODE_OMLX)

    return ArtifactReadinessPlan(
        repo_id=repo_id,
        model_dir_name=identity.model_dir_name,
        runtime_model_id=identity.runtime_model_id,
        node=node,
        cache_omlx_model_dir=cache_omlx_model_dir,
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
    cache_omlx_models_dir: str = DEFAULT_OMLX_MODELS_DIR,
    ssh_host_key_alias: str | None = None,
) -> ArtifactSyncPlan:
    """Build a cache-to-node oMLX model-directory rsync plan."""
    _validate_user_host_path(node_user=node_user, node_host=node_host, node_home_dir=node_home_dir)
    if ssh_host_key_alias is not None:
        _validate_host(ssh_host_key_alias, name="ssh_host_key_alias")
    identity = build_artifact_identity(repo_id)
    source_path = f"{_expanded_cache_omlx_models_dir(cache_omlx_models_dir) / identity.model_dir_name}/"
    remote_omlx_models_dir = f"{node_home_dir}/.omlx/models"
    remote_model_parent_dir = f"{remote_omlx_models_dir}/{identity.namespace}"
    destination = f"{node_user}@{node_host}:{remote_omlx_models_dir}/{identity.model_dir_name}/"
    ssh_options = [
        "-o",
        "BatchMode=yes",
        "-o",
        "ConnectTimeout=8",
    ]
    if ssh_host_key_alias:
        ssh_options.extend(["-o", f"HostKeyAlias={ssh_host_key_alias}"])
    mkdir_args = [
        "ssh",
        *ssh_options,
        f"{node_user}@{node_host}",
        "mkdir",
        "-p",
        remote_model_parent_dir,
    ]
    rsync_args = [
        "rsync",
        "-a",
        "--progress",
        "--partial-dir=.rsync-partial",
        "--exclude",
        ".cache/",
        "-e",
        "ssh " + " ".join(shlex.quote(option) for option in ssh_options),
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
    cache_omlx_models_dir: str = DEFAULT_OMLX_MODELS_DIR,
) -> ArtifactDownloadPlan:
    """Build a plan to download a model through oMLX into cache role's model directory."""
    identity = build_artifact_identity(repo_id)
    destination = _cache_omlx_model_dir(cache_omlx_models_dir, identity.model_dir_name)
    models_dir_arg = str(_expanded_cache_omlx_models_dir(cache_omlx_models_dir))
    base_url = "http://127.0.0.1:8020"
    args = [
        "omlx",
        "serve",
        "--host",
        "127.0.0.1",
        "--port",
        "8020",
        "--model-dir",
        models_dir_arg,
        "--max-model-memory",
        "disabled",
    ]
    command = (
        " ".join(shlex.quote(arg) for arg in args)
        + f"; POST {base_url}/admin/api/hf/download repo_id={shlex.quote(repo_id)} hf_token=$HF_TOKEN"
    )
    return ArtifactDownloadPlan(
        repo_id=repo_id,
        model_dir_name=identity.model_dir_name,
        runtime_model_id=identity.runtime_model_id,
        destination=destination,
        command=command,
        args=args,
        base_url=base_url,
    )


def _cache_omlx_model_dir(cache_omlx_models_dir: str, model_dir_name: str) -> str:
    return f"{cache_omlx_models_dir.rstrip('/')}/{model_dir_name}"


def _expanded_cache_omlx_models_dir(cache_omlx_models_dir: str) -> Path:
    return Path(cache_omlx_models_dir).expanduser()


def _env_without_socks_proxy() -> dict[str, str]:
    """Return process env safe for httpx tools when SOCKS extras are unavailable."""
    import os

    env = os.environ.copy()
    _load_dotenv_into_env(env, start_dir=Path.cwd())
    env.pop("ALL_PROXY", None)
    env.pop("all_proxy", None)
    return env


def _load_dotenv_into_env(env: dict[str, str], *, start_dir: Path) -> None:
    env_file = _find_dotenv(start_dir)
    if env_file is None:
        return

    for raw_line in env_file.read_text().splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("export "):
            line = line.removeprefix("export ").lstrip()
        if "=" not in line:
            continue
        key, value = line.split("=", maxsplit=1)
        key = key.strip()
        if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", key):
            continue
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in {"'", '"'}:
            value = value[1:-1]
        if not env.get(key):
            env[key] = value


def _find_dotenv(start_dir: Path) -> Path | None:
    current = start_dir if start_dir.is_dir() else start_dir.parent
    for directory in (current, *current.parents):
        candidate = directory / ".env"
        if candidate.is_file():
            return candidate
        if (directory / ".git").exists():
            break
    return None


def run_artifact_download(
    plan: ArtifactDownloadPlan,
    *,
    timeout: int = 7200,
    progress_callback: Callable[[dict], None] | None = None,
) -> subprocess.CompletedProcess[str]:
    """Execute a previously built download plan through oMLX's downloader API."""
    env = _env_without_socks_proxy()
    server_process: subprocess.Popen[str] | None = None
    server_started = False
    stdout = ""
    stderr = ""

    try:
        downloader_api_key = env.get("TF_OMLX_DOWNLOADER_API_KEY") or env.get("OMLX_API_KEY")
        if not _omlx_server_ready(plan.base_url):
            downloader_api_key = secrets.token_urlsafe(24)
            server_process = subprocess.Popen(
                [*plan.args, "--api-key", downloader_api_key],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                text=True,
                env=env,
            )
            server_started = True
            _wait_for_omlx_server(plan.base_url, process=server_process, timeout_seconds=60)

        token = env.get(plan.hf_token_env, "")
        client = _omlx_admin_client(plan.base_url, api_key=downloader_api_key)
        try:
            task = _start_omlx_hf_download(client, plan.repo_id, hf_token=token)
            task = _poll_omlx_hf_task(
                client,
                task_id=task["task_id"],
                repo_id=plan.repo_id,
                timeout_seconds=timeout,
                progress_callback=progress_callback,
            )
        finally:
            client.close()
        stdout = f"download_task: {task['task_id']}\nstatus: {task['status']}\n"
        return subprocess.CompletedProcess(args=plan.args, returncode=0, stdout=stdout, stderr=stderr)
    except Exception as exc:  # noqa: BLE001
        stderr = str(exc)
        return subprocess.CompletedProcess(args=plan.args, returncode=1, stdout=stdout, stderr=stderr)
    finally:
        if server_started and server_process is not None:
            server_process.terminate()
            try:
                server_process.wait(timeout=10)
            except subprocess.TimeoutExpired:
                server_process.kill()
                server_process.wait(timeout=10)


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


def _omlx_server_ready(base_url: str) -> bool:
    try:
        with httpx.Client(base_url=base_url, timeout=2.0, trust_env=False) as client:
            response = client.get("/health")
            return response.is_success
    except httpx.HTTPError:
        return False


def _wait_for_omlx_server(
    base_url: str,
    *,
    process: subprocess.Popen[str],
    timeout_seconds: int,
) -> None:
    deadline = time.monotonic() + timeout_seconds
    while time.monotonic() < deadline:
        if process.poll() is not None:
            raise RuntimeError(f"oMLX downloader server exited with code {process.returncode}")
        if _omlx_server_ready(base_url):
            return
        time.sleep(1)
    raise TimeoutError(f"oMLX downloader server did not become ready: {base_url}")


def _omlx_admin_client(base_url: str, *, api_key: str | None) -> httpx.Client:
    client = httpx.Client(base_url=base_url, timeout=30.0, trust_env=False)
    if api_key:
        response = client.post("/admin/api/login", json={"api_key": api_key, "remember": False})
        response.raise_for_status()
    return client


def _start_omlx_hf_download(client: httpx.Client, repo_id: str, *, hf_token: str) -> dict:
    response = client.post(
        "/admin/api/hf/download",
        json={"repo_id": repo_id, "hf_token": hf_token},
    )
    if response.status_code == 401:
        raise RuntimeError(
            "oMLX downloader admin API requires authentication; set TF_OMLX_DOWNLOADER_API_KEY "
            "or stop the existing local server so TF can start a transient downloader"
        )
    if response.status_code == 400 and "already in progress" in response.text:
        return _find_omlx_hf_task(client, repo_id)
    response.raise_for_status()
    payload = response.json()
    task = payload.get("task")
    if not isinstance(task, dict):
        raise RuntimeError(f"oMLX downloader returned no task for {repo_id}")
    return task


def _find_omlx_hf_task(client: httpx.Client, repo_id: str) -> dict:
    response = client.get("/admin/api/hf/tasks")
    response.raise_for_status()
    tasks = response.json().get("tasks", [])
    for task in tasks:
        if task.get("repo_id") == repo_id and task.get("status") in {"pending", "downloading"}:
            return task
    raise RuntimeError(f"No active oMLX download task found for {repo_id}")


def _poll_omlx_hf_task(
    client: httpx.Client,
    *,
    task_id: str,
    repo_id: str,
    timeout_seconds: int,
    progress_callback: Callable[[dict], None] | None = None,
) -> dict:
    deadline = time.monotonic() + timeout_seconds
    last_task: dict | None = None
    while time.monotonic() < deadline:
        response = client.get("/admin/api/hf/tasks")
        response.raise_for_status()
        tasks = response.json().get("tasks", [])
        for task in tasks:
            if task.get("task_id") == task_id:
                last_task = task
                if progress_callback:
                    progress_callback(task)
                status = task.get("status")
                if status == "completed":
                    return task
                if status in {"failed", "cancelled"}:
                    error = task.get("error") or f"download {status}"
                    raise RuntimeError(f"oMLX download failed for {repo_id}: {error}")
                break
        time.sleep(2)
    detail = f"last status: {last_task}" if last_task else "task not found"
    raise TimeoutError(f"Timed out waiting for oMLX download {repo_id}; {detail}")


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


def _remote_artifact_complete(*, user: str, host: str, path: str) -> bool:
    quoted_path = shlex.quote(path)
    quoted_config = shlex.quote(f"{path}/config.json")
    command = " && ".join(
        [
            f"test -d {quoted_path}",
            f"test -f {quoted_config}",
            f"test -z \"$(find {quoted_path} -name '*.incomplete' -print -quit)\"",
            f"test ! -e {shlex.quote(f'{path}/.rsync-partial')}",
            (f"test -n \"$(find {quoted_path} \\( -name '*.safetensors' -o -name '*.bin' \\) -type f -print -quit)\""),
        ]
    )
    result = ssh_run(user, host, command, timeout=30)
    return result.returncode == 0


def _validate_repo_id(repo_id: str) -> None:
    if not re.fullmatch(r"[A-Za-z0-9._-]+/[A-Za-z0-9._-]+", repo_id):
        msg = f"Invalid repo_id for Hugging Face model: {repo_id!r}"
        raise ValueError(msg)


def _validate_user_host_path(*, node_user: str, node_host: str, node_home_dir: str) -> None:
    if not re.fullmatch(r"[A-Za-z0-9._-]+", node_user):
        msg = f"Invalid node_user: {node_user!r}"
        raise ValueError(msg)
    _validate_host(node_host, name="node_host")
    if not node_home_dir.startswith("/Users/"):
        msg = f"Invalid node_home_dir: {node_home_dir!r}"
        raise ValueError(msg)
    if not re.fullmatch(r"/[A-Za-z0-9._/-]+", node_home_dir):
        msg = f"Invalid node_home_dir: {node_home_dir!r}"
        raise ValueError(msg)


def _validate_host(host: str, *, name: str) -> None:
    if not re.fullmatch(r"[A-Za-z0-9._-]+", host):
        msg = f"Invalid {name}: {host!r}"
        raise ValueError(msg)


def probe_artifact_presence(
    *,
    repo_id: str,
    node_user: str,
    node_host: str,
    node_home_dir: str,
    cache_omlx_models_dir: str | None = None,
) -> ArtifactPresence:
    """Check oMLX model-directory completeness on the cache host and a node."""
    identity = build_artifact_identity(repo_id)
    cache_models_dir = cache_omlx_models_dir or cache_omlx_models_dir_from_env()
    cache_omlx_model_dir = Path(cache_models_dir).expanduser() / identity.model_dir_name
    node_omlx_model_dir = f"{node_home_dir}/.omlx/models/{identity.model_dir_name}"

    return ArtifactPresence(
        cache_omlx_model_dir=is_local_artifact_complete(cache_omlx_model_dir),
        node_omlx_model_dir=_remote_artifact_complete(user=node_user, host=node_host, path=node_omlx_model_dir),
    )
