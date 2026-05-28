"""Tests for model download and sync logic."""

from pathlib import Path
from textwrap import dedent

import pytest

from thunder_forge.cluster.config import load_cluster_config
from thunder_forge.cluster.models import resolve_model_tasks


@pytest.fixture()
def config_path(tmp_path: Path) -> Path:
    content = dedent("""\
        models:
          coder:
            source:
              type: huggingface
              repo: "mlx-community/Qwen3-Coder-Next-4bit"
              revision: "main"
            disk_gb: 44.8
            kv_per_32k_gb: 8
            max_context: 131072
          local_model:
            source:
              type: local
              path: "/Users/admin/models/custom"
            disk_gb: 30
            kv_per_32k_gb: 6
            max_context: 32768

        nodes:
          rock: { ip: "192.168.1.61", ram_gb: 32, user: "infra_user", role: gateway }
          msm1: { ip: "192.168.1.101", ram_gb: 128, user: "admin", role: node }
          msm2: { ip: "192.168.1.102", ram_gb: 128, user: "admin", role: node }

        assignments:
          msm1:
            - model: coder
              port: 8000
          msm2:
            - model: coder
              port: 8000
            - model: local_model
              port: 8001
    """)
    p = tmp_path / "node-assignments.yaml"
    p.write_text(content)
    return p


def test_resolve_model_tasks(config_path: Path) -> None:
    config = load_cluster_config(config_path)
    tasks = resolve_model_tasks(config)

    coder_tasks = [t for t in tasks if t.model_name == "coder"]
    assert len(coder_tasks) == 1
    assert set(coder_tasks[0].target_nodes) == {"msm1", "msm2"}
    assert coder_tasks[0].source_type == "huggingface"

    local_tasks = [t for t in tasks if t.model_name == "local_model"]
    assert len(local_tasks) == 1
    assert local_tasks[0].source_type == "local"
    assert local_tasks[0].target_nodes == ["msm2"]
