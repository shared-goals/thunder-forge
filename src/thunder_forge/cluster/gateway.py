"""Gateway daemon setup helpers for frontend Thunder Forge services."""

from __future__ import annotations

import platform
import shlex
import socket
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path

from thunder_forge.cluster.edge import (
    _build_edge_launchd_result,
    _build_edge_systemd_result,
    _wait_edge_healthy,
)
from thunder_forge.cluster.olla import (
    _build_olla_launchd_result,
    _build_olla_systemd_result,
    _wait_olla_healthy,
)
from thunder_forge.cluster.ports import DEFAULT_EDGE_PORT, DEFAULT_OLLA_PORT, local_base_url, resolve_port
from thunder_forge.cluster.services import (
    LaunchdServiceResult,
    daemon_sudoers_path,
    generate_daemon_sudoers_file,
    launchd_daemon_sudoers_command_set,
    run_local_commands,
    system_launchd_bootstrap_script,
    system_launchd_stop_wait_script,
    systemd_daemon_sudoers_command_set,
    write_local_file,
)


@dataclass
class GatewayDaemonSetupResult:
    user: str
    admin_user: str
    sudoers_path: str
    script_path: str
    services: list[LaunchdServiceResult] = field(default_factory=list)
    script_content: str = ""
    commands: list[str] = field(default_factory=list)
    applied: bool = False
    sudoers_verified: bool = False
    service_labels_verified: bool = False
    health_ok: bool = False
    errors: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return (
            self.applied
            and self.sudoers_verified
            and self.service_labels_verified
            and self.health_ok
            and not self.errors
        )


def _linux_shell_program() -> str:
    for candidate in ("/bin/zsh", "/usr/bin/zsh", "/bin/bash"):
        if Path(candidate).exists():
            return candidate
    return "/bin/sh"


def generate_gateway_daemon_sudoers(
    *,
    user: str,
    olla_result: LaunchdServiceResult,
    edge_result: LaunchdServiceResult,
    olla_port: int,
    edge_port: int,
    manager: str = "launchd",
) -> str:
    normalized_manager = manager.lower()
    if normalized_manager == "systemd":
        return generate_daemon_sudoers_file(
            user=user,
            command_sets=[
                systemd_daemon_sudoers_command_set(
                    alias_prefix=f"TF_OLLA_{olla_port}",
                    staging_unit_path=olla_result.staging_plist_path,
                    unit_path=olla_result.plist_path,
                    service_name=olla_result.label,
                ),
                systemd_daemon_sudoers_command_set(
                    alias_prefix=f"TF_EDGE_{edge_port}",
                    staging_unit_path=edge_result.staging_plist_path,
                    unit_path=edge_result.plist_path,
                    service_name=edge_result.label,
                ),
            ],
        )
    if normalized_manager != "launchd":
        msg = f"Unsupported gateway service manager: {manager}"
        raise ValueError(msg)
    return generate_daemon_sudoers_file(
        user=user,
        command_sets=[
            launchd_daemon_sudoers_command_set(
                alias_prefix=f"TF_OLLA_{olla_port}",
                staging_plist_path=olla_result.staging_plist_path,
                plist_path=olla_result.plist_path,
                label=olla_result.label,
            ),
            launchd_daemon_sudoers_command_set(
                alias_prefix=f"TF_EDGE_{edge_port}",
                staging_plist_path=edge_result.staging_plist_path,
                plist_path=edge_result.plist_path,
                label=edge_result.label,
            ),
        ],
    )


def _gateway_setup_run_command(
    script_path: str,
    *,
    admin_user: str,
    interactive_sudo: bool,
    shell_program: str,
    use_su_pty: bool = False,
) -> list[str]:
    host = socket.gethostname()
    host_tag = f"[{host}] " if host else ""
    quoted_script = shlex.quote(script_path)
    quoted_shell = shlex.quote(shell_program)
    if admin_user:
        sudo_prompt = f"[%h] password: user={admin_user} reason=install Thunder Forge gateway daemons: "
        sudo_command = (
            f"/usr/bin/sudo -p {shlex.quote(sudo_prompt)} {quoted_shell} {quoted_script}"
            if interactive_sudo
            else f"/usr/bin/sudo -n {quoted_shell} {quoted_script}"
        )
        notice = f"{host_tag}password prompt: method=su user={admin_user} reason=install Thunder Forge gateway daemons"
        su_command = (
            f"/usr/bin/su {'-P ' if use_su_pty else ''}- {shlex.quote(admin_user)} "
            f"-c {shlex.quote(sudo_command)}"
        )
        return [
            f"printf '%s\\n' {shlex.quote(notice)}",
            su_command,
        ]

    sudo_prompt = "[%h] password: user=%p reason=install Thunder Forge gateway daemons: "
    sudo_command = (
        f"/usr/bin/sudo -p {shlex.quote(sudo_prompt)} {quoted_shell} {quoted_script}"
        if interactive_sudo
        else f"/usr/bin/sudo -n {quoted_shell} {quoted_script}"
    )
    if not interactive_sudo:
        return [sudo_command]
    notice = f"{host_tag}password prompt: method=sudo user=%p reason=install Thunder Forge gateway daemons"
    return [f"printf '%s\\n' {shlex.quote(notice)}", sudo_command]


def _generate_gateway_launchd_setup_script(
    *,
    repo_root: Path,
    user: str,
    sudoers_path: str,
    sudoers_content: str,
    services: list[LaunchdServiceResult],
) -> str:
    run_dirs = sorted(
        {str(Path(service.staging_plist_path).parent) for service in services if service.staging_plist_path}
    )
    log_dirs = [str(repo_root / "logs")]
    setup_dirs = " ".join(shlex.quote(path) for path in [*run_dirs, *log_dirs])

    plist_blocks: list[str] = []
    service_blocks: list[str] = []
    cleanup_paths = ['"$TMP_SUDOERS"']
    for index, service in enumerate(services):
        tmp_var = f"TMP_PLIST_{index}"
        cleanup_paths.append(f'"${tmp_var}"')
        process_pattern = service.process_pattern
        plist_blocks.append(
            f"""{tmp_var}=\"$(/usr/bin/mktemp \"/tmp/{service.label}.plist.XXXXXX\")\"
/bin/cat > \"${tmp_var}\" <<'THUNDER_FORGE_PLIST_{index}'
{service.plist_content.rstrip()}
THUNDER_FORGE_PLIST_{index}
""".rstrip()
        )
        stop_wait_script = system_launchd_stop_wait_script(uid_var="OPERATOR_UID")
        bootstrap_script = system_launchd_bootstrap_script()
        service_blocks.append(
            f"""LABEL={shlex.quote(service.label)}
    STAGING_PLIST_PATH={shlex.quote(service.staging_plist_path)}
    PLIST_PATH={shlex.quote(service.plist_path)}
    PROCESS_PATTERN={shlex.quote(process_pattern)}

    echo \"launchd: restarting $LABEL\"
    echo \"launchd: stopping $LABEL\"
    {stop_wait_script}
    echo \"launchd: installing $LABEL\"
    run_root /usr/bin/install -o \"$OPERATOR_USER\" -g staff -m 644 \"${tmp_var}\" \"$STAGING_PLIST_PATH\"
    run_root /usr/bin/install -o root -g wheel -m 644 \"${tmp_var}\" \"$PLIST_PATH\"
    echo \"launchd: starting $LABEL\"
    {bootstrap_script}
    echo \"service: $LABEL ready\"
""".rstrip()
        )

    cleanup_args = " ".join(cleanup_paths)
    plist_setup = "\n\n".join(plist_blocks)
    service_setup = "\n\n".join(service_blocks)

    return f"""#!/bin/zsh
set -euo pipefail

OPERATOR_USER={shlex.quote(user)}
SUDOERS_PATH={shlex.quote(sudoers_path)}
SUDOERS_DIR=\"$(/usr/bin/dirname \"$SUDOERS_PATH\")\"
TMP_SUDOERS=\"$(/usr/bin/mktemp \"/tmp/thunder-forge-sudoers.XXXXXX\")\"

cleanup() {{
    /bin/rm -f {cleanup_args}
}}
trap cleanup EXIT

{plist_setup}

/bin/cat > \"$TMP_SUDOERS\" <<'THUNDER_FORGE_SUDOERS'
{sudoers_content.rstrip()}
THUNDER_FORGE_SUDOERS

if ! /usr/bin/id -u \"$OPERATOR_USER\" >/dev/null 2>&1; then
    echo \"Gateway operator user does not exist: $OPERATOR_USER\" >&2
    exit 1
fi
OPERATOR_UID=\"$(/usr/bin/id -u \"$OPERATOR_USER\")\"

run_root() {{
    if [[ \"$(/usr/bin/id -u)\" -eq 0 ]]; then
        \"$@\"
    else
        /usr/bin/sudo \"$@\"
    fi
}}

if [[ \"$(/usr/bin/uname -s)\" != \"Darwin\" ]]; then
    echo \"Thunder Forge gateway daemon setup currently supports macOS only\" >&2
    exit 1
fi

run_root /bin/mkdir -p {setup_dirs} \"$SUDOERS_DIR\"
run_root /usr/bin/chown -R \"$OPERATOR_USER\":staff {setup_dirs}
run_root /usr/sbin/visudo -cf \"$TMP_SUDOERS\"
run_root /usr/bin/install -o root -g wheel -m 440 \"$TMP_SUDOERS\" \"$SUDOERS_PATH\"

{service_setup}

echo \"sudoers: $SUDOERS_PATH\"
"""


def _generate_gateway_systemd_setup_script(
    *,
    repo_root: Path,
    user: str,
    sudoers_path: str,
    sudoers_content: str,
    services: list[LaunchdServiceResult],
) -> str:
    run_dirs = sorted(
        {str(Path(service.staging_plist_path).parent) for service in services if service.staging_plist_path}
    )
    log_dirs = [str(repo_root / "logs")]
    setup_dirs = " ".join(shlex.quote(path) for path in [*run_dirs, *log_dirs])

    unit_blocks: list[str] = []
    service_blocks: list[str] = []
    cleanup_paths = ['"$TMP_SUDOERS"']
    for index, service in enumerate(services):
        tmp_var = f"TMP_UNIT_{index}"
        cleanup_paths.append(f'"${tmp_var}"')
        unit_blocks.append(
            f"""{tmp_var}=\"$(/usr/bin/mktemp \"/tmp/{service.label}.unit.XXXXXX\")\"
/bin/cat > \"${tmp_var}\" <<'THUNDER_FORGE_UNIT_{index}'
{service.plist_content.rstrip()}
THUNDER_FORGE_UNIT_{index}
""".rstrip()
        )
        service_blocks.append(
            f"""SERVICE_NAME={shlex.quote(service.label)}
    STAGING_UNIT_PATH={shlex.quote(service.staging_plist_path)}
    UNIT_PATH={shlex.quote(service.plist_path)}

    echo \"systemd: stopping $SERVICE_NAME\"
    run_root /bin/systemctl stop \"$SERVICE_NAME\" 2>/dev/null || true
    echo \"systemd: installing $SERVICE_NAME\"
    run_root /usr/bin/install -o \"$OPERATOR_USER\" -g \"$OPERATOR_GROUP\" -m 644 \"${tmp_var}\" \"$STAGING_UNIT_PATH\"
    run_root /usr/bin/install -o root -g root -m 644 \"${tmp_var}\" \"$UNIT_PATH\"
    echo \"systemd: starting $SERVICE_NAME\"
    run_root /bin/systemctl daemon-reload
    run_root /bin/systemctl enable --now \"$SERVICE_NAME\"
    run_root /bin/systemctl restart \"$SERVICE_NAME\"
    run_root /bin/systemctl is-active --quiet \"$SERVICE_NAME\"
    echo \"service: $SERVICE_NAME ready\"
""".rstrip()
        )

    cleanup_args = " ".join(cleanup_paths)
    unit_setup = "\n\n".join(unit_blocks)
    service_setup = "\n\n".join(service_blocks)

    return f"""#!/bin/bash
set -euo pipefail

OPERATOR_USER={shlex.quote(user)}
SUDOERS_PATH={shlex.quote(sudoers_path)}
SUDOERS_DIR=\"$(/usr/bin/dirname \"$SUDOERS_PATH\")\"
TMP_SUDOERS=\"$(/usr/bin/mktemp \"/tmp/thunder-forge-sudoers.XXXXXX\")\"

cleanup() {{
    /bin/rm -f {cleanup_args}
}}
trap cleanup EXIT

{unit_setup}

/bin/cat > \"$TMP_SUDOERS\" <<'THUNDER_FORGE_SUDOERS'
{sudoers_content.rstrip()}
THUNDER_FORGE_SUDOERS

if ! /usr/bin/id -u \"$OPERATOR_USER\" >/dev/null 2>&1; then
    echo \"Gateway operator user does not exist: $OPERATOR_USER\" >&2
    exit 1
fi
OPERATOR_GROUP=\"$(/usr/bin/id -gn \"$OPERATOR_USER\")\"

run_root() {{
    if [[ \"$(/usr/bin/id -u)\" -eq 0 ]]; then
        \"$@\"
    else
        /usr/bin/sudo \"$@\"
    fi
}}

if [[ \"$(/usr/bin/uname -s)\" != \"Linux\" ]]; then
    echo \"Thunder Forge gateway daemon setup currently supports Linux only\" >&2
    exit 1
fi

run_root /bin/mkdir -p {setup_dirs} \"$SUDOERS_DIR\"
run_root /usr/bin/chown -R \"$OPERATOR_USER\":\"$OPERATOR_GROUP\" {setup_dirs}
run_root /usr/sbin/visudo -cf \"$TMP_SUDOERS\"
run_root /usr/bin/install -o root -g root -m 440 \"$TMP_SUDOERS\" \"$SUDOERS_PATH\"

{service_setup}

echo \"sudoers: $SUDOERS_PATH\"
"""


def generate_gateway_daemon_setup_script(
    *,
    repo_root: Path,
    user: str,
    sudoers_path: str,
    sudoers_content: str,
    services: list[LaunchdServiceResult],
    manager: str,
) -> str:
    normalized_manager = manager.lower()
    if normalized_manager == "systemd":
        return _generate_gateway_systemd_setup_script(
            repo_root=repo_root,
            user=user,
            sudoers_path=sudoers_path,
            sudoers_content=sudoers_content,
            services=services,
        )
    if normalized_manager == "launchd":
        return _generate_gateway_launchd_setup_script(
            repo_root=repo_root,
            user=user,
            sudoers_path=sudoers_path,
            sudoers_content=sudoers_content,
            services=services,
        )
    msg = f"Unsupported gateway service manager: {manager}"
    raise ValueError(msg)


def build_gateway_daemon_setup_result(
    *,
    repo_root: Path,
    binary: Path,
    config_path: Path,
    edge_host: str,
    olla_port: int | None,
    edge_port: int | None,
    olla_base_url: str | None,
    users_env: str,
    access_log_path: Path,
    user: str,
    admin_user: str,
    interactive_sudo: bool,
    script_path: str | None,
) -> tuple[GatewayDaemonSetupResult, str, str]:
    resolved_olla_port = resolve_port(olla_port, default=DEFAULT_OLLA_PORT)
    resolved_edge_port = resolve_port(edge_port, default=DEFAULT_EDGE_PORT)
    resolved_olla_base_url = olla_base_url or local_base_url(resolved_olla_port)
    result = GatewayDaemonSetupResult(
        user=user,
        admin_user=admin_user,
        sudoers_path=daemon_sudoers_path(),
        script_path=script_path or str(repo_root / ".tmp" / "run" / "thunder-forge-gateway-daemon-setup.sh"),
    )

    host_os = platform.system()
    if host_os == "Linux":
        manager = "systemd"
        shell_program = _linux_shell_program()
    elif host_os == "Darwin":
        manager = "launchd"
        shell_program = "/bin/zsh"
    else:
        result.errors.append(f"Unsupported gateway daemon setup host OS: {host_os}")
        return result, "", ""

    if manager == "systemd":
        olla_result, olla_health_url = _build_olla_systemd_result(
            repo_root=repo_root,
            binary=binary,
            config_path=config_path,
            port=resolved_olla_port,
            user=user,
        )
        edge_result, edge_health_url = _build_edge_systemd_result(
            repo_root=repo_root,
            host=edge_host,
            port=resolved_edge_port,
            olla_base_url=resolved_olla_base_url,
            users_env=users_env,
            access_log_path=access_log_path,
            user=user,
        )
    else:
        olla_result, olla_health_url = _build_olla_launchd_result(
            repo_root=repo_root,
            binary=binary,
            config_path=config_path,
            port=resolved_olla_port,
            user=user,
            manager="daemon",
        )
        edge_result, edge_health_url = _build_edge_launchd_result(
            repo_root=repo_root,
            host=edge_host,
            port=resolved_edge_port,
            olla_base_url=resolved_olla_base_url,
            users_env=users_env,
            access_log_path=access_log_path,
            user=user,
            manager="daemon",
        )

    result.services = [olla_result, edge_result]
    for service in result.services:
        result.errors.extend(service.errors)
    if result.errors:
        return result, olla_health_url, edge_health_url

    sudoers_content = generate_gateway_daemon_sudoers(
        user=user,
        olla_result=olla_result,
        edge_result=edge_result,
        olla_port=resolved_olla_port,
        edge_port=resolved_edge_port,
        manager=manager,
    )
    result.script_content = generate_gateway_daemon_setup_script(
        repo_root=repo_root,
        user=user,
        sudoers_path=result.sudoers_path,
        sudoers_content=sudoers_content,
        services=result.services,
        manager=manager,
    )

    verify_commands = [
        f"/usr/bin/sudo -n /bin/launchctl print system/{service.label} >/dev/null" for service in result.services
    ]
    if manager == "systemd":
        verify_commands = [
            f"/usr/bin/sudo -n /bin/systemctl is-active --quiet {service.label}" for service in result.services
        ]

    result.commands = [
        f"write setup script to {result.script_path}",
        *_gateway_setup_run_command(
            result.script_path,
            admin_user=admin_user,
            interactive_sudo=interactive_sudo,
            shell_program=shell_program,
            use_su_pty=manager == "systemd" and interactive_sudo and bool(admin_user),
        ),
        *verify_commands,
    ]
    return result, olla_health_url, edge_health_url


def run_gateway_daemon_setup(
    *,
    repo_root: Path,
    binary: Path = Path(".tmp/olla-bin/olla"),
    config_path: Path = Path("configs/olla-config.yaml"),
    edge_host: str = "127.0.0.1",
    olla_port: int | None = None,
    edge_port: int | None = None,
    olla_base_url: str | None = None,
    users_env: str,
    access_log_path: Path,
    user: str,
    admin_user: str = "",
    interactive_sudo: bool = False,
    script_path: str | None = None,
    apply: bool = True,
    timeout: int = 300,
    progress: Callable[[str], None] | None = None,
) -> GatewayDaemonSetupResult:
    result, olla_health_url, edge_health_url = build_gateway_daemon_setup_result(
        repo_root=repo_root,
        binary=binary,
        config_path=config_path,
        edge_host=edge_host,
        olla_port=olla_port,
        edge_port=edge_port,
        olla_base_url=olla_base_url,
        users_env=users_env,
        access_log_path=access_log_path,
        user=user,
        admin_user=admin_user,
        interactive_sudo=interactive_sudo,
        script_path=script_path,
    )
    if not apply or result.errors:
        return result

    write_local_file(result.script_path, result.script_content)
    setup_command_count = 2 if admin_user or interactive_sudo else 1
    setup_commands = result.commands[1 : 1 + setup_command_count]
    ok, error = run_local_commands(setup_commands, timeout=timeout, stream=interactive_sudo)
    result.applied = True
    if not ok:
        result.errors.append(error)
        return result

    verify_commands = result.commands[1 + setup_command_count :]
    ok, error = run_local_commands(verify_commands, timeout=timeout)
    result.sudoers_verified = ok
    result.service_labels_verified = ok
    if not ok:
        result.errors.append(error)

    olla_ok = _wait_olla_healthy(olla_health_url, retries=30, interval=1.0, timeout=5.0, progress=progress)
    if not olla_ok:
        result.errors.append(f"Olla health check failed at {olla_health_url}")
    elif progress:
        progress(f"health: olla ok ({olla_health_url})")

    edge_ok = _wait_edge_healthy(edge_health_url, retries=30, interval=1.0, timeout=5.0, progress=progress)
    if not edge_ok:
        result.errors.append(f"TF edge health check failed at {edge_health_url}")
    elif progress:
        progress(f"health: edge ok ({edge_health_url})")

    result.health_ok = olla_ok and edge_ok
    return result
