"""Tests for oMLX artifact readiness planning."""

from thunder_forge.cluster.artifacts import (
    ArtifactPresence,
    ArtifactReadinessAction,
    build_artifact_download_plan,
    build_artifact_identity,
    build_artifact_readiness_plan,
    build_artifact_sync_plan,
    is_local_artifact_complete,
    omlx_model_dir_name,
)


def test_artifact_identity_preserves_hf_namespace_in_direct_child_dir() -> None:
    identity = build_artifact_identity("mlx-community/gpt-oss-20b-MXFP4-Q8")

    assert identity.namespace == "mlx-community"
    assert identity.repo_name == "gpt-oss-20b-MXFP4-Q8"
    assert identity.model_dir_name == "hf--mlx-community--gpt-oss-20b-MXFP4-Q8"
    assert identity.runtime_model_id == "hf--mlx-community--gpt-oss-20b-MXFP4-Q8"
    assert omlx_model_dir_name("mlx-community/gpt-oss-20b-MXFP4-Q8") == identity.model_dir_name


def test_artifact_plan_downloads_to_studio_omlx_when_studio_model_dir_missing() -> None:
    plan = build_artifact_readiness_plan(
        repo_id="mlx-community/gpt-oss-20b-MXFP4-Q8",
        node="msm3",
        node_home_dir="/Users/shag",
        presence=ArtifactPresence(studio_omlx_model_dir=False, node_omlx_model_dir=False),
    )

    assert plan.ready is False
    assert plan.actions == [ArtifactReadinessAction.DOWNLOAD_TO_STUDIO_OMLX]
    assert plan.model_dir_name == "hf--mlx-community--gpt-oss-20b-MXFP4-Q8"
    assert plan.runtime_model_id == "hf--mlx-community--gpt-oss-20b-MXFP4-Q8"
    assert plan.studio_omlx_model_dir == "~/.omlx/models/hf--mlx-community--gpt-oss-20b-MXFP4-Q8"
    assert plan.node_omlx_model_dir == "/Users/shag/.omlx/models/hf--mlx-community--gpt-oss-20b-MXFP4-Q8"


def test_artifact_plan_syncs_to_node_when_studio_omlx_model_dir_exists() -> None:
    plan = build_artifact_readiness_plan(
        repo_id="mlx-community/gpt-oss-20b-MXFP4-Q8",
        node="msm3",
        node_home_dir="/Users/shag",
        presence=ArtifactPresence(studio_omlx_model_dir=True, node_omlx_model_dir=False),
    )

    assert plan.ready is False
    assert plan.actions == [ArtifactReadinessAction.SYNC_TO_NODE_OMLX]


def test_artifact_plan_ready_when_studio_and_node_omlx_model_dirs_exist() -> None:
    plan = build_artifact_readiness_plan(
        repo_id="mlx-community/gpt-oss-20b-MXFP4-Q8",
        node="msm3",
        node_home_dir="/Users/shag",
        presence=ArtifactPresence(studio_omlx_model_dir=True, node_omlx_model_dir=True),
    )

    assert plan.ready is True
    assert plan.actions == []


def test_artifact_sync_plan_uses_omlx_model_dir_as_source_and_management_host_by_default() -> None:
    plan = build_artifact_sync_plan(
        repo_id="BAAI/bge-small-en-v1.5",
        node_user="shag",
        node_host="msm3-wifi.lan",
        node_home_dir="/Users/shag",
    )

    assert plan.model_dir_name == "hf--BAAI--bge-small-en-v1.5"
    assert plan.runtime_model_id == "hf--BAAI--bge-small-en-v1.5"
    assert plan.source_path == "/Users/shag/.omlx/models/hf--BAAI--bge-small-en-v1.5/"
    assert plan.destination == "shag@msm3-wifi.lan:/Users/shag/.omlx/models/hf--BAAI--bge-small-en-v1.5/"
    assert "shag@msm3-wifi.lan" in plan.command
    assert "hf--BAAI--bge-small-en-v1.5/" in plan.command
    assert ".cache/huggingface" not in plan.command
    assert plan.mkdir_args == [
        "ssh",
        "-o",
        "BatchMode=yes",
        "-o",
        "ConnectTimeout=8",
        "shag@msm3-wifi.lan",
        "mkdir",
        "-p",
        "/Users/shag/.omlx/models",
    ]
    assert plan.rsync_args[0] == "rsync"
    assert plan.rsync_args[-1] == plan.destination


def test_artifact_download_plan_downloads_directly_to_omlx_model_dir() -> None:
    plan = build_artifact_download_plan(repo_id="mlx-community/Qwen3-1.7B-4bit")

    assert plan.repo_id == "mlx-community/Qwen3-1.7B-4bit"
    assert plan.model_dir_name == "hf--mlx-community--Qwen3-1.7B-4bit"
    assert plan.runtime_model_id == "hf--mlx-community--Qwen3-1.7B-4bit"
    assert plan.destination == "~/.omlx/models/hf--mlx-community--Qwen3-1.7B-4bit"
    assert plan.args == [
        "uvx",
        "--from",
        "huggingface_hub",
        "hf",
        "download",
        "mlx-community/Qwen3-1.7B-4bit",
        "--local-dir",
        "/Users/shag/.omlx/models/hf--mlx-community--Qwen3-1.7B-4bit",
    ]
    assert ".cache/huggingface" not in plan.command


def test_artifact_download_runner_ignores_socks_proxy(monkeypatch) -> None:
    calls = []

    def fake_run(args, **kwargs):
        calls.append((args, kwargs))
        import subprocess

        return subprocess.CompletedProcess(args=args, returncode=0)

    import thunder_forge.cluster.artifacts as artifacts_module

    monkeypatch.setenv("ALL_PROXY", "socks5://127.0.0.1:1080")
    monkeypatch.setenv("all_proxy", "socks5://127.0.0.1:1080")
    monkeypatch.setenv("HTTP_PROXY", "http://127.0.0.1:8888")
    monkeypatch.setattr(artifacts_module.subprocess, "run", fake_run)

    plan = build_artifact_download_plan(repo_id="mlx-community/Qwen3-1.7B-4bit")
    artifacts_module.run_artifact_download(plan)

    env = calls[0][1]["env"]
    assert "ALL_PROXY" not in env
    assert "all_proxy" not in env
    assert env["HTTP_PROXY"] == "http://127.0.0.1:8888"


def test_local_artifact_complete_requires_config_and_weight_file(tmp_path) -> None:
    model_dir = tmp_path / "hf--mlx-community--gpt-oss-20b-MXFP4-Q8"
    model_dir.mkdir()

    assert is_local_artifact_complete(model_dir) is False

    (model_dir / "config.json").write_text("{}")
    assert is_local_artifact_complete(model_dir) is False

    (model_dir / "model.safetensors").write_text("weights")
    assert is_local_artifact_complete(model_dir) is True


def test_local_artifact_complete_rejects_incomplete_download_marker(tmp_path) -> None:
    model_dir = tmp_path / "hf--mlx-community--gpt-oss-20b-MXFP4-Q8"
    model_dir.mkdir()
    (model_dir / "config.json").write_text("{}")
    (model_dir / "model.safetensors").write_text("weights")
    (model_dir / "model.safetensors.incomplete").write_text("partial")

    assert is_local_artifact_complete(model_dir) is False


def test_artifact_sync_plan_can_use_fabric_host() -> None:
    plan = build_artifact_sync_plan(
        repo_id="BAAI/bge-small-en-v1.5",
        node_user="shag",
        node_host="169.254.251.195",
        node_home_dir="/Users/shag",
    )

    assert plan.destination.startswith("shag@169.254.251.195:")
    assert "shag@169.254.251.195" in plan.mkdir_args


def test_artifact_sync_plan_rejects_invalid_repo_id() -> None:
    try:
        build_artifact_sync_plan(
            repo_id="BAAI/bge;touch /tmp/pwned",
            node_user="shag",
            node_host="msm3-wifi.lan",
            node_home_dir="/Users/shag",
        )
    except ValueError as exc:
        assert "repo_id" in str(exc)
    else:
        raise AssertionError("Expected invalid repo_id to be rejected")
