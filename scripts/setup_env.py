from __future__ import annotations

import argparse
from pathlib import Path

from services.hosts_service import build_hosts_block, upsert_managed_hosts_block
from services.config_service import load_config
from services.ssh_service import run_ssh_sudo
from services.tbnet_service import configure_thunderbolt_ipv4


def _write_text(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


def _cmd_configure_tb(args: argparse.Namespace) -> int:
    inventory = load_config(args.config)
    ssh = inventory.settings.ssh

    for node in inventory.nodes:
        if node.tbnet is None:
            print(f"[skip] {node.name}: no tbnet section")
            continue
        if node.service_manager != "brew":
            print(f"[skip] {node.name}: tbnet automation only supports macOS/brew nodes for now")
            continue

        print(f"[tbnet] {node.name}: setting {node.tbnet.ipv4.address} on {node.tbnet.service_name}")
        configure_thunderbolt_ipv4(node=node, ssh=ssh)

    return 0


def _cmd_generate_hosts(args: argparse.Namespace) -> int:
    inventory = load_config(args.config)
    artifacts = build_hosts_block(inventory)

    out_path = Path(args.out)
    _write_text(out_path, artifacts.block)
    print(f"wrote {out_path}")
    return 0


def _cmd_push_hosts(args: argparse.Namespace) -> int:
    inventory = load_config(args.config)
    ssh = inventory.settings.ssh

    artifacts = build_hosts_block(inventory)
    local_out = Path(args.out)
    _write_text(local_out, artifacts.block)

    for node in inventory.nodes:
        print(f"[hosts] {node.name}: updating /etc/hosts")
        current = run_ssh_sudo(node=node, settings=ssh, remote_command="cat /etc/hosts").stdout
        updated = upsert_managed_hosts_block(
            hosts_file_text=current,
            managed_block=artifacts.block,
            settings=inventory.settings.hosts_sync,
        )

        # Use a heredoc through tee to overwrite /etc/hosts.
        # KISS: avoid needing remote python.
        escaped = updated.replace("'", "'\\''")
        run_ssh_sudo(
            node=node,
            settings=ssh,
            remote_command=f"sh -lc 'printf %s \'{escaped}\' | tee /etc/hosts >/dev/null'",
        )

    print(f"wrote {local_out} and pushed to {len(inventory.nodes)} nodes")
    return 0


def main() -> int:
    p = argparse.ArgumentParser(prog="setup-env")
    p.add_argument(
        "--config",
        default=None,
        help="Config path (default: TF_CONFIG_PATH or tf.yml)",
    )

    sub = p.add_subparsers(dest="cmd", required=True)

    tb = sub.add_parser("tbnet", help="Configure Thunderbolt Bridge IPv4 on nodes")
    tb.set_defaults(func=_cmd_configure_tb)

    gh = sub.add_parser("hosts", help="Generate managed hosts block from inventory")
    gh.add_argument("--out", default="artifacts/hosts.block")
    gh.set_defaults(func=_cmd_generate_hosts)

    ph = sub.add_parser("push-hosts", help="Generate and push /etc/hosts block to all nodes")
    ph.add_argument("--out", default="artifacts/hosts.block")
    ph.set_defaults(func=_cmd_push_hosts)

    args = p.parse_args()
    return int(args.func(args))


if __name__ == "__main__":
    raise SystemExit(main())
