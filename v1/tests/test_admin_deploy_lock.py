"""Tests for gateway deploy lock file parsing and management."""

import time


def test_parse_lock_file_content():
    from thunder_forge.cluster.deploy import parse_lock_file

    content = f"PID:12345\nHEARTBEAT:{int(time.time())}"
    lock = parse_lock_file(content)
    assert lock["pid"] == 12345
    assert isinstance(lock["heartbeat"], int)


def test_parse_lock_file_empty():
    from thunder_forge.cluster.deploy import parse_lock_file

    assert parse_lock_file("") is None
    assert parse_lock_file(None) is None


def test_format_lock_file():
    from thunder_forge.cluster.deploy import format_lock_file

    content = format_lock_file(12345)
    assert "PID:12345" in content
    assert "HEARTBEAT:" in content


def test_build_gateway_command_resolves_uv_without_hardcoded_local_path():
    from thunder_admin.deploy import build_gateway_command

    command = build_gateway_command("health", "--skip-preflight", tf_dir="/home/serpo/thunder-forge")

    assert "command -v uv" in command
    assert "/opt/homebrew/bin/uv" in command
    assert "uv_run run thunder-forge health --skip-preflight" in command
    assert "cd /home/serpo/thunder-forge" in command
    assert "~/.local/bin/uv" not in command


def test_build_gateway_command_quotes_arguments():
    from thunder_admin.deploy import build_gateway_command

    command = build_gateway_command("restart-services", "--node", "node one", tf_dir="/tmp/thunder forge")

    assert "cd '/tmp/thunder forge'" in command
    assert "uv_run run thunder-forge restart-services --node 'node one'" in command
