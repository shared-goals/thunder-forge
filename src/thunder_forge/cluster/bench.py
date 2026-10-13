"""Model benchmarking for oMLX nodes.

Combines three data sources into one comparison report:

- HF model cards (params, activated params, KL-vs-bf16 quality index,
  context length, weight bytes) — no local test needed for card facts.
- omlx.ai published benchmarks for the node's chip (best PP/TG rows,
  proxied through the node's admin API).
- Local runs via the oMLX admin API: context-window benchmark (the real
  memory admission boundary on this node) and throughput benchmark
  (PP/TG tok/s on this node's hardware).

Embedding and reranker models assigned to the node are treated as
reserved: their weight budget is subtracted from the memory-guard
ceiling and they are never benchmarked.
"""

import json
import re
import time
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

import httpx

from .config import ClusterConfig, Model, Node
from .omlx import resolve_omlx_api_key

ADMIN_API_PREFIX = "/admin"  # the installed fleet mounts admin routes under /admin

HF_CARD_URL = "https://huggingface.co/{repo}/raw/main/README.md"
HF_CONFIG_URL = "https://huggingface.co/{repo}/raw/main/config.json"
HF_TREE_URL = "https://huggingface.co/api/models/{repo}/tree/main?recursive=true"

RESERVED_ALIASES = ("embedding", "reranker")

CONTEXT_BENCH_DEFAULT_TARGET = 65536
THROUGHPUT_DEFAULT_PROMPT_LENGTHS = (1024, 4096)
THROUGHPUT_DEFAULT_BATCH_SIZES = (2, 4)
THROUGHPUT_DEFAULT_GENERATION_LENGTH = 128
CONTEXT_POLL_INTERVAL_S = 5
THROUGHPUT_POLL_INTERVAL_S = 5
ACCURACY_POLL_INTERVAL_S = 15


class BenchError(RuntimeError):
    """Raised when a benchmark source is unavailable or a run fails."""


# ---------------------------------------------------------------------------
# HF model card data
# ---------------------------------------------------------------------------


@dataclass
class HfModelCard:
    repo: str
    weights_gb: float = 0.0
    total_params_b: float = 0.0
    activated_params_b: float = 0.0
    bits_per_weight: float = 0.0
    kl_code: float | None = None
    kl_novel_en: float | None = None
    context_tokens: int = 0
    card_available: bool = False

    @property
    def weights_display(self) -> str:
        if self.weights_gb > 0:
            return f"{self.weights_gb:.1f} GB"
        return "?"


def _fetch_hf_text(url: str, *, transport: httpx.BaseTransport | None = None, timeout: float = 20.0) -> str:
    with httpx.Client(timeout=timeout, transport=transport, trust_env=False) as client:
        response = client.get(url)
        response.raise_for_status()
        return response.text


def _parse_card_table_value(card: str, row_label: str) -> float | None:
    """Parse the updated-value column from a card comparison table row."""
    pattern = rf"\|\s*{re.escape(row_label)}\s*\|[^|]+\|\s*([\d.]+)\s*\|"
    match = re.search(pattern, card)
    if match:
        return float(match.group(1))
    return None


def parse_hf_model_card(repo: str, card_text: str) -> HfModelCard:
    """Extract the structured facts we rely on from an HF model card."""
    data = HfModelCard(repo=repo, card_available=True)

    activated = re.search(r"([\d.]+)\s*B parameters and ([\d.]+)\s*B activated", card_text)
    if activated:
        data.total_params_b = float(activated.group(1))
        data.activated_params_b = float(activated.group(2))
    total_row = re.search(r"\*\*Total\*\*\s*\|\s*\*\*([\d.]+)B\*\*", card_text)
    if total_row and data.total_params_b == 0.0:
        data.total_params_b = float(total_row.group(1))
    bpw = re.search(r"([\d.]+)\s*bits/weight", card_text)
    if bpw:
        data.bits_per_weight = float(bpw.group(1))
    data.kl_code = _parse_card_table_value(card_text, "Code (Python)")
    data.kl_novel_en = _parse_card_table_value(card_text, "Novel (English)")
    return data


def _parse_context_tokens(config_json: dict[str, Any]) -> int:
    for source in (config_json, config_json.get("text_config") or {}):
        value = source.get("max_position_embeddings")
        if isinstance(value, int) and value > 0:
            return value
    return 0


def fetch_hf_model_card(
    repo: str,
    *,
    transport: httpx.BaseTransport | None = None,
    with_context: bool = True,
) -> HfModelCard:
    """Fetch card facts for one HF repo. Raises BenchError when unavailable."""
    data = parse_hf_model_card(repo, _fetch_hf_text(HF_CARD_URL.format(repo=repo), transport=transport))
    try:
        tree = json.loads(_fetch_hf_text(HF_TREE_URL.format(repo=repo), transport=transport))
        data.weights_gb = sum(
            entry["size"] for entry in tree if entry.get("path", "").endswith(".safetensors")
        ) / 1e9
    except (httpx.HTTPError, ValueError, KeyError, TypeError):
        pass
    if with_context:
        try:
            config_json = json.loads(_fetch_hf_text(HF_CONFIG_URL.format(repo=repo), transport=transport))
            data.context_tokens = _parse_context_tokens(config_json)
        except (httpx.HTTPError, ValueError):
            pass
    return data


def resolve_repo_for_alias(model: Model | None, fallback: str) -> str:
    """Resolve the HF repo for a registry alias, falling back to a raw repo id."""
    if model is not None and model.source.repo.strip():
        return model.source.repo.strip()
    return fallback


def runtime_model_id_for_alias(model: Model | None, fallback: str) -> str:
    if model is not None and model.runtime_model_id.strip():
        return model.runtime_model_id.strip()
    return fallback.split("/")[-1]


# ---------------------------------------------------------------------------
# Node admin API client
# ---------------------------------------------------------------------------


def node_base_url(node: Node) -> str:
    if node.runtime is None:
        msg = f"node {node.host} has no runtime configured"
        raise BenchError(msg)
    return f"http://{node.host}:{node.runtime.port}"


def admin_request(
    node: Node,
    method: str,
    path: str,
    *,
    api_key: str | None = None,
    json_body: dict[str, Any] | None = None,
    transport: httpx.BaseTransport | None = None,
    timeout: float = 30.0,
) -> httpx.Response:
    """Call the node's keyed admin API."""
    resolved_key = resolve_omlx_api_key(api_key)
    headers = {"Authorization": f"Bearer {resolved_key}"} if resolved_key else {}
    with httpx.Client(
        base_url=node_base_url(node), timeout=timeout, transport=transport, trust_env=False, headers=headers
    ) as client:
        response = client.request(method, path, json=json_body)
        return response


def fetch_node_health(
    node: Node,
    *,
    api_key: str | None = None,
    transport: httpx.BaseTransport | None = None,
) -> dict[str, Any]:
    response = admin_request(node, "GET", "/health", api_key=api_key, transport=transport)
    response.raise_for_status()
    return response.json()


def fetch_device_info(
    node: Node,
    *,
    api_key: str | None = None,
    transport: httpx.BaseTransport | None = None,
) -> dict[str, Any]:
    response = admin_request(node, "GET", f"{ADMIN_API_PREFIX}/api/device-info", api_key=api_key, transport=transport)
    response.raise_for_status()
    return response.json()


def fetch_model_catalog(
    node: Node,
    *,
    api_key: str | None = None,
    transport: httpx.BaseTransport | None = None,
) -> list[dict[str, Any]]:
    """Node model catalog: id, display_name (owner/name), estimated_size, loaded, pinned."""
    response = admin_request(node, "GET", f"{ADMIN_API_PREFIX}/api/models", api_key=api_key, transport=transport)
    response.raise_for_status()
    models = response.json().get("models") or []
    return [entry for entry in models if isinstance(entry, dict)]


def resolve_catalog_entry(
    node: Node,
    model_id: str,
    *,
    api_key: str | None = None,
    transport: httpx.BaseTransport | None = None,
) -> dict[str, Any] | None:
    """Find a catalog entry by runtime model id (dir basename)."""
    for entry in fetch_model_catalog(node, api_key=api_key, transport=transport):
        if entry.get("id") == model_id:
            return entry
    return None


OMLX_AI_BEST_URL = "https://omlx.ai/api/benchmarks/best"


def fetch_omlx_ai_best(
    device: dict[str, Any],
    model_id: str,
    *,
    transport: httpx.BaseTransport | None = None,
    timeout: float = 20.0,
) -> dict[str, Any]:
    """Best published omlx.ai benchmarks for this chip and model, by PP and by TG.

    Queried directly from the management host: omlx.ai blocks some node
    networks (403), which made the node-side admin proxy return 502.
    """
    model_name = str(model_id).rstrip("/").split("/")[-1]
    params = {
        "chip": device.get("chip_name", ""),
        "variant": device.get("chip_variant", ""),
        "memory_gb": device.get("memory_gb", ""),
        "model": model_name,
        "context": 4096,
        "limit": 5,
    }
    groups: dict[str, list[dict[str, Any]]] = {}
    with httpx.Client(timeout=timeout, transport=transport, trust_env=False) as client:
        for sort in ("pp", "tg"):
            response = client.get(OMLX_AI_BEST_URL, params={**params, "sort": sort})
            response.raise_for_status()
            rows = response.json().get("results") or []
            groups[f"by_{sort}"] = [row for row in rows if isinstance(row, dict)]
    found = bool(groups["by_pp"] or groups["by_tg"])
    return {
        "found": found,
        "model_name": model_name,
        "by_pp": groups["by_pp"],
        "by_tg": groups["by_tg"],
    }


# ---------------------------------------------------------------------------
# Local benchmark runs
# ---------------------------------------------------------------------------


@dataclass
class ContextBenchResult:
    status: str = ""
    applied_tokens: int = 0
    verified_tokens: int = 0
    native_context_length: int = 0
    prefill_tps: float = 0.0
    duration_s: float = 0.0
    capped_by: str = ""
    raw: dict[str, Any] = field(default_factory=dict)


@dataclass
class ThroughputBenchResult:
    status: str = ""
    rows: list[dict[str, Any]] = field(default_factory=list)

    @property
    def best_tg_tps(self) -> float:
        """Best single-stream decode tok/s (comparable to a hand measurement)."""
        single = [
            float(row.get("gen_tps") or 0.0)
            for row in self.rows
            if row.get("test_type") == "single"
        ]
        if single:
            return max(single)
        return self.best_batch_tg_tps

    @property
    def best_batch_tg_tps(self) -> float:
        return max(
            (float(row.get("tg_tps") or 0.0) for row in self.rows if row.get("test_type") == "batch"),
            default=0.0,
        )

    @property
    def peak_memory_gb(self) -> float:
        peaks = [float(row.get("peak_memory_bytes") or 0.0) for row in self.rows]
        peaks += [float(row.get("system_metrics", {}).get("peak_memory_bytes") or 0.0) for row in self.rows]
        return max(peaks, default=0.0) / 1e9


def _raise_admin_error(response: httpx.Response, action: str) -> None:
    """Raise BenchError carrying the API's detail message, if any."""
    try:
        response.raise_for_status()
    except httpx.HTTPStatusError as exc:
        try:
            body = exc.response.json()
            detail = str(body.get("detail") or "")
        except (ValueError, AttributeError):
            detail = exc.response.text[:200]
        suffix = f": {detail}" if detail else ""
        msg = f"{action} failed with HTTP {exc.response.status_code}{suffix}"
        raise BenchError(msg) from exc


def _poll_until_terminal(
    node: Node,
    *,
    status_path: str,
    result_path: str,
    api_key: str | None,
    poll_interval: int,
    timeout_s: int,
    transport: httpx.BaseTransport | None = None,
    progress: Any = None,
    label: str,
    bench_id: str = "",
) -> dict[str, Any]:
    """Poll a benchmark status endpoint until no run is active, then fetch results."""
    deadline = time.monotonic() + timeout_s
    started_at = time.monotonic()
    last_progress = 0.0
    while True:
        payload = admin_request(node, "GET", status_path, api_key=api_key, transport=transport).json()
        running = bool(isinstance(payload, dict) and payload.get("running"))
        status_bench_id = str(payload.get("bench_id") or "") if isinstance(payload, dict) else ""
        if status_bench_id:
            bench_id = status_bench_id
        now = time.monotonic()
        if progress and now - last_progress >= 15:
            elapsed = int(now - started_at)
            progress(f"{label}: still running (bench_id={bench_id}, {elapsed}s elapsed)")
            last_progress = now
        if not running and bench_id:
            # Terminal: the run finished (or died) — fetch its results.
            break
        if now >= deadline:
            msg = f"{label}: timed out after {timeout_s}s"
            raise BenchError(msg)
        time.sleep(poll_interval)

    result_response = admin_request(
        node, "GET", result_path.format(bench_id=bench_id), api_key=api_key, transport=transport
    )
    result_response.raise_for_status()
    return result_response.json()


def run_context_benchmark(
    node: Node,
    model_id: str,
    *,
    api_key: str | None = None,
    target_tokens: int = CONTEXT_BENCH_DEFAULT_TARGET,
    timeout_s: int = 2400,
    transport: httpx.BaseTransport | None = None,
    progress: Any = None,
) -> ContextBenchResult:
    """Measure the node's real context admission boundary for one model."""
    response = admin_request(
        node,
        "POST",
        f"{ADMIN_API_PREFIX}/api/bench/context/start",
        api_key=api_key,
        json_body={"model_id": model_id, "target_tokens": target_tokens},
        transport=transport,
    )
    if response.status_code == 409:
        msg = "another benchmark is already running on the node"
        raise BenchError(msg)
    _raise_admin_error(response, "context bench start")
    bench_id = str(response.json().get("bench_id") or "")
    if progress:
        progress(f"context bench: started (bench_id={bench_id}, target={target_tokens})")

    payload = _poll_until_terminal(
        node,
        status_path=f"{ADMIN_API_PREFIX}/api/bench/context/active",
        result_path=f"{ADMIN_API_PREFIX}/api/bench/context/{{bench_id}}/results",
        api_key=api_key,
        poll_interval=CONTEXT_POLL_INTERVAL_S,
        timeout_s=timeout_s,
        transport=transport,
        progress=progress,
        label="context bench",
        bench_id=bench_id,
    )
    result = payload.get("result") or {}
    return ContextBenchResult(
        status=str(payload.get("status") or ""),
        applied_tokens=int(result.get("applied_tokens") or 0),
        verified_tokens=int(result.get("verified_tokens") or 0),
        native_context_length=int(result.get("native_context_length") or 0),
        prefill_tps=float(result.get("prefill_tps") or 0.0),
        duration_s=float(result.get("duration_s") or 0.0),
        capped_by=str(result.get("capped_by") or ""),
        raw=result,
    )


def run_throughput_benchmark(
    node: Node,
    model_id: str,
    *,
    api_key: str | None = None,
    prompt_lengths: tuple[int, ...] = THROUGHPUT_DEFAULT_PROMPT_LENGTHS,
    batch_sizes: tuple[int, ...] = THROUGHPUT_DEFAULT_BATCH_SIZES,
    generation_length: int = THROUGHPUT_DEFAULT_GENERATION_LENGTH,
    timeout_s: int = 1800,
    transport: httpx.BaseTransport | None = None,
    progress: Any = None,
) -> ThroughputBenchResult:
    """Measure PP/TG throughput on the node's hardware."""
    response = admin_request(
        node,
        "POST",
        f"{ADMIN_API_PREFIX}/api/bench/start",
        api_key=api_key,
        json_body={
            "model_id": model_id,
            "prompt_lengths": sorted(prompt_lengths),
            "batch_sizes": sorted(batch_sizes),
            "generation_length": generation_length,
            "context_profile": "code_python",
        },
        transport=transport,
    )
    if response.status_code == 409:
        msg = "another benchmark is already running on the node"
        raise BenchError(msg)
    _raise_admin_error(response, "throughput bench start")
    bench_id = str(response.json().get("bench_id") or "")
    if progress:
        progress(f"throughput bench: started (bench_id={bench_id})")

    payload = _poll_until_terminal(
        node,
        status_path=f"{ADMIN_API_PREFIX}/api/bench/active",
        result_path=f"{ADMIN_API_PREFIX}/api/bench/{{bench_id}}/results",
        api_key=api_key,
        poll_interval=THROUGHPUT_POLL_INTERVAL_S,
        timeout_s=timeout_s,
        transport=transport,
        progress=progress,
        label="throughput bench",
        bench_id=bench_id,
    )
    rows_raw = payload.get("results")
    rows: list[dict[str, Any]] = []
    if isinstance(rows_raw, dict):
        rows = [value for value in rows_raw.values() if isinstance(value, dict)]
    elif isinstance(rows_raw, list):
        rows = [value for value in rows_raw if isinstance(value, dict)]
    return ThroughputBenchResult(status=str(payload.get("status") or ""), rows=rows)


def run_accuracy_benchmark(
    node: Node,
    model_id: str,
    *,
    api_key: str | None = None,
    benchmarks: dict[str, int],
    timeout_s: int = 3600,
    transport: httpx.BaseTransport | None = None,
    progress: Any = None,
) -> list[dict[str, Any]]:
    """Queue accuracy benchmarks (intelligence/coder indexes) and wait for results."""
    response = admin_request(
        node,
        "POST",
        f"{ADMIN_API_PREFIX}/api/bench/accuracy/queue/add",
        api_key=api_key,
        json_body={
            "model_id": model_id,
            "benchmarks": benchmarks,
            "batch_size": 1,
            "sampling_profile": "deterministic",
        },
    )
    if response.status_code == 409:
        msg = "another benchmark is already running on the node"
        raise BenchError(msg)
    _raise_admin_error(response, "accuracy bench queue")
    if progress:
        progress(f"accuracy bench: queued {sorted(benchmarks)}")

    deadline = time.monotonic() + timeout_s
    last_progress = 0.0
    while True:
        status_path = f"{ADMIN_API_PREFIX}/api/bench/accuracy/queue/status"
        status = admin_request(node, "GET", status_path, api_key=api_key).json()
        running = bool(status.get("running") or status.get("queue_running"))
        if not running:
            break
        now = time.monotonic()
        if progress and now - last_progress >= 60:
            current = status.get("current_model") or model_id
            progress(f"accuracy bench: running for {current}")
            last_progress = now
        if time.monotonic() >= deadline:
            msg = f"accuracy bench: timed out after {timeout_s}s"
            raise BenchError(msg)
        time.sleep(ACCURACY_POLL_INTERVAL_S)

    results = admin_request(node, "GET", f"{ADMIN_API_PREFIX}/api/bench/accuracy/results", api_key=api_key).json()
    entries = results.get("results") if isinstance(results, dict) else results
    if not isinstance(entries, list):
        return []
    return [entry for entry in entries if isinstance(entry, dict) and entry.get("model_id") == model_id]


# ---------------------------------------------------------------------------
# Report
# ---------------------------------------------------------------------------


@dataclass
class ModelRow:
    label: str
    repo: str
    card: HfModelCard | None
    assigned_as: str
    local_context_tokens: int = 0
    local_pp_tps: float = 0.0
    local_tg_tps: float = 0.0
    is_candidate: bool = False
    is_current: bool = False


def compute_bench_fit(
    rows: list[ModelRow],
    reserved: list[tuple[str, float]],
    co_resident: list[tuple[str, float]],
    ceiling_gb: float,
) -> dict[str, Any] | None:
    """Memory fit for the candidate: all-resident (replacement) + universal.

    None when the candidate's weight size is unknown (card fetch failed).
    """
    candidate_row = next((row for row in rows if row.is_candidate), None)
    if candidate_row is None or candidate_row.card is None or candidate_row.card.weights_gb <= 0:
        return None
    reserved_gb = sum(gb for _, gb in reserved)
    others_gb = sum(gb for _, gb in co_resident)
    total = candidate_row.card.weights_gb + reserved_gb + others_gb
    steady = candidate_row.card.weights_gb + reserved_gb
    return {
        "all_resident": {
            "candidate_gb": candidate_row.card.weights_gb,
            "reserved_gb": reserved_gb,
            "others_gb": others_gb,
            "total_gb": total,
            "headroom_gb": ceiling_gb - total,
            "verdict": "OK" if ceiling_gb - total > 0 else "OVER BUDGET",
        },
        "universal": {
            "total_gb": steady,
            "headroom_gb": ceiling_gb - steady,
        },
    }


def format_bench_report(
    *,
    node_id: str,
    node: Node,
    ceiling_gb: float,
    chip: str,
    reserved: list[tuple[str, float]],
    co_resident: list[tuple[str, float]],
    rows: list[ModelRow],
    candidate_repo: str,
    compare_alias: str,
    omlx_ai: dict[str, Any] | None,
    context_result: ContextBenchResult | None,
    throughput_result: ThroughputBenchResult | None,
    accuracy_entries: list[dict[str, Any]] | None,
) -> list[str]:
    lines: list[str] = []
    lines.append(f"== Bench: {node_id} ({node.host}) ==")
    lines.append(f"  chip: {chip or '?'}  ram: {node.ram_gb} GB  guard ceiling: {ceiling_gb:.1f} GB")
    if reserved:
        lines.append("  reserved (untouched): " + ", ".join(f"{alias} {gb:.1f} GB" for alias, gb in reserved))
    else:
        lines.append("  reserved (untouched): none")
    co_labels = ", ".join(f"{alias} {gb:.1f} GB" for alias, gb in co_resident) or "none"
    lines.append(f"  co-resident (LRU-swappable): {co_labels}")

    for row in rows:
        card = row.card
        kind = "candidate" if row.is_candidate else "current model" if row.is_current else "assigned"
        lines.append(f"-- {kind}: {row.repo} [{row.assigned_as}] --")
        if card is None or not card.card_available:
            lines.append("  card: unavailable")
            continue
        params = ""
        if card.total_params_b:
            params = f"{card.total_params_b:g}B params"
            if card.activated_params_b:
                params += f", {card.activated_params_b:g}B activated"
        bits = f", {card.bits_per_weight:g} bpw" if card.bits_per_weight else ""
        if params or bits:
            lines.append(f"  card: {params}{bits}")
        kl_parts = []
        if card.kl_code is not None:
            kl_parts.append(f"code {card.kl_code}")
        if card.kl_novel_en is not None:
            kl_parts.append(f"novel-en {card.kl_novel_en}")
        if kl_parts:
            lines.append("  KL vs bf16 (lower is better): " + ", ".join(kl_parts))
        context = f"{card.context_tokens}" if card.context_tokens else "?"
        lines.append(f"  weights: {card.weights_display}  context (card): {context}")

    if omlx_ai is not None:
        found = bool(omlx_ai.get("found"))
        lines.append("-- omlx.ai published benchmarks for this chip --")
        if not found:
            lines.append("  none for this chip+model")
        else:
            for sort_key in ("by_pp", "by_tg"):
                entries = omlx_ai.get(sort_key) or []
                if entries:
                    lines.append(f"  {sort_key}: {len(entries)} rows (best settings available on node)")

    if context_result is not None:
        lines.append("-- Local: context benchmark --")
        if context_result.applied_tokens:
            lines.append(
                f"  applied={context_result.applied_tokens} verified={context_result.verified_tokens} "
                f"prefill={context_result.prefill_tps:.0f} tok/s ({context_result.duration_s:.0f}s)"
            )
        else:
            lines.append(f"  failed: status={context_result.status or 'unknown'}")

    if throughput_result is not None:
        lines.append("-- Local: throughput benchmark --")
        if throughput_result.rows:
            for row in throughput_result.rows:
                if row.get("test_type") == "single":
                    lines.append(
                        f"  single pp={row.get('pp')}: prefill={row.get('processing_tps')} tok/s "
                        f"tg={row.get('gen_tps')} tok/s ttft={row.get('ttft_ms')} ms"
                    )
                else:
                    lines.append(
                        f"  batch={row.get('batch_size', '?')} (pp1024): "
                        f"pp={row.get('pp_tps', '?')} tok/s tg={row.get('tg_tps', '?')} tok/s "
                        f"ttft={row.get('avg_ttft_ms', '?')} ms"
                    )
            lines.append(f"  peak memory: {throughput_result.peak_memory_gb:.1f} GB")
        else:
            lines.append(f"  failed: status={throughput_result.status or 'unknown'}")

    if accuracy_entries:
        lines.append("-- Local: accuracy (deterministic) --")
        for entry in accuracy_entries:
            summary = entry.get("summary") or entry.get("results") or entry
            lines.append(f"  {json.dumps(summary, sort_keys=True)[:300]}")

    lines.append(f"-- Fit on {node_id} (ceiling {ceiling_gb:.1f} GB) --")
    header = f"  {'model':<44} {'weights':>9} {'ctx card':>9} {'ctx local':>9} {'pp/s':>8} {'tg/s':>8} {'KL code':>8}"
    lines.append(header)
    for row in rows:
        card = row.card
        weights = card.weights_display if card else "?"
        ctx_card = str(card.context_tokens) if card and card.context_tokens else "?"
        ctx_local = str(row.local_context_tokens) if row.local_context_tokens else "-"
        pp = f"{row.local_pp_tps:.0f}" if row.local_pp_tps else "-"
        tg = f"{row.local_tg_tps:.0f}" if row.local_tg_tps else "-"
        kl = f"{card.kl_code:g}" if card and card.kl_code is not None else "-"
        marker = f"  <- candidate for {compare_alias}" if row.is_candidate else "  (current)" if row.is_current else ""
        lines.append(f"  {row.repo:<44} {weights:>9} {ctx_card:>9} {ctx_local:>9} {pp:>8} {tg:>8} {kl:>8}{marker}")

    fit = compute_bench_fit(rows, reserved, co_resident, ceiling_gb)
    if fit is not None:
        all_resident = fit["all_resident"]
        lines.append(
            f"  all-resident fit if candidate replaces current {compare_alias}: "
            f"candidate {all_resident['candidate_gb']:.1f} "
            f"+ reserved {all_resident['reserved_gb']:.1f} + others "
            f"{all_resident['others_gb']:.1f} = {all_resident['total_gb']:.1f} GB "
            f"vs ceiling {ceiling_gb:.1f} GB -> {all_resident['verdict']} "
            f"(headroom {all_resident['headroom_gb']:.1f} GB)"
        )
        universal = fit["universal"]
        lines.append(
            f"  universal fit (candidate alone serves agent+memory, reserved stays): "
            f"{universal['total_gb']:.1f} GB "
            f"-> headroom {universal['headroom_gb']:.1f} GB for KV cache"
        )
    return lines


# ---------------------------------------------------------------------------
# Persistent reports
# ---------------------------------------------------------------------------

BENCH_SCHEMA = "tf-bench/1"


def build_report_payload(
    *,
    node_id: str,
    node: Node,
    ceiling_gb: float,
    chip: str,
    compare_alias: str,
    candidate_repo: str,
    candidate_model_id: str,
    reserved: list[tuple[str, float]],
    co_resident: list[tuple[str, float]],
    rows: list[ModelRow],
    omlx_ai: dict[str, Any] | None,
    context_result: ContextBenchResult | None,
    throughput_result: ThroughputBenchResult | None,
    accuracy_entries: list[dict[str, Any]] | None,
    timestamp: str,
) -> dict[str, Any]:
    """Structured benchmark payload for reports/bench/*.json."""

    def card_payload(card: HfModelCard | None) -> dict[str, Any] | None:
        if card is None:
            return None
        return {
            "available": card.card_available,
            "weights_gb": card.weights_gb,
            "total_params_b": card.total_params_b,
            "activated_params_b": card.activated_params_b,
            "bits_per_weight": card.bits_per_weight,
            "kl_code": card.kl_code,
            "kl_novel_en": card.kl_novel_en,
            "context_tokens": card.context_tokens,
        }

    rows_payload = []
    for row in rows:
        rows_payload.append(
            {
                "repo": row.repo,
                "slot": row.assigned_as,
                "kind": "candidate" if row.is_candidate else "current" if row.is_current else "assigned",
                "card": card_payload(row.card),
                "local": {
                    "context_tokens": row.local_context_tokens or None,
                    "pp_tps": row.local_pp_tps or None,
                    "tg_tps": row.local_tg_tps or None,
                },
            }
        )

    fit = compute_bench_fit(rows, reserved, co_resident, ceiling_gb)

    return {
        "schema": BENCH_SCHEMA,
        "generated_at": timestamp,
        "node": {"id": node_id, "host": node.host, "ram_gb": node.ram_gb, "ceiling_gb": ceiling_gb, "chip": chip},
        "candidate": {
            "repo": candidate_repo,
            "model_id": candidate_model_id,
            "slot": compare_alias,
        },
        "reserved": [{"alias": alias, "weights_gb": gb} for alias, gb in reserved],
        "co_resident": [{"alias": alias, "weights_gb": gb} for alias, gb in co_resident],
        "rows": rows_payload,
        "omlx_ai": omlx_ai,
        "local": {
            "context": context_result.raw if context_result is not None and context_result.raw else None,
            "context_applied_tokens": context_result.applied_tokens if context_result is not None else None,
            "throughput": {
                "rows": throughput_result.rows,
                "best_single_tg_tps": throughput_result.best_tg_tps or None,
                "best_batch_tg_tps": throughput_result.best_batch_tg_tps or None,
                "peak_memory_gb": throughput_result.peak_memory_gb or None,
            }
            if throughput_result is not None
            else None,
            "accuracy": accuracy_entries,
        },
        "fit": fit or {"all_resident": None, "universal": None},
    }


def load_latest_bench_summary(reports_dir: Path, repo: str) -> tuple[Path, dict[str, Any]] | None:
    """Latest saved bench report whose candidate repo matches `repo`."""
    if not reports_dir.is_dir():
        return None
    best: tuple[str, Path, dict[str, Any]] | None = None
    for path in sorted(reports_dir.glob("*.json")):
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (ValueError, OSError):
            continue
        if not isinstance(payload, dict) or payload.get("schema") != BENCH_SCHEMA:
            continue
        if (payload.get("candidate") or {}).get("repo") != repo:
            continue
        generated = str(payload.get("generated_at") or "")
        key = (generated, path.name)
        if best is None or key > best[0]:
            best = (key, path, payload)
    if best is None:
        return None
    return best[1], best[2]


def format_previous_comparison(
    previous_path: Path,
    previous_payload: dict[str, Any],
    current_payload: dict[str, Any],
) -> list[str]:
    """Human-readable delta lines between the previous bench of the current model and this run."""

    def metric(payload: dict[str, Any], name: str) -> float | None:
        local = (payload.get("local") or {}).get("throughput") or {}
        return local.get(name)

    lines: list[str] = []
    previous_repo = (previous_payload.get("candidate") or {}).get("repo")
    previous_date = str(previous_payload.get("generated_at") or "?")
    lines.append(
        f"-- vs previous bench of {previous_repo} ({previous_path.name}, {previous_date}) --"
    )
    comparisons = [
        ("single tg/s", "best_single_tg_tps", "tok/s"),
        ("batch tg/s", "best_batch_tg_tps", "tok/s"),
        ("peak memory", "peak_memory_gb", "GB"),
    ]
    for label, key, unit in comparisons:
        previous_value = metric(previous_payload, key)
        current_value = metric(current_payload, key)
        if previous_value and current_value:
            delta = (current_value - previous_value) / previous_value * 100.0
            lines.append(f"  {label}: {previous_value:.1f} -> {current_value:.1f} {unit} ({delta:+.1f}%)")
    previous_ctx = (previous_payload.get("local") or {}).get("context_applied_tokens")
    current_ctx = (current_payload.get("local") or {}).get("context_applied_tokens")
    if previous_ctx and current_ctx:
        lines.append(f"  context applied: {previous_ctx} -> {current_ctx} tokens")
    return lines


def save_bench_report(
    reports_dir: Path,
    node_id: str,
    model_alias: str,
    timestamp: str,
    payload: dict[str, Any],
) -> Path:
    """Write the structured benchmark report as reports_dir/<stem>.json."""
    model_slug = re.sub(r"[^A-Za-z0-9._-]+", "-", model_alias)
    stem = f"bench-{node_id}-{model_slug}-{timestamp}"
    reports_dir.mkdir(parents=True, exist_ok=True)
    json_path = reports_dir / f"{stem}.json"
    json_path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    return json_path


# ---------------------------------------------------------------------------
# Command entry point
# ---------------------------------------------------------------------------


def _model_weights(model: Model | None, card: HfModelCard | None) -> float:
    """Prefer the measured HF-tree weight; tfconfig disk_gb is the fallback."""
    if card is not None and card.weights_gb > 0:
        return card.weights_gb
    if model is not None and model.disk_gb:
        return float(model.disk_gb)
    return 0.0


def collect_model_rows(
    config: ClusterConfig,
    node_id: str,
    candidate_alias: str,
    *,
    compare_alias: str = "agent",
    candidate_repo: str | None = None,
    transport: httpx.BaseTransport | None = None,
    progress: Any = None,
) -> tuple[list[ModelRow], list[tuple[str, float]], list[tuple[str, float]]]:
    """Fetch HF cards for the candidate, the compare-alias current model, and other assigned models.

    The compare alias is the slot the candidate would take over (default:
    agent). Its current model is shown as the baseline row and is excluded
    from the fit math (it gets replaced). Returns (rows, reserved,
    co_resident) — co_resident lists only the models that stay after the
    replacement.
    """
    node = config.nodes[node_id]
    candidate_model = config.models.get(candidate_alias)
    if candidate_repo is None:
        candidate_repo = resolve_repo_for_alias(candidate_model, candidate_alias)
    rows: list[ModelRow] = []
    reserved: list[tuple[str, float]] = []
    co_resident: list[tuple[str, float]] = []

    repos: list[tuple[str, str, str, bool, bool]] = []  # (repo, label, assigned_as, is_candidate, is_current)
    repos.append((candidate_repo, candidate_repo, candidate_alias, True, False))

    compare_model = config.models.get(compare_alias)
    compare_repo = resolve_repo_for_alias(compare_model, compare_alias)
    if compare_repo != candidate_repo:
        # The candidate is not itself the current model of the slot: show the baseline.
        repos.append((compare_repo, compare_repo, f"current {compare_alias}", False, True))

    for alias in node.models:
        if alias == compare_alias:
            # Replaced by the candidate — represented by the baseline row above.
            continue
        if alias in RESERVED_ALIASES:
            model = config.models.get(alias)
            weights = float(model.disk_gb) if model is not None and model.disk_gb else 0.0
            reserved.append((alias, weights))
            continue
        model = config.models.get(alias)
        repo = resolve_repo_for_alias(model, alias)
        if repo == candidate_repo:
            continue
        repos.append((repo, repo, alias, False, False))

    for repo, label, assigned_as, is_candidate, is_current in repos:
        try:
            card = fetch_hf_model_card(repo, transport=transport)
        except (httpx.HTTPError, ValueError):
            card = HfModelCard(repo=repo, card_available=False)
        rows.append(
            ModelRow(
                label=label,
                repo=repo,
                card=card,
                assigned_as=assigned_as,
                is_candidate=is_candidate,
                is_current=is_current,
            )
        )
        if progress:
            progress(f"card: {repo} ({'candidate' if is_candidate else 'current' if is_current else 'assigned'})")
        if is_candidate or is_current:
            # The candidate's weights are added directly in the fit math; the
            # current model is replaced by the candidate.
            continue
        model = config.models.get(assigned_as)
        weights = _model_weights(model, card)
        co_resident.append((assigned_as, weights))

    return rows, reserved, co_resident


@dataclass
class BenchWorkflowResult:
    report_lines: list[str]
    payload: dict[str, Any]
    report_path: Path


def run_bench_workflow(
    *,
    config: ClusterConfig,
    node_id: str,
    node: Node,
    repo_root: Path,
    model_alias: str,
    compare_alias: str = "agent",
    api_key: str | None = None,
    skip_local: bool = False,
    run_context: bool = False,
    skip_throughput: bool = False,
    target_context: int = CONTEXT_BENCH_DEFAULT_TARGET,
    generation_length: int = THROUGHPUT_DEFAULT_GENERATION_LENGTH,
    prompt_lengths: tuple[int, ...] = THROUGHPUT_DEFAULT_PROMPT_LENGTHS,
    batch_sizes: tuple[int, ...] = THROUGHPUT_DEFAULT_BATCH_SIZES,
    accuracy: dict[str, int] | None = None,
    timeout_context: int = 2400,
    timeout_throughput: int = 1800,
    progress: Any = None,
) -> tuple[list[str], Path]:
    """Full bench flow: cards, omlx.ai, optional local runs, report file.

    Raises BenchError on unreachable admin API or failed benchmark runs.
    Returns (report lines, saved JSON report path).
    """
    candidate_model = config.models.get(model_alias)
    candidate_repo = resolve_repo_for_alias(candidate_model, model_alias)
    candidate_model_id = runtime_model_id_for_alias(candidate_model, model_alias)

    try:
        health = fetch_node_health(node, api_key=api_key)
        device = fetch_device_info(node, api_key=api_key)
    except (httpx.HTTPError, BenchError) as exc:
        msg = f"oMLX admin API on {node.host} is unreachable: {exc}"
        raise BenchError(msg) from exc
    ceiling_gb = float(health.get("final_ceiling") or 0) / 1e9
    if ceiling_gb <= 0:
        ceiling_gb = float(node.ram_gb) - 8

    # Bare model names resolve against the node catalog (display_name carries the owner).
    catalog_entry: dict[str, Any] | None = None
    try:
        catalog_entry = resolve_catalog_entry(node, candidate_model_id, api_key=api_key)
    except httpx.HTTPError:
        pass
    if catalog_entry and catalog_entry.get("display_name"):
        candidate_repo = str(catalog_entry["display_name"])

    if progress:
        progress(f"node: chip={device.get('chip_name', '?')} memory={device.get('memory_gb', '?')} GB")
        progress(f"card+assigned data for {candidate_repo}")

    rows, reserved, co_resident = collect_model_rows(
        config,
        node_id,
        model_alias,
        compare_alias=compare_alias,
        candidate_repo=candidate_repo,
        progress=progress,
    )

    candidate_weights = float(candidate_model.disk_gb or 0) if candidate_model else 0.0
    if candidate_weights <= 0 and catalog_entry:
        candidate_weights = float(catalog_entry.get("estimated_size") or 0) / 1e9
    if candidate_weights > 0 and candidate_weights >= 0.5 * ceiling_gb and progress:
        progress(
            f"note: candidate weights are {candidate_weights:.1f} GB — model load takes minutes "
            "and the KV boundary will be small; consider skipping the context benchmark"
        )

    omlx_ai: dict[str, Any] | None = None
    try:
        omlx_ai = fetch_omlx_ai_best(device, candidate_model_id)
    except (httpx.HTTPError, BenchError) as exc:
        if progress:
            progress(f"omlx.ai: unavailable ({exc})")

    context_result = None
    throughput_result = None
    accuracy_entries = None
    if not skip_local and not (not run_context and skip_throughput and not accuracy):
        if progress:
            progress("local benchmarks (note: they unload/reload models on the node)")
        if run_context:
            context_result = run_context_benchmark(
                node,
                candidate_model_id,
                api_key=api_key,
                target_tokens=target_context,
                timeout_s=timeout_context,
                progress=progress,
            )
        if not skip_throughput:
            throughput_result = run_throughput_benchmark(
                node,
                candidate_model_id,
                api_key=api_key,
                prompt_lengths=prompt_lengths,
                batch_sizes=batch_sizes,
                generation_length=generation_length,
                timeout_s=timeout_throughput,
                progress=progress,
            )
        if accuracy:
            accuracy_entries = run_accuracy_benchmark(
                node,
                candidate_model_id,
                api_key=api_key,
                benchmarks=accuracy,
                progress=progress,
            )

    for row in rows:
        if row.is_candidate:
            row.local_context_tokens = context_result.applied_tokens if context_result else 0
            row.local_pp_tps = (
                max((float(r.get("pp_tps") or 0.0) for r in throughput_result.rows), default=0.0)
                if throughput_result
                else 0.0
            )
            row.local_tg_tps = throughput_result.best_tg_tps if throughput_result else 0.0

    chip = f"{device.get('chip_name', '')} {device.get('chip_variant', '')}".strip()
    timestamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    report_lines = format_bench_report(
        node_id=node_id,
        node=node,
        ceiling_gb=ceiling_gb,
        chip=chip,
        compare_alias=compare_alias,
        reserved=reserved,
        co_resident=co_resident,
        rows=rows,
        candidate_repo=candidate_repo,
        omlx_ai=omlx_ai,
        context_result=context_result,
        throughput_result=throughput_result,
        accuracy_entries=accuracy_entries,
    )

    payload = build_report_payload(
        node_id=node_id,
        node=node,
        ceiling_gb=ceiling_gb,
        chip=chip,
        compare_alias=compare_alias,
        candidate_repo=candidate_repo,
        candidate_model_id=candidate_model_id,
        reserved=reserved,
        co_resident=co_resident,
        rows=rows,
        omlx_ai=omlx_ai,
        context_result=context_result,
        throughput_result=throughput_result,
        accuracy_entries=accuracy_entries,
        timestamp=timestamp,
    )

    # Compare against the latest bench of the slot's current model.
    current_model = config.models.get(compare_alias)
    current_repo = resolve_repo_for_alias(current_model, compare_alias)
    previous = load_latest_bench_summary(repo_root / "reports" / "bench", current_repo)
    if previous is not None and previous[1].get("candidate", {}).get("repo") != candidate_repo:
        comparison_lines = format_previous_comparison(previous[0], previous[1], payload)
        report_lines.extend(comparison_lines)
        payload["previous_comparison"] = {"report": str(previous[0]), "lines": comparison_lines}
    elif current_repo != candidate_repo and progress:
        progress(
            f"previous bench: none found for {current_repo}; "
            f"baseline it with: make bench {node_id} {current_repo} {compare_alias}"
        )

    report_path = save_bench_report(repo_root / "reports" / "bench", node_id, model_alias, timestamp, payload)
    return report_lines, report_path
