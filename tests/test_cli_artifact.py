"""CLI tests for oMLX artifact readiness commands."""

from pathlib import Path
from textwrap import dedent

from typer.testing import CliRunner

from thunder_forge.cli import app
from thunder_forge.cluster.artifacts import ArtifactPresence

runner = CliRunner()


def _write_runtime_config(repo: Path, *, fabric_host: bool = True) -> None:
    config_dir = repo / "configs"
    config_dir.mkdir()
    fabric_line = "                fabric_host: msm3-fabric\n" if fabric_host else ""
    (config_dir / "node-assignments.yaml").write_text(
        dedent(f"""\
            models: {{}}
            nodes:
              msm3:
                host: msm3-wifi.lan
{fabric_line}                ram_gb: 128
                user: shag
                role: node
                home_dir: /Users/shag
                runtime:
                  type: omlx
                  port: 8018
            assignments: {{}}
        """)
    )


def test_artifact_status_prints_readiness_plan(tmp_path: Path, monkeypatch) -> None:
    repo = tmp_path
    _write_runtime_config(repo)

    import thunder_forge.cli as cli_module
    import thunder_forge.cluster.config as config_module

    monkeypatch.setattr(config_module, "find_repo_root", lambda: repo)
    monkeypatch.setattr(
        cli_module,
        "probe_artifact_presence",
        lambda *, repo_id, node_host, node_home_dir: ArtifactPresence(
            studio_omlx_model_dir=True,
            node_omlx_model_dir=False,
        ),
    )

    result = runner.invoke(
        app,
        [
            "artifact",
            "status",
            "--model",
            "mlx-community/gpt-oss-20b-MXFP4-Q8",
            "--node",
            "msm3",
        ],
    )

    assert result.exit_code == 0
    assert "model: mlx-community/gpt-oss-20b-MXFP4-Q8" in result.stdout
    assert "node: msm3" in result.stdout
    assert "management_host: msm3-wifi.lan" in result.stdout
    assert "studio_omlx_model_dir_path: ~/.omlx/models/gpt-oss-20b-MXFP4-Q8" in result.stdout
    assert "node_omlx_model_dir_path: /Users/shag/.omlx/models/gpt-oss-20b-MXFP4-Q8" in result.stdout
    assert "studio_omlx_model_dir: present" in result.stdout
    assert "node_omlx_model_dir: missing" in result.stdout
    assert "ready: no" in result.stdout
    assert "next_actions:" in result.stdout
    assert "- sync_to_node_omlx" in result.stdout
    assert ".cache/huggingface" not in result.stdout


def test_artifact_download_dry_run_prints_direct_omlx_download_plan(tmp_path: Path, monkeypatch) -> None:
    repo = tmp_path
    _write_runtime_config(repo)

    import thunder_forge.cluster.config as config_module

    monkeypatch.setattr(config_module, "find_repo_root", lambda: repo)

    result = runner.invoke(
        app,
        [
            "artifact",
            "download",
            "--model",
            "mlx-community/Qwen3-1.7B-4bit",
            "--dry-run",
        ],
    )

    assert result.exit_code == 0
    assert "model: mlx-community/Qwen3-1.7B-4bit" in result.stdout
    assert "model_dir_name: Qwen3-1.7B-4bit" in result.stdout
    assert "destination: ~/.omlx/models/Qwen3-1.7B-4bit" in result.stdout
    assert "action: download_to_studio_omlx" in result.stdout
    assert "mode: dry-run" in result.stdout
    assert "hf download mlx-community/Qwen3-1.7B-4bit" in result.stdout
    assert "--local-dir" in result.stdout
    assert "Qwen3-1.7B-4bit" in result.stdout
    assert ".cache/huggingface" not in result.stdout


def test_artifact_sync_dry_run_prints_studio_to_node_plan(tmp_path: Path, monkeypatch) -> None:
    repo = tmp_path
    _write_runtime_config(repo, fabric_host=False)

    import thunder_forge.cli as cli_module
    import thunder_forge.cluster.config as config_module

    monkeypatch.setattr(config_module, "find_repo_root", lambda: repo)
    monkeypatch.setattr(
        cli_module,
        "probe_artifact_presence",
        lambda *, repo_id, node_host, node_home_dir: ArtifactPresence(
            studio_omlx_model_dir=True,
            node_omlx_model_dir=False,
        ),
    )

    result = runner.invoke(
        app,
        [
            "artifact",
            "sync",
            "--model",
            "BAAI/bge-small-en-v1.5",
            "--node",
            "msm3",
            "--dry-run",
        ],
    )

    assert result.exit_code == 0
    assert "mode: dry-run" in result.stdout
    assert "source: studio" in result.stdout
    assert "transport_host: msm3-wifi.lan" in result.stdout
    assert "action: sync_to_node_omlx" in result.stdout
    assert "rsync" in result.stdout
    assert "~/.omlx/models/bge-small-en-v1.5/" in result.stdout
    assert "shag@msm3-wifi.lan:/Users/shag/.omlx/models/bge-small-en-v1.5/" in result.stdout
    assert ".cache/huggingface" not in result.stdout


def test_artifact_sync_uses_resolved_fabric_by_default_when_available(tmp_path: Path, monkeypatch) -> None:
    repo = tmp_path
    _write_runtime_config(repo)

    import thunder_forge.cli as cli_module
    import thunder_forge.cluster.config as config_module

    monkeypatch.setattr(config_module, "find_repo_root", lambda: repo)
    monkeypatch.setattr(
        cli_module,
        "probe_artifact_presence",
        lambda *, repo_id, node_host, node_home_dir: ArtifactPresence(
            studio_omlx_model_dir=True,
            node_omlx_model_dir=False,
        ),
    )
    monkeypatch.setattr(cli_module, "resolve_fabric_host", lambda host: "169.254.251.195")

    result = runner.invoke(
        app,
        [
            "artifact",
            "sync",
            "--model",
            "BAAI/bge-small-en-v1.5",
            "--node",
            "msm3",
            "--dry-run",
        ],
    )

    assert result.exit_code == 0
    assert "transport_host: msm3-fabric" in result.stdout
    assert "resolved_transport_host: 169.254.251.195" in result.stdout
    assert "shag@169.254.251.195:/Users/shag/.omlx/models/bge-small-en-v1.5/" in result.stdout


def test_artifact_sync_falls_back_to_management_host_when_fabric_is_unresolved(
    tmp_path: Path,
    monkeypatch,
) -> None:
    repo = tmp_path
    _write_runtime_config(repo)

    import thunder_forge.cli as cli_module
    import thunder_forge.cluster.config as config_module

    monkeypatch.setattr(config_module, "find_repo_root", lambda: repo)
    monkeypatch.setattr(
        cli_module,
        "probe_artifact_presence",
        lambda *, repo_id, node_host, node_home_dir: ArtifactPresence(
            studio_omlx_model_dir=True,
            node_omlx_model_dir=False,
        ),
    )
    monkeypatch.setattr(cli_module, "resolve_fabric_host", lambda host: None)
    monkeypatch.setattr(
        cli_module,
        "discover_link_local_fabric_host",
        lambda *, management_host, node_user: None,
    )

    result = runner.invoke(
        app,
        [
            "artifact",
            "sync",
            "--model",
            "BAAI/bge-small-en-v1.5",
            "--node",
            "msm3",
            "--dry-run",
        ],
    )

    assert result.exit_code == 0
    assert "transport_host: msm3-wifi.lan" in result.stdout
    assert "fabric_fallback: msm3-fabric unresolved" in result.stdout
    assert "shag@msm3-wifi.lan:/Users/shag/.omlx/models/bge-small-en-v1.5/" in result.stdout


def test_artifact_sync_can_force_management_host_when_fabric_host_is_configured(tmp_path: Path, monkeypatch) -> None:
    repo = tmp_path
    _write_runtime_config(repo)

    import thunder_forge.cli as cli_module
    import thunder_forge.cluster.config as config_module

    monkeypatch.setattr(config_module, "find_repo_root", lambda: repo)
    monkeypatch.setattr(
        cli_module,
        "probe_artifact_presence",
        lambda *, repo_id, node_host, node_home_dir: ArtifactPresence(
            studio_omlx_model_dir=True,
            node_omlx_model_dir=False,
        ),
    )
    monkeypatch.setattr(cli_module, "resolve_fabric_host", lambda host: "169.254.251.195")

    result = runner.invoke(
        app,
        [
            "artifact",
            "sync",
            "--model",
            "BAAI/bge-small-en-v1.5",
            "--node",
            "msm3",
            "--management",
            "--dry-run",
        ],
    )

    assert result.exit_code == 0
    assert "transport_host: msm3-wifi.lan" in result.stdout
    assert "resolved_transport_host" not in result.stdout
    assert "shag@msm3-wifi.lan:/Users/shag/.omlx/models/bge-small-en-v1.5/" in result.stdout


def test_artifact_sync_without_fabric_host_uses_management_host(tmp_path: Path, monkeypatch) -> None:
    repo = tmp_path
    _write_runtime_config(repo, fabric_host=False)

    import thunder_forge.cli as cli_module
    import thunder_forge.cluster.config as config_module

    monkeypatch.setattr(config_module, "find_repo_root", lambda: repo)
    monkeypatch.setattr(
        cli_module,
        "probe_artifact_presence",
        lambda *, repo_id, node_host, node_home_dir: ArtifactPresence(
            studio_omlx_model_dir=True,
            node_omlx_model_dir=False,
        ),
    )

    result = runner.invoke(
        app,
        [
            "artifact",
            "sync",
            "--model",
            "BAAI/bge-small-en-v1.5",
            "--node",
            "msm3",
            "--dry-run",
        ],
    )

    assert result.exit_code == 0
    assert "transport_host: msm3-wifi.lan" in result.stdout


def test_artifact_sync_apply_invokes_runner(tmp_path: Path, monkeypatch) -> None:
    repo = tmp_path
    _write_runtime_config(repo)
    calls = []

    import subprocess

    import thunder_forge.cli as cli_module
    import thunder_forge.cluster.config as config_module

    monkeypatch.setattr(config_module, "find_repo_root", lambda: repo)
    monkeypatch.setattr(
        cli_module,
        "probe_artifact_presence",
        lambda *, repo_id, node_host, node_home_dir: ArtifactPresence(
            studio_omlx_model_dir=True,
            node_omlx_model_dir=False,
        ),
    )

    def fake_run_artifact_sync(plan, *, timeout):
        calls.append((plan, timeout))
        return subprocess.CompletedProcess(args=plan.rsync_args, returncode=0)

    monkeypatch.setattr(cli_module, "run_artifact_sync", fake_run_artifact_sync)

    result = runner.invoke(
        app,
        [
            "artifact",
            "sync",
            "--model",
            "BAAI/bge-small-en-v1.5",
            "--node",
            "msm3",
            "--apply",
            "--timeout",
            "123",
        ],
    )

    assert result.exit_code == 0
    assert "status: synced" in result.stdout
    assert calls[0][1] == 123


def test_artifact_download_apply_invokes_runner(tmp_path: Path, monkeypatch) -> None:
    repo = tmp_path
    _write_runtime_config(repo)
    calls = []

    import subprocess

    import thunder_forge.cli as cli_module
    import thunder_forge.cluster.config as config_module

    monkeypatch.setattr(config_module, "find_repo_root", lambda: repo)

    def fake_run_artifact_download(plan, *, timeout):
        calls.append((plan, timeout))
        return subprocess.CompletedProcess(args=plan.args, returncode=0)

    monkeypatch.setattr(cli_module, "run_artifact_download", fake_run_artifact_download)

    result = runner.invoke(
        app,
        [
            "artifact",
            "download",
            "--model",
            "mlx-community/Qwen3-1.7B-4bit",
            "--apply",
            "--timeout",
            "456",
        ],
    )

    assert result.exit_code == 0
    assert "status: downloaded" in result.stdout
    assert calls[0][0].destination == "~/.omlx/models/Qwen3-1.7B-4bit"
    assert calls[0][1] == 456


def test_artifact_sync_apply_propagates_runner_failure(tmp_path: Path, monkeypatch) -> None:
    repo = tmp_path
    _write_runtime_config(repo)

    import subprocess

    import thunder_forge.cli as cli_module
    import thunder_forge.cluster.config as config_module

    monkeypatch.setattr(config_module, "find_repo_root", lambda: repo)
    monkeypatch.setattr(
        cli_module,
        "probe_artifact_presence",
        lambda *, repo_id, node_host, node_home_dir: ArtifactPresence(
            studio_omlx_model_dir=True,
            node_omlx_model_dir=False,
        ),
    )
    monkeypatch.setattr(
        cli_module,
        "run_artifact_sync",
        lambda plan, *, timeout: subprocess.CompletedProcess(args=plan.rsync_args, returncode=23),
    )

    result = runner.invoke(
        app,
        [
            "artifact",
            "sync",
            "--model",
            "BAAI/bge-small-en-v1.5",
            "--node",
            "msm3",
            "--apply",
        ],
    )

    assert result.exit_code == 23
    assert "sync failed with exit code 23" in result.stderr
