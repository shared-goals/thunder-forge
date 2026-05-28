"""Tests for admin config JSONB<->YAML serialization and roundtrip."""

import yaml

from thunder_forge.cluster.config import parse_cluster_config, validate_memory


def test_jsonb_to_yaml_key_order():
    from thunder_admin.config import jsonb_to_yaml

    config_json = {
        "models": {
            "coder": {
                "source": {"type": "huggingface", "repo": "mlx-community/Qwen3-Coder", "revision": "main"},
                "disk_gb": 44.8,
                "ram_gb": None,
                "kv_per_32k_gb": 8,
                "max_context": 131072,
                "extra_args": None,
                "notes": "test",
                "serving": "",
            }
        },
        "nodes": {"msm1": {"ip": "192.168.1.101", "ram_gb": 128, "role": "node", "user": "admin"}},
        "assignments": {"msm1": [{"model": "coder", "port": 8000, "embedding": False}]},
        "external_endpoints": [],
    }
    yaml_str = jsonb_to_yaml(config_json)
    parsed = yaml.safe_load(yaml_str)
    assert parsed is not None
    lines = yaml_str.strip().split("\n")
    top_keys = [line.split(":")[0] for line in lines if not line.startswith(" ") and ":" in line]
    assert top_keys == ["models", "nodes", "assignments", "external_endpoints"]


def test_jsonb_to_yaml_preserves_null_fields():
    from thunder_admin.config import jsonb_to_yaml

    config_json = {
        "models": {
            "test": {
                "source": {"type": "huggingface", "repo": "test/model"},
                "disk_gb": 10,
                "extra_args": None,
                "notes": "",
                "serving": "",
            }
        },
        "nodes": {},
        "assignments": {},
        "external_endpoints": [],
    }
    yaml_str = jsonb_to_yaml(config_json)
    parsed = yaml.safe_load(yaml_str)
    assert parsed["models"]["test"]["extra_args"] is None
    assert parsed["models"]["test"]["notes"] == ""


def test_roundtrip_jsonb_yaml_cli():
    from thunder_admin.config import jsonb_to_yaml

    config_json = {
        "models": {
            "coder": {
                "source": {"type": "huggingface", "repo": "mlx-community/Qwen3-Coder", "revision": "main"},
                "disk_gb": 44.8,
                "kv_per_32k_gb": 8,
                "max_context": 131072,
            }
        },
        "nodes": {
            "rock": {"ip": "192.168.1.61", "ram_gb": 32, "role": "gateway", "user": "infra_user"},
            "msm1": {"ip": "192.168.1.101", "ram_gb": 128, "role": "node", "user": "admin"},
        },
        "assignments": {"msm1": [{"model": "coder", "port": 8000}]},
        "external_endpoints": [],
    }
    yaml_str = jsonb_to_yaml(config_json)
    parsed_raw = yaml.safe_load(yaml_str)
    admin_config = parse_cluster_config(parsed_raw)
    errors = validate_memory(admin_config)
    assert errors == []
    assert "coder" in admin_config.models
    assert admin_config.models["coder"].source.repo == "mlx-community/Qwen3-Coder"
    assert admin_config.nodes["msm1"].user == "admin"


def test_validate_config_catches_missing_model():
    from thunder_admin.config import validate_config

    config_json = {
        "models": {},
        "nodes": {"n1": {"ip": "1.2.3.4", "ram_gb": 128, "role": "node", "user": "admin"}},
        "assignments": {"n1": [{"model": "nonexistent", "port": 8000}]},
        "external_endpoints": [],
    }
    errors = validate_config(config_json)
    assert any("nonexistent" in e for e in errors)


def test_validate_config_catches_missing_node():
    from thunder_admin.config import validate_config

    config_json = {
        "models": {"m1": {"source": {"type": "huggingface", "repo": "test"}, "disk_gb": 10}},
        "nodes": {},
        "assignments": {"missing_node": [{"model": "m1", "port": 8000}]},
        "external_endpoints": [],
    }
    errors = validate_config(config_json)
    assert any("missing_node" in e for e in errors)


def test_roundtrip_admin_and_cli_equivalence(tmp_path):
    """Admin JSONB->YAML->parse and CLI load_cluster_config produce equivalent configs."""
    from unittest.mock import patch

    from thunder_admin.config import jsonb_to_yaml

    import thunder_forge.cluster.config as config_module
    from thunder_forge.cluster.config import load_cluster_config, parse_cluster_config, validate_memory

    config_json = {
        "models": {
            "coder": {
                "source": {"type": "huggingface", "repo": "mlx-community/Qwen3-Coder-Next-4bit", "revision": "main"},
                "disk_gb": 44.8,
                "kv_per_32k_gb": 8,
                "max_context": 131072,
            },
            "fast": {
                "source": {"type": "huggingface", "repo": "mlx-community/Qwen3.5-9B", "revision": "main"},
                "disk_gb": 5.6,
                "kv_per_32k_gb": 0.3,
                "max_context": 131072,
            },
        },
        "nodes": {
            "rock": {"ip": "192.168.1.61", "ram_gb": 32, "role": "gateway", "user": "infra_user"},
            "msm1": {"ip": "192.168.1.101", "ram_gb": 128, "role": "node", "user": "admin"},
        },
        "assignments": {
            "msm1": [
                {"model": "coder", "port": 8000, "embedding": False},
                {"model": "fast", "port": 8001},
            ]
        },
        "external_endpoints": [
            {"model_name": "qwen3-30b", "api_base": "http://example.com/v1", "api_key_env": "MY_KEY"}
        ],
    }

    # Path 1: Admin JSONB -> YAML -> parse_cluster_config
    yaml_str = jsonb_to_yaml(config_json)
    parsed_raw = yaml.safe_load(yaml_str)
    admin_config = parse_cluster_config(parsed_raw)

    # Path 2: Write YAML to file -> load_cluster_config
    yaml_path = tmp_path / "node-assignments.yaml"
    yaml_path.write_text(yaml_str)

    with patch.object(config_module, "find_repo_root", return_value=tmp_path):
        cli_config = load_cluster_config(yaml_path)

    # Compare models
    assert set(admin_config.models.keys()) == set(cli_config.models.keys())
    for name in admin_config.models:
        assert admin_config.models[name].source.repo == cli_config.models[name].source.repo
        assert admin_config.models[name].disk_gb == cli_config.models[name].disk_gb

    # Compare nodes
    assert set(admin_config.nodes.keys()) == set(cli_config.nodes.keys())
    for name in admin_config.nodes:
        assert admin_config.nodes[name].ip == cli_config.nodes[name].ip
        assert admin_config.nodes[name].ram_gb == cli_config.nodes[name].ram_gb
        assert admin_config.nodes[name].role == cli_config.nodes[name].role

    # Memory validation should agree
    admin_errors = validate_memory(admin_config)
    cli_errors = validate_memory(cli_config)
    assert admin_errors == cli_errors == []


def test_validate_config_catches_duplicate_ports():
    from thunder_admin.config import validate_config

    config_json = {
        "models": {
            "m1": {"source": {"type": "huggingface", "repo": "test/a"}, "disk_gb": 10},
            "m2": {"source": {"type": "huggingface", "repo": "test/b"}, "disk_gb": 10},
        },
        "nodes": {"n1": {"ip": "1.2.3.4", "ram_gb": 128, "role": "node", "user": "admin"}},
        "assignments": {"n1": [{"model": "m1", "port": 8000}, {"model": "m2", "port": 8000}]},
        "external_endpoints": [],
    }
    errors = validate_config(config_json)
    assert any("duplicate" in e.lower() or "port" in e.lower() for e in errors)
