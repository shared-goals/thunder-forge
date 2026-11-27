## Problem

`usage collect-node-metrics` recorded every runtime entry the oMLX status endpoint reported, while `GET /status` filtered them. On 2026-11-27 node snapshots carried `MarkItDown` — a tool adapter, not a model of this cluster — in `hot_loaded_models` for 5539 of 5546 samples, always with `actual_size: 0`. Three consequences, all visible in the real report: `hot_loaded_count` counted a tool, `msm1` looked like it had four loaded models while the report itself listed two, and any hot-load or memory aggregation built on these rows was off by one entry per node.

## Change

- `_collect_node_metric_snapshots` maps hot-loaded runtime ids with `include_unmanaged=False`, the same call `GET /status` already makes.
- `loaded_models` entries now require a configured alias; a runtime id without one is skipped instead of being written as a raw runtime id. `hot_loaded_count` is therefore the count of cluster models.
- `docs/operations/daily-summary-contract.md` states the rule: only models declared in the cluster configuration appear in node snapshots.

## Verification

- `uv run pytest -q` → 431 passed (430 on `main` + 1 new).
- `uv run ruff check .` → clean.
- New test `tests/test_cli_usage.py::test_usage_collect_node_metrics_skips_unmanaged_runtime_entries` feeds a status payload with `MarkItDown` beside `agent` and asserts `hot_loaded_models == ["agent"]`, `hot_loaded_count == 1`, and one `loaded_models` entry.
- `test_usage_collect_node_metrics_writes_snapshot_jsonl` previously relied on the unfiltered behaviour (its config declared no alias for the runtime id it asserted); it now declares one, so it still tests the snapshot shape rather than the filtering gap.

## Limitations

Historical snapshots keep the polluted rows; the fix applies from the first collector run after deploy. `usage report` already filtered the tool out of its own output, so the report shape does not change — only the raw JSONL and the counts derived from it. The `infer-03` count-without-list row in `usage.py` is not addressed here; it belongs to the report PR.
