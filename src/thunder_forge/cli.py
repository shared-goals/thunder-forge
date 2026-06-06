"""Thunder Forge CLI — cluster management commands."""

import base64
import json
import os
import platform
import re
import shlex
import socket
import sys
import threading
import time
import urllib.request
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import cast

import httpx
import typer

from thunder_forge.cluster.artifacts import (
    ArtifactDownloadPlan,
    ArtifactPresence,
    ArtifactReadinessAction,
    ArtifactSyncPlan,
    build_artifact_download_plan,
    build_artifact_identity,
    build_artifact_readiness_plan,
    build_artifact_sync_plan,
    cache_omlx_models_dir_from_env,
    probe_artifact_presence,
    run_artifact_download,
    run_artifact_sync,
)
from thunder_forge.cluster.bootstrap import (
    LATEST_OLLA_RELEASE_API,
    ensure_cache_hub_dir,
    ensure_olla_binary,
    write_generated_olla_config,
)
from thunder_forge.cluster.config import ClusterConfig, Node, NodeRole, NodeRuntime, ServiceConfig
from thunder_forge.cluster.edge import (
    EDGE_USER_PREFIX,
    EdgeModelCatalogEntry,
    EdgeProxyConfig,
    build_edge_clients_from_env,
    edge_api_key_from_env,
    ensure_edge_api_keys,
    run_edge_service_restart,
    serve_edge_proxy,
    smoke_edge_contract,
    summarize_edge_usage,
)
from thunder_forge.cluster.fabric import TransportPlan, build_transport_plan
from thunder_forge.cluster.gateway import GatewayDaemonSetupResult, run_gateway_daemon_setup
from thunder_forge.cluster.log_retention import trim_local_logs
from thunder_forge.cluster.model_aliases import map_runtime_models_to_aliases
from thunder_forge.cluster.olla import dev_smoke_olla, run_olla_service_restart, smoke_olla_router
from thunder_forge.cluster.omlx import (
    check_omlx_health,
    ensure_omlx_tooling,
    run_omlx_daemon_restart,
    run_omlx_daemon_setup,
    run_omlx_install,
    run_omlx_process_restart,
    run_omlx_runtime_restart,
    run_omlx_runtime_start,
    smoke_omlx_chat,
)
from thunder_forge.cluster.ports import (
    DEFAULT_EDGE_PORT,
    DEFAULT_OLLA_PORT,
    local_base_url,
    resolve_port,
)
from thunder_forge.cluster.remote_cache import (
    cache_hub_setup_command as _cache_hub_setup_command,
)
from thunder_forge.cluster.remote_cache import (
    remote_artifact_download_command as _remote_artifact_download_command,
)
from thunder_forge.cluster.remote_cache import (
    remote_cache_sync_command as _remote_cache_sync_command,
)
from thunder_forge.cluster.remote_cache import (
    remote_transport_plan_probe_command as _remote_transport_plan_probe_command,
)
from thunder_forge.cluster.ssh import ssh_run
from thunder_forge.cluster.usage import extract_hot_loaded_models, summarize_daily_usage

app = typer.Typer(
    name="thunder-forge",
    help="CLI for managing a local MLX inference cluster.",
    no_args_is_help=True,
)
runtime_app = typer.Typer(help="Manage node-level runtimes such as oMLX.", no_args_is_help=True)
artifact_app = typer.Typer(help="Inspect model artifact readiness for oMLX nodes.", no_args_is_help=True)
edge_app = typer.Typer(help="Smoke-test and operate the minimal TF edge.", no_args_is_help=True)
olla_app = typer.Typer(help="Smoke-test the generated Olla router layer.", no_args_is_help=True)
config_app = typer.Typer(help="Inspect and validate Thunder Forge configuration.", no_args_is_help=True)
service_app = typer.Typer(help="Install and restart managed Thunder Forge services.", no_args_is_help=True)
cluster_app = typer.Typer(help="Prepare the gateway, cache hub, and inference nodes.", no_args_is_help=True)
usage_app = typer.Typer(help="Summarize daily usage from structured JSONL logs.", no_args_is_help=True)
app.add_typer(runtime_app, name="runtime")
app.add_typer(artifact_app, name="artifact")
app.add_typer(edge_app, name="edge")
app.add_typer(olla_app, name="olla")
app.add_typer(config_app, name="config")
app.add_typer(service_app, name="service")
app.add_typer(cluster_app, name="cluster")
app.add_typer(usage_app, name="usage")

DEFAULT_OPENCODE_API_KEY_ENV = "TF_USER_OPENCODE"
DEFAULT_HERMES_API_KEY_ENV = "TF_USER_HERMES"
REMOTE_CACHE_EXEC_ENV = "TF_CACHE_REMOTE_EXEC"


@dataclass(frozen=True)
class ArtifactSyncExecutionPlan:
    source_path: str
    destination: str
    command: str
    runtime_model_id: str
    model_dir_name: str
    readiness_actions: list[ArtifactReadinessAction]
    sync_plan: ArtifactSyncPlan | None = None


def _load_config() -> tuple[ClusterConfig, Path]:
    """Load the TF cluster config. Returns (ClusterConfig, repo_root Path)."""
    import thunder_forge.cluster.config as _cfg

    repo_root = _cfg.find_repo_root()
    cluster_config_path = _cfg.default_cluster_config_path(repo_root)
    if not cluster_config_path.exists():
        typer.echo(f"Error: {cluster_config_path} not found", err=True)
        raise typer.Exit(1)
    return _cfg.load_cluster_config(cluster_config_path), repo_root


def _load_config_if_present() -> tuple[ClusterConfig, Path] | None:
    """Load cluster config when it exists without making ad-hoc smoke checks require it."""
    import thunder_forge.cluster.config as _cfg

    repo_root = _cfg.find_repo_root()
    cluster_config_path = _cfg.default_cluster_config_path(repo_root)
    if not cluster_config_path.exists():
        return None
    return _cfg.load_cluster_config(cluster_config_path), repo_root


def _resolve_olla_port_from_optional_config(port: int | None) -> int:
    if port is not None:
        return resolve_port(port, default=DEFAULT_OLLA_PORT)
    loaded = _load_config_if_present()
    default_port = loaded[0].services.olla_port if loaded is not None else DEFAULT_OLLA_PORT
    return resolve_port(None, default=default_port)


def _repo_root() -> Path:
    from thunder_forge.cluster.config import find_repo_root

    return find_repo_root()


def _load_repo_dotenv() -> tuple[Path, Path]:
    """Load repo-local .env without overriding process env. Returns (repo_root, env_file)."""
    from dotenv import load_dotenv

    repo_root = _repo_root()
    env_file = repo_root / ".env"
    if env_file.exists():
        load_dotenv(env_file, override=False)
    return repo_root, env_file


def _repo_relative_path(repo_root: Path, path: Path) -> Path:
    expanded = path.expanduser()
    return expanded if expanded.is_absolute() else repo_root / expanded


def _edge_access_log_path(repo_root: Path, config: ClusterConfig, access_log: Path | None) -> Path:
    configured = str(access_log) if access_log is not None else config.services.edge_access_log
    return _repo_relative_path(repo_root, Path(configured))


def _usage_metrics_log_path(repo_root: Path, metrics_log: Path | None) -> Path:
    if metrics_log is None:
        return repo_root / "logs" / "tf-node-metrics.jsonl"
    return _repo_relative_path(repo_root, metrics_log)


def _trim_logs_with_policy(
    *,
    repo_root: Path,
    config: ClusterConfig,
    node_metrics_path: Path,
    retention_days: int | None,
) -> tuple[int, int, int]:
    days = retention_days if retention_days is not None else config.services.log_retention_days
    if days <= 0:
        typer.echo("Error: retention days must be positive", err=True)
        raise typer.Exit(1)
    summary = trim_local_logs(
        repo_root=repo_root,
        config=config,
        node_metrics_log_path=node_metrics_path,
        retention_days=days,
    )
    jsonl_removed = sum(result.removed_lines for result in summary.jsonl_results)
    file_removed = sum(1 for result in summary.file_results if result.removed)
    return days, jsonl_removed, file_removed


def _collect_node_metric_snapshots(config: ClusterConfig, *, timeout: float) -> list[dict[str, object]]:
    snapshots: list[dict[str, object]] = []
    for node_name, node in sorted(config.compute_nodes.items()):
        if node.runtime is None:
            continue
        base_url = f"http://{node.host}:{_runtime(node).port}"
        health = check_omlx_health(base_url, timeout=timeout, include_models=True)
        hot_loaded_runtime_ids = extract_hot_loaded_models(health.model_statuses)
        hot_loaded_models = map_runtime_models_to_aliases(config, node, hot_loaded_runtime_ids)
        snapshots.append(
            {
                "timestamp": datetime.now().astimezone().isoformat(),
                "node_name": node_name,
                "health_ok": health.health_ok,
                "models_ok": health.models_ok,
                "status_ok": health.status_ok,
                "hot_loaded_models": hot_loaded_models,
                "hot_loaded_count": len(hot_loaded_models),
                "errors": health.errors,
            }
        )
    return snapshots


def _append_snapshots(metrics_path: Path, snapshots: list[dict[str, object]]) -> None:
    with metrics_path.open("a") as handle:
        for snapshot in snapshots:
            handle.write(json.dumps(snapshot, separators=(",", ":")) + "\n")


def _print_snapshot_summary(snapshots: list[dict[str, object]]) -> None:
    for snapshot in snapshots:
        node_name = str(snapshot.get("node_name", ""))
        hot_loaded = snapshot.get("hot_loaded_models")
        if isinstance(hot_loaded, list):
            hot_loaded_display = ",".join(str(item) for item in hot_loaded)
        else:
            hot_loaded_display = ""
        typer.echo(
            f"node: {node_name} health={'ok' if snapshot.get('health_ok') else 'fail'} "
            f"models={'ok' if snapshot.get('models_ok') else 'fail'} "
            f"hot_loaded_count={snapshot.get('hot_loaded_count', 0)} "
            f"hot_loaded={hot_loaded_display}"
        )


def _first_cache_node(config: ClusterConfig) -> tuple[str, Node] | None:
    for node_name, node in config.nodes.items():
        if node.has_role(NodeRole.CACHE):
            return node_name, node
    return None


def _remote_cache_target(config: ClusterConfig) -> tuple[str, Node] | None:
    if os.environ.get(REMOTE_CACHE_EXEC_ENV) == "1":
        return None
    cache_target = _first_cache_node(config)
    if cache_target is None:
        return None
    cache_name, cache_node = cache_target
    if _is_local_host(cache_node.host):
        return None
    return cache_name, cache_node


def _is_local_host(host: str) -> bool:
    normalized = host.strip().lower()
    if normalized in {"localhost", "127.0.0.1", "::1"}:
        return True

    local_names = {socket.gethostname().lower(), socket.getfqdn().lower()}
    local_short_names = {name.split(".")[0] for name in local_names}
    if normalized in local_names or normalized.split(".")[0] in local_short_names:
        return True

    return False


def _dispatch_cache_command_if_remote(
    *,
    config: ClusterConfig,
    cache_command_args: list[str],
    timeout: int,
) -> bool:
    cache_target = _remote_cache_target(config)
    if cache_target is None:
        return False
    cache_name, cache_node = cache_target

    remote_command = " ".join(
        shlex.quote(arg)
        for arg in [
            "env",
            f"{REMOTE_CACHE_EXEC_ENV}=1",
            "uv",
            "run",
            "thunder-forge",
            *cache_command_args,
        ]
    )

    typer.echo(f"cache_exec: remote {cache_name} ({cache_node.host})")
    result = ssh_run(
        cache_node.user,
        cache_node.host,
        remote_command,
        timeout=timeout,
        stream=True,
        shell=cache_node.shell,
        node_name=cache_name,
    )
    if result.returncode != 0:
        typer.echo(f"Error: remote cache command failed with exit code {result.returncode}", err=True)
        raise typer.Exit(result.returncode)
    return True


def _remote_artifact_complete_on_cache(*, cache_node: Node, model_dir_name: str, timeout: int = 30) -> bool:
    command = (
        "set -euo pipefail; "
        'CACHE_ROOT="${TF_CACHE_OMLX_MODELS_DIR:-$HOME/.omlx/models}"; '
        f'MODEL_DIR="$CACHE_ROOT/{model_dir_name}"; '
        'test -d "$MODEL_DIR" && '
        'test -f "$MODEL_DIR/config.json" && '
        "test -z \"$(find \"$MODEL_DIR\" -name '*.incomplete' -print -quit)\" && "
        'test ! -e "$MODEL_DIR/.rsync-partial" && '
        "test -n \"$(find \"$MODEL_DIR\" \\( -name '*.safetensors' -o -name '*.bin' \\) -type f -print -quit)\""
    )
    result = ssh_run(
        cache_node.user,
        cache_node.host,
        command,
        timeout=timeout,
        shell=cache_node.shell,
    )
    return result.returncode == 0


def _resolve_transport_plan_for_sync(
    *,
    requested_transport: str,
    runtime_node: Node,
    remote_cache_target: tuple[str, Node] | None,
    timeout: int,
) -> TransportPlan:
    if remote_cache_target is None or requested_transport == "management" or not runtime_node.fabric_host:
        return build_transport_plan(
            requested_transport=requested_transport,
            management_host=runtime_node.host,
            node_user=runtime_node.user,
            fabric_host=runtime_node.fabric_host,
        )

    cache_name, cache_node = remote_cache_target
    payload = {
        "requested_transport": requested_transport,
        "management_host": runtime_node.host,
        "node_user": runtime_node.user,
        "fabric_host": runtime_node.fabric_host,
        "timeout": 2,
    }
    payload_b64 = base64.b64encode(json.dumps(payload).encode("utf-8")).decode("ascii")
    result = ssh_run(
        cache_node.user,
        cache_node.host,
        _remote_transport_plan_probe_command(payload_b64=payload_b64),
        timeout=max(timeout + 30, 60),
        shell=cache_node.shell,
        node_name=cache_name,
    )
    if result.returncode != 0:
        typer.echo(f"Error: remote transport probe failed on {cache_name} ({cache_node.host})", err=True)
        stderr = (result.stderr or "").strip()
        if stderr:
            typer.echo(stderr, err=True)
        raise typer.Exit(result.returncode)

    marker = "__TF_TRANSPORT_PLAN__"
    payload_line = next(
        (line for line in reversed((result.stdout or "").splitlines()) if line.startswith(marker)),
        None,
    )
    if payload_line is None:
        typer.echo(
            f"Error: remote transport probe on {cache_name} ({cache_node.host}) returned no plan",
            err=True,
        )
        raise typer.Exit(1)

    plan_data = json.loads(payload_line[len(marker) :])
    return TransportPlan(
        requested_transport=str(plan_data["requested_transport"]),
        management_host=str(plan_data["management_host"]),
        transport_host=str(plan_data["transport_host"]),
        resolved_transport_host=str(plan_data["resolved_transport_host"]),
        fabric_fallback=str(plan_data.get("fabric_fallback", "")),
        error=str(plan_data.get("error", "")),
    )


def _prepare_cache_role_node(*, cache_name: str, cache_node: Node, timeout: int) -> None:
    if cache_node.home_dir is None:
        cache_node.home_dir = f"/Users/{cache_node.user}"

    tooling_result = ensure_omlx_tooling(
        cache_node,
        apply=True,
        timeout=timeout,
        upgrade=True,
        progress=_progress,
    )
    _fail_on_setup_errors(tooling_result.errors)
    if not tooling_result.ok:
        typer.echo(f"Error: cache setup did not verify cleanly on {cache_name}", err=True)
        raise typer.Exit(1)
    typer.echo(f"  tooling_path: {tooling_result.resolved_omlx_path or tooling_result.omlx_path}")

    if _is_local_host(cache_node.host):
        ensure_cache_hub_dir(progress=_progress)
        return

    _progress(f"cache_exec: ensuring cache hub on {cache_name} ({cache_node.host})")
    setup_result = ssh_run(
        cache_node.user,
        cache_node.host,
        _cache_hub_setup_command(),
        timeout=timeout,
        stream=True,
        shell=cache_node.shell,
        node_name=cache_name,
    )
    if setup_result.returncode != 0:
        typer.echo(f"Error: remote cache setup failed on {cache_name} ({cache_node.host})", err=True)
        raise typer.Exit(setup_result.returncode)


def _get_runtime_node(config: ClusterConfig, node: str) -> Node:
    """Return a configured oMLX runtime node or exit with a CLI error."""
    from thunder_forge.cluster.config import RuntimeType

    if node not in config.nodes:
        typer.echo(f"Error: node '{node}' not found", err=True)
        raise typer.Exit(1)
    runtime_node = config.nodes[node]
    if runtime_node.runtime is None:
        typer.echo(f"Error: node '{node}' has no runtime configured", err=True)
        raise typer.Exit(1)
    if runtime_node.runtime.type != RuntimeType.OMLX:
        typer.echo(f"Error: unsupported runtime '{runtime_node.runtime.type}'", err=True)
        raise typer.Exit(1)
    return runtime_node


def _format_bytes(size_bytes: int) -> str:
    value = float(size_bytes)
    for unit in ("B", "KB", "MB", "GB"):
        if value < 1024 or unit == "GB":
            return f"{value:.1f} {unit}" if unit != "B" else f"{int(value)} B"
        value /= 1024
    return f"{value:.1f} GB"


def _runtime(runtime_node: Node) -> NodeRuntime:
    return cast(NodeRuntime, runtime_node.runtime)


def _edge_model_catalog_from_config(config: ClusterConfig) -> list[EdgeModelCatalogEntry]:
    assigned_aliases: set[str] = set()
    for node in config.nodes.values():
        if not node.models or not node.has_role(NodeRole.INFERENCE) or node.runtime is None:
            continue
        assigned_aliases.update(model_id for model_id in node.models if model_id in config.models)

    catalog: list[EdgeModelCatalogEntry] = []
    for model_id in assigned_aliases:
        model = config.models[model_id]
        base_model = model.model_info.base_model if model.model_info is not None else ""
        real_llm_id = base_model or model.source.repo or model.runtime_model_id
        description_parts = [real_llm_id] if real_llm_id else []
        if model.runtime_model_id and model.runtime_model_id != real_llm_id:
            description_parts.append(f"runtime: {model.runtime_model_id}")
        if model.notes:
            description_parts.append(model.notes)
        catalog.append(
            EdgeModelCatalogEntry(
                id=model_id,
                name=model_id,
                description="; ".join(description_parts),
                runtime_model_id=model.runtime_model_id,
                source_repo=model.source.repo,
                base_model=base_model,
                context_length=model.max_context,
                benchmark_only=model.benchmark_only,
            )
        )
    return catalog


def _edge_base_url_from_config(config: ClusterConfig) -> str:
    try:
        host = config.gateway.host
    except ValueError:
        host = "127.0.0.1"
    return f"http://{host}:{config.services.edge_port}/v1"


def _opencode_config_from_catalog(
    *,
    model_catalog: list[EdgeModelCatalogEntry],
    provider_id: str,
    provider_name: str,
    base_url: str,
    api_key: str,
    model: str | None = None,
    small_model: str | None = None,
) -> dict[str, object]:
    models: dict[str, dict[str, object]] = {}
    for entry in sorted(model_catalog, key=lambda item: item.id):
        model_config: dict[str, object] = {
            "name": entry.name,
        }
        if entry.benchmark_only:
            model_config["status"] = "beta"
        models[entry.id] = model_config

    provider: dict[str, object] = {
        "npm": "@ai-sdk/openai-compatible",
        "name": provider_name,
        "options": {
            "baseURL": base_url,
            "apiKey": api_key,
        },
        "models": models,
    }

    payload: dict[str, object] = {
        "$schema": "https://opencode.ai/config.json",
        "provider": {provider_id: provider},
    }
    if model:
        payload["model"] = f"{provider_id}/{model}"
    if small_model:
        payload["small_model"] = f"{provider_id}/{small_model}"
    return payload


def _model_comment(entry: EdgeModelCatalogEntry) -> str:
    return entry.base_model or entry.source_repo or entry.runtime_model_id


def _yaml_scalar(value: str) -> str:
    if value and all(char.isalnum() or char in "._/:+-" for char in value):
        return value
    return json.dumps(value, ensure_ascii=False)


def _opencode_config_jsonc_from_catalog(
    *,
    model_catalog: list[EdgeModelCatalogEntry],
    provider_id: str,
    provider_name: str,
    base_url: str,
    api_key: str,
    api_key_comment: str = "",
    model: str | None = None,
    small_model: str | None = None,
) -> str:
    model_blocks: list[str] = []
    for entry in sorted(model_catalog, key=lambda item: item.id):
        model_config: dict[str, object] = {"name": entry.name}
        if entry.benchmark_only:
            model_config["status"] = "beta"
        model_config_text = json.dumps(model_config, indent=2, ensure_ascii=False).replace("\n", "\n        ")
        comment = _model_comment(entry)
        comment_line = f"        // {comment}\n" if comment else ""
        model_blocks.append(f"{comment_line}        {json.dumps(entry.id)}: {model_config_text}")

    properties = [f'  "$schema": {json.dumps("https://opencode.ai/config.json")}']
    if model:
        properties.append(f'  "model": {json.dumps(f"{provider_id}/{model}")}')
    if small_model:
        properties.append(f'  "small_model": {json.dumps(f"{provider_id}/{small_model}")}')
    api_key_lines = [
        f'        "baseURL": {json.dumps(base_url)},',
    ]
    if api_key_comment:
        api_key_lines.append(f"        // {api_key_comment}")
    api_key_lines.append(f'        "apiKey": {json.dumps(api_key)}')
    properties.append(
        "\n".join(
            [
                '  "provider": {',
                f"    {json.dumps(provider_id)}: {{",
                '      "npm": "@ai-sdk/openai-compatible",',
                f'      "name": {json.dumps(provider_name)},',
                '      "options": {',
                "\n".join(api_key_lines),
                "      },",
                '      "models": {',
                ",\n".join(model_blocks),
                "      }",
                "    }",
                "  }",
            ]
        )
    )
    return "{\n" + ",\n".join(properties) + "\n}\n"


def _opencode_api_key_comment(*, client_id: str | None, api_key_env: str | None) -> str:
    resolved_env = api_key_env or DEFAULT_OPENCODE_API_KEY_ENV
    if client_id:
        resolved_env, _ = edge_api_key_from_env(env={}, client_id=client_id, users_env=EDGE_USER_PREFIX)
    return f"{resolved_env}: check .env"


def _edge_api_key_env_name(
    *,
    client_id: str | None,
    api_key_env: str | None,
    default_api_key_env: str,
) -> str:
    if client_id:
        resolved_env, _ = edge_api_key_from_env(env={}, client_id=client_id, users_env=EDGE_USER_PREFIX)
        return resolved_env
    return api_key_env or default_api_key_env


def _ensure_edge_client_key_if_requested(
    *,
    client_id: str | None,
    create_missing_key: bool,
    yes: bool,
) -> None:
    if not create_missing_key:
        return
    if not client_id:
        typer.echo("Error: --create-missing-key requires a client id", err=True)
        raise typer.Exit(1)

    resolved_env, _ = edge_api_key_from_env(env={}, client_id=client_id, users_env=EDGE_USER_PREFIX)
    _repo_root, env_file = _load_repo_dotenv()
    if _dotenv_value(env_file, resolved_env):
        typer.echo(f"{resolved_env}: present in {env_file}", err=True)
        return
    if not yes and not _confirm_create_edge_key(resolved_env, env_file):
        typer.echo(f"Error: {resolved_env} was not created", err=True)
        raise typer.Exit(1)

    result = ensure_edge_api_keys(env_file=env_file, clients=[client_id], users_env=EDGE_USER_PREFIX)
    status = result.keys[0].status if result.keys else "present"
    typer.echo(f"{resolved_env}: {status} in {env_file}", err=True)


def _hermes_config_yaml_from_catalog(
    *,
    model_catalog: list[EdgeModelCatalogEntry],
    provider_id: str,
    base_url: str,
    api_key_env: str,
) -> str:
    lines = [
        "custom_providers:",
        f"  - name: {_yaml_scalar(provider_id)}",
        f"    base_url: {_yaml_scalar(base_url)}",
        f"    key_env: {_yaml_scalar(api_key_env)}",
        "    api_mode: chat_completions",
        "    models:",
    ]
    for entry in sorted(model_catalog, key=lambda item: item.id):
        comment_parts = [
            part
            for part in (_model_comment(entry), "benchmark-only" if entry.benchmark_only else "")
            if part
        ]
        if comment_parts:
            lines.append(f"      # {'; '.join(comment_parts)}")
        lines.append(f"      {entry.id}: {{}}")
    return "\n".join(lines) + "\n"


def _dotenv_value(env_file: Path, env_name: str) -> str:
    from dotenv import dotenv_values

    value = dotenv_values(env_file).get(env_name)
    return str(value or "").strip()


def _confirm_create_edge_key(env_name: str, env_file: Path) -> bool:
    if not sys.stdin.isatty():
        typer.echo(f"Error: {env_name} is not set; rerun with --yes to create it in {env_file}", err=True)
        raise typer.Exit(1)
    print(f"{env_name} is not set. Create it in {env_file}? [Y/n] ", end="", file=sys.stderr, flush=True)
    answer = sys.stdin.readline().strip().lower()
    return answer in {"", "y", "yes"}


def _osc52_clipboard_sequence(text: str) -> str:
    return _multiplexer_aware_osc52_sequence(text, env=os.environ)


def _raw_osc52_clipboard_sequence(text: str) -> str:
    encoded = base64.b64encode(text.encode()).decode("ascii")
    return f"\033]52;c;{encoded}\a"


def _tmux_passthrough_sequence(sequence: str) -> str:
    escape = "\033"
    escaped_sequence = sequence.replace(escape, escape * 2)
    return f"{escape}Ptmux;{escaped_sequence}{escape}\\"


def _screen_passthrough_sequence(sequence: str) -> str:
    return f"\033P{sequence}\033\\"


def _multiplexer_aware_osc52_sequence(text: str, *, env: dict[str, str]) -> str:
    sequence = _raw_osc52_clipboard_sequence(text)
    term = env.get("TERM", "").lower()
    if env.get("TMUX") or term.startswith("tmux") or (term.startswith("screen") and not env.get("STY")):
        return _tmux_passthrough_sequence(sequence)
    if env.get("STY") or term.startswith("screen"):
        return _screen_passthrough_sequence(sequence)
    return sequence


def _copy_to_clipboard(text: str) -> None:
    try:
        with Path("/dev/tty").open("w") as terminal:
            terminal.write(_osc52_clipboard_sequence(text))
            terminal.flush()
            return
    except OSError:
        pass

    if not sys.stderr.isatty():
        typer.echo("Error: no terminal available for OSC52 clipboard copy", err=True)
        raise typer.Exit(2)
    sys.stderr.write(_osc52_clipboard_sequence(text))
    sys.stderr.flush()


def _resolve_opencode_api_key(
    *,
    client_id: str | None,
    api_key_env: str | None,
    inject_api_key: bool,
    create_missing_key: bool,
    yes: bool,
) -> str:
    resolved_env = api_key_env or DEFAULT_OPENCODE_API_KEY_ENV
    if client_id:
        resolved_env, _ = edge_api_key_from_env(env={}, client_id=client_id, users_env=EDGE_USER_PREFIX)

    if not inject_api_key:
        return f"{{env:{resolved_env}}}"

    _repo_root, env_file = _load_repo_dotenv()
    if client_id:
        resolved_env, api_key = edge_api_key_from_env(client_id=client_id, users_env=EDGE_USER_PREFIX)
    else:
        api_key = os.environ.get(resolved_env, "").strip()
    if not api_key:
        api_key = _dotenv_value(env_file, resolved_env)

    if api_key:
        return api_key
    if not client_id or not create_missing_key:
        typer.echo(f"Error: {resolved_env} is not set", err=True)
        raise typer.Exit(1)

    if not yes and not _confirm_create_edge_key(resolved_env, env_file):
        typer.echo(f"Error: {resolved_env} was not created", err=True)
        raise typer.Exit(1)

    result = ensure_edge_api_keys(env_file=env_file, clients=[client_id], users_env=EDGE_USER_PREFIX)
    api_key = _dotenv_value(env_file, resolved_env)
    if not api_key:
        typer.echo(f"Error: failed to create {resolved_env} in {env_file}", err=True)
        raise typer.Exit(1)
    status = result.keys[0].status if result.keys else "present"
    typer.echo(f"{resolved_env}: {status} in {env_file}", err=True)
    return api_key


def _gateway_restart_notice(config: ClusterConfig) -> str:
    try:
        gateway_name = config.gateway_name
    except ValueError:
        return ""
    return (
        "gateway_routes: unchanged; run "
        f"`make restart {gateway_name}` only after changing model placement or node topology"
    )


def _assigned_repo_ids_for_node(config: ClusterConfig, node_name: str, runtime_node: Node) -> list[str]:
    if not runtime_node.models:
        typer.echo(f"Error: node '{node_name}' has no models configured", err=True)
        raise typer.Exit(1)

    repo_ids: list[str] = []
    for model_id in runtime_node.models:
        configured_model = config.models.get(model_id)
        if configured_model is None:
            typer.echo(f"Error: node '{node_name}' references unknown model '{model_id}'", err=True)
            raise typer.Exit(1)
        repo_id = configured_model.source.repo.strip()
        if not repo_id:
            typer.echo(f"Error: models.{model_id}.source.repo is required for full node sync", err=True)
            raise typer.Exit(1)
        if repo_id not in repo_ids:
            repo_ids.append(repo_id)
    return repo_ids


def _assigned_model_dirs_for_node(config: ClusterConfig, node_name: str, runtime_node: Node) -> set[str]:
    repo_ids = _assigned_repo_ids_for_node(config, node_name, runtime_node)
    return {
        build_artifact_identity(repo_id).model_dir_name
        for repo_id in repo_ids
    }


def _list_node_cache_model_dirs(runtime_node: Node, *, timeout: int) -> list[str]:
    if runtime_node.home_dir is None:
        runtime_node.home_dir = f"/Users/{runtime_node.user}"
    models_dir = f"{runtime_node.home_dir}/.omlx/models"
    command = (
        "set -euo pipefail; "
        f"MODELS_DIR={shlex.quote(models_dir)}; "
        "if [ ! -d \"$MODELS_DIR\" ]; then exit 0; fi; "
        "find \"$MODELS_DIR\" -mindepth 2 -maxdepth 2 -type d"
    )
    result = ssh_run(
        runtime_node.user,
        runtime_node.host,
        command,
        timeout=timeout,
        shell=runtime_node.shell,
    )
    if result.returncode != 0:
        typer.echo(f"Error: failed to list node cache model directories on {runtime_node.host}", err=True)
        if result.stderr:
            typer.echo(result.stderr.strip(), err=True)
        raise typer.Exit(result.returncode)

    model_dirs: list[str] = []
    prefix = f"{models_dir}/"
    for raw_line in result.stdout.splitlines():
        line = raw_line.strip()
        if not line:
            continue
        if line.startswith(prefix):
            line = line.removeprefix(prefix)
        if "/" not in line:
            continue
        model_dirs.append(line)
    return sorted(dict.fromkeys(model_dirs))


def _prune_node_cache_models(
    *,
    config: ClusterConfig,
    node_name: str,
    runtime_node: Node,
    dry_run: bool,
    timeout: int,
) -> None:
    if runtime_node.home_dir is None:
        runtime_node.home_dir = f"/Users/{runtime_node.user}"
    assigned_model_dirs = _assigned_model_dirs_for_node(config, node_name, runtime_node)
    current_model_dirs = _list_node_cache_model_dirs(runtime_node, timeout=timeout)
    stale_model_dirs = [model_dir for model_dir in current_model_dirs if model_dir not in assigned_model_dirs]

    typer.echo("")
    typer.echo("== Cache Prune ==")
    typer.echo(f"assigned_models: {len(assigned_model_dirs)}")
    typer.echo(f"cached_models: {len(current_model_dirs)}")
    if not stale_model_dirs:
        typer.echo("status: prune not needed")
        return

    for model_dir in stale_model_dirs:
        typer.echo(f"prune_model_dir: {model_dir}")

    if dry_run:
        typer.echo("mode: dry-run")
        return

    for model_dir in stale_model_dirs:
        absolute_model_dir = f"{runtime_node.home_dir}/.omlx/models/{model_dir}"
        result = ssh_run(
            runtime_node.user,
            runtime_node.host,
            f"rm -rf {shlex.quote(absolute_model_dir)}",
            timeout=timeout,
            shell=runtime_node.shell,
        )
        if result.returncode != 0:
            typer.echo(f"Error: prune failed for {model_dir} on {runtime_node.host}", err=True)
            if result.stderr:
                typer.echo(result.stderr.strip(), err=True)
            raise typer.Exit(result.returncode)

    typer.echo(f"status: pruned {len(stale_model_dirs)} model_dir(s)")


def _resolve_cluster_smoke_inputs(
    config: ClusterConfig,
    *,
    model: str | None,
    alias: str | None,
    client_id: str | None,
    timeout: float | None,
) -> tuple[str, str, str, float]:
    resolved_alias = alias or config.operations.smoke.alias
    resolved_model = model or config.operations.smoke.model
    resolved_client_id = client_id or config.operations.smoke.client_id
    resolved_timeout = timeout or config.operations.smoke.timeout

    if not resolved_alias:
        typer.echo("Error: --alias is required unless operations.smoke.alias is set", err=True)
        raise typer.Exit(1)
    if not resolved_model:
        configured_model = config.models.get(resolved_alias)
        if configured_model is not None:
            resolved_model = configured_model.runtime_model_id
    if not resolved_model:
        typer.echo(
            "Error: --model is required unless operations.smoke.model is set "
            "or operations.smoke.alias names a configured model",
            err=True,
        )
        raise typer.Exit(1)
    if not resolved_client_id:
        typer.echo("Error: --client-id is required unless operations.smoke.client_id is set", err=True)
        raise typer.Exit(1)

    return resolved_model, resolved_alias, resolved_client_id, resolved_timeout


def _print_runtime_node_header(node: str, runtime_node: Node) -> None:
    typer.echo(f"node: {node}")
    typer.echo(f"runtime: {_runtime(runtime_node).type}")
    typer.echo(f"management_host: {runtime_node.host}")
    if runtime_node.fabric_host:
        typer.echo("fabric_host: true")


@config_app.command("lint")
def config_lint() -> None:
    """Validate Thunder Forge desired state before generating runtime/router config."""
    from thunder_forge.cluster.config import lint_cluster_config

    config, _ = _load_config()
    issues = lint_cluster_config(config)
    if not issues:
        typer.echo("config: ok")
        return

    typer.echo("config: issues found")
    for issue in issues:
        typer.echo(f"{issue.severity}: {issue.path}: {issue.message}")
    if any(issue.severity == "error" for issue in issues):
        raise typer.Exit(1)


def _print_launchd_service_result(result, *, manager: str, dry_run: bool) -> None:
    systemd_layout = manager == "systemd" or result.plist_path.endswith(".service")
    manager_display = "systemd (daemon alias)" if manager == "daemon" and systemd_layout else manager
    typer.echo(f"service: {result.service}")
    typer.echo(f"manager: {manager_display}")
    path_label = "unit_path" if systemd_layout else "plist_path"
    staging_label = "staging_unit_path" if systemd_layout else "staging_plist_path"
    content_label = "unit" if systemd_layout else "plist"
    typer.echo(f"{path_label}: {result.plist_path}")
    if result.staging_plist_path:
        typer.echo(f"{staging_label}: {result.staging_plist_path}")
    typer.echo(f"label: {result.label}")
    typer.echo(f"mode: {'dry-run' if dry_run else 'apply'}")
    if result.plist_content:
        typer.echo(f"{content_label}:")
        for line in result.plist_content.splitlines():
            typer.echo(f"  {line}")
    if result.commands:
        typer.echo("commands:")
        for cmd in result.commands:
            typer.echo(f"  - {cmd}")
    if result.applied:
        typer.echo(f"service_label_verified: {'yes' if result.service_label_verified else 'no'}")
        typer.echo(f"health_ok: {'yes' if result.health_ok else 'no'}")
        if result.ok:
            typer.echo("status: restarted")
    for error in result.errors:
        typer.echo(f"Error: {error}", err=True)


def _service_result_failed(result) -> bool:
    return bool(result.errors) or (result.applied and not result.ok)


def _gateway_operator_user(config: ClusterConfig, user: str) -> str:
    if user:
        return user
    try:
        return config.gateway.user or os.environ.get("USER", "")
    except ValueError:
        return os.environ.get("USER", "")


def _frontend_system_manager() -> str:
    return "systemd" if platform.system() == "Linux" else "daemon"


def _gateway_frontend_setup_reason() -> str:
    if platform.system() == "Linux":
        return "install Olla + TF edge systemd services"
    return "install Olla + TF edge LaunchDaemons"


def _print_gateway_daemon_setup_result(result: GatewayDaemonSetupResult, *, dry_run: bool) -> None:
    systemd_layout = any(service.plist_path.endswith(".service") for service in result.services)
    typer.echo("scope: gateway")
    typer.echo("manager: systemd (daemon alias)" if systemd_layout else "manager: daemon")
    typer.echo(f"operator_user: {result.user}")
    typer.echo(f"admin_user: {result.admin_user or '(direct sudo)'}")
    typer.echo(f"sudoers_path: {result.sudoers_path}")
    typer.echo(f"script_path: {result.script_path}")
    typer.echo(f"mode: {'dry-run' if dry_run else 'apply'}")
    if result.services:
        typer.echo("services:")
        for service in result.services:
            service_systemd = service.plist_path.endswith(".service")
            path_label = "unit_path" if service_systemd else "plist_path"
            staging_label = "staging_unit_path" if service_systemd else "staging_plist_path"
            typer.echo(f"  - {service.service}: {service.label}")
            typer.echo(f"    {path_label}: {service.plist_path}")
            typer.echo(f"    {staging_label}: {service.staging_plist_path}")
    if dry_run and result.script_content:
        typer.echo("script:")
        for line in result.script_content.splitlines():
            typer.echo(f"  {line}")
    if result.commands:
        typer.echo("commands:")
        for cmd in result.commands:
            typer.echo(f"  - {cmd}")
    if result.applied:
        typer.echo(f"sudoers_verified: {'yes' if result.sudoers_verified else 'no'}")
        typer.echo(f"service_labels_verified: {'yes' if result.service_labels_verified else 'no'}")
        typer.echo(f"health_ok: {'yes' if result.health_ok else 'no'}")
        if result.ok:
            typer.echo("status: daemon setup complete")
    for error in result.errors:
        typer.echo(f"Error: {error}", err=True)


def _node_names_with_role(config: ClusterConfig, role: NodeRole) -> list[str]:
    return [name for name, node in config.nodes.items() if node.has_role(role)]


def _resolve_prepare_targets(config: ClusterConfig, target: str | None) -> tuple[list[str], list[str], list[str]]:
    if target:
        if target not in config.nodes:
            typer.echo(f"Error: node '{target}' not found", err=True)
            raise typer.Exit(1)
        node = config.nodes[target]
        gateway_names = [target] if node.has_role(NodeRole.GATEWAY) else []
        cache_names = [target] if node.has_role(NodeRole.CACHE) else []
        inference_names = [target] if node.has_role(NodeRole.INFERENCE) else []
        if not gateway_names and not cache_names and not inference_names:
            typer.echo(f"Error: node '{target}' has no prepare role", err=True)
            raise typer.Exit(1)
        return gateway_names, cache_names, inference_names

    gateway_names = _node_names_with_role(config, NodeRole.GATEWAY)[:1]
    cache_names = _node_names_with_role(config, NodeRole.CACHE)
    inference_names = list(config.compute_nodes.keys())
    return gateway_names, cache_names, inference_names


def _print_prepare_plan(
    *,
    target: str | None,
    dry_run: bool,
    gateway_names: list[str],
    cache_names: list[str],
    inference_names: list[str],
    config: ClusterConfig,
) -> None:
    typer.echo("Thunder Forge cluster prepare")
    typer.echo(f"target: {target or 'all'}")
    typer.echo(f"mode: {'dry-run' if dry_run else 'apply'}")
    typer.echo("plan:")
    if gateway_names:
        for name in gateway_names:
            node = config.nodes[name]
            typer.echo(f"  gateway: {name} ({node.host}) -> Olla + TF edge")
    else:
        typer.echo("  gateway: skipped")
    if cache_names:
        for name in cache_names:
            node = config.nodes[name]
            typer.echo(f"  cache: {name} ({node.host}) -> oMLX model hub")
    else:
        typer.echo("  cache: skipped")
    if inference_names:
        typer.echo(f"  inference: {', '.join(inference_names)} -> oMLX LaunchDaemon")
    else:
        typer.echo("  inference: skipped")


def _edge_status_base_url(config: ClusterConfig) -> str:
    try:
        gateway_host = config.gateway.host
    except ValueError:
        gateway_host = "127.0.0.1"
    return f"http://{gateway_host}:{config.services.edge_port}"


def _fetch_cluster_status_payload(config: ClusterConfig, *, target: str | None) -> dict[str, object]:
    base_url = _edge_status_base_url(config)
    params = {"target": target} if target else None
    with httpx.Client(base_url=base_url, timeout=30.0, trust_env=False) as client:
        try:
            response = client.get("/status", params=params)
        except httpx.HTTPError as exc:
            typer.echo(f"Error: failed to fetch cluster status from {base_url}: {exc}", err=True)
            raise typer.Exit(1)
    try:
        payload = response.json()
    except ValueError as exc:
        typer.echo(f"Error: edge status endpoint returned invalid JSON: {exc}", err=True)
        raise typer.Exit(1) from exc
    if not isinstance(payload, dict):
        typer.echo("Error: edge status endpoint returned an invalid payload", err=True)
        raise typer.Exit(1)
    if payload.get("error"):
        typer.echo(f"Error: edge status endpoint error: {payload['error']}", err=True)
        raise typer.Exit(1)
    return payload


def _print_cluster_status_payload(payload: dict[str, object]) -> None:
    typer.echo("Thunder Forge cluster status")
    typer.echo(f"target: {payload.get('target', 'all')}")

    gateway = payload.get("gateway")
    if isinstance(gateway, dict):
        typer.echo(
            f"{gateway.get('name', 'gateway')}: olla_version={gateway.get('olla_version', 'unknown')} "
            f"latest={gateway.get('latest_olla_version', 'unknown')} upgrade={gateway.get('upgrade', 'unknown')}"
        )

    inference = payload.get("inference")
    if isinstance(inference, list):
        for node in inference:
            if not isinstance(node, dict):
                continue
            typer.echo(
                f"{node.get('name', 'node')}: health={node.get('health', 'fail')} "
                f"models={node.get('models', 'fail')}"
            )
            typer.echo(f"  omlx_version: {node.get('omlx_version', 'unknown')}")
            served_models = node.get("served_models")
            if isinstance(served_models, list) and served_models:
                typer.echo(f"  served_models: {', '.join(str(item) for item in served_models)}")
            hot_loaded_models = node.get("hot_loaded_models")
            if isinstance(hot_loaded_models, list) and hot_loaded_models:
                typer.echo(f"  hot_loaded_models: {', '.join(str(item) for item in hot_loaded_models)}")
            errors = node.get("errors")
            if isinstance(errors, list):
                for error in errors:
                    typer.echo(f"Error: {node.get('name', 'node')}: {error}", err=True)

    summary = payload.get("summary")
    if isinstance(summary, dict) and summary.get("omlx_upgrade_hint"):
        typer.echo(f"omlx_upgrade_hint: {summary['omlx_upgrade_hint']}")


def _usage_report_default_period() -> str:
    return datetime.now().date().isoformat()


def _extract_version_token(raw_output: str) -> str:
    semver_pattern = re.compile(r"^v?\d+(?:\.\d+)*(?:\.[A-Za-z0-9]+)*(?:[-+][A-Za-z0-9.-]+)?$")
    fallback = ""
    for line in raw_output.splitlines():
        stripped_line = line.strip()
        if not stripped_line:
            continue
        if not fallback:
            fallback = stripped_line
        normalized_line = (
            stripped_line.replace("(", " ")
            .replace(")", " ")
            .replace(",", " ")
            .replace(";", " ")
            .replace(":", " ")
            .replace("=", " ")
        )
        for token in normalized_line.split():
            normalized = token.strip()
            if not normalized:
                continue
            if semver_pattern.match(normalized):
                return normalized
    return fallback


def _probe_node_version(node: Node, command: str, *, timeout: int = 8) -> str:
    try:
        result = ssh_run(
            node.user,
            node.host,
            command,
            timeout=timeout,
            shell=node.shell,
        )
    except Exception:
        return "unknown"

    output = "\n".join(part for part in [result.stdout, result.stderr] if part)
    version = _extract_version_token(output)
    if not version:
        return "unknown"
    return version


def _gateway_olla_version_for_node(config: ClusterConfig, repo_root: Path, gateway_node: Node) -> str:
    candidate = str((repo_root / config.services.olla_bin_dir / "olla").expanduser())
    command = (
        "set -u; "
        f"CANDIDATE={shlex.quote(candidate)}; "
        "LOCAL_CANDIDATE=\"$HOME/.local/bin/olla\"; "
        "if [ -x \"$CANDIDATE\" ]; then \"$CANDIDATE\" --version; "
        "elif [ -x \"$LOCAL_CANDIDATE\" ]; then \"$LOCAL_CANDIDATE\" --version; "
        "elif command -v olla >/dev/null 2>&1; then \"$(command -v olla)\" --version; "
        "else exit 127; fi"
    )
    return _probe_node_version(gateway_node, command)


def _omlx_version_for_node(runtime_node: Node) -> str:
    home_dir = runtime_node.home_dir or f"/Users/{runtime_node.user}"
    candidate = f"{home_dir}/.local/bin/omlx" if home_dir else ""
    if candidate:
        command = (
            "set -u; "
            f"CANDIDATE={shlex.quote(candidate)}; "
            "LOCAL_CANDIDATE=\"$HOME/.local/bin/omlx\"; "
            "if [ -x \"$CANDIDATE\" ]; then \"$CANDIDATE\" --version; "
            "elif [ -x \"$LOCAL_CANDIDATE\" ]; then \"$LOCAL_CANDIDATE\" --version; "
            "elif command -v omlx >/dev/null 2>&1; then \"$(command -v omlx)\" --version; "
            "else exit 127; fi"
        )
    else:
        command = (
            "set -u; "
            "if command -v omlx >/dev/null 2>&1; then \"$(command -v omlx)\" --version; "
            "else exit 127; fi"
        )
    return _probe_node_version(runtime_node, command)


def _latest_olla_release_version(*, timeout: int = 5) -> str:
    request = urllib.request.Request(LATEST_OLLA_RELEASE_API, headers={"User-Agent": "thunder-forge"})
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            payload = json.loads(response.read().decode())
    except (OSError, TimeoutError, ValueError, TypeError):
        return ""
    latest = str(payload.get("tag_name", "")).strip()
    return latest


def _resolved_olla_version_for_prepare(*, cli_version: str | None, services: ServiceConfig) -> str:
    if cli_version is not None and cli_version.strip():
        return cli_version.strip()
    if services.olla_version_pinned:
        return services.olla_version
    return "latest"


def _progress(message: str) -> None:
    typer.echo(f"  {message}")


def _fail_on_setup_errors(errors: list[str]) -> None:
    for error in errors:
        typer.echo(f"Error: {error}", err=True)
    if errors:
        raise typer.Exit(1)


def _olla_upgrade_note(status: str) -> str:
    if status == "upgraded":
        return "upgrade applied"
    if status == "installed":
        return "installed (fresh bootstrap)"
    if status == "current":
        return "already current"
    return "checked and applied if newer"


@cluster_app.command("prepare")
def cluster_prepare(
    target: str | None = typer.Argument(
        None,
        help="Optional node name. Omit for gateway + cache + all inference nodes.",
    ),
    dry_run: bool = typer.Option(
        True,
        "--dry-run/--apply",
        help="Print the prepare plan without changing hosts by default.",
    ),
    admin_user: str = typer.Option(
        "",
        "--admin-user",
        help="Override the inference admin account used for remote su/sudo bootstrap.",
    ),
    timeout: int = typer.Option(300, "--timeout", help="Timeout in seconds for daemon setup commands."),
    olla_version: str | None = typer.Option(
        None,
        "--olla-version",
        help="Olla release version override. When omitted and config is unpinned, uses latest.",
    ),
    olla_os: str | None = typer.Option(None, "--olla-os", help="Olla release OS segment."),
    olla_arch: str | None = typer.Option(None, "--olla-arch", help="Olla release architecture segment."),
    olla_bin_dir: Path | None = typer.Option(None, "--olla-bin-dir", help="Local Olla binary directory."),
) -> None:
    """Prepare the gateway, cache/download hub, and inference daemons as one cluster."""
    config, repo_root = _load_config()
    resolved_olla_version = _resolved_olla_version_for_prepare(
        cli_version=olla_version,
        services=config.services,
    )
    resolved_olla_os = olla_os or config.services.olla_os
    resolved_olla_arch = olla_arch or config.services.olla_arch
    resolved_olla_bin_dir = olla_bin_dir or Path(config.services.olla_bin_dir)
    gateway_names, cache_names, inference_names = _resolve_prepare_targets(config, target)
    _print_prepare_plan(
        target=target,
        dry_run=dry_run,
        gateway_names=gateway_names,
        cache_names=cache_names,
        inference_names=inference_names,
        config=config,
    )

    if dry_run:
        if gateway_names:
            preview_olla_path = resolved_olla_bin_dir / "olla"
            typer.echo(f"would: ensure Olla {resolved_olla_version} at {preview_olla_path}")
            typer.echo("would: generate configs/olla-config.yaml")
        if cache_names:
            for name in cache_names:
                cache_node = config.nodes[name]
                home_dir = cache_node.home_dir or f"/Users/{cache_node.user}"
                typer.echo(f"would: ensure/upgrade oMLX CLI at {home_dir}/.local/bin/omlx")
                if _is_local_host(cache_node.host):
                    typer.echo(f"would: ensure cache hub {cache_omlx_models_dir_from_env()}")
                else:
                    typer.echo("would: ensure remote cache hub ${TF_CACHE_OMLX_MODELS_DIR:-$HOME/.omlx/models}")
        for name in inference_names:
            node = config.nodes[name]
            resolved_admin_user = admin_user or node.admin_user
            escalation = f"su={resolved_admin_user}" if resolved_admin_user else f"sudo={node.user}"
            home_dir = node.home_dir or f"/Users/{node.user}"
            typer.echo(f"would: ensure/upgrade oMLX CLI at {home_dir}/.local/bin/omlx")
            typer.echo(f"would: bootstrap {name} ssh={node.user}@{node.host} {escalation}")
        return

    binary_path = _repo_relative_path(repo_root, resolved_olla_bin_dir) / "olla"
    if gateway_names:
        typer.echo("")
        typer.echo("== Gateway Tooling ==")
        olla_result = ensure_olla_binary(
            version=resolved_olla_version,
            os_name=resolved_olla_os,
            arch=resolved_olla_arch,
            bin_dir=_repo_relative_path(repo_root, resolved_olla_bin_dir),
            progress=_progress,
        )
        binary_path = olla_result.binary_path
        typer.echo(f"  latest_olla: {getattr(olla_result, 'version', resolved_olla_version)}")
        typer.echo(f"  upgrade_note: {_olla_upgrade_note(getattr(olla_result, 'status', ''))}")
        config_path = write_generated_olla_config(config, repo_root=repo_root)
        typer.echo(f"  config: generated {config_path}")

    for gateway_name in gateway_names:
        gateway_node = config.nodes[gateway_name]
        typer.echo("")
        typer.echo(f"== Gateway: {gateway_name} ({gateway_node.host}) ==")
        gateway_admin_user = config.services.frontend_admin_user or gateway_node.admin_user or "(direct sudo)"
        typer.echo(
            "  auth: "
            f"operator={_gateway_operator_user(config, '')} admin={gateway_admin_user} "
            f"reason={_gateway_frontend_setup_reason()}"
        )
        result = run_gateway_daemon_setup(
            repo_root=repo_root,
            binary=binary_path,
            config_path=repo_root / "configs" / "olla-config.yaml",
            edge_host=config.services.edge_host,
            olla_port=resolve_port(None, default=config.services.olla_port),
            edge_port=resolve_port(None, default=config.services.edge_port),
            olla_base_url=local_base_url(config.services.olla_port),
            users_env=EDGE_USER_PREFIX,
            access_log_path=_edge_access_log_path(repo_root, config, None),
            user=_gateway_operator_user(config, ""),
            admin_user=config.services.frontend_admin_user or gateway_node.admin_user,
            interactive_sudo=True,
            apply=True,
            timeout=timeout,
            progress=_progress,
        )
        _fail_on_setup_errors(result.errors)
        if not result.ok:
            typer.echo("Error: gateway setup did not verify cleanly", err=True)
            raise typer.Exit(1)
        typer.echo("  status: gateway ready")

    for cache_name in cache_names:
        cache_node = config.nodes[cache_name]
        typer.echo("")
        typer.echo(f"== Cache Hub: {cache_name} ({cache_node.host}) ==")
        _prepare_cache_role_node(cache_name=cache_name, cache_node=cache_node, timeout=timeout)
        typer.echo("  status: cache hub ready")

    for node_name in inference_names:
        runtime_node = _get_runtime_node(config, node_name)
        if runtime_node.home_dir is None:
            runtime_node.home_dir = f"/Users/{runtime_node.user}"
        resolved_admin_user = admin_user or runtime_node.admin_user
        via_su = bool(resolved_admin_user)
        typer.echo("")
        typer.echo(f"== Inference: {node_name} ({runtime_node.host}) ==")
        if via_su:
            typer.echo(
                "  auth: "
                f"ssh={runtime_node.user}@{runtime_node.host} method=su admin={resolved_admin_user} "
                "reason=install oMLX LaunchDaemon"
            )
        else:
            typer.echo(
                "  auth: "
                f"ssh={runtime_node.user}@{runtime_node.host} method=sudo user={runtime_node.user} "
                "reason=install oMLX LaunchDaemon"
            )
        tooling_result = ensure_omlx_tooling(
            runtime_node,
            apply=True,
            timeout=timeout,
            upgrade=True,
            progress=_progress,
        )
        _fail_on_setup_errors(tooling_result.errors)
        if not tooling_result.ok:
            typer.echo("Error: oMLX tooling setup did not verify cleanly", err=True)
            raise typer.Exit(1)
        typer.echo(f"  latest_omlx: {tooling_result.resolved_omlx_version or 'unknown'}")
        typer.echo("  upgrade_note: checked and applied if newer")
        result = run_omlx_daemon_setup(
            runtime_node,
            admin_user=resolved_admin_user or None,
            via_su=via_su,
            apply=True,
            timeout=timeout,
            progress=_progress,
        )
        _fail_on_setup_errors(result.errors)
        if not result.ok:
            typer.echo("Error: inference setup did not verify cleanly", err=True)
            raise typer.Exit(1)
        typer.echo("  status: inference ready")

    typer.echo("")
    typer.echo("status: cluster prepare complete")


@cluster_app.command("restart")
def cluster_restart(
    target: str | None = typer.Argument(
        None,
        help="Optional node name. Omit for gateway + all inference nodes.",
    ),
    dry_run: bool = typer.Option(
        True,
        "--dry-run/--apply",
        help="Print the restart plan without changing hosts by default.",
    ),
    timeout: int = typer.Option(300, "--timeout", help="Timeout in seconds for daemon restart commands."),
    binary: Path | None = typer.Option(None, "--binary", help="Local Olla binary path."),
) -> None:
    """Restart gateway and inference daemons through the configured managers."""
    config, repo_root = _load_config()
    resolved_binary = binary or Path(config.services.olla_bin_dir) / "olla"
    gateway_names, _cache_names, inference_names = _resolve_prepare_targets(config, target)
    typer.echo("Thunder Forge cluster restart")
    typer.echo(f"target: {target or 'all'}")
    typer.echo(f"mode: {'dry-run' if dry_run else 'apply'}")
    frontend_manager = _frontend_system_manager()

    if gateway_names:
        typer.echo("")
        typer.echo("== Gateway ==")
        if dry_run:
            typer.echo("  would: generate configs/olla-config.yaml")
        else:
            write_generated_olla_config(config, repo_root=repo_root)
            typer.echo("  config: generated configs/olla-config.yaml")

        olla_result = run_olla_service_restart(
            repo_root=repo_root,
            binary=resolved_binary,
            config_path=repo_root / "configs" / "olla-config.yaml",
            port=resolve_port(None, default=config.services.olla_port),
            manager=frontend_manager,
            apply=not dry_run,
            timeout=timeout,
            user=_gateway_operator_user(config, ""),
        )
        typer.echo(f"  olla: {olla_result.label}")
        if not dry_run and _service_result_failed(olla_result):
            _fail_on_setup_errors(olla_result.errors or ["Olla restart did not verify cleanly"])

        edge_result = run_edge_service_restart(
            repo_root=repo_root,
            host=config.services.edge_host,
            port=resolve_port(None, default=config.services.edge_port),
            manager=frontend_manager,
            apply=not dry_run,
            timeout=timeout,
            users_env=EDGE_USER_PREFIX,
            access_log_path=_edge_access_log_path(repo_root, config, None),
            user=_gateway_operator_user(config, ""),
        )
        typer.echo(f"  edge: {edge_result.label}")
        if not dry_run and _service_result_failed(edge_result):
            _fail_on_setup_errors(edge_result.errors or ["TF edge restart did not verify cleanly"])

    for node_name in inference_names:
        runtime_node = _get_runtime_node(config, node_name)
        if runtime_node.home_dir is None:
            runtime_node.home_dir = f"/Users/{runtime_node.user}"
        typer.echo("")
        typer.echo(f"== Inference: {node_name} ({runtime_node.host}) ==")
        result = run_omlx_daemon_restart(runtime_node, apply=not dry_run, timeout=timeout)
        typer.echo(f"  omlx: {result.label}")
        if not dry_run and _service_result_failed(result):
            _fail_on_setup_errors(result.errors or ["oMLX restart did not verify cleanly"])

    if not gateway_names and not inference_names:
        typer.echo("status: nothing to restart")
        return
    typer.echo("")
    typer.echo("status: cluster restart complete" if not dry_run else "status: dry-run complete")


@cluster_app.command("status")
def cluster_status(
    target: str | None = typer.Argument(
        None,
        help="Optional inference node name. Omit for all inference nodes.",
    ),
    json_output: bool = typer.Option(False, "--json", help="Emit machine-readable JSON output."),
) -> None:
    """Check cluster status through the edge /status JSON endpoint."""
    config, _ = _load_config()
    payload = _fetch_cluster_status_payload(config, target=target)
    if json_output:
        typer.echo(json.dumps(payload, indent=2, sort_keys=True))
    else:
        _print_cluster_status_payload(payload)


@cluster_app.command("sync")
def cluster_sync(
    target: str = typer.Argument(..., help="Inference node name to sync."),
    model: str | None = typer.Option(
        None,
        "--model",
        help="Optional Hugging Face model repo id. Omit to sync every model assigned to the node.",
    ),
    dry_run: bool = typer.Option(True, "--dry-run/--apply", help="Print sync commands without executing by default."),
    transport: str | None = typer.Option(
        None,
        "--transport",
        help="Transport selection: auto, fabric, or management. Defaults to operations.sync.transport.",
    ),
    management: bool = typer.Option(
        False,
        "--management",
        help="Force management host even when fabric_host probing is enabled.",
    ),
    timeout: int | None = typer.Option(None, "--timeout", help="Timeout in seconds for rsync when applying."),
    restart_runtime: bool = typer.Option(False, "--restart-runtime", help="Restart node runtime after sync."),
    no_restart_runtime: bool = typer.Option(False, "--no-restart-runtime", help="Skip node runtime restart."),
    prune: bool = typer.Option(
        False,
        "--prune",
        help="Prune node cache models that are not assigned to this inference node before restart.",
    ),
) -> None:
    """Sync configured model artifacts to a node and optionally restart its runtime."""
    if restart_runtime and no_restart_runtime:
        typer.echo("Error: use only one of --restart-runtime or --no-restart-runtime", err=True)
        raise typer.Exit(1)

    config, _ = _load_config()
    runtime_node = _get_runtime_node(config, target)
    resolved_transport = transport or config.operations.sync.transport
    resolved_timeout = timeout or config.operations.sync.timeout
    resolved_restart = config.operations.sync.restart_runtime
    if restart_runtime:
        resolved_restart = True
    if no_restart_runtime:
        resolved_restart = False

    typer.echo("Thunder Forge cluster sync")
    typer.echo(f"target: {target}")
    typer.echo(f"mode: {'dry-run' if dry_run else 'apply'}")
    _run_artifact_sync_workflow(
        config=config,
        node=target,
        model=model,
        dry_run=dry_run,
        transport=resolved_transport,
        management=management,
        timeout=resolved_timeout,
    )

    if prune:
        _prune_node_cache_models(
            config=config,
            node_name=target,
            runtime_node=runtime_node,
            dry_run=dry_run,
            timeout=resolved_timeout,
        )

    if resolved_restart:
        if dry_run:
            typer.echo(f"would: restart {target} oMLX runtime after sync")
        else:
            if runtime_node.home_dir is None:
                runtime_node.home_dir = f"/Users/{runtime_node.user}"
            typer.echo("")
            typer.echo("== Runtime Restart ==")
            result = run_omlx_daemon_restart(runtime_node, apply=True, timeout=300)
            typer.echo(f"  omlx: restarted {result.label}")
            if _service_result_failed(result):
                _fail_on_setup_errors(result.errors or ["oMLX restart did not verify cleanly"])
    else:
        typer.echo("runtime_restart: skipped")

    if notice := _gateway_restart_notice(config):
        typer.echo(notice)


@cluster_app.command("smoke")
def cluster_smoke(
    target: str | None = typer.Argument(
        None,
        help="Optional node name. Omit for all inference nodes plus gateway smoke.",
    ),
    model: str | None = typer.Option(None, "--model", help="Backend runtime model id to verify."),
    alias: str | None = typer.Option(None, "--alias", help="Public alias routed by Olla to the backend model."),
    client_id: str | None = typer.Option(None, "--client-id", help="TF edge client id whose API key should be used."),
    timeout: float | None = typer.Option(None, "--timeout", help="HTTP timeout in seconds."),
) -> None:
    """Smoke inference node health, Olla routing, and TF edge auth/proxy."""
    config, _ = _load_config()
    model, alias, client_id, timeout = _resolve_cluster_smoke_inputs(
        config,
        model=model,
        alias=alias,
        client_id=client_id,
        timeout=timeout,
    )
    _gateway_names, _cache_names, inference_names = _resolve_prepare_targets(config, target)
    typer.echo("Thunder Forge cluster smoke")
    typer.echo(f"target: {target or 'all'}")

    failed = False
    for node_name in inference_names:
        runtime_node = _get_runtime_node(config, node_name)
        base_url = f"http://{runtime_node.host}:{_runtime(runtime_node).port}"
        health = check_omlx_health(base_url)
        health_status = "ok" if health.health_ok else "fail"
        model_visible = model in health.models
        models_status = "ok" if health.models_ok and model_visible else "fail"
        typer.echo(
            f"runtime {node_name}: health={health_status} "
            f"models={models_status} model_visible={'yes' if model_visible else 'no'}"
        )
        if health.models_ok and not model_visible:
            typer.echo(f"Error: {node_name}: model '{model}' is not visible", err=True)
        failed = failed or not (health.health_ok and health.models_ok and model_visible)

    expected_endpoint = f"{inference_names[0]}-omlx-live" if target and len(inference_names) == 1 else None
    olla_result = smoke_olla_router(
        base_url=local_base_url(config.services.olla_port),
        model=model,
        alias=alias,
        expected_endpoint=expected_endpoint,
        timeout=timeout,
    )
    typer.echo(
        "olla: "
        f"health={'ok' if olla_result.health_ok else 'fail'} "
        f"chat={'ok' if olla_result.chat_ok else 'fail'} "
        f"alias={'ok' if olla_result.alias_ok else 'fail'}"
    )
    failed = failed or not olla_result.ok

    env_name, api_key = edge_api_key_from_env(client_id=client_id, users_env=EDGE_USER_PREFIX)
    if not api_key:
        typer.echo(f"Error: {env_name} is not set", err=True)
        raise typer.Exit(1)
    edge_result = smoke_edge_contract(
        base_url=local_base_url(config.services.edge_port),
        api_key=api_key,
        model=alias,
        prompt="Reply with one short word: pong.",
        timeout=timeout,
    )
    typer.echo(
        "edge: "
        f"auth={'ok' if edge_result.missing_auth_401 and edge_result.invalid_auth_401 else 'fail'} "
        f"chat={'ok' if edge_result.chat_ok else 'fail'} "
        f"session={'ok' if edge_result.session_ok else 'fail'}"
    )
    failed = failed or not edge_result.ok

    for result in [olla_result, edge_result]:
        for error in result.errors:
            typer.echo(f"Error: {error}", err=True)
    if failed:
        raise typer.Exit(1)
    typer.echo("status: cluster smoke complete")


@service_app.command("setup-daemon")
def service_setup_daemon(
    dry_run: bool = typer.Option(
        True,
        "--dry-run/--apply",
        help="Print gateway setup script and commands without executing by default.",
    ),
    timeout: int = typer.Option(300, "--timeout", help="Timeout in seconds for setup commands."),
    user: str = typer.Option(
        "",
        "--user",
        help="Operator/runtime user to grant narrow sudoers rights. Defaults to the configured gateway user.",
    ),
    admin_user: str = typer.Option(
        "",
        "--admin-user",
        help="Admin account that can run sudo on the gateway. Defaults to services.frontend.admin_user.",
    ),
    script_path: str = typer.Option(
        "",
        "--script-path",
        help="Local path for the generated setup script. Defaults to .tmp/run/thunder-forge-gateway-daemon-setup.sh.",
    ),
    binary: Path = typer.Option(Path(".tmp/olla-bin/olla"), "--binary", help="Olla binary path."),
    config_path: Path = typer.Option(Path("configs/olla-config.yaml"), "--config", help="Olla config path."),
    edge_host: str | None = typer.Option(
        None,
        "--host",
        help="Host/interface for TF edge. Defaults to tfconfig services.edge.host.",
    ),
    olla_port: int | None = typer.Option(
        None,
        "--olla-port",
        help="Olla port. Defaults to tfconfig services.olla.port.",
    ),
    edge_port: int | None = typer.Option(
        None,
        "--edge-port",
        help="Edge port. Defaults to tfconfig services.edge.port.",
    ),
    olla_base_url: str | None = typer.Option(
        None,
        "--olla-base-url",
        help=f"Olla base URL for edge. Defaults to tfconfig services.olla.port or {DEFAULT_OLLA_PORT}.",
    ),
    users_env: str = typer.Option(
        EDGE_USER_PREFIX,
        "--users-env",
        help="Env var prefix for per-user TF_USER_<NAME> API keys (gateway setup).",
    ),
    access_log: Path | None = typer.Option(
        None,
        "--access-log",
        help="TF edge JSONL access log path. Defaults to tfconfig services.edge.access_log.",
    ),
    allow_sudo_prompt: bool = typer.Option(
        False,
        "--allow-sudo-prompt",
        help="Allow gateway setup to prompt through sudo/su. Use from a real terminal.",
    ),
) -> None:
    """Set up gateway Olla/Edge system daemons and narrow sudoers for future repairs."""
    config, repo_root = _load_config()
    resolved_admin_user = admin_user or config.services.frontend_admin_user
    if not dry_run and resolved_admin_user and not allow_sudo_prompt:
        typer.echo(
            "Error: gateway setup through an admin user requires --allow-sudo-prompt from a real terminal",
            err=True,
        )
        raise typer.Exit(1)

    setup_kwargs = {
        "repo_root": repo_root,
        "binary": binary,
        "config_path": config_path,
        "edge_host": edge_host or config.services.edge_host,
        "olla_port": resolve_port(olla_port, default=config.services.olla_port),
        "edge_port": resolve_port(edge_port, default=config.services.edge_port),
        "olla_base_url": olla_base_url or local_base_url(config.services.olla_port),
        "users_env": users_env,
        "access_log_path": _edge_access_log_path(repo_root, config, access_log),
        "user": _gateway_operator_user(config, user),
        "admin_user": resolved_admin_user,
        "interactive_sudo": allow_sudo_prompt,
        "script_path": script_path or None,
        "apply": not dry_run,
        "timeout": timeout,
    }
    if not dry_run:
        setup_kwargs["progress"] = typer.echo
    result = run_gateway_daemon_setup(**setup_kwargs)
    _print_gateway_daemon_setup_result(result, dry_run=dry_run)
    if bool(result.errors) or (result.applied and not result.ok):
        raise typer.Exit(1)


@service_app.command("restart")
def service_restart(
    service: str = typer.Option(..., "--service", help="Service to restart: olla, edge, or omlx."),
    node: str = typer.Option("", "--node", help="Node name for node-scoped services such as omlx."),
    manager: str = typer.Option(
        "launchd",
        "--manager",
        help="Service manager. Olla/edge: launchd, daemon, or systemd. oMLX: process, daemon, or launchd.",
    ),
    dry_run: bool = typer.Option(
        True,
        "--dry-run/--apply",
        help="Print plist and commands without executing by default.",
    ),
    timeout: int = typer.Option(60, "--timeout", help="Timeout in seconds for service commands."),
    binary: Path = typer.Option(Path(".tmp/olla-bin/olla"), "--binary", help="Olla binary path."),
    config_path: Path = typer.Option(Path("configs/olla-config.yaml"), "--config", help="Olla config path."),
    port: int | None = typer.Option(
        None,
        "--port",
        help="Service port. Defaults to the matching tfconfig services.*.port value.",
    ),
    host: str | None = typer.Option(
        None,
        "--host",
        help="Host/interface for frontend services such as edge. Defaults to tfconfig services.edge.host.",
    ),
    olla_base_url: str | None = typer.Option(
        None,
        "--olla-base-url",
        help=f"Olla base URL for edge. Defaults to tfconfig services.olla.port or {DEFAULT_OLLA_PORT}.",
    ),
    users_env: str = typer.Option(
        EDGE_USER_PREFIX,
        "--users-env",
        help="Env var prefix for per-user TF_USER_<NAME> API keys (service restart).",
    ),
    access_log: Path | None = typer.Option(
        None,
        "--access-log",
        help="TF edge JSONL access log path. Defaults to tfconfig services.edge.access_log.",
    ),
    allow_sudo_prompt: bool = typer.Option(
        False,
        "--allow-sudo-prompt",
        help="Allow frontend system-daemon install commands to prompt for sudo instead of requiring sudo -n.",
    ),
) -> None:
    """Restart a managed Thunder Forge service through its configured service manager."""
    normalized_service = service.lower()
    normalized_manager = manager.lower()

    if normalized_service == "olla":
        config, repo_root = _load_config()
        resolved_port = resolve_port(port, default=config.services.olla_port)
        result = run_olla_service_restart(
            repo_root=repo_root,
            binary=binary,
            config_path=config_path,
            port=resolved_port,
            manager=normalized_manager,
            apply=not dry_run,
            timeout=timeout,
            interactive_sudo=allow_sudo_prompt,
            admin_user=config.services.frontend_admin_user if allow_sudo_prompt else "",
        )
        _print_launchd_service_result(result, manager=normalized_manager, dry_run=dry_run)
        if _service_result_failed(result):
            raise typer.Exit(1)
        return

    if normalized_service == "edge":
        config, repo_root = _load_config()
        resolved_port = resolve_port(port, default=config.services.edge_port)
        resolved_olla_base_url = olla_base_url or local_base_url(config.services.olla_port)
        access_log_path = _edge_access_log_path(repo_root, config, access_log)
        result = run_edge_service_restart(
            repo_root=repo_root,
            host=host or config.services.edge_host,
            port=resolved_port,
            olla_base_url=resolved_olla_base_url,
            users_env=users_env,
            access_log_path=access_log_path,
            manager=normalized_manager,
            apply=not dry_run,
            timeout=timeout,
            interactive_sudo=allow_sudo_prompt,
            admin_user=config.services.frontend_admin_user if allow_sudo_prompt else "",
        )
        _print_launchd_service_result(result, manager=normalized_manager, dry_run=dry_run)
        if _service_result_failed(result):
            raise typer.Exit(1)
        return

    if normalized_service != "omlx":
        typer.echo("Error: --service must be 'olla', 'edge', or 'omlx'", err=True)
        raise typer.Exit(1)
    if not node:
        typer.echo("Error: --service omlx requires --node", err=True)
        raise typer.Exit(1)

    config, _ = _load_config()
    runtime_node = _get_runtime_node(config, node)
    if runtime_node.home_dir is None:
        runtime_node.home_dir = f"/Users/{runtime_node.user}"
    _print_runtime_node_header(node, runtime_node)

    if normalized_manager == "process":
        process_result = run_omlx_process_restart(runtime_node, apply=not dry_run, timeout=timeout)
        typer.echo("service: omlx")
        typer.echo(f"manager: {normalized_manager}")
        typer.echo(f"command: {process_result.command}")
        typer.echo(f"pid_path: {process_result.pid_path}")
        typer.echo(f"stdout_log: {process_result.stdout_log}")
        typer.echo(f"stderr_log: {process_result.stderr_log}")
        typer.echo(f"mode: {'dry-run' if dry_run else 'apply'}")
        if process_result.commands:
            typer.echo("commands:")
            for cmd in process_result.commands:
                typer.echo(f"  - {cmd}")
        if process_result.applied:
            if process_result.pid:
                typer.echo(f"pid: {process_result.pid}")
            typer.echo(f"health_ok: {'yes' if process_result.health_ok else 'no'}")
            if process_result.ok:
                typer.echo("status: restarted")
        for error in process_result.errors:
            typer.echo(f"Error: {error}", err=True)
        if process_result.errors or (process_result.applied and not process_result.ok):
            raise typer.Exit(1)
        return

    if normalized_manager == "daemon":
        result = run_omlx_daemon_restart(runtime_node, apply=not dry_run, timeout=timeout)
    elif normalized_manager == "launchd":
        result = run_omlx_runtime_restart(runtime_node, apply=not dry_run, timeout=timeout)
    else:
        typer.echo("Error: omlx --manager must be 'process', 'daemon', or 'launchd'", err=True)
        raise typer.Exit(1)
    result.service = "omlx"
    _print_launchd_service_result(result, manager=normalized_manager, dry_run=dry_run)
    if _service_result_failed(result):
        raise typer.Exit(1)


@edge_app.command("smoke")
def edge_smoke(
    base_url: str | None = typer.Option(
        None,
        "--base-url",
        help=f"TF edge base URL. Defaults to tfconfig services.edge.port or {DEFAULT_EDGE_PORT}.",
    ),
    users_env: str = typer.Option(
        EDGE_USER_PREFIX,
        "--users-env",
        help="Env var prefix for per-user TF_USER_<NAME> API keys (smoke).",
    ),
    client_id: str = typer.Option(..., "--client-id", help="Client id whose API key should be used."),
    model: str = typer.Option(..., "--model", help="Model or alias to use for the chat smoke."),
    prompt: str = typer.Option("Reply with one short word: pong.", "--prompt", help="Short smoke-test prompt."),
    timeout: float = typer.Option(30.0, "--timeout", help="HTTP timeout in seconds."),
) -> None:
    """Run a black-box smoke test against a running TF edge."""
    config, _ = _load_config()
    resolved_base_url = base_url or local_base_url(config.services.edge_port)
    env_name, api_key = edge_api_key_from_env(client_id=client_id, users_env=users_env)
    if not api_key:
        typer.echo(f"Error: {env_name} is not set", err=True)
        raise typer.Exit(1)

    result = smoke_edge_contract(
        base_url=resolved_base_url,
        api_key=api_key,
        model=model,
        prompt=prompt,
        timeout=timeout,
    )
    typer.echo(f"base_url: {result.base_url}")
    typer.echo(f"model: {result.model}")
    typer.echo(f"missing_auth_401: {'yes' if result.missing_auth_401 else 'no'}")
    typer.echo(f"invalid_auth_401: {'yes' if result.invalid_auth_401 else 'no'}")
    typer.echo(f"models: {'ok' if result.models_ok else 'fail'}")
    typer.echo(f"chat: {'ok' if result.chat_ok else 'fail'}")
    typer.echo(f"session: {'ok' if result.session_ok else 'fail'}")
    typer.echo(f"latency_ms: {result.latency_ms}")
    if result.olla_endpoint:
        typer.echo(f"olla_endpoint: {result.olla_endpoint}")
    for error in result.errors:
        typer.echo(f"Error: {error}", err=True)
    if not result.ok:
        raise typer.Exit(1)


@edge_app.command("keys")
def edge_keys(
    clients: list[str] = typer.Option(
        [],
        "--client",
        help="Client id to ensure in .env. Repeat for multiple clients.",
    ),
    env_file: Path = typer.Option(Path(".env"), "--env-file", help="Dotenv file to update."),
    users_env: str = typer.Option(
        EDGE_USER_PREFIX,
        "--users-env",
        help="Env var prefix for per-user TF_USER_<NAME> API keys (keys).",
    ),
) -> None:
    """Generate missing MVP TF edge API keys into a local dotenv file."""
    if not clients:
        typer.echo("Error: provide at least one --client", err=True)
        raise typer.Exit(1)
    repo_root, _ = _load_repo_dotenv()
    env_path = _repo_relative_path(repo_root, env_file)
    result = ensure_edge_api_keys(
        env_file=env_path,
        clients=clients,
        users_env=users_env,
    )
    typer.echo(f"env_file: {result.env_file}")
    typer.echo(f"users_env: {users_env}")
    for key in result.keys:
        typer.echo(f"client: {key.client_id}")
        typer.echo(f"status: {key.status}")
    typer.echo("secrets_printed: no")


@edge_app.command("usage")
def edge_usage(
    access_log: Path | None = typer.Option(
        None,
        "--access-log",
        help="JSONL access log path. Defaults to tfconfig services.edge.access_log.",
    ),
) -> None:
    """Summarize TF edge JSONL request accounting by client id."""
    config, repo_root = _load_config()
    log_path = _edge_access_log_path(repo_root, config, access_log)
    summary = summarize_edge_usage(log_path)
    typer.echo(f"access_log: {summary.access_log_path}")
    typer.echo(f"requests_total: {summary.requests_total}")
    typer.echo(f"invalid_lines: {summary.invalid_lines}")
    if not summary.clients:
        typer.echo("clients: []")
        return
    typer.echo("clients:")
    for client in summary.clients:
        typer.echo(f"  - client_id: {client.client_id}")
        typer.echo(f"    requests: {client.requests}")
        typer.echo(f"    failures: {client.failures}")
        typer.echo(f"    latency_ms_p50: {client.latency_ms_p50}")
        typer.echo(f"    latency_ms_p95: {client.latency_ms_p95}")
        if client.models:
            typer.echo("    models:")
            for model, count in client.models.items():
                typer.echo(f"      {model}: {count}")
        if client.endpoints:
            typer.echo("    endpoints:")
            for endpoint, count in client.endpoints.items():
                typer.echo(f"      {endpoint}: {count}")


@usage_app.command("report")
def usage_report(
    period: str | None = typer.Option(
        None,
        "--period",
        help="Optional YYYY-MM-DD day to summarize. Use 'all' to summarize all records.",
    ),
    access_log: Path | None = typer.Option(
        None,
        "--access-log",
        help="JSONL edge access log path. Defaults to tfconfig services.edge.access_log.",
    ),
    node_metrics_log: Path | None = typer.Option(
        None,
        "--node-metrics-log",
        help="Optional JSONL node metrics file with hot-loaded model samples.",
    ),
    json_output: bool = typer.Option(
        False,
        "--json",
        help="Emit machine-readable JSON instead of a compact text summary.",
    ),
) -> None:
    """Summarize daily TF usage by user, node, model, and hour."""
    config, repo_root = _load_config()
    access_log_path = _edge_access_log_path(repo_root, config, access_log)
    node_metrics_path = _usage_metrics_log_path(repo_root, node_metrics_log)
    if period is None:
        period = _usage_report_default_period()
    summary = summarize_daily_usage(access_log_path, period=period, node_metrics_path=node_metrics_path)

    if json_output:
        typer.echo(json.dumps(summary.to_json_dict(), indent=2, sort_keys=True))
        return

    typer.echo(f"period: {summary.period}")
    typer.echo(f"access_log: {summary.access_log_path}")
    if summary.node_metrics_path:
        typer.echo(f"node_metrics_log: {summary.node_metrics_path}")
    typer.echo(f"requests_total: {summary.requests_total}")
    typer.echo(f"consumed_ms_total: {summary.consumed_ms_total}")
    typer.echo(f"invalid_lines: {summary.invalid_lines}")

    def _print_mapping(title: str, mapping: dict[str, object]) -> None:
        typer.echo(f"{title}:")
        if not mapping:
            typer.echo("  []")
            return
        for key, value in mapping.items():
            typer.echo(f"  - {key}: {value}")

    _print_mapping("requests_by_user", summary.requests_by_user)
    _print_mapping("consumed_ms_by_user", summary.consumed_ms_by_user)
    if summary.requests_by_user_model:
        typer.echo("requests_by_user_model:")
        for client_id, models in summary.requests_by_user_model.items():
            typer.echo(f"  - {client_id}:")
            for model, count in models.items():
                typer.echo(f"      {model}: {count}")
    _print_mapping("requests_by_node", summary.requests_by_node)
    _print_mapping("consumed_ms_by_node", summary.consumed_ms_by_node)
    _print_mapping("requests_by_model", summary.requests_by_model)
    _print_mapping("consumed_ms_by_model", summary.consumed_ms_by_model)
    _print_mapping("requests_by_hour", summary.requests_by_hour)
    if summary.requests_by_node_model:
        typer.echo("requests_by_node_model:")
        for node_name, models in summary.requests_by_node_model.items():
            typer.echo(f"  - {node_name}:")
            for model, count in models.items():
                typer.echo(f"      {model}: {count}")
@usage_app.command("collect-node-metrics")
def usage_collect_node_metrics(
    output: Path | None = typer.Option(
        None,
        "--output",
        help="JSONL output path. Defaults to logs/tf-node-metrics.jsonl.",
    ),
    timeout: float = typer.Option(
        5.0,
        "--timeout",
        help="Per-node oMLX health/status timeout in seconds.",
    ),
    interval_seconds: int = typer.Option(
        60,
        "--interval-seconds",
        help="Collection interval in seconds when --continuous is enabled.",
    ),
    continuous: bool = typer.Option(
        False,
        "--continuous/--once",
        help="Run continuously. Default is one collection run.",
    ),
    retention_days: int | None = typer.Option(
        None,
        "--retention-days",
        help="Override log retention days. Defaults to tfconfig services.log_retention_days (3).",
    ),
) -> None:
    """Collect usage snapshots per inference node for hot-loaded model reporting."""
    config, repo_root = _load_config()
    if interval_seconds <= 0:
        typer.echo("Error: interval seconds must be positive", err=True)
        raise typer.Exit(1)
    metrics_path = _usage_metrics_log_path(repo_root, output)
    metrics_path.parent.mkdir(parents=True, exist_ok=True)
    run_count = 0

    while True:
        run_count += 1
        days, jsonl_removed, file_removed = _trim_logs_with_policy(
            repo_root=repo_root,
            config=config,
            node_metrics_path=metrics_path,
            retention_days=retention_days,
        )

        snapshots = _collect_node_metric_snapshots(config, timeout=timeout)
        _append_snapshots(metrics_path, snapshots)

        typer.echo(f"output: {metrics_path}")
        typer.echo(f"run: {run_count}")
        typer.echo(f"samples_written: {len(snapshots)}")
        typer.echo(f"retention_days: {days}")
        typer.echo(f"trimmed_jsonl_records: {jsonl_removed}")
        typer.echo(f"pruned_log_files: {file_removed}")
        _print_snapshot_summary(snapshots)

        if not continuous:
            break
        typer.echo(f"next_run_in_seconds: {interval_seconds}")
        time.sleep(interval_seconds)


@usage_app.command("trim-logs")
def usage_trim_logs(
    node_metrics_log: Path | None = typer.Option(
        None,
        "--node-metrics-log",
        help="Optional node metrics JSONL path. Defaults to logs/tf-node-metrics.jsonl.",
    ),
    retention_days: int | None = typer.Option(
        None,
        "--retention-days",
        help="Override log retention days. Defaults to tfconfig services.log_retention_days (3).",
    ),
) -> None:
    """Trim local TF logs with one shared retention policy."""
    config, repo_root = _load_config()
    metrics_path = _usage_metrics_log_path(repo_root, node_metrics_log)
    days, jsonl_removed, file_removed = _trim_logs_with_policy(
        repo_root=repo_root,
        config=config,
        node_metrics_path=metrics_path,
        retention_days=retention_days,
    )
    typer.echo(f"retention_days: {days}")
    typer.echo(f"trimmed_jsonl_records: {jsonl_removed}")
    typer.echo(f"pruned_log_files: {file_removed}")


@olla_app.command("smoke")
def olla_smoke(
    base_url: str | None = typer.Option(
        None,
        "--base-url",
        help=f"Olla base URL. Defaults to tfconfig services.olla.port or {DEFAULT_OLLA_PORT}.",
    ),
    model: str = typer.Option(..., "--model", help="Backend runtime model id to verify."),
    alias: str = typer.Option(..., "--alias", help="Public alias routed by Olla to the backend model."),
    expected_endpoint: str | None = typer.Option(
        None,
        "--expected-endpoint",
        help="Require this Olla endpoint to be healthy in /internal/status/endpoints.",
    ),
    prompt: str = typer.Option("Reply with one short word: pong.", "--prompt", help="Short smoke-test prompt."),
    timeout: float = typer.Option(30.0, "--timeout", help="HTTP timeout in seconds."),
) -> None:
    """Run a black-box smoke test against a running Olla router."""
    resolved_base_url = base_url or local_base_url(_resolve_olla_port_from_optional_config(None))
    result = smoke_olla_router(
        base_url=resolved_base_url,
        model=model,
        alias=alias,
        expected_endpoint=expected_endpoint,
        prompt=prompt,
        timeout=timeout,
    )
    typer.echo(f"base_url: {result.base_url}")
    typer.echo(f"model: {result.model}")
    typer.echo(f"alias: {result.alias}")
    typer.echo(f"health: {'ok' if result.health_ok else 'fail'}")
    typer.echo(f"endpoints: {'ok' if result.endpoints_ok else 'fail'}")
    typer.echo(f"models: {'ok' if result.models_ok else 'fail'}")
    typer.echo(f"chat: {'ok' if result.chat_ok else 'fail'}")
    typer.echo(f"alias_routing: {'ok' if result.alias_ok else 'fail'}")
    typer.echo(f"session: {'ok' if result.session_ok else 'fail'}")
    typer.echo(f"root_v1: {'absent' if result.root_v1_absent else 'present'}")
    typer.echo(f"latency_ms: {result.latency_ms}")
    if result.olla_endpoint:
        typer.echo(f"olla_endpoint: {result.olla_endpoint}")
    if result.alias_endpoint:
        typer.echo(f"alias_endpoint: {result.alias_endpoint}")
    for error in result.errors:
        typer.echo(f"Error: {error}", err=True)
    if not result.ok:
        raise typer.Exit(1)


@olla_app.command("dev-smoke")
def olla_dev_smoke(
    binary: str = typer.Option(..., "--binary", help="Path to the Olla binary."),
    model: str = typer.Option(..., "--model", help="Backend runtime model id to verify."),
    alias: str = typer.Option(..., "--alias", help="Public alias routed by Olla to the backend model."),
    expected_endpoint: str | None = typer.Option(
        None,
        "--expected-endpoint",
        help="Require this Olla endpoint to be healthy in /internal/status/endpoints.",
    ),
    prompt: str = typer.Option("Reply with one short word: pong.", "--prompt", help="Short smoke-test prompt."),
    timeout: float = typer.Option(30.0, "--timeout", help="HTTP timeout in seconds for smoke checks."),
    port: int | None = typer.Option(
        None,
        "--port",
        help=f"Olla service port. Defaults to tfconfig services.olla.port or {DEFAULT_OLLA_PORT}.",
    ),
) -> None:
    """Generate Olla config, spawn Olla, smoke, teardown. Single-command dev workflow."""
    resolved_port = _resolve_olla_port_from_optional_config(port)
    result = dev_smoke_olla(
        binary=binary,
        model=model,
        alias=alias,
        expected_endpoint=expected_endpoint,
        prompt=prompt,
        smoke_timeout=timeout,
        port=resolved_port,
    )
    typer.echo(f"config_generated: {'yes' if result.config_generated else 'no'}")
    if result.config_path:
        typer.echo(f"config_path: {result.config_path}")
    typer.echo(f"olla_started: {'yes' if result.olla_started else 'no'}")
    typer.echo(f"olla_healthy: {'yes' if result.olla_healthy else 'no'}")
    if result.smoke_result is not None:
        sr = result.smoke_result
        typer.echo(f"health: {'ok' if sr.health_ok else 'fail'}")
        typer.echo(f"endpoints: {'ok' if sr.endpoints_ok else 'fail'}")
        typer.echo(f"models: {'ok' if sr.models_ok else 'fail'}")
        typer.echo(f"chat: {'ok' if sr.chat_ok else 'fail'}")
        typer.echo(f"alias_routing: {'ok' if sr.alias_ok else 'fail'}")
        typer.echo(f"session: {'ok' if sr.session_ok else 'fail'}")
        typer.echo(f"root_v1: {'absent' if sr.root_v1_absent else 'present'}")
        typer.echo(f"latency_ms: {sr.latency_ms}")
        if sr.olla_endpoint:
            typer.echo(f"olla_endpoint: {sr.olla_endpoint}")
        if sr.alias_endpoint:
            typer.echo(f"alias_endpoint: {sr.alias_endpoint}")
        for error in sr.errors:
            typer.echo(f"Error: {error}", err=True)
    typer.echo(f"olla_terminated: {'yes' if result.olla_terminated else 'no'}")
    for error in result.errors:
        typer.echo(f"Error: {error}", err=True)
    if not result.ok:
        raise typer.Exit(1)


@edge_app.command("serve")
def edge_serve(
    host: str = typer.Option("127.0.0.1", "--host", help="Host/interface for the TF edge listener."),
    port: int | None = typer.Option(
        None,
        "--port",
        help=f"Port for the TF edge listener. Defaults to tfconfig services.edge.port or {DEFAULT_EDGE_PORT}.",
    ),
    olla_base_url: str | None = typer.Option(
        None,
        "--olla-base-url",
        help=f"Olla base URL. Defaults to tfconfig services.olla.port or {DEFAULT_OLLA_PORT}.",
    ),
    users_env: str = typer.Option(
        EDGE_USER_PREFIX,
        "--users-env",
        help="Env var prefix for per-user TF_USER_<NAME> API keys (serve).",
    ),
    access_log: Path | None = typer.Option(
        None,
        "--access-log",
        help="JSONL access log path. Defaults to tfconfig services.edge.access_log.",
    ),
    collect_node_metrics: bool = typer.Option(
        True,
        "--collect-node-metrics/--no-collect-node-metrics",
        help="Collect node metrics in-process while edge is running.",
    ),
    metrics_interval_seconds: int = typer.Option(
        60,
        "--metrics-interval-seconds",
        help="Node metrics collection interval in seconds.",
    ),
    metrics_timeout: float = typer.Option(
        5.0,
        "--metrics-timeout",
        help="Per-node oMLX health/status timeout in seconds for edge metrics collector.",
    ),
) -> None:
    """Run the minimal TF edge proxy."""
    cluster_config, repo_root = _load_config()
    if metrics_interval_seconds <= 0:
        typer.echo("Error: metrics interval seconds must be positive", err=True)
        raise typer.Exit(1)
    resolved_port = resolve_port(port, default=cluster_config.services.edge_port)
    resolved_olla_base_url = olla_base_url or local_base_url(cluster_config.services.olla_port)
    clients_by_key = build_edge_clients_from_env(
        users_env=users_env,
    )
    if not clients_by_key:
        typer.echo(
            "Error: no edge API keys found. Run "
            f"`thunder-forge edge keys --client ...` or `make edge-keys EDGE_CLIENTS=...`, or set {users_env}.",
            err=True,
        )
        raise typer.Exit(1)

    access_log_path = _edge_access_log_path(repo_root, cluster_config, access_log)
    metrics_path = _usage_metrics_log_path(repo_root, None)
    days, jsonl_removed, file_removed = _trim_logs_with_policy(
        repo_root=repo_root,
        config=cluster_config,
        node_metrics_path=metrics_path,
        retention_days=None,
    )

    def log_sink(line: str) -> None:
        access_log_path.parent.mkdir(parents=True, exist_ok=True)
        with access_log_path.open("a") as handle:
            handle.write(f"{line}\n")

    client_ids = sorted({client.client_id for client in clients_by_key.values()})

    edge_proxy_config = EdgeProxyConfig(
        olla_base_url=resolved_olla_base_url,
        clients_by_key=clients_by_key,
        cluster_config=cluster_config,
        repo_root=repo_root,
        access_log_sink=log_sink,
        model_catalog=_edge_model_catalog_from_config(cluster_config),
    )
    typer.echo(f"serving_edge: http://{host}:{resolved_port}")
    typer.echo(f"olla_base_url: {resolved_olla_base_url}")
    typer.echo(f"clients: {', '.join(client_ids)}")
    typer.echo(f"api_key_count: {len(clients_by_key)}")
    typer.echo(f"access_log: {access_log_path}")
    typer.echo(f"retention_days: {days}")
    typer.echo(f"trimmed_jsonl_records: {jsonl_removed}")
    typer.echo(f"pruned_log_files: {file_removed}")

    stop_metrics_collector = threading.Event()

    def _run_edge_metrics_collector() -> None:
        while not stop_metrics_collector.is_set():
            try:
                _, _, _ = _trim_logs_with_policy(
                    repo_root=repo_root,
                    config=cluster_config,
                    node_metrics_path=metrics_path,
                    retention_days=None,
                )
                snapshots = _collect_node_metric_snapshots(cluster_config, timeout=metrics_timeout)
                if snapshots:
                    _append_snapshots(metrics_path, snapshots)
            except Exception as exc:
                # Keep edge serving even when metrics sampling fails transiently.
                typer.echo(f"node_metrics_collector_error: {exc}")
            if stop_metrics_collector.wait(metrics_interval_seconds):
                break

    collector_thread: threading.Thread | None = None
    if collect_node_metrics:
        typer.echo(f"node_metrics_collector: enabled interval_seconds={metrics_interval_seconds}")
        typer.echo(f"node_metrics_log: {metrics_path}")
        collector_thread = threading.Thread(target=_run_edge_metrics_collector, daemon=True)
        collector_thread.start()
    else:
        typer.echo("node_metrics_collector: disabled")

    try:
        serve_edge_proxy(host=host, port=resolved_port, config=edge_proxy_config)
    finally:
        if collector_thread is not None:
            stop_metrics_collector.set()
            collector_thread.join(timeout=2.0)


def _validate_client_config_model_aliases(
    model_catalog: list[EdgeModelCatalogEntry],
    *,
    model: str | None,
    small_model: str | None,
) -> None:
    model_ids = {entry.id for entry in model_catalog}
    for option_name, option_value in (("--model", model), ("--small-model", small_model)):
        if option_value is not None and option_value not in model_ids:
            typer.echo(f"Error: {option_name} alias '{option_value}' is not assigned to an inference node", err=True)
            raise typer.Exit(1)


@edge_app.command("client-config")
def edge_client_config(
    target: str = typer.Argument(..., help="Client config target: opencode or hermes."),
    client_id: str | None = typer.Argument(
        None,
        help="Optional TF edge client id. For Hermes this selects key_env; for OpenCode it can inject the key.",
    ),
    base_url: str | None = typer.Option(
        None,
        "--base-url",
        help="TF edge OpenAI-compatible base URL. Defaults to http://<gateway-host>:<edge-port>/v1.",
    ),
    provider_id: str = typer.Option("thunder-forge", "--provider-id", help="Provider id/name for the client config."),
    provider_name: str = typer.Option("Thunder Forge", "--provider-name", help="OpenCode provider display name."),
    api_key_env: str | None = typer.Option(None, "--api-key-env", help="Env var name used for API key references."),
    model: str | None = typer.Option(None, "--model", help="Optional default TF alias for OpenCode model."),
    small_model: str | None = typer.Option(
        None,
        "--small-model",
        help="Optional default TF alias for OpenCode small_model.",
    ),
    output_format: str = typer.Option("auto", "--format", help="Output format: auto, jsonc, json, or yaml."),
    copy: bool = typer.Option(False, "--copy", help="Copy the generated config to the terminal clipboard via OSC52."),
    output: Path | None = typer.Option(None, "--output", help="Optional file path to write the generated config."),
    inject_api_key: bool = typer.Option(
        False,
        "--inject-api-key",
        help="OpenCode only: write the actual API key instead of an env placeholder.",
    ),
    create_missing_key: bool = typer.Option(
        False,
        "--create-missing-key",
        help="Create a missing TF_USER_<CLIENT> key in .env when a client id is provided.",
    ),
    yes: bool = typer.Option(False, "--yes", "-y", help="Create a missing client key without prompting."),
) -> None:
    """Print client config generated from assigned TF model aliases."""
    config, _ = _load_config()
    model_catalog = _edge_model_catalog_from_config(config)
    if not model_catalog:
        typer.echo("Error: no TF model aliases are assigned to inference nodes", err=True)
        raise typer.Exit(1)

    normalized_target = target.strip().lower()
    normalized_format = output_format.strip().lower()
    if normalized_format == "auto":
        normalized_format = "jsonc" if normalized_target == "opencode" else "yaml"
    resolved_base_url = base_url or _edge_base_url_from_config(config)

    if normalized_target == "opencode":
        _validate_client_config_model_aliases(model_catalog, model=model, small_model=small_model)
        resolved_api_key = _resolve_opencode_api_key(
            client_id=client_id,
            api_key_env=api_key_env,
            inject_api_key=inject_api_key,
            create_missing_key=create_missing_key,
            yes=yes,
        )
        if normalized_format == "jsonc":
            output_text = _opencode_config_jsonc_from_catalog(
                model_catalog=model_catalog,
                provider_id=provider_id,
                provider_name=provider_name,
                base_url=resolved_base_url,
                api_key=resolved_api_key,
                api_key_comment=_opencode_api_key_comment(client_id=client_id, api_key_env=api_key_env),
                model=model,
                small_model=small_model,
            )
        elif normalized_format == "json":
            payload = _opencode_config_from_catalog(
                model_catalog=model_catalog,
                provider_id=provider_id,
                provider_name=provider_name,
                base_url=resolved_base_url,
                api_key=resolved_api_key,
                model=model,
                small_model=small_model,
            )
            output_text = json.dumps(payload, indent=2, ensure_ascii=False) + "\n"
        else:
            typer.echo("Error: --format must be auto, jsonc, or json for opencode", err=True)
            raise typer.Exit(1)
        label = "OpenCode"
    elif normalized_target == "hermes":
        if model or small_model:
            typer.echo("Error: Hermes config snippets do not set top-level model defaults", err=True)
            raise typer.Exit(1)
        if inject_api_key:
            typer.echo("Error: Hermes config uses key_env; do not use --inject-api-key", err=True)
            raise typer.Exit(1)
        if normalized_format not in {"yaml", "yml"}:
            typer.echo("Error: --format must be auto or yaml for hermes", err=True)
            raise typer.Exit(1)
        _ensure_edge_client_key_if_requested(client_id=client_id, create_missing_key=create_missing_key, yes=yes)
        output_text = _hermes_config_yaml_from_catalog(
            model_catalog=model_catalog,
            provider_id=provider_id,
            base_url=resolved_base_url,
            api_key_env=_edge_api_key_env_name(
                client_id=client_id,
                api_key_env=api_key_env,
                default_api_key_env=DEFAULT_HERMES_API_KEY_ENV,
            ),
        )
        label = "Hermes"
    else:
        typer.echo("Error: target must be opencode or hermes", err=True)
        raise typer.Exit(1)

    typer.echo(output_text, nl=False)
    if output is not None:
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(output_text)
        typer.echo(f"wrote {output}", err=True)
    if copy:
        _copy_to_clipboard(output_text)
        typer.echo(f"copied {label} config to clipboard", err=True)

@artifact_app.command("status")
def artifact_status(
    model: str = typer.Option(..., "--model", help="Hugging Face model repo id."),
    node: str = typer.Option(..., "--node", help="Node name to check artifact readiness for (e.g. infer-01)."),
) -> None:
    """Inspect oMLX model-directory readiness without downloading or syncing."""
    config, _ = _load_config()
    runtime_node = _get_runtime_node(config, node)
    node_home_dir = runtime_node.home_dir or f"/Users/{runtime_node.user}"
    cache_omlx_models_dir = cache_omlx_models_dir_from_env()
    cache_target = _first_cache_node(config)
    presence = probe_artifact_presence(
        repo_id=model,
        node_user=runtime_node.user,
        node_host=runtime_node.host,
        node_home_dir=node_home_dir,
        cache_omlx_models_dir=cache_omlx_models_dir,
    )
    if cache_target is not None:
        _, cache_node = cache_target
        if not _is_local_host(cache_node.host):
            identity = build_artifact_identity(model)
            presence = ArtifactPresence(
                cache_omlx_model_dir=_remote_artifact_complete_on_cache(
                    cache_node=cache_node,
                    model_dir_name=identity.model_dir_name,
                ),
                node_omlx_model_dir=presence.node_omlx_model_dir,
            )
    plan = build_artifact_readiness_plan(
        repo_id=model,
        node=node,
        node_home_dir=node_home_dir,
        presence=presence,
        cache_omlx_models_dir=cache_omlx_models_dir,
    )

    typer.echo(f"model: {model}")
    typer.echo(f"model_dir_name: {plan.model_dir_name}")
    typer.echo(f"runtime_model_id: {plan.runtime_model_id}")
    _print_runtime_node_header(node, runtime_node)
    typer.echo(f"cache_omlx_model_dir_path: {plan.cache_omlx_model_dir}")
    typer.echo(f"node_omlx_model_dir_path: {plan.node_omlx_model_dir}")
    typer.echo(f"cache_omlx_model_dir: {'ready' if presence.cache_omlx_model_dir else 'missing_or_incomplete'}")
    typer.echo(f"node_omlx_model_dir: {'ready' if presence.node_omlx_model_dir else 'missing_or_incomplete'}")
    typer.echo(f"ready: {'yes' if plan.ready else 'no'}")
    if plan.actions:
        typer.echo("next_actions:")
        for action in plan.actions:
            typer.echo(f"  - {action.value}")


def _artifact_download_display(plan: ArtifactDownloadPlan, *, remote_cache: bool) -> tuple[str, str]:
    if not remote_cache:
        return plan.destination, plan.command

    remote_cache_root = '${TF_CACHE_OMLX_MODELS_DIR:-$HOME/.omlx/models}'
    display_destination = f"{remote_cache_root}/{plan.model_dir_name}"
    display_command = (
        "python3 remote-helper (cache host) -> "
        "omlx serve --host 127.0.0.1 --port 8020 "
        f"--model-dir {remote_cache_root}; "
        f"POST http://127.0.0.1:8020/admin/api/hf/download repo_id={plan.repo_id} hf_token=$HF_TOKEN"
    )
    return display_destination, display_command


def _print_artifact_download_plan(
    *,
    model: str,
    plan: ArtifactDownloadPlan,
    remote_cache_target: tuple[str, Node] | None,
) -> None:
    display_destination, display_command = _artifact_download_display(
        plan,
        remote_cache=remote_cache_target is not None,
    )

    typer.echo(f"model: {model}")
    typer.echo(f"model_dir_name: {plan.model_dir_name}")
    typer.echo(f"runtime_model_id: {plan.runtime_model_id}")
    typer.echo(f"destination: {display_destination}")
    typer.echo("action: download_to_cache_omlx")
    typer.echo(f"command: {display_command}")


def _run_remote_artifact_download(
    *,
    model: str,
    plan: ArtifactDownloadPlan,
    remote_cache_target: tuple[str, Node],
    timeout: int,
) -> None:
    cache_name, cache_node = remote_cache_target
    typer.echo(f"cache_exec: remote {cache_name} ({cache_node.host})")
    result = ssh_run(
        cache_node.user,
        cache_node.host,
        _remote_artifact_download_command(
            repo_id=model,
            model_dir_name=plan.model_dir_name,
            timeout=timeout,
        ),
        timeout=max(timeout + 120, 300),
        stream=True,
        shell=cache_node.shell,
        node_name=cache_name,
    )
    if result.returncode != 0:
        typer.echo(f"Error: download failed with exit code {result.returncode}", err=True)
        raise typer.Exit(result.returncode)
    typer.echo("status: downloaded")


def _artifact_download_progress_printer() -> Callable[[dict], None]:
    last_progress_bucket: int | None = None
    last_progress_status: str | None = None

    def print_progress(task: dict) -> None:
        nonlocal last_progress_bucket, last_progress_status
        status = str(task.get("status") or "unknown")
        progress = float(task.get("progress") or 0.0)
        progress_bucket = int(progress)
        if status == last_progress_status and progress_bucket == last_progress_bucket:
            return
        last_progress_status = status
        last_progress_bucket = progress_bucket
        downloaded_size = int(task.get("downloaded_size") or 0)
        total_size = int(task.get("total_size") or 0)
        if total_size > 0:
            typer.echo(
                "download_progress: "
                f"{status} {progress:.1f}% "
                f"({_format_bytes(downloaded_size)} / {_format_bytes(total_size)})"
            )
        elif downloaded_size > 0:
            typer.echo(f"download_progress: {status} {_format_bytes(downloaded_size)}")
        else:
            typer.echo(f"download_progress: {status}")

    return print_progress


def _run_local_artifact_download(plan: ArtifactDownloadPlan, *, timeout: int) -> None:
    result = run_artifact_download(
        plan,
        timeout=timeout,
        progress_callback=_artifact_download_progress_printer(),
    )
    if result.returncode != 0:
        typer.echo(f"Error: download failed with exit code {result.returncode}", err=True)
        if result.stderr:
            typer.echo(result.stderr, err=True)
        raise typer.Exit(result.returncode)
    typer.echo("status: downloaded")


@artifact_app.command("download")
def artifact_download(
    model: str = typer.Option(..., "--model", help="Hugging Face model repo id."),
    dry_run: bool = typer.Option(
        True,
        "--dry-run/--apply",
        help="Print download command without executing by default.",
    ),
    timeout: int = typer.Option(7200, "--timeout", help="Timeout in seconds for model download when applying."),
) -> None:
    """Download a model directly into cache role's oMLX model directory."""
    config, _ = _load_config()
    remote_cache_target = _remote_cache_target(config)

    _load_repo_dotenv()
    plan = build_artifact_download_plan(
        repo_id=model,
        cache_omlx_models_dir=cache_omlx_models_dir_from_env(),
    )

    _print_artifact_download_plan(model=model, plan=plan, remote_cache_target=remote_cache_target)
    if dry_run:
        typer.echo("mode: dry-run")
        return

    if remote_cache_target is not None:
        _run_remote_artifact_download(
            model=model,
            plan=plan,
            remote_cache_target=remote_cache_target,
            timeout=timeout,
        )
        return

    _run_local_artifact_download(plan, timeout=timeout)


def _remote_cache_target_for_artifact_workflow(config: ClusterConfig) -> tuple[str, Node] | None:
    if os.environ.get(REMOTE_CACHE_EXEC_ENV) == "1":
        return None
    cache_target = _first_cache_node(config)
    if cache_target is None:
        return None
    cache_name, cache_node = cache_target
    if _is_local_host(cache_node.host):
        return None
    return cache_name, cache_node


def _build_artifact_sync_execution_plan(
    *,
    repo_id: str,
    node: str,
    runtime_node: Node,
    node_home_dir: str,
    cache_omlx_models_dir: str,
    transport_plan: TransportPlan,
    remote_cache_target: tuple[str, Node] | None,
) -> ArtifactSyncExecutionPlan:
    ssh_host_key_alias = runtime_node.host if transport_plan.uses_fabric else None
    if remote_cache_target is not None:
        source_path, destination, command = _remote_cache_sync_command(
            repo_id=repo_id,
            runtime_node=runtime_node,
            node_home_dir=node_home_dir,
            transport_host=transport_plan.resolved_transport_host,
            ssh_host_key_alias=ssh_host_key_alias,
        )
        identity = build_artifact_identity(repo_id)
        return ArtifactSyncExecutionPlan(
            source_path=source_path,
            destination=destination,
            command=command,
            runtime_model_id=identity.runtime_model_id,
            model_dir_name=identity.model_dir_name,
            readiness_actions=[],
        )

    presence = probe_artifact_presence(
        repo_id=repo_id,
        node_user=runtime_node.user,
        node_host=runtime_node.host,
        node_home_dir=node_home_dir,
        cache_omlx_models_dir=cache_omlx_models_dir,
    )
    readiness_plan = build_artifact_readiness_plan(
        repo_id=repo_id,
        node=node,
        node_home_dir=node_home_dir,
        presence=presence,
        cache_omlx_models_dir=cache_omlx_models_dir,
    )
    sync_plan = build_artifact_sync_plan(
        repo_id=repo_id,
        node_user=runtime_node.user,
        node_host=transport_plan.resolved_transport_host,
        node_home_dir=node_home_dir,
        cache_omlx_models_dir=cache_omlx_models_dir,
        ssh_host_key_alias=ssh_host_key_alias,
    )
    return ArtifactSyncExecutionPlan(
        source_path=sync_plan.source_path,
        destination=sync_plan.destination,
        command=sync_plan.command,
        runtime_model_id=sync_plan.runtime_model_id,
        model_dir_name=sync_plan.model_dir_name,
        readiness_actions=readiness_plan.actions,
        sync_plan=sync_plan,
    )


def _print_artifact_sync_execution_plan(
    *,
    node: str,
    runtime_node: Node,
    repo_id: str,
    plan: ArtifactSyncExecutionPlan,
    transport_plan: TransportPlan,
) -> None:
    typer.echo(f"model: {repo_id}")
    typer.echo(f"model_dir_name: {plan.model_dir_name}")
    typer.echo(f"runtime_model_id: {plan.runtime_model_id}")
    _print_runtime_node_header(node, runtime_node)
    typer.echo("source: cache")
    typer.echo(f"transport_host: {transport_plan.transport_host}")
    if transport_plan.resolved_transport_host != transport_plan.transport_host:
        typer.echo(f"resolved_transport_host: {transport_plan.resolved_transport_host}")
    if transport_plan.fabric_fallback:
        typer.echo(f"fabric_fallback: {transport_plan.fabric_fallback}")
    typer.echo(f"source_path: {plan.source_path}")
    typer.echo(f"destination: {plan.destination}")
    typer.echo("action: sync_to_node_omlx")
    typer.echo(f"command: {plan.command}")


def _local_artifact_sync_needed(plan: ArtifactSyncExecutionPlan) -> bool:
    if ArtifactReadinessAction.DOWNLOAD_TO_CACHE_OMLX in plan.readiness_actions:
        typer.echo(
            "Error: cache oMLX model directory is missing or incomplete; "
            "download the model to cache oMLX models first",
            err=True,
        )
        raise typer.Exit(1)
    if ArtifactReadinessAction.SYNC_TO_NODE_OMLX not in plan.readiness_actions:
        typer.echo("status: sync not needed")
        return False
    return True


def _run_artifact_sync_execution(
    *,
    plan: ArtifactSyncExecutionPlan,
    remote_cache_target: tuple[str, Node] | None,
    timeout: int,
) -> None:
    if remote_cache_target is None:
        if plan.sync_plan is None:
            msg = "local artifact sync plan is missing"
            raise RuntimeError(msg)
        result = run_artifact_sync(plan.sync_plan, timeout=timeout)
    else:
        cache_name, cache_node = remote_cache_target
        result = ssh_run(
            cache_node.user,
            cache_node.host,
            plan.command,
            timeout=timeout,
            stream=True,
            shell=cache_node.shell,
            node_name=cache_name,
        )
    if result.returncode != 0:
        typer.echo(f"Error: sync failed with exit code {result.returncode}", err=True)
        raise typer.Exit(result.returncode)
    typer.echo("status: synced")


def _run_artifact_sync_workflow(
    *,
    config: ClusterConfig,
    node: str,
    model: str | None,
    dry_run: bool,
    transport: str,
    management: bool,
    timeout: int,
) -> None:
    remote_cache_target = _remote_cache_target_for_artifact_workflow(config)
    runtime_node = _get_runtime_node(config, node)
    node_home_dir = runtime_node.home_dir or f"/Users/{runtime_node.user}"
    cache_omlx_models_dir = cache_omlx_models_dir_from_env()
    repo_ids = [model] if model else []
    if not repo_ids:
        repo_ids = _assigned_repo_ids_for_node(config, node, runtime_node)

    requested_transport = "management" if management else transport
    transport_plan = _resolve_transport_plan_for_sync(
        requested_transport=requested_transport,
        runtime_node=runtime_node,
        remote_cache_target=remote_cache_target,
        timeout=timeout,
    )
    if not transport_plan.ok:
        typer.echo(f"Error: {transport_plan.error}", err=True)
        raise typer.Exit(1)

    if len(repo_ids) > 1:
        typer.echo("sync_scope: node")
        typer.echo(f"models: {len(repo_ids)}")

    if remote_cache_target is not None and not dry_run:
        cache_name, cache_node = remote_cache_target
        typer.echo(f"cache_exec: remote {cache_name} ({cache_node.host})")

    for index, repo_id in enumerate(repo_ids, start=1):
        if len(repo_ids) > 1:
            typer.echo("")
            typer.echo(f"== Model {index}/{len(repo_ids)} ==")
        sync_execution_plan = _build_artifact_sync_execution_plan(
            repo_id=repo_id,
            node=node,
            runtime_node=runtime_node,
            node_home_dir=node_home_dir,
            cache_omlx_models_dir=cache_omlx_models_dir,
            transport_plan=transport_plan,
            remote_cache_target=remote_cache_target,
        )
        _print_artifact_sync_execution_plan(
            node=node,
            runtime_node=runtime_node,
            repo_id=repo_id,
            plan=sync_execution_plan,
            transport_plan=transport_plan,
        )

        if remote_cache_target is None and not _local_artifact_sync_needed(sync_execution_plan):
            continue
        if dry_run:
            typer.echo("mode: dry-run")
            continue

        _run_artifact_sync_execution(
            plan=sync_execution_plan,
            remote_cache_target=remote_cache_target,
            timeout=timeout,
        )

    if len(repo_ids) > 1:
        status = "node sync dry-run complete" if dry_run else "node sync complete"
        typer.echo(f"status: {status}")


@artifact_app.command("sync")
def artifact_sync(
    model: str | None = typer.Option(
        None,
        "--model",
        help="Optional Hugging Face model repo id. Omit to sync every model assigned to the node.",
    ),
    node: str = typer.Option(..., "--node", help="Node name to sync artifact to (e.g. infer-01)."),
    dry_run: bool = typer.Option(True, "--dry-run/--apply", help="Print sync command without executing by default."),
    transport: str | None = typer.Option(
        None,
        "--transport",
        help="Transport selection: auto, fabric, or management. Defaults to operations.sync.transport.",
    ),
    management: bool = typer.Option(
        False,
        "--management",
        help="Force management host even when fabric_host probing is enabled.",
    ),
    timeout: int | None = typer.Option(None, "--timeout", help="Timeout in seconds for rsync when applying."),
) -> None:
    """Sync oMLX model directories from cache to a node."""
    config, _ = _load_config()
    _run_artifact_sync_workflow(
        config=config,
        node=node,
        model=model,
        dry_run=dry_run,
        transport=transport or config.operations.sync.transport,
        management=management,
        timeout=timeout or config.operations.sync.timeout,
    )


@runtime_app.command("start")
def runtime_start(
    node: str = typer.Option(..., "--node", help="Node name to start runtime on (e.g. infer-01)."),
    dry_run: bool = typer.Option(True, "--dry-run/--apply", help="Print command without executing by default."),
    timeout: int = typer.Option(30, "--timeout", help="Timeout in seconds for the SSH start command."),
) -> None:
    """Start or dry-run a node-level runtime such as oMLX."""
    from thunder_forge.cluster.omlx import build_omlx_serve_command

    config, _ = _load_config()
    runtime_node = _get_runtime_node(config, node)
    if runtime_node.home_dir is None:
        runtime_node.home_dir = f"/Users/{runtime_node.user}"

    command = build_omlx_serve_command(runtime_node)
    _print_runtime_node_header(node, runtime_node)
    typer.echo(f"command: {command}")

    if dry_run:
        typer.echo("mode: dry-run")
        return

    base_url = f"http://{runtime_node.host}:{_runtime(runtime_node).port}"
    health = check_omlx_health(base_url)
    if health.health_ok and health.models_ok:
        typer.echo(f"base_url: {health.base_url}")
        typer.echo("status: already running")
        return

    result = run_omlx_runtime_start(runtime_node, timeout=timeout)
    if result.returncode != 0:
        typer.echo(f"Error: runtime start failed with exit code {result.returncode}", err=True)
        if result.stderr:
            typer.echo(result.stderr.strip(), err=True)
        raise typer.Exit(result.returncode)
    if result.pid:
        typer.echo(f"pid: {result.pid}")
    typer.echo("status: started")


@runtime_app.command("setup-daemon")
def runtime_setup_daemon(
    node: str = typer.Option(..., "--node", help="Node name to set up for system oMLX daemon management."),
    admin_user: str = typer.Option(
        "",
        "--admin-user",
        help="Admin account that can run sudo on the node. Defaults to nodes.<node>.admin_user, then the node user.",
    ),
    via_su: bool = typer.Option(
        False,
        "--via-su/--direct-sudo",
        help="SSH as the node user, then run the setup script through su - ADMIN_USER.",
    ),
    script_path: str = typer.Option(
        "",
        "--script-path",
        help="Remote path for the generated setup script. Defaults to /tmp/thunder-forge-setup-<label>.sh.",
    ),
    dry_run: bool = typer.Option(
        True,
        "--dry-run/--apply",
        help="Print the setup script and remote commands without executing by default.",
    ),
    timeout: int = typer.Option(300, "--timeout", help="Timeout in seconds for remote setup when applying."),
) -> None:
    """Set up a node for sudo-backed system oMLX daemon management."""
    config, _ = _load_config()
    runtime_node = _get_runtime_node(config, node)
    if runtime_node.home_dir is None:
        runtime_node.home_dir = f"/Users/{runtime_node.user}"
    resolved_admin_user = admin_user or runtime_node.admin_user
    if via_su and not resolved_admin_user:
        typer.echo("Error: --via-su requires --admin-user or nodes.<node>.admin_user", err=True)
        raise typer.Exit(1)

    if not dry_run:
        if via_su:
            typer.echo(
                f"[{runtime_node.host}] auth: ssh={runtime_node.user}@{runtime_node.host} "
                f"method=su admin={resolved_admin_user} reason=install oMLX LaunchDaemon"
            )
        else:
            typer.echo(
                f"[{runtime_node.host}] auth: ssh={runtime_node.user}@{runtime_node.host} "
                f"method=sudo user={runtime_node.user} reason=install oMLX LaunchDaemon"
            )

    result = run_omlx_daemon_setup(
        runtime_node,
        admin_user=resolved_admin_user or None,
        via_su=via_su,
        script_path=script_path or None,
        apply=not dry_run,
        timeout=timeout,
    )

    typer.echo("")
    _print_runtime_node_header(node, runtime_node)
    typer.echo("manager: daemon")
    typer.echo(f"admin_user: {result.admin_user}")
    typer.echo(f"ssh_user: {result.ssh_user}")
    typer.echo(f"via_su: {'yes' if result.via_su else 'no'}")
    typer.echo(f"plist_path: {result.plist_path}")
    typer.echo(f"staging_plist_path: {result.staging_plist_path}")
    typer.echo(f"sudoers_path: {result.sudoers_path}")
    typer.echo(f"script_path: {result.script_path}")
    typer.echo(f"label: {result.label}")
    typer.echo(f"mode: {'dry-run' if dry_run else 'apply'}")
    if dry_run and result.script_content:
        typer.echo("script:")
        for line in result.script_content.splitlines():
            typer.echo(f"  {line}")
    if result.commands:
        typer.echo("commands:")
        for cmd in result.commands:
            typer.echo(f"  - {cmd}")
    if result.applied:
        typer.echo(f"sudoers_verified: {'yes' if result.sudoers_verified else 'no'}")
        typer.echo(f"service_label_verified: {'yes' if result.service_label_verified else 'no'}")
        typer.echo(f"health_ok: {'yes' if result.health_ok else 'no'}")
        if result.ok:
            typer.echo("status: daemon setup complete")
    for error in result.errors:
        typer.echo(f"Error: {error}", err=True)
    if result.applied and not result.ok:
        raise typer.Exit(1)


@runtime_app.command("restart")
def runtime_restart(
    node: str = typer.Option(..., "--node", help="Node name to restart runtime on (e.g. infer-01)."),
    manager: str = typer.Option(
        "process",
        "--manager",
        help=(
            "Restart manager: process for no-GUI/no-sudo SSH control, "
            "daemon for sudo-backed system launchd, or launchd for a user LaunchAgent."
        ),
    ),
    dry_run: bool = typer.Option(
        True,
        "--dry-run/--apply",
        help="Print plist and commands without executing by default.",
    ),
    timeout: int = typer.Option(60, "--timeout", help="Timeout in seconds for each SSH command."),
) -> None:
    """Restart a node-level runtime on a remote node."""
    config, _ = _load_config()
    runtime_node = _get_runtime_node(config, node)
    if runtime_node.home_dir is None:
        runtime_node.home_dir = f"/Users/{runtime_node.user}"

    _print_runtime_node_header(node, runtime_node)
    normalized_manager = manager.lower()
    typer.echo(f"manager: {normalized_manager}")

    if normalized_manager == "process":
        process_result = run_omlx_process_restart(runtime_node, apply=not dry_run, timeout=timeout)
        typer.echo(f"command: {process_result.command}")
        typer.echo(f"pid_path: {process_result.pid_path}")
        typer.echo(f"stdout_log: {process_result.stdout_log}")
        typer.echo(f"stderr_log: {process_result.stderr_log}")
        typer.echo(f"mode: {'dry-run' if dry_run else 'apply'}")
        if process_result.commands:
            typer.echo("commands:")
            for cmd in process_result.commands:
                typer.echo(f"  - {cmd}")
        if process_result.applied:
            if process_result.pid:
                typer.echo(f"pid: {process_result.pid}")
            typer.echo(f"health_ok: {'yes' if process_result.health_ok else 'no'}")
            if process_result.ok:
                typer.echo("status: restarted")
        for error in process_result.errors:
            typer.echo(f"Error: {error}", err=True)
        if not process_result.ok and process_result.applied:
            raise typer.Exit(1)
        return

    if normalized_manager == "daemon":
        result = run_omlx_daemon_restart(runtime_node, apply=not dry_run, timeout=timeout)
        typer.echo(f"plist_path: {result.plist_path}")
        if result.staging_plist_path:
            typer.echo(f"staging_plist_path: {result.staging_plist_path}")
        typer.echo(f"label: {result.label}")
        typer.echo(f"mode: {'dry-run' if dry_run else 'apply'}")
        if result.plist_content:
            typer.echo("plist:")
            for line in result.plist_content.splitlines():
                typer.echo(f"  {line}")
        if result.commands:
            typer.echo("commands:")
            for cmd in result.commands:
                typer.echo(f"  - {cmd}")
        if result.applied:
            typer.echo(f"service_label_verified: {'yes' if result.service_label_verified else 'no'}")
            typer.echo(f"health_ok: {'yes' if result.health_ok else 'no'}")
            if result.ok:
                typer.echo("status: restarted")
        for error in result.errors:
            typer.echo(f"Error: {error}", err=True)
        if not result.ok and result.applied:
            raise typer.Exit(1)
        return

    if normalized_manager != "launchd":
        typer.echo("Error: --manager must be 'process', 'daemon', or 'launchd'", err=True)
        raise typer.Exit(1)

    result = run_omlx_runtime_restart(runtime_node, apply=not dry_run, timeout=timeout)
    typer.echo(f"plist_path: {result.plist_path}")
    typer.echo(f"label: {result.label}")
    typer.echo(f"mode: {'dry-run' if dry_run else 'apply'}")
    if result.plist_content:
        typer.echo("plist:")
        for line in result.plist_content.splitlines():
            typer.echo(f"  {line}")
    if result.commands:
        typer.echo("commands:")
        for cmd in result.commands:
            typer.echo(f"  - {cmd}")
    if result.applied:
        typer.echo(f"service_label_verified: {'yes' if result.service_label_verified else 'no'}")
        typer.echo(f"health_ok: {'yes' if result.health_ok else 'no'}")
        if result.ok:
            typer.echo("status: restarted")
    for error in result.errors:
        typer.echo(f"Error: {error}", err=True)
    if not result.ok and result.applied:
        raise typer.Exit(1)


@runtime_app.command("status")
def runtime_status(
    node: str = typer.Option(..., "--node", help="Node name to check runtime status for (e.g. infer-01)."),
) -> None:
    """Probe a node-level oMLX runtime directly."""
    config, _ = _load_config()
    runtime_node = _get_runtime_node(config, node)
    base_url = f"http://{runtime_node.host}:{_runtime(runtime_node).port}"
    result = check_omlx_health(base_url)

    _print_runtime_node_header(node, runtime_node)
    typer.echo(f"base_url: {result.base_url}")
    typer.echo(f"health: {'ok' if result.health_ok else 'fail'}")
    typer.echo(f"models: {'ok' if result.models_ok else 'fail'}")
    if result.status_ok is not None:
        typer.echo(f"status: {'ok' if result.status_ok else 'unavailable'}")
    if result.models:
        typer.echo("served_models:")
        for model_id in result.models:
            typer.echo(f"  - {model_id}")
    for error in result.errors:
        typer.echo(f"Error: {error}", err=True)

    if not result.health_ok or not result.models_ok:
        raise typer.Exit(1)


@runtime_app.command("smoke")
def runtime_smoke(
    node: str = typer.Option(..., "--node", help="Node name to smoke-test runtime for (e.g. infer-01)."),
    model: str = typer.Option(..., "--model", help="oMLX model id to test, usually the model directory name."),
    prompt: str = typer.Option("Reply with one short word: pong.", "--prompt", help="Short smoke-test prompt."),
    timeout: float = typer.Option(30.0, "--timeout", help="HTTP timeout in seconds."),
) -> None:
    """Run a direct oMLX chat smoke test."""
    config, _ = _load_config()
    runtime_node = _get_runtime_node(config, node)
    base_url = f"http://{runtime_node.host}:{_runtime(runtime_node).port}"
    result = smoke_omlx_chat(base_url, model=model, prompt=prompt, timeout=timeout)

    _print_runtime_node_header(node, runtime_node)
    typer.echo(f"base_url: {result.base_url}")
    typer.echo(f"model: {result.model}")
    typer.echo(f"health: {'ok' if result.health_ok else 'fail'}")
    typer.echo(f"models: {'ok' if result.models_ok else 'fail'}")
    typer.echo(f"model_visible: {'yes' if result.model_visible else 'no'}")
    typer.echo(f"chat: {'ok' if result.chat_ok else 'fail'}")
    typer.echo(f"latency_ms: {result.latency_ms}")
    if result.answer:
        typer.echo(f"answer: {result.answer}")
    if result.models:
        typer.echo("served_models:")
        for model_id in result.models:
            typer.echo(f"  - {model_id}")
    for error in result.errors:
        typer.echo(f"Error: {error}", err=True)

    if not result.ok:
        raise typer.Exit(1)


@runtime_app.command("install")
def runtime_install(
    node: str = typer.Option(..., "--node", help="Node name to install launchd daemon for (e.g. infer-01)."),
    dry_run: bool = typer.Option(
        True,
        "--dry-run/--apply",
        help="Print plist and commands without executing by default.",
    ),
    timeout: int = typer.Option(60, "--timeout", help="Timeout in seconds for each SSH command."),
) -> None:
    """Install or update a node-level oMLX launchd daemon."""
    config, _ = _load_config()
    runtime_node = _get_runtime_node(config, node)
    if runtime_node.home_dir is None:
        runtime_node.home_dir = f"/Users/{runtime_node.user}"

    result = run_omlx_install(runtime_node, apply=not dry_run, timeout=timeout)

    _print_runtime_node_header(node, runtime_node)
    typer.echo(f"plist_path: {result.plist_path}")
    typer.echo(f"label: {result.label}")
    typer.echo(f"mode: {'dry-run' if dry_run else 'apply'}")
    if result.plist_content:
        typer.echo("plist:")
        for line in result.plist_content.splitlines():
            typer.echo(f"  {line}")
    if result.commands:
        typer.echo("commands:")
        for cmd in result.commands:
            typer.echo(f"  - {cmd}")
    if result.applied:
        typer.echo(f"service_label_verified: {'yes' if result.service_label_verified else 'no'}")
        typer.echo(f"health_ok: {'yes' if result.health_ok else 'no'}")
    for error in result.errors:
        typer.echo(f"Error: {error}", err=True)
    if not result.ok and result.applied:
        raise typer.Exit(1)


@app.command("generate-olla-config")
def generate_olla_config_cmd(
    port: int | None = typer.Option(
        None,
        "--port",
        help=f"Olla service port. Defaults to tfconfig services.olla.port or {DEFAULT_OLLA_PORT}.",
    ),
) -> None:
    """Generate olla-config.yaml from the TF cluster config."""
    config, repo_root = _load_config()
    config_path = write_generated_olla_config(config, repo_root=repo_root, port=port)
    typer.echo(f"generated: {config_path}")
