"""CLI tests for Thunder Forge config commands."""

from pathlib import Path
from textwrap import dedent

from typer.testing import CliRunner

from thunder_forge.cli import app

runner = CliRunner()


def test_config_lint_reports_errors_and_warnings(tmp_path: Path, monkeypatch) -> None:
    repo = tmp_path
    (repo / "tfconfig.yaml").write_text(
        dedent("""\
            models:
              memory-bf16:
                source: { repo: mlx-community/gpt-oss-20b-mxfp4-bf16 }
                benchmark_only: true
                runtime_model_id: gpt-oss-20b-mxfp4-bf16
            nodes:
              msm3:
                host: msm3-wifi.lan
                ram_gb: 128
                user: shag
                role: node
                runtime:
                  type: omlx
                  port: 8018
                models:
                  - memory-bf16
                  - missing
        """)
    )

    import thunder_forge.cluster.config as config_module

    monkeypatch.setattr(config_module, "find_repo_root", lambda: repo)

    result = runner.invoke(app, ["config", "lint"])

    assert result.exit_code == 1
    assert "config: issues found" in result.stdout
    assert "error: nodes.msm3.models: unknown model 'missing'" in result.stdout
    assert "warning: nodes.msm3.models: benchmark-only model 'memory-bf16' is assigned to node" in result.stdout
    assert "warning: nodes.msm3.runtime: oMLX runtime binds 0.0.0.0 without trusted_network: true" in result.stdout


def test_config_lint_passes_clean_config(tmp_path: Path, monkeypatch) -> None:
    repo = tmp_path
    (repo / "tfconfig.yaml").write_text(
        dedent("""\
            models:
              memory:
                source: { repo: mlx-community/gpt-oss-20b-MXFP4-Q8 }
                runtime_model_id: gpt-oss-20b-MXFP4-Q8
            nodes:
              msm3:
                host: msm3-wifi.lan
                ram_gb: 128
                user: shag
                role: node
                runtime:
                  type: omlx
                  port: 8018
                  trusted_network: true
                models:
                  - memory
        """)
    )

    import thunder_forge.cluster.config as config_module

    monkeypatch.setattr(config_module, "find_repo_root", lambda: repo)

    result = runner.invoke(app, ["config", "lint"])

    assert result.exit_code == 0
    assert "config: ok" in result.stdout
