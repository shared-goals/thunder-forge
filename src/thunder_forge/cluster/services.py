"""Shared launchd service helpers for Thunder Forge daemons."""

from __future__ import annotations

import html
import re
import shlex
import subprocess
from dataclasses import dataclass, field
from pathlib import Path

DEFAULT_DAEMON_SUDOERS_PATH = "/etc/sudoers.d/thunder-forge"


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
    launchd_commands: list[str]

    @property
    def allowed_aliases(self) -> list[str]:
        return [f"{self.alias_prefix}_INSTALL", f"{self.alias_prefix}_LAUNCHD"]


def launchd_safe_alias(value: str) -> str:
    return re.sub(r"[^A-Z0-9_]", "_", value.upper())


def daemon_sudoers_path() -> str:
    return DEFAULT_DAEMON_SUDOERS_PATH


def launch_agent_path(label: str) -> str:
    return f"~/Library/LaunchAgents/{label}.plist"


def launch_daemon_path(label: str) -> str:
    return f"/Library/LaunchDaemons/{label}.plist"


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
        sudo_prompt = f"Password for {admin_user} on %h to manage Thunder Forge frontend daemon {label}: "
        sudo_validate = (
            f"/usr/bin/sudo -p {shlex.quote(sudo_prompt)} -v"
            if interactive_sudo
            else "/usr/bin/sudo -n -v"
        )
        admin_notice = (
            f"Next prompt 'Password:' is su asking for admin user {admin_user}'s local macOS login password "
            f"so Thunder Forge can manage frontend daemon {label}."
        )
        sudo_notice = (
            f"After su succeeds, sudo may ask once for admin user {admin_user}'s password to install/restart {label}."
        )
        admin_script_parts = [
            "set -e",
            f"printf '%s\\n' {shlex.quote(sudo_notice)}",
            sudo_validate,
            f"/usr/bin/sudo -n /usr/bin/install -o root -g wheel -m 644 {staging_plist_path} {plist_path}",
            f"/usr/bin/sudo -n /bin/launchctl bootout system/{label} 2>/dev/null || true",
            f"/usr/bin/sudo -n /bin/launchctl bootstrap system {plist_path}",
            f"/usr/bin/sudo -n /bin/launchctl kickstart -k system/{label}",
            f"/usr/bin/sudo -n /bin/launchctl print system/{label} >/dev/null",
        ]
        commands = [
            setup_command,
            f"printf '%s\\n' {shlex.quote(admin_notice)}",
            f"launchctl bootout user/$(id -u)/{label} 2>/dev/null || true",
            f"launchctl bootout gui/$(id -u)/{label} 2>/dev/null || true",
        ]
        if process_pattern:
            commands.append(f"pkill -f {shlex.quote(process_pattern)} 2>/dev/null || true")
        admin_script = "; ".join(admin_script_parts)
        commands.append(f"/usr/bin/su - {shlex.quote(admin_user)} -c {shlex.quote(admin_script)}")
        commands.append("true")
        return commands

    def root_command(command: str) -> str:
        if interactive_sudo:
            prompt = f"Password for %p on %h to manage Thunder Forge frontend daemon {label}: "
            sudo_command = f"/usr/bin/sudo -p {shlex.quote(prompt)} {command}"
        else:
            sudo_command = f"/usr/bin/sudo -n {command}"
        return sudo_command

    commands = [setup_command]
    if interactive_sudo:
        notice = (
            "sudo needs the local macOS login password for the sudo-capable user "
            f"on this frontend host to manage {label}."
        )
        commands.append(f"printf '%s\\n' {shlex.quote(notice)}")
    commands.extend(
        [
            root_command(f"/usr/bin/install -o root -g wheel -m 644 {staging_plist_path} {plist_path}"),
            f"launchctl bootout user/$(id -u)/{label} 2>/dev/null || true",
            f"launchctl bootout gui/$(id -u)/{label} 2>/dev/null || true",
            root_command(f"/bin/launchctl bootout system/{label} 2>/dev/null || true"),
        ]
    )
    if process_pattern:
        commands.append(f"pkill -f {shlex.quote(process_pattern)} 2>/dev/null || true")
    commands.extend(
        [
            root_command(f"/bin/launchctl bootstrap system {plist_path}"),
            root_command(f"/bin/launchctl kickstart -k system/{label}"),
            root_command(f"/bin/launchctl print system/{label} >/dev/null"),
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
        launchd_commands=[
            f"/bin/launchctl bootout system/{label}",
            f"/bin/launchctl bootstrap system {plist_path}",
            f"/bin/launchctl kickstart -k system/{label}",
            f"/bin/launchctl print system/{label}",
        ],
    )


def generate_daemon_sudoers_file(*, user: str, command_sets: list[DaemonSudoersCommandSet]) -> str:
    alias_lines: list[str] = []
    allowed_aliases: list[str] = []
    for command_set in command_sets:
        launchd_command_list = ", ".join(command_set.launchd_commands)
        alias_lines.extend(
            [
                f"Cmnd_Alias {command_set.alias_prefix}_INSTALL = {command_set.install_command}",
                f"Cmnd_Alias {command_set.alias_prefix}_LAUNCHD = {launchd_command_list}",
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