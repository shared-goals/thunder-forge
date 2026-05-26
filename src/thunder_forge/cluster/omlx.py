"""oMLX node-level runtime helpers."""

from __future__ import annotations

import shlex

from thunder_forge.cluster.config import Node, RuntimeType


def build_omlx_serve_command(node: Node) -> str:
    """Build the remote command that starts an oMLX server for a runtime node.

    oMLX's default model directory is intentionally represented as
    ``node.runtime.model_dir is None``. In that normal case, omit
    ``--model-dir`` and let oMLX use its own default ``~/.omlx/models``.
    """
    if node.runtime is None:
        msg = "Node has no runtime configured"
        raise ValueError(msg)
    if node.runtime.type != RuntimeType.OMLX:
        msg = f"Unsupported runtime type: {node.runtime.type}"
        raise ValueError(msg)
    if node.home_dir is None:
        msg = "node.home_dir is None — run pre-flight first or provide resolved home_dir"
        raise ValueError(msg)

    args = [
        f"{node.home_dir}/.local/bin/omlx",
        "serve",
        "--host",
        "0.0.0.0",
        "--port",
        str(node.runtime.port),
    ]
    if node.runtime.model_dir is not None:
        args.extend(["--model-dir", node.runtime.model_dir])
    return " ".join(shlex.quote(arg) for arg in args)
