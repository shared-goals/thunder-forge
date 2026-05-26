"""CLI tests for node-level runtime commands."""

from pathlib import Path
from textwrap import dedent

from typer.testing import CliRunner

from thunder_forge.cli import app
from thunder_forge.cluster.omlx import OmlxHealthResult, OmlxSmokeResult

runner = CliRunner()


def test_runtime_start_dry_run_omits_default_model_dir(tmp_path: Path, monkeypatch) -> None:
    repo = tmp_path
    config_dir = repo / "configs"
    config_dir.mkdir()
    (config_dir / "node-assignments.yaml").write_text(
        dedent("""\
            models: {}
            nodes:
              msm3:
                host: msm3-wifi.lan
                fabric_host: msm3-fabric
                ram_gb: 128
                user: shag
                role: node
                home_dir: /Users/shag
                runtime:
                  type: omlx
                  port: 8018
            assignments: {}
        """)
    )

    import thunder_forge.cluster.config as config_module

    monkeypatch.setattr(config_module, "find_repo_root", lambda: repo)
    result = runner.invoke(app, ["runtime", "start", "--node", "msm3", "--dry-run"])

    assert result.exit_code == 0
    assert "node: msm3" in result.stdout
    assert "runtime: omlx" in result.stdout
    assert "management_host: msm3-wifi.lan" in result.stdout
    assert "fabric_host: msm3-fabric" in result.stdout
    assert "/Users/shag/.local/bin/omlx serve --host 0.0.0.0 --port 8018" in result.stdout
    assert "--model-dir" not in result.stdout


def test_runtime_start_apply_starts_remote_runtime(tmp_path: Path, monkeypatch) -> None:
    repo = tmp_path
    config_dir = repo / "configs"
    config_dir.mkdir()
    (config_dir / "node-assignments.yaml").write_text(
        dedent("""\
            models: {}
            nodes:
              msm3:
                host: msm3-wifi.lan
                fabric_host: msm3-fabric
                ram_gb: 128
                user: shag
                role: node
                home_dir: /Users/shag
                runtime:
                  type: omlx
                  port: 8018
            assignments: {}
        """)
    )

    import thunder_forge.cli as cli_module
    import thunder_forge.cluster.config as config_module
    from thunder_forge.cluster.omlx import OmlxStartResult

    monkeypatch.setattr(config_module, "find_repo_root", lambda: repo)
    monkeypatch.setattr(
        cli_module,
        "check_omlx_health",
        lambda base_url: OmlxHealthResult(base_url=base_url, health_ok=False, models_ok=False),
    )
    monkeypatch.setattr(
        cli_module,
        "run_omlx_runtime_start",
        lambda runtime_node, *, timeout: OmlxStartResult(returncode=0, pid="4242"),
    )

    result = runner.invoke(app, ["runtime", "start", "--node", "msm3", "--apply"])

    assert result.exit_code == 0
    assert "node: msm3" in result.stdout
    assert "command: /Users/shag/.local/bin/omlx serve --host 0.0.0.0 --port 8018" in result.stdout
    assert "pid: 4242" in result.stdout
    assert "status: started" in result.stdout


def test_runtime_start_apply_skips_when_runtime_is_already_healthy(tmp_path: Path, monkeypatch) -> None:
    repo = tmp_path
    config_dir = repo / "configs"
    config_dir.mkdir()
    (config_dir / "node-assignments.yaml").write_text(
        dedent("""\
            models: {}
            nodes:
              msm3:
                host: msm3-wifi.lan
                fabric_host: msm3-fabric
                ram_gb: 128
                user: shag
                role: node
                home_dir: /Users/shag
                runtime:
                  type: omlx
                  port: 8018
            assignments: {}
        """)
    )

    import thunder_forge.cli as cli_module
    import thunder_forge.cluster.config as config_module

    started = False

    def fake_start(runtime_node, *, timeout):
        nonlocal started
        started = True
        from thunder_forge.cluster.omlx import OmlxStartResult

        return OmlxStartResult(returncode=0, pid="4242")

    monkeypatch.setattr(config_module, "find_repo_root", lambda: repo)
    monkeypatch.setattr(
        cli_module,
        "check_omlx_health",
        lambda base_url: OmlxHealthResult(base_url=base_url, health_ok=True, models_ok=True),
    )
    monkeypatch.setattr(cli_module, "run_omlx_runtime_start", fake_start)

    result = runner.invoke(app, ["runtime", "start", "--node", "msm3", "--apply"])

    assert result.exit_code == 0
    assert started is False
    assert "status: already running" in result.stdout


def test_runtime_status_reports_omlx_health(tmp_path: Path, monkeypatch) -> None:
    repo = tmp_path
    config_dir = repo / "configs"
    config_dir.mkdir()
    (config_dir / "node-assignments.yaml").write_text(
        dedent("""\
            models: {}
            nodes:
              msm3:
                host: msm3-wifi.lan
                fabric_host: msm3-fabric
                ram_gb: 128
                user: shag
                role: node
                runtime:
                  type: omlx
                  port: 8018
            assignments: {}
        """)
    )

    import thunder_forge.cli as cli_module
    import thunder_forge.cluster.config as config_module

    monkeypatch.setattr(config_module, "find_repo_root", lambda: repo)
    monkeypatch.setattr(
        cli_module,
        "check_omlx_health",
        lambda base_url: OmlxHealthResult(
            base_url=base_url,
            health_ok=True,
            models_ok=True,
            status_ok=True,
            models=["mlx-community/test-model"],
        ),
        raising=False,
    )

    result = runner.invoke(app, ["runtime", "status", "--node", "msm3"])

    assert result.exit_code == 0
    assert "node: msm3" in result.stdout
    assert "runtime: omlx" in result.stdout
    assert "management_host: msm3-wifi.lan" in result.stdout
    assert "fabric_host: msm3-fabric" in result.stdout
    assert "base_url: http://msm3-wifi.lan:8018" in result.stdout
    assert "health: ok" in result.stdout
    assert "models: ok" in result.stdout
    assert "status: ok" in result.stdout
    assert "- mlx-community/test-model" in result.stdout


def test_runtime_smoke_reports_direct_chat_result(tmp_path: Path, monkeypatch) -> None:
    repo = tmp_path
    config_dir = repo / "configs"
    config_dir.mkdir()
    (config_dir / "node-assignments.yaml").write_text(
        dedent("""\
            models: {}
            nodes:
              msm3:
                host: msm3-wifi.lan
                fabric_host: msm3-fabric
                ram_gb: 128
                user: shag
                role: node
                runtime:
                  type: omlx
                  port: 8018
            assignments: {}
        """)
    )

    import thunder_forge.cli as cli_module
    import thunder_forge.cluster.config as config_module

    monkeypatch.setattr(config_module, "find_repo_root", lambda: repo)
    monkeypatch.setattr(
        cli_module,
        "smoke_omlx_chat",
        lambda base_url, *, model, prompt, timeout: OmlxSmokeResult(
            base_url=base_url,
            model=model,
            health_ok=True,
            models_ok=True,
            model_visible=True,
            chat_ok=True,
            models=[model],
            answer="pong",
            latency_ms=123,
        ),
        raising=False,
    )

    result = runner.invoke(
        app,
        [
            "runtime",
            "smoke",
            "--node",
            "msm3",
            "--model",
            "Qwen3-1.7B-4bit",
        ],
    )

    assert result.exit_code == 0
    assert "node: msm3" in result.stdout
    assert "base_url: http://msm3-wifi.lan:8018" in result.stdout
    assert "model: Qwen3-1.7B-4bit" in result.stdout
    assert "health: ok" in result.stdout
    assert "models: ok" in result.stdout
    assert "model_visible: yes" in result.stdout
    assert "chat: ok" in result.stdout
    assert "latency_ms: 123" in result.stdout
    assert "answer: pong" in result.stdout
