"""CLI tests for node-level runtime commands."""

from pathlib import Path
from textwrap import dedent

from typer.testing import CliRunner

from thunder_forge.cli import app

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
