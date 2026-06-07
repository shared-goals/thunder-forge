"""CLI tests for node-level runtime commands."""

import json
import platform
from pathlib import Path
from textwrap import dedent
from types import SimpleNamespace

import yaml as yaml_lib
from typer.testing import CliRunner

from thunder_forge.cli import app
from thunder_forge.cluster.config import ClusterConfig, ServiceConfig
from thunder_forge.cluster.edge import EdgeSmokeResult
from thunder_forge.cluster.gateway import GatewayDaemonSetupResult
from thunder_forge.cluster.olla import OllaDevSmokeResult, OllaSmokeResult
from thunder_forge.cluster.omlx import (
    OmlxDaemonSetupResult,
    OmlxHealthResult,
    OmlxProcessResult,
    OmlxSmokeResult,
    OmlxToolingResult,
)
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
              infer-03:
                host: infer-03.lan
                fabric_host: true
                ram_gb: 128
                user: shag
                roles: [inference]
                home_dir: /Users/shag
                runtime:
                  type: omlx
                  port: 8018
        """)
    )

    import thunder_forge.cluster.config as config_module

    monkeypatch.setattr(config_module, "find_repo_root", lambda: repo)
    result = runner.invoke(app, ["runtime", "start", "--node", "infer-03", "--dry-run"])

    assert result.exit_code == 0
    assert "node: infer-03" in result.stdout
    assert "runtime: omlx" in result.stdout
    assert "management_host: infer-03.lan" in result.stdout
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
              infer-03:
                host: infer-03.lan
                fabric_host: true
                ram_gb: 128
                user: shag
                roles: [inference]
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
    monkeypatch.setattr(cli_module, "_omlx_version_for_node", lambda runtime_node: "0.4.2.dev2")
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

    result = runner.invoke(app, ["runtime", "start", "--node", "infer-03", "--apply"])

    assert result.exit_code == 0
    assert "node: infer-03" in result.stdout
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
              infer-03:
                host: infer-03.lan
                fabric_host: true
                ram_gb: 128
                user: shag
                roles: [inference]
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
        lambda base_url: OmlxHealthResult(
            base_url=base_url,
            health_ok=True,
            models_ok=True,
            models=["gpt-oss-20b-MXFP4-Q8"],
        ),
    )
    monkeypatch.setattr(cli_module, "run_omlx_runtime_start", fake_start)

    result = runner.invoke(app, ["runtime", "start", "--node", "infer-03", "--apply"])

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
              infer-03:
                host: infer-03.lan
                fabric_host: true
                ram_gb: 128
                user: shag
                roles: [inference]
                home_dir: /Users/shag
                runtime:
                  type: omlx
                  port: 8018
        """)
    )

    import thunder_forge.cluster.config as config_module

    monkeypatch.setattr(config_module, "find_repo_root", lambda: repo)

    result = runner.invoke(app, ["runtime", "restart", "--node", "infer-03", "--dry-run"])

    assert result.exit_code == 0
    assert "node: infer-03" in result.stdout
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
              infer-03:
                host: infer-03.lan
                fabric_host: true
                ram_gb: 128
                user: shag
                roles: [inference]
                home_dir: /Users/shag
                runtime:
                  type: omlx
                  port: 8018
        """)
    )

    import thunder_forge.cluster.config as config_module

    monkeypatch.setattr(config_module, "find_repo_root", lambda: repo)

    result = runner.invoke(app, ["runtime", "restart", "--node", "infer-03", "--manager", "daemon", "--dry-run"])

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
                host: 0.0.0.0
                port: 45116
                access_log: logs/custom-edge.jsonl
            models: {}
            nodes:
              gateway-cache-01:
                host: gateway-cache-01.lan
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
    assert calls[0]["edge_host"] == "0.0.0.0"
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


def test_cluster_prepare_dry_run_prints_unified_plan(tmp_path: Path, monkeypatch) -> None:
    import thunder_forge.cluster.config as config_module

    repo = tmp_path
    (repo / "tfconfig.yaml").write_text(
        dedent("""\
                        services:
                            frontend:
                                admin_user: serpo
                            edge:
                                host: 0.0.0.0
                        models: {}
                        nodes:
                            gateway-cache-01:
                                host: gateway-cache-01.lan
                                ram_gb: 128
                                roles: [gateway, cache]
                                user: shag
                                admin_user: serpo
                            infer-03:
                                host: infer-03.lan
                                ram_gb: 128
                                roles: [inference]
                                user: shag
                                admin_user: admin
                                runtime:
                                    type: omlx
                """)
    )
    monkeypatch.setattr(config_module, "find_repo_root", lambda: repo)

    result = runner.invoke(app, ["cluster", "prepare"])

    assert result.exit_code == 0
    assert "Thunder Forge cluster prepare" in result.stdout
    assert "gateway: gateway-cache-01 (gateway-cache-01.lan) -> Olla + TF edge" in result.stdout
    assert "cache: gateway-cache-01 (gateway-cache-01.lan) -> oMLX model hub" in result.stdout
    assert "inference: infer-03 -> oMLX LaunchDaemon" in result.stdout
    assert "would: ensure Olla v0.0.27" in result.stdout
    assert "would: ensure/upgrade oMLX CLI at /Users/shag/.local/bin/omlx" in result.stdout
    assert "would: bootstrap infer-03 ssh=shag@infer-03.lan su=admin" in result.stdout


def test_cluster_prepare_apply_runs_gateway_cache_and_inference(tmp_path: Path, monkeypatch) -> None:
    import thunder_forge.cli as cli_module
    import thunder_forge.cluster.config as config_module

    repo = tmp_path
    (repo / "tfconfig.yaml").write_text(
        dedent("""\
            services:
              frontend:
                admin_user: serpo
            models: {}
            nodes:
              gateway-cache-01:
                host: gateway-cache-01.lan
                ram_gb: 128
                roles: [gateway, cache]
                user: shag
                admin_user: serpo
              infer-03:
                host: infer-03.lan
                ram_gb: 128
                roles: [inference]
                user: shag
                admin_user: admin
                runtime:
                  type: omlx
        """)
    )
    calls: list[str] = []
    gateway_calls: list[dict] = []

    def fake_ensure_olla_binary(**kwargs):
        calls.append("olla")
        kwargs["progress"]("olla: already current at .tmp/olla-bin/olla")
        return SimpleNamespace(binary_path=repo / ".tmp/olla-bin/olla")

    def fake_write_generated_olla_config(config, *, repo_root, port=None):
        calls.append("config")
        return repo_root / "configs/olla-config.yaml"

    def fake_run_gateway_daemon_setup(**kwargs):
        calls.append("gateway")
        gateway_calls.append(kwargs)
        kwargs["progress"]("health: gateway ok")
        return GatewayDaemonSetupResult(
            user=kwargs["user"],
            admin_user=kwargs["admin_user"],
            sudoers_path="/etc/sudoers.d/thunder-forge",
            script_path=str(repo / ".tmp/run/thunder-forge-gateway-daemon-setup.sh"),
            applied=True,
            sudoers_verified=True,
            service_labels_verified=True,
            health_ok=True,
        )

    def fake_ensure_cache_hub_dir(*, progress):
        calls.append("cache")
        progress("cache: oMLX model hub ready at /Users/shag/.omlx/models")
        return Path("/Users/shag/.omlx/models")

    def fake_ensure_omlx_tooling(runtime_node, **kwargs):
        calls.append(f"tooling:{runtime_node.host}")
        assert kwargs["upgrade"] is True
        kwargs["progress"]("tooling: oMLX CLI ready at /Users/shag/.local/bin/omlx")
        return OmlxToolingResult(
            node=runtime_node.host,
            uv_path="/Users/shag/.local/bin/uv",
            omlx_path="/Users/shag/.local/bin/omlx",
            tool_spec="git+https://github.com/jundot/omlx.git",
            applied=True,
            verified=True,
        )

    def fake_run_omlx_daemon_setup(runtime_node, **kwargs):
        calls.append("inference")
        kwargs["progress"]("health: oMLX ok (http://infer-03.lan:8018)")
        return OmlxDaemonSetupResult(
            node=runtime_node.host,
            label="com.thunder-forge.omlx-8018",
            plist_path="/Library/LaunchDaemons/com.thunder-forge.omlx-8018.plist",
            staging_plist_path="/Users/shag/.omlx/run/com.thunder-forge.omlx-8018.plist",
            sudoers_path="/etc/sudoers.d/thunder-forge",
            script_path="/tmp/thunder-forge-setup-com.thunder-forge.omlx-8018.sh",
            admin_user=kwargs["admin_user"],
            ssh_user=runtime_node.user,
            via_su=kwargs["via_su"],
            applied=True,
            sudoers_verified=True,
            service_label_verified=True,
            health_ok=True,
        )

    monkeypatch.setattr(config_module, "find_repo_root", lambda: repo)
    monkeypatch.setattr(cli_module, "ensure_olla_binary", fake_ensure_olla_binary)
    monkeypatch.setattr(cli_module, "write_generated_olla_config", fake_write_generated_olla_config)
    monkeypatch.setattr(cli_module, "run_gateway_daemon_setup", fake_run_gateway_daemon_setup)
    monkeypatch.setattr(cli_module, "_is_local_host", lambda host: True)
    monkeypatch.setattr(cli_module, "ensure_cache_hub_dir", fake_ensure_cache_hub_dir)
    monkeypatch.setattr(cli_module, "ensure_omlx_tooling", fake_ensure_omlx_tooling)
    monkeypatch.setattr(cli_module, "run_omlx_daemon_setup", fake_run_omlx_daemon_setup)

    result = runner.invoke(app, ["cluster", "prepare", "--apply"])

    assert result.exit_code == 0
    assert calls == [
        "olla",
        "config",
        "gateway",
        "tooling:gateway-cache-01.lan",
        "cache",
        "tooling:infer-03.lan",
        "inference",
    ]
    assert gateway_calls[0]["edge_host"] == "0.0.0.0"
    assert "== Gateway: gateway-cache-01 (gateway-cache-01.lan) ==" in result.stdout
    frontend_reason = (
        "install Olla + TF edge systemd services"
        if platform.system() == "Linux"
        else "install Olla + TF edge LaunchDaemons"
    )
    assert f"auth: operator=shag admin=serpo reason={frontend_reason}" in result.stdout
    assert "== Cache Hub: gateway-cache-01 (gateway-cache-01.lan) ==" in result.stdout
    assert "tooling_path: /Users/shag/.local/bin/omlx" in result.stdout
    assert "== Inference: infer-03 (infer-03.lan) ==" in result.stdout
    assert "auth: ssh=shag@infer-03.lan method=su admin=admin reason=install oMLX LaunchDaemon" in result.stdout
    assert "tooling: oMLX CLI ready at /Users/shag/.local/bin/omlx" in result.stdout
    assert "status: cluster prepare complete" in result.stdout


def test_cluster_prepare_dry_run_uses_latest_olla_when_config_is_unpinned(tmp_path: Path, monkeypatch) -> None:
        import thunder_forge.cluster.config as config_module

        repo = tmp_path
        (repo / "tfconfig.yaml").write_text(
                dedent("""\
                        services:
                            olla:
                                os: linux
                                arch: arm64
                        models: {}
                        nodes:
                            rock:
                                host: rock.lan
                                ram_gb: 32
                                roles: [gateway]
                                user: shag
                """)
        )
        monkeypatch.setattr(config_module, "find_repo_root", lambda: repo)

        result = runner.invoke(app, ["cluster", "prepare", "--dry-run"])

        assert result.exit_code == 0
        assert "would: ensure Olla latest" in result.stdout


def test_cluster_prepare_apply_uses_latest_olla_when_config_is_unpinned(tmp_path: Path, monkeypatch) -> None:
        import thunder_forge.cli as cli_module
        import thunder_forge.cluster.config as config_module

        repo = tmp_path
        (repo / "tfconfig.yaml").write_text(
                dedent("""\
                        services:
                            frontend:
                                admin_user: serpo
                            olla:
                                os: linux
                                arch: arm64
                        models: {}
                        nodes:
                            rock:
                                host: rock.lan
                                ram_gb: 32
                                roles: [gateway]
                                user: shag
                                admin_user: serpo
                """)
        )

        def fake_ensure_olla_binary(**kwargs):
                assert kwargs["version"] == "latest"
                kwargs["progress"]("olla: upgraded .tmp/olla-bin/olla")
                return SimpleNamespace(binary_path=repo / ".tmp/olla-bin/olla")

        def fake_write_generated_olla_config(config, *, repo_root, port=None):
                return repo_root / "configs/olla-config.yaml"

        def fake_run_gateway_daemon_setup(**kwargs):
                kwargs["progress"]("health: gateway ok")
                return GatewayDaemonSetupResult(
                        user=kwargs["user"],
                        admin_user=kwargs["admin_user"],
                        sudoers_path="/etc/sudoers.d/thunder-forge",
                        script_path=str(repo / ".tmp/run/thunder-forge-gateway-daemon-setup.sh"),
                        applied=True,
                        sudoers_verified=True,
                        service_labels_verified=True,
                        health_ok=True,
                )

        monkeypatch.setattr(config_module, "find_repo_root", lambda: repo)
        monkeypatch.setattr(cli_module, "ensure_olla_binary", fake_ensure_olla_binary)
        monkeypatch.setattr(cli_module, "write_generated_olla_config", fake_write_generated_olla_config)
        monkeypatch.setattr(cli_module, "run_gateway_daemon_setup", fake_run_gateway_daemon_setup)

        result = runner.invoke(app, ["cluster", "prepare", "--apply"])

        assert result.exit_code == 0
        assert "status: cluster prepare complete" in result.stdout


def test_cluster_prepare_apply_uses_configured_local_olla_binary(tmp_path: Path, monkeypatch) -> None:
    import thunder_forge.cli as cli_module
    import thunder_forge.cluster.config as config_module

    repo = tmp_path
    local_binary = repo / "olla" / "bin" / "olla"
    local_binary.parent.mkdir(parents=True, exist_ok=True)
    local_binary.write_text("#!/bin/sh\necho local\n")
    local_binary.chmod(0o755)
    (repo / "olla" / "config" / "profiles").mkdir(parents=True, exist_ok=True)

    (repo / "tfconfig.yaml").write_text(
        dedent("""\
            services:
              frontend:
                admin_user: serpo
              olla:
                local_binary: olla/bin/olla
            models: {}
            nodes:
              rock:
                host: rock.lan
                ram_gb: 32
                roles: [gateway]
                user: shag
                admin_user: serpo
        """)
    )

    def fail_ensure_olla_binary(**kwargs):
        raise AssertionError("release downloader should not run when services.olla.local_binary is set")

    def fake_write_generated_olla_config(config, *, repo_root, port=None):
        return repo_root / "configs/olla-config.yaml"

    gateway_calls: list[dict] = []

    def fake_run_gateway_daemon_setup(**kwargs):
        gateway_calls.append(kwargs)
        kwargs["progress"]("health: gateway ok")
        return GatewayDaemonSetupResult(
            user=kwargs["user"],
            admin_user=kwargs["admin_user"],
            sudoers_path="/etc/sudoers.d/thunder-forge",
            script_path=str(repo / ".tmp/run/thunder-forge-gateway-daemon-setup.sh"),
            applied=True,
            sudoers_verified=True,
            service_labels_verified=True,
            health_ok=True,
        )

    monkeypatch.setattr(config_module, "find_repo_root", lambda: repo)
    monkeypatch.setattr(cli_module, "ensure_olla_binary", fail_ensure_olla_binary)
    monkeypatch.setattr(cli_module, "write_generated_olla_config", fake_write_generated_olla_config)
    monkeypatch.setattr(cli_module, "run_gateway_daemon_setup", fake_run_gateway_daemon_setup)

    result = runner.invoke(app, ["cluster", "prepare", "--apply"])

    assert result.exit_code == 0
    assert f"local_olla_binary: {local_binary}" in result.stdout
    assert f"local_olla_workdir: {repo / 'olla'}" in result.stdout
    assert gateway_calls[0]["binary"] == local_binary
    assert gateway_calls[0]["olla_working_directory"] == repo / "olla"
    assert "status: cluster prepare complete" in result.stdout


def test_cluster_prepare_apply_prepares_remote_cache_hub(tmp_path: Path, monkeypatch) -> None:
    import subprocess

    import thunder_forge.cli as cli_module
    import thunder_forge.cluster.config as config_module

    repo = tmp_path
    (repo / "tfconfig.yaml").write_text(
        dedent("""\
            services:
              frontend:
                admin_user: serpo
            models: {}
            nodes:
              rock:
                host: rock.lan
                ram_gb: 32
                roles: [gateway]
                user: shag
                admin_user: serpo
              studio:
                host: studio.lan
                ram_gb: 64
                roles: [cache]
                user: shag
        """)
    )
    calls: list[str] = []
    ssh_calls = []

    def fake_ensure_olla_binary(**kwargs):
        calls.append("olla")
        kwargs["progress"]("olla: already current at .tmp/olla-bin/olla")
        return SimpleNamespace(binary_path=repo / ".tmp/olla-bin/olla")

    def fake_write_generated_olla_config(config, *, repo_root, port=None):
        calls.append("config")
        return repo_root / "configs/olla-config.yaml"

    def fake_run_gateway_daemon_setup(**kwargs):
        calls.append("gateway")
        kwargs["progress"]("health: gateway ok")
        return GatewayDaemonSetupResult(
            user=kwargs["user"],
            admin_user=kwargs["admin_user"],
            sudoers_path="/etc/sudoers.d/thunder-forge",
            script_path=str(repo / ".tmp/run/thunder-forge-gateway-daemon-setup.sh"),
            applied=True,
            sudoers_verified=True,
            service_labels_verified=True,
            health_ok=True,
        )

    def fake_ensure_omlx_tooling(runtime_node, **kwargs):
        calls.append(f"tooling:{runtime_node.host}")
        assert kwargs["upgrade"] is True
        kwargs["progress"]("tooling: oMLX CLI ready at /Users/shag/.local/bin/omlx")
        return OmlxToolingResult(
            node=runtime_node.host,
            uv_path="/Users/shag/.local/bin/uv",
            omlx_path="/Users/shag/.local/bin/omlx",
            tool_spec="git+https://github.com/jundot/omlx.git",
            applied=True,
            verified=True,
        )

    def fake_ssh_run(user, ip, cmd, **kwargs):
        ssh_calls.append((user, ip, cmd, kwargs))
        return subprocess.CompletedProcess(args=cmd, returncode=0)

    monkeypatch.setattr(config_module, "find_repo_root", lambda: repo)
    monkeypatch.setattr(cli_module, "ensure_olla_binary", fake_ensure_olla_binary)
    monkeypatch.setattr(cli_module, "write_generated_olla_config", fake_write_generated_olla_config)
    monkeypatch.setattr(cli_module, "run_gateway_daemon_setup", fake_run_gateway_daemon_setup)
    monkeypatch.setattr(cli_module, "ensure_omlx_tooling", fake_ensure_omlx_tooling)
    monkeypatch.setattr(cli_module, "ssh_run", fake_ssh_run)
    monkeypatch.setattr(cli_module, "_is_local_host", lambda host: host == "rock.lan")
    monkeypatch.setattr(
        cli_module,
        "ensure_cache_hub_dir",
        lambda **kwargs: (_ for _ in ()).throw(AssertionError("local cache setup should not run")),
    )

    result = runner.invoke(app, ["cluster", "prepare", "--apply"])

    assert result.exit_code == 0
    assert calls == ["olla", "config", "gateway", "tooling:studio.lan"]
    assert ssh_calls[0][0] == "shag"
    assert ssh_calls[0][1] == "studio.lan"
    assert "TF_CACHE_OMLX_MODELS_DIR" in ssh_calls[0][2]
    assert "/bin/mkdir -p" in ssh_calls[0][2]
    assert "tooling_path: /Users/shag/.local/bin/omlx" in result.stdout
    assert "cache_exec: ensuring cache hub on studio (studio.lan)" in result.stdout
    assert "status: cluster prepare complete" in result.stdout


def test_cluster_restart_apply_dispatches_gateway_and_inference(tmp_path: Path, monkeypatch) -> None:
    import thunder_forge.cli as cli_module
    import thunder_forge.cluster.config as config_module

    repo = tmp_path
    (repo / "tfconfig.yaml").write_text(
        dedent("""\
            services:
              olla:
                port: 45115
              edge:
                host: 0.0.0.0
                port: 45116
            models: {}
            nodes:
              gateway-cache-01:
                host: gateway-cache-01.lan
                ram_gb: 128
                roles: [gateway, cache]
                user: shag
              infer-03:
                host: infer-03.lan
                ram_gb: 128
                roles: [inference]
                user: shag
                runtime:
                  type: omlx
        """)
    )
    calls: list[str] = []
    edge_calls: list[dict] = []

    def fake_write_generated_olla_config(config, *, repo_root, port=None):
        calls.append("config")
        return repo_root / "configs/olla-config.yaml"

    def fake_service(**kwargs):
        calls.append(kwargs.get("service", "service"))
        return LaunchdServiceResult(
            service=kwargs.get("service", "service"),
            label=f"com.thunder-forge.{kwargs.get('service', 'service')}",
            plist_path="/Library/LaunchDaemons/com.thunder-forge.test.plist",
            applied=True,
            service_label_verified=True,
            health_ok=True,
        )

    monkeypatch.setattr(config_module, "find_repo_root", lambda: repo)
    monkeypatch.setattr(cli_module, "write_generated_olla_config", fake_write_generated_olla_config)
    monkeypatch.setattr(cli_module, "run_olla_service_restart", lambda **kwargs: fake_service(service="olla", **kwargs))

    def fake_edge_restart(**kwargs):
        edge_calls.append(kwargs)
        return fake_service(service="edge", **kwargs)

    monkeypatch.setattr(cli_module, "run_edge_service_restart", fake_edge_restart)
    monkeypatch.setattr(cli_module, "run_omlx_daemon_restart", lambda *args, **kwargs: fake_service(service="omlx"))

    result = runner.invoke(app, ["cluster", "restart", "--apply"])

    assert result.exit_code == 0
    assert calls == ["config", "olla", "edge", "omlx"]
    assert edge_calls[0]["host"] == "0.0.0.0"
    assert "Thunder Forge cluster restart" in result.stdout
    assert "status: cluster restart complete" in result.stdout


def test_cluster_status_reports_inference_health(tmp_path: Path, monkeypatch) -> None:
    import thunder_forge.cli as cli_module

    payload = {
        "ok": True,
        "target": "all",
        "gateway": None,
        "inference": [
            {
                "name": "infer-03",
                "host": "infer-03.lan",
                "health": "ok",
                "models": "ok",
                "omlx_version": "0.4.2.dev2",
                "served_models": ["memory"],
                "hot_loaded_models": ["memory"],
                "errors": [],
            }
        ],
        "summary": {"omlx_upgrade_hint": "no (versions aligned)"},
    }
    monkeypatch.setattr(cli_module, "_fetch_cluster_status_payload", lambda config, *, target: payload)

    result = runner.invoke(app, ["cluster", "status"])

    assert result.exit_code == 0
    assert "Thunder Forge cluster status" in result.stdout
    assert "infer-03: health=ok models=ok" in result.stdout
    assert "omlx_version: 0.4.2.dev2" in result.stdout
    assert "served_models: memory" in result.stdout
    assert "hot_loaded_models: memory" in result.stdout
    assert "omlx_upgrade_hint: no (versions aligned)" in result.stdout


def test_cluster_status_reports_gateway_and_runtime_versions(tmp_path: Path, monkeypatch) -> None:
    import thunder_forge.cli as cli_module

    payload = {
        "ok": True,
        "target": "all",
        "gateway": {
            "name": "rock",
            "host": "rock.lan",
            "olla_version": "v0.0.27",
            "latest_olla_version": "v0.0.27",
            "upgrade": "no",
        },
        "inference": [
            {
                "name": "infer-03",
                "host": "infer-03.lan",
                "health": "ok",
                "models": "ok",
                "omlx_version": "0.4.2.dev2",
                "served_models": ["memory"],
                "hot_loaded_models": ["memory"],
                "errors": [],
            }
        ],
        "summary": {"omlx_upgrade_hint": "no (versions aligned)"},
    }
    monkeypatch.setattr(cli_module, "_fetch_cluster_status_payload", lambda config, *, target: payload)

    result = runner.invoke(app, ["cluster", "status"])

    assert result.exit_code == 0
    assert "rock: olla_version=v0.0.27 latest=v0.0.27 upgrade=no" in result.stdout
    assert "infer-03: health=ok models=ok" in result.stdout
    assert "omlx_version: 0.4.2.dev2" in result.stdout
    assert "omlx_upgrade_hint: no (versions aligned)" in result.stdout


def test_cluster_status_json_output_emits_payload(monkeypatch) -> None:
    import thunder_forge.cli as cli_module

    payload = {
        "ok": True,
        "target": "msm1",
        "gateway": None,
        "inference": [],
        "summary": {"omlx_upgrade_hint": "no (versions aligned)"},
    }
    monkeypatch.setattr(cli_module, "_fetch_cluster_status_payload", lambda config, *, target: payload)

    result = runner.invoke(app, ["cluster", "status", "msm1", "--json"])

    assert result.exit_code == 0
    assert json.loads(result.stdout) == payload


def test_cluster_status_returns_success_for_unhealthy_payload(monkeypatch) -> None:
    import thunder_forge.cli as cli_module

    payload = {
        "ok": False,
        "target": "all",
        "gateway": None,
        "inference": [],
        "summary": {},
    }
    monkeypatch.setattr(cli_module, "_fetch_cluster_status_payload", lambda config, *, target: payload)

    result = runner.invoke(app, ["cluster", "status"])

    assert result.exit_code == 0
    assert "Thunder Forge cluster status" in result.stdout


def test_probe_node_version_parses_stderr_output(tmp_path: Path, monkeypatch) -> None:
    import subprocess

    import thunder_forge.cli as cli_module
    import thunder_forge.cluster.config as config_module

    repo = tmp_path
    (repo / "tfconfig.yaml").write_text(
        dedent("""\
            models: {}
            nodes:
              infer-03:
                host: infer-03.lan
                ram_gb: 128
                roles: [inference]
                user: shag
                runtime:
                  type: omlx
        """)
    )
    monkeypatch.setattr(config_module, "find_repo_root", lambda: repo)

    def fake_ssh_run(*args, **kwargs):
        return subprocess.CompletedProcess(args="omlx --version", returncode=0, stdout="", stderr="omlx 0.4.2.dev2\n")

    monkeypatch.setattr(cli_module, "ssh_run", fake_ssh_run)

    config, _ = cli_module._load_config()
    node = cli_module._get_runtime_node(config, "infer-03")
    version = cli_module._omlx_version_for_node(node)

    assert version == "0.4.2.dev2"


def test_omlx_version_uses_default_user_home_when_unset(tmp_path: Path, monkeypatch) -> None:
    import subprocess

    import thunder_forge.cli as cli_module
    import thunder_forge.cluster.config as config_module

    repo = tmp_path
    (repo / "tfconfig.yaml").write_text(
        dedent("""\
            models: {}
            nodes:
              infer-03:
                host: infer-03.lan
                ram_gb: 128
                roles: [inference]
                user: shag
                runtime:
                  type: omlx
        """)
    )
    monkeypatch.setattr(config_module, "find_repo_root", lambda: repo)

    captured = {"cmd": ""}

    def fake_ssh_run(user, ip, cmd, **kwargs):
        captured["cmd"] = cmd
        return subprocess.CompletedProcess(args=cmd, returncode=0, stdout="omlx 0.4.2.dev2\n", stderr="")

    monkeypatch.setattr(cli_module, "ssh_run", fake_ssh_run)

    config, _ = cli_module._load_config()
    node = cli_module._get_runtime_node(config, "infer-03")
    node.home_dir = None
    version = cli_module._omlx_version_for_node(node)

    assert version == "0.4.2.dev2"
    assert "/Users/shag/.local/bin/omlx" in captured["cmd"]


def test_cluster_smoke_runs_runtime_olla_and_edge(tmp_path: Path, monkeypatch) -> None:
    import thunder_forge.cli as cli_module
    import thunder_forge.cluster.config as config_module

    repo = tmp_path
    (repo / "tfconfig.yaml").write_text(
        """operations:
  smoke:
    alias: memory
    client_id: admin
models:
    memory:
        source:
            repo: mlx-community/gpt-oss-20b-MXFP4-Q8
        runtime_model_id: gpt-oss-20b-MXFP4-Q8
nodes:
    infer-03:
        host: infer-03.lan
        ram_gb: 128
        roles: [inference]
        user: shag
        runtime:
            type: omlx
"""
    )
    monkeypatch.setattr(config_module, "find_repo_root", lambda: repo)
    monkeypatch.setattr(
        cli_module,
        "check_omlx_health",
        lambda base_url: OmlxHealthResult(
            base_url=base_url,
            health_ok=True,
            models_ok=True,
            models=["gpt-oss-20b-MXFP4-Q8"],
        ),
    )
    captured_olla: dict[str, object] = {}

    def fake_smoke_olla_router(**kwargs) -> OllaSmokeResult:
        captured_olla.update(kwargs)
        return OllaSmokeResult(
            base_url=kwargs["base_url"],
            model=kwargs["model"],
            alias=kwargs["alias"],
            health_ok=True,
            endpoints_ok=True,
            models_ok=True,
            chat_ok=True,
            alias_ok=True,
            session_ok=True,
            root_v1_absent=True,
        )

    monkeypatch.setattr(cli_module, "smoke_olla_router", fake_smoke_olla_router)
    monkeypatch.setattr(cli_module, "edge_api_key_from_env", lambda **kwargs: ("TF_USER_ADMIN", "secret"))
    monkeypatch.setattr(
        cli_module,
        "smoke_edge_contract",
        lambda **kwargs: EdgeSmokeResult(
            base_url=kwargs["base_url"],
            model=kwargs["model"],
            missing_auth_401=True,
            invalid_auth_401=True,
            models_ok=True,
            chat_ok=True,
            session_ok=True,
        ),
    )

    result = runner.invoke(
        app,
        ["cluster", "smoke", "infer-03"],
    )

    assert result.exit_code == 0
    assert "runtime infer-03: health=ok models=ok model_visible=yes" in result.stdout
    assert "olla: health=ok chat=ok alias=ok" in result.stdout
    assert "edge: auth=ok chat=ok session=ok" in result.stdout
    assert "status: cluster smoke complete" in result.stdout
    assert captured_olla["expected_endpoint"] == "infer-03-omlx-live"


def test_cluster_smoke_fails_when_runtime_model_is_missing(tmp_path: Path, monkeypatch) -> None:
    import thunder_forge.cli as cli_module
    import thunder_forge.cluster.config as config_module

    repo = tmp_path
    (repo / "tfconfig.yaml").write_text(
        dedent("""\
            models: {}
            nodes:
              infer-03:
                host: infer-03.lan
                ram_gb: 128
                roles: [inference]
                user: shag
                runtime:
                  type: omlx
        """)
    )
    monkeypatch.setattr(config_module, "find_repo_root", lambda: repo)
    monkeypatch.setattr(
        cli_module,
        "check_omlx_health",
        lambda base_url: OmlxHealthResult(base_url=base_url, health_ok=True, models_ok=True, models=[]),
    )
    monkeypatch.setattr(
        cli_module,
        "smoke_olla_router",
        lambda **kwargs: OllaSmokeResult(
            base_url=kwargs["base_url"],
            model=kwargs["model"],
            alias=kwargs["alias"],
            health_ok=True,
            endpoints_ok=True,
            models_ok=True,
            chat_ok=True,
            alias_ok=True,
            session_ok=True,
            root_v1_absent=True,
        ),
    )
    monkeypatch.setattr(cli_module, "edge_api_key_from_env", lambda **kwargs: ("TF_USER_ADMIN", "secret"))
    monkeypatch.setattr(
        cli_module,
        "smoke_edge_contract",
        lambda **kwargs: EdgeSmokeResult(
            base_url=kwargs["base_url"],
            model=kwargs["model"],
            missing_auth_401=True,
            invalid_auth_401=True,
            models_ok=True,
            chat_ok=True,
            session_ok=True,
        ),
    )

    result = runner.invoke(
        app,
        ["cluster", "smoke", "--model", "gpt-oss-20b-MXFP4-Q8", "--alias", "memory", "--client-id", "admin"],
    )

    assert result.exit_code == 1
    assert "runtime infer-03: health=ok models=fail model_visible=no" in result.stdout
    assert "Error: infer-03: model 'gpt-oss-20b-MXFP4-Q8' is not visible" in result.stderr


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


def test_service_restart_edge_systemd_dry_run_prints_unit_plan(tmp_path: Path, monkeypatch) -> None:
    import thunder_forge.cli as cli_module
    import thunder_forge.cluster.config as config_module

    repo = tmp_path
    (repo / "tfconfig.yaml").write_text(
        dedent("""\
            services:
              edge:
                port: 45116
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
            label="com.thunder-forge.edge-45116.service",
            plist_path="/etc/systemd/system/com.thunder-forge.edge-45116.service",
            staging_plist_path=str(repo / ".tmp/run/com.thunder-forge.edge-45116.service"),
            plist_content="[Unit]\nDescription=TF edge\n",
            commands=["/usr/bin/sudo -n /bin/systemctl restart com.thunder-forge.edge-45116.service"],
        )

    monkeypatch.setattr(config_module, "find_repo_root", lambda: repo)
    monkeypatch.setattr(cli_module, "run_edge_service_restart", fake_run_edge_service_restart)

    result = runner.invoke(app, ["service", "restart", "--service", "edge", "--manager", "systemd"])

    assert result.exit_code == 0
    assert calls[0]["manager"] == "systemd"
    assert "manager: systemd" in result.stdout
    assert "unit_path: /etc/systemd/system/com.thunder-forge.edge-45116.service" in result.stdout
    assert "staging_unit_path:" in result.stdout
    assert "unit:" in result.stdout
    assert "systemctl restart" in result.stdout


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
              infer-03:
                host: infer-03.lan
                fabric_host: true
                ram_gb: 128
                user: shag
                admin_user: admin
                roles: [inference]
                home_dir: /Users/shag
                runtime:
                  type: omlx
                  port: 8018
        """)
    )

    import thunder_forge.cluster.config as config_module

    monkeypatch.setattr(config_module, "find_repo_root", lambda: repo)

    result = runner.invoke(app, ["service", "restart", "--service", "omlx", "--node", "infer-03"])

    assert result.exit_code == 0
    assert "node: infer-03" in result.stdout
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
              infer-03:
                host: infer-03.lan
                fabric_host: true
                ram_gb: 128
                user: shag
                admin_user: admin
                roles: [inference]
                home_dir: /Users/shag
                runtime:
                  type: omlx
                  port: 8018
        """)
    )

    import thunder_forge.cluster.config as config_module

    monkeypatch.setattr(config_module, "find_repo_root", lambda: repo)

    result = runner.invoke(app, ["runtime", "setup-daemon", "--node", "infer-03", "--via-su"])

    assert result.exit_code == 0
    assert "manager: daemon" in result.stdout
    assert "admin_user: admin" in result.stdout
    assert "ssh_user: shag" in result.stdout
    assert "via_su: yes" in result.stdout
    assert "sudoers_path: /etc/sudoers.d/thunder-forge" in result.stdout
    assert "script:" in result.stdout
    assert "#!/bin/zsh" in result.stdout
    assert "run_root /usr/sbin/visudo -cf" in result.stdout
    assert "copy setup script to shag@infer-03.lan" in result.stdout


def test_runtime_setup_daemon_apply_hides_admin_script(tmp_path: Path, monkeypatch) -> None:
    repo = tmp_path
    config_dir = repo / "configs"
    config_dir.mkdir()
    (repo / "tfconfig.yaml").write_text(
        dedent("""\
            models: {}
            nodes:
              infer-03:
                host: infer-03.lan
                fabric_host: true
                ram_gb: 128
                user: shag
                roles: [inference]
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
        ["runtime", "setup-daemon", "--node", "infer-03", "--admin-user", "admin", "--apply"],
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
              infer-03:
                host: infer-03.lan
                ram_gb: 128
                user: shag
                roles: [inference]
                runtime:
                  type: omlx
        """)
    )

    import thunder_forge.cluster.config as config_module

    monkeypatch.setattr(config_module, "find_repo_root", lambda: repo)

    result = runner.invoke(app, ["runtime", "setup-daemon", "--node", "infer-03", "--via-su"])

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
              infer-03:
                host: infer-03.lan
                fabric_host: true
                ram_gb: 128
                user: shag
                roles: [inference]
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

    result = runner.invoke(app, ["runtime", "restart", "--node", "infer-03", "--apply"])

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
              infer-03:
                host: infer-03.lan
                fabric_host: true
                ram_gb: 128
                user: shag
                roles: [inference]
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

    result = runner.invoke(app, ["runtime", "status", "--node", "infer-03"])

    assert result.exit_code == 0
    assert "node: infer-03" in result.stdout
    assert "runtime: omlx" in result.stdout
    assert "management_host: infer-03.lan" in result.stdout
    assert "fabric_host: true" in result.stdout
    assert "base_url: http://infer-03.lan:8018" in result.stdout
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
              infer-03:
                host: infer-03.lan
                fabric_host: true
                ram_gb: 128
                user: shag
                roles: [inference]
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
            "infer-03",
            "--model",
            "Qwen3-1.7B-4bit",
        ],
    )

    assert result.exit_code == 0
    assert "node: infer-03" in result.stdout
    assert "base_url: http://infer-03.lan:8018" in result.stdout
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
                            qwen3-1.7b-omlx-infer-03-test:
                                source: { repo: mlx-community/Qwen3-1.7B-4bit }
                                runtime_model_id: Qwen3-1.7B-4bit
                        nodes:
                            gateway-cache-01:
                                host: gateway-cache-01.lan
                                ram_gb: 64
                                user: shag
                                roles: [gateway]
                            infer-03:
                                host: infer-03.lan
                                ram_gb: 128
                                user: shag
                                roles: [inference]
                                runtime:
                                    type: omlx
                                    port: 8018
                                models:
                                    - qwen3-1.7b-omlx-infer-03-test
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
    assert parsed["model_aliases"] == {"qwen3-1.7b-omlx-infer-03-test": ["Qwen3-1.7B-4bit"]}
    assert f"generated: {output_path}" in result.stdout


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
                            infer-03:
                                host: infer-03.lan
                                ram_gb: 128
                                user: shag
                                roles: [inference]
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
            olla_endpoint="infer-03-omlx-live",
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
            "qwen3-1.7b-omlx-infer-03-test",
        ],
    )

    assert result.exit_code == 0
    assert "base_url: http://127.0.0.1:40115" in result.stdout
    assert "model: Qwen3-1.7B-4bit" in result.stdout
    assert "alias: qwen3-1.7b-omlx-infer-03-test" in result.stdout
    assert "health: ok" in result.stdout
    assert "endpoints: ok" in result.stdout
    assert "models: ok" in result.stdout
    assert "chat: ok" in result.stdout
    assert "alias_routing: ok" in result.stdout
    assert "session: ok" in result.stdout
    assert "root_v1: absent" in result.stdout
    assert "olla_endpoint: infer-03-omlx-live" in result.stdout


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
            "qwen3-1.7b-omlx-infer-01-test",
            "--expected-endpoint",
            "infer-01-omlx-live",
        ],
    )

    assert result.exit_code == 0
    assert captured["expected_endpoint"] == "infer-01-omlx-live"


def test_olla_dev_smoke_cli_prints_summary(monkeypatch) -> None:
    import thunder_forge.cli as cli_module

    fake_smoke = OllaSmokeResult(
        base_url="http://127.0.0.1:40115",
        model="Qwen3-1.7B-4bit",
        alias="qwen3-1.7b-omlx-infer-03-test",
        health_ok=True,
        endpoints_ok=True,
        models_ok=True,
        chat_ok=True,
        alias_ok=True,
        session_ok=True,
        root_v1_absent=True,
        latency_ms=245,
        olla_endpoint="infer-03-omlx-live",
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
            "qwen3-1.7b-omlx-infer-03-test",
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
            alias="qwen3-1.7b-omlx-infer-01-test",
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
            "qwen3-1.7b-omlx-infer-01-test",
            "--expected-endpoint",
            "infer-01-omlx-live",
        ],
    )

    assert result.exit_code == 0
    assert captured["expected_endpoint"] == "infer-01-omlx-live"


def test_runtime_install_dry_run_prints_plist_and_commands(tmp_path: Path, monkeypatch) -> None:
    repo = tmp_path
    config_dir = repo / "configs"
    config_dir.mkdir()
    (repo / "tfconfig.yaml").write_text(
        dedent(
            """\
            models: {}
            nodes:
              infer-03:
                host: infer-03.lan
                fabric_host: true
                ram_gb: 128
                user: shag
                roles: [inference]
                home_dir: /Users/shag
                runtime:
                  type: omlx
                  port: 8018
        """
        )
    )

    import thunder_forge.cluster.config as config_module

    monkeypatch.setattr(config_module, "find_repo_root", lambda: repo)

    result = runner.invoke(app, ["runtime", "install", "--node", "infer-03", "--dry-run"])

    assert result.exit_code == 0
    assert "node: infer-03" in result.stdout
    assert "plist_path: ~/Library/LaunchAgents/com.thunder-forge.omlx-8018.plist" in result.stdout
    assert "label: com.thunder-forge.omlx-8018" in result.stdout
    assert "mode: dry-run" in result.stdout
    assert "com.thunder-forge.omlx-8018" in result.stdout
    assert "/Users/shag/.local/bin/omlx" in result.stdout
    assert "bootout" in result.stdout
    assert "bootstrap" in result.stdout
