"""Tests for oMLX model benchmarking (bench module and CLI plumbing)."""

import json
from pathlib import Path
from textwrap import dedent
from types import SimpleNamespace

import httpx
import pytest
from typer.testing import CliRunner

from thunder_forge.cli import app
from thunder_forge.cluster.bench import (
    ContextBenchResult,
    ModelRow,
    ThroughputBenchResult,
    _parse_context_tokens,
    collect_model_rows,
    fetch_hf_model_card,
    format_bench_report,
    parse_hf_model_card,
)
from thunder_forge.cluster.config import Model, ModelSource

runner = CliRunner()

FLASH_NEXT_CARD = dedent(
    """\
    # Qwen3.8-Flash-Next-oQ4e-mtp

    A mixed-precision MLX checkpoint of [Qwen/Qwen3.8-Flash-Next](https://example.com),
    an experimental preview: a mixture-of-experts vision-language model with
    125B parameters and 6B activated, plus a 51B n-gram embedding and a 4B MTP module.

    The effective average is 4.724 bits/weight for the complete checkpoint.

    ## KL Divergence from the Original Model

    | Corpus | Previous oQ4e | Updated oQ4e | Change |
    |---|---|---|---|
    | Code (Python) | 0.0877 | 0.0776 | -11.6% |
    | Novel (English) | 0.1909 | 0.1713 | -10.2% |

    | Storage format | Logical weights | Share of total | Tensor storage | Effective bits/weight |
    |---|---|---|---|---|
    | **Total** | **180.000B** | **100%** | **98.994 GiB** | **4.724** |
    """
)

FLASH_NEXT_CONFIG = {
    "model_type": "qwen4_exp",
    "text_config": {"max_position_embeddings": 262144, "num_experts": 256},
}


def _mock_hf_transport(*, card: str, tree: list[dict], config: dict) -> httpx.MockTransport:
    def handler(request: httpx.Request) -> httpx.Response:
        url = str(request.url)
        if url.endswith("/raw/main/README.md"):
            return httpx.Response(200, text=card)
        if "tree/main" in url:
            return httpx.Response(200, json=tree)
        if url.endswith("/raw/main/config.json"):
            return httpx.Response(200, json=config)
        return httpx.Response(404)

    return httpx.MockTransport(handler)


def test_parse_hf_model_card_extracts_facts() -> None:
    card = parse_hf_model_card("Jundot/Qwen3.8-Flash-Next-oQ4e-mtp", FLASH_NEXT_CARD)

    assert card.card_available is True
    assert card.total_params_b == 125.0
    assert card.activated_params_b == 6.0
    assert card.bits_per_weight == 4.724
    assert card.kl_code == 0.0776
    assert card.kl_novel_en == 0.1713


def test_parse_hf_model_card_without_tables_is_available_but_empty() -> None:
    card = parse_hf_model_card("some/model", "# plain card\nno tables here\n")
    assert card.card_available is True
    assert card.total_params_b == 0.0
    assert card.kl_code is None


def test_parse_context_tokens_reads_text_config() -> None:
    assert _parse_context_tokens(FLASH_NEXT_CONFIG) == 262144
    assert _parse_context_tokens({"max_position_embeddings": 131072}) == 131072
    assert _parse_context_tokens({}) == 0


def test_fetch_hf_model_card_combines_card_tree_and_config() -> None:
    transport = _mock_hf_transport(
        card=FLASH_NEXT_CARD,
        tree=[
            {"path": "model.safetensors", "size": 50_000_000_000},
            {"path": "model-00002.safetensors", "size": 49_000_000_000},
            {"path": "tokenizer.json", "size": 1_000_000},
        ],
        config=FLASH_NEXT_CONFIG,
    )

    card = fetch_hf_model_card("Jundot/Qwen3.8-Flash-Next-oQ4e-mtp", transport=transport)

    assert card.weights_gb == pytest.approx(99.0)
    assert card.context_tokens == 262144
    assert card.activated_params_b == 6.0


def test_format_bench_report_fit_math() -> None:
    candidate_card = parse_hf_model_card("cand/model", FLASH_NEXT_CARD)
    candidate_card.weights_gb = 99.0  # normally populated from the HF tree API
    assigned_card = parse_hf_model_card("assigned/model", "# plain\n")
    rows = [
        ModelRow(
            label="candidate",
            repo="cand/model",
            card=candidate_card,
            assigned_as="agent",
            is_candidate=True,
            local_context_tokens=131072,
            local_pp_tps=1500.0,
            local_tg_tps=90.0,
        ),
        ModelRow(label="memory", repo="assigned/model", card=assigned_card, assigned_as="memory"),
    ]

    lines = format_bench_report(
        node_id="msm1",
        node=SimpleNamespace(host="msm1-wifi.lan", ram_gb=128),
        ceiling_gb=116.9,
        chip="Apple M3 Max",
        compare_alias="agent",
        reserved=[("embedding", 1.1), ("reranker", 2.1)],
        co_resident=[("memory", 13.0)],
        rows=rows,
        candidate_repo="cand/model",
        omlx_ai=None,
        context_result=None,
        throughput_result=None,
        accuracy_entries=None,
    )

    text = "\n".join(lines)
    assert "guard ceiling: 116.9 GB" in text
    assert "reserved (untouched): embedding 1.1 GB, reranker 2.1 GB" in text
    assert "co-resident (LRU-swappable): memory 13.0 GB" in text
    assert "125B params, 6B activated, 4.724 bpw" in text
    assert "KL vs bf16 (lower is better): code 0.0776, novel-en 0.1713" in text
    assert "<- candidate for agent" in text
    assert "all-resident fit if candidate replaces current agent: candidate 99.0 + reserved 3.2 + others 13.0" in text
    assert "universal fit (candidate alone serves agent+memory, reserved stays): 102.2 GB" in text
    assert "headroom 14.7 GB for KV cache" in text


def test_collect_model_rows_splits_reserved_and_coresident() -> None:
    import thunder_forge.cluster.config as config_module

    config = config_module.ClusterConfig()
    config.models = {
        "agent": Model(
            source=ModelSource(type="huggingface", repo="Jundot/Qwen3.6-35B-A3B-oQ8e-mtp"),
            runtime_model_id="Qwen3.6-35B-A3B-oQ8e-mtp",
            disk_gb=36.0,
        ),
        "memory": Model(
            source=ModelSource(type="huggingface", repo="mlx-community/gpt-oss-20b-mxfp4-bf16"),
            runtime_model_id="gpt-oss-20b-mxfp4-bf16",
            disk_gb=13.0,
        ),
        "reranker": Model(
            source=ModelSource(type="huggingface", repo="BAAI/bge-reranker-v2-m3"),
            runtime_model_id="bge-reranker-v2-m3",
            disk_gb=2.1,
        ),
    }
    config.nodes["msm1"] = config_module.Node(
        host="msm1-wifi.lan",
        ram_gb=128,
        user="shag",
        roles=["inference"],
        runtime=config_module.NodeRuntime(type="omlx", port=8018, trusted_network=True),
        models=["agent", "agent-better", "memory", "reranker", "embedding"],
    )
    config.models["agent-better"] = Model(
        source=ModelSource(type="huggingface", repo="Jundot/Qwen3.6-35B-A3B-oQ8e-mtp"),
        runtime_model_id="Qwen3.6-35B-A3B-oQ8e-mtp",
        disk_gb=36.0,
    )

    transport = _mock_hf_transport(card="# empty\n", tree=[], config={})

    rows, reserved, co_resident = collect_model_rows(config, "msm1", "agent", transport=transport)

    # Reserved aliases (embedding, reranker) are budget-only and never become rows;
    # an assigned alias with the same repo as the candidate replaces itself (no dupe).
    assert [row.repo for row in rows] == [
        "Jundot/Qwen3.6-35B-A3B-oQ8e-mtp",
        "mlx-community/gpt-oss-20b-mxfp4-bf16",
    ]
    assert rows[0].is_candidate is True
    assert ("reranker", 2.1) in reserved
    assert ("embedding", 0.0) in reserved  # not in registry; falls back to card weight (0)
    assert ("memory", 13.0) in co_resident


def test_run_context_benchmark_polls_until_terminal() -> None:
    import thunder_forge.cluster.bench as bench_mod

    calls: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(f"{request.method} {request.url.path}")
        assert request.headers.get("authorization") == "Bearer key-1"
        if request.method == "POST" and request.url.path == "/admin/api/bench/context/start":
            return httpx.Response(200, json={"bench_id": "ctx-1"})
        if request.url.path == "/admin/api/bench/context/active":
            if calls.count("GET /admin/api/bench/context/active") == 1:
                return httpx.Response(200, json={"running": True, "bench_id": "ctx-1"})
            return httpx.Response(200, json={"running": False, "bench_id": "ctx-1"})
        if request.url.path == "/admin/api/bench/context/ctx-1/results":
            return httpx.Response(
                200,
                json={
                    "status": "completed",
                    "result": {
                        "applied_tokens": 131072,
                        "verified_tokens": 131072,
                        "native_context_length": 262144,
                        "prefill_tps": 1450.5,
                        "duration_s": 320.0,
                    },
                },
            )
        return httpx.Response(404)

    node = _make_node()
    result = bench_mod.run_context_benchmark(
        node,
        "Qwen3.6-35B-A3B-oQ8e-mtp",
        api_key="key-1",
        transport=httpx.MockTransport(handler),
    )

    assert result.applied_tokens == 131072
    assert result.native_context_length == 262144
    assert result.prefill_tps == pytest.approx(1450.5)
    assert "POST /admin/api/bench/context/start" in calls
    assert "GET /admin/api/bench/context/ctx-1/results" in calls


def _make_node():
    from thunder_forge.cluster.config import Node, NodeRuntime

    return Node(
        host="msm1-wifi.lan",
        ram_gb=128,
        user="shag",
        roles=["inference"],
        runtime=NodeRuntime(type="omlx", port=8018, trusted_network=True),
        models=["agent"],
    )


def test_throughput_result_aggregates_rows() -> None:
    result = ThroughputBenchResult(
        status="completed",
        rows=[
            {"test_type": "single", "pp": 1024, "gen_tps": 152.5},
            {"test_type": "batch", "pp_tps": 900.0, "tg_tps": 80.0, "peak_memory_bytes": 40e9},
            {"test_type": "batch", "pp_tps": 1200.0, "tg_tps": 95.0, "peak_memory_bytes": 42e9},
        ],
    )
    assert result.best_tg_tps == pytest.approx(152.5)
    assert result.best_batch_tg_tps == pytest.approx(95.0)
    assert result.peak_memory_gb == pytest.approx(42.0)


def test_save_bench_report_writes_json_only(tmp_path: Path) -> None:
    import thunder_forge.cluster.bench as bench_mod

    payload = {
        "schema": bench_mod.BENCH_SCHEMA,
        "generated_at": "20261013-160256",
        "candidate": {"repo": "mlx-community/gpt-oss-20b-mxfp4-bf16", "slot": "memory"},
        "local": {"throughput": {"best_single_tg_tps": 102.0, "best_batch_tg_tps": 249.2, "peak_memory_gb": 53.4}},
    }

    path = bench_mod.save_bench_report(tmp_path, "msm1", "gpt-oss-20b-mxfp4-bf16", "20261013-160256", payload)

    assert path.name == "bench-msm1-gpt-oss-20b-mxfp4-bf16-20261013-160256.json"
    assert path.exists()
    assert not (tmp_path / "bench-msm1-gpt-oss-20b-mxfp4-bf16-20261013-160256.txt").exists()
    loaded = json.loads(path.read_text())
    assert loaded["local"]["throughput"]["best_single_tg_tps"] == 102.0


def test_load_latest_bench_summary_picks_newest_match(tmp_path: Path) -> None:
    import thunder_forge.cluster.bench as bench_mod

    def write(stem: str, repo: str, generated: str, tg: float) -> None:
        (tmp_path / f"{stem}.json").write_text(
            json.dumps(
                {
                    "schema": bench_mod.BENCH_SCHEMA,
                    "generated_at": generated,
                    "candidate": {"repo": repo, "slot": "agent"},
                    "local": {"throughput": {"best_single_tg_tps": tg}},
                }
            )
        )

    write("bench-msm1-a-20261013-120000", "mlx-community/mxfp8", "20261013-120000", 96.0)
    write("bench-msm1-a-20261013-150000", "mlx-community/mxfp8", "20261013-150000", 98.0)
    write("bench-msm1-b-20261013-160000", "Jundot/oQ8e", "20261013-160000", 155.0)
    (tmp_path / "garbage.json").write_text("not json at all")

    found = bench_mod.load_latest_bench_summary(tmp_path, "mlx-community/mxfp8")

    assert found is not None
    path, payload = found
    assert path.name == "bench-msm1-a-20261013-150000.json"
    assert payload["local"]["throughput"]["best_single_tg_tps"] == 98.0
    assert bench_mod.load_latest_bench_summary(tmp_path, "unknown/repo") is None


def test_format_previous_comparison_shows_deltas(tmp_path: Path) -> None:
    import thunder_forge.cluster.bench as bench_mod

    previous = {
        "candidate": {"repo": "mlx-community/mxfp8"},
        "generated_at": "20261013-120000",
        "local": {
            "throughput": {"best_single_tg_tps": 98.0, "best_batch_tg_tps": 233.0, "peak_memory_gb": 78.0},
            "context_applied_tokens": 65536,
        },
    }
    current = {
        "candidate": {"repo": "Jundot/oQ8e"},
        "generated_at": "20261013-160000",
        "local": {
            "throughput": {"best_single_tg_tps": 155.0, "best_batch_tg_tps": 270.0, "peak_memory_gb": 81.1},
            "context_applied_tokens": 65536,
        },
    }

    lines = bench_mod.format_previous_comparison(tmp_path / "prev.json", previous, current)

    text = "\n".join(lines)
    assert "-- vs previous bench of mlx-community/mxfp8" in text
    assert "single tg/s: 98.0 -> 155.0 tok/s (+58.2%)" in text
    assert "batch tg/s: 233.0 -> 270.0 tok/s (+15.9%)" in text
    assert "peak memory: 78.0 -> 81.1 GB (+4.0%)" in text
    assert "context applied: 65536 -> 65536 tokens" in text


def test_cli_bench_unknown_node_fails_fast(tmp_path: Path, monkeypatch) -> None:
    import thunder_forge.cli as cli_module
    import thunder_forge.cluster.config as config_module

    repo = tmp_path
    (repo / "tfconfig.yaml").write_text(
        dedent(
            """\
            models: {}
            nodes:
              msm1:
                host: msm1-wifi.lan
                ram_gb: 128
                roles: [inference]
                user: shag
                runtime:
                  type: omlx
            """
        )
    )
    monkeypatch.setattr(config_module, "find_repo_root", lambda: repo)
    monkeypatch.setattr(cli_module, "_load_repo_dotenv", lambda: (repo, repo / ".env"))
    monkeypatch.delenv("OMLX_API_KEY", raising=False)

    result = runner.invoke(app, ["cluster", "bench", "nosuch", "agent"])

    assert result.exit_code == 1
    assert "not defined in tfconfig.yaml" in result.stderr


def test_cli_bench_full_flow_with_mocks(tmp_path: Path, monkeypatch) -> None:
    import thunder_forge.cli as cli_module
    import thunder_forge.cluster.bench as bench_mod
    import thunder_forge.cluster.config as config_module

    repo = tmp_path
    (repo / "tfconfig.yaml").write_text(
        dedent(
            """\
            models:
              agent:
                source:
                  repo: "Jundot/Qwen3.6-35B-A3B-oQ8e-mtp"
                runtime_model_id: Qwen3.6-35B-A3B-oQ8e-mtp
                disk_gb: 36.0
                max_context: 262144
              memory:
                source:
                  repo: "mlx-community/gpt-oss-20b-mxfp4-bf16"
                runtime_model_id: gpt-oss-20b-mxfp4-bf16
                disk_gb: 13.0
            nodes:
              msm1:
                host: msm1-wifi.lan
                ram_gb: 128
                roles: [inference]
                user: shag
                runtime:
                  type: omlx
                  trusted_network: true
                models:
                  - agent
                  - memory
                  - embedding
                  - reranker
            """
        )
    )
    monkeypatch.setattr(config_module, "find_repo_root", lambda: repo)
    monkeypatch.setattr(cli_module, "_load_repo_dotenv", lambda: (repo, repo / ".env"))
    monkeypatch.setenv("OMLX_API_KEY", "key-9")

    monkeypatch.setattr(
        bench_mod,
        "fetch_node_health",
        lambda node, **kwargs: {"final_ceiling": 116.9e9},
    )
    monkeypatch.setattr(
        bench_mod,
        "fetch_device_info",
        lambda node, **kwargs: {"chip_name": "Apple M3 Max", "chip_variant": "", "memory_gb": 128},
    )

    def fake_collect(config, node_id, alias, **kwargs):
        card = parse_hf_model_card("x", FLASH_NEXT_CARD)
        card.weights_gb = 36.0  # normally populated from the HF tree API
        rows = [
            ModelRow(
                label=alias,
                repo="Jundot/Qwen3.6-35B-A3B-oQ8e-mtp",
                card=card,
                assigned_as=alias,
                is_candidate=True,
            )
        ]
        reserved = [("embedding", 1.1), ("reranker", 2.1)]
        co_resident = [("memory", 13.0)]
        return rows, reserved, co_resident

    monkeypatch.setattr(bench_mod, "collect_model_rows", fake_collect)
    monkeypatch.setattr(
        bench_mod,
        "fetch_omlx_ai_best",
        lambda device, model_id, **kwargs: {"found": False},
    )
    monkeypatch.setattr(
        bench_mod,
        "run_context_benchmark",
        lambda node, model_id, **kwargs: ContextBenchResult(
            status="completed",
            applied_tokens=131072,
            verified_tokens=131072,
            prefill_tps=1450.0,
            duration_s=300.0,
        ),
    )
    monkeypatch.setattr(
        bench_mod,
        "run_throughput_benchmark",
        lambda node, model_id, **kwargs: ThroughputBenchResult(
            status="completed",
            rows=[{"prompt_length": 4096, "batch_size": 4, "pp_tps": 1200.0, "tg_tps": 95.0}],
        ),
    )

    result = runner.invoke(
        app, ["cluster", "bench", "msm1", "agent", "--context", "--target-context", "131072"]
    )

    assert result.exit_code == 0, result.stdout
    assert "Bench: msm1 (msm1-wifi.lan)" in result.stdout
    assert "chip: Apple M3 Max" in result.stdout
    assert "125B params, 6B activated" in result.stdout
    assert "applied=131072" in result.stdout
    assert "tg=95.0 tok/s" in result.stdout
    assert "<- candidate for agent" in result.stdout
    assert "for KV cache" in result.stdout
    assert "report: saved to" in result.stdout
    assert ".json" in result.stdout
    assert "status: bench complete" in result.stdout
