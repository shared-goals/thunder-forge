"""Tests for operator-facing README and Makefile consistency."""

from __future__ import annotations

from pathlib import Path


def test_readme_edge_shortcuts_exist_in_makefile() -> None:
    repo_root = Path(__file__).parents[1]
    readme = (repo_root / "README.md").read_text()
    makefile = (repo_root / "Makefile").read_text()

    assert "make edge-keys" in readme
    assert "make edge-usage" in readme
    assert "\nedge-keys:" in makefile
    assert "\nedge-usage:" in makefile
