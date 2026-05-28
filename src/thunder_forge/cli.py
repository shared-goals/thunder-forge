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
from thunder_forge.cluster.fabric import build_transport_plan
from thunder_forge.cluster.olla import dev_smoke_olla, smoke_olla_router
from thunder_forge.cluster.omlx import (
    check_omlx_health,
    run_omlx_daemon_restart,
    run_omlx_daemon_setup,
    run_omlx_install,
    run_omlx_process_restart,
    run_omlx_runtime_restart,
    run_omlx_runtime_start,
    smoke_omlx_chat,
)

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
    """Load the TF cluster config. Returns (ClusterConfig, repo_root Path)."""
    from thunder_forge.cluster.config import find_repo_root, load_cluster_config

    repo_root = find_repo_root()
    cluster_config_path = repo_root / "configs" / "node-assignments.yaml"
    if not cluster_config_path.exists():
        typer.echo(f"Error: {cluster_config_path} not found", err=True)
        raise typer.Exit(1)
    return load_cluster_config(cluster_config_path), repo_root


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
        typer.echo("fabric_host: true")


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
    typer.echo(f"model_dir_name: {plan.model_dir_name}")
    typer.echo(f"runtime_model_id: {plan.runtime_model_id}")
    _print_runtime_node_header(node, runtime_node)
    typer.echo(f"studio_omlx_model_dir_path: {plan.studio_omlx_model_dir}")
    typer.echo(f"node_omlx_model_dir_path: {plan.node_omlx_model_dir}")
    typer.echo(f"studio_omlx_model_dir: {'ready' if presence.studio_omlx_model_dir else 'missing_or_incomplete'}")
    typer.echo(f"node_omlx_model_dir: {'ready' if presence.node_omlx_model_dir else 'missing_or_incomplete'}")
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
    typer.echo(f"runtime_model_id: {plan.runtime_model_id}")
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
        help="Transport selection: auto, fabric, or management. Auto probes fabric only when fabric_host is true.",
    ),
    management: bool = typer.Option(
        False,
        "--management",
        help="Force management host even when fabric_host probing is enabled.",
    ),
    timeout: int = typer.Option(7200, "--timeout", help="Timeout in seconds for rsync when applying."),
) -> None:
    """Sync an oMLX model directory from studio to a node."""
    config, _ = _load_config()
    runtime_node = _get_runtime_node(config, node)
    node_home_dir = runtime_node.home_dir or f"/Users/{runtime_node.user}"
    requested_transport = "management" if management else transport
    transport_plan = build_transport_plan(
        requested_transport=requested_transport,
        management_host=runtime_node.host,
        node_user=runtime_node.user,
        fabric_host=runtime_node.fabric_host,
    )
    if not transport_plan.ok:
        typer.echo(f"Error: {transport_plan.error}", err=True)
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
        node_host=transport_plan.resolved_transport_host,
        node_home_dir=node_home_dir,
    )

    typer.echo(f"model: {model}")
    typer.echo(f"model_dir_name: {sync_plan.model_dir_name}")
    typer.echo(f"runtime_model_id: {sync_plan.runtime_model_id}")
    _print_runtime_node_header(node, runtime_node)
    typer.echo("source: studio")
    typer.echo(f"transport_host: {transport_plan.transport_host}")
    if transport_plan.resolved_transport_host != transport_plan.transport_host:
        typer.echo(f"resolved_transport_host: {transport_plan.resolved_transport_host}")
    if transport_plan.fabric_fallback:
        typer.echo(f"fabric_fallback: {transport_plan.fabric_fallback}")
    typer.echo(f"source_path: {sync_plan.source_path}")
    typer.echo(f"destination: {sync_plan.destination}")
    typer.echo("action: sync_to_node_omlx")
    typer.echo(f"command: {sync_plan.command}")

    if ArtifactReadinessAction.DOWNLOAD_TO_STUDIO_OMLX in readiness_plan.actions:
        typer.echo(
            "Error: studio oMLX model directory is missing or incomplete; "
            "download the model to studio oMLX models first",
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


@runtime_app.command("setup-daemon")
def runtime_setup_daemon(
    node: str = typer.Option(..., "--node", help="Node name to set up for system oMLX daemon management."),
    admin_user: str = typer.Option(
        "",
        "--admin-user",
        help="Admin account that can run sudo on the node. Defaults to the configured node user.",
    ),
    via_su: bool = typer.Option(
        False,
        "--via-su/--ssh-admin",
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
    if via_su and not admin_user:
        typer.echo("Error: --via-su requires --admin-user", err=True)
        raise typer.Exit(1)

    config, _ = _load_config()
    runtime_node = _get_runtime_node(config, node)
    if runtime_node.home_dir is None:
        runtime_node.home_dir = f"/Users/{runtime_node.user}"

    result = run_omlx_daemon_setup(
        runtime_node,
        admin_user=admin_user or None,
        via_su=via_su,
        script_path=script_path or None,
        apply=not dry_run,
        timeout=timeout,
    )

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
    node: str = typer.Option(..., "--node", help="Node name to restart runtime on (e.g. msm3)."),
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
    node: str = typer.Option(..., "--node", help="Node name to check runtime status for (e.g. msm3)."),
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
    node: str = typer.Option(..., "--node", help="Node name to smoke-test runtime for (e.g. msm3)."),
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
    node: str = typer.Option(..., "--node", help="Node name to install launchd daemon for (e.g. msm3)."),
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
def generate_olla_config_cmd() -> None:
    """Generate olla-config.yaml from the TF cluster config."""
    from thunder_forge.cluster.config import generate_olla_config

    config, repo_root = _load_config()
    config_path = repo_root / "configs" / "olla-config.yaml"
    content = generate_olla_config(config)
    config_path.write_text(content)
    typer.echo(f"✓ Generated {config_path}")
