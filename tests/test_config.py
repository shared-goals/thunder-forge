"""Tests for config parsing, validation, and generation."""

from pathlib import Path
from textwrap import dedent

import pytest
import yaml as yaml_lib

from thunder_forge.cluster.config import (
    DEFAULT_EDGE_ACCESS_LOG,
    DEFAULT_EDGE_HOST,
    find_repo_root,
    generate_olla_config,
    lint_cluster_config,
    load_cluster_config,
    parse_cluster_config,
)


@pytest.fixture()
def cluster_yaml(tmp_path: Path) -> Path:
    """Create a minimal tfconfig.yaml for testing."""
    content = dedent("""\
        models:
          coder:
            source:
              repo: "mlx-community/Qwen3-Coder-Next-4bit"
            runtime_model_id: Qwen3-Coder-Next-4bit
            disk_gb: 44.8
            kv_per_32k_gb: 8
            max_context: 131072

        nodes:
          rock: { host: "rock.lan", ram_gb: 32, user: "infra_user", roles: [gateway] }
          msm1:
            host: "msm1-wifi.lan"
            ram_gb: 128
            user: "admin"
            roles: [inference]
            admin_user: admin
            runtime: { type: omlx, port: 8018 }
            models: [coder]
    """)
    path = tmp_path / "tfconfig.yaml"
    path.write_text(content)
    return path


def test_load_cluster_config(cluster_yaml: Path) -> None:
    config = load_cluster_config(cluster_yaml)
    assert "coder" in config.models
    assert config.models["coder"].source.type == "huggingface"
    assert config.models["coder"].runtime_model_id == "Qwen3-Coder-Next-4bit"
    assert config.models["coder"].disk_gb == 44.8
    assert "msm1" in config.nodes
    assert config.nodes["msm1"].host == "msm1-wifi.lan"
    assert config.nodes["msm1"].role == "inference"
    assert config.nodes["msm1"].admin_user == "admin"
    assert config.nodes["msm1"].models == ["coder"]
    assert "rock" in config.nodes
    assert config.nodes["rock"].role == "gateway"


def test_parse_config_admin_users() -> None:
    config = parse_cluster_config(
        {
            "services": {"frontend": {"admin_user": "serpo"}},
            "models": {},
            "nodes": {
                "studio": {
                    "host": "studio.lan",
                    "ram_gb": 128,
                    "user": "shag",
                    "admin_user": "serpo",
                    "roles": ["gateway", "cache"],
                },
                "msm3": {
                    "host": "msm3-wifi.lan",
                    "ram_gb": 128,
                    "user": "shag",
                    "admin_user": "admin",
                    "roles": ["inference"],
                }
            },
        }
    )

    assert config.services.frontend_admin_user == "serpo"
    assert config.nodes["studio"].roles == ["gateway", "cache"]
    assert config.nodes["studio"].admin_user == "serpo"
    assert config.nodes["msm3"].user == "shag"
    assert config.nodes["msm3"].admin_user == "admin"


def test_parse_config_rejects_role_field() -> None:
    with pytest.raises(ValueError, match="'role' is not supported"):
        parse_cluster_config(
            {
                "models": {},
                "nodes": {
                    "studio": {
                        "host": "studio.lan",
                        "ram_gb": 128,
                        "role": "gateway",
                    }
                },
            }
        )


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
          rock: { host: "rock.lan", ram_gb: 32, roles: [gateway] }
          msm1: { host: "msm1-wifi.lan", ram_gb: 128, roles: [inference] }
    """)
    path = tmp_path / "tfconfig.yaml"
    path.write_text(content)
    config = load_cluster_config(path)
    assert config.nodes["msm1"].user == "testuser"
    assert config.nodes["rock"].user == "testuser"


def test_load_cluster_config_rejects_legacy_roles(tmp_path: Path) -> None:
    """Old role names are rejected instead of migrated."""
    content = dedent("""\
        models:
          coder:
            source: { type: huggingface, repo: "test/coder" }
            disk_gb: 10
        nodes:
          rock: { host: "rock.lan", ram_gb: 32, user: "infra_user", roles: [infra] }
          msm1: { host: "msm1-wifi.lan", ram_gb: 128, user: "admin", roles: [node] }
    """)
    path = tmp_path / "tfconfig.yaml"
    path.write_text(content)
    with pytest.raises(ValueError, match="not a valid"):
        load_cluster_config(path)


def test_node_resolved_fields_default_to_none(cluster_yaml: Path) -> None:
    """Resolved fields are None after initial load - populated later by pre-flight."""
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
            roles: [inference]
            home_dir: /srv/shag
            runtime:
              type: omlx
              port: 8018
    """)
    path = tmp_path / "tfconfig.yaml"
    path.write_text(content)

    config = load_cluster_config(path)
    node = config.nodes["msm3"]

    assert node.host == "msm3-wifi.lan"
    assert node.fabric_host is True
    assert node.home_dir == "/srv/shag"
    assert node.runtime is not None
    assert node.runtime.type == "omlx"
    assert node.runtime.port == 8018
    assert node.runtime.model_dir is None
    assert node.models == []


def test_parse_node_runtime_options_for_omlx_serve(tmp_path: Path) -> None:
    content = dedent("""\
        models: {}
        nodes:
          msm3:
            host: msm3-wifi.lan
            ram_gb: 128
            user: shag
            roles: [node]
            runtime:
              type: omlx
              port: 8018
              bind_host: 127.0.0.1
              base_path: /Users/shag/.omlx-tf
              log_level: warning
              max_model_memory: 90GB
              max_process_memory: auto
              max_concurrent_requests: 4
              paged_ssd_cache_dir: /Users/shag/.omlx/cache
              paged_ssd_cache_max_size: 50GB
              hot_cache_max_size: 8GB
              no_cache: true
              mcp_config: /Users/shag/.omlx/mcp.json
              hf_endpoint: https://hf.example
              trusted_network: true
    """)
    path = tmp_path / "node-assignments.yaml"
    path.write_text(content)

    config = load_cluster_config(path)
    runtime = config.nodes["msm3"].runtime

    assert runtime is not None
    assert runtime.bind_host == "127.0.0.1"
    assert runtime.base_path == "/Users/shag/.omlx-tf"
    assert runtime.log_level == "warning"
    assert runtime.max_model_memory == "90GB"
    assert runtime.max_process_memory == "auto"
    assert runtime.max_concurrent_requests == 4
    assert runtime.paged_ssd_cache_dir == "/Users/shag/.omlx/cache"
    assert runtime.paged_ssd_cache_max_size == "50GB"
    assert runtime.hot_cache_max_size == "8GB"
    assert runtime.no_cache is True
    assert runtime.mcp_config == "/Users/shag/.omlx/mcp.json"
    assert runtime.hf_endpoint == "https://hf.example"
    assert runtime.trusted_network is True


def test_example_config_keeps_active_omlx_runtime_blocks_consistent() -> None:
    example_path = Path(__file__).parents[1] / "tfconfig.example.yaml"
    raw = yaml_lib.safe_load(example_path.read_text())
    runtime_blocks = [
        node["runtime"]
        for node in raw.get("nodes", {}).values()
        if isinstance(node, dict) and node.get("runtime", {}).get("type") == "omlx"
    ]

    assert len(runtime_blocks) >= 1
    assert all(runtime == runtime_blocks[0] for runtime in runtime_blocks)


def test_parse_node_rejects_string_fabric_host(tmp_path: Path) -> None:
    content = dedent("""\
        models: {}
        nodes:
          msm3:
            host: msm3-wifi.lan
            fabric_host: msm3-fabric
            ram_gb: 128
            user: shag
            roles: [inference]
    """)
    path = tmp_path / "tfconfig.yaml"
    path.write_text(content)

    with pytest.raises(ValueError, match="fabric_host must be boolean"):
        load_cluster_config(path)


def test_load_cluster_config_user_from_env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """GATEWAY_SSH_USER env var overrides default when no YAML user is set."""
    content = dedent("""\
        models:
          coder:
            source: { type: huggingface, repo: "test/coder" }
            disk_gb: 10
        nodes:
          msm1: { host: "msm1-wifi.lan", ram_gb: 128, roles: [inference] }
    """)
    path = tmp_path / "tfconfig.yaml"
    path.write_text(content)
    monkeypatch.setenv("GATEWAY_SSH_USER", "deploy_bot")
    config = load_cluster_config(path)
    assert config.nodes["msm1"].user == "deploy_bot"


def test_generate_olla_config_node_models_with_alias_and_failover_probe(tmp_path: Path) -> None:
    content = dedent("""\
        models:
          qwen3-1.7b-omlx-msm3-test:
            source: { repo: mlx-community/Qwen3-1.7B-4bit }
            runtime_model_id: Qwen3-1.7B-4bit
        nodes:
          studio:
            host: studio.lan
            ram_gb: 64
            user: shag
            roles: [gateway]
          msm3:
            host: msm3-wifi.lan
            ram_gb: 128
            user: shag
            roles: [inference]
            runtime:
              type: omlx
              port: 8018
            models:
              - qwen3-1.7b-omlx-msm3-test
    """)
    path = tmp_path / "tfconfig.yaml"
    path.write_text(content)
    config = load_cluster_config(path)

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

def test_generate_olla_config_uses_service_port_and_ignores_env(monkeypatch) -> None:
    monkeypatch.setenv("TF_OLLA_PORT", "45115")
    config = parse_cluster_config(
        {
            "services": {"olla": {"port": 46115}},
            "models": {
                "memory": {
                    "source": {"repo": "mlx-community/gpt-oss-20b-MXFP4-Q8"},
                    "runtime_model_id": "gpt-oss-20b-MXFP4-Q8",
                }
            },
            "nodes": {
                "msm3": {
                    "host": "msm3-wifi.lan",
                    "ram_gb": 128,
                    "roles": ["inference"],
                    "runtime": {"type": "omlx", "port": 8018},
                    "models": ["memory"],
                }
            },
        },
    )

    parsed = yaml_lib.safe_load(generate_olla_config(config))

    assert parsed["server"]["port"] == 46115


def test_runtime_port_defaults_to_shared_omlx_service_port() -> None:
    config = parse_cluster_config(
        {
            "services": {"omlx": {"port": 8818}},
            "models": {},
            "nodes": {
                "msm3": {
                    "host": "msm3-wifi.lan",
                    "ram_gb": 128,
                    "roles": ["inference"],
                    "runtime": {"type": "omlx"},
                }
            },
        },
    )

    assert config.nodes["msm3"].runtime is not None
    assert config.nodes["msm3"].runtime.port == 8818


def test_edge_defaults_and_overrides() -> None:
    default_config = parse_cluster_config({"models": {}, "nodes": {}})
    assert default_config.services.edge_host == DEFAULT_EDGE_HOST
    assert default_config.services.edge_access_log == DEFAULT_EDGE_ACCESS_LOG

    configured = parse_cluster_config(
        {
            "services": {"edge": {"host": "127.0.0.1", "access_log": "logs/custom-edge.jsonl"}},
            "models": {},
            "nodes": {},
        }
    )
    assert configured.services.edge_host == "127.0.0.1"
    assert configured.services.edge_access_log == "logs/custom-edge.jsonl"


def test_generate_olla_config_rejects_unknown_node_model(tmp_path: Path) -> None:
    content = dedent("""\
        models: {}
        nodes:
          msm3:
            host: msm3-wifi.lan
            ram_gb: 128
            user: shag
            roles: [inference]
            runtime:
              type: omlx
              port: 8018
            models:
              - missing-model
    """)
    path = tmp_path / "tfconfig.yaml"
    path.write_text(content)
    config = load_cluster_config(path)

    with pytest.raises(ValueError, match="unknown model 'missing-model'"):
        generate_olla_config(config)


def test_lint_cluster_config_reports_unknown_models_and_exposure_warnings(tmp_path: Path) -> None:
    content = dedent("""\
        models:
          memory:
            source: { repo: mlx-community/gpt-oss-20b-MXFP4-Q8 }
            runtime_model_id: gpt-oss-20b-MXFP4-Q8
          memory-copy:
            source: { repo: mlx-community/gpt-oss-20b-MXFP4-Q8 }
            runtime_model_id: gpt-oss-20b-MXFP4-Q8
          memory-bf16:
            source: { repo: mlx-community/gpt-oss-20b-mxfp4-bf16 }
            benchmark_only: true
            runtime_model_id: gpt-oss-20b-mxfp4-bf16
        nodes:
          msm3:
            host: msm3-wifi.lan
            ram_gb: 128
            user: shag
            roles: [node]
            runtime:
              type: omlx
              port: 8018
            models:
              - memory
              - memory-bf16
              - missing
    """)
    path = tmp_path / "node-assignments.yaml"
    path.write_text(content)
    config = load_cluster_config(path)

    issues = lint_cluster_config(config)

    assert ("error", "nodes.msm3.models", "unknown model 'missing'") in [
        (issue.severity, issue.path, issue.message) for issue in issues
    ]
    assert ("warning", "nodes.msm3.models", "benchmark-only model 'memory-bf16' is assigned to node") in [
        (issue.severity, issue.path, issue.message) for issue in issues
    ]
    assert ("warning", "models.memory-copy.runtime_model_id", "runtime model id also used by 'memory'") in [
        (issue.severity, issue.path, issue.message) for issue in issues
    ]
    assert ("warning", "nodes.msm3.runtime", "oMLX runtime binds 0.0.0.0 without trusted_network: true") in [
        (issue.severity, issue.path, issue.message) for issue in issues
    ]


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


def test_find_repo_root_ignores_legacy_node_assignments(monkeypatch, tmp_path: Path) -> None:
    legacy_dir = tmp_path / "configs"
    legacy_dir.mkdir()
    (legacy_dir / "node-assignments.yaml").write_text("models: {}\nnodes: {}\n")
    monkeypatch.chdir(tmp_path)

    with pytest.raises(FileNotFoundError, match="tfconfig.yaml"):
        find_repo_root()


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
    model_info = config.models["coder"].model_info
    assert model_info is not None
    assert model_info.base_model == "meta-llama/Llama-3-70b"
    assert model_info.mode == "chat"
    assert model_info.input_cost_per_token == 0.000001
    assert model_info.output_cost_per_token == 0.000002
    assert model_info.supports_vision is True
    assert model_info.supports_function_calling is True
    assert model_info.supports_parallel_function_calling is False
    assert model_info.supports_response_schema is True


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
