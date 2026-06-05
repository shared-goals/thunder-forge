"""Tests for remote cache command builders used by CLI workflows."""

from __future__ import annotations

from thunder_forge.cluster.config import Node
from thunder_forge.cluster.remote_cache import (
    cache_hub_setup_command,
    remote_artifact_download_command,
    remote_cache_sync_command,
    remote_transport_plan_probe_command,
)


def test_cache_hub_setup_command_uses_omlx_models_dir_env_fallback() -> None:
    command = cache_hub_setup_command()

    assert 'CACHE_DIR="${TF_CACHE_OMLX_MODELS_DIR:-$HOME/.omlx/models}"' in command
    assert "mkdir -p" in command


def test_remote_artifact_download_command_runs_downloader_helper() -> None:
    command = remote_artifact_download_command(
        repo_id="mlx-community/Qwen3-1.7B-4bit",
        model_dir_name="mlx-community/Qwen3-1.7B-4bit",
        timeout=7200,
    )

    assert command.startswith("python3 -u - <<'PY'")
    assert "base64.b64decode" in command
    assert "/admin/api/hf/download" in command


def test_remote_artifact_download_command_keeps_progress_bucket_update_inside_print_branch() -> None:
    command = remote_artifact_download_command(
        repo_id="mlx-community/Qwen3-1.7B-4bit",
        model_dir_name="mlx-community/Qwen3-1.7B-4bit",
        timeout=7200,
    )

    assert (
        "        if bucket != last_bucket:\n"
        "            print(f'download_progress: {status} {progress:.1f}%')\n"
        "            last_bucket = bucket\n"
        "        if status == 'completed':"
    ) in command


def test_remote_cache_sync_command_builds_batchmode_rsync_plan() -> None:
    node = Node(host="infer-01.lan", ram_gb=128, user="shag")

    source_path, destination, command = remote_cache_sync_command(
        repo_id="BAAI/bge-small-en-v1.5",
        runtime_node=node,
        node_home_dir="/Users/shag",
        transport_host="169.254.1.2",
        ssh_host_key_alias="infer-01.lan",
    )

    assert source_path == "${TF_CACHE_OMLX_MODELS_DIR:-$HOME/.omlx/models}/BAAI/bge-small-en-v1.5/"
    assert destination == "shag@169.254.1.2:/Users/shag/.omlx/models/BAAI/bge-small-en-v1.5/"
    assert "BatchMode=yes" in command
    assert "--exclude .cache/" in command
    assert "HostKeyAlias=infer-01.lan" in command


def test_remote_transport_plan_probe_command_decodes_payload() -> None:
    command = remote_transport_plan_probe_command(payload_b64="eyJmb28iOiJiYXIifQ==")

    assert command.startswith("python3 - <<'PY'")
    assert "payload = json.loads(base64.b64decode('eyJmb28iOiJiYXIifQ==').decode())" in command
    assert "__TF_TRANSPORT_PLAN__" in command
