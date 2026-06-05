"""Tests for operator-facing README and Makefile consistency."""

from __future__ import annotations

import re
from pathlib import Path


def _makefile_targets(makefile: str) -> set[str]:
    return {
        match.group(1)
        for match in re.finditer(r"^([A-Za-z][A-Za-z0-9_.-]*):(?:\s|$)", makefile, flags=re.MULTILINE)
    }


def _documented_make_targets(text: str) -> set[str]:
    return set(re.findall(r"(?<![\w-])make\s+([A-Za-z][A-Za-z0-9_.-]*)", text))


def test_active_operator_make_references_exist_in_makefile() -> None:
    repo_root = Path(__file__).parents[1]
    makefile = (repo_root / "Makefile").read_text()
    target_names = _makefile_targets(makefile)

    doc_paths = [
        repo_root / "README.md",
        repo_root / ".github" / "skills" / "thunder-forge" / "SKILL.md",
        *sorted((repo_root / "docs" / "operations").glob("*.md")),
    ]
    missing = {
        f"{path.relative_to(repo_root)}: make {target}"
        for path in doc_paths
        for target in _documented_make_targets(path.read_text())
        if target not in target_names
    }

    assert missing == set()
