from __future__ import annotations

import socket
import time
from typing import Any

from pydantic import BaseModel

from services.config_service import Node, TFConfig


class PortStatus(BaseModel):
    ssh: bool
    ollama: bool


class NodeStatus(BaseModel):
    name: str
    wifi_ip: str
    tb_ip: str | None
    wifi: PortStatus
    thunderbolt: PortStatus


class ClusterStatus(BaseModel):
    ts: float
    nodes: list[NodeStatus]


def _tcp_probe(host: str, port: int, timeout_seconds: float) -> bool:
    try:
        with socket.create_connection((host, port), timeout=timeout_seconds):
            return True
    except OSError:
        return False


def _node_status(node: Node, ssh_port: int, ollama_port: int, timeout_seconds: float) -> NodeStatus:
    wifi = PortStatus(
        ssh=_tcp_probe(node.wifi_ip, ssh_port, timeout_seconds),
        ollama=_tcp_probe(node.wifi_ip, ollama_port, timeout_seconds),
    )
    if node.tb_ip:
        thunderbolt = PortStatus(
            ssh=_tcp_probe(node.tb_ip, ssh_port, timeout_seconds),
            ollama=_tcp_probe(node.tb_ip, ollama_port, timeout_seconds),
        )
    else:
        thunderbolt = PortStatus(ssh=False, ollama=False)

    return NodeStatus(
        name=node.name,
        wifi_ip=node.wifi_ip,
        tb_ip=node.tb_ip,
        wifi=wifi,
        thunderbolt=thunderbolt,
    )


def get_cluster_status(inventory: TFConfig) -> ClusterStatus:
    ssh_port = inventory.settings.monitor.ssh_port
    ollama_port = inventory.settings.monitor.ollama_port
    timeout_seconds = inventory.settings.ssh.connect_timeout_seconds

    nodes = [
        _node_status(node, ssh_port=ssh_port, ollama_port=ollama_port, timeout_seconds=timeout_seconds)
        for node in inventory.nodes
    ]
    return ClusterStatus(ts=time.time(), nodes=nodes)


def cluster_status_as_dict(inventory: TFConfig) -> dict[str, Any]:
    return get_cluster_status(inventory).model_dump()
