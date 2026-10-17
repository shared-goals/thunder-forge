"""Tests for the cluster export command (oMLX state file collection)."""

from __future__ import annotations

import shutil
import tarfile
from pathlib import Path

from thunder_forge.cluster.config import Node, NodeRole
from thunder_forge.cluster.export import (
    OMLX_EXPORT_FILES,
    OMLX_EXPORT_LOG_DIR,
    ExportResult,
    export_cluster,
    export_node,
)


def _make_node(host: str = "127.0.0.1") -> Node:
    return Node(host=host, user="shag", roles=[NodeRole.INFERENCE])


def _make_fake_home(tmp_path: Path, *, with_logs: bool = True) -> Path:
    home = tmp_path / "home"
    omlx = home / ".omlx"
    omlx.mkdir(parents=True)
    for name in OMLX_EXPORT_FILES:
        (omlx / name).write_text("{}" if name.endswith(".json") else "sqlite")
    if with_logs:
        (omlx / OMLX_EXPORT_LOG_DIR).mkdir()
        (omlx / OMLX_EXPORT_LOG_DIR / "server.log").write_text("log line\n")
    return home


def _strip_logs(home: Path) -> None:
    shutil.rmtree(home / ".omlx" / OMLX_EXPORT_LOG_DIR)


def test_export_node_local_collects_state_files(tmp_path: Path, monkeypatch) -> None:
    home = _make_fake_home(tmp_path)
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setattr("thunder_forge.cluster.export._is_local", lambda ip: True)

    result = export_node("msm1", _make_node(), tmp_path / "out")

    assert result.ok, result.error
    assert result.archive_path is not None and result.archive_path.is_file()
    with tarfile.open(result.archive_path, "r:gz") as tar:
        names = tar.getnames()
    for name in (*OMLX_EXPORT_FILES, OMLX_EXPORT_LOG_DIR):
        assert f".omlx/{name}" in names


def test_export_node_fails_when_no_state_files(tmp_path: Path, monkeypatch) -> None:
    home = tmp_path / "empty-home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setattr("thunder_forge.cluster.export._is_local", lambda ip: True)

    result = export_node("msm1", _make_node(), tmp_path / "out")

    assert not result.ok
    assert result.error is not None and "no oMLX state files" in result.error


def test_export_node_skips_missing_files(tmp_path: Path, monkeypatch) -> None:
    home = _make_fake_home(tmp_path)
    (home / ".omlx" / "stats.json").unlink()
    _strip_logs(home)
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setattr("thunder_forge.cluster.export._is_local", lambda ip: True)

    result = export_node("msm1", _make_node(), tmp_path / "out")

    assert result.ok, result.error
    with tarfile.open(result.archive_path, "r:gz") as tar:
        names = tar.getnames()
    assert ".omlx/settings.json" in names
    assert ".omlx/stats.json" not in names
    assert ".omlx/logs" not in names


def test_export_cluster_bundles_per_node_folders(tmp_path: Path, monkeypatch) -> None:
    def fake_export_node(node_id: str, node: Node, output_dir: Path) -> ExportResult:
        staged = output_dir / f"{node_id}.tgz"
        staged.parent.mkdir(parents=True, exist_ok=True)
        source = tmp_path / f"src-{node_id}"
        source.mkdir(parents=True, exist_ok=True)
        (source / "settings.json").write_text(f'{{"node": "{node_id}"}}')
        with tarfile.open(staged, "w:gz") as tar:
            tar.add(source / "settings.json", arcname=".omlx/settings.json")
        return ExportResult(node_id, staged, True)

    monkeypatch.setattr("thunder_forge.cluster.export.export_node", fake_export_node)
    targets = [("msm1", _make_node()), ("msm2", _make_node())]

    results, final_path = export_cluster(targets, tmp_path / "out")

    assert all(r.ok for r in results)
    assert final_path is not None and final_path.name.startswith("tf-export-")
    with tarfile.open(final_path, "r:gz") as tar:
        names = tar.getnames()
    assert "msm1/.omlx/settings.json" in names
    assert "msm2/.omlx/settings.json" in names


def test_export_cluster_fails_when_any_node_fails(tmp_path: Path, monkeypatch) -> None:
    def fake_export_node(node_id: str, node: Node, output_dir: Path) -> ExportResult:
        if node_id == "msm2":
            return ExportResult(node_id, None, False, "boom")
        staged = output_dir / f"{node_id}.tgz"
        staged.parent.mkdir(parents=True, exist_ok=True)
        with tarfile.open(staged, "w:gz"):
            pass
        return ExportResult(node_id, staged, True)

    monkeypatch.setattr("thunder_forge.cluster.export.export_node", fake_export_node)
    targets = [("msm1", _make_node()), ("msm2", _make_node())]

    results, final_path = export_cluster(targets, tmp_path / "out")

    assert not results[1].ok
    assert final_path is None
