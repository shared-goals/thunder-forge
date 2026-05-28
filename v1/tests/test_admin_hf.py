"""Tests for HuggingFace API integration (mocked)."""

import pytest


def test_parse_model_metadata_from_api_response():
    from thunder_admin.hf import parse_model_metadata

    api_response = {
        "siblings": [
            {"rfilename": "model-00001-of-00002.safetensors", "size": 5_000_000_000},
            {"rfilename": "model-00002-of-00002.safetensors", "size": 5_000_000_000},
            {"rfilename": "tokenizer_config.json", "size": 1000},
            {"rfilename": "config.json", "size": 2000},
        ],
        "sha": "abc123def",
    }
    config_json = {
        "max_position_embeddings": 131072,
        "num_key_value_heads": 8,
        "head_dim": 128,
        "num_hidden_layers": 32,
    }
    meta = parse_model_metadata(api_response, config_json)
    assert meta["disk_gb"] == pytest.approx(10.0, abs=0.1)
    assert meta["max_context"] == 131072
    assert meta["revision"] == "abc123def"
    assert meta["kv_per_32k_gb"] > 0
    assert meta["has_tokenizer"]
    assert meta["has_safetensors"]


def test_parse_model_metadata_no_safetensors():
    from thunder_admin.hf import parse_model_metadata

    api_response = {
        "siblings": [{"rfilename": "model.gguf", "size": 5_000_000_000}],
        "sha": "abc123",
    }
    meta = parse_model_metadata(api_response, {})
    assert not meta["has_safetensors"]
