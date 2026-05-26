"""CLI tests for artifact readiness commands."""

from pathlib import Path
from textwrap import dedent

from typer.testing import CliRunner

from thunder_forge.cli import app
from thunder_forge.cluster.artifacts import ArtifactPresence

runner = CliRunner()


def test_artifact_status_prints_readiness_plan(tmp_path: Path, monkeypatch) -> None:
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

    monkeypatch.setattr(config_module, "find_repo_root", lambda: repo)
    monkeypatch.setattr(
        cli_module,
        "probe_artifact_presence",
        lambda *, repo_id, node_host, node_home_dir, studio_hf_home: ArtifactPresence(
            studio_hf_cache=True,
            node_hf_cache=False,
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
    assert "studio_hf_cache: present" in result.stdout
    assert "node_hf_cache: missing" in result.stdout
    assert "node_omlx_model_dir: missing" in result.stdout
    assert "ready: no" in result.stdout
    assert "next_actions:" in result.stdout
    assert "- sync_to_node" in result.stdout
    assert "/Users/shag/.omlx/models/mlx-community/gpt-oss-20b-MXFP4-Q8" in result.stdout
