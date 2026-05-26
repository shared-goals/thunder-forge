"""Tests for fabric transport discovery helpers."""

import subprocess

from thunder_forge.cluster.fabric import discover_link_local_fabric_host, resolve_fabric_host


def test_resolve_fabric_host_accepts_reachable_link_local_ip(monkeypatch) -> None:
    monkeypatch.setattr("thunder_forge.cluster.fabric._ping", lambda address, *, timeout: True)

    assert resolve_fabric_host("169.254.251.195") == "169.254.251.195"


def test_discover_link_local_fabric_host_returns_first_ssh_reachable_node_address(monkeypatch) -> None:
    calls = []

    def fake_run(args, **kwargs):
        calls.append(args)
        if args[-1] == "ifconfig":
            return subprocess.CompletedProcess(
                args=args,
                returncode=0,
                stdout="en12: flags=...\n\tinet 169.254.251.195 netmask 0xffff0000\n",
                stderr="",
            )
        if args[-1] == "hostname" and "169.254.251.195" in args[-2]:
            return subprocess.CompletedProcess(args=args, returncode=0, stdout="", stderr="")
        return subprocess.CompletedProcess(args=args, returncode=1, stdout="", stderr="")

    monkeypatch.setattr(subprocess, "run", fake_run)

    address = discover_link_local_fabric_host(management_host="msm3-wifi.lan", node_user="shag")

    assert address == "169.254.251.195"
    assert calls[0] == [
        "ssh",
        "-o",
        "BatchMode=yes",
        "-o",
        "ConnectTimeout=8",
        "shag@msm3-wifi.lan",
        "ifconfig",
    ]
