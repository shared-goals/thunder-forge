"""Fabric transport discovery helpers."""

from __future__ import annotations

import ipaddress
import re
import subprocess
from dataclasses import dataclass


@dataclass(frozen=True)
class TransportPlan:
    requested_transport: str
    management_host: str
    transport_host: str
    resolved_transport_host: str
    fabric_fallback: str = ""
    error: str = ""

    @property
    def ok(self) -> bool:
        return not self.error

    @property
    def uses_fabric(self) -> bool:
        return self.ok and self.transport_host != self.management_host


def build_transport_plan(
    *,
    requested_transport: str,
    management_host: str,
    node_user: str,
    fabric_host: bool = False,
) -> TransportPlan:
    """Select the best sync transport for a node.

    ``fabric_host`` is a boolean config flag: true enables dynamic
    Thunderbolt/link-local probing, false disables it. ``auto`` probes only when
    enabled and falls back visibly to the management host. ``fabric`` fails
    instead of falling back, because the caller explicitly requested fabric
    transport.
    """
    if requested_transport not in {"auto", "fabric", "management"}:
        return TransportPlan(
            requested_transport=requested_transport,
            management_host=management_host,
            transport_host=management_host,
            resolved_transport_host=management_host,
            error="--transport must be one of: auto, fabric, management",
        )

    if requested_transport == "management":
        return TransportPlan(
            requested_transport=requested_transport,
            management_host=management_host,
            transport_host=management_host,
            resolved_transport_host=management_host,
        )

    if not fabric_host:
        error = "fabric probe disabled for node" if requested_transport == "fabric" else ""
        return TransportPlan(
            requested_transport=requested_transport,
            management_host=management_host,
            transport_host=management_host,
            resolved_transport_host=management_host,
            error=error,
        )

    resolved_fabric_host = discover_link_local_fabric_host(
        management_host=management_host,
        node_user=node_user,
    )
    if resolved_fabric_host is not None:
        return TransportPlan(
            requested_transport=requested_transport,
            management_host=management_host,
            transport_host=resolved_fabric_host,
            resolved_transport_host=resolved_fabric_host,
        )

    if requested_transport == "fabric":
        return TransportPlan(
            requested_transport=requested_transport,
            management_host=management_host,
            transport_host=management_host,
            resolved_transport_host=management_host,
            error="no reachable fabric address discovered",
        )

    return TransportPlan(
        requested_transport=requested_transport,
        management_host=management_host,
        transport_host=management_host,
        resolved_transport_host=management_host,
        fabric_fallback="dynamic probe unresolved",
    )


def discover_link_local_fabric_host(
    *,
    management_host: str,
    node_user: str,
    timeout: int = 2,
) -> str | None:
    """Discover a reachable link-local fabric address for a node.

    Ask the node over its management hostname for Thunderbolt interface
    inventory and link-local IPv4 addresses, then verify from the local machine
    that the route to the candidate also uses a Thunderbolt interface. This
    avoids treating unrelated 169.254 addresses as fabric.
    """
    local_thunderbolt_devices = _local_thunderbolt_devices(timeout=timeout)
    if not local_thunderbolt_devices:
        return None

    result = subprocess.run(
        [
            "ssh",
            "-o",
            "BatchMode=yes",
            "-o",
            "ConnectTimeout=8",
            f"{node_user}@{management_host}",
            "networksetup -listallhardwareports 2>/dev/null; printf '\\n__TF_IFCONFIG__\\n'; ifconfig",
        ],
        check=False,
        capture_output=True,
        text=True,
        timeout=timeout + 8,
    )
    if result.returncode != 0:
        return None

    remote_inventory, _, remote_ifconfig = result.stdout.partition("__TF_IFCONFIG__")
    remote_thunderbolt_devices = _extract_thunderbolt_devices(remote_inventory)
    if not remote_thunderbolt_devices:
        return None

    for address in _extract_link_local_ipv4(remote_ifconfig, allowed_interfaces=remote_thunderbolt_devices):
        if (
            _route_uses_allowed_interface(address, allowed_interfaces=local_thunderbolt_devices, timeout=timeout)
            and _ssh_hostname_check(
                address,
                node_user=node_user,
                host_key_alias=management_host,
                timeout=timeout,
            )
        ):
            return address
    return None


def _local_thunderbolt_devices(*, timeout: int) -> set[str]:
    result = subprocess.run(
        ["networksetup", "-listallhardwareports"],
        check=False,
        capture_output=True,
        text=True,
        timeout=timeout,
    )
    if result.returncode != 0:
        return set()
    return _extract_thunderbolt_devices(result.stdout)


def _extract_thunderbolt_devices(text: str) -> set[str]:
    devices: set[str] = set()
    for block in re.split(r"\n\s*\n", text):
        if "Thunderbolt" not in block:
            continue
        match = re.search(r"^Device:\s*(\S+)\s*$", block, flags=re.MULTILINE)
        if match:
            devices.add(match.group(1))
    return devices


def _extract_link_local_ipv4(text: str, *, allowed_interfaces: set[str] | None = None) -> list[str]:
    addresses: list[str] = []
    current_interface = ""
    for line in text.splitlines():
        interface_match = re.match(r"^([A-Za-z0-9._-]+):", line)
        if interface_match:
            current_interface = interface_match.group(1)
        if allowed_interfaces is not None and current_interface not in allowed_interfaces:
            continue
        match = re.search(r"\binet\s+(169\.254\.\d{1,3}\.\d{1,3})\b", line)
        if match:
            address = match.group(1)
            if _is_acceptable_fabric_address(address) and address not in addresses:
                addresses.append(address)
    return addresses


def _route_uses_allowed_interface(address: str, *, allowed_interfaces: set[str], timeout: int) -> bool:
    result = subprocess.run(
        ["route", "-n", "get", address],
        check=False,
        capture_output=True,
        text=True,
        timeout=timeout,
    )
    if result.returncode != 0:
        return False
    match = re.search(r"^\s*interface:\s*(\S+)\s*$", result.stdout, flags=re.MULTILINE)
    return bool(match and match.group(1) in allowed_interfaces)


def _ssh_hostname_check(address: str, *, node_user: str, host_key_alias: str, timeout: int) -> bool:
    ssh_args = [
        "ssh",
        "-o",
        "BatchMode=yes",
        "-o",
        f"ConnectTimeout={timeout}",
        "-o",
        f"HostKeyAlias={host_key_alias}",
        f"{node_user}@{address}",
        "hostname",
    ]
    result = subprocess.run(
        ssh_args,
        check=False,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        timeout=timeout + 1,
    )
    return result.returncode == 0


def _is_acceptable_fabric_address(address: str) -> bool:
    try:
        parsed = ipaddress.ip_address(address)
    except ValueError:
        return False
    return parsed.is_link_local or parsed.is_private
