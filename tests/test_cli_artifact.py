"""CLI tests for oMLX artifact readiness commands."""

from pathlib import Path
from textwrap import dedent

from typer.testing import CliRunner

from thunder_forge.cli import app
from thunder_forge.cluster.artifacts import STUDIO_OMLX_MODELS_DIR_ENV, ArtifactPresence

runner = CliRunner()


def _write_runtime_config(repo: Path, *, fabric_host: bool = True) -> None:
    config_dir = repo / "configs"
    config_dir.mkdir()
    fabric_line = "                fabric_host: true\n" if fabric_host else ""
    (repo / "tfconfig.yaml").write_text(
        dedent(f"""\
            models: {{}}
            nodes:
              msm3:
                host: msm3-wifi.lan
{fabric_line}                ram_gb: 128
                user: shag
                roles: [inference]
                home_dir: /Users/shag
                runtime:
                  type: omlx
                  port: 8018
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
        lambda *, repo_id, node_host, node_home_dir, studio_omlx_models_dir=None: ArtifactPresence(
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
    assert "model_dir_name: mlx-community/gpt-oss-20b-MXFP4-Q8" in result.stdout
    assert "runtime_model_id: gpt-oss-20b-MXFP4-Q8" in result.stdout
    assert "studio_omlx_model_dir_path: ~/.omlx/models/mlx-community/gpt-oss-20b-MXFP4-Q8" in result.stdout
    assert "node_omlx_model_dir_path: /Users/shag/.omlx/models/mlx-community/gpt-oss-20b-MXFP4-Q8" in result.stdout
    assert "studio_omlx_model_dir: ready" in result.stdout
    assert "node_omlx_model_dir: missing_or_incomplete" in result.stdout
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
    assert "model_dir_name: mlx-community/Qwen3-1.7B-4bit" in result.stdout
    assert "runtime_model_id: Qwen3-1.7B-4bit" in result.stdout
    assert "destination: ~/.omlx/models/mlx-community/Qwen3-1.7B-4bit" in result.stdout
    assert "action: download_to_studio_omlx" in result.stdout
    assert "mode: dry-run" in result.stdout
    assert "omlx serve" in result.stdout
    assert "/admin/api/hf/download" in result.stdout
    assert "mlx-community/Qwen3-1.7B-4bit" in result.stdout
    assert ".cache/huggingface" not in result.stdout


def test_artifact_download_dry_run_uses_studio_omlx_dir_env(tmp_path: Path, monkeypatch) -> None:
    repo = tmp_path
    _write_runtime_config(repo)
    studio_omlx_models_dir = str(tmp_path / "studio" / ".omlx" / "models")

    import thunder_forge.cluster.config as config_module

    monkeypatch.setattr(config_module, "find_repo_root", lambda: repo)
    monkeypatch.setenv(STUDIO_OMLX_MODELS_DIR_ENV, studio_omlx_models_dir)

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
    assert f"destination: {studio_omlx_models_dir}/mlx-community/Qwen3-1.7B-4bit" in result.stdout
    assert f"--model-dir {studio_omlx_models_dir}" in result.stdout


def test_artifact_sync_dry_run_prints_studio_to_node_plan(tmp_path: Path, monkeypatch) -> None:
    repo = tmp_path
    _write_runtime_config(repo, fabric_host=False)

    import thunder_forge.cli as cli_module
    import thunder_forge.cluster.config as config_module

    monkeypatch.setattr(config_module, "find_repo_root", lambda: repo)
    monkeypatch.setattr(
        cli_module,
        "probe_artifact_presence",
        lambda *, repo_id, node_host, node_home_dir, studio_omlx_models_dir=None: ArtifactPresence(
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
    assert "fabric_fallback" not in result.stdout
    assert "model_dir_name: BAAI/bge-small-en-v1.5" in result.stdout
    assert "runtime_model_id: bge-small-en-v1.5" in result.stdout
    assert "action: sync_to_node_omlx" in result.stdout
    assert "rsync" in result.stdout
    assert "/Users/shag/.omlx/models/BAAI/bge-small-en-v1.5/" in result.stdout
    assert "shag@msm3-wifi.lan:/Users/shag/.omlx/models/BAAI/bge-small-en-v1.5/" in result.stdout
    assert ".cache/huggingface" not in result.stdout


def test_artifact_sync_dry_run_uses_studio_omlx_dir_env(tmp_path: Path, monkeypatch) -> None:
    repo = tmp_path
    _write_runtime_config(repo, fabric_host=False)
    studio_omlx_models_dir = str(tmp_path / "studio" / ".omlx" / "models")

    import thunder_forge.cli as cli_module
    import thunder_forge.cluster.config as config_module

    monkeypatch.setattr(config_module, "find_repo_root", lambda: repo)
    monkeypatch.setenv(STUDIO_OMLX_MODELS_DIR_ENV, studio_omlx_models_dir)
    monkeypatch.setattr(
        cli_module,
        "probe_artifact_presence",
        lambda *, repo_id, node_host, node_home_dir, studio_omlx_models_dir=None: ArtifactPresence(
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
    assert f"source_path: {studio_omlx_models_dir}/BAAI/bge-small-en-v1.5/" in result.stdout


def test_artifact_sync_uses_dynamic_fabric_by_default_when_enabled(tmp_path: Path, monkeypatch) -> None:
    repo = tmp_path
    _write_runtime_config(repo)

    import thunder_forge.cli as cli_module
    import thunder_forge.cluster.config as config_module
    import thunder_forge.cluster.fabric as fabric_module

    monkeypatch.setattr(config_module, "find_repo_root", lambda: repo)
    monkeypatch.setattr(
        cli_module,
        "probe_artifact_presence",
        lambda *, repo_id, node_host, node_home_dir, studio_omlx_models_dir=None: ArtifactPresence(
            studio_omlx_model_dir=True,
            node_omlx_model_dir=False,
        ),
    )
    monkeypatch.setattr(
        fabric_module,
        "discover_link_local_fabric_host",
        lambda *, management_host, node_user: "169.254.251.195",
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
    assert "transport_host: 169.254.251.195" in result.stdout
    assert "resolved_transport_host" not in result.stdout
    assert "HostKeyAlias=msm3-wifi.lan" in result.stdout
    assert "shag@169.254.251.195:/Users/shag/.omlx/models/BAAI/bge-small-en-v1.5/" in result.stdout


def test_artifact_sync_falls_back_to_management_host_when_fabric_is_unresolved(
    tmp_path: Path,
    monkeypatch,
) -> None:
    repo = tmp_path
    _write_runtime_config(repo)

    import thunder_forge.cli as cli_module
    import thunder_forge.cluster.config as config_module
    import thunder_forge.cluster.fabric as fabric_module

    monkeypatch.setattr(config_module, "find_repo_root", lambda: repo)
    monkeypatch.setattr(
        cli_module,
        "probe_artifact_presence",
        lambda *, repo_id, node_host, node_home_dir, studio_omlx_models_dir=None: ArtifactPresence(
            studio_omlx_model_dir=True,
            node_omlx_model_dir=False,
        ),
    )
    monkeypatch.setattr(
        fabric_module,
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
    assert "fabric_fallback: dynamic probe unresolved" in result.stdout
    assert "shag@msm3-wifi.lan:/Users/shag/.omlx/models/BAAI/bge-small-en-v1.5/" in result.stdout


def test_artifact_sync_can_force_management_host_when_fabric_host_is_configured(tmp_path: Path, monkeypatch) -> None:
    repo = tmp_path
    _write_runtime_config(repo)

    import thunder_forge.cli as cli_module
    import thunder_forge.cluster.config as config_module

    monkeypatch.setattr(config_module, "find_repo_root", lambda: repo)
    monkeypatch.setattr(
        cli_module,
        "probe_artifact_presence",
        lambda *, repo_id, node_host, node_home_dir, studio_omlx_models_dir=None: ArtifactPresence(
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
            "--management",
            "--dry-run",
        ],
    )

    assert result.exit_code == 0
    assert "transport_host: msm3-wifi.lan" in result.stdout
    assert "resolved_transport_host" not in result.stdout
    assert "shag@msm3-wifi.lan:/Users/shag/.omlx/models/BAAI/bge-small-en-v1.5/" in result.stdout


def test_artifact_sync_without_fabric_host_uses_management_even_when_probe_would_work(
    tmp_path: Path,
    monkeypatch,
) -> None:
    repo = tmp_path
    _write_runtime_config(repo, fabric_host=False)

    import thunder_forge.cli as cli_module
    import thunder_forge.cluster.config as config_module
    import thunder_forge.cluster.fabric as fabric_module

    monkeypatch.setattr(config_module, "find_repo_root", lambda: repo)
    monkeypatch.setattr(
        cli_module,
        "probe_artifact_presence",
        lambda *, repo_id, node_host, node_home_dir, studio_omlx_models_dir=None: ArtifactPresence(
            studio_omlx_model_dir=True,
            node_omlx_model_dir=False,
        ),
    )
    monkeypatch.setattr(
        fabric_module,
        "discover_link_local_fabric_host",
        lambda *, management_host, node_user: "169.254.251.197",
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
    assert "resolved_transport_host" not in result.stdout
    assert "shag@msm3-wifi.lan:/Users/shag/.omlx/models/BAAI/bge-small-en-v1.5/" in result.stdout


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
        lambda *, repo_id, node_host, node_home_dir, studio_omlx_models_dir=None: ArtifactPresence(
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

    def fake_run_artifact_download(plan, *, timeout, progress_callback):
        calls.append((plan, timeout, progress_callback))
        progress_callback(
            {
                "status": "downloading",
                "progress": 50.0,
                "downloaded_size": 1024**3,
                "total_size": 2 * 1024**3,
            }
        )
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
    assert "download_progress: downloading 50.0% (1.0 GB / 2.0 GB)" in result.stdout
    assert "status: downloaded" in result.stdout
    assert calls[0][0].destination == "~/.omlx/models/mlx-community/Qwen3-1.7B-4bit"
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
        lambda *, repo_id, node_host, node_home_dir, studio_omlx_models_dir=None: ArtifactPresence(
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
