"""Thunder Forge CLI — cluster management commands."""

import os
from pathlib import Path
from typing import cast

import typer

from thunder_forge.cluster.artifacts import (
    ArtifactReadinessAction,
    build_artifact_download_plan,
    build_artifact_readiness_plan,
    build_artifact_sync_plan,
    probe_artifact_presence,
    run_artifact_download,
    run_artifact_sync,
)
from thunder_forge.cluster.config import ClusterConfig, Node, NodeRuntime
from thunder_forge.cluster.edge import EdgeClient, EdgeProxyConfig, serve_edge_proxy, smoke_edge_contract
from thunder_forge.cluster.fabric import discover_link_local_fabric_host, resolve_fabric_host
from thunder_forge.cluster.olla import dev_smoke_olla, smoke_olla_router
from thunder_forge.cluster.omlx import check_omlx_health, run_omlx_runtime_start, smoke_omlx_chat

app = typer.Typer(
    name="thunder-forge",
    help="CLI for managing a local MLX inference cluster.",
    no_args_is_help=True,
)
runtime_app = typer.Typer(help="Manage node-level runtimes such as oMLX.", no_args_is_help=True)
artifact_app = typer.Typer(help="Inspect model artifact readiness for oMLX nodes.", no_args_is_help=True)
edge_app = typer.Typer(help="Smoke-test and operate the minimal TF edge.", no_args_is_help=True)
olla_app = typer.Typer(help="Smoke-test the generated Olla router layer.", no_args_is_help=True)
app.add_typer(runtime_app, name="runtime")
app.add_typer(artifact_app, name="artifact")
app.add_typer(edge_app, name="edge")
app.add_typer(olla_app, name="olla")


def _load_config() -> tuple[ClusterConfig, Path]:
    """Load cluster config from node-assignments.yaml. Returns (ClusterConfig, repo_root Path)."""
    from thunder_forge.cluster.config import find_repo_root, load_cluster_config

    repo_root = find_repo_root()
    assignments_path = repo_root / "configs" / "node-assignments.yaml"
    if not assignments_path.exists():
        typer.echo(f"Error: {assignments_path} not found", err=True)
        raise typer.Exit(1)
    return load_cluster_config(assignments_path), repo_root


def _run_preflight(config: object, *, target_node: str | None = None) -> None:
    """Run pre-flight checks. Exit on failure."""
    from thunder_forge.cluster.preflight import print_preflight_result, run_preflight

    errors = run_preflight(config, target_node=target_node)
    print_preflight_result(errors, config)
    if errors:
        raise typer.Exit(1)


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


def _runtime(runtime_node: Node) -> NodeRuntime:
    return cast(NodeRuntime, runtime_node.runtime)


def _print_runtime_node_header(node: str, runtime_node: Node) -> None:
    typer.echo(f"node: {node}")
    typer.echo(f"runtime: {_runtime(runtime_node).type}")
    typer.echo(f"management_host: {runtime_node.host}")
    if runtime_node.fabric_host:
        typer.echo(f"fabric_host: {runtime_node.fabric_host}")


@edge_app.command("smoke")
def edge_smoke(
    base_url: str = typer.Option(..., "--base-url", help="TF edge base URL, for example http://127.0.0.1:40116."),
    api_key_env: str = typer.Option(..., "--api-key-env", help="Environment variable containing the edge API key."),
    model: str = typer.Option(..., "--model", help="Model or alias to use for the chat smoke."),
    prompt: str = typer.Option("Reply with one short word: pong.", "--prompt", help="Short smoke-test prompt."),
    timeout: float = typer.Option(30.0, "--timeout", help="HTTP timeout in seconds."),
) -> None:
    """Run a black-box smoke test against a running TF edge."""
    api_key = os.environ.get(api_key_env)
    if not api_key:
        typer.echo(f"Error: {api_key_env} is not set", err=True)
        raise typer.Exit(1)

    result = smoke_edge_contract(base_url=base_url, api_key=api_key, model=model, prompt=prompt, timeout=timeout)
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


@olla_app.command("smoke")
def olla_smoke(
    base_url: str = typer.Option(..., "--base-url", help="Olla base URL, for example http://127.0.0.1:40115."),
    model: str = typer.Option(..., "--model", help="Backend runtime model id to verify."),
    alias: str = typer.Option(..., "--alias", help="Public alias routed by Olla to the backend model."),
    prompt: str = typer.Option("Reply with one short word: pong.", "--prompt", help="Short smoke-test prompt."),
    timeout: float = typer.Option(30.0, "--timeout", help="HTTP timeout in seconds."),
) -> None:
    """Run a black-box smoke test against a running Olla router."""
    result = smoke_olla_router(base_url=base_url, model=model, alias=alias, prompt=prompt, timeout=timeout)
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
    for error in result.errors:
        typer.echo(f"Error: {error}", err=True)
    if not result.ok:
        raise typer.Exit(1)


@olla_app.command("dev-smoke")
def olla_dev_smoke(
    binary: str = typer.Option(..., "--binary", help="Path to the Olla binary."),
    model: str = typer.Option(..., "--model", help="Backend runtime model id to verify."),
    alias: str = typer.Option(..., "--alias", help="Public alias routed by Olla to the backend model."),
    prompt: str = typer.Option("Reply with one short word: pong.", "--prompt", help="Short smoke-test prompt."),
    timeout: float = typer.Option(30.0, "--timeout", help="HTTP timeout in seconds for smoke checks."),
) -> None:
    """Generate Olla config, spawn Olla, smoke, teardown. Single-command dev workflow."""
    result = dev_smoke_olla(binary=binary, model=model, alias=alias, prompt=prompt, smoke_timeout=timeout)
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
    port: int = typer.Option(40116, "--port", help="Port for the TF edge listener."),
    olla_base_url: str = typer.Option(..., "--olla-base-url", help="Olla base URL, e.g. http://127.0.0.1:40115."),
    api_key_env: str = typer.Option(..., "--api-key-env", help="Environment variable containing the edge API key."),
    client_id: str = typer.Option("shag-dev", "--client-id", help="Client identity mapped to the API key."),
) -> None:
    """Run the minimal non-streaming TF edge proxy."""
    api_key = os.environ.get(api_key_env)
    if not api_key:
        typer.echo(f"Error: {api_key_env} is not set", err=True)
        raise typer.Exit(1)

    def log_sink(line: str) -> None:
        typer.echo(line)

    config = EdgeProxyConfig(
        olla_base_url=olla_base_url,
        clients_by_key={api_key: EdgeClient(client_id=client_id)},
        access_log_sink=log_sink,
    )
    typer.echo(f"serving_edge: http://{host}:{port}")
    typer.echo(f"olla_base_url: {olla_base_url}")
    typer.echo(f"client_id: {client_id}")
    serve_edge_proxy(host=host, port=port, config=config)


@artifact_app.command("status")
def artifact_status(
    model: str = typer.Option(..., "--model", help="Hugging Face model repo id."),
    node: str = typer.Option(..., "--node", help="Node name to check artifact readiness for (e.g. msm3)."),
) -> None:
    """Inspect oMLX model-directory readiness without downloading or syncing."""
    config, _ = _load_config()
    runtime_node = _get_runtime_node(config, node)
    node_home_dir = runtime_node.home_dir or f"/Users/{runtime_node.user}"
    presence = probe_artifact_presence(
        repo_id=model,
        node_host=runtime_node.host,
        node_home_dir=node_home_dir,
    )
    plan = build_artifact_readiness_plan(
        repo_id=model,
        node=node,
        node_home_dir=node_home_dir,
        presence=presence,
    )

    typer.echo(f"model: {model}")
    _print_runtime_node_header(node, runtime_node)
    typer.echo(f"studio_omlx_model_dir_path: {plan.studio_omlx_model_dir}")
    typer.echo(f"node_omlx_model_dir_path: {plan.node_omlx_model_dir}")
    typer.echo(f"studio_omlx_model_dir: {'present' if presence.studio_omlx_model_dir else 'missing'}")
    typer.echo(f"node_omlx_model_dir: {'present' if presence.node_omlx_model_dir else 'missing'}")
    typer.echo(f"ready: {'yes' if plan.ready else 'no'}")
    if plan.actions:
        typer.echo("next_actions:")
        for action in plan.actions:
            typer.echo(f"  - {action.value}")


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
    """Download a model directly into studio's oMLX model directory."""
    plan = build_artifact_download_plan(repo_id=model)

    typer.echo(f"model: {model}")
    typer.echo(f"model_dir_name: {plan.model_dir_name}")
    typer.echo(f"destination: {plan.destination}")
    typer.echo("action: download_to_studio_omlx")
    typer.echo(f"command: {plan.command}")

    if dry_run:
        typer.echo("mode: dry-run")
        return

    result = run_artifact_download(plan, timeout=timeout)
    if result.returncode != 0:
        typer.echo(f"Error: download failed with exit code {result.returncode}", err=True)
        raise typer.Exit(result.returncode)
    typer.echo("status: downloaded")


@artifact_app.command("sync")
def artifact_sync(
    model: str = typer.Option(..., "--model", help="Hugging Face model repo id."),
    node: str = typer.Option(..., "--node", help="Node name to sync artifact to (e.g. msm3)."),
    dry_run: bool = typer.Option(True, "--dry-run/--apply", help="Print sync command without executing by default."),
    transport: str = typer.Option(
        "auto",
        "--transport",
        help="Transport selection: auto, fabric, or management. Auto prefers configured reachable fabric.",
    ),
    management: bool = typer.Option(
        False,
        "--management",
        help="Force management host even when fabric_host is configured.",
    ),
    timeout: int = typer.Option(7200, "--timeout", help="Timeout in seconds for rsync when applying."),
) -> None:
    """Sync an oMLX model directory from studio to a node."""
    config, _ = _load_config()
    runtime_node = _get_runtime_node(config, node)
    node_home_dir = runtime_node.home_dir or f"/Users/{runtime_node.user}"
    requested_transport = "management" if management else transport
    if requested_transport not in {"auto", "fabric", "management"}:
        typer.echo("Error: --transport must be one of: auto, fabric, management", err=True)
        raise typer.Exit(1)

    transport_host = runtime_node.host
    resolved_transport_host = runtime_node.host
    fabric_fallback = ""
    if requested_transport in {"auto", "fabric"} and runtime_node.fabric_host:
        resolved_fabric_host = resolve_fabric_host(runtime_node.fabric_host)
        if resolved_fabric_host is None:
            resolved_fabric_host = discover_link_local_fabric_host(
                management_host=runtime_node.host,
                node_user=runtime_node.user,
            )
        if resolved_fabric_host is not None:
            transport_host = runtime_node.fabric_host
            resolved_transport_host = resolved_fabric_host
        elif requested_transport == "fabric":
            typer.echo(f"Error: fabric_host '{runtime_node.fabric_host}' is not reachable", err=True)
            raise typer.Exit(1)
        else:
            fabric_fallback = f"{runtime_node.fabric_host} unresolved"
    elif requested_transport == "fabric":
        typer.echo(f"Error: node '{node}' has no fabric_host configured", err=True)
        raise typer.Exit(1)
    presence = probe_artifact_presence(
        repo_id=model,
        node_host=runtime_node.host,
        node_home_dir=node_home_dir,
    )
    readiness_plan = build_artifact_readiness_plan(
        repo_id=model,
        node=node,
        node_home_dir=node_home_dir,
        presence=presence,
    )
    sync_plan = build_artifact_sync_plan(
        repo_id=model,
        node_user=runtime_node.user,
        node_host=resolved_transport_host,
        node_home_dir=node_home_dir,
    )

    typer.echo(f"model: {model}")
    _print_runtime_node_header(node, runtime_node)
    typer.echo("source: studio")
    typer.echo(f"transport_host: {transport_host}")
    if resolved_transport_host != transport_host:
        typer.echo(f"resolved_transport_host: {resolved_transport_host}")
    if fabric_fallback:
        typer.echo(f"fabric_fallback: {fabric_fallback}")
    typer.echo(f"source_path: {sync_plan.source_path}")
    typer.echo(f"destination: {sync_plan.destination}")
    typer.echo("action: sync_to_node_omlx")
    typer.echo(f"command: {sync_plan.command}")

    if ArtifactReadinessAction.DOWNLOAD_TO_STUDIO_OMLX in readiness_plan.actions:
        typer.echo(
            "Error: studio oMLX model directory is missing; download the model to studio oMLX models first",
            err=True,
        )
        raise typer.Exit(1)
    if ArtifactReadinessAction.SYNC_TO_NODE_OMLX not in readiness_plan.actions:
        typer.echo("status: sync not needed")
        return
    if dry_run:
        typer.echo("mode: dry-run")
        return

    result = run_artifact_sync(sync_plan, timeout=timeout)
    if result.returncode != 0:
        typer.echo(f"Error: sync failed with exit code {result.returncode}", err=True)
        raise typer.Exit(result.returncode)
    typer.echo("status: synced")


@runtime_app.command("start")
def runtime_start(
    node: str = typer.Option(..., "--node", help="Node name to start runtime on (e.g. msm3)."),
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


@runtime_app.command("status")
def runtime_status(
    node: str = typer.Option(..., "--node", help="Node name to check runtime status for (e.g. msm3)."),
) -> None:
    """Probe a node-level oMLX runtime directly, without LiteLLM."""
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
    node: str = typer.Option(..., "--node", help="Node name to smoke-test runtime for (e.g. msm3)."),
    model: str = typer.Option(..., "--model", help="oMLX model id to test, usually the model directory name."),
    prompt: str = typer.Option("Reply with one short word: pong.", "--prompt", help="Short smoke-test prompt."),
    timeout: float = typer.Option(30.0, "--timeout", help="HTTP timeout in seconds."),
) -> None:
    """Run a direct oMLX chat smoke test, without LiteLLM."""
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


@app.command()
def generate_config(
    check: bool = typer.Option(
        False, "--check", help="Compare generated config with committed file, exit 1 on mismatch."
    ),
) -> None:
    """Generate litellm-config.yaml from node-assignments.yaml."""
    from thunder_forge.cluster.config import (
        OS_OVERHEAD_GB,
        check_config_sync,
        generate_litellm_config,
        validate_memory,
    )

    config, repo_root = _load_config()
    config_path = repo_root / "configs" / "litellm-config.yaml"

    typer.echo("Validating memory budgets...")
    errors = validate_memory(config)
    for node_name, slots in sorted(config.assignments.items()):
        node = config.nodes[node_name]
        parts = []
        total = OS_OVERHEAD_GB
        for slot in slots:
            model = config.models[slot.model]
            weight = model.ram_gb if model.ram_gb is not None else model.disk_gb
            kv = model.kv_per_32k_gb
            total += weight + kv
            parts.append(f"{slot.model}({weight}+{kv}kv)")
        budget = " + ".join(parts) + f" + {OS_OVERHEAD_GB} OS = {total:.1f} GB / {node.ram_gb} GB"
        status = "✓" if total <= node.ram_gb else "✗ EXCEEDS"
        typer.echo(f"  {node_name}: {budget} {status}")

    if errors:
        for err in errors:
            typer.echo(f"Error: {err}", err=True)
        raise typer.Exit(1)

    if check:
        if check_config_sync(config, config_path):
            typer.echo("✓ Config is in sync with assignments")
            raise typer.Exit(0)
        else:
            typer.echo("✗ Config mismatch — run 'thunder-forge generate-config' to update", err=True)
            raise typer.Exit(1)

    content = generate_litellm_config(config)
    config_path.write_text(content)
    typer.echo(f"✓ Generated {config_path}")


@app.command("generate-olla-config")
def generate_olla_config_cmd() -> None:
    """Generate olla-config.yaml from node-assignments.yaml."""
    from thunder_forge.cluster.config import generate_olla_config

    config, repo_root = _load_config()
    config_path = repo_root / "configs" / "olla-config.yaml"
    content = generate_olla_config(config)
    config_path.write_text(content)
    typer.echo(f"✓ Generated {config_path}")


@app.command()
def ensure_models(
    dry_run: bool = typer.Option(False, "--dry-run", help="Print what would be downloaded without doing it."),
    skip_preflight: bool = typer.Option(False, "--skip-preflight", help="Skip pre-flight node checks."),
    download_timeout: int = typer.Option(
        7200, "--download-timeout", help="Timeout in seconds for each model download."
    ),
) -> None:
    """Download and sync models to assigned nodes."""
    from thunder_forge.cluster.models import run_ensure_models

    config, _ = _load_config()

    if not skip_preflight:
        _run_preflight(config)

    success = run_ensure_models(config, dry_run=dry_run, download_timeout=download_timeout)
    raise typer.Exit(0 if success else 1)


@app.command()
def deploy(
    node: str | None = typer.Option(None, "--node", help="Deploy to a single node (e.g. msm1)."),
    skip_models: bool = typer.Option(False, "--skip-models", help="Skip model download/sync step."),
    dry_run: bool = typer.Option(False, "--dry-run", help="Show deployment plan without executing."),
    skip_preflight: bool = typer.Option(False, "--skip-preflight", help="Skip pre-flight node checks."),
    download_timeout: int = typer.Option(
        7200, "--download-timeout", help="Timeout in seconds for each model download."
    ),
) -> None:
    """Deploy models, plists, and configs to the cluster."""
    from thunder_forge.cluster.config import generate_litellm_config, validate_memory
    from thunder_forge.cluster.deploy import run_deploy
    from thunder_forge.cluster.models import run_ensure_models

    config, repo_root = _load_config()
    config_path = repo_root / "configs" / "litellm-config.yaml"

    if not skip_preflight:
        _run_preflight(config, target_node=node)

    if not skip_models and not dry_run:
        typer.echo("Ensuring models are present...")
        if not run_ensure_models(config, target_node=node, download_timeout=download_timeout):
            typer.echo("Model sync failed", err=True)
            raise typer.Exit(1)

    if not dry_run:
        typer.echo("\nGenerating config...")
        errors = validate_memory(config)
        if errors:
            for err in errors:
                typer.echo(f"Error: {err}", err=True)
            raise typer.Exit(1)
        content = generate_litellm_config(config)
        config_path.write_text(content)
        typer.echo(f"  Generated {config_path}")

    success = run_deploy(config, target_node=node, dry_run=dry_run)
    raise typer.Exit(0 if success else 1)


@app.command()
def restart(
    node: str | None = typer.Option(None, "--node", help="Restart services on a single node (e.g. msm1)."),
    skip_preflight: bool = typer.Option(False, "--skip-preflight", help="Skip pre-flight node checks."),
    skip_gateway: bool = typer.Option(False, "--skip-gateway", help="Don't restart the LiteLLM proxy."),
) -> None:
    """Restart inference services and the LiteLLM proxy."""
    from thunder_forge.cluster.deploy import run_restart

    config, _ = _load_config()

    if not skip_preflight:
        _run_preflight(config, target_node=node)

    success = run_restart(config, target_node=node, skip_gateway=skip_gateway)
    raise typer.Exit(0 if success else 1)


@app.command()
def stop(
    node: str | None = typer.Option(None, "--node", help="Stop services on a single node (e.g. msm1)."),
    skip_preflight: bool = typer.Option(False, "--skip-preflight", help="Skip pre-flight node checks."),
    skip_gateway: bool = typer.Option(False, "--skip-gateway", help="Don't stop the LiteLLM proxy."),
) -> None:
    """Stop inference services and the LiteLLM proxy."""
    from thunder_forge.cluster.deploy import run_stop

    config, _ = _load_config()

    if not skip_preflight:
        _run_preflight(config, target_node=node)

    success = run_stop(config, target_node=node, skip_gateway=skip_gateway)
    raise typer.Exit(0 if success else 1)


@app.command()
def health(
    skip_preflight: bool = typer.Option(False, "--skip-preflight", help="Skip pre-flight node checks."),
) -> None:
    """Check health of all cluster services."""
    from thunder_forge.cluster.health import run_health_checks

    config, _ = _load_config()

    if not skip_preflight:
        _run_preflight(config)

    all_healthy = run_health_checks(config)
    raise typer.Exit(0 if all_healthy else 1)
