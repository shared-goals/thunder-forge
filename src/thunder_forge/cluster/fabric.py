"""Fabric transport discovery helpers."""

from __future__ import annotations

import ipaddress
import re
import socket
import subprocess


def resolve_fabric_host(host: str, *, timeout: int = 2) -> str | None:
    """Resolve and lightly validate a configured fabric host alias/IP."""
    address = _resolve_host_to_address(host)
    if address is None or not _is_acceptable_fabric_address(address):
        return None
    if not _ping(address, timeout=timeout):
        return None
    return address


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


def _resolve_host_to_address(host: str) -> str | None:
    try:
        return str(ipaddress.ip_address(host))
    except ValueError:
        pass
    try:
        return socket.gethostbyname(host)
    except OSError:
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


def _ping(address: str, *, timeout: int) -> bool:
    result = subprocess.run(
        ["ping", "-c", "1", "-W", str(timeout * 1000), address],
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
