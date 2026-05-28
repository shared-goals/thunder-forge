"""Model download and sync to inference nodes."""

from __future__ import annotations

import os
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field

from thunder_forge.cluster.config import ClusterConfig
from thunder_forge.cluster.ssh import _is_local, _ssh_key_args, run_local, ssh_run

# Default timeout (seconds) for individual HuggingFace HTTP requests.
# Prevents hf download from hanging indefinitely on flaky connections.
DEFAULT_HF_HTTP_TIMEOUT = 300


def _rsync_ssh_cmd() -> str:
    """Build the SSH command string for rsync -e, including key if configured."""
    parts = ["ssh", *_ssh_key_args(), "-o", "StrictHostKeyChecking=no"]
    return " ".join(parts)


# HF cache dir on the machine running the CLI (gateway), respects HF_HOME env var
HF_CACHE = os.environ.get("HF_HOME", "~/.cache/huggingface") + "/hub"
# Default HF cache on inference nodes (no custom HF_HOME)
DEFAULT_HF_CACHE = "~/.cache/huggingface/hub"
# Full path to hf CLI — needed when running via SSH (no login shell, ~/.local/bin not in PATH)
HF_BIN = "~/.local/bin/hf"


def _hf_env_str() -> str:
    """Build env prefix for hf download commands (HF_HOME, HF_TOKEN, HF_HUB_DOWNLOAD_TIMEOUT)."""
    parts = [f"HF_HOME={os.environ.get('HF_HOME', '~/.cache/huggingface')}"]
    if os.environ.get("HF_TOKEN"):
        parts.append(f"HF_TOKEN={os.environ['HF_TOKEN']}")
    timeout = os.environ.get("HF_HUB_DOWNLOAD_TIMEOUT", str(DEFAULT_HF_HTTP_TIMEOUT))
    parts.append(f"HF_HUB_DOWNLOAD_TIMEOUT={timeout}")
    return " ".join(parts)


@dataclass
class ModelTask:
    model_name: str
    source_type: str
    repo: str = ""
    revision: str = "main"
    quantize: str = ""
    path: str = ""
    package: str = ""
    weight_repo: str = ""
    target_nodes: list[str] = field(default_factory=list)


def resolve_model_tasks(
    config: ClusterConfig,
    *,
    target_node: str | None = None,
) -> list[ModelTask]:
    task_map: dict[str, ModelTask] = {}
    for node_name, slots in config.assignments.items():
        if target_node and node_name != target_node:
            continue
        for slot in slots:
            model = config.models[slot.model]
            src = model.source
            if slot.model not in task_map:
                task_map[slot.model] = ModelTask(
                    model_name=slot.model,
                    source_type=src.type,
                    repo=src.repo,
                    revision=src.revision,
                    quantize=src.quantize,
                    path=src.path,
                    package=src.package,
                    weight_repo=src.weight_repo,
                )
            task_map[slot.model].target_nodes.append(node_name)
    return list(task_map.values())


def _check_hf_cached(
    user: str, ip: str, repo: str, *, hf_cache: str = DEFAULT_HF_CACHE, shell: str | None = None
) -> bool:
    """Return True only if the HF cache has a valid, complete-enough snapshot.

    Checks:
    1. refs/main exists and is non-empty (revision pointer)
    2. snapshots/<ref>/ exists (the commit snapshot directory is present)
    3. At least one blob file exists (some model weights were actually transferred)

    A partial rsync leaves the snapshots/ dir but may be missing refs/main
    or the commit-specific snapshot subdir, causing mlx_lm to fail with
    LocalEntryNotFoundError even when HF_HUB_OFFLINE=1.
    """
    hf_path = repo.replace("/", "--")
    base = f"{hf_cache}/models--{hf_path}"
    check_cmd = (
        f'ref=$(cat {base}/refs/main 2>/dev/null) && '
        f'[ -n "$ref" ] && '
        f'test -d {base}/snapshots/"$ref" && '
        f'[ "$(ls {base}/blobs/ 2>/dev/null | wc -l)" -gt 0 ] && '
        f'[ "$(ls {base}/blobs/*.incomplete 2>/dev/null | wc -l)" -eq 0 ]'
    )
    result = ssh_run(user, ip, check_cmd, shell=shell)
    return result.returncode == 0


def ensure_huggingface(
    task: ModelTask, config: ClusterConfig, *, dry_run: bool = False, download_timeout: int = 3600,
) -> list[str]:
    errors: list[str] = []
    gw_name = config.gateway_name
    gw = config.gateway
    if dry_run:
        print(f"    [download] {task.repo} on {gw_name}")
        for node_name in task.target_nodes:
            print(f"    [rsync] to {node_name}")
        return errors
    gw_hf_cache = os.environ.get("HF_HOME", "~/.cache/huggingface") + "/hub"
    if _check_hf_cached(gw.user, gw.ip, task.repo, hf_cache=gw_hf_cache, shell=gw.shell):
        print(f"  {task.repo} already cached on {gw_name}")
    else:
        print(f"  Downloading {task.repo} on {gw_name}...")
        hf_env = _hf_env_str()
        dl_cmd = f"{hf_env} {HF_BIN} download {task.repo} --revision {task.revision}"
        result = ssh_run(gw.user, gw.ip, dl_cmd, timeout=download_timeout, stream=True, shell=gw.shell)
        if result.returncode != 0:
            errors.append(f"Download failed for {task.repo}: {(result.stderr or '').strip()}")
            return errors
    hf_cache_path = task.repo.replace("/", "--")
    # Ensure refs/main exists so mlx_lm.server can locate the model in offline mode.
    # hf download with a commit hash creates the snapshot dir but not refs/main.
    if task.revision and task.revision != "main":
        refs_cmd = (
            f"mkdir -p {HF_CACHE}/models--{hf_cache_path}/refs && "
            f"echo -n {task.revision} > {HF_CACHE}/models--{hf_cache_path}/refs/main"
        )
        ssh_run(gw.user, gw.ip, refs_cmd, shell=gw.shell)
    def _sync_to_node(node_name: str) -> str | None:
        node = config.nodes[node_name]
        if _check_hf_cached(node.user, node.ip, task.repo, shell=node.shell):
            print(f"  {task.model_name} already cached on {node_name}")
            return None
        print(f"  Syncing {task.model_name} to {node_name}...")
        ssh_args = " ".join([*_ssh_key_args(), "-o", "StrictHostKeyChecking=no"])
        rsync_cmd = (
            f"rsync -az --progress -e 'ssh {ssh_args}' "
            f"{HF_CACHE}/models--{hf_cache_path}/ "
            f"{node.user}@{node.ip}:{DEFAULT_HF_CACHE}/models--{hf_cache_path}/"
        )
        rsync_result = ssh_run(gw.user, gw.ip, rsync_cmd, timeout=download_timeout, stream=True, shell=gw.shell)
        if rsync_result.returncode != 0:
            return f"Rsync to {node_name} failed: {(rsync_result.stderr or '').strip()}"
        return None

    with ThreadPoolExecutor(max_workers=max(1, len(task.target_nodes))) as pool:
        futures = {pool.submit(_sync_to_node, n): n for n in task.target_nodes}
        for future in as_completed(futures):
            err = future.result()
            if err:
                errors.append(err)
    return errors


def ensure_convert(
    task: ModelTask, config: ClusterConfig, *, dry_run: bool = False, download_timeout: int = 3600,
) -> list[str]:
    errors: list[str] = []
    gw_name = config.gateway_name
    gw = config.gateway
    if dry_run:
        print(f"    [download] {task.repo} on {gw_name}")
        for node_name in task.target_nodes:
            print(f"    [rsync] to {node_name}")
        return errors
    print(f"  Downloading source {task.repo} on {gw_name}...")
    hf_env = _hf_env_str()
    dl_result = ssh_run(
        gw.user, gw.ip, f"{hf_env} {HF_BIN} download {task.repo}",
        timeout=download_timeout, stream=True, shell=gw.shell,
    )
    if dl_result.returncode != 0:
        errors.append(f"Download failed for {task.repo}: {(dl_result.stderr or '').strip()}")
        return errors
    convert_node_name = task.target_nodes[0]
    convert_node = config.nodes[convert_node_name]
    output_dir = f"~/.cache/mlx-models/{task.model_name}/"
    check = ssh_run(convert_node.user, convert_node.ip, f"test -d {output_dir}", shell=convert_node.shell)
    if check.returncode == 0:
        print(f"  Already converted on {convert_node_name}")
    else:
        print(f"  Converting on {convert_node_name} (quantize={task.quantize})...")
        convert_cmd = (
            f"python -m mlx_lm.convert --hf-path {task.repo} "
            f"-q --q-bits {task.quantize} --upload-repo '' --mlx-path {output_dir}"
        )
        conv_result = ssh_run(convert_node.user, convert_node.ip, convert_cmd, timeout=1800, shell=convert_node.shell)
        if conv_result.returncode != 0:
            errors.append(f"Conversion failed on {convert_node_name}: {(conv_result.stderr or '').strip()}")
            return errors
    for node_name in task.target_nodes[1:]:
        node = config.nodes[node_name]
        check = ssh_run(node.user, node.ip, f"test -d {output_dir}", shell=node.shell)
        if check.returncode == 0:
            print(f"  Already on {node_name}")
            continue
        print(f"  Syncing converted model to {node_name}...")
        src = f"{convert_node.user}@{convert_node.ip}:{output_dir}"
        dest = f"{node.user}@{node.ip}:{output_dir}"
        rsync_result = run_local(
            ["rsync", "-az", "-e", _rsync_ssh_cmd(), src, dest],
            timeout=3600,
        )
        if rsync_result.returncode != 0:
            errors.append(f"Rsync to {node_name} failed: {(rsync_result.stderr or '').strip()}")
    return errors


def ensure_local(task: ModelTask, config: ClusterConfig, *, dry_run: bool = False) -> list[str]:
    errors: list[str] = []
    for node_name in task.target_nodes:
        node = config.nodes[node_name]
        if dry_run:
            print(f"    [verify] {task.path} on {node_name}")
            continue
        result = ssh_run(node.user, node.ip, f"test -d {task.path}", shell=node.shell)
        if result.returncode != 0:
            errors.append(f"{node_name}: path {task.path} does not exist")
    return errors


def ensure_pip(
    task: ModelTask, config: ClusterConfig, *, dry_run: bool = False, download_timeout: int = 3600,
) -> list[str]:
    errors: list[str] = []
    gw_name = config.gateway_name
    gw = config.gateway
    # Download weights on gateway node first, then rsync to nodes (same pattern as HF models)
    if task.weight_repo:
        if dry_run:
            print(f"    [download] {task.weight_repo} on {gw_name}")
        else:
            print(f"  Downloading weights {task.weight_repo} on {gw_name}...")
            hf_env = _hf_env_str()
            dl_cmd = f"{hf_env} {HF_BIN} download {task.weight_repo}"
            dl_result = ssh_run(gw.user, gw.ip, dl_cmd, timeout=download_timeout, stream=True, shell=gw.shell)
            if dl_result.returncode != 0:
                errors.append(f"Weight download failed for {task.weight_repo}: {(dl_result.stderr or '').strip()}")
                return errors
    for node_name in task.target_nodes:
        node = config.nodes[node_name]
        if dry_run:
            print(f"    [verify] {task.package} on {node_name}")
            if task.weight_repo:
                print(f"    [rsync] to {node_name}")
            continue
        check = ssh_run(node.user, node.ip, f"uv tool list 2>/dev/null | grep -q {task.package}", shell=node.shell)
        if check.returncode == 0:
            print(f"  {task.package} already installed on {node_name}")
        else:
            print(f"  Installing {task.package} on {node_name}...")
            result = ssh_run(node.user, node.ip, f"uv tool install {task.package}", timeout=120, shell=node.shell)
            if result.returncode != 0:
                errors.append(f"{node_name}: install of {task.package} failed: {(result.stderr or '').strip()}")
        if task.weight_repo:
            hf_cache_path = task.weight_repo.replace("/", "--")
            cache_dir = f"{DEFAULT_HF_CACHE}/models--{hf_cache_path}/snapshots"
            check_cached = ssh_run(node.user, node.ip, f"test -d {cache_dir}", shell=node.shell)
            if check_cached.returncode == 0:
                print(f"  Weights {task.weight_repo} already cached on {node_name}")
                continue
            if _is_local(gw.ip):
                src_path = f"{HF_CACHE}/models--{hf_cache_path}/"
            else:
                src_path = f"{gw.user}@{gw.ip}:{HF_CACHE}/models--{hf_cache_path}/"
            dest_path = f"{node.user}@{node.ip}:{DEFAULT_HF_CACHE}/models--{hf_cache_path}/"
            print(f"  Syncing weights {task.weight_repo} to {node_name}...")
            rsync_result = run_local(
                ["rsync", "-az", "--progress", "-e", _rsync_ssh_cmd(), src_path, dest_path],
                timeout=3600,
            )
            if rsync_result.returncode != 0:
                errors.append(f"{node_name}: weight rsync failed: {(rsync_result.stderr or '').strip()}")
    return errors


def _needs_gateway_download(tasks: list[ModelTask]) -> bool:
    """Return True if any task will download models on the gateway node."""
    for task in tasks:
        if task.source_type in ("huggingface", "convert"):
            return True
        if task.source_type == "pip" and task.weight_repo:
            return True
    return False


def run_ensure_models(
    config: ClusterConfig,
    *,
    dry_run: bool = False,
    target_node: str | None = None,
    download_timeout: int = 7200,
) -> bool:
    tasks = resolve_model_tasks(config, target_node=target_node)

    if _needs_gateway_download(tasks) and "HF_HOME" not in os.environ:
        print(
            "ERROR: HF_HOME is not set. Without it, models download to ~/.cache/huggingface "
            "on the root partition, which likely has insufficient space.\n"
            "Set HF_HOME to the external drive mount point, e.g.:\n"
            "  export HF_HOME=/mnt/external/.cache/huggingface"
        )
        return False

    all_ok = True
    for task in tasks:
        print(f"\n{task.model_name} ({task.source_type})")
        handler = {
            "huggingface": ensure_huggingface,
            "convert": ensure_convert,
            "local": ensure_local,
            "pip": ensure_pip,
        }.get(task.source_type)
        if handler is None:
            print(f"  Source type '{task.source_type}' not yet implemented (skipping)")
            continue
        kwargs: dict = {"dry_run": dry_run}
        if task.source_type in ("huggingface", "convert", "pip"):
            kwargs["download_timeout"] = download_timeout
        errors = handler(task, config, **kwargs)
        if errors:
            all_ok = False
            for err in errors:
                print(f"  {err}")
        elif not dry_run:
            print("  Done")
    return all_ok
