"""Tests for oMLX node-level runtime helpers."""

from thunder_forge.cluster.config import Node, NodeRuntime
from thunder_forge.cluster.omlx import build_omlx_serve_command


def test_build_omlx_serve_command_omits_default_model_dir() -> None:
    node = Node(
        host="msm3-wifi.lan",
        ram_gb=128,
        user="shag",
        role="node",
        runtime=NodeRuntime(type="omlx", port=8018),
        home_dir="/Users/shag",
    )

    command = build_omlx_serve_command(node)

    assert command == "/Users/shag/.local/bin/omlx serve --host 0.0.0.0 --port 8018"
    assert "--model-dir" not in command


def test_build_omlx_serve_command_includes_explicit_model_dir_only_when_configured() -> None:
    node = Node(
        host="msm3-wifi.lan",
        ram_gb=128,
        user="shag",
        role="node",
        runtime=NodeRuntime(type="omlx", port=8018, model_dir="/Volumes/cache/omlx-models"),
        home_dir="/Users/shag",
    )

    command = build_omlx_serve_command(node)

    assert command == (
        "/Users/shag/.local/bin/omlx serve --host 0.0.0.0 --port 8018 "
        "--model-dir /Volumes/cache/omlx-models"
    )
