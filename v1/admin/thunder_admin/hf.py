"""HuggingFace API integration for model metadata fetching."""

from __future__ import annotations

import os

import httpx

HF_API_BASE = "https://huggingface.co/api/models"
TIMEOUT = 15.0


def _get_headers() -> dict[str, str]:
    headers = {"Accept": "application/json"}
    token = os.environ.get("HF_TOKEN")
    if token:
        headers["Authorization"] = f"Bearer {token}"
    return headers


def fetch_model_info(repo: str) -> dict:
    """Fetch model info from HuggingFace API (with blob sizes)."""
    url = f"{HF_API_BASE}/{repo}?blobs=true"
    resp = httpx.get(url, headers=_get_headers(), timeout=TIMEOUT)
    resp.raise_for_status()
    return resp.json()


def fetch_config_json(repo: str, revision: str = "main") -> dict | None:
    """Fetch config.json from a HuggingFace model repo."""
    url = f"https://huggingface.co/{repo}/raw/{revision}/config.json"
    try:
        resp = httpx.get(url, headers=_get_headers(), timeout=TIMEOUT)
        resp.raise_for_status()
        return resp.json()
    except (httpx.HTTPStatusError, httpx.ConnectError, httpx.TimeoutException):
        return None


def parse_model_metadata(api_response: dict, config_json: dict) -> dict:
    """Extract useful metadata from HuggingFace API response and config.json."""
    siblings = api_response.get("siblings", [])

    safetensor_bytes = sum(s.get("size", 0) for s in siblings if s.get("rfilename", "").endswith(".safetensors"))
    disk_gb = safetensor_bytes / 1e9

    filenames = {s.get("rfilename", "") for s in siblings}
    has_safetensors = any(f.endswith(".safetensors") for f in filenames)
    has_tokenizer = "tokenizer_config.json" in filenames

    # VLM models (e.g. Qwen3.5) nest text config under "text_config"
    text_cfg = config_json.get("text_config", config_json)

    max_context = text_cfg.get("max_position_embeddings", 0)

    num_kv_heads = text_cfg.get("num_key_value_heads", 0)
    head_dim = text_cfg.get("head_dim", 0)
    num_layers = text_cfg.get("num_hidden_layers", 0)
    if num_kv_heads and head_dim and num_layers:
        kv_per_32k_gb = num_kv_heads * head_dim * num_layers * 2 * 2 * 32768 / 1e9
    else:
        kv_per_32k_gb = 0.0

    revision = api_response.get("sha", "main")

    return {
        "disk_gb": round(disk_gb, 1),
        "max_context": max_context,
        "kv_per_32k_gb": round(kv_per_32k_gb, 2),
        "revision": revision,
        "has_tokenizer": has_tokenizer,
        "has_safetensors": has_safetensors,
    }
