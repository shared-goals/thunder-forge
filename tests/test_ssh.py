"""Tests for shared SSH command construction."""

from __future__ import annotations

import subprocess

import thunder_forge.cluster.ssh as ssh_module


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


def test_scp_content_quotes_remote_path(monkeypatch) -> None:
    calls: list[list[str]] = []

    def fake_run(cmd, **kwargs):
        calls.append(cmd)
        return subprocess.CompletedProcess(args=cmd, returncode=0, stdout="", stderr="")

    monkeypatch.setattr(ssh_module, "_is_local", lambda ip: False)
    monkeypatch.setattr(ssh_module.subprocess, "run", fake_run)

    ssh_module.scp_content("shag", "infer-01.lan", "payload", "/tmp/path with spaces/setup.sh")

    assert calls[0][-1] == "cat > '/tmp/path with spaces/setup.sh'"
