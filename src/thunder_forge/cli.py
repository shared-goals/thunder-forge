"""Thunder Forge CLI — cluster management commands."""

from pathlib import Path
from typing import cast

import typer

from thunder_forge.cluster.artifacts import build_artifact_readiness_plan, probe_artifact_presence
from thunder_forge.cluster.config import ClusterConfig, Node, NodeRuntime
from thunder_forge.cluster.omlx import check_omlx_health

app = typer.Typer(
    name="thunder-forge",
    help="CLI for managing a local MLX inference cluster.",
    no_args_is_help=True,
)
runtime_app = typer.Typer(help="Manage node-level runtimes such as oMLX.", no_args_is_help=True)
artifact_app = typer.Typer(help="Inspect model artifact readiness for oMLX nodes.", no_args_is_help=True)
app.add_typer(runtime_app, name="runtime")
app.add_typer(artifact_app, name="artifact")


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


@artifact_app.command("status")
def artifact_status(
    model: str = typer.Option(..., "--model", help="Hugging Face model repo id."),
    node: str = typer.Option(..., "--node", help="Node name to check artifact readiness for (e.g. msm3)."),
    studio_hf_home: str = typer.Option("~/.cache/huggingface", "--studio-hf-home", help="Studio HF home path."),
) -> None:
    """Inspect model artifact readiness without downloading or syncing."""
    config, _ = _load_config()
    runtime_node = _get_runtime_node(config, node)
    node_home_dir = runtime_node.home_dir or f"/Users/{runtime_node.user}"
    presence = probe_artifact_presence(
        repo_id=model,
        node_host=runtime_node.host,
        node_home_dir=node_home_dir,
        studio_hf_home=studio_hf_home,
    )
    plan = build_artifact_readiness_plan(
        repo_id=model,
        node=node,
        node_home_dir=node_home_dir,
        presence=presence,
        studio_hf_home=studio_hf_home,
    )

    typer.echo(f"model: {model}")
    _print_runtime_node_header(node, runtime_node)
    typer.echo(f"studio_hf_cache_path: {plan.studio_hf_cache_path}")
    typer.echo(f"node_hf_cache_path: {plan.node_hf_cache_path}")
    typer.echo(f"node_omlx_model_dir: {plan.node_omlx_model_dir}")
    typer.echo(f"studio_hf_cache: {'present' if presence.studio_hf_cache else 'missing'}")
    typer.echo(f"node_hf_cache: {'present' if presence.node_hf_cache else 'missing'}")
    typer.echo(f"node_omlx_model_dir: {'present' if presence.node_omlx_model_dir else 'missing'}")
    typer.echo(f"ready: {'yes' if plan.ready else 'no'}")
    if plan.actions:
        typer.echo("next_actions:")
        for action in plan.actions:
            typer.echo(f"  - {action}")


@runtime_app.command("start")
def runtime_start(
    node: str = typer.Option(..., "--node", help="Node name to start runtime on (e.g. msm3)."),
    dry_run: bool = typer.Option(True, "--dry-run/--apply", help="Print command without executing by default."),
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

    typer.echo("Error: runtime apply/start is not implemented yet; use --dry-run", err=True)
    raise typer.Exit(1)


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
