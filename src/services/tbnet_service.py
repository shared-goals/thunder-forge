from __future__ import annotations

from services.config_service import Node, SSHSettings
from services.ssh_service import run_ssh_sudo


def configure_thunderbolt_ipv4(*, node: Node, ssh: SSHSettings) -> None:
    if node.tbnet is None:
        raise ValueError(f"Node {node.name} has no tbnet section")

    svc = node.tbnet.service_name
    ip = node.tbnet.ipv4.address
    netmask = node.tbnet.ipv4.netmask
    router = node.tbnet.ipv4.router or "0.0.0.0"

    # macOS: configure a named network service
    # NOTE: requires NOPASSWD sudo on the node.
    cmd = f"networksetup -setmanual {svc!r} {ip} {netmask} {router}"
    run_ssh_sudo(node=node, settings=ssh, remote_command=cmd)
