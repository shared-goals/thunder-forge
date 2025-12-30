from __future__ import annotations

import argparse
import json

from services.config_service import load_config
from services.monitor_service import cluster_status_as_dict


def _cmd_status(args: argparse.Namespace) -> int:
    cfg = load_config(args.config)
    print(json.dumps(cluster_status_as_dict(cfg), indent=2, sort_keys=True))
    return 0


def _cmd_serve(_: argparse.Namespace) -> int:
    from main import main

    main()
    return 0


def main() -> None:
    parser = argparse.ArgumentParser(prog="thunder-forge")
    sub = parser.add_subparsers(dest="cmd", required=True)

    p_status = sub.add_parser("status", help="Print current fleet status")
    p_status.add_argument(
        "--config",
        default=None,
        help="Config path (default: TF_CONFIG_PATH or tf.yml)",
    )
    p_status.set_defaults(func=_cmd_status)

    p_serve = sub.add_parser("serve", help="Start FastAPI server")
    p_serve.set_defaults(func=_cmd_serve)

    args = parser.parse_args()
    raise SystemExit(args.func(args))
