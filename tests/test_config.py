"""Tests for config parsing, validation, and generation."""

from pathlib import Path
from textwrap import dedent

import pytest
import yaml as yaml_lib

from thunder_forge.cluster.config import (
    check_config_sync,
    generate_litellm_config,
    load_cluster_config,
    parse_cluster_config,
    validate_memory,
)


@pytest.fixture()
def assignments_yaml(tmp_path: Path) -> Path:
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

        assignments:
          msm1:
            - model: coder
              port: 8000
    """)
    p = tmp_path / "node-assignments.yaml"
    p.write_text(content)
    return p


def test_load_cluster_config(assignments_yaml: Path) -> None:
    config = load_cluster_config(assignments_yaml)
    assert "coder" in config.models
    assert config.models["coder"].source.type == "huggingface"
    assert config.models["coder"].disk_gb == 44.8
    assert "msm1" in config.nodes
    assert config.nodes["msm1"].host == "msm1-wifi.lan"
    assert config.nodes["msm1"].ip == "msm1-wifi.lan"
    assert config.nodes["msm1"].role == "node"
    assert "rock" in config.nodes
    assert config.nodes["rock"].role == "gateway"
    assert len(config.assignments["msm1"]) == 1
    assert config.assignments["msm1"][0].model == "coder"
    assert config.assignments["msm1"][0].port == 8000


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
        assignments:
          msm1:
            - model: coder
              port: 8000
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
        assignments:
          msm1:
            - model: coder
              port: 8000
    """)
    p = tmp_path / "node-assignments.yaml"
    p.write_text(content)
    with pytest.warns(DeprecationWarning, match="deprecated"):
        config = load_cluster_config(p)
    assert config.nodes["msm1"].role == "node"
    assert config.nodes["rock"].role == "gateway"


def test_node_resolved_fields_default_to_none(assignments_yaml: Path) -> None:
    """Resolved fields are None after initial load — populated later by pre-flight."""
    config = load_cluster_config(assignments_yaml)
    for node in config.nodes.values():
        assert node.platform is None
        assert node.shell is None
        assert node.home_dir is None
        assert node.homebrew_prefix is None


def test_parse_node_runtime_identity_defaults_to_omlx_model_dir(tmp_path: Path) -> None:
    """A v2 node can declare management host, fabric host, and node-level oMLX runtime."""
    content = dedent("""\
        models: {}
        nodes:
          msm3:
            host: msm3-wifi.lan
            fabric_host: msm3-fabric
            ram_gb: 128
            user: shag
            role: node
            runtime:
              type: omlx
              port: 8018
        assignments: {}
    """)
    p = tmp_path / "node-assignments.yaml"
    p.write_text(content)

    config = load_cluster_config(p)
    node = config.nodes["msm3"]

    assert node.host == "msm3-wifi.lan"
    assert node.fabric_host == "msm3-fabric"
    assert node.runtime is not None
    assert node.runtime.type == "omlx"
    assert node.runtime.port == 8018
    assert node.runtime.model_dir is None


def test_load_cluster_config_user_from_env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """GATEWAY_SSH_USER env var overrides default when no YAML user is set."""
    content = dedent("""\
        models:
          coder:
            source: { type: huggingface, repo: "test/coder" }
            disk_gb: 10
        nodes:
          msm1: { host: "msm1-wifi.lan", ram_gb: 128, role: node }
        assignments:
          msm1:
            - model: coder
              port: 8000
    """)
    p = tmp_path / "node-assignments.yaml"
    p.write_text(content)
    monkeypatch.setenv("GATEWAY_SSH_USER", "deploy_bot")
    config = load_cluster_config(p)
    assert config.nodes["msm1"].user == "deploy_bot"


def test_validate_memory_single_model_passes(assignments_yaml: Path) -> None:
    config = load_cluster_config(assignments_yaml)
    errors = validate_memory(config)
    assert errors == []


@pytest.fixture()
def overloaded_yaml(tmp_path: Path) -> Path:
    content = dedent("""\
        models:
          big_model:
            source: { type: huggingface, repo: "test/big" }
            disk_gb: 100
            kv_per_32k_gb: 30
            max_context: 32768

        nodes:
          msm1: { host: "msm1-wifi.lan", ram_gb: 128, user: "admin", role: node }
          rock: { host: "rock.lan", ram_gb: 32, user: "infra_user", role: gateway }

        assignments:
          msm1:
            - model: big_model
              port: 8000
    """)
    p = tmp_path / "node-assignments.yaml"
    p.write_text(content)
    return p


def test_validate_memory_overloaded_fails(overloaded_yaml: Path) -> None:
    config = load_cluster_config(overloaded_yaml)
    errors = validate_memory(config)
    assert len(errors) == 1
    assert "msm1" in errors[0]


@pytest.fixture()
def multi_model_yaml(tmp_path: Path) -> Path:
    content = dedent("""\
        models:
          coder:
            source: { type: huggingface, repo: "test/coder" }
            disk_gb: 44.8
            kv_per_32k_gb: 8
            max_context: 131072
          general:
            source: { type: huggingface, repo: "test/general" }
            disk_gb: 44.8
            kv_per_32k_gb: 8
            max_context: 131072

        nodes:
          msm1: { host: "msm1-wifi.lan", ram_gb: 128, user: "admin", role: node }
          rock: { host: "rock.lan", ram_gb: 32, user: "infra_user", role: gateway }

        assignments:
          msm1:
            - model: coder
              port: 8000
            - model: general
              port: 8001
    """)
    p = tmp_path / "node-assignments.yaml"
    p.write_text(content)
    return p


def test_validate_memory_multi_model_passes(multi_model_yaml: Path) -> None:
    config = load_cluster_config(multi_model_yaml)
    errors = validate_memory(config)
    assert errors == []


def test_validate_memory_uses_ram_gb_override(tmp_path: Path) -> None:
    content = dedent("""\
        models:
          video:
            source: { type: pip, package: "mlx-video" }
            disk_gb: 5
            ram_gb: 120
            max_context: 0
            serving: cli

        nodes:
          msm1: { host: "msm1-wifi.lan", ram_gb: 128, user: "admin", role: node }
          rock: { host: "rock.lan", ram_gb: 32, user: "infra_user", role: gateway }

        assignments:
          msm1:
            - model: video
              port: 8000
    """)
    p = tmp_path / "node-assignments.yaml"
    p.write_text(content)
    config = load_cluster_config(p)
    errors = validate_memory(config)
    assert errors == []


def test_generate_litellm_config_basic(assignments_yaml: Path) -> None:
    config = load_cluster_config(assignments_yaml)
    result = generate_litellm_config(config)
    parsed = yaml_lib.safe_load(result)
    assert result.startswith("# AUTO-GENERATED")
    assert len(parsed["model_list"]) == 1
    entry = parsed["model_list"][0]
    assert entry["model_name"] == "coder"
    assert entry["litellm_params"]["model"] == "openai/mlx-community/Qwen3-Coder-Next-4bit"
    assert entry["litellm_params"]["api_base"] == "http://msm1-wifi.lan:8000/v1"
    assert entry["litellm_params"]["api_key"] == "none"
    assert entry["litellm_params"]["max_input_tokens"] == 131072
    assert entry["litellm_params"]["max_output_tokens"] == 16384
    assert parsed["litellm_settings"]["callbacks"] == ["prometheus"]
    assert parsed["litellm_settings"]["use_chat_completions_url_for_anthropic_messages"] is True
    assert parsed["router_settings"]["routing_strategy"] == "least-busy"
    assert parsed["general_settings"]["master_key"] == "os.environ/LITELLM_MASTER_KEY"


def test_generate_litellm_config_multi_node(multi_model_yaml: Path) -> None:
    config = load_cluster_config(multi_model_yaml)
    result = generate_litellm_config(config)
    parsed = yaml_lib.safe_load(result)
    assert len(parsed["model_list"]) == 2
    names = {e["model_name"] for e in parsed["model_list"]}
    assert names == {"coder", "general"}


def test_generate_litellm_config_embedding_slot(tmp_path: Path) -> None:
    content = dedent("""\
        models:
          coder:
            source: { type: huggingface, repo: "test/coder" }
            disk_gb: 44.8
            kv_per_32k_gb: 8
            max_context: 131072
          embedding:
            source: { type: huggingface, repo: "test/embedding-model" }
            disk_gb: 0.5
            serving: embedding

        nodes:
          msm1: { host: "msm1-wifi.lan", ram_gb: 128, user: "admin", role: node }
          rock: { host: "rock.lan", ram_gb: 32, user: "infra_user", role: gateway }

        assignments:
          msm1:
            - model: coder
              port: 8000
              embedding: true
    """)
    p = tmp_path / "node-assignments.yaml"
    p.write_text(content)
    config = load_cluster_config(p)
    result = generate_litellm_config(config)
    parsed = yaml_lib.safe_load(result)
    assert len(parsed["model_list"]) == 2
    names = [e["model_name"] for e in parsed["model_list"]]
    assert "coder" in names
    assert "embedding" in names
    emb_entry = next(e for e in parsed["model_list"] if e["model_name"] == "embedding")
    assert emb_entry["litellm_params"]["model"] == "openai/test/embedding-model"
    assert emb_entry["litellm_params"]["api_base"] == "http://msm1-wifi.lan:8000/v1"


def test_generate_litellm_config_skips_cli_serving(tmp_path: Path) -> None:
    content = dedent("""\
        models:
          video:
            source: { type: pip, package: "mlx-video" }
            disk_gb: 5
            ram_gb: 20
            max_context: 0
            serving: cli

        nodes:
          msm1: { host: "msm1-wifi.lan", ram_gb: 128, user: "admin", role: node }
          rock: { host: "rock.lan", ram_gb: 32, user: "infra_user", role: gateway }

        assignments:
          msm1:
            - model: video
              port: 8000
    """)
    p = tmp_path / "node-assignments.yaml"
    p.write_text(content)
    config = load_cluster_config(p)
    result = generate_litellm_config(config)
    parsed = yaml_lib.safe_load(result)
    assert len(parsed["model_list"]) == 0


def test_check_config_sync_matches(assignments_yaml: Path, tmp_path: Path) -> None:
    config = load_cluster_config(assignments_yaml)
    generated = generate_litellm_config(config)
    committed = tmp_path / "litellm-config.yaml"
    committed.write_text(generated)
    assert check_config_sync(config, committed) is True


def test_check_config_sync_mismatch(assignments_yaml: Path, tmp_path: Path) -> None:
    config = load_cluster_config(assignments_yaml)
    committed = tmp_path / "litellm-config.yaml"
    committed.write_text("stale content")
    assert check_config_sync(config, committed) is False


def test_generate_litellm_config_litellm_params_override(tmp_path: Path) -> None:
    """litellm_params in model config overrides defaults and adds routing fields."""
    content = dedent("""\
        models:
          coder:
            source: { type: huggingface, repo: "test/coder" }
            disk_gb: 44.8
            max_context: 131072
            litellm_params:
              max_output_tokens: 32768
              timeout: 300
              stream_timeout: 600
              weight: 2
              tpm: 100000
              rpm: 100

        nodes:
          msm1: { host: "msm1-wifi.lan", ram_gb: 128, user: "admin", role: node }
          rock: { host: "rock.lan", ram_gb: 32, user: "infra_user", role: gateway }

        assignments:
          msm1:
            - model: coder
              port: 8000
    """)
    p = tmp_path / "node-assignments.yaml"
    p.write_text(content)
    config = load_cluster_config(p)
    result = generate_litellm_config(config)
    parsed = yaml_lib.safe_load(result)
    entry = parsed["model_list"][0]
    assert entry["litellm_params"]["max_output_tokens"] == 32768
    assert entry["litellm_params"]["max_input_tokens"] == 131072
    assert entry["litellm_params"]["timeout"] == 300
    assert entry["litellm_params"]["stream_timeout"] == 600
    assert entry["litellm_params"]["weight"] == 2
    assert entry["litellm_params"]["tpm"] == 100000
    assert entry["litellm_params"]["rpm"] == 100


def test_generate_litellm_config_litellm_params_partial(tmp_path: Path) -> None:
    """Only set litellm_params fields appear in output; unset fields are absent."""
    content = dedent("""\
        models:
          coder:
            source: { type: huggingface, repo: "test/coder" }
            disk_gb: 44.8
            max_context: 131072
            litellm_params:
              max_output_tokens: 65536

        nodes:
          msm1: { host: "msm1-wifi.lan", ram_gb: 128, user: "admin", role: node }
          rock: { host: "rock.lan", ram_gb: 32, user: "infra_user", role: gateway }

        assignments:
          msm1:
            - model: coder
              port: 8000
    """)
    p = tmp_path / "node-assignments.yaml"
    p.write_text(content)
    config = load_cluster_config(p)
    result = generate_litellm_config(config)
    parsed = yaml_lib.safe_load(result)
    entry = parsed["model_list"][0]
    assert entry["litellm_params"]["max_output_tokens"] == 65536
    assert "timeout" not in entry["litellm_params"]
    assert "rpm" not in entry["litellm_params"]


def test_load_cluster_config_loads_dotenv(assignments_yaml: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """load_cluster_config loads .env from repo root."""
    import thunder_forge.cluster.config as config_module

    monkeypatch.delenv("HF_HOME", raising=False)

    # Create .env next to configs/ (find_repo_root() will find this)
    repo_root = assignments_yaml.parent.parent
    (repo_root / ".git").mkdir(exist_ok=True)  # find_repo_root() needs a git marker
    dotenv_path = repo_root / ".env"
    dotenv_path.write_text("HF_HOME=/test/hf/cache\n")

    monkeypatch.setattr(config_module, "find_repo_root", lambda: repo_root)

    load_cluster_config(assignments_yaml)

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
        "assignments": {},
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
        "assignments": {},
    }
    config = parse_cluster_config(raw)
    assert config.models["coder"].model_info is None


def test_parse_litellm_params_new_fields() -> None:
    """New litellm_params fields (temperature, max_tokens, seed) are parsed."""
    raw = {
        "models": {
            "coder": {
                "source": {"type": "huggingface", "repo": "test/coder"},
                "disk_gb": 10,
                "litellm_params": {
                    "temperature": 0.7,
                    "max_tokens": 4096,
                    "seed": 42,
                },
            }
        },
        "nodes": {},
        "assignments": {},
    }
    config = parse_cluster_config(raw)
    lp = config.models["coder"].litellm_params
    assert lp is not None
    assert lp.temperature == 0.7
    assert lp.max_tokens == 4096
    assert lp.seed == 42


def test_generate_litellm_config_model_info(tmp_path: Path) -> None:
    """model_info section appears in generated config when set."""
    content = dedent("""\
        models:
          coder:
            source: { type: huggingface, repo: "test/coder" }
            disk_gb: 44.8
            max_context: 131072
            model_info:
              base_model: meta-llama/Llama-3-70b
              mode: chat
              input_cost_per_token: 0.000001
              output_cost_per_token: 0.000002
              supports_vision: true
              supports_function_calling: true

        nodes:
          msm1: { host: "msm1-wifi.lan", ram_gb: 128, user: "admin", role: node }
          rock: { host: "rock.lan", ram_gb: 32, user: "infra_user", role: gateway }

        assignments:
          msm1:
            - model: coder
              port: 8000
    """)
    p = tmp_path / "node-assignments.yaml"
    p.write_text(content)
    config = load_cluster_config(p)
    result = generate_litellm_config(config)
    parsed = yaml_lib.safe_load(result)
    entry = parsed["model_list"][0]
    assert "model_info" in entry
    assert entry["model_info"]["base_model"] == "meta-llama/Llama-3-70b"
    assert entry["model_info"]["mode"] == "chat"
    assert entry["model_info"]["input_cost_per_token"] == 0.000001
    assert entry["model_info"]["output_cost_per_token"] == 0.000002
    assert entry["model_info"]["supports_vision"] is True
    assert entry["model_info"]["supports_function_calling"] is True
    assert "supports_parallel_function_calling" not in entry["model_info"]
    assert "supports_response_schema" not in entry["model_info"]


def test_generate_litellm_config_no_model_info(tmp_path: Path) -> None:
    """model_info section is absent when not configured."""
    content = dedent("""\
        models:
          coder:
            source: { type: huggingface, repo: "test/coder" }
            disk_gb: 44.8
            max_context: 131072

        nodes:
          msm1: { host: "msm1-wifi.lan", ram_gb: 128, user: "admin", role: node }
          rock: { host: "rock.lan", ram_gb: 32, user: "infra_user", role: gateway }

        assignments:
          msm1:
            - model: coder
              port: 8000
    """)
    p = tmp_path / "node-assignments.yaml"
    p.write_text(content)
    config = load_cluster_config(p)
    result = generate_litellm_config(config)
    parsed = yaml_lib.safe_load(result)
    entry = parsed["model_list"][0]
    assert "model_info" not in entry


def test_generate_litellm_config_new_litellm_params(tmp_path: Path) -> None:
    """New litellm_params fields (temperature, max_tokens, seed) appear in generated config."""
    content = dedent("""\
        models:
          coder:
            source: { type: huggingface, repo: "test/coder" }
            disk_gb: 44.8
            max_context: 131072
            litellm_params:
              temperature: 0.7
              max_tokens: 4096
              seed: 42

        nodes:
          msm1: { host: "msm1-wifi.lan", ram_gb: 128, user: "admin", role: node }
          rock: { host: "rock.lan", ram_gb: 32, user: "infra_user", role: gateway }

        assignments:
          msm1:
            - model: coder
              port: 8000
    """)
    p = tmp_path / "node-assignments.yaml"
    p.write_text(content)
    config = load_cluster_config(p)
    result = generate_litellm_config(config)
    parsed = yaml_lib.safe_load(result)
    entry = parsed["model_list"][0]
    assert entry["litellm_params"]["temperature"] == 0.7
    assert entry["litellm_params"]["max_tokens"] == 4096
    assert entry["litellm_params"]["seed"] == 42
