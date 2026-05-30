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
    studio_omlx_models_dir_from_env,
)
from thunder_forge.cluster.bootstrap import ensure_cache_hub_dir, ensure_olla_binary, write_generated_olla_config
from thunder_forge.cluster.config import ClusterConfig, Node, NodeRole, NodeRuntime
from thunder_forge.cluster.edge import (
    EDGE_USER_PREFIX,
    EdgeProxyConfig,
    build_edge_clients_from_env,
    edge_api_key_from_env,
    ensure_edge_api_keys,
    run_edge_service_restart,
    serve_edge_proxy,
    smoke_edge_contract,
    summarize_edge_usage,
)
from thunder_forge.cluster.fabric import build_transport_plan
from thunder_forge.cluster.gateway import GatewayDaemonSetupResult, run_gateway_daemon_setup
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
app.add_typer(runtime_app, name="runtime")
app.add_typer(artifact_app, name="artifact")
app.add_typer(edge_app, name="edge")
app.add_typer(olla_app, name="olla")
app.add_typer(config_app, name="config")
app.add_typer(service_app, name="service")
app.add_typer(cluster_app, name="cluster")

def _load_config() -> tuple[ClusterConfig, Path]:
    """Load the TF cluster config. Returns (ClusterConfig, repo_root Path)."""
    import thunder_forge.cluster.config as _cfg

    repo_root = _cfg.find_repo_root()
    cluster_config_path = _cfg.default_cluster_config_path(repo_root)
    if not cluster_config_path.exists():
        typer.echo(f"Error: {cluster_config_path} not found", err=True)
        raise typer.Exit(1)
    return _cfg.load_cluster_config(cluster_config_path), repo_root


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


def _format_bytes(size_bytes: int) -> str:
    value = float(size_bytes)
    for unit in ("B", "KB", "MB", "GB"):
        if value < 1024 or unit == "GB":
            return f"{value:.1f} {unit}" if unit != "B" else f"{int(value)} B"
        value /= 1024
    return f"{value:.1f} GB"


def _runtime(runtime_node: Node) -> NodeRuntime:
    return cast(NodeRuntime, runtime_node.runtime)


def _gateway_restart_notice(config: ClusterConfig) -> str:
    try:
        gateway_name = config.gateway_name
    except ValueError:
        return ""
    return (
        "notice: if model placement or node topology changed, "
        f"restart gateway routes with `make restart {gateway_name}`"
    )


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
    typer.echo(f"service: {result.service}")
    typer.echo(f"manager: {manager}")
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


def _service_result_failed(result) -> bool:
    return bool(result.errors) or (result.applied and not result.ok)


def _gateway_operator_user(config: ClusterConfig, user: str) -> str:
    if user:
        return user
    try:
        return config.gateway.user or os.environ.get("USER", "")
    except ValueError:
        return os.environ.get("USER", "")


def _print_gateway_daemon_setup_result(result: GatewayDaemonSetupResult, *, dry_run: bool) -> None:
    typer.echo("scope: gateway")
    typer.echo("manager: daemon")
    typer.echo(f"operator_user: {result.user}")
    typer.echo(f"admin_user: {result.admin_user or '(direct sudo)'}")
    typer.echo(f"sudoers_path: {result.sudoers_path}")
    typer.echo(f"script_path: {result.script_path}")
    typer.echo(f"mode: {'dry-run' if dry_run else 'apply'}")
    if result.services:
        typer.echo("services:")
        for service in result.services:
            typer.echo(f"  - {service.service}: {service.label}")
            typer.echo(f"    plist_path: {service.plist_path}")
            typer.echo(f"    staging_plist_path: {service.staging_plist_path}")
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
        inference_names = [target] if node.has_role(NodeRole.INFERENCE) or node.has_role(NodeRole.NODE) else []
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


def _progress(message: str) -> None:
    typer.echo(f"  {message}")


def _fail_on_setup_errors(errors: list[str]) -> None:
    for error in errors:
        typer.echo(f"Error: {error}", err=True)
    if errors:
        raise typer.Exit(1)


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
    olla_version: str | None = typer.Option(None, "--olla-version", help="Pinned Olla release version."),
    olla_os: str | None = typer.Option(None, "--olla-os", help="Olla release OS segment."),
    olla_arch: str | None = typer.Option(None, "--olla-arch", help="Olla release architecture segment."),
    olla_bin_dir: Path | None = typer.Option(None, "--olla-bin-dir", help="Local Olla binary directory."),
) -> None:
    """Prepare the gateway, cache/download hub, and inference daemons as one cluster."""
    config, repo_root = _load_config()
    resolved_olla_version = olla_version or config.services.olla_version
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
            typer.echo(f"would: ensure cache hub {studio_omlx_models_dir_from_env()}")
        for name in inference_names:
            node = config.nodes[name]
            resolved_admin_user = admin_user or node.admin_user
            escalation = f"su={resolved_admin_user}" if resolved_admin_user else f"sudo={node.user}"
            home_dir = node.home_dir or f"/Users/{node.user}"
            typer.echo(f"would: ensure oMLX CLI at {home_dir}/.local/bin/omlx")
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
            "reason=install Olla + TF edge LaunchDaemons"
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
        ensure_cache_hub_dir(progress=_progress)
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
            progress=_progress,
        )
        _fail_on_setup_errors(tooling_result.errors)
        if not tooling_result.ok:
            typer.echo("Error: oMLX tooling setup did not verify cleanly", err=True)
            raise typer.Exit(1)
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
            manager="daemon",
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
            manager="daemon",
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
) -> None:
    """Check direct oMLX health for selected inference nodes."""
    config, _ = _load_config()
    _gateway_names, _cache_names, inference_names = _resolve_prepare_targets(config, target)
    typer.echo("Thunder Forge cluster status")
    typer.echo(f"target: {target or 'all'}")
    if not inference_names:
        typer.echo("status: no inference nodes selected")
        return

    failed = False
    for node_name in inference_names:
        runtime_node = _get_runtime_node(config, node_name)
        base_url = f"http://{runtime_node.host}:{_runtime(runtime_node).port}"
        result = check_omlx_health(base_url)
        health_status = "ok" if result.health_ok else "fail"
        models_status = "ok" if result.models_ok else "fail"
        typer.echo(f"{node_name}: health={health_status} models={models_status}")
        if result.models:
            typer.echo(f"  served_models: {', '.join(result.models)}")
        for error in result.errors:
            typer.echo(f"Error: {node_name}: {error}", err=True)
        failed = failed or not (result.health_ok and result.models_ok)

    if failed:
        raise typer.Exit(1)


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

    if resolved_restart:
        if dry_run:
            typer.echo(f"would: restart {target} oMLX runtime after sync")
        else:
            typer.echo("")
            typer.echo("== Runtime Restart ==")
            result = run_omlx_daemon_restart(runtime_node, apply=True, timeout=300)
            typer.echo(f"  omlx: {result.label}")
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
        help="Service manager. Olla/edge: launchd or daemon. oMLX: process, daemon, or launchd.",
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
    config, _ = _load_config()
    resolved_base_url = base_url or local_base_url(
        resolve_port(None, default=config.services.olla_port)
    )
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
    config, _ = _load_config()
    resolved_port = resolve_port(port, default=config.services.olla_port)
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
) -> None:
    """Run the minimal TF edge proxy."""
    config, repo_root = _load_config()
    resolved_port = resolve_port(port, default=config.services.edge_port)
    resolved_olla_base_url = olla_base_url or local_base_url(config.services.olla_port)
    clients_by_key = build_edge_clients_from_env(
        users_env=users_env,
    )
    if not clients_by_key:
        typer.echo(
            f"Error: no edge API keys found. Run `make edge-keys EDGE_CLIENTS=...` or set {users_env}.",
            err=True,
        )
        raise typer.Exit(1)

    access_log_path = _edge_access_log_path(repo_root, config, access_log)

    def log_sink(line: str) -> None:
        access_log_path.parent.mkdir(parents=True, exist_ok=True)
        with access_log_path.open("a") as handle:
            handle.write(f"{line}\n")

    client_ids = sorted({client.client_id for client in clients_by_key.values()})

    config = EdgeProxyConfig(
        olla_base_url=resolved_olla_base_url,
        clients_by_key=clients_by_key,
        access_log_sink=log_sink,
    )
    typer.echo(f"serving_edge: http://{host}:{resolved_port}")
    typer.echo(f"olla_base_url: {resolved_olla_base_url}")
    typer.echo(f"clients: {', '.join(client_ids)}")
    typer.echo(f"api_key_count: {len(clients_by_key)}")
    typer.echo(f"access_log: {access_log_path}")
    serve_edge_proxy(host=host, port=resolved_port, config=config)


@artifact_app.command("status")
def artifact_status(
    model: str = typer.Option(..., "--model", help="Hugging Face model repo id."),
    node: str = typer.Option(..., "--node", help="Node name to check artifact readiness for (e.g. msm3)."),
) -> None:
    """Inspect oMLX model-directory readiness without downloading or syncing."""
    config, _ = _load_config()
    runtime_node = _get_runtime_node(config, node)
    node_home_dir = runtime_node.home_dir or f"/Users/{runtime_node.user}"
    studio_omlx_models_dir = studio_omlx_models_dir_from_env()
    presence = probe_artifact_presence(
        repo_id=model,
        node_host=runtime_node.host,
        node_home_dir=node_home_dir,
        studio_omlx_models_dir=studio_omlx_models_dir,
    )
    plan = build_artifact_readiness_plan(
        repo_id=model,
        node=node,
        node_home_dir=node_home_dir,
        presence=presence,
        studio_omlx_models_dir=studio_omlx_models_dir,
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
    _load_repo_dotenv()
    plan = build_artifact_download_plan(
        repo_id=model,
        studio_omlx_models_dir=studio_omlx_models_dir_from_env(),
    )

    typer.echo(f"model: {model}")
    typer.echo(f"model_dir_name: {plan.model_dir_name}")
    typer.echo(f"runtime_model_id: {plan.runtime_model_id}")
    typer.echo(f"destination: {plan.destination}")
    typer.echo("action: download_to_studio_omlx")
    typer.echo(f"command: {plan.command}")

    if dry_run:
        typer.echo("mode: dry-run")
        return

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

    result = run_artifact_download(plan, timeout=timeout, progress_callback=print_progress)
    if result.returncode != 0:
        typer.echo(f"Error: download failed with exit code {result.returncode}", err=True)
        if result.stderr:
            typer.echo(result.stderr, err=True)
        raise typer.Exit(result.returncode)
    typer.echo("status: downloaded")


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
    runtime_node = _get_runtime_node(config, node)
    node_home_dir = runtime_node.home_dir or f"/Users/{runtime_node.user}"
    studio_omlx_models_dir = studio_omlx_models_dir_from_env()
    repo_ids = [model] if model else []
    if not repo_ids:
        if not runtime_node.models:
            typer.echo(f"Error: node '{node}' has no models configured", err=True)
            raise typer.Exit(1)
        for model_id in runtime_node.models:
            configured_model = config.models.get(model_id)
            if configured_model is None:
                typer.echo(f"Error: node '{node}' references unknown model '{model_id}'", err=True)
                raise typer.Exit(1)
            repo_id = configured_model.source.repo.strip()
            if not repo_id:
                typer.echo(f"Error: models.{model_id}.source.repo is required for full node sync", err=True)
                raise typer.Exit(1)
            if repo_id not in repo_ids:
                repo_ids.append(repo_id)

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

    if len(repo_ids) > 1:
        typer.echo("sync_scope: node")
        typer.echo(f"models: {len(repo_ids)}")

    for index, repo_id in enumerate(repo_ids, start=1):
        if len(repo_ids) > 1:
            typer.echo("")
            typer.echo(f"== Model {index}/{len(repo_ids)} ==")
        presence = probe_artifact_presence(
            repo_id=repo_id,
            node_host=runtime_node.host,
            node_home_dir=node_home_dir,
            studio_omlx_models_dir=studio_omlx_models_dir,
        )
        readiness_plan = build_artifact_readiness_plan(
            repo_id=repo_id,
            node=node,
            node_home_dir=node_home_dir,
            presence=presence,
            studio_omlx_models_dir=studio_omlx_models_dir,
        )
        sync_plan = build_artifact_sync_plan(
            repo_id=repo_id,
            node_user=runtime_node.user,
            node_host=transport_plan.resolved_transport_host,
            node_home_dir=node_home_dir,
            studio_omlx_models_dir=studio_omlx_models_dir,
            ssh_host_key_alias=runtime_node.host if transport_plan.uses_fabric else None,
        )

        typer.echo(f"model: {repo_id}")
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
            continue
        if dry_run:
            typer.echo("mode: dry-run")
            continue

        result = run_artifact_sync(sync_plan, timeout=timeout)
        if result.returncode != 0:
            typer.echo(f"Error: sync failed with exit code {result.returncode}", err=True)
            raise typer.Exit(result.returncode)
        typer.echo("status: synced")

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
    node: str = typer.Option(..., "--node", help="Node name to sync artifact to (e.g. msm3)."),
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
    """Sync oMLX model directories from studio to a node."""
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
