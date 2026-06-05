"""Tests for shared SSH command construction."""

from __future__ import annotations

import subprocess

import thunder_forge.cluster.ssh as ssh_module
from thunder_forge.cluster.config import Node


def test_ssh_run_uses_strict_host_key_checking_by_default(monkeypatch) -> None:
    calls: list[list[str]] = []

    def fake_run(cmd, **kwargs):
        calls.append(cmd)
        return subprocess.CompletedProcess(args=cmd, returncode=0, stdout="", stderr="")

    monkeypatch.setattr(ssh_module, "_is_local", lambda ip: False)
    monkeypatch.setattr(ssh_module.subprocess, "run", fake_run)
    monkeypatch.delenv("TF_SSH_STRICT_HOST_KEY_CHECKING", raising=False)

    result = ssh_module.ssh_run("shag", "infer-01.lan", "true")

    assert result.returncode == 0
    assert "-o" in calls[0]
    assert "StrictHostKeyChecking=yes" in calls[0]
    assert "StrictHostKeyChecking=no" not in calls[0]


def test_ssh_run_allows_host_key_policy_override(monkeypatch) -> None:
    calls: list[list[str]] = []

    def fake_run(cmd, **kwargs):
        calls.append(cmd)
        return subprocess.CompletedProcess(args=cmd, returncode=0, stdout="", stderr="")

    monkeypatch.setattr(ssh_module, "_is_local", lambda ip: False)
    monkeypatch.setattr(ssh_module.subprocess, "run", fake_run)
    monkeypatch.setenv("TF_SSH_STRICT_HOST_KEY_CHECKING", "accept-new")

    ssh_module.ssh_run("shag", "infer-01.lan", "true")

    assert "StrictHostKeyChecking=accept-new" in calls[0]


def test_ssh_run_without_shell_probes_remote_shell(monkeypatch) -> None:
    calls: list[list[str]] = []

    def fake_run(cmd, **kwargs):
        calls.append(cmd)
        return subprocess.CompletedProcess(args=cmd, returncode=0, stdout="", stderr="")

    monkeypatch.setattr(ssh_module, "_is_local", lambda ip: False)
    monkeypatch.setattr(ssh_module.subprocess, "run", fake_run)

    ssh_module.ssh_run("shag", "infer-01.lan", "true")

    remote_command = calls[0][-1]
    assert "command -v zsh" in remote_command
    assert "exec zsh -lc true" in remote_command
    assert "exec bash -lc true" in remote_command
    assert "exec sh -lc true" in remote_command


def test_scp_content_quotes_remote_path(monkeypatch) -> None:
    calls: list[list[str]] = []

    def fake_run(cmd, **kwargs):
        calls.append(cmd)
        return subprocess.CompletedProcess(args=cmd, returncode=0, stdout="", stderr="")

    monkeypatch.setattr(ssh_module, "_is_local", lambda ip: False)
    monkeypatch.setattr(ssh_module.subprocess, "run", fake_run)

    ssh_module.scp_content("shag", "infer-01.lan", "payload", "/tmp/path with spaces/setup.sh")

    assert calls[0][-1] == "cat > '/tmp/path with spaces/setup.sh'"


def test_resolve_remote_node_facts_populates_shell_home_and_platform(monkeypatch) -> None:
    calls: list[tuple[str, str, str]] = []

    def fake_ssh_run(user, ip, cmd, **kwargs):
        calls.append((user, ip, cmd))
        return subprocess.CompletedProcess(
            args=["ssh"],
            returncode=0,
            stdout='{"platform":"Darwin","shell":"zsh","home_dir":"/Users/shag","homebrew_prefix":"/opt/homebrew"}\n',
            stderr="",
        )

    node = Node(host="infer-01.lan", ram_gb=128, user="shag")
    monkeypatch.setattr(ssh_module, "ssh_run", fake_ssh_run)

    result = ssh_module.resolve_remote_node_facts(node)

    assert result.platform == "Darwin"
    assert result.shell == "zsh"
    assert result.home_dir == "/Users/shag"
    assert result.homebrew_prefix == "/opt/homebrew"
    assert node.platform == "Darwin"
    assert node.shell == "zsh"
    assert node.home_dir == "/Users/shag"
    assert node.homebrew_prefix == "/opt/homebrew"
    assert calls == [("shag", "infer-01.lan", ssh_module.REMOTE_NODE_FACTS_COMMAND)]


def test_resolve_remote_node_facts_leaves_existing_configured_values(monkeypatch) -> None:
    def fake_ssh_run(user, ip, cmd, **kwargs):
        return subprocess.CompletedProcess(
            args=["ssh"],
            returncode=0,
            stdout='{"platform":"Linux","shell":"bash","home_dir":"/home/shag","homebrew_prefix":""}\n',
            stderr="",
        )

    node = Node(
        host="rock.lan",
        ram_gb=64,
        user="serpo",
        shell="/bin/zsh",
        home_dir="/srv/serpo",
    )
    monkeypatch.setattr(ssh_module, "ssh_run", fake_ssh_run)

    result = ssh_module.resolve_remote_node_facts(node)

    assert result.platform == "Linux"
    assert result.shell == "/bin/zsh"
    assert result.home_dir == "/srv/serpo"
    assert node.shell == "/bin/zsh"
    assert node.home_dir == "/srv/serpo"
