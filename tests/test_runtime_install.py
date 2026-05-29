"""Tests for node-level runtime launchd install."""

from __future__ import annotations

import subprocess

from thunder_forge.cluster.config import RuntimeType
from thunder_forge.cluster.omlx import (
    Node,
    OmlxHealthResult,
    OmlxInstallResult,
    generate_daemon_setup_script,
    generate_daemon_sudoers,
    generate_launchd_plist,
    run_omlx_daemon_restart,
    run_omlx_daemon_setup,
    run_omlx_install,
    run_omlx_process_restart,
    run_omlx_runtime_restart,
)


def _make_runtime_node(home_dir="/Users/shag", port=8018, model_dir=None):
    from thunder_forge.cluster.config import NodeRuntime

    runtime = NodeRuntime(type=RuntimeType.OMLX, port=port, model_dir=model_dir)
    return Node(
        host="msm3-wifi.lan",
        fabric_host=False,
        ram_gb=128,
        user="shag",
        role="inference",
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


def test_generate_launchd_plist_system_daemon_runs_as_node_user() -> None:
    plist = generate_launchd_plist(_make_runtime_node(), system_daemon=True)

    assert "<key>UserName</key>" in plist
    assert "<string>shag</string>" in plist
    assert "<key>EnvironmentVariables</key>" in plist
    assert "<key>HOME</key>" in plist
    assert "<string>/Users/shag</string>" in plist


def test_generate_launchd_plist_errors_without_runtime() -> None:
    node = Node(
        host="msm3-wifi.lan",
        fabric_host=False,
        ram_gb=128,
        user="shag",
        role="inference",
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


def test_run_omlx_install_writes_plist_before_bootstrap(monkeypatch) -> None:
    node = _make_runtime_node()
    calls: list[tuple[str, str]] = []

    def fake_ssh_run(user, ip, cmd, *, timeout):
        calls.append(("ssh", cmd))
        return subprocess.CompletedProcess(args=cmd, returncode=0, stdout="", stderr="")

    def fake_scp_content(user, ip, content, remote_path, *, shell=None):
        calls.append(("scp", remote_path))
        assert "com.thunder-forge.omlx-8018" in content
        return subprocess.CompletedProcess(args=remote_path, returncode=0, stdout="", stderr="")

    import thunder_forge.cluster.omlx as omlx_module

    monkeypatch.setattr(omlx_module, "ssh_run", fake_ssh_run)
    monkeypatch.setattr(omlx_module, "scp_content", fake_scp_content)
    monkeypatch.setattr(
        omlx_module,
        "check_omlx_health",
        lambda base_url, *, timeout: OmlxHealthResult(base_url=base_url, health_ok=True, models_ok=True),
    )

    result = run_omlx_install(node, apply=True)

    assert result.ok
    assert calls[0] == ("ssh", "mkdir -p ~/Library/LaunchAgents ~/Library/Logs")
    assert calls[1][1] == "launchctl bootout user/$(id -u)/com.thunder-forge.omlx-8018 2>/dev/null || true"
    assert calls[2] == ("ssh", "rm -f ~/Library/LaunchAgents/com.thunder-forge.omlx-8018.plist")
    assert calls[3] == ("scp", "~/Library/LaunchAgents/com.thunder-forge.omlx-8018.plist")
    assert "pkill" in calls[4][1]
    assert calls[5][1] == "launchctl bootstrap user/$(id -u) ~/Library/LaunchAgents/com.thunder-forge.omlx-8018.plist"
    assert "launchctl list" in calls[6][1]


def test_run_omlx_runtime_restart_dry_run_describes_commands() -> None:
    result = run_omlx_runtime_restart(_make_runtime_node(), apply=False)

    assert result.label == "com.thunder-forge.omlx-8018"
    assert not result.applied
    assert any("bootout" in c for c in result.commands)
    assert any("bootstrap" in c for c in result.commands)


def test_run_omlx_daemon_restart_dry_run_describes_sudo_commands() -> None:
    result = run_omlx_daemon_restart(_make_runtime_node(), apply=False)

    assert result.label == "com.thunder-forge.omlx-8018"
    assert result.plist_path == "/Library/LaunchDaemons/com.thunder-forge.omlx-8018.plist"
    assert result.staging_plist_path == "/Users/shag/.omlx/run/com.thunder-forge.omlx-8018.plist"
    assert not result.applied
    assert any("sudo -n /usr/bin/install" in c for c in result.commands)
    assert any("sudo -n /bin/launchctl bootstrap system" in c for c in result.commands)
    assert any("sudo -n /bin/launchctl kickstart -k system/com.thunder-forge.omlx-8018" in c for c in result.commands)


def test_generate_daemon_sudoers_limits_commands_to_daemon_manager() -> None:
    sudoers = generate_daemon_sudoers(_make_runtime_node())

    assert "Cmnd_Alias TF_OMLX_8018_INSTALL" in sudoers
    assert "/usr/bin/install -o root -g wheel -m 644" in sudoers
    assert "/Library/LaunchDaemons/com.thunder-forge.omlx-8018.plist" in sudoers
    assert "/bin/launchctl bootstrap system /Library/LaunchDaemons/com.thunder-forge.omlx-8018.plist" in sudoers
    assert "shag ALL=(root) NOPASSWD: TF_OMLX_8018_INSTALL, TF_OMLX_8018_LAUNCHD" in sudoers


def test_generate_daemon_setup_script_installs_sudoers_and_daemon() -> None:
    script = generate_daemon_setup_script(_make_runtime_node())

    assert script.startswith("#!/bin/zsh\n")
    assert "THUNDER_FORGE_PLIST" in script
    assert "<key>UserName</key>" in script
    assert "run_root /usr/sbin/visudo -cf" in script
    assert "run_root /usr/bin/install -o root -g wheel -m 440" in script
    assert "run_root /bin/launchctl bootstrap system" in script
    assert "\nTHUNDER_FORGE_PLIST\n\n/bin/cat" in script


def test_run_omlx_daemon_setup_dry_run_describes_admin_script() -> None:
    result = run_omlx_daemon_setup(_make_runtime_node(), admin_user="admin", via_su=True, apply=False)

    assert result.admin_user == "admin"
    assert result.ssh_user == "shag"
    assert result.via_su
    assert result.sudoers_path == "/etc/sudoers.d/thunder-forge"
    assert result.script_path == "/tmp/thunder-forge-setup-com.thunder-forge.omlx-8018.sh"
    assert result.script_content.startswith("#!/bin/zsh")
    assert any("su - admin" in c for c in result.commands)


def test_run_omlx_daemon_setup_apply_copies_and_runs_script(monkeypatch) -> None:
    node = _make_runtime_node()
    calls: list[tuple[str, str, str]] = []

    def fake_scp_content(user, ip, content, remote_path, *, shell=None):
        calls.append(("scp", user, remote_path))
        assert content.startswith("#!/bin/zsh")
        return subprocess.CompletedProcess(args=remote_path, returncode=0, stdout="", stderr="")

    def fake_ssh_run(user, ip, cmd, *, timeout, stream=False, shell=None, node_name=None, tty=False):
        calls.append(("ssh", user, cmd))
        if "sudo /bin/zsh" in cmd:
            assert user == "admin"
            assert stream is True
            assert tty is True
        if "sudo -n /bin/launchctl print" in cmd:
            assert user == "shag"
        return subprocess.CompletedProcess(args=cmd, returncode=0, stdout="", stderr="")

    import thunder_forge.cluster.omlx as omlx_module

    monkeypatch.setattr(omlx_module, "scp_content", fake_scp_content)
    monkeypatch.setattr(omlx_module, "ssh_run", fake_ssh_run)
    monkeypatch.setattr(
        omlx_module,
        "check_omlx_health",
        lambda base_url, *, timeout: OmlxHealthResult(base_url=base_url, health_ok=True, models_ok=True),
    )

    result = run_omlx_daemon_setup(node, admin_user="admin", apply=True)

    assert result.ok
    assert calls[0] == ("scp", "admin", "/tmp/thunder-forge-setup-com.thunder-forge.omlx-8018.sh")
    assert calls[1][0] == "ssh"
    expected_verify = "/usr/bin/sudo -n /bin/launchctl print system/com.thunder-forge.omlx-8018 >/dev/null"
    assert calls[2] == ("ssh", "shag", expected_verify)


def test_run_omlx_process_restart_dry_run_describes_rootless_commands() -> None:
    result = run_omlx_process_restart(_make_runtime_node(), apply=False)

    assert result.command == "/Users/shag/.local/bin/omlx serve --host 0.0.0.0 --port 8018"
    assert result.pid_path == "/Users/shag/.omlx/run/omlx-8018.pid"
    assert not result.applied
    assert any("launchctl bootout user/$(id -u)/com.thunder-forge.omlx-8018" in c for c in result.commands)
    assert any("nohup" in c for c in result.commands)


def test_run_omlx_process_restart_apply_records_pid_and_health(monkeypatch) -> None:
    node = _make_runtime_node()
    calls: list[str] = []

    def fake_ssh_run(user, ip, cmd, *, timeout):
        calls.append(cmd)
        stdout = "12345\n" if "nohup" in cmd else ""
        return subprocess.CompletedProcess(args=cmd, returncode=0, stdout=stdout, stderr="")

    import thunder_forge.cluster.omlx as omlx_module

    monkeypatch.setattr(omlx_module, "ssh_run", fake_ssh_run)
    monkeypatch.setattr(
        omlx_module,
        "check_omlx_health",
        lambda base_url, *, timeout: OmlxHealthResult(base_url=base_url, health_ok=True, models_ok=True),
    )

    result = run_omlx_process_restart(node, apply=True)

    assert result.ok
    assert result.pid == "12345"
    assert len(calls) == 2
    assert "pkill" in calls[0]
    assert "nohup" in calls[1]
