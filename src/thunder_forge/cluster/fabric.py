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

    This is deliberately small: ask the node over its management hostname for
    link-local IPv4 addresses, then try a bounded SSH hostname check against each
    candidate from the local machine. The first candidate that answers is the
    current point-to-point fabric address.
    """
    result = subprocess.run(
        [
            "ssh",
            "-o",
            "BatchMode=yes",
            "-o",
            "ConnectTimeout=8",
            f"{node_user}@{management_host}",
            "ifconfig",
        ],
        check=False,
        capture_output=True,
        text=True,
        timeout=timeout + 8,
    )
    if result.returncode != 0:
        return None

    for address in _extract_link_local_ipv4(result.stdout):
        if _ssh_hostname_check(address, node_user=node_user, timeout=timeout):
            return address
    return None


def _extract_link_local_ipv4(text: str) -> list[str]:
    addresses: list[str] = []
    for match in re.finditer(r"\binet\s+(169\.254\.\d{1,3}\.\d{1,3})\b", text):
        address = match.group(1)
        if _is_acceptable_fabric_address(address) and address not in addresses:
            addresses.append(address)
    return addresses


def _ssh_hostname_check(address: str, *, node_user: str, timeout: int) -> bool:
    result = subprocess.run(
        [
            "ssh",
            "-o",
            "BatchMode=yes",
            "-o",
            f"ConnectTimeout={timeout}",
            f"{node_user}@{address}",
            "hostname",
        ],
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
