"""Tests for parse_cluster_config — raw dict parsing without file I/O."""

import pytest

from thunder_forge.cluster.config import parse_cluster_config


def test_parse_cluster_config_basic():
    """parse_cluster_config accepts a raw dict and returns a ClusterConfig."""
    raw = {
        "models": {
            "coder": {
                "source": {"type": "huggingface", "repo": "mlx-community/Qwen3-Coder-Next-4bit", "revision": "main"},
                "disk_gb": 44.8,
                "kv_per_32k_gb": 8,
                "max_context": 131072,
            }
        },
        "nodes": {
            "rock": {"host": "rock.lan", "ram_gb": 32, "user": "infra_user", "role": "gateway"},
            "msm1": {"host": "msm1-wifi.lan", "ram_gb": 128, "user": "admin", "role": "node"},
        },
        "assignments": {"msm1": [{"model": "coder", "port": 8000}]},
    }
    config = parse_cluster_config(raw)
    assert "coder" in config.models
    assert config.models["coder"].source.type == "huggingface"
    assert config.models["coder"].disk_gb == 44.8
    assert config.nodes["msm1"].user == "admin"
    assert config.nodes["rock"].role == "gateway"
    assert config.assignments["msm1"][0].model == "coder"


def test_parse_cluster_config_user_stored_as_is():
    """User field is stored as-is — no env var resolution."""
    raw = {
        "models": {},
        "nodes": {"n1": {"host": "n1.lan", "ram_gb": 64, "role": "node"}},
        "assignments": {},
    }
    config = parse_cluster_config(raw)
    assert config.nodes["n1"].user == ""


def test_parse_cluster_config_role_migration():
    """Deprecated role names are migrated."""
    raw = {
        "models": {},
        "nodes": {
            "n1": {"host": "n1.lan", "ram_gb": 64, "role": "inference"},
            "gw": {"host": "gw.lan", "ram_gb": 32, "role": "infra"},
        },
        "assignments": {},
    }
    with pytest.warns(DeprecationWarning, match="deprecated"):
        config = parse_cluster_config(raw)
    assert config.nodes["n1"].role == "node"
    assert config.nodes["gw"].role == "gateway"


def test_parse_cluster_config_external_endpoints():
    """External endpoints are parsed correctly."""
    raw = {
        "models": {},
        "nodes": {},
        "assignments": {},
        "external_endpoints": [
            {"model_name": "qwen3-30b", "api_base": "http://example.com/v1", "api_key_env": "MY_KEY"}
        ],
    }
    config = parse_cluster_config(raw)
    assert len(config.external_endpoints) == 1
    assert config.external_endpoints[0].model_name == "qwen3-30b"
    assert config.external_endpoints[0].api_key_env == "MY_KEY"


def test_parse_model_server_args_populated():
    """server_args dict in YAML becomes a ServerArgs dataclass."""
    raw = {
        "models": {
            "coder": {
                "source": {"type": "huggingface", "repo": "test/model"},
                "disk_gb": 10,
                "server_args": {
                    "decode_concurrency": 48,
                    "prompt_concurrency": 16,
                    "max_tokens": 8192,
                    "temp": 0.5,
                    "draft_model": "mlx-community/Qwen3-0.6B-4bit",
                    "num_draft_tokens": 5,
                },
            }
        },
        "nodes": {},
        "assignments": {},
    }
    config = parse_cluster_config(raw)
    sa = config.models["coder"].server_args
    assert sa is not None
    assert sa.decode_concurrency == 48
    assert sa.prompt_concurrency == 16
    assert sa.max_tokens == 8192
    assert sa.temp == 0.5
    assert sa.draft_model == "mlx-community/Qwen3-0.6B-4bit"
    assert sa.num_draft_tokens == 5


def test_parse_model_server_args_absent():
    """No server_args key in YAML → model.server_args is None."""
    raw = {
        "models": {
            "coder": {
                "source": {"type": "huggingface", "repo": "test/model"},
                "disk_gb": 10,
            }
        },
        "nodes": {},
        "assignments": {},
    }
    config = parse_cluster_config(raw)
    assert config.models["coder"].server_args is None


def test_parse_model_server_args_partial():
    """Partial server_args dict — unset fields are None."""
    raw = {
        "models": {
            "coder": {
                "source": {"type": "huggingface", "repo": "test/model"},
                "disk_gb": 10,
                "server_args": {"decode_concurrency": 64},
            }
        },
        "nodes": {},
        "assignments": {},
    }
    config = parse_cluster_config(raw)
    sa = config.models["coder"].server_args
    assert sa is not None
    assert sa.decode_concurrency == 64
    assert sa.prompt_concurrency is None
    assert sa.temp is None
    assert sa.draft_model is None
