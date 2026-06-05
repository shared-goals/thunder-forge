"""Tests for oMLX artifact readiness planning."""

from pathlib import Path

from thunder_forge.cluster.artifacts import (
    ArtifactPresence,
    ArtifactReadinessAction,
    build_artifact_download_plan,
    build_artifact_identity,
    build_artifact_readiness_plan,
    build_artifact_sync_plan,
    is_local_artifact_complete,
    omlx_model_dir_name,
    probe_artifact_presence,
)


def test_artifact_identity_uses_omlx_native_owner_model_dir() -> None:
    identity = build_artifact_identity("mlx-community/gpt-oss-20b-MXFP4-Q8")

    assert identity.namespace == "mlx-community"
    assert identity.repo_name == "gpt-oss-20b-MXFP4-Q8"
    assert identity.model_dir_name == "mlx-community/gpt-oss-20b-MXFP4-Q8"
    assert identity.runtime_model_id == "gpt-oss-20b-MXFP4-Q8"
    assert omlx_model_dir_name("mlx-community/gpt-oss-20b-MXFP4-Q8") == identity.model_dir_name


def test_artifact_plan_downloads_to_cache_omlx_when_cache_model_dir_missing() -> None:
    plan = build_artifact_readiness_plan(
        repo_id="mlx-community/gpt-oss-20b-MXFP4-Q8",
        node="infer-01",
        node_home_dir="/Users/shag",
        presence=ArtifactPresence(cache_omlx_model_dir=False, node_omlx_model_dir=False),
    )

    assert plan.ready is False
    assert plan.actions == [ArtifactReadinessAction.DOWNLOAD_TO_CACHE_OMLX]
    assert plan.model_dir_name == "mlx-community/gpt-oss-20b-MXFP4-Q8"
    assert plan.runtime_model_id == "gpt-oss-20b-MXFP4-Q8"
    assert plan.cache_omlx_model_dir == "~/.omlx/models/mlx-community/gpt-oss-20b-MXFP4-Q8"
    assert plan.node_omlx_model_dir == "/Users/shag/.omlx/models/mlx-community/gpt-oss-20b-MXFP4-Q8"


def test_artifact_plan_syncs_to_node_when_cache_omlx_model_dir_exists() -> None:
    plan = build_artifact_readiness_plan(
        repo_id="mlx-community/gpt-oss-20b-MXFP4-Q8",
        node="infer-01",
        node_home_dir="/Users/shag",
        presence=ArtifactPresence(cache_omlx_model_dir=True, node_omlx_model_dir=False),
    )

    assert plan.ready is False
    assert plan.actions == [ArtifactReadinessAction.SYNC_TO_NODE_OMLX]


def test_artifact_plan_ready_when_cache_and_node_omlx_model_dirs_exist() -> None:
    plan = build_artifact_readiness_plan(
        repo_id="mlx-community/gpt-oss-20b-MXFP4-Q8",
        node="infer-01",
        node_home_dir="/Users/shag",
        presence=ArtifactPresence(cache_omlx_model_dir=True, node_omlx_model_dir=True),
    )

    assert plan.ready is True
    assert plan.actions == []


def test_artifact_sync_plan_uses_omlx_model_dir_as_source_and_management_host_by_default() -> None:
    plan = build_artifact_sync_plan(
        repo_id="BAAI/bge-small-en-v1.5",
        node_user="shag",
        node_host="infer-01.lan",
        node_home_dir="/Users/shag",
    )
    expected_cache_models_dir = str(Path("~/.omlx/models").expanduser())

    assert plan.model_dir_name == "BAAI/bge-small-en-v1.5"
    assert plan.runtime_model_id == "bge-small-en-v1.5"
    assert plan.source_path == f"{expected_cache_models_dir}/BAAI/bge-small-en-v1.5/"
    assert plan.destination == "shag@infer-01.lan:/Users/shag/.omlx/models/BAAI/bge-small-en-v1.5/"
    assert "shag@infer-01.lan" in plan.command
    assert "BAAI/bge-small-en-v1.5/" in plan.command
    assert ".cache/huggingface" not in plan.command
    assert plan.mkdir_args == [
        "ssh",
        "-o",
        "BatchMode=yes",
        "-o",
        "ConnectTimeout=8",
        "shag@infer-01.lan",
        "mkdir",
        "-p",
        "/Users/shag/.omlx/models/BAAI",
    ]
    assert plan.rsync_args[0] == "rsync"
    assert "-a" in plan.rsync_args
    assert "-az" not in plan.rsync_args
    assert "--partial-dir=.rsync-partial" in plan.rsync_args
    assert plan.rsync_args[-1] == plan.destination


def test_artifact_download_plan_downloads_directly_to_omlx_model_dir() -> None:
    plan = build_artifact_download_plan(repo_id="mlx-community/Qwen3-1.7B-4bit")
    expected_cache_models_dir = str(Path("~/.omlx/models").expanduser())

    assert plan.repo_id == "mlx-community/Qwen3-1.7B-4bit"
    assert plan.model_dir_name == "mlx-community/Qwen3-1.7B-4bit"
    assert plan.runtime_model_id == "Qwen3-1.7B-4bit"
    assert plan.destination == "~/.omlx/models/mlx-community/Qwen3-1.7B-4bit"
    assert plan.args == [
        "omlx",
        "serve",
        "--host",
        "127.0.0.1",
        "--port",
        "8020",
        "--model-dir",
        expected_cache_models_dir,
        "--max-model-memory",
        "disabled",
    ]
    assert "/admin/api/hf/download" in plan.command
    assert ".cache/huggingface" not in plan.command


def test_artifact_download_env_loads_hf_token_from_dotenv(tmp_path, monkeypatch) -> None:
    import thunder_forge.cluster.artifacts as artifacts_module

    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("HF_TOKEN", raising=False)
    (tmp_path / ".env").write_text("HF_TOKEN=secret-from-dotenv\n")

    env = artifacts_module._env_without_socks_proxy()

    assert env["HF_TOKEN"] == "secret-from-dotenv"


def test_artifact_download_env_keeps_existing_hf_token(tmp_path, monkeypatch) -> None:
    import thunder_forge.cluster.artifacts as artifacts_module

    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("HF_TOKEN", "secret-from-process")
    (tmp_path / ".env").write_text("HF_TOKEN=secret-from-dotenv\n")

    env = artifacts_module._env_without_socks_proxy()

    assert env["HF_TOKEN"] == "secret-from-process"


def test_artifact_download_runner_ignores_socks_proxy(tmp_path, monkeypatch) -> None:
    import thunder_forge.cluster.artifacts as artifacts_module

    calls = []

    class FakePopen:
        returncode = None

        def __init__(self, args, **kwargs):
            calls.append((args, kwargs))

        def poll(self):
            return None

        def terminate(self):
            self.returncode = 0

        def wait(self, timeout):
            self.returncode = 0

    class FakeClient:
        def close(self):
            pass

    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("ALL_PROXY", "socks5://127.0.0.1:1080")
    monkeypatch.setenv("all_proxy", "socks5://127.0.0.1:1080")
    monkeypatch.setenv("HTTP_PROXY", "http://127.0.0.1:8888")
    monkeypatch.setattr(artifacts_module, "_omlx_server_ready", lambda base_url: False)
    monkeypatch.setattr(artifacts_module, "_wait_for_omlx_server", lambda base_url, process, timeout_seconds: None)
    monkeypatch.setattr(artifacts_module, "_omlx_admin_client", lambda base_url, api_key: FakeClient())
    monkeypatch.setattr(
        artifacts_module,
        "_start_omlx_hf_download",
        lambda base_url, repo_id, hf_token: {"task_id": "1"},
    )
    monkeypatch.setattr(
        artifacts_module,
        "_poll_omlx_hf_task",
        lambda base_url, task_id, repo_id, timeout_seconds, progress_callback=None: {
            "task_id": "1",
            "status": "completed",
        },
    )
    monkeypatch.setattr(artifacts_module.subprocess, "Popen", FakePopen)

    plan = build_artifact_download_plan(repo_id="mlx-community/Qwen3-1.7B-4bit")
    artifacts_module.run_artifact_download(plan)

    env = calls[0][1]["env"]
    assert "ALL_PROXY" not in env
    assert "all_proxy" not in env
    assert env["HTTP_PROXY"] == "http://127.0.0.1:8888"


def test_local_artifact_complete_requires_config_and_weight_file(tmp_path) -> None:
    model_dir = tmp_path / "mlx-community" / "gpt-oss-20b-MXFP4-Q8"
    model_dir.mkdir(parents=True)

    assert is_local_artifact_complete(model_dir) is False

    (model_dir / "config.json").write_text("{}")
    assert is_local_artifact_complete(model_dir) is False

    (model_dir / "model.safetensors").write_text("weights")
    assert is_local_artifact_complete(model_dir) is True


def test_local_artifact_complete_rejects_incomplete_download_marker(tmp_path) -> None:
    model_dir = tmp_path / "mlx-community" / "gpt-oss-20b-MXFP4-Q8"
    model_dir.mkdir(parents=True)
    (model_dir / "config.json").write_text("{}")
    (model_dir / "model.safetensors").write_text("weights")
    (model_dir / "model.safetensors.incomplete").write_text("partial")

    assert is_local_artifact_complete(model_dir) is False


def test_local_artifact_complete_rejects_rsync_partial_dir(tmp_path) -> None:
    model_dir = tmp_path / "BAAI" / "model"
    model_dir.mkdir(parents=True)
    (model_dir / "config.json").write_text("{}")
    (model_dir / "model.safetensors").write_text("weights")
    (model_dir / ".rsync-partial").mkdir()

    assert is_local_artifact_complete(model_dir) is False


def test_probe_artifact_presence_checks_node_as_configured_user(tmp_path, monkeypatch) -> None:
    import subprocess

    import thunder_forge.cluster.artifacts as artifacts_module

    cache_root = tmp_path / "cache"
    cache_model_dir = cache_root / "mlx-community" / "gpt-oss-20b-MXFP4-Q8"
    cache_model_dir.mkdir(parents=True)
    (cache_model_dir / "config.json").write_text("{}")
    (cache_model_dir / "model.safetensors").write_text("weights")
    calls: list[tuple[str, str, str]] = []

    def fake_ssh_run(user, ip, cmd, **kwargs):
        calls.append((user, ip, cmd))
        return subprocess.CompletedProcess(args=cmd, returncode=0, stdout="", stderr="")

    monkeypatch.setattr(artifacts_module, "ssh_run", fake_ssh_run)

    presence = probe_artifact_presence(
        repo_id="mlx-community/gpt-oss-20b-MXFP4-Q8",
        node_user="operator",
        node_host="infer-01.lan",
        node_home_dir="/Users/operator",
        cache_omlx_models_dir=str(cache_root),
    )

    assert presence.cache_omlx_model_dir is True
    assert presence.node_omlx_model_dir is True
    assert calls == [
        (
            "operator",
            "infer-01.lan",
            (
                "test -d /Users/operator/.omlx/models/mlx-community/gpt-oss-20b-MXFP4-Q8 && "
                "test -f /Users/operator/.omlx/models/mlx-community/gpt-oss-20b-MXFP4-Q8/config.json && "
                "test -z \"$(find /Users/operator/.omlx/models/mlx-community/gpt-oss-20b-MXFP4-Q8 "
                "-name '*.incomplete' -print -quit)\" && "
                "test ! -e /Users/operator/.omlx/models/mlx-community/gpt-oss-20b-MXFP4-Q8/.rsync-partial && "
                "test -n \"$(find /Users/operator/.omlx/models/mlx-community/gpt-oss-20b-MXFP4-Q8 "
                "\\( -name '*.safetensors' -o -name '*.bin' \\) -type f -print -quit)\""
            ),
        )
    ]


def test_artifact_sync_plan_can_use_fabric_host() -> None:
    plan = build_artifact_sync_plan(
        repo_id="BAAI/bge-small-en-v1.5",
        node_user="shag",
        node_host="169.254.251.195",
        node_home_dir="/Users/shag",
        ssh_host_key_alias="infer-01.lan",
    )

    assert plan.destination.startswith("shag@169.254.251.195:")
    assert "shag@169.254.251.195" in plan.mkdir_args
    assert "HostKeyAlias=infer-01.lan" in plan.mkdir_args
    assert any("HostKeyAlias=infer-01.lan" in arg for arg in plan.rsync_args)


def test_artifact_sync_plan_rejects_invalid_repo_id() -> None:
    try:
        build_artifact_sync_plan(
            repo_id="BAAI/bge;touch /tmp/pwned",
            node_user="shag",
            node_host="infer-01.lan",
            node_home_dir="/Users/shag",
        )
    except ValueError as exc:
        assert "repo_id" in str(exc)
    else:
        raise AssertionError("Expected invalid repo_id to be rejected")
