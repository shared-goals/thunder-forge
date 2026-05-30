"""Tests for shared service lifecycle helpers."""

from __future__ import annotations

from pathlib import Path

from thunder_forge.cluster.edge import edge_launchd_label, run_edge_service_restart
from thunder_forge.cluster.gateway import build_gateway_daemon_setup_result, run_gateway_daemon_setup
from thunder_forge.cluster.olla import olla_launchd_label, run_olla_service_restart
from thunder_forge.cluster.services import (
    LaunchdServiceSpec,
    generate_launchd_plist,
    run_local_commands,
    system_launchd_commands,
)


def test_generate_launchd_plist_with_environment_and_user() -> None:
    spec = LaunchdServiceSpec(
        name="sample",
        label="com.thunder-forge.sample",
        program_arguments=["/usr/local/bin/sample", "serve"],
        working_directory="/tmp/thunder-forge",
        stdout_log="/tmp/sample.out",
        stderr_log="/tmp/sample.err",
        environment={"HOME": "/Users/shag", "PATH": "/usr/bin:/bin"},
        user="shag",
    )

    plist = generate_launchd_plist(spec, system_daemon=True)

    assert "<string>com.thunder-forge.sample</string>" in plist
    assert "<string>/usr/local/bin/sample</string>" in plist
    assert "<string>serve</string>" in plist
    assert "<key>WorkingDirectory</key>" in plist
    assert "<string>/tmp/thunder-forge</string>" in plist
    assert "<key>EnvironmentVariables</key>" in plist
    assert "<key>HOME</key>" in plist
    assert "<string>/Users/shag</string>" in plist
    assert "<key>UserName</key>" in plist
    assert "<string>shag</string>" in plist


def test_system_launchd_commands_can_prompt_for_install_repair() -> None:
    commands = system_launchd_commands(
        label="com.thunder-forge.sample",
        staging_plist_path="/tmp/com.thunder-forge.sample.plist",
        plist_path="/Library/LaunchDaemons/com.thunder-forge.sample.plist",
        setup_command="mkdir -p /tmp",
        interactive_sudo=True,
    )

    assert "password prompt: host=%h method=sudo user=%p reason=manage Thunder Forge daemon" in commands[1]
    assert "launchctl bootout" in commands[2]
    assert "/usr/bin/sudo -p" in commands[3]
    assert "[%h] password: user=%p reason=manage Thunder Forge daemon com.thunder-forge.sample" in commands[3]
    assert "/usr/bin/install" in commands[3]
    assert "launchctl enable system/com.thunder-forge.sample" in "\n".join(commands)
    assert "/usr/bin/sudo -n" not in "\n".join(commands)


def test_system_launchd_commands_can_prompt_through_admin_user() -> None:
    commands = system_launchd_commands(
        label="com.thunder-forge.sample",
        staging_plist_path="/tmp/com.thunder-forge.sample.plist",
        plist_path="/Library/LaunchDaemons/com.thunder-forge.sample.plist",
        setup_command="mkdir -p /tmp",
        interactive_sudo=True,
        admin_user="serpo",
    )

    joined = "\n".join(commands)
    assert "password prompt: host=%h method=su user=serpo" in commands[1]
    assert "reason=manage Thunder Forge daemon com.thunder-forge.sample" in commands[1]
    assert sum("/usr/bin/su - serpo -c" in command for command in commands) == 1
    assert joined.count("/usr/bin/sudo -p") == 1
    assert "password prompt: host=%h method=sudo user=serpo" in joined
    assert "[%h] password: user=serpo reason=manage Thunder Forge daemon com.thunder-forge.sample" in joined
    assert "/usr/bin/install" in joined
    assert "launchctl enable system/com.thunder-forge.sample" in joined
    assert "/usr/bin/sudo -n /bin/launchctl bootstrap system" in joined
    assert commands[-1] == "true"


def test_gateway_daemon_setup_generates_combined_sudoers(tmp_path: Path) -> None:
    result, _olla_health_url, _edge_health_url = build_gateway_daemon_setup_result(
        repo_root=tmp_path,
        binary=Path(".tmp/olla-bin/olla"),
        config_path=Path("configs/olla-config.yaml"),
        edge_host="127.0.0.1",
        olla_port=40115,
        edge_port=40116,
        olla_base_url=None,
        users_env="TF_USER_",
        access_log_path=Path("logs/tf-edge-access.jsonl"),
        user="shag",
        admin_user="serpo",
        interactive_sudo=True,
        script_path=None,
    )

    assert result.sudoers_path == "/etc/sudoers.d/thunder-forge"
    assert "Cmnd_Alias TF_OLLA_40115_INSTALL" in result.script_content
    assert "Cmnd_Alias TF_EDGE_40116_INSTALL" in result.script_content
    assert (
        "shag ALL=(root) NOPASSWD: TF_OLLA_40115_INSTALL, TF_OLLA_40115_LAUNCHD, "
        "TF_EDGE_40116_INSTALL, TF_EDGE_40116_LAUNCHD"
    ) in result.script_content
    assert "run_root /usr/sbin/visudo -cf" in result.script_content
    assert "run_root /bin/launchctl bootstrap system" in result.script_content
    assert any("/usr/bin/su - serpo -c" in command for command in result.commands)
    assert any("user=serpo reason=install Thunder Forge gateway daemons" in command for command in result.commands)


def test_gateway_daemon_setup_apply_verifies_with_narrow_sudoers(tmp_path: Path, monkeypatch) -> None:
    import thunder_forge.cluster.gateway as gateway_module

    written_files: list[tuple[str, str]] = []
    command_batches: list[list[str]] = []

    def fake_write_local_file(path, content):
        written_files.append((path, content))

    def fake_run_local_commands(commands, *, timeout, stream=False):
        command_batches.append(list(commands))
        assert timeout == 300
        return True, ""

    health_checks: list[tuple[str, str]] = []

    def fake_wait_olla_healthy(base_url, **kwargs):
        health_checks.append(("olla", base_url))
        return True

    def fake_wait_edge_healthy(base_url, **kwargs):
        health_checks.append(("edge", base_url))
        return True

    monkeypatch.setattr(gateway_module, "write_local_file", fake_write_local_file)
    monkeypatch.setattr(gateway_module, "run_local_commands", fake_run_local_commands)
    monkeypatch.setattr(gateway_module, "_wait_olla_healthy", fake_wait_olla_healthy)
    monkeypatch.setattr(gateway_module, "_wait_edge_healthy", fake_wait_edge_healthy)

    result = run_gateway_daemon_setup(
        repo_root=tmp_path,
        users_env="TF_USER_",
        access_log_path=Path("logs/tf-edge-access.jsonl"),
        user="shag",
        admin_user="serpo",
        interactive_sudo=True,
        apply=True,
    )

    assert result.ok
    assert written_files[0][0] == str(tmp_path / ".tmp/run/thunder-forge-gateway-daemon-setup.sh")
    assert command_batches[0][0].startswith("printf '%s\\n' ")
    assert "/usr/bin/su - serpo -c" in command_batches[0][1]
    assert command_batches[1] == [
        "/usr/bin/sudo -n /bin/launchctl print system/com.thunder-forge.olla-40115 >/dev/null",
        "/usr/bin/sudo -n /bin/launchctl print system/com.thunder-forge.edge-40116 >/dev/null",
    ]
    assert health_checks == [("olla", "http://127.0.0.1:40115"), ("edge", "http://127.0.0.1:40116")]


def test_run_local_commands_stream_failure_reports_exit_code() -> None:
    ok, error = run_local_commands(["false"], timeout=5, stream=True)

    assert ok is False
    assert "Command failed with exit code 1" in error


def test_run_olla_service_restart_dry_run_describes_frontend_launch_agent(tmp_path: Path) -> None:
    repo_root = tmp_path

    result = run_olla_service_restart(repo_root=repo_root, apply=False, user="shag")

    assert result.service == "olla"
    assert result.label == olla_launchd_label(port=40115)
    assert result.plist_path == "~/Library/LaunchAgents/com.thunder-forge.olla-40115.plist"
    assert result.staging_plist_path == ""
    assert str(repo_root / ".tmp/olla-bin/olla") in result.plist_content
    assert str(repo_root / "configs/olla-config.yaml") in result.plist_content
    assert str(repo_root / "logs/olla-40115.stdout.log") in result.plist_content
    assert any("launchctl bootstrap gui/$(id -u)" in command for command in result.commands)
    assert any(
        "launchctl kickstart -k gui/$(id -u)/com.thunder-forge.olla-40115" in command
        for command in result.commands
    )
    assert not result.applied


def test_run_olla_service_restart_apply_writes_plist_then_starts(tmp_path: Path, monkeypatch) -> None:
    import thunder_forge.cluster.olla as olla_module

    command_batches: list[list[str]] = []
    written_files: list[tuple[str, str]] = []

    def fake_run_local_commands(commands, *, timeout, stream=False):
        command_batches.append(list(commands))
        assert timeout == 12
        assert stream is False
        return True, ""

    def fake_write_local_file(path, content):
        written_files.append((path, content))

    monkeypatch.setattr(olla_module, "run_local_commands", fake_run_local_commands)
    monkeypatch.setattr(olla_module, "write_local_file", fake_write_local_file)
    monkeypatch.setattr(olla_module, "_wait_olla_healthy", lambda base_url, **kwargs: base_url == "http://127.0.0.1:40115")

    result = run_olla_service_restart(repo_root=tmp_path, apply=True, timeout=12, user="shag")

    assert result.ok
    assert result.applied
    assert result.service_label_verified
    assert result.health_ok
    assert written_files[0][0] == "~/Library/LaunchAgents/com.thunder-forge.olla-40115.plist"
    assert "com.thunder-forge.olla-40115" in written_files[0][1]
    assert command_batches[0][0].startswith("mkdir -p ~/Library/LaunchAgents")
    assert any("launchctl bootstrap gui/$(id -u)" in command for command in command_batches[1])
    assert command_batches[2][0].startswith("launchctl list com.thunder-forge.olla-40115")


def test_run_olla_service_restart_daemon_apply_writes_staging_before_install(tmp_path: Path, monkeypatch) -> None:
    import thunder_forge.cluster.olla as olla_module

    command_batches: list[list[str]] = []
    written_files: list[tuple[str, str]] = []

    def fake_run_local_commands(commands, *, timeout, stream=False):
        command_batches.append(list(commands))
        assert stream is False
        return True, ""

    def fake_write_local_file(path, content):
        written_files.append((path, content))

    monkeypatch.setattr(olla_module, "run_local_commands", fake_run_local_commands)
    monkeypatch.setattr(olla_module, "write_local_file", fake_write_local_file)
    monkeypatch.setattr(olla_module, "_wait_olla_healthy", lambda base_url, **kwargs: True)

    result = run_olla_service_restart(repo_root=tmp_path, apply=True, manager="daemon", timeout=12, user="shag")

    assert result.ok
    assert written_files[0][0] == str(tmp_path / ".tmp/run/com.thunder-forge.olla-40115.plist")
    assert command_batches[0] == [f"mkdir -p {tmp_path}/.tmp/run {tmp_path}/logs"]
    assert "launchctl bootout" in command_batches[1][0]
    assert command_batches[1][1].startswith("/usr/bin/sudo -n /usr/bin/install")
    assert "launchctl bootstrap system" in command_batches[1][2]
    assert command_batches[2] == [
        "/usr/bin/sudo -n /bin/launchctl print system/com.thunder-forge.olla-40115 >/dev/null"
    ]


def test_run_edge_service_restart_dry_run_describes_frontend_launch_daemon(tmp_path: Path) -> None:
    repo_root = tmp_path

    result = run_edge_service_restart(repo_root=repo_root, manager="daemon", apply=False, user="shag")

    assert result.service == "edge"
    assert result.label == edge_launchd_label(port=40116)
    assert result.plist_path == "/Library/LaunchDaemons/com.thunder-forge.edge-40116.plist"
    assert result.staging_plist_path == str(repo_root / ".tmp/run/com.thunder-forge.edge-40116.plist")
    assert "edge" in result.plist_content
    assert "serve" in result.plist_content
    assert "--port" in result.plist_content
    assert "40116" in result.plist_content
    assert "logs/tf-edge-access.jsonl" in result.plist_content
    assert any(command.startswith("/usr/bin/sudo -n /usr/bin/install") for command in result.commands)
    assert any("launchctl bootstrap system" in command for command in result.commands)
    assert not result.applied


def test_run_edge_service_restart_daemon_apply_reinstalls_every_time(tmp_path: Path, monkeypatch) -> None:
    import thunder_forge.cluster.edge as edge_module

    command_batches: list[list[str]] = []
    written_files: list[tuple[str, str]] = []

    def fake_run_local_commands(commands, *, timeout, stream=False):
        command_batches.append(list(commands))
        assert timeout == 12
        assert stream is False
        return True, ""

    def fake_write_local_file(path, content):
        written_files.append((path, content))

    monkeypatch.setattr(edge_module, "run_local_commands", fake_run_local_commands)
    monkeypatch.setattr(edge_module, "write_local_file", fake_write_local_file)
    monkeypatch.setattr(edge_module, "_wait_edge_healthy", lambda base_url, **kwargs: base_url == "http://127.0.0.1:40116")

    result = run_edge_service_restart(repo_root=tmp_path, apply=True, manager="daemon", timeout=12, user="shag")

    assert result.ok
    assert written_files[0][0] == str(tmp_path / ".tmp/run/com.thunder-forge.edge-40116.plist")
    assert command_batches[0] == [f"mkdir -p {tmp_path}/.tmp/run {tmp_path}/logs"]
    assert "launchctl bootout" in command_batches[1][0]
    assert command_batches[1][1].startswith("/usr/bin/sudo -n /usr/bin/install")
    assert "launchctl bootstrap system" in command_batches[1][2]
    assert command_batches[2] == [
        "/usr/bin/sudo -n /bin/launchctl print system/com.thunder-forge.edge-40116 >/dev/null"
    ]
