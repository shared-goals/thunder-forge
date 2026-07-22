"""Shared launchd service helpers for Thunder Forge daemons."""

from __future__ import annotations

import html
import re
import shlex
import subprocess
from dataclasses import dataclass, field
from pathlib import Path

DEFAULT_DAEMON_SUDOERS_PATH = "/etc/sudoers.d/thunder-forge"
DEFAULT_SYSTEMD_UNIT_DIR = "/etc/systemd/system"


@dataclass
class LaunchdServiceSpec:
    name: str
    label: str
    program_arguments: list[str]
    working_directory: str
    stdout_log: str
    stderr_log: str
    environment: dict[str, str] = field(default_factory=dict)
    process_pattern: str = ""
    user: str = ""


def systemd_service_name(label: str) -> str:
    return f"{label}.service"


def systemd_unit_path(service_name: str) -> str:
    return f"{DEFAULT_SYSTEMD_UNIT_DIR}/{service_name}"


@dataclass
class LaunchdServiceResult:
    service: str
    label: str
    plist_path: str
    staging_plist_path: str = ""
    plist_content: str = ""
    process_pattern: str = ""
    commands: list[str] = field(default_factory=list)
    applied: bool = False
    service_label_verified: bool = False
    health_ok: bool = False
    errors: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return self.applied and self.service_label_verified and self.health_ok and not self.errors


@dataclass(frozen=True)
class DaemonSudoersCommandSet:
    alias_prefix: str
    install_command: str
    manager_alias: str
    manager_commands: list[str]

    @property
    def allowed_aliases(self) -> list[str]:
        return [f"{self.alias_prefix}_INSTALL", f"{self.alias_prefix}_{self.manager_alias}"]


def launchd_safe_alias(value: str) -> str:
    return re.sub(r"[^A-Z0-9_]", "_", value.upper())


def daemon_sudoers_path() -> str:
    return DEFAULT_DAEMON_SUDOERS_PATH


def launch_agent_path(label: str) -> str:
    return f"~/Library/LaunchAgents/{label}.plist"


def launch_daemon_path(label: str) -> str:
    return f"/Library/LaunchDaemons/{label}.plist"


def system_launchd_stop_wait_script(
    *,
    label_var: str = "LABEL",
    uid_var: str = "OPERATOR_UID",
    process_pattern_var: str = "PROCESS_PATTERN",
    run_root: str = "run_root",
    wait_seconds: int = 10,
) -> str:
    label_ref = f"${{{label_var}}}"
    uid_ref = f"${{{uid_var}}}"
    pattern_ref = f"${{{process_pattern_var}}}"
    return f"""{run_root} /bin/launchctl bootout "user/{uid_ref}/{label_ref}" 2>/dev/null || true
{run_root} /bin/launchctl bootout "gui/{uid_ref}/{label_ref}" 2>/dev/null || true
{run_root} /bin/launchctl bootout "system/{label_ref}" 2>/dev/null || true
if [[ -n "{pattern_ref}" ]]; then
    {run_root} /usr/bin/pkill -TERM -f "{pattern_ref}" 2>/dev/null || true
    _tf_wait_seconds=0
    while /usr/bin/pgrep -f "{pattern_ref}" >/dev/null 2>&1; do
        if [[ $_tf_wait_seconds -ge {wait_seconds} ]]; then
            echo "process still running after {wait_seconds}s; sending SIGKILL" >&2
            {run_root} /usr/bin/pkill -KILL -f "{pattern_ref}" 2>/dev/null || true
            break
        fi
        sleep 1
        _tf_wait_seconds=$((_tf_wait_seconds + 1))
    done
fi"""


def system_launchd_bootstrap_script(
    *,
    label_var: str = "LABEL",
    plist_path_var: str = "PLIST_PATH",
    run_root: str = "run_root",
) -> str:
    label_ref = f"${{{label_var}}}"
    plist_ref = f"${{{plist_path_var}}}"
    return "\n".join(_system_launchd_bootstrap_commands(label_ref=label_ref, plist_ref=plist_ref, run_root=run_root))


def _system_launchd_bootstrap_commands(*, label_ref: str, plist_ref: str, run_root: str) -> list[str]:
    root = run_root.rstrip()
    return [
        f"{root} /bin/launchctl enable \"system/{label_ref}\" 2>/dev/null || true",
        "set +e",
        f"_tf_bootstrap_output=$({root} /bin/launchctl bootstrap system \"{plist_ref}\" 2>&1)",
        "_tf_bootstrap_exit=$?",
        "set -e",
        "if [[ $_tf_bootstrap_exit -ne 0 ]] && [[ $_tf_bootstrap_exit -ne 5 ]]; then",
        "    printf '%s\\n' \"$_tf_bootstrap_output\" >&2",
        "fi",
        "if [[ $_tf_bootstrap_exit -eq 5 ]]; then",
        "    echo \"launchd bootstrap returned 5; retrying bootout/bootstrap once\" >&2",
        "    set +e",
        f"    {root} /bin/launchctl bootout \"system/{label_ref}\" 2>/dev/null || true",
        f"    {root} /bin/launchctl enable \"system/{label_ref}\" 2>/dev/null || true",
        f"    _tf_bootstrap_retry_output=$({root} /bin/launchctl bootstrap system \"{plist_ref}\" 2>&1)",
        "    _tf_bootstrap_retry_exit=$?",
        "    set -e",
        "    if [[ $_tf_bootstrap_retry_exit -eq 0 ]]; then",
        "        _tf_bootstrap_exit=0",
        "    else",
        "        _tf_bootstrap_exit=$_tf_bootstrap_retry_exit",
        "        if [[ $_tf_bootstrap_retry_exit -ne 5 ]]; then",
        "            printf '%s\\n' \"$_tf_bootstrap_retry_output\" >&2",
        "        fi",
        "    fi",
        "fi",
        "if [[ $_tf_bootstrap_exit -eq 5 ]]; then",
        "    set +e",
        f"    {root} /bin/launchctl kickstart -k \"system/{label_ref}\"",
        "    _tf_kickstart_exit=$?",
        f"    _tf_print_output=$({root} /bin/launchctl print \"system/{label_ref}\" 2>&1)",
        "    _tf_print_exit=$?",
        "    set -e",
        (
            "    if [[ $_tf_print_exit -eq 0 ]] && "
            "printf '%s\\n' \"$_tf_print_output\" | /usr/bin/grep -q \"job state = running\"; then"
        ),
        "        echo \"launchd bootstrap returned 5; continuing because service is running\" >&2",
        "        _tf_bootstrap_exit=0",
        "    fi",
        "fi",
        "if [[ $_tf_bootstrap_exit -ne 0 ]]; then",
        f"    echo \"launchd bootstrap failed: label={label_ref} exit=$_tf_bootstrap_exit\" >&2",
        f"    echo \"launchd plist: {plist_ref}\" >&2",
        f"    {root} /bin/launchctl print \"system/{label_ref}\" 2>&1 || true",
        "    exit $_tf_bootstrap_exit",
        "fi",
        "set +e",
        f"{root} /bin/launchctl kickstart -k \"system/{label_ref}\"",
        "_tf_final_kickstart_exit=$?",
        f"_tf_final_print_output=$({root} /bin/launchctl print \"system/{label_ref}\" 2>&1)",
        "_tf_final_print_exit=$?",
        "set -e",
        "if [[ $_tf_final_print_exit -ne 0 ]]; then",
        f"    echo \"launchd print failed after bootstrap: label={label_ref} exit=$_tf_final_print_exit\" >&2",
        "    printf '%s\\n' \"$_tf_final_print_output\" >&2",
        "    exit $_tf_final_print_exit",
        "fi",
        (
            "if [[ $_tf_final_kickstart_exit -ne 0 ]] && ! "
            "printf '%s\\n' \"$_tf_final_print_output\" | /usr/bin/grep -q \"job state = running\"; then"
        ),
        f"    echo \"launchd kickstart failed after bootstrap: label={label_ref} exit=$_tf_final_kickstart_exit\" >&2",
        "    printf '%s\\n' \"$_tf_final_print_output\" >&2",
        "    exit $_tf_final_kickstart_exit",
        "fi",
    ]


def system_launchd_stop_wait_command(
    *,
    label: str,
    process_pattern: str = "",
    uid_expr: str = "$(id -u)",
    root_prefix: str = "/usr/bin/sudo -n ",
    wait_seconds: int = 10,
) -> str:
    commands = [
        f"{root_prefix}/bin/launchctl bootout system/{label} 2>/dev/null || true",
    ]
    if process_pattern:
        quoted_pattern = shlex.quote(process_pattern)
        commands.extend(
            [
                f"/usr/bin/pkill -TERM -f {quoted_pattern} 2>/dev/null || true",
                "_tf_wait_seconds=0",
                (
                    f"while /usr/bin/pgrep -f {quoted_pattern} >/dev/null 2>&1; do "
                    f"if [[ $_tf_wait_seconds -ge {wait_seconds} ]]; then "
                    f"echo 'process still running after {wait_seconds}s; sending SIGKILL' >&2; "
                    f"/usr/bin/pkill -KILL -f {quoted_pattern} 2>/dev/null || true; "
                    "break; fi; "
                    "sleep 1; "
                    "_tf_wait_seconds=$((_tf_wait_seconds + 1)); "
                    "done"
                ),
            ]
        )
    return " ; ".join(
        [
            f"launchctl bootout user/{uid_expr}/{label} 2>/dev/null || true",
            f"launchctl bootout gui/{uid_expr}/{label} 2>/dev/null || true",
            *commands,
        ]
    )


def system_launchd_bootstrap_command(
    *,
    label: str,
    plist_path: str,
    root_prefix: str = "/usr/bin/sudo -n ",
) -> str:
    commands = _system_launchd_bootstrap_commands(
        label_ref=label,
        plist_ref=plist_path,
        run_root=root_prefix,
    )
    return " ; ".join(line.strip() for line in commands if line.strip())


def generate_launchd_plist(spec: LaunchdServiceSpec, *, system_daemon: bool = False) -> str:
    program_arguments_xml = "\n".join(
        f"        <string>{html.escape(argument)}</string>" for argument in spec.program_arguments
    )
    user_xml = ""
    if system_daemon:
        if not spec.user:
            msg = "system daemon service spec requires user"
            raise ValueError(msg)
        user_xml = f"    <key>UserName</key>\n    <string>{html.escape(spec.user)}</string>\n"

    environment_xml = ""
    if spec.environment:
        items = "\n".join(
            f"        <key>{html.escape(key)}</key>\n        <string>{html.escape(value)}</string>"
            for key, value in spec.environment.items()
        )
        environment_xml = f"    <key>EnvironmentVariables</key>\n    <dict>\n{items}\n    </dict>\n"

    return f"""<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>Label</key>
    <string>{html.escape(spec.label)}</string>
{user_xml}{environment_xml}    <key>ProgramArguments</key>
    <array>
{program_arguments_xml}
    </array>
    <key>RunAtLoad</key>
    <true/>
    <key>KeepAlive</key>
    <true/>
    <key>StandardOutPath</key>
    <string>{html.escape(spec.stdout_log)}</string>
    <key>StandardErrorPath</key>
    <string>{html.escape(spec.stderr_log)}</string>
    <key>WorkingDirectory</key>
    <string>{html.escape(spec.working_directory)}</string>
</dict>
</plist>
"""


def generate_systemd_unit(spec: LaunchdServiceSpec) -> str:
    env_lines = "\n".join(
        f"Environment={name}={shlex.quote(value)}" for name, value in sorted(spec.environment.items())
    )
    exec_start = " ".join(shlex.quote(argument) for argument in spec.program_arguments)
    user_line = f"User={spec.user}\n" if spec.user else ""
    stdout_line = (
        "StandardOutput=null"
        if spec.stdout_log == "/dev/null"
        else f"StandardOutput=append:{spec.stdout_log}"
    )
    stderr_line = f"StandardError=append:{spec.stderr_log}"

    return (
        "[Unit]\n"
        f"Description=Thunder Forge {spec.name} ({spec.label})\n"
        "After=network-online.target\n"
        "Wants=network-online.target\n\n"
        "[Service]\n"
        "Type=simple\n"
        f"{user_line}"
        f"WorkingDirectory={spec.working_directory}\n"
        f"{env_lines}\n"
        f"ExecStart={exec_start}\n"
        "Restart=always\n"
        "RestartSec=2\n"
        f"{stdout_line}\n"
        f"{stderr_line}\n\n"
        "[Install]\n"
        "WantedBy=multi-user.target\n"
    )


def user_launchd_commands(
    *,
    label: str,
    plist_path: str,
    process_pattern: str = "",
    setup_command: str = "mkdir -p ~/Library/LaunchAgents ~/Library/Logs",
    domain: str = "user/$(id -u)",
    kickstart: bool = False,
) -> list[str]:
    commands = [
        setup_command,
        f"launchctl bootout {domain}/{label} 2>/dev/null || true",
        f"rm -f {plist_path}",
    ]
    if process_pattern:
        commands.append(f"pkill -f {shlex.quote(process_pattern)} 2>/dev/null || true")
    commands.append(f"launchctl bootstrap {domain} {plist_path}")
    if kickstart:
        commands.append(f"launchctl kickstart -k {domain}/{label}")
    commands.append(f"launchctl list {label} 2>/dev/null | grep -q {label}")
    return commands


def system_launchd_commands(
    *,
    label: str,
    staging_plist_path: str,
    plist_path: str,
    process_pattern: str = "",
    setup_command: str,
    interactive_sudo: bool = False,
    admin_user: str = "",
) -> list[str]:
    admin_user = admin_user.strip()

    if admin_user:
        sudo_prompt = f"[%h] password: user={admin_user} reason=manage Thunder Forge daemon {label}: "
        sudo_validate = f"/usr/bin/sudo -p {shlex.quote(sudo_prompt)} -v" if interactive_sudo else "/usr/bin/sudo -n -v"
        admin_notice = (
            f"password prompt: host=%h method=su user={admin_user} reason=manage Thunder Forge daemon {label}"
        )
        sudo_notice = f"password prompt: host=%h method=sudo user={admin_user} reason=install/restart {label}"
        stop_wait = system_launchd_stop_wait_command(
            label=label,
            process_pattern=process_pattern,
            root_prefix="/usr/bin/sudo -n ",
        )
        bootstrap = system_launchd_bootstrap_command(
            label=label,
            plist_path=plist_path,
            root_prefix="/usr/bin/sudo -n ",
        )
        admin_script_parts = [
            "set -e",
            f"printf '%s\\n' {shlex.quote(sudo_notice)}",
            sudo_validate,
            stop_wait,
            f"/usr/bin/sudo -n /usr/bin/install -o root -g wheel -m 644 {staging_plist_path} {plist_path}",
            bootstrap,
        ]
        commands = [
            setup_command,
            f"printf '%s\\n' {shlex.quote(admin_notice)}",
        ]
        admin_script = "; ".join(admin_script_parts)
        commands.append(f"/usr/bin/su - {shlex.quote(admin_user)} -c {shlex.quote(admin_script)}")
        commands.append("true")
        return commands

    sudo_prompt = f"[%h] password: user=%p reason=manage Thunder Forge daemon {label}: "

    def root_command(command: str) -> str:
        if interactive_sudo:
            sudo_command = f"/usr/bin/sudo -p {shlex.quote(sudo_prompt)} {command}"
        else:
            sudo_command = f"/usr/bin/sudo -n {command}"
        return sudo_command

    root_prefix = f"/usr/bin/sudo -p {shlex.quote(sudo_prompt)} " if interactive_sudo else "/usr/bin/sudo -n "

    commands = [setup_command]
    if interactive_sudo:
        notice = f"password prompt: host=%h method=sudo user=%p reason=manage Thunder Forge daemon {label}"
        commands.append(f"printf '%s\\n' {shlex.quote(notice)}")
    commands.append(
        system_launchd_stop_wait_command(
            label=label,
            process_pattern=process_pattern,
            root_prefix=root_prefix,
        )
    )
    commands.extend(
        [
            root_command(f"/usr/bin/install -o root -g wheel -m 644 {staging_plist_path} {plist_path}"),
            system_launchd_bootstrap_command(label=label, plist_path=plist_path, root_prefix=root_prefix),
            root_command(f"/bin/launchctl print system/{label} >/dev/null"),
        ]
    )
    return commands


def systemd_commands(
    *,
    service_name: str,
    staging_unit_path: str,
    unit_path: str,
    setup_command: str,
    interactive_sudo: bool = False,
    admin_user: str = "",
) -> list[str]:
    admin_user = admin_user.strip()

    if admin_user:
        sudo_prompt = f"[%h] password: user={admin_user} reason=manage Thunder Forge service {service_name}: "
        sudo_validate = f"/usr/bin/sudo -p {shlex.quote(sudo_prompt)} -v" if interactive_sudo else "/usr/bin/sudo -n -v"
        admin_notice = (
            f"password prompt: host=%h method=su user={admin_user} "
            f"reason=manage Thunder Forge service {service_name}"
        )
        sudo_notice = (
            f"password prompt: host=%h method=sudo user={admin_user} "
            f"reason=install/restart {service_name}"
        )
        admin_script_parts = [
            "set -e",
            f"printf '%s\\n' {shlex.quote(sudo_notice)}",
            sudo_validate,
            f"/usr/bin/sudo -n /bin/systemctl stop {service_name} 2>/dev/null || true",
            f"/usr/bin/sudo -n /usr/bin/install -o root -g root -m 644 {staging_unit_path} {unit_path}",
            "/usr/bin/sudo -n /bin/systemctl daemon-reload",
            f"/usr/bin/sudo -n /bin/systemctl enable --now {service_name}",
            f"/usr/bin/sudo -n /bin/systemctl restart {service_name}",
        ]
        admin_script = "; ".join(admin_script_parts)
        return [
            setup_command,
            f"printf '%s\\n' {shlex.quote(admin_notice)}",
            f"/usr/bin/su - {shlex.quote(admin_user)} -c {shlex.quote(admin_script)}",
            "true",
        ]

    sudo_prompt = f"[%h] password: user=%p reason=manage Thunder Forge service {service_name}: "

    def root_command(command: str) -> str:
        if interactive_sudo:
            return f"/usr/bin/sudo -p {shlex.quote(sudo_prompt)} {command}"
        return f"/usr/bin/sudo -n {command}"

    commands = [setup_command]
    if interactive_sudo:
        notice = f"password prompt: host=%h method=sudo user=%p reason=manage Thunder Forge service {service_name}"
        commands.append(f"printf '%s\\n' {shlex.quote(notice)}")
    commands.extend(
        [
            root_command(f"/bin/systemctl stop {service_name} 2>/dev/null || true"),
            root_command(f"/usr/bin/install -o root -g root -m 644 {staging_unit_path} {unit_path}"),
            root_command("/bin/systemctl daemon-reload"),
            root_command(f"/bin/systemctl enable --now {service_name}"),
            root_command(f"/bin/systemctl restart {service_name}"),
            root_command(f"/bin/systemctl is-active --quiet {service_name}"),
        ]
    )
    return commands


def generate_daemon_sudoers(
    *,
    user: str,
    alias_prefix: str,
    staging_plist_path: str,
    plist_path: str,
    label: str,
) -> str:
    command_set = launchd_daemon_sudoers_command_set(
        alias_prefix=alias_prefix,
        staging_plist_path=staging_plist_path,
        plist_path=plist_path,
        label=label,
    )
    return generate_daemon_sudoers_file(user=user, command_sets=[command_set])


def launchd_daemon_sudoers_command_set(
    *,
    alias_prefix: str,
    staging_plist_path: str,
    plist_path: str,
    label: str,
) -> DaemonSudoersCommandSet:
    return DaemonSudoersCommandSet(
        alias_prefix=alias_prefix,
        install_command=f"/usr/bin/install -o root -g wheel -m 644 {staging_plist_path} {plist_path}",
        manager_alias="LAUNCHD",
        manager_commands=[
            f"/bin/launchctl bootout system/{label}",
            f"/bin/launchctl enable system/{label}",
            f"/bin/launchctl bootstrap system {plist_path}",
            f"/bin/launchctl kickstart -k system/{label}",
            f"/bin/launchctl print system/{label}",
        ],
    )


def systemd_daemon_sudoers_command_set(
    *,
    alias_prefix: str,
    staging_unit_path: str,
    unit_path: str,
    service_name: str,
) -> DaemonSudoersCommandSet:
    return DaemonSudoersCommandSet(
        alias_prefix=alias_prefix,
        install_command=f"/usr/bin/install -o root -g root -m 644 {staging_unit_path} {unit_path}",
        manager_alias="SYSTEMD",
        manager_commands=[
            f"/bin/systemctl stop {service_name}",
            "/bin/systemctl daemon-reload",
            f"/bin/systemctl enable --now {service_name}",
            f"/bin/systemctl restart {service_name}",
            f"/bin/systemctl is-active --quiet {service_name}",
        ],
    )


def generate_daemon_sudoers_file(*, user: str, command_sets: list[DaemonSudoersCommandSet]) -> str:
    alias_lines: list[str] = []
    allowed_aliases: list[str] = []
    for command_set in command_sets:
        manager_command_list = ", ".join(command_set.manager_commands)
        alias_lines.extend(
            [
                f"Cmnd_Alias {command_set.alias_prefix}_INSTALL = {command_set.install_command}",
                f"Cmnd_Alias {command_set.alias_prefix}_{command_set.manager_alias} = {manager_command_list}",
            ]
        )
        allowed_aliases.extend(command_set.allowed_aliases)
    alias_lines.append(f"{user} ALL=(root) NOPASSWD: {', '.join(allowed_aliases)}")
    return "\n".join(alias_lines) + "\n"


def run_local_commands(commands: list[str], *, timeout: int, stream: bool = False) -> tuple[bool, str]:
    for command in commands:
        if stream:
            completed = subprocess.run(command, shell=True, text=True, timeout=timeout, check=False)
        else:
            completed = subprocess.run(
                command,
                shell=True,
                text=True,
                capture_output=True,
                timeout=timeout,
                check=False,
            )
        if completed.returncode != 0:
            output = "" if stream else (completed.stderr or completed.stdout).strip()
            suffix = f": {output}" if output else ""
            return False, f"Command failed with exit code {completed.returncode}: {command}{suffix}"
    return True, ""


def write_local_file(path: str, content: str) -> Path:
    expanded = Path(path).expanduser()
    expanded.parent.mkdir(parents=True, exist_ok=True)
    expanded.write_text(content)
    return expanded
