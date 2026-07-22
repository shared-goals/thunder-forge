"""CLI tests for Thunder Forge config commands."""

from pathlib import Path
from textwrap import dedent

from typer.testing import CliRunner

from thunder_forge.cli import app

runner = CliRunner()


def test_config_add_model_appends_unassigned_entry(tmp_path: Path, monkeypatch) -> None:
    repo = tmp_path
    (repo / "tfconfig.yaml").write_text(
        dedent("""\
            models:
              memory:
                source: { repo: mlx-community/gpt-oss-20b-MXFP4-Q8 }
                runtime_model_id: gpt-oss-20b-MXFP4-Q8
            nodes:
              gateway:
                host: gateway.lan
                ram_gb: 32
                user: shag
                roles: [gateway]
              infer-03:
                host: infer-03.lan
                ram_gb: 128
                user: shag
                roles: [inference]
                runtime:
                  type: omlx
                  port: 8018
                models:
                  - memory
        """)
    )

    import thunder_forge.cli as cli_module
    import thunder_forge.cluster.config as config_module

    monkeypatch.setattr(config_module, "find_repo_root", lambda: repo)
    monkeypatch.setattr(
        cli_module,
        "_fetch_hf_model_metadata",
        lambda repo_id, timeout: {
            "pipeline_tag": "image-text-to-text",
            "usedStorage": 28680724485,
            "config": {"quantization_config": {"bits": 8}, "text_config": {"max_position_embeddings": 262144}},
        },
    )

    result = runner.invoke(
        app,
        ["config", "add-model", "--repo", "mlx-community/Qwen3.6-27B-mxfp8", "--apply"],
    )

    assert result.exit_code == 0
    updated = (repo / "tfconfig.yaml").read_text()
    assert "qwen3-6-27b-mxfp8:" in updated
    assert 'repo: "mlx-community/Qwen3.6-27B-mxfp8"' in updated
    assert "runtime_model_id: Qwen3.6-27B-mxfp8" in updated
    assert "status: no node assignments were changed" in result.stdout


def test_config_add_model_rejects_existing_alias(tmp_path: Path, monkeypatch) -> None:
    repo = tmp_path
    (repo / "tfconfig.yaml").write_text(
        dedent("""\
            models:
              memory:
                source: { repo: mlx-community/gpt-oss-20b-MXFP4-Q8 }
                runtime_model_id: gpt-oss-20b-MXFP4-Q8
            nodes:
              gateway:
                host: gateway.lan
                ram_gb: 32
                user: shag
                roles: [gateway]
              infer-03:
                host: infer-03.lan
                ram_gb: 128
                user: shag
                roles: [inference]
                runtime:
                  type: omlx
                  port: 8018
                models:
                  - memory
        """)
    )

    import thunder_forge.cluster.config as config_module

    monkeypatch.setattr(config_module, "find_repo_root", lambda: repo)

    result = runner.invoke(
        app,
        ["config", "add-model", "--repo", "mlx-community/Qwen3.6-27B-mxfp8", "--alias", "memory", "--apply"],
    )

    assert result.exit_code == 1
    updated = (repo / "tfconfig.yaml").read_text()
    assert "Qwen3.6-27B-mxfp8" not in updated


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
              gateway:
                host: gateway.lan
                ram_gb: 32
                user: shag
                roles: [gateway]
              infer-03:
                host: infer-03.lan
                ram_gb: 128
                user: shag
                roles: [inference]
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
    assert "error: nodes.infer-03.models: unknown model 'missing'" in result.stdout
    assert "warning: nodes.infer-03.models: benchmark-only model 'memory-bf16' is assigned to node" in result.stdout
    assert "warning: nodes.infer-03.runtime: oMLX runtime binds 0.0.0.0 without trusted_network: true" in result.stdout


def test_config_lint_passes_clean_config(tmp_path: Path, monkeypatch) -> None:
    repo = tmp_path
    (repo / "tfconfig.yaml").write_text(
        dedent("""\
            models:
              memory:
                source: { repo: mlx-community/gpt-oss-20b-MXFP4-Q8 }
                runtime_model_id: gpt-oss-20b-MXFP4-Q8
            nodes:
              gateway:
                host: gateway.lan
                ram_gb: 32
                user: shag
                roles: [gateway]
              infer-03:
                host: infer-03.lan
                ram_gb: 128
                user: shag
                roles: [inference]
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
