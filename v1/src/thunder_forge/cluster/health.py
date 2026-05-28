"""Health checks for compute nodes and gateway services."""

from __future__ import annotations

import json
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor, as_completed

from thunder_forge.cluster.config import ClusterConfig
from thunder_forge.cluster.ssh import ssh_run


def check_node(ip: str, port: int, timeout: float = 5.0) -> bool:
    """Check if a vllm-mlx service is responding on the given node/port."""
    url = f"http://{ip}:{port}/v1/models"
    try:
        handler = urllib.request.ProxyHandler({})
        opener = urllib.request.build_opener(handler)
        with opener.open(url, timeout=timeout):
            return True
    except (urllib.error.URLError, OSError, TimeoutError):
        return False


def check_gateway_services(
    gateway_ip: str,
    gateway_user: str,
    expected_services: tuple[str, ...] = ("litellm", "openwebui", "postgres"),
    *,
    shell: str | None = None,
) -> dict[str, bool]:
    """Check Docker Compose services on gateway node."""
    from thunder_forge.cluster.config import find_repo_root

    results = {svc: False for svc in expected_services}
    docker_dir = find_repo_root() / "docker"
    proc = ssh_run(
        gateway_user, gateway_ip, f"cd {docker_dir} && docker compose ps --format json", timeout=15, shell=shell
    )
    if proc.returncode != 0:
        return results
    for line in proc.stdout.strip().splitlines():
        if not line.strip():
            continue
        try:
            svc = json.loads(line)
        except json.JSONDecodeError:
            continue
        name = svc.get("Name", svc.get("Service", ""))
        state = svc.get("State", "")
        health = svc.get("Health", "")
        for expected in expected_services:
            if expected in name:
                results[expected] = state == "running" and health in ("healthy", "")
    return results


def run_health_checks(config: ClusterConfig) -> bool:
    """Run health checks on all nodes and gateway services. Print results."""
    all_healthy = True

    print("=== Nodes ===")
    check_tasks = []
    for node_name, slots in sorted(config.assignments.items()):
        node = config.nodes[node_name]
        for slot in slots:
            check_tasks.append((node_name, node.ip, slot))
    with ThreadPoolExecutor(max_workers=max(1, len(check_tasks))) as pool:
        futures = {
            pool.submit(check_node, ip, slot.port): (node_name, slot)
            for node_name, ip, slot in check_tasks
        }
        for future in as_completed(futures):
            node_name, slot = futures[future]
            healthy = future.result()
            status = "✓" if healthy else "✗"
            print(f"  {status} {node_name}:{slot.port} ({slot.model})")
            if not healthy:
                all_healthy = False

    print("\n=== Gateway ===")
    gw = config.gateway
    docker_health = check_gateway_services(gw.ip, gw.user, shell=gw.shell)
    display_names = {"litellm": "LiteLLM", "openwebui": "Open WebUI", "postgres": "PostgreSQL"}
    for svc, healthy in docker_health.items():
        status = "✓" if healthy else "✗"
        name = display_names.get(svc, svc)
        print(f"  {status} {name}")
        if not healthy:
            all_healthy = False

    print("\n=== Assignments ===")
    for node_name, slots in sorted(config.assignments.items()):
        slot_strs = [f"{s.model}:{s.port}" for s in slots]
        print(f"  {node_name}: {', '.join(slot_strs)}")

    return all_healthy
