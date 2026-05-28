"""Tests for fabric transport discovery helpers."""

import subprocess

from thunder_forge.cluster.fabric import build_transport_plan, discover_link_local_fabric_host


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


def test_build_transport_plan_prefers_discovered_fabric_when_enabled(monkeypatch) -> None:
    monkeypatch.setattr(
        "thunder_forge.cluster.fabric.discover_link_local_fabric_host",
        lambda *, management_host, node_user: "169.254.251.195",
    )

    plan = build_transport_plan(
        requested_transport="auto",
        management_host="msm3-wifi.lan",
        node_user="shag",
        fabric_host=True,
    )

    assert plan.ok is True
    assert plan.uses_fabric is True
    assert plan.transport_host == "169.254.251.195"
    assert plan.resolved_transport_host == "169.254.251.195"


def test_build_transport_plan_discovers_link_local_fabric_when_enabled(monkeypatch) -> None:
    monkeypatch.setattr(
        "thunder_forge.cluster.fabric.discover_link_local_fabric_host",
        lambda *, management_host, node_user: "169.254.251.196",
    )

    plan = build_transport_plan(
        requested_transport="auto",
        management_host="msm3-wifi.lan",
        node_user="shag",
        fabric_host=True,
    )

    assert plan.ok is True
    assert plan.transport_host == "169.254.251.196"
    assert plan.resolved_transport_host == "169.254.251.196"


def test_build_transport_plan_uses_management_when_fabric_probe_disabled(monkeypatch) -> None:
    monkeypatch.setattr(
        "thunder_forge.cluster.fabric.discover_link_local_fabric_host",
        lambda *, management_host, node_user: "169.254.251.197",
    )

    plan = build_transport_plan(
        requested_transport="auto",
        management_host="msm3-wifi.lan",
        node_user="shag",
    )

    assert plan.ok is True
    assert plan.uses_fabric is False
    assert plan.transport_host == "msm3-wifi.lan"
    assert plan.resolved_transport_host == "msm3-wifi.lan"
    assert plan.fabric_fallback == ""


def test_build_transport_plan_falls_back_to_management_when_auto_fabric_unreachable(monkeypatch) -> None:
    monkeypatch.setattr(
        "thunder_forge.cluster.fabric.discover_link_local_fabric_host",
        lambda *, management_host, node_user: None,
    )

    plan = build_transport_plan(
        requested_transport="auto",
        management_host="msm3-wifi.lan",
        node_user="shag",
        fabric_host=True,
    )

    assert plan.ok is True
    assert plan.uses_fabric is False
    assert plan.transport_host == "msm3-wifi.lan"
    assert plan.resolved_transport_host == "msm3-wifi.lan"
    assert plan.fabric_fallback == "dynamic probe unresolved"


def test_build_transport_plan_falls_back_to_management_when_dynamic_probe_unresolved(monkeypatch) -> None:
    monkeypatch.setattr(
        "thunder_forge.cluster.fabric.discover_link_local_fabric_host",
        lambda *, management_host, node_user: None,
    )

    plan = build_transport_plan(
        requested_transport="auto",
        management_host="msm3-wifi.lan",
        node_user="shag",
        fabric_host=True,
    )

    assert plan.ok is True
    assert plan.uses_fabric is False
    assert plan.transport_host == "msm3-wifi.lan"
    assert plan.fabric_fallback == "dynamic probe unresolved"


def test_build_transport_plan_forced_fabric_fails_when_dynamic_probe_unresolved(monkeypatch) -> None:
    monkeypatch.setattr(
        "thunder_forge.cluster.fabric.discover_link_local_fabric_host",
        lambda *, management_host, node_user: None,
    )

    plan = build_transport_plan(
        requested_transport="fabric",
        management_host="msm3-wifi.lan",
        node_user="shag",
        fabric_host=True,
    )

    assert plan.ok is False
    assert plan.error == "no reachable fabric address discovered"


def test_build_transport_plan_forced_fabric_fails_when_probe_disabled() -> None:
    plan = build_transport_plan(
        requested_transport="fabric",
        management_host="msm3-wifi.lan",
        node_user="shag",
    )

    assert plan.ok is False
    assert plan.error == "fabric probe disabled for node"


def test_build_transport_plan_rejects_invalid_transport() -> None:
    plan = build_transport_plan(
        requested_transport="warp",
        management_host="msm3-wifi.lan",
        node_user="shag",
    )

    assert plan.ok is False
    assert plan.error == "--transport must be one of: auto, fabric, management"
