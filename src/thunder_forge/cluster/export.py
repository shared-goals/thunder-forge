"""Export oMLX runtime state files from inference nodes."""

from __future__ import annotations

import subprocess
import tarfile
import tempfile
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from thunder_forge.cluster.config import Node
from thunder_forge.cluster.ssh import _is_local, run_local, ssh_run

OMLX_EXPORT_FILES = ("settings.json", "stats.json", "model_settings.json", "usage.sqlite3")
OMLX_EXPORT_LOG_DIR = "logs"
OMLX_DIR = ".omlx"

_REMOTE_TAR_TIMEOUT = 120
_SCP_TIMEOUT = 600


@dataclass(frozen=True)
class ExportResult:
    """Outcome of exporting one node's oMLX state."""

    node_id: str
    archive_path: Path | None
    ok: bool
    error: str | None = None


def _error(completed: subprocess.CompletedProcess[str]) -> str:
    return (completed.stderr or completed.stdout).strip()


def _remote_collect_command(tmp_tar: str) -> str:
    """Build the remote command that tars existing oMLX state files."""
    return (
        f"src=\"$HOME/{OMLX_DIR}\"; "
        "set --; "
        f"for f in {' '.join(OMLX_EXPORT_FILES)} {OMLX_EXPORT_LOG_DIR}; do "
        f'[ -e "$src/$f" ] && set -- "$@" "{OMLX_DIR}/$f"; done; '
        'if [ "$#" -eq 0 ]; then echo "no oMLX state files found in $src" >&2; exit 3; fi; '
        f"tar -C \"$HOME\" -czf {tmp_tar} \"$@\""
    )


def _scp_pull_command(node: Node, remote_tar: str, local_tar: Path) -> list[str]:
    from thunder_forge.cluster.ssh import _ssh_key_args, _strict_host_key_checking

    return [
        "scp",
        *_ssh_key_args(),
        "-o",
        "ConnectTimeout=10",
        "-o",
        f"StrictHostKeyChecking={_strict_host_key_checking()}",
        "-o",
        "BatchMode=yes",
        f"{node.user}@{node.host}:{remote_tar}",
        str(local_tar),
    ]


def export_node(node_id: str, node: Node, output_dir: Path) -> ExportResult:
    """Collect oMLX state files from one node into a local .tgz archive."""
    with tempfile.TemporaryDirectory(prefix="tf-export-") as tmp:
        local_tar = Path(tmp) / f"{node_id}.tgz"
        if _is_local(node.host):
            completed = subprocess.run(
                ["bash", "-lc", _remote_collect_command(str(local_tar))],
                capture_output=True,
                text=True,
                timeout=_REMOTE_TAR_TIMEOUT,
            )
        else:
            remote_tar = f"/tmp/tf-export-{node_id}.tgz"
            completed = ssh_run(
                node.user,
                node.host,
                _remote_collect_command(remote_tar),
                timeout=_REMOTE_TAR_TIMEOUT,
                shell=node.shell,
            )
            if completed.returncode != 0:
                return ExportResult(node_id, None, False, _error(completed))
            completed = run_local(_scp_pull_command(node, remote_tar, local_tar), timeout=_SCP_TIMEOUT)
            if completed.returncode != 0:
                return ExportResult(node_id, None, False, _error(completed))
            ssh_run(
                node.user,
                node.host,
                f"rm -f {remote_tar}",
                timeout=30,
                shell=node.shell,
            )
        if completed.returncode != 0:
            return ExportResult(node_id, None, False, _error(completed))
        if not local_tar.is_file() or local_tar.stat().st_size == 0:
            return ExportResult(node_id, None, False, "archive download failed or empty")

        with tarfile.open(local_tar, "r:gz") as tar:
            names = tar.getnames()
        if not names:
            return ExportResult(node_id, None, False, "archive contains no files")
        tar_bytes = local_tar.read_bytes()

    output_dir.mkdir(parents=True, exist_ok=True)
    final_path = output_dir / f"{node_id}.tgz"
    final_path.write_bytes(tar_bytes)
    return ExportResult(node_id, final_path, True)


def export_cluster(
    targets: list[tuple[str, Node]],
    output_dir: Path,
) -> tuple[list[ExportResult], Path | None]:
    """Export each node and bundle per-node folders into one timestamped archive."""
    with tempfile.TemporaryDirectory(prefix="tf-export-bundle-") as tmp:
        bundle_tmp = Path(tmp)
        results = [export_node(node_id, node, bundle_tmp / "staged") for node_id, node in targets]
        if any(not r.ok for r in results):
            return results, None

        extract_dir = bundle_tmp / "bundle"
        for result in results:
            node_dir = extract_dir / result.node_id
            node_dir.mkdir(parents=True)
            with tarfile.open(result.archive_path, "r:gz") as tar:  # type: ignore[arg-type]
                tar.extractall(node_dir, filter="data")

        output_dir.mkdir(parents=True, exist_ok=True)
        timestamp = datetime.now(UTC).strftime("%Y%m%d-%H%M")
        first_node = results[0].node_id
        name = f"{first_node}-{timestamp}.tgz" if len(results) == 1 else f"tf-export-{timestamp}.tgz"
        final_path = output_dir / name
        with tarfile.open(final_path, "w:gz") as tar:
            for result in results:
                tar.add(extract_dir / result.node_id, arcname=result.node_id)
        return results, final_path
