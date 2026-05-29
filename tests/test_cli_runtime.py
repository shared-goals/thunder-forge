"""CLI tests for node-level runtime commands."""

from pathlib import Path
from textwrap import dedent

import yaml as yaml_lib
from typer.testing import CliRunner

from thunder_forge.cli import app
from thunder_forge.cluster.config import ClusterConfig, ServiceConfig
from thunder_forge.cluster.gateway import GatewayDaemonSetupResult
from thunder_forge.cluster.olla import OllaDevSmokeResult, OllaSmokeResult
from thunder_forge.cluster.omlx import OmlxDaemonSetupResult, OmlxHealthResult, OmlxProcessResult, OmlxSmokeResult
from thunder_forge.cluster.services import LaunchdServiceResult

runner = CliRunner()


def _cluster_config(*, edge_port: int = 40116, olla_port: int = 40115) -> ClusterConfig:
    return ClusterConfig(services=ServiceConfig(edge_port=edge_port, olla_port=olla_port))


def test_runtime_start_dry_run_omits_default_model_dir(tmp_path: Path, monkeypatch) -> None:
    repo = tmp_path
    config_dir = repo / "configs"
    config_dir.mkdir()
    (repo / "tfconfig.yaml").write_text(
        dedent("""\
            models: {}
            nodes:
              msm3:
                host: msm3-wifi.lan
                fabric_host: true
                ram_gb: 128
                user: shag
                role: inference
                home_dir: /Users/shag
                runtime:
                  type: omlx
                  port: 8018
        """)
    )

    import thunder_forge.cluster.config as config_module

    monkeypatch.setattr(config_module, "find_repo_root", lambda: repo)
    result = runner.invoke(app, ["runtime", "start", "--node", "msm3", "--dry-run"])

    assert result.exit_code == 0
    assert "node: msm3" in result.stdout
    assert "runtime: omlx" in result.stdout
    assert "management_host: msm3-wifi.lan" in result.stdout
    assert "fabric_host: true" in result.stdout
    assert "/Users/shag/.local/bin/omlx serve --host 0.0.0.0 --port 8018" in result.stdout
    assert "--model-dir" not in result.stdout


def test_runtime_start_apply_starts_remote_runtime(tmp_path: Path, monkeypatch) -> None:
    repo = tmp_path
    config_dir = repo / "configs"
    config_dir.mkdir()
    (repo / "tfconfig.yaml").write_text(
        dedent("""\
            models: {}
            nodes:
              msm3:
                host: msm3-wifi.lan
                fabric_host: true
                ram_gb: 128
                user: shag
                role: inference
                home_dir: /Users/shag
                runtime:
                  type: omlx
                  port: 8018
        """)
    )

    import thunder_forge.cli as cli_module
    import thunder_forge.cluster.config as config_module
    from thunder_forge.cluster.omlx import OmlxStartResult

    monkeypatch.setattr(config_module, "find_repo_root", lambda: repo)
    monkeypatch.setattr(
        cli_module,
        "check_omlx_health",
        lambda base_url: OmlxHealthResult(base_url=base_url, health_ok=False, models_ok=False),
    )
    monkeypatch.setattr(
        cli_module,
        "run_omlx_runtime_start",
        lambda runtime_node, *, timeout: OmlxStartResult(returncode=0, pid="4242"),
    )

    result = runner.invoke(app, ["runtime", "start", "--node", "msm3", "--apply"])

    assert result.exit_code == 0
    assert "node: msm3" in result.stdout
    assert "command: /Users/shag/.local/bin/omlx serve --host 0.0.0.0 --port 8018" in result.stdout
    assert "pid: 4242" in result.stdout
    assert "status: started" in result.stdout


def test_runtime_start_apply_skips_when_runtime_is_already_healthy(tmp_path: Path, monkeypatch) -> None:
    repo = tmp_path
    config_dir = repo / "configs"
    config_dir.mkdir()
    (repo / "tfconfig.yaml").write_text(
        dedent("""\
            models: {}
            nodes:
              msm3:
                host: msm3-wifi.lan
                fabric_host: true
                ram_gb: 128
                user: shag
                role: inference
                home_dir: /Users/shag
                runtime:
                  type: omlx
                  port: 8018
        """)
    )

    import thunder_forge.cli as cli_module
    import thunder_forge.cluster.config as config_module

    started = False

    def fake_start(runtime_node, *, timeout):
        nonlocal started
        started = True
        from thunder_forge.cluster.omlx import OmlxStartResult

        return OmlxStartResult(returncode=0, pid="4242")

    monkeypatch.setattr(config_module, "find_repo_root", lambda: repo)
    monkeypatch.setattr(
        cli_module,
        "check_omlx_health",
        lambda base_url: OmlxHealthResult(base_url=base_url, health_ok=True, models_ok=True),
    )
    monkeypatch.setattr(cli_module, "run_omlx_runtime_start", fake_start)

    result = runner.invoke(app, ["runtime", "start", "--node", "msm3", "--apply"])

    assert result.exit_code == 0
    assert started is False
    assert "status: already running" in result.stdout


def test_runtime_restart_dry_run_prints_process_commands(tmp_path: Path, monkeypatch) -> None:
    repo = tmp_path
    config_dir = repo / "configs"
    config_dir.mkdir()
    (repo / "tfconfig.yaml").write_text(
        dedent("""\
            models: {}
            nodes:
              msm3:
                host: msm3-wifi.lan
                fabric_host: true
                ram_gb: 128
                user: shag
                role: inference
                home_dir: /Users/shag
                runtime:
                  type: omlx
                  port: 8018
        """)
    )

    import thunder_forge.cluster.config as config_module

    monkeypatch.setattr(config_module, "find_repo_root", lambda: repo)

    result = runner.invoke(app, ["runtime", "restart", "--node", "msm3", "--dry-run"])

    assert result.exit_code == 0
    assert "node: msm3" in result.stdout
    assert "manager: process" in result.stdout
    assert "command: /Users/shag/.local/bin/omlx serve --host 0.0.0.0 --port 8018" in result.stdout
    assert "pid_path: /Users/shag/.omlx/run/omlx-8018.pid" in result.stdout
    assert "mode: dry-run" in result.stdout
    assert "bootout" in result.stdout
    assert "nohup" in result.stdout


def test_runtime_restart_daemon_dry_run_prints_plist_and_sudo_commands(tmp_path: Path, monkeypatch) -> None:
    repo = tmp_path
    config_dir = repo / "configs"
    config_dir.mkdir()
    (repo / "tfconfig.yaml").write_text(
        dedent("""\
            models: {}
            nodes:
              msm3:
                host: msm3-wifi.lan
                fabric_host: true
                ram_gb: 128
                user: shag
                role: inference
                home_dir: /Users/shag
                runtime:
                  type: omlx
                  port: 8018
        """)
    )

    import thunder_forge.cluster.config as config_module

    monkeypatch.setattr(config_module, "find_repo_root", lambda: repo)

    result = runner.invoke(app, ["runtime", "restart", "--node", "msm3", "--manager", "daemon", "--dry-run"])

    assert result.exit_code == 0
    assert "manager: daemon" in result.stdout
    assert "plist_path: /Library/LaunchDaemons/com.thunder-forge.omlx-8018.plist" in result.stdout
    assert "staging_plist_path: /Users/shag/.omlx/run/com.thunder-forge.omlx-8018.plist" in result.stdout
    assert "<key>UserName</key>" in result.stdout
    assert "sudo -n /usr/bin/install" in result.stdout
    assert "sudo -n /bin/launchctl bootstrap system" in result.stdout


def test_service_restart_olla_dry_run_prints_frontend_service_plan(tmp_path: Path, monkeypatch) -> None:
    import thunder_forge.cli as cli_module
    import thunder_forge.cluster.config as config_module

    repo = tmp_path
    (repo / "tfconfig.yaml").write_text(
        dedent("""\
            services:
              frontend:
                admin_user: serpo
              olla:
                port: 45115
            models: {}
            nodes: {}
        """)
    )
    calls = []

    def fake_run_olla_service_restart(**kwargs):
        calls.append(kwargs)
        return LaunchdServiceResult(
            service="olla",
            label="com.thunder-forge.olla-40115",
            plist_path="~/Library/LaunchAgents/com.thunder-forge.olla-40115.plist",
            plist_content="<plist><string>com.thunder-forge.olla-40115</string></plist>",
            commands=["launchctl bootstrap gui/$(id -u) ~/Library/LaunchAgents/com.thunder-forge.olla-40115.plist"],
        )

    monkeypatch.setattr(config_module, "find_repo_root", lambda: repo)
    monkeypatch.setattr(cli_module, "run_olla_service_restart", fake_run_olla_service_restart)

    result = runner.invoke(app, ["service", "restart", "--service", "olla"])

    assert result.exit_code == 0
    assert calls[0]["repo_root"] == repo
    assert calls[0]["apply"] is False
    assert calls[0]["manager"] == "launchd"
    assert calls[0]["port"] == 45115
    assert calls[0]["admin_user"] == ""
    assert "service: olla" in result.stdout
    assert "manager: launchd" in result.stdout
    assert "plist_path: ~/Library/LaunchAgents/com.thunder-forge.olla-40115.plist" in result.stdout
    assert "mode: dry-run" in result.stdout
    assert "launchctl bootstrap gui/$(id -u)" in result.stdout


def test_service_restart_olla_allow_sudo_prompt_uses_frontend_admin_user(tmp_path: Path, monkeypatch) -> None:
    import thunder_forge.cli as cli_module
    import thunder_forge.cluster.config as config_module

    repo = tmp_path
    (repo / "tfconfig.yaml").write_text(
        dedent("""\
            services:
              frontend:
                admin_user: serpo
            models: {}
            nodes: {}
        """)
    )
    calls = []

    def fake_run_olla_service_restart(**kwargs):
        calls.append(kwargs)
        return LaunchdServiceResult(
            service="olla",
            label="com.thunder-forge.olla-40115",
            plist_path="/Library/LaunchDaemons/com.thunder-forge.olla-40115.plist",
        )

    monkeypatch.setattr(config_module, "find_repo_root", lambda: repo)
    monkeypatch.setattr(cli_module, "run_olla_service_restart", fake_run_olla_service_restart)

    result = runner.invoke(
        app,
        ["service", "restart", "--service", "olla", "--manager", "daemon", "--allow-sudo-prompt"],
    )

    assert result.exit_code == 0
    assert calls[0]["admin_user"] == "serpo"
    assert calls[0]["interactive_sudo"] is True


def test_service_setup_daemon_dry_run_prints_gateway_plan(tmp_path: Path, monkeypatch) -> None:
    import thunder_forge.cli as cli_module
    import thunder_forge.cluster.config as config_module

    repo = tmp_path
    (repo / "tfconfig.yaml").write_text(
        dedent("""\
            services:
              frontend:
                admin_user: serpo
              olla:
                port: 45115
              edge:
                port: 45116
                access_log: logs/custom-edge.jsonl
            models: {}
            nodes:
              studio:
                host: studio.lan
                ram_gb: 128
                roles: [gateway, cache]
                user: shag
                admin_user: serpo
        """)
    )
    calls = []

    def fake_run_gateway_daemon_setup(**kwargs):
        calls.append(kwargs)
        return GatewayDaemonSetupResult(
            user=kwargs["user"],
            admin_user=kwargs["admin_user"],
            sudoers_path="/etc/sudoers.d/thunder-forge",
            script_path=str(repo / ".tmp/run/thunder-forge-gateway-daemon-setup.sh"),
            services=[
                LaunchdServiceResult(
                    service="olla",
                    label="com.thunder-forge.olla-45115",
                    plist_path="/Library/LaunchDaemons/com.thunder-forge.olla-45115.plist",
                    staging_plist_path=str(repo / ".tmp/run/com.thunder-forge.olla-45115.plist"),
                ),
                LaunchdServiceResult(
                    service="edge",
                    label="com.thunder-forge.edge-45116",
                    plist_path="/Library/LaunchDaemons/com.thunder-forge.edge-45116.plist",
                    staging_plist_path=str(repo / ".tmp/run/com.thunder-forge.edge-45116.plist"),
                ),
            ],
            script_content="#!/bin/zsh\nCmnd_Alias TF_OLLA_45115_INSTALL = ...\n",
            commands=["write setup script", "/usr/bin/su - serpo -c 'sudo /bin/zsh script'"],
        )

    monkeypatch.setattr(config_module, "find_repo_root", lambda: repo)
    monkeypatch.setattr(cli_module, "run_gateway_daemon_setup", fake_run_gateway_daemon_setup)

    result = runner.invoke(app, ["service", "setup-daemon"])

    assert result.exit_code == 0
    assert calls[0]["repo_root"] == repo
    assert calls[0]["user"] == "shag"
    assert calls[0]["admin_user"] == "serpo"
    assert calls[0]["olla_port"] == 45115
    assert calls[0]["edge_port"] == 45116
    assert calls[0]["access_log_path"] == repo / "logs/custom-edge.jsonl"
    assert calls[0]["apply"] is False
    assert "scope: gateway" in result.stdout
    assert "sudoers_path: /etc/sudoers.d/thunder-forge" in result.stdout
    assert "com.thunder-forge.olla-45115" in result.stdout
    assert "script:" in result.stdout


def test_service_setup_daemon_apply_requires_prompt_when_admin_user_configured(
    tmp_path: Path,
    monkeypatch,
) -> None:
    import thunder_forge.cluster.config as config_module

    repo = tmp_path
    (repo / "tfconfig.yaml").write_text(
        dedent("""\
            services:
              frontend:
                admin_user: serpo
            models: {}
            nodes: {}
        """)
    )

    monkeypatch.setattr(config_module, "find_repo_root", lambda: repo)

    result = runner.invoke(app, ["service", "setup-daemon", "--apply"])

    assert result.exit_code == 1
    assert "requires --allow-sudo-prompt" in result.stderr


def test_service_restart_olla_apply_exits_on_early_error(tmp_path: Path, monkeypatch) -> None:
    import thunder_forge.cli as cli_module
    import thunder_forge.cluster.config as config_module

    repo = tmp_path
    (repo / "tfconfig.yaml").write_text("models: {}\nnodes: {}\n")

    monkeypatch.setattr(config_module, "find_repo_root", lambda: repo)
    monkeypatch.setattr(
        cli_module,
        "run_olla_service_restart",
        lambda **kwargs: LaunchdServiceResult(
            service="olla",
            label="com.thunder-forge.olla-40115",
            plist_path="/Library/LaunchDaemons/com.thunder-forge.olla-40115.plist",
            errors=["Command failed with exit code 1: /usr/bin/su - serpo -c 'sudo install ...'"],
        ),
    )

    result = runner.invoke(app, ["service", "restart", "--service", "olla", "--apply"])

    assert result.exit_code == 1
    assert "Error: Command failed with exit code 1" in result.stderr


def test_service_restart_edge_dry_run_prints_frontend_daemon_plan(tmp_path: Path, monkeypatch) -> None:
    import thunder_forge.cli as cli_module
    import thunder_forge.cluster.config as config_module

    repo = tmp_path
    (repo / "tfconfig.yaml").write_text(
        dedent("""\
            services:
              frontend:
                admin_user: serpo
              edge:
                port: 45116
                access_log: logs/custom-edge.jsonl
              olla:
                port: 45115
            models: {}
            nodes: {}
        """)
    )
    calls = []

    def fake_run_edge_service_restart(**kwargs):
        calls.append(kwargs)
        return LaunchdServiceResult(
            service="edge",
            label="com.thunder-forge.edge-45116",
            plist_path="/Library/LaunchDaemons/com.thunder-forge.edge-45116.plist",
            staging_plist_path=str(repo / ".tmp/run/com.thunder-forge.edge-45116.plist"),
            plist_content="<plist><string>com.thunder-forge.edge-45116</string></plist>",
            commands=[
                "/usr/bin/sudo -n /bin/launchctl bootstrap system "
                "/Library/LaunchDaemons/com.thunder-forge.edge-45116.plist"
            ],
        )

    monkeypatch.setattr(config_module, "find_repo_root", lambda: repo)
    monkeypatch.setattr(cli_module, "run_edge_service_restart", fake_run_edge_service_restart)

    result = runner.invoke(app, ["service", "restart", "--service", "edge", "--manager", "daemon"])

    assert result.exit_code == 0
    assert calls[0]["repo_root"] == repo
    assert calls[0]["apply"] is False
    assert calls[0]["manager"] == "daemon"
    assert calls[0]["port"] == 45116
    assert calls[0]["olla_base_url"] == "http://127.0.0.1:45115"
    assert calls[0]["access_log_path"] == repo / "logs/custom-edge.jsonl"
    assert calls[0]["admin_user"] == ""
    assert "service: edge" in result.stdout
    assert "manager: daemon" in result.stdout
    assert "plist_path: /Library/LaunchDaemons/com.thunder-forge.edge-45116.plist" in result.stdout
    assert "mode: dry-run" in result.stdout
    assert "launchctl bootstrap system" in result.stdout


def test_service_restart_edge_apply_exits_on_early_error(tmp_path: Path, monkeypatch) -> None:
    import thunder_forge.cli as cli_module
    import thunder_forge.cluster.config as config_module

    repo = tmp_path
    (repo / "tfconfig.yaml").write_text("models: {}\nnodes: {}\n")

    monkeypatch.setattr(config_module, "find_repo_root", lambda: repo)
    monkeypatch.setattr(
        cli_module,
        "run_edge_service_restart",
        lambda **kwargs: LaunchdServiceResult(
            service="edge",
            label="com.thunder-forge.edge-40116",
            plist_path="/Library/LaunchDaemons/com.thunder-forge.edge-40116.plist",
            errors=["Command failed with exit code 1: /usr/bin/su - serpo -c 'sudo install ...'"],
        ),
    )

    result = runner.invoke(app, ["service", "restart", "--service", "edge", "--apply"])

    assert result.exit_code == 1
    assert "Error: Command failed with exit code 1" in result.stderr


def test_service_restart_omlx_requires_node() -> None:
    result = runner.invoke(app, ["service", "restart", "--service", "omlx"])

    assert result.exit_code == 1
    assert "Error: --service omlx requires --node" in result.stderr


def test_service_restart_omlx_launchd_dry_run_prints_service_plan(tmp_path: Path, monkeypatch) -> None:
    repo = tmp_path
    config_dir = repo / "configs"
    config_dir.mkdir()
    (repo / "tfconfig.yaml").write_text(
        dedent("""\
            models: {}
            nodes:
              msm3:
                host: msm3-wifi.lan
                fabric_host: true
                ram_gb: 128
                user: shag
                admin_user: admin
                role: inference
                home_dir: /Users/shag
                runtime:
                  type: omlx
                  port: 8018
        """)
    )

    import thunder_forge.cluster.config as config_module

    monkeypatch.setattr(config_module, "find_repo_root", lambda: repo)

    result = runner.invoke(app, ["service", "restart", "--service", "omlx", "--node", "msm3"])

    assert result.exit_code == 0
    assert "node: msm3" in result.stdout
    assert "service: omlx" in result.stdout
    assert "manager: launchd" in result.stdout
    assert "plist_path: ~/Library/LaunchAgents/com.thunder-forge.omlx-8018.plist" in result.stdout
    assert "launchctl bootstrap user/$(id -u)" in result.stdout


def test_runtime_setup_daemon_dry_run_prints_admin_script(tmp_path: Path, monkeypatch) -> None:
    repo = tmp_path
    config_dir = repo / "configs"
    config_dir.mkdir()
    (repo / "tfconfig.yaml").write_text(
        dedent("""\
            models: {}
            nodes:
              msm3:
                host: msm3-wifi.lan
                fabric_host: true
                ram_gb: 128
                user: shag
                admin_user: admin
                role: inference
                home_dir: /Users/shag
                runtime:
                  type: omlx
                  port: 8018
        """)
    )

    import thunder_forge.cluster.config as config_module

    monkeypatch.setattr(config_module, "find_repo_root", lambda: repo)

    result = runner.invoke(app, ["runtime", "setup-daemon", "--node", "msm3", "--via-su"])

    assert result.exit_code == 0
    assert "manager: daemon" in result.stdout
    assert "admin_user: admin" in result.stdout
    assert "ssh_user: shag" in result.stdout
    assert "via_su: yes" in result.stdout
    assert "sudoers_path: /etc/sudoers.d/thunder-forge" in result.stdout
    assert "script:" in result.stdout
    assert "#!/bin/zsh" in result.stdout
    assert "run_root /usr/sbin/visudo -cf" in result.stdout
    assert "copy setup script to shag@msm3-wifi.lan" in result.stdout


def test_runtime_setup_daemon_apply_hides_admin_script(tmp_path: Path, monkeypatch) -> None:
    repo = tmp_path
    config_dir = repo / "configs"
    config_dir.mkdir()
    (repo / "tfconfig.yaml").write_text(
        dedent("""\
            models: {}
            nodes:
              msm3:
                host: msm3-wifi.lan
                fabric_host: true
                ram_gb: 128
                user: shag
                role: inference
                home_dir: /Users/shag
                runtime:
                  type: omlx
                  port: 8018
        """)
    )

    import thunder_forge.cli as cli_module
    import thunder_forge.cluster.config as config_module

    monkeypatch.setattr(config_module, "find_repo_root", lambda: repo)
    monkeypatch.setattr(
        cli_module,
        "run_omlx_daemon_setup",
        lambda runtime_node, *, admin_user, via_su, script_path, apply, timeout: OmlxDaemonSetupResult(
            node=runtime_node.host,
            label="com.thunder-forge.omlx-8018",
            plist_path="/Library/LaunchDaemons/com.thunder-forge.omlx-8018.plist",
            staging_plist_path="/Users/shag/.omlx/run/com.thunder-forge.omlx-8018.plist",
            sudoers_path="/etc/sudoers.d/thunder-forge",
            script_path="/tmp/thunder-forge-setup-com.thunder-forge.omlx-8018.sh",
            admin_user=admin_user or runtime_node.user,
            ssh_user=runtime_node.user if via_su else (admin_user or runtime_node.user),
            via_su=via_su,
            script_content="#!/bin/zsh\necho hidden\n",
            commands=["copy setup script", "run setup script"],
            applied=True,
            sudoers_verified=True,
            service_label_verified=True,
            health_ok=True,
        ),
    )

    result = runner.invoke(
        app,
        ["runtime", "setup-daemon", "--node", "msm3", "--admin-user", "admin", "--apply"],
    )

    assert result.exit_code == 0
    assert "mode: apply" in result.stdout
    assert "script:" not in result.stdout
    assert "echo hidden" not in result.stdout
    assert "status: daemon setup complete" in result.stdout


def test_runtime_setup_daemon_via_su_requires_admin_user(tmp_path: Path, monkeypatch) -> None:
    repo = tmp_path
    config_dir = repo / "configs"
    config_dir.mkdir()
    (repo / "tfconfig.yaml").write_text(
        dedent("""\
            models: {}
            nodes:
              msm3:
                host: msm3-wifi.lan
                ram_gb: 128
                user: shag
                role: inference
                runtime:
                  type: omlx
        """)
    )

    import thunder_forge.cluster.config as config_module

    monkeypatch.setattr(config_module, "find_repo_root", lambda: repo)

    result = runner.invoke(app, ["runtime", "setup-daemon", "--node", "msm3", "--via-su"])

    assert result.exit_code == 1
    assert "--via-su requires --admin-user or nodes.<node>.admin_user" in result.stderr


def test_runtime_restart_apply_reports_restarted(tmp_path: Path, monkeypatch) -> None:
    repo = tmp_path
    config_dir = repo / "configs"
    config_dir.mkdir()
    (repo / "tfconfig.yaml").write_text(
        dedent("""\
            models: {}
            nodes:
              msm3:
                host: msm3-wifi.lan
                fabric_host: true
                ram_gb: 128
                user: shag
                role: inference
                home_dir: /Users/shag
                runtime:
                  type: omlx
                  port: 8018
        """)
    )

    import thunder_forge.cli as cli_module
    import thunder_forge.cluster.config as config_module

    monkeypatch.setattr(config_module, "find_repo_root", lambda: repo)
    monkeypatch.setattr(
        cli_module,
        "run_omlx_process_restart",
        lambda runtime_node, *, apply, timeout: OmlxProcessResult(
            node=runtime_node.host,
            command="/Users/shag/.local/bin/omlx serve --host 0.0.0.0 --port 8018",
            pid_path="/Users/shag/.omlx/run/omlx-8018.pid",
            stdout_log="/Users/shag/Library/Logs/omlx-8018.stdout.log",
            stderr_log="/Users/shag/Library/Logs/omlx-8018.stderr.log",
            commands=["nohup /Users/shag/.local/bin/omlx serve --host 0.0.0.0 --port 8018"],
            pid="12345",
            applied=True,
            health_ok=True,
        ),
    )

    result = runner.invoke(app, ["runtime", "restart", "--node", "msm3", "--apply"])

    assert result.exit_code == 0
    assert "manager: process" in result.stdout
    assert "mode: apply" in result.stdout
    assert "pid: 12345" in result.stdout
    assert "health_ok: yes" in result.stdout
    assert "status: restarted" in result.stdout


def test_runtime_status_reports_omlx_health(tmp_path: Path, monkeypatch) -> None:
    repo = tmp_path
    config_dir = repo / "configs"
    config_dir.mkdir()
    (repo / "tfconfig.yaml").write_text(
        dedent("""\
            models: {}
            nodes:
              msm3:
                host: msm3-wifi.lan
                fabric_host: true
                ram_gb: 128
                user: shag
                role: inference
                runtime:
                  type: omlx
                  port: 8018
        """)
    )

    import thunder_forge.cli as cli_module
    import thunder_forge.cluster.config as config_module

    monkeypatch.setattr(config_module, "find_repo_root", lambda: repo)
    monkeypatch.setattr(
        cli_module,
        "check_omlx_health",
        lambda base_url: OmlxHealthResult(
            base_url=base_url,
            health_ok=True,
            models_ok=True,
            status_ok=True,
            models=["mlx-community/test-model"],
        ),
        raising=False,
    )

    result = runner.invoke(app, ["runtime", "status", "--node", "msm3"])

    assert result.exit_code == 0
    assert "node: msm3" in result.stdout
    assert "runtime: omlx" in result.stdout
    assert "management_host: msm3-wifi.lan" in result.stdout
    assert "fabric_host: true" in result.stdout
    assert "base_url: http://msm3-wifi.lan:8018" in result.stdout
    assert "health: ok" in result.stdout
    assert "models: ok" in result.stdout
    assert "status: ok" in result.stdout
    assert "- mlx-community/test-model" in result.stdout


def test_runtime_smoke_reports_direct_chat_result(tmp_path: Path, monkeypatch) -> None:
    repo = tmp_path
    config_dir = repo / "configs"
    config_dir.mkdir()
    (repo / "tfconfig.yaml").write_text(
        dedent("""\
            models: {}
            nodes:
              msm3:
                host: msm3-wifi.lan
                fabric_host: true
                ram_gb: 128
                user: shag
                role: inference
                runtime:
                  type: omlx
                  port: 8018
        """)
    )

    import thunder_forge.cli as cli_module
    import thunder_forge.cluster.config as config_module

    monkeypatch.setattr(config_module, "find_repo_root", lambda: repo)
    monkeypatch.setattr(
        cli_module,
        "smoke_omlx_chat",
        lambda base_url, *, model, prompt, timeout: OmlxSmokeResult(
            base_url=base_url,
            model=model,
            health_ok=True,
            models_ok=True,
            model_visible=True,
            chat_ok=True,
            models=[model],
            answer="pong",
            latency_ms=123,
        ),
        raising=False,
    )

    result = runner.invoke(
        app,
        [
            "runtime",
            "smoke",
            "--node",
            "msm3",
            "--model",
            "Qwen3-1.7B-4bit",
        ],
    )

    assert result.exit_code == 0
    assert "node: msm3" in result.stdout
    assert "base_url: http://msm3-wifi.lan:8018" in result.stdout
    assert "model: Qwen3-1.7B-4bit" in result.stdout
    assert "health: ok" in result.stdout
    assert "models: ok" in result.stdout
    assert "model_visible: yes" in result.stdout
    assert "chat: ok" in result.stdout
    assert "latency_ms: 123" in result.stdout
    assert "answer: pong" in result.stdout


def test_generate_olla_config_cli_writes_generated_yaml(tmp_path: Path, monkeypatch) -> None:
    repo = tmp_path
    config_dir = repo / "configs"
    config_dir.mkdir()
    assignments = repo / "tfconfig.yaml"
    assignments.write_text(
        dedent("""\
                        models:
                            qwen3-1.7b-omlx-msm3-test:
                                source: { repo: mlx-community/Qwen3-1.7B-4bit }
                                runtime_model_id: Qwen3-1.7B-4bit
                        nodes:
                            studio:
                                host: studio.lan
                                ram_gb: 64
                                user: shag
                                role: gateway
                            msm3:
                                host: msm3-wifi.lan
                                ram_gb: 128
                                user: shag
                                role: inference
                                runtime:
                                    type: omlx
                                    port: 8018
                                models:
                                    - qwen3-1.7b-omlx-msm3-test
                """)
    )

    import thunder_forge.cluster.config as config_module

    monkeypatch.setattr(config_module, "find_repo_root", lambda: repo)

    result = runner.invoke(app, ["generate-olla-config"])

    assert result.exit_code == 0
    output_path = config_dir / "olla-config.yaml"
    assert output_path.exists()
    parsed = yaml_lib.safe_load(output_path.read_text())
    assert parsed["server"]["port"] == 40115
    assert parsed["model_aliases"] == {"qwen3-1.7b-omlx-msm3-test": ["Qwen3-1.7B-4bit"]}
    assert f"Generated {output_path}" in result.stdout


def test_generate_olla_config_cli_uses_service_port(tmp_path: Path, monkeypatch) -> None:
        repo = tmp_path
        config_dir = repo / "configs"
        config_dir.mkdir()
        (repo / "tfconfig.yaml").write_text(
                dedent("""\
                        services:
                            olla:
                                port: 45115
                        models: {}
                        nodes:
                            msm3:
                                host: msm3-wifi.lan
                                ram_gb: 128
                                user: shag
                                role: inference
                                runtime:
                                    type: omlx
                                    port: 8018
                """)
        )

        import thunder_forge.cluster.config as config_module

        monkeypatch.setattr(config_module, "find_repo_root", lambda: repo)

        result = runner.invoke(app, ["generate-olla-config"])

        assert result.exit_code == 0
        parsed = yaml_lib.safe_load((config_dir / "olla-config.yaml").read_text())
        assert parsed["server"]["port"] == 45115


def test_olla_smoke_cli_prints_summary(monkeypatch) -> None:
    import thunder_forge.cli as cli_module

    monkeypatch.setattr(cli_module, "_load_config", lambda: (_cluster_config(), Path.cwd()), raising=False)
    monkeypatch.setattr(
        cli_module,
        "smoke_olla_router",
        lambda *, base_url, model, alias, expected_endpoint, prompt, timeout: OllaSmokeResult(
            base_url=base_url,
            model=model,
            alias=alias,
            health_ok=True,
            endpoints_ok=True,
            models_ok=True,
            chat_ok=True,
            alias_ok=True,
            session_ok=True,
            root_v1_absent=True,
            latency_ms=245,
            olla_endpoint="msm3-omlx-live",
        ),
        raising=False,
    )

    result = runner.invoke(
        app,
        [
            "olla",
            "smoke",
            "--base-url",
            "http://127.0.0.1:40115",
            "--model",
            "Qwen3-1.7B-4bit",
            "--alias",
            "qwen3-1.7b-omlx-msm3-test",
        ],
    )

    assert result.exit_code == 0
    assert "base_url: http://127.0.0.1:40115" in result.stdout
    assert "model: Qwen3-1.7B-4bit" in result.stdout
    assert "alias: qwen3-1.7b-omlx-msm3-test" in result.stdout
    assert "health: ok" in result.stdout
    assert "endpoints: ok" in result.stdout
    assert "models: ok" in result.stdout
    assert "chat: ok" in result.stdout
    assert "alias_routing: ok" in result.stdout
    assert "session: ok" in result.stdout
    assert "root_v1: absent" in result.stdout
    assert "olla_endpoint: msm3-omlx-live" in result.stdout


def test_olla_smoke_cli_passes_expected_endpoint(monkeypatch) -> None:
    import thunder_forge.cli as cli_module

    captured: dict[str, object] = {}

    def fake_smoke(**kwargs) -> OllaSmokeResult:
        captured.update(kwargs)
        return OllaSmokeResult(
            base_url=str(kwargs["base_url"]),
            model=str(kwargs["model"]),
            alias=str(kwargs["alias"]),
            health_ok=True,
            endpoints_ok=True,
            models_ok=True,
            chat_ok=True,
            alias_ok=True,
            session_ok=True,
            root_v1_absent=True,
        )

    monkeypatch.setattr(cli_module, "smoke_olla_router", fake_smoke, raising=False)

    result = runner.invoke(
        app,
        [
            "olla",
            "smoke",
            "--base-url",
            "http://127.0.0.1:40115",
            "--model",
            "Qwen3-1.7B-4bit",
            "--alias",
            "qwen3-1.7b-omlx-msm1-test",
            "--expected-endpoint",
            "msm1-omlx-live",
        ],
    )

    assert result.exit_code == 0
    assert captured["expected_endpoint"] == "msm1-omlx-live"


def test_olla_dev_smoke_cli_prints_summary(monkeypatch) -> None:
    import thunder_forge.cli as cli_module

    fake_smoke = OllaSmokeResult(
        base_url="http://127.0.0.1:40115",
        model="Qwen3-1.7B-4bit",
        alias="qwen3-1.7b-omlx-msm3-test",
        health_ok=True,
        endpoints_ok=True,
        models_ok=True,
        chat_ok=True,
        alias_ok=True,
        session_ok=True,
        root_v1_absent=True,
        latency_ms=245,
        olla_endpoint="msm3-omlx-live",
    )
    fake_dev_smoke = OllaDevSmokeResult(
        config_generated=True,
        config_path="/tmp/configs/olla-config.yaml",
        olla_started=True,
        olla_healthy=True,
        smoke_result=fake_smoke,
        olla_terminated=True,
    )

    monkeypatch.setattr(cli_module, "_load_config", lambda: (_cluster_config(), Path.cwd()), raising=False)
    monkeypatch.setattr(
        cli_module,
        "dev_smoke_olla",
        lambda **kw: fake_dev_smoke,
        raising=False,
    )

    result = runner.invoke(
        app,
        [
            "olla",
            "dev-smoke",
            "--binary",
            "/tmp/olla",
            "--model",
            "Qwen3-1.7B-4bit",
            "--alias",
            "qwen3-1.7b-omlx-msm3-test",
        ],
    )

    assert result.exit_code == 0
    assert "config_generated: yes" in result.stdout
    assert "olla_started: yes" in result.stdout
    assert "olla_healthy: yes" in result.stdout
    assert "olla_terminated: yes" in result.stdout
    assert "health: ok" in result.stdout
    assert "chat: ok" in result.stdout


def test_olla_dev_smoke_cli_passes_expected_endpoint(monkeypatch) -> None:
    import thunder_forge.cli as cli_module

    fake_dev_smoke = OllaDevSmokeResult(
        config_generated=True,
        config_path="/tmp/configs/olla-config.yaml",
        olla_started=True,
        olla_healthy=True,
        smoke_result=OllaSmokeResult(
            base_url="http://127.0.0.1:40115",
            model="Qwen3-1.7B-4bit",
            alias="qwen3-1.7b-omlx-msm1-test",
            health_ok=True,
            endpoints_ok=True,
            models_ok=True,
            chat_ok=True,
            alias_ok=True,
            session_ok=True,
            root_v1_absent=True,
        ),
        olla_terminated=True,
    )
    captured: dict[str, object] = {}

    def fake_dev_smoke_func(**kwargs) -> OllaDevSmokeResult:
        captured.update(kwargs)
        return fake_dev_smoke

    monkeypatch.setattr(cli_module, "dev_smoke_olla", fake_dev_smoke_func, raising=False)

    result = runner.invoke(
        app,
        [
            "olla",
            "dev-smoke",
            "--binary",
            "/tmp/olla",
            "--model",
            "Qwen3-1.7B-4bit",
            "--alias",
            "qwen3-1.7b-omlx-msm1-test",
            "--expected-endpoint",
            "msm1-omlx-live",
        ],
    )

    assert result.exit_code == 0
    assert captured["expected_endpoint"] == "msm1-omlx-live"


def test_runtime_install_dry_run_prints_plist_and_commands(tmp_path: Path, monkeypatch) -> None:
    repo = tmp_path
    config_dir = repo / "configs"
    config_dir.mkdir()
    (repo / "tfconfig.yaml").write_text(
        dedent(
            """\
            models: {}
            nodes:
              msm3:
                host: msm3-wifi.lan
                fabric_host: true
                ram_gb: 128
                user: shag
                role: inference
                home_dir: /Users/shag
                runtime:
                  type: omlx
                  port: 8018
        """
        )
    )

    import thunder_forge.cluster.config as config_module

    monkeypatch.setattr(config_module, "find_repo_root", lambda: repo)

    result = runner.invoke(app, ["runtime", "install", "--node", "msm3", "--dry-run"])

    assert result.exit_code == 0
    assert "node: msm3" in result.stdout
    assert "plist_path: ~/Library/LaunchAgents/com.thunder-forge.omlx-8018.plist" in result.stdout
    assert "label: com.thunder-forge.omlx-8018" in result.stdout
    assert "mode: dry-run" in result.stdout
    assert "com.thunder-forge.omlx-8018" in result.stdout
    assert "/Users/shag/.local/bin/omlx" in result.stdout
    assert "bootout" in result.stdout
    assert "bootstrap" in result.stdout
