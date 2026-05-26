"""Tests for node-level runtime launchd install."""

from __future__ import annotations

from thunder_forge.cluster.config import RuntimeType
from thunder_forge.cluster.omlx import (
    Node,
    OmlxInstallResult,
    generate_launchd_plist,
)


def _make_runtime_node(home_dir="/Users/shag", port=8018, model_dir=None):
    from thunder_forge.cluster.config import NodeRuntime

    runtime = NodeRuntime(type=RuntimeType.OMLX, port=port, model_dir=model_dir)
    return Node(
        host="msm3-wifi.lan",
        fabric_host=None,
        ram_gb=128,
        user="shag",
        role="node",
        shell="zsh",
        home_dir=home_dir,
        runtime=runtime,
    )


def test_generate_launchd_plist_default_model_dir() -> None:
    node = _make_runtime_node()
    plist = generate_launchd_plist(node)
    expected_label = "com.thunder-forge.omlx-8018"
    expected_program_args = [
        "/Users/shag/.local/bin/omlx",
        "serve",
        "--host",
        "0.0.0.0",
        "--port",
        "8018",
    ]

    assert f"<string>{expected_label}</string>" in plist
    for arg in expected_program_args:
        assert f"<string>{arg}</string>" in plist
    # Default model dir is not pinned in the plist.
    assert "--model-dir" not in plist
    assert "<key>KeepAlive</key>" in plist
    assert "<key>RunAtLoad</key>" in plist
    # Logs go to a TF-managed location under the user home.
    assert "/Users/shag/Library/Logs/omlx-8018.stdout.log" in plist
    assert "/Users/shag/Library/Logs/omlx-8018.stderr.log" in plist


def test_generate_launchd_plist_explicit_model_dir() -> None:
    node = _make_runtime_node(model_dir="/opt/models")
    plist = generate_launchd_plist(node)

    assert "<string>--model-dir</string>" in plist
    assert "<string>/opt/models</string>" in plist


def test_generate_launchd_plist_errors_without_runtime() -> None:
    node = Node(
        host="msm3-wifi.lan",
        fabric_host=None,
        ram_gb=128,
        user="shag",
        role="node",
        shell="zsh",
        home_dir="/Users/shag",
        runtime=None,
    )

    import pytest

    with pytest.raises(ValueError):
        generate_launchd_plist(node)


def test_omlx_install_result_dry_run_describes_commands() -> None:
    result = OmlxInstallResult(
        node="msm3",
        plist_path="~/Library/LaunchAgents/com.thunder-forge.omlx-8018.plist",
        label="com.thunder-forge.omlx-8018",
        commands=[
            "launchctl bootout gui/501/com.thunder-forge.omlx-8018 2>/dev/null || true",
            "rm -f ~/Library/LaunchAgents/com.thunder-forge.omlx-8018.plist",
        ],
        applied=False,
    )
    assert not result.applied
    assert any("bootout" in c for c in result.commands)
    assert any("rm" in c for c in result.commands)


def test_omlx_install_result_apply_records_success() -> None:
    result = OmlxInstallResult(
        node="msm3",
        plist_path="~/Library/LaunchAgents/com.thunder-forge.omlx-8018.plist",
        label="com.thunder-forge.omlx-8018",
        commands=[],
        applied=True,
        service_label_verified=True,
        health_ok=True,
    )
    assert result.applied
    assert result.service_label_verified
    assert result.health_ok
    assert result.ok
