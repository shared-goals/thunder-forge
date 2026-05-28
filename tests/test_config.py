"""Tests for config parsing, validation, and generation."""

from pathlib import Path
from textwrap import dedent

import pytest
import yaml as yaml_lib

from thunder_forge.cluster.config import (
    generate_olla_config,
    load_cluster_config,
    parse_cluster_config,
)


@pytest.fixture()
def cluster_yaml(tmp_path: Path) -> Path:
    """Create a minimal node-assignments.yaml for testing."""
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

        nodes:
          rock: { host: "rock.lan", ram_gb: 32, user: "infra_user", role: gateway }
          msm1: { host: "msm1-wifi.lan", ram_gb: 128, user: "admin", role: node }
    """)
    p = tmp_path / "node-assignments.yaml"
    p.write_text(content)
    return p


def test_load_cluster_config(cluster_yaml: Path) -> None:
    config = load_cluster_config(cluster_yaml)
    assert "coder" in config.models
    assert config.models["coder"].source.type == "huggingface"
    assert config.models["coder"].disk_gb == 44.8
    assert "msm1" in config.nodes
    assert config.nodes["msm1"].host == "msm1-wifi.lan"
    assert config.nodes["msm1"].ip == "msm1-wifi.lan"
    assert config.nodes["msm1"].role == "node"
    assert "rock" in config.nodes
    assert config.nodes["rock"].role == "gateway"


def test_load_cluster_config_user_defaults(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Both node and gateway default to the current OS user when no YAML user or GATEWAY_SSH_USER is set."""
    import thunder_forge.cluster.config as config_module

    monkeypatch.setenv("USER", "testuser")
    monkeypatch.delenv("GATEWAY_SSH_USER", raising=False)
    # Point find_repo_root to tmp_path so no real .env is loaded (which could set GATEWAY_SSH_USER)
    monkeypatch.setattr(config_module, "find_repo_root", lambda: tmp_path)

    content = dedent("""\
        models:
          coder:
            source: { type: huggingface, repo: "test/coder" }
            disk_gb: 10
        nodes:
          rock: { host: "rock.lan", ram_gb: 32, role: gateway }
          msm1: { host: "msm1-wifi.lan", ram_gb: 128, role: node }
    """)
    p = tmp_path / "node-assignments.yaml"
    p.write_text(content)
    config = load_cluster_config(p)
    assert config.nodes["msm1"].user == "testuser"
    assert config.nodes["rock"].user == "testuser"


def test_load_cluster_config_role_migration(tmp_path: Path) -> None:
    """Old role names (inference, infra) are migrated with a deprecation warning."""
    content = dedent("""\
        models:
          coder:
            source: { type: huggingface, repo: "test/coder" }
            disk_gb: 10
        nodes:
          rock: { host: "rock.lan", ram_gb: 32, user: "infra_user", role: infra }
          msm1: { host: "msm1-wifi.lan", ram_gb: 128, user: "admin", role: inference }
    """)
    p = tmp_path / "node-assignments.yaml"
    p.write_text(content)
    with pytest.warns(DeprecationWarning, match="deprecated"):
        config = load_cluster_config(p)
    assert config.nodes["msm1"].role == "node"
    assert config.nodes["rock"].role == "gateway"


def test_node_resolved_fields_default_to_none(cluster_yaml: Path) -> None:
    """Resolved fields are None after initial load — populated later by pre-flight."""
    config = load_cluster_config(cluster_yaml)
    for node in config.nodes.values():
        assert node.platform is None
        assert node.shell is None
        assert node.home_dir is None
        assert node.homebrew_prefix is None


def test_parse_node_runtime_identity_defaults_to_omlx_model_dir(tmp_path: Path) -> None:
    """A v2 node can declare management host, fabric probing, and node-level oMLX runtime."""
    content = dedent("""\
        models: {}
        nodes:
          msm3:
            host: msm3-wifi.lan
            fabric_host: true
            ram_gb: 128
            user: shag
            role: node
            home_dir: /srv/shag
            runtime:
              type: omlx
              port: 8018
    """)
    p = tmp_path / "node-assignments.yaml"
    p.write_text(content)

    config = load_cluster_config(p)
    node = config.nodes["msm3"]

    assert node.host == "msm3-wifi.lan"
    assert node.fabric_host is True
    assert node.home_dir == "/srv/shag"
    assert node.runtime is not None
    assert node.runtime.type == "omlx"
    assert node.runtime.port == 8018
    assert node.runtime.model_dir is None


def test_parse_node_rejects_string_fabric_host(tmp_path: Path) -> None:
    content = dedent("""\
        models: {}
        nodes:
          msm3:
            host: msm3-wifi.lan
            fabric_host: msm3-fabric
            ram_gb: 128
            user: shag
            role: node
    """)
    p = tmp_path / "node-assignments.yaml"
    p.write_text(content)

    with pytest.raises(ValueError, match="fabric_host must be boolean"):
        load_cluster_config(p)


def test_load_cluster_config_user_from_env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """GATEWAY_SSH_USER env var overrides default when no YAML user is set."""
    content = dedent("""\
        models:
          coder:
            source: { type: huggingface, repo: "test/coder" }
            disk_gb: 10
        nodes:
          msm1: { host: "msm1-wifi.lan", ram_gb: 128, role: node }
    """)
    p = tmp_path / "node-assignments.yaml"
    p.write_text(content)
    monkeypatch.setenv("GATEWAY_SSH_USER", "deploy_bot")
    config = load_cluster_config(p)
    assert config.nodes["msm1"].user == "deploy_bot"


def test_generate_olla_config_runtime_route_with_alias_and_failover_probe(tmp_path: Path) -> None:
    content = dedent("""\
        models: {}
        nodes:
          studio:
            host: studio.lan
            ram_gb: 64
            user: shag
            role: gateway
          msm3:
            host: msm3-wifi.lan
            ram_gb: 128
            user: shag
            role: node
            runtime:
              type: omlx
              port: 8018
        runtime_routes:
          - model_name: qwen3-1.7b-omlx-msm3-test
            runtime: omlx
            node: msm3
            model: Qwen3-1.7B-4bit
    """)
    p = tmp_path / "node-assignments.yaml"
    p.write_text(content)
    config = load_cluster_config(p)

    result = generate_olla_config(config)
    parsed = yaml_lib.safe_load(result)

    assert result.startswith("# AUTO-GENERATED")
    assert parsed["server"]["host"] == "127.0.0.1"
    assert parsed["server"]["port"] == 40115
    assert parsed["proxy"]["engine"] == "olla"
    assert parsed["proxy"]["sticky_sessions"]["enabled"] is True
    endpoints = parsed["discovery"]["static"]["endpoints"]
    assert endpoints == [
        {
            "url": "http://msm3-wifi.lan:8018",
            "name": "msm3-omlx-live",
            "type": "openai-compatible",
            "priority": 100,
            "model_url": "/v1/models",
            "health_check_url": "/health",
            "check_interval": "3s",
            "check_timeout": "2s",
        }
    ]
    assert parsed["model_aliases"] == {
        "qwen3-1.7b-omlx-msm3-test": ["Qwen3-1.7B-4bit"],
    }


def test_load_cluster_config_loads_dotenv(cluster_yaml: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """load_cluster_config loads .env from repo root."""
    import thunder_forge.cluster.config as config_module

    monkeypatch.delenv("HF_HOME", raising=False)

    # Create .env next to configs/ (find_repo_root() will find this)
    repo_root = cluster_yaml.parent.parent
    (repo_root / ".git").mkdir(exist_ok=True)  # find_repo_root() needs a git marker
    dotenv_path = repo_root / ".env"
    dotenv_path.write_text("HF_HOME=/test/hf/cache\n")

    monkeypatch.setattr(config_module, "find_repo_root", lambda: repo_root)

    load_cluster_config(cluster_yaml)

    import os

    assert os.environ.get("HF_HOME") == "/test/hf/cache"


def test_parse_model_info() -> None:
    """model_info is parsed into ModelInfo dataclass."""
    raw = {
        "models": {
            "coder": {
                "source": {"type": "huggingface", "repo": "test/coder"},
                "disk_gb": 10,
                "model_info": {
                    "base_model": "meta-llama/Llama-3-70b",
                    "mode": "chat",
                    "input_cost_per_token": 0.000001,
                    "output_cost_per_token": 0.000002,
                    "supports_vision": True,
                    "supports_function_calling": True,
                    "supports_parallel_function_calling": False,
                    "supports_response_schema": True,
                },
            }
        },
        "nodes": {},
    }
    config = parse_cluster_config(raw)
    mi = config.models["coder"].model_info
    assert mi is not None
    assert mi.base_model == "meta-llama/Llama-3-70b"
    assert mi.mode == "chat"
    assert mi.input_cost_per_token == 0.000001
    assert mi.output_cost_per_token == 0.000002
    assert mi.supports_vision is True
    assert mi.supports_function_calling is True
    assert mi.supports_parallel_function_calling is False
    assert mi.supports_response_schema is True


def test_parse_model_info_absent() -> None:
    """model_info is None when not provided."""
    raw = {
        "models": {
            "coder": {
                "source": {"type": "huggingface", "repo": "test/coder"},
                "disk_gb": 10,
            }
        },
        "nodes": {},
    }
    config = parse_cluster_config(raw)
    assert config.models["coder"].model_info is None


