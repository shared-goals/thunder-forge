"""CLI tests for oMLX artifact readiness commands."""

from pathlib import Path
from textwrap import dedent

from typer.testing import CliRunner

from thunder_forge.cli import app
from thunder_forge.cluster.artifacts import CACHE_OMLX_MODELS_DIR_ENV, ArtifactPresence
from thunder_forge.cluster.services import LaunchdServiceResult

runner = CliRunner()


def _write_runtime_config(repo: Path, *, fabric_host: bool = True) -> None:
    config_dir = repo / "configs"
    config_dir.mkdir()
    fabric_line = "                fabric_host: true\n" if fabric_host else ""
    (repo / "tfconfig.yaml").write_text(
        dedent(f"""\
            models: {{}}
            nodes:
              infer-01:
                host: infer-01.lan
{fabric_line}                ram_gb: 128
                user: shag
                roles: [inference]
                home_dir: /Users/shag
                runtime:
                  type: omlx
                  port: 8018
        """)
    )


def _write_runtime_config_with_models(repo: Path, *, fabric_host: bool = False) -> None:
    config_dir = repo / "configs"
    config_dir.mkdir()
    fabric_line = "        fabric_host: true\n" if fabric_host else ""
    (repo / "tfconfig.yaml").write_text(
        f"""models:
    memory:
        source:
            repo: mlx-community/gpt-oss-20b-MXFP4-Q8
    coder:
        source:
            repo: mlx-community/Qwen3-Coder-Next-4bit
nodes:
    infer-01:
        host: infer-01.lan
{fabric_line}        ram_gb: 128
        user: shag
        roles: [inference]
        home_dir: /Users/shag
        runtime:
            type: omlx
            port: 8018
        models:
            - memory
            - coder
"""
    )


def _presence_output(cmd: str, *, complete: bool) -> str:
    """Fabricate TFPRESENCE marker lines for a batched presence probe command."""
    names = __import__("re").findall(r"\bMODEL_DIR='([^']+)'", cmd) or __import__(
        "re"
    ).findall(r"\bMODEL_DIR=([A-Za-z0-9._/-]+)", cmd)
    flag = "1" if complete else "0"
    return "".join(f"TFPRESENCE|{name}={flag}\n" for name in names)


def _write_runtime_config_with_remote_cache(
    repo: Path,
    *,
    cache_host: str = "studio.lan",
    seed_host: str | None = None,
    extra_models: str = "",
) -> None:
    config_dir = repo / "configs"
    config_dir.mkdir()
    seed_block = (
        f"""    seed-02:
        host: {seed_host}
        ram_gb: 64
        user: shag
        roles: [inference]
        home_dir: /Users/shag
        fabric_host: true
        runtime:
            type: omlx
            port: 8019
        models:
            - memory
"""
        if seed_host
        else ""
    )
    (repo / "tfconfig.yaml").write_text(
        f"""models:
    memory:
        source:
            repo: mlx-community/gpt-oss-20b-MXFP4-Q8
{extra_models}nodes:
    rock:
        host: rock.lan
        ram_gb: 32
        user: shag
        roles: [gateway]
    studio:
        host: {cache_host}
        ram_gb: 64
        user: shag
        roles: [cache]
    infer-01:
        host: infer-01.lan
        ram_gb: 128
        user: shag
        roles: [inference]
        home_dir: /Users/shag
        runtime:
            type: omlx
            port: 8018
        models:
            - memory
            - big
            - small
{seed_block}"""
    )


def _write_runtime_config_with_remote_cache_and_fabric(repo: Path, *, cache_host: str = "studio.lan") -> None:
    config_dir = repo / "configs"
    config_dir.mkdir()
    (repo / "tfconfig.yaml").write_text(
        f"""models:
    memory:
        source:
            repo: mlx-community/gpt-oss-20b-MXFP4-Q8
nodes:
    rock:
        host: rock.lan
        ram_gb: 32
        user: shag
        roles: [gateway]
    studio:
        host: {cache_host}
        ram_gb: 64
        user: shag
        roles: [cache]
    infer-01:
        host: infer-01.lan
        fabric_host: true
        ram_gb: 128
        user: shag
        roles: [inference]
        home_dir: /Users/shag
        runtime:
            type: omlx
            port: 8018
        models:
            - memory
"""
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
        lambda *, repo_id, node_user, node_host, node_home_dir, cache_omlx_models_dir=None: ArtifactPresence(
            cache_omlx_model_dir=True,
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
            "infer-01",
        ],
    )

    assert result.exit_code == 0
    assert "model: mlx-community/gpt-oss-20b-MXFP4-Q8" in result.stdout
    assert "node: infer-01" in result.stdout
    assert "management_host: infer-01.lan" in result.stdout
    assert "model_dir_name: mlx-community/gpt-oss-20b-MXFP4-Q8" in result.stdout
    assert "runtime_model_id: gpt-oss-20b-MXFP4-Q8" in result.stdout
    assert "cache_omlx_model_dir_path: ~/.omlx/models/mlx-community/gpt-oss-20b-MXFP4-Q8" in result.stdout
    assert "node_omlx_model_dir_path: /Users/shag/.omlx/models/mlx-community/gpt-oss-20b-MXFP4-Q8" in result.stdout
    assert "cache_omlx_model_dir: ready" in result.stdout
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
    assert "action: download_to_cache_omlx" in result.stdout
    assert "mode: dry-run" in result.stdout
    assert "omlx serve" in result.stdout
    assert "/admin/api/hf/download" in result.stdout
    assert "mlx-community/Qwen3-1.7B-4bit" in result.stdout
    assert ".cache/huggingface" not in result.stdout


def test_artifact_download_dry_run_uses_cache_omlx_dir_env(tmp_path: Path, monkeypatch) -> None:
    repo = tmp_path
    _write_runtime_config(repo)
    cache_omlx_models_dir = str(tmp_path / "cache" / ".omlx" / "models")

    import thunder_forge.cluster.config as config_module

    monkeypatch.setattr(config_module, "find_repo_root", lambda: repo)
    monkeypatch.setenv(CACHE_OMLX_MODELS_DIR_ENV, cache_omlx_models_dir)

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
    assert f"destination: {cache_omlx_models_dir}/mlx-community/Qwen3-1.7B-4bit" in result.stdout
    assert f"--model-dir {cache_omlx_models_dir}" in result.stdout


def test_artifact_download_apply_dispatches_to_remote_cache_host(tmp_path: Path, monkeypatch) -> None:
    repo = tmp_path
    _write_runtime_config_with_remote_cache(repo, cache_host="remote-cache.lan")
    calls = []

    import subprocess

    import thunder_forge.cli as cli_module
    import thunder_forge.cluster.config as config_module

    monkeypatch.setattr(config_module, "find_repo_root", lambda: repo)
    monkeypatch.setenv(CACHE_OMLX_MODELS_DIR_ENV, "/tmp/local-cache-path")

    def fake_ssh_run(user, ip, cmd, **kwargs):
        calls.append((user, ip, cmd, kwargs))
        stdout = _presence_output(cmd, complete=True) if "TFPRESENCE|" in cmd else ""
        return subprocess.CompletedProcess(args=cmd, returncode=0, stdout=stdout, stderr="")

    monkeypatch.setattr(cli_module, "ssh_run", fake_ssh_run)

    result = runner.invoke(
        app,
        [
            "artifact",
            "download",
            "--model",
            "mlx-community/Qwen3-1.7B-4bit",
            "--apply",
        ],
    )

    assert result.exit_code == 0
    assert "cache_exec: remote studio (remote-cache.lan)" in result.stdout
    assert "destination: ${TF_CACHE_OMLX_MODELS_DIR:-$HOME/.omlx/models}/mlx-community/Qwen3-1.7B-4bit" in result.stdout
    assert "/tmp/local-cache-path" not in result.stdout
    assert calls[0][0] == "shag"
    assert calls[0][1] == "remote-cache.lan"
    assert "TF_CACHE_REMOTE_EXEC=1" not in calls[0][2]
    assert "python3 -u - <<'PY'" in calls[0][2]
    assert "omlx" in calls[0][2]


def test_artifact_status_checks_remote_cache_host_over_ssh(tmp_path: Path, monkeypatch) -> None:
    repo = tmp_path
    _write_runtime_config_with_remote_cache(repo, cache_host="remote-cache.lan")
    calls = []

    import subprocess

    import thunder_forge.cli as cli_module
    import thunder_forge.cluster.artifacts as artifacts_module
    import thunder_forge.cluster.config as config_module

    monkeypatch.setattr(config_module, "find_repo_root", lambda: repo)
    monkeypatch.setattr(
        artifacts_module,
        "_remote_artifact_complete",
        lambda *, user, host, path: True,
    )

    def fake_ssh_run(user, ip, cmd, **kwargs):
        calls.append((user, ip, cmd, kwargs))
        stdout = _presence_output(cmd, complete=True) if "TFPRESENCE|" in cmd else ""
        return subprocess.CompletedProcess(args=cmd, returncode=0, stdout=stdout, stderr="")

    monkeypatch.setattr(cli_module, "ssh_run", fake_ssh_run)

    result = runner.invoke(
        app,
        [
            "artifact",
            "status",
            "--model",
            "mlx-community/gpt-oss-20b-MXFP4-Q8",
            "--node",
            "infer-01",
        ],
    )

    assert result.exit_code == 0
    assert calls[0][0] == "shag"
    assert calls[0][1] == "remote-cache.lan"
    assert "TF_CACHE_REMOTE_EXEC=1" not in calls[0][2]
    assert "test -d" in calls[0][2]
    assert "cache_omlx_model_dir: ready" in result.stdout


def test_artifact_sync_dispatches_to_remote_cache_host(tmp_path: Path, monkeypatch) -> None:
    repo = tmp_path
    _write_runtime_config_with_remote_cache(repo, cache_host="remote-cache.lan")
    calls = []

    import subprocess

    import thunder_forge.cli as cli_module
    import thunder_forge.cluster.config as config_module

    monkeypatch.setattr(config_module, "find_repo_root", lambda: repo)

    def fake_ssh_run(user, ip, cmd, **kwargs):
        calls.append((user, ip, cmd, kwargs))
        return subprocess.CompletedProcess(args=cmd, returncode=0, stdout="", stderr="")

    monkeypatch.setattr(cli_module, "ssh_run", fake_ssh_run)
    monkeypatch.setattr(
        cli_module,
        "probe_artifact_presence",
        lambda **kwargs: (_ for _ in ()).throw(AssertionError("local sync path should not run")),
    )

    result = runner.invoke(
        app,
        [
            "artifact",
            "sync",
            "--model",
            "mlx-community/gpt-oss-20b-MXFP4-Q8",
            "--node",
            "infer-01",
            "--dry-run",
        ],
    )

    assert result.exit_code == 0
    assert calls == []
    assert "transport_host: infer-01.lan" in result.stdout
    assert "mode: dry-run" in result.stdout


def test_artifact_sync_apply_runs_directly_on_remote_cache_host(tmp_path: Path, monkeypatch) -> None:
    repo = tmp_path
    _write_runtime_config_with_remote_cache(repo, cache_host="remote-cache.lan")
    calls = []

    import subprocess

    import thunder_forge.cli as cli_module
    import thunder_forge.cluster.config as config_module

    monkeypatch.setattr(config_module, "find_repo_root", lambda: repo)

    def fake_ssh_run(user, ip, cmd, **kwargs):
        calls.append((user, ip, cmd, kwargs))
        stdout = _presence_output(cmd, complete=True) if "TFPRESENCE|" in cmd else ""
        return subprocess.CompletedProcess(args=cmd, returncode=0, stdout=stdout, stderr="")

    monkeypatch.setattr(cli_module, "ssh_run", fake_ssh_run)
    monkeypatch.setattr(cli_module, "_is_local_host", lambda host: False)

    result = runner.invoke(
        app,
        [
            "artifact",
            "sync",
            "--model",
            "mlx-community/gpt-oss-20b-MXFP4-Q8",
            "--node",
            "infer-01",
            "--apply",
            "--timeout",
            "123",
        ],
    )

    assert result.exit_code == 0
    assert "cache_exec: remote studio (remote-cache.lan)" in result.stdout
    assert "hub: ready mlx-community/gpt-oss-20b-MXFP4-Q8" in result.stdout
    assert calls[0][0] == "shag"
    assert calls[0][1] == "remote-cache.lan"
    assert "MODEL_DIR" in calls[0][2]
    # hub and target-node presence probes run before the sync is dispatched
    assert ("shag", "infer-01.lan") in [(call[0], call[1]) for call in calls[1:]]
    sync_call = next(call for call in calls if "rsync -a" in call[2])
    assert sync_call[0] == "shag"
    assert sync_call[1] == "remote-cache.lan"
    assert "--exclude" in sync_call[2]
    assert ".cache/" in sync_call[2]
    assert "TF_CACHE_REMOTE_EXEC=1" not in sync_call[2]
    assert "mkdir -p" in sync_call[2]
    assert "destination already has complete oMLX model" in sync_call[2]
    assert "test -d /Users/shag/.omlx/models/mlx-community/gpt-oss-20b-MXFP4-Q8" in sync_call[2]
    assert "missing cache oMLX model dir" in sync_call[2]
    assert calls[0][3]["timeout"] == 30
    assert sync_call[3]["timeout"] == 123
    assert "status: synced" in result.stdout


def test_artifact_sync_apply_resolves_fabric_on_remote_cache_host(tmp_path: Path, monkeypatch) -> None:
    repo = tmp_path
    _write_runtime_config_with_remote_cache_and_fabric(repo, cache_host="remote-cache.lan")
    calls = []

    import subprocess

    import thunder_forge.cli as cli_module
    import thunder_forge.cluster.config as config_module

    monkeypatch.setattr(config_module, "find_repo_root", lambda: repo)
    monkeypatch.setattr(cli_module, "_is_local_host", lambda host: False)

    def fake_ssh_run(user, ip, cmd, **kwargs):
        calls.append((user, ip, cmd, kwargs))
        if "__TF_TRANSPORT_PLAN__" in cmd:
            return subprocess.CompletedProcess(
                args=cmd,
                returncode=0,
                stdout=(
                    '__TF_TRANSPORT_PLAN__{"error": "", '
                    '"fabric_fallback": "", '
                    '"management_host": "infer-01.lan", '
                    '"requested_transport": "auto", '
                    '"resolved_transport_host": "169.254.251.195", '
                    '"transport_host": "169.254.251.195"}\n'
                ),
                stderr="",
            )
        if "TFPRESENCE|" in cmd:
            return subprocess.CompletedProcess(
                args=cmd, returncode=0, stdout=_presence_output(cmd, complete=True), stderr=""
            )
        return subprocess.CompletedProcess(args=cmd, returncode=0, stdout="", stderr="")

    monkeypatch.setattr(cli_module, "ssh_run", fake_ssh_run)

    result = runner.invoke(
        app,
        [
            "artifact",
            "sync",
            "--model",
            "mlx-community/gpt-oss-20b-MXFP4-Q8",
            "--node",
            "infer-01",
            "--apply",
            "--timeout",
            "123",
        ],
    )

    assert result.exit_code == 0
    assert calls[0][0] == "shag"
    assert calls[0][1] == "remote-cache.lan"
    assert "__TF_TRANSPORT_PLAN__" in calls[0][2]
    assert "MODEL_DIR" in calls[1][2]
    sync_call = next(call for call in calls if "rsync -a" in call[2])
    assert sync_call[0] == "shag"
    assert sync_call[1] == "remote-cache.lan"
    assert "HostKeyAlias=infer-01.lan" in sync_call[2]
    assert "169.254.251.195" in sync_call[2]
    assert "transport_host: 169.254.251.195" in result.stdout
    assert "fabric_fallback" not in result.stdout
    assert "status: synced" in result.stdout


def test_artifact_sync_dry_run_prints_cache_to_node_plan(tmp_path: Path, monkeypatch) -> None:
    repo = tmp_path
    _write_runtime_config(repo, fabric_host=False)

    import thunder_forge.cli as cli_module
    import thunder_forge.cluster.config as config_module

    monkeypatch.setattr(config_module, "find_repo_root", lambda: repo)
    monkeypatch.setattr(
        cli_module,
        "probe_artifact_presence",
        lambda *, repo_id, node_user, node_host, node_home_dir, cache_omlx_models_dir=None: ArtifactPresence(
            cache_omlx_model_dir=True,
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
            "infer-01",
            "--dry-run",
        ],
    )

    assert result.exit_code == 0
    assert "mode: dry-run" in result.stdout
    assert "source: cache" in result.stdout
    assert "transport_host: infer-01.lan" in result.stdout
    assert "fabric_fallback" not in result.stdout
    assert "model_dir_name: BAAI/bge-small-en-v1.5" in result.stdout
    assert "runtime_model_id: bge-small-en-v1.5" in result.stdout
    assert "action: sync_to_node_omlx" in result.stdout
    assert "rsync" in result.stdout
    assert "--exclude .cache/" in result.stdout
    assert "/Users/shag/.omlx/models/BAAI/bge-small-en-v1.5/" in result.stdout
    assert "shag@infer-01.lan:/Users/shag/.omlx/models/BAAI/bge-small-en-v1.5/" in result.stdout
    assert ".cache/huggingface" not in result.stdout


def test_artifact_sync_without_model_syncs_all_node_models(tmp_path: Path, monkeypatch) -> None:
    repo = tmp_path
    _write_runtime_config_with_models(repo)

    import thunder_forge.cli as cli_module
    import thunder_forge.cluster.config as config_module

    monkeypatch.setattr(config_module, "find_repo_root", lambda: repo)
    monkeypatch.setattr(
        cli_module,
        "probe_artifact_presence",
        lambda *, repo_id, node_user, node_host, node_home_dir, cache_omlx_models_dir=None: ArtifactPresence(
            cache_omlx_model_dir=True,
            node_omlx_model_dir=False,
        ),
    )

    result = runner.invoke(
        app,
        [
            "artifact",
            "sync",
            "--node",
            "infer-01",
            "--dry-run",
        ],
    )

    assert result.exit_code == 0
    assert "sync_scope: node" in result.stdout
    assert "models: 2" in result.stdout
    assert "model: mlx-community/gpt-oss-20b-MXFP4-Q8" in result.stdout
    assert "model: mlx-community/Qwen3-Coder-Next-4bit" in result.stdout
    assert result.stdout.count("mode: dry-run") == 2
    assert "status: node sync dry-run complete" in result.stdout


def test_artifact_sync_dry_run_uses_cache_omlx_dir_env(tmp_path: Path, monkeypatch) -> None:
    repo = tmp_path
    _write_runtime_config(repo, fabric_host=False)
    cache_omlx_models_dir = str(tmp_path / "cache" / ".omlx" / "models")

    import thunder_forge.cli as cli_module
    import thunder_forge.cluster.config as config_module

    monkeypatch.setattr(config_module, "find_repo_root", lambda: repo)
    monkeypatch.setenv(CACHE_OMLX_MODELS_DIR_ENV, cache_omlx_models_dir)
    monkeypatch.setattr(
        cli_module,
        "probe_artifact_presence",
        lambda *, repo_id, node_user, node_host, node_home_dir, cache_omlx_models_dir=None: ArtifactPresence(
            cache_omlx_model_dir=True,
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
            "infer-01",
            "--dry-run",
        ],
    )

    assert result.exit_code == 0
    assert f"source_path: {cache_omlx_models_dir}/BAAI/bge-small-en-v1.5/" in result.stdout


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
        lambda *, repo_id, node_user, node_host, node_home_dir, cache_omlx_models_dir=None: ArtifactPresence(
            cache_omlx_model_dir=True,
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
            "infer-01",
            "--dry-run",
        ],
    )

    assert result.exit_code == 0
    assert "transport_host: 169.254.251.195" in result.stdout
    assert "resolved_transport_host" not in result.stdout
    assert "HostKeyAlias=infer-01.lan" in result.stdout
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
        lambda *, repo_id, node_user, node_host, node_home_dir, cache_omlx_models_dir=None: ArtifactPresence(
            cache_omlx_model_dir=True,
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
            "infer-01",
            "--dry-run",
        ],
    )

    assert result.exit_code == 0
    assert "transport_host: infer-01.lan" in result.stdout
    assert "fabric_fallback: dynamic probe unresolved" in result.stdout
    assert "shag@infer-01.lan:/Users/shag/.omlx/models/BAAI/bge-small-en-v1.5/" in result.stdout


def test_artifact_sync_can_force_management_host_when_fabric_host_is_configured(tmp_path: Path, monkeypatch) -> None:
    repo = tmp_path
    _write_runtime_config(repo)

    import thunder_forge.cli as cli_module
    import thunder_forge.cluster.config as config_module

    monkeypatch.setattr(config_module, "find_repo_root", lambda: repo)
    monkeypatch.setattr(
        cli_module,
        "probe_artifact_presence",
        lambda *, repo_id, node_user, node_host, node_home_dir, cache_omlx_models_dir=None: ArtifactPresence(
            cache_omlx_model_dir=True,
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
            "infer-01",
            "--management",
            "--dry-run",
        ],
    )

    assert result.exit_code == 0
    assert "transport_host: infer-01.lan" in result.stdout
    assert "resolved_transport_host" not in result.stdout
    assert "shag@infer-01.lan:/Users/shag/.omlx/models/BAAI/bge-small-en-v1.5/" in result.stdout


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
        lambda *, repo_id, node_user, node_host, node_home_dir, cache_omlx_models_dir=None: ArtifactPresence(
            cache_omlx_model_dir=True,
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
            "infer-01",
            "--dry-run",
        ],
    )

    assert result.exit_code == 0
    assert "transport_host: infer-01.lan" in result.stdout
    assert "resolved_transport_host" not in result.stdout
    assert "shag@infer-01.lan:/Users/shag/.omlx/models/BAAI/bge-small-en-v1.5/" in result.stdout


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
        lambda *, repo_id, node_user, node_host, node_home_dir, cache_omlx_models_dir=None: ArtifactPresence(
            cache_omlx_model_dir=True,
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
            "infer-01",
            "--apply",
            "--timeout",
            "123",
        ],
    )

    assert result.exit_code == 0
    assert "status: synced" in result.stdout
    assert calls[0][1] == 123


def test_artifact_sync_apply_without_model_invokes_runner_for_each_node_model(
    tmp_path: Path,
    monkeypatch,
) -> None:
    repo = tmp_path
    _write_runtime_config_with_models(repo)
    calls = []

    import subprocess

    import thunder_forge.cli as cli_module
    import thunder_forge.cluster.config as config_module

    monkeypatch.setattr(config_module, "find_repo_root", lambda: repo)
    monkeypatch.setattr(
        cli_module,
        "probe_artifact_presence",
        lambda *, repo_id, node_user, node_host, node_home_dir, cache_omlx_models_dir=None: ArtifactPresence(
            cache_omlx_model_dir=True,
            node_omlx_model_dir=False,
        ),
    )

    def fake_run_artifact_sync(plan, *, timeout):
        calls.append((plan.repo_id, timeout))
        return subprocess.CompletedProcess(args=plan.rsync_args, returncode=0)

    monkeypatch.setattr(cli_module, "run_artifact_sync", fake_run_artifact_sync)

    result = runner.invoke(
        app,
        [
            "artifact",
            "sync",
            "--node",
            "infer-01",
            "--apply",
            "--timeout",
            "123",
        ],
    )

    assert result.exit_code == 0
    assert calls == [
        ("mlx-community/gpt-oss-20b-MXFP4-Q8", 123),
        ("mlx-community/Qwen3-Coder-Next-4bit", 123),
    ]
    assert result.stdout.count("status: synced") == 2
    assert "status: node sync complete" in result.stdout


def test_cluster_sync_uses_config_defaults_and_restarts_runtime(tmp_path: Path, monkeypatch) -> None:
    repo = tmp_path
    _write_runtime_config_with_models(repo)
    config_path = repo / "tfconfig.yaml"
    config_body = config_path.read_text().replace(
        "nodes:\n",
        """nodes:
    gateway-cache-01:
        host: gateway-cache-01.lan
        ram_gb: 64
        user: shag
        roles: [gateway, cache]
""",
        1,
    )
    config_path.write_text(
        """operations:
  sync:
    transport: management
    timeout: 123
    restart_runtime: true
"""
        + config_body.replace("        home_dir: /Users/shag\n", "")
    )
    calls = []
    restarts = []

    import subprocess

    import thunder_forge.cli as cli_module
    import thunder_forge.cluster.config as config_module

    monkeypatch.setattr(config_module, "find_repo_root", lambda: repo)
    monkeypatch.setenv("TF_CACHE_REMOTE_EXEC", "1")
    monkeypatch.setattr(
        cli_module,
        "probe_artifact_presence",
        lambda *, repo_id, node_user, node_host, node_home_dir, cache_omlx_models_dir=None: ArtifactPresence(
            cache_omlx_model_dir=True,
            node_omlx_model_dir=False,
        ),
    )

    def fake_run_artifact_sync(plan, *, timeout):
        calls.append((plan.repo_id, timeout))
        return subprocess.CompletedProcess(args=plan.rsync_args, returncode=0)

    def fake_run_omlx_daemon_restart(runtime_node, *, apply, timeout):
        restarts.append((runtime_node.host, runtime_node.home_dir, apply, timeout))
        return LaunchdServiceResult(
            service="omlx",
            label="com.thunder-forge.omlx-8018",
            plist_path="/Library/LaunchDaemons/com.thunder-forge.omlx-8018.plist",
            applied=True,
            service_label_verified=True,
            health_ok=True,
        )

    monkeypatch.setattr(cli_module, "run_artifact_sync", fake_run_artifact_sync)
    monkeypatch.setattr(cli_module, "run_omlx_daemon_restart", fake_run_omlx_daemon_restart)

    result = runner.invoke(app, ["cluster", "sync", "infer-01", "--apply"])

    assert result.exit_code == 0
    assert calls == [
        ("mlx-community/gpt-oss-20b-MXFP4-Q8", 123),
        ("mlx-community/Qwen3-Coder-Next-4bit", 123),
    ]
    assert restarts == [("infer-01.lan", "/Users/shag", True, 123)]
    assert "Thunder Forge cluster sync" in result.stdout
    assert "transport_host: infer-01.lan" in result.stdout
    assert "== Runtime Restart ==" in result.stdout
    assert "omlx: restarted com.thunder-forge.omlx-8018" in result.stdout
    assert "gateway_routes: unchanged" in result.stdout
    assert "run `make restart gateway-cache-01` only after changing model placement or node topology" in result.stdout


def test_cluster_sync_with_prune_removes_unassigned_node_cache_models(tmp_path: Path, monkeypatch) -> None:
    repo = tmp_path
    _write_runtime_config_with_models(repo)
    calls = []
    restarts = []
    removed: list[str] = []

    import subprocess

    import thunder_forge.cli as cli_module
    import thunder_forge.cluster.config as config_module

    monkeypatch.setattr(config_module, "find_repo_root", lambda: repo)
    monkeypatch.setenv("TF_CACHE_REMOTE_EXEC", "1")
    monkeypatch.setattr(
        cli_module,
        "probe_artifact_presence",
        lambda *, repo_id, node_user, node_host, node_home_dir, cache_omlx_models_dir=None: ArtifactPresence(
            cache_omlx_model_dir=True,
            node_omlx_model_dir=False,
        ),
    )

    def fake_run_artifact_sync(plan, *, timeout):
        calls.append((plan.repo_id, timeout))
        return subprocess.CompletedProcess(args=plan.rsync_args, returncode=0)

    def fake_ssh_run(user, ip, cmd, *, timeout=30, stream=False, shell=None, node_id=None, tty=False):
        if "find \"$MODELS_DIR\" -mindepth 2 -maxdepth 2 -type d" in cmd:
            return subprocess.CompletedProcess(
                args=[cmd],
                returncode=0,
                stdout="\n".join(
                    [
                        "/Users/shag/.omlx/models/mlx-community/gpt-oss-20b-MXFP4-Q8",
                        "/Users/shag/.omlx/models/mlx-community/Qwen3-Coder-Next-4bit",
                        "/Users/shag/.omlx/models/mlx-community/old-unused-model",
                    ]
                )
                + "\n",
                stderr="",
            )
        if cmd.startswith("rm -rf "):
            removed.append(cmd)
            return subprocess.CompletedProcess(args=[cmd], returncode=0, stdout="", stderr="")
        raise AssertionError(f"unexpected ssh command: {cmd}")

    def fake_run_omlx_daemon_restart(runtime_node, *, apply, timeout):
        restarts.append((runtime_node.host, runtime_node.home_dir, apply, timeout))
        return LaunchdServiceResult(
            service="omlx",
            label="com.thunder-forge.omlx-8018",
            plist_path="/Library/LaunchDaemons/com.thunder-forge.omlx-8018.plist",
            applied=True,
            service_label_verified=True,
            health_ok=True,
        )

    monkeypatch.setattr(cli_module, "run_artifact_sync", fake_run_artifact_sync)
    monkeypatch.setattr(cli_module, "ssh_run", fake_ssh_run)
    monkeypatch.setattr(cli_module, "run_omlx_daemon_restart", fake_run_omlx_daemon_restart)

    result = runner.invoke(app, ["cluster", "sync", "infer-01", "--apply", "--prune"])

    assert result.exit_code == 0
    assert calls == [
        ("mlx-community/gpt-oss-20b-MXFP4-Q8", 7200),
        ("mlx-community/Qwen3-Coder-Next-4bit", 7200),
    ]
    assert len(removed) == 1
    assert "old-unused-model" in removed[0]
    assert restarts == [("infer-01.lan", "/Users/shag", True, 7200)]
    assert "== Cache Prune ==" in result.stdout
    assert "status: pruned 1 model_dir(s)" in result.stdout
    assert "== Runtime Restart ==" in result.stdout


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
        lambda *, repo_id, node_user, node_host, node_home_dir, cache_omlx_models_dir=None: ArtifactPresence(
            cache_omlx_model_dir=True,
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
            "infer-01",
            "--apply",
        ],
    )

    assert result.exit_code == 23
    assert "sync failed with exit code 23" in result.stderr


def _plan_stub_stdout(cmd: str) -> str | None:
    """Transport-plan payload for stubbed probes: fabric resolved toward the hub."""
    if "__TF_TRANSPORT_PLAN__" not in cmd:
        return None
    return (
        '__TF_TRANSPORT_PLAN__{"error": "", "fabric_fallback": "", '
        '"management_host": "studio.lan", "requested_transport": "auto", '
        '"resolved_transport_host": "169.254.251.195", '
        '"transport_host": "169.254.251.195"}\n'
    )


def test_artifact_sync_stages_missing_model_from_seed_node_to_hub(tmp_path: Path, monkeypatch) -> None:
    """Hub and target both lack the model: stage seed-node -> hub, then sync hub -> node."""
    repo = tmp_path
    _write_runtime_config_with_remote_cache(repo, cache_host="studio.lan", seed_host="seed-02.lan")
    calls = []

    import subprocess

    import thunder_forge.cli as cli_module
    import thunder_forge.cluster.config as config_module

    monkeypatch.setattr(config_module, "find_repo_root", lambda: repo)
    monkeypatch.setattr(cli_module, "_is_local_host", lambda host: False)

    def fake_presence(*, node, model_dir_names, root_expr, timeout: int = 30):
        # Only the seed node has it; hub and sync target do not.
        return {name: node.host == "seed-02.lan" for name in model_dir_names}

    monkeypatch.setattr(cli_module, "_remote_models_presence", fake_presence)

    def fake_ssh_run(user, ip, cmd, **kwargs):
        calls.append((user, ip, cmd, kwargs))
        plan = _plan_stub_stdout(cmd)
        if plan is not None:
            return subprocess.CompletedProcess(args=cmd, returncode=0, stdout=plan, stderr="")
        if "printf %s" in cmd:
            return subprocess.CompletedProcess(
                args=cmd, returncode=0, stdout="/Users/shag/.omlx/models", stderr=""
            )
        return subprocess.CompletedProcess(args=cmd, returncode=0, stdout="", stderr="")

    monkeypatch.setattr(cli_module, "ssh_run", fake_ssh_run)

    result = runner.invoke(
        app,
        [
            "artifact",
            "sync",
            "--model",
            "mlx-community/gpt-oss-20b-MXFP4-Q8",
            "--node",
            "infer-01",
            "--apply",
        ],
    )

    assert result.exit_code == 0, result.stdout
    assert "hub_stage: mlx-community/gpt-oss-20b-MXFP4-Q8 from seed-02.lan (fabric)" in result.stdout
    stage_calls = [c for c in calls if "staged" in c[2] and "CACHE_ROOT" not in c[2]]
    assert stage_calls, "expected a staging rsync command"
    assert stage_calls[0][0] == "shag"
    assert stage_calls[0][1] == "seed-02.lan"
    assert "HostKeyAlias=studio.lan" in stage_calls[0][2]
    assert "169.254.251.195" in stage_calls[0][2]
    # Ordinary hub->node sync still runs afterwards.
    sync_calls = [c for c in calls if "missing cache oMLX model dir" in c[2]]
    assert sync_calls, "expected the ordinary hub-to-node sync to run"
    assert "status: synced" in result.stdout


def test_artifact_sync_skips_staging_when_target_node_already_has_model(
    tmp_path: Path, monkeypatch
) -> None:
    """Hub gaps for models the target already holds must never trigger a copy."""
    repo = tmp_path
    _write_runtime_config_with_remote_cache(repo, cache_host="studio.lan", seed_host="seed-02.lan")
    staged = []

    import thunder_forge.cli as cli_module
    import thunder_forge.cluster.config as config_module

    monkeypatch.setattr(config_module, "find_repo_root", lambda: repo)
    monkeypatch.setattr(cli_module, "_is_local_host", lambda host: False)

    def fake_presence(*, node, model_dir_names, root_expr, timeout: int = 30):
        # Target and seed nodes have everything; the hub is empty.
        return {name: node.host != "studio.lan" for name in model_dir_names}

    monkeypatch.setattr(cli_module, "_remote_models_presence", fake_presence)
    monkeypatch.setattr(cli_module, "_stage_model_to_hub", lambda **kwargs: staged.append(kwargs["repo_id"]))

    result = runner.invoke(
        app,
        [
            "artifact",
            "sync",
            "--model",
            "mlx-community/gpt-oss-20b-MXFP4-Q8",
            "--node",
            "infer-01",
            "--apply",
        ],
    )

    assert result.exit_code == 0, result.stdout
    assert staged == []
    assert "node: ready mlx-community/gpt-oss-20b-MXFP4-Q8" in result.stdout
    assert "hub: skip mlx-community/gpt-oss-20b-MXFP4-Q8 (not needed by infer-01.lan)" in result.stdout


def test_artifact_sync_stages_smallest_model_first(tmp_path: Path, monkeypatch) -> None:
    """Staging order is size-ascending so quick wins precede huge pulls."""
    repo = tmp_path
    _write_runtime_config_with_remote_cache(
        repo,
        cache_host="studio.lan",
        seed_host="seed-02.lan",
        extra_models="""    big:
        source:
            repo: big-org/Big-Model
        disk_gb: 106.3
    small:
        source:
            repo: small-org/Small-Model
        disk_gb: 1.1
""",
    )
    staged = []

    import thunder_forge.cli as cli_module
    import thunder_forge.cluster.config as config_module

    monkeypatch.setattr(config_module, "find_repo_root", lambda: repo)
    monkeypatch.setattr(cli_module, "_is_local_host", lambda host: False)
    monkeypatch.setattr(
        cli_module, "_remote_cache_models_dir", lambda cache_node: "/Users/shag/.omlx/models"
    )

    def fake_presence(*, node, model_dir_names, root_expr, timeout: int = 30):
        # Nothing exists anywhere except the seed node.
        return {name: node.host == "seed-02.lan" for name in model_dir_names}

    monkeypatch.setattr(cli_module, "_remote_models_presence", fake_presence)
    monkeypatch.setattr(
        cli_module,
        "_remote_transport_plan_probe",
        lambda probe_node, **kwargs: cli_module.TransportPlan(
            requested_transport="auto",
            management_host=str(kwargs["management_host"]),
            transport_host="169.254.251.195",
            resolved_transport_host="169.254.251.195",
        ),
    )
    monkeypatch.setattr(
        cli_module, "_stage_model_to_hub", lambda **kwargs: staged.append(kwargs["repo_id"])
    )
    monkeypatch.setattr(cli_module, "_run_artifact_sync_execution", lambda **kwargs: None)

    result = runner.invoke(app, ["artifact", "sync", "--node", "infer-01", "--apply"])

    assert result.exit_code == 0, result.stdout
    # ascending by configured size; memory has no disk_gb and sorts last
    assert staged == ["small-org/Small-Model", "big-org/Big-Model", "mlx-community/gpt-oss-20b-MXFP4-Q8"]


def test_artifact_sync_fails_fast_when_hub_and_nodes_missing_model(tmp_path: Path, monkeypatch) -> None:
    """Hub missing + no inference node has the model: fail with seeding guidance."""
    repo = tmp_path
    _write_runtime_config_with_remote_cache(repo, cache_host="studio.lan")
    calls = []

    import subprocess

    import thunder_forge.cli as cli_module
    import thunder_forge.cluster.config as config_module

    monkeypatch.setattr(config_module, "find_repo_root", lambda: repo)
    monkeypatch.setattr(cli_module, "_remote_artifact_complete_on_cache", lambda **kwargs: False)

    def fake_presence(*, node, model_dir_names, root_expr, timeout: int = 30):
        # Nothing holds the model anywhere in the cluster.
        return {name: False for name in model_dir_names}

    monkeypatch.setattr(cli_module, "_remote_models_presence", fake_presence)

    def fake_ssh_run(user, ip, cmd, **kwargs):
        calls.append((user, ip, cmd, kwargs))
        return subprocess.CompletedProcess(args=cmd, returncode=0, stdout="", stderr="")

    monkeypatch.setattr(cli_module, "ssh_run", fake_ssh_run)

    result = runner.invoke(
        app,
        [
            "artifact",
            "sync",
            "--model",
            "mlx-community/gpt-oss-20b-MXFP4-Q8",
            "--node",
            "infer-01",
            "--apply",
        ],
    )

    assert result.exit_code == 1
    assert "hub is missing mlx-community/gpt-oss-20b-MXFP4-Q8" in result.stderr
    assert "seed it first: make model mlx-community/gpt-oss-20b-MXFP4-Q8" in result.stderr
    # No sync script may run when nothing can stage the model.
    assert not [c for c in calls if "missing cache oMLX model dir" in c[2]]
