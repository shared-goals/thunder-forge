"""Tests for oMLX artifact readiness planning."""

from thunder_forge.cluster.artifacts import (
    ArtifactPresence,
    ArtifactReadinessAction,
    build_artifact_download_plan,
    build_artifact_readiness_plan,
    build_artifact_sync_plan,
    omlx_model_dir_name,
)


def test_omlx_model_dir_name_uses_omlx_default_flat_subdirectory() -> None:
    assert omlx_model_dir_name("mlx-community/gpt-oss-20b-MXFP4-Q8") == "gpt-oss-20b-MXFP4-Q8"


def test_artifact_plan_downloads_to_studio_omlx_when_studio_model_dir_missing() -> None:
    plan = build_artifact_readiness_plan(
        repo_id="mlx-community/gpt-oss-20b-MXFP4-Q8",
        node="msm3",
        node_home_dir="/Users/shag",
        presence=ArtifactPresence(studio_omlx_model_dir=False, node_omlx_model_dir=False),
    )

    assert plan.ready is False
    assert plan.actions == [ArtifactReadinessAction.DOWNLOAD_TO_STUDIO_OMLX]
    assert plan.studio_omlx_model_dir == "~/.omlx/models/gpt-oss-20b-MXFP4-Q8"
    assert plan.node_omlx_model_dir == "/Users/shag/.omlx/models/gpt-oss-20b-MXFP4-Q8"


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

    assert plan.source_path == "/Users/shag/.omlx/models/bge-small-en-v1.5/"
    assert plan.destination == "shag@msm3-wifi.lan:/Users/shag/.omlx/models/bge-small-en-v1.5/"
    assert "shag@msm3-wifi.lan" in plan.command
    assert "bge-small-en-v1.5/" in plan.command
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
    assert plan.model_dir_name == "Qwen3-1.7B-4bit"
    assert plan.destination == "~/.omlx/models/Qwen3-1.7B-4bit"
    assert plan.args == [
        "uvx",
        "--from",
        "huggingface_hub",
        "hf",
        "download",
        "mlx-community/Qwen3-1.7B-4bit",
        "--local-dir",
        "/Users/shag/.omlx/models/Qwen3-1.7B-4bit",
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


def test_artifact_sync_plan_can_use_fabric_host() -> None:
    plan = build_artifact_sync_plan(
        repo_id="BAAI/bge-small-en-v1.5",
        node_user="shag",
        node_host="msm3-fabric",
        node_home_dir="/Users/shag",
    )

    assert plan.destination.startswith("shag@msm3-fabric:")
    assert "shag@msm3-fabric" in plan.mkdir_args


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
