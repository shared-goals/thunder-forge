"""Tests for node-level runtime launchd install."""

from __future__ import annotations

import subprocess

from thunder_forge.cluster.config import RuntimeType
from thunder_forge.cluster.omlx import (
    Node,
    OmlxHealthResult,
    OmlxInstallResult,
    ensure_omlx_tooling,
    generate_daemon_setup_script,
    generate_daemon_sudoers,
    generate_launchd_plist,
    run_omlx_daemon_restart,
    run_omlx_daemon_setup,
    run_omlx_install,
    run_omlx_process_restart,
    run_omlx_runtime_restart,
)


def _make_runtime_node(home_dir="/Users/shag", port=8018, model_dir=None, bind_host="0.0.0.0"):
    from thunder_forge.cluster.config import NodeRuntime

    runtime = NodeRuntime(type=RuntimeType.OMLX, port=port, model_dir=model_dir, bind_host=bind_host)
    return Node(
        host="infer-03.lan",
        fabric_host=False,
        ram_gb=128,
        user="shag",
        roles=["inference"],
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
        host="infer-03.lan",
        fabric_host=False,
        ram_gb=128,
        user="shag",
        roles=["inference"],
        shell="zsh",
        home_dir="/Users/shag",
        runtime=None,
    )

    import pytest

    with pytest.raises(ValueError):
        generate_launchd_plist(node)


def test_omlx_install_result_dry_run_describes_commands() -> None:
    result = OmlxInstallResult(
        node="infer-03",
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
        node="infer-03",
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
        lambda base_url, **kwargs: OmlxHealthResult(base_url=base_url, health_ok=True, models_ok=True),
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
    assert any('sudo -n /bin/launchctl kickstart -k "system/com.thunder-forge.omlx-8018"' in c for c in result.commands)


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
    assert 'OMLX_CACHE_DIR="$OMLX_HOME/cache"' in script
    assert 'OMLX_MODELS_DIR="$OMLX_HOME/models"' in script
    assert 'CHOWN_BIN="$(command -v chown || true)"' in script
    assert 'run_root "$CHOWN_BIN" "$NODE_USER":staff "$OMLX_HOME"' in script
    assert "run_root /usr/bin/install -o root -g wheel -m 440" in script
    assert "run_root /bin/launchctl bootstrap system" in script
    assert "\nTHUNDER_FORGE_PLIST\n\n/bin/cat" in script


def test_ensure_omlx_tooling_dry_run_installs_user_local_uv_and_omlx() -> None:
    result = ensure_omlx_tooling(_make_runtime_node(), apply=False)

    assert result.uv_path == "/Users/shag/.local/bin/uv"
    assert result.omlx_path == "/Users/shag/.local/bin/omlx"
    assert result.tool_spec == "git+https://github.com/jundot/omlx.git"
    assert "OMLX_UPGRADE=0" in result.command
    assert 'OMLX_TOOL_PYTHON=3.13' in result.command
    assert "https://astral.sh/uv/install.sh" in result.command
    assert '"$UV_BINARY" tool install --python "$OMLX_TOOL_PYTHON" "$OMLX_TOOL_SPEC"' in result.command
    assert 'OMLX_PYTHON=' in result.command
    assert 'mlx.core import check failed' in result.command
    assert 'tool install --python "$OMLX_TOOL_PYTHON" --reinstall "$OMLX_TOOL_SPEC"' in result.command
    assert '"$OMLX_BINARY" --help >/dev/null' in result.command


def test_ensure_omlx_tooling_dry_run_upgrade_mode_requests_tool_upgrade() -> None:
    result = ensure_omlx_tooling(_make_runtime_node(), apply=False, upgrade=True)

    assert "OMLX_UPGRADE=1" in result.command
    assert '"$UV_BINARY" tool install --python "$OMLX_TOOL_PYTHON" --upgrade "$OMLX_TOOL_SPEC"' in result.command


def test_ensure_omlx_tooling_apply_runs_as_node_user(monkeypatch) -> None:
    node = _make_runtime_node()
    calls: list[tuple[str, str, str, bool]] = []

    def fake_ssh_run(user, ip, cmd, *, timeout, stream=False, shell=None, node_id=None, tty=False):
        calls.append((user, ip, cmd, stream))
        if cmd == "command -v omlx":
            return subprocess.CompletedProcess(
                args=cmd,
                returncode=0,
                stdout="/Users/shag/.local/bin/omlx\n",
                stderr="",
            )
        if "import mlx.core as mx" in cmd:
            return subprocess.CompletedProcess(args=cmd, returncode=0, stdout="mlx.core\n", stderr="")
        if cmd == "/Users/shag/.local/bin/omlx --version":
            return subprocess.CompletedProcess(args=cmd, returncode=0, stdout="omlx 0.4.2.dev2\n", stderr="")
        return subprocess.CompletedProcess(args=cmd, returncode=0, stdout="", stderr="")

    import thunder_forge.cluster.omlx as omlx_module

    monkeypatch.setattr(omlx_module, "ssh_run", fake_ssh_run)

    result = ensure_omlx_tooling(node, apply=True, timeout=120)

    assert result.ok
    assert calls == [
        ("shag", "infer-03.lan", result.command, True),
        ("shag", "infer-03.lan", "command -v omlx", False),
        ("shag", "infer-03.lan", "/Users/shag/.local/bin/omlx --version", False),
    ]
    assert "NODE_HOME=/Users/shag" in result.command
    assert result.resolved_omlx_path == "/Users/shag/.local/bin/omlx"
    assert result.resolved_omlx_version == "0.4.2.dev2"


def test_ensure_omlx_tooling_apply_falls_back_to_direct_path_when_omlx_is_missing_from_login_path(
    monkeypatch,
) -> None:
    node = _make_runtime_node()
    calls: list[tuple[str, str, str, bool]] = []

    def fake_ssh_run(user, ip, cmd, *, timeout, stream=False, shell=None, node_id=None, tty=False):
        calls.append((user, ip, cmd, stream))
        if cmd == "command -v omlx":
            return subprocess.CompletedProcess(args=cmd, returncode=1, stdout="", stderr="not found")
        if cmd == "/Users/shag/.local/bin/omlx --version":
            return subprocess.CompletedProcess(args=cmd, returncode=0, stdout="omlx 0.4.2.dev2\n", stderr="")
        return subprocess.CompletedProcess(args=cmd, returncode=0, stdout="", stderr="")

    import thunder_forge.cluster.omlx as omlx_module

    monkeypatch.setattr(omlx_module, "ssh_run", fake_ssh_run)

    result = ensure_omlx_tooling(node, apply=True, timeout=120)

    assert result.ok
    assert result.applied
    assert result.verified
    assert calls == [
        ("shag", "infer-03.lan", result.command, True),
        ("shag", "infer-03.lan", "command -v omlx", False),
        ("shag", "infer-03.lan", "/Users/shag/.local/bin/omlx --version", False),
    ]
    assert result.resolved_omlx_path == "/Users/shag/.local/bin/omlx"
    assert result.resolved_omlx_version == "0.4.2.dev2"
    assert result.errors == []


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

    def fake_ssh_run(user, ip, cmd, *, timeout, stream=False, shell=None, node_id=None, tty=False):
        calls.append(("ssh", user, cmd))
        if "sudo /bin/zsh" in cmd:
            assert user == "shag"
            assert stream is True
            assert tty is True
        return subprocess.CompletedProcess(args=cmd, returncode=0, stdout="", stderr="")

    import thunder_forge.cluster.omlx as omlx_module

    monkeypatch.setattr(omlx_module, "scp_content", fake_scp_content)
    monkeypatch.setattr(omlx_module, "ssh_run", fake_ssh_run)

    result = run_omlx_daemon_setup(node, admin_user="admin", apply=True)

    assert result.ok
    assert result.sudoers_verified
    assert result.service_label_verified
    assert result.health_ok
    assert calls[0] == ("scp", "shag", "/tmp/thunder-forge-setup-com.thunder-forge.omlx-8018.sh")
    assert calls[1][0] == "ssh"
    assert len(calls) == 2


def test_run_omlx_daemon_setup_skips_post_apply_probes(monkeypatch) -> None:
    node = _make_runtime_node()

    def fake_scp_content(user, ip, content, remote_path, *, shell=None):
        return subprocess.CompletedProcess(args=remote_path, returncode=0, stdout="", stderr="")

    def fake_ssh_run(user, ip, cmd, *, timeout, stream=False, shell=None, node_id=None, tty=False):
        return subprocess.CompletedProcess(args=cmd, returncode=0, stdout="", stderr="")

    import thunder_forge.cluster.omlx as omlx_module

    monkeypatch.setattr(omlx_module, "scp_content", fake_scp_content)
    monkeypatch.setattr(omlx_module, "ssh_run", fake_ssh_run)
    monkeypatch.setattr(
        omlx_module,
        "_wait_for_omlx_health",
        lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("health probe should be skipped")),
    )
    monkeypatch.setattr(
        omlx_module,
        "run_omlx_daemon_restart",
        lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("daemon retry should be skipped")),
    )

    result = run_omlx_daemon_setup(node, admin_user="admin", apply=True, timeout=45)

    assert result.ok
    assert result.errors == []


def test_run_omlx_process_restart_dry_run_describes_rootless_commands() -> None:
    result = run_omlx_process_restart(_make_runtime_node(), apply=False)

    assert result.command == "/Users/shag/.local/bin/omlx serve --host 0.0.0.0 --port 8018"
    assert result.pid_path == "/Users/shag/.omlx/run/omlx-8018.pid"
    assert not result.applied
    assert any("launchctl bootout user/$(id -u)/com.thunder-forge.omlx-8018" in c for c in result.commands)
    assert any("nohup" in c for c in result.commands)


def test_omlx_stop_patterns_follow_configured_bind_host() -> None:
    node = _make_runtime_node(bind_host="127.0.0.1")

    launchd_result = run_omlx_runtime_restart(node, apply=False)
    daemon_result = run_omlx_daemon_restart(node, apply=False)
    process_result = run_omlx_process_restart(node, apply=False)
    setup_script = generate_daemon_setup_script(node)

    assert any(r"--host 127\.0\.0\.1 --port 8018" in command for command in launchd_result.commands)
    assert any(r"--host 127\.0\.0\.1 --port 8018" in command for command in daemon_result.commands)
    assert any(r"--host 127\.0\.0\.1 --port 8018" in command for command in process_result.commands)
    assert r"--host 127\.0\.0\.1 --port 8018" in setup_script
    assert r"--host 0\.0\.0\.0 --port 8018" not in "\n".join(
        [*launchd_result.commands, *daemon_result.commands, *process_result.commands, setup_script]
    )


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
        lambda base_url, **kwargs: OmlxHealthResult(base_url=base_url, health_ok=True, models_ok=True),
    )

    result = run_omlx_process_restart(node, apply=True)

    assert result.ok
    assert result.pid == "12345"
    assert len(calls) == 2
    assert "pkill" in calls[0]
    assert "nohup" in calls[1]
