# Daily Summary Contract

**Status:** Implemented (MVP)  
**Last Updated:** 2026-06-02  
**Owner:** Thunder Forge

## Overview

Thunder Forge daily usage reporting is file-backed and CLI-first:

- request events: `logs/tf-edge-access.jsonl`
- node snapshots: `logs/tf-node-metrics.jsonl`
- node snapshots are collected by `thunder-forge edge serve` in-process every 60s by default
- summary command: `thunder-forge usage report`
- shared retention command: `thunder-forge usage trim-logs`
- ad hoc SQL: DuckDB CLI over JSONL

This keeps operations simple and avoids a metrics backend while still supporting daily rollups by user, node, model, and hour.

## Implemented Commands

```bash
uv run thunder-forge usage collect-node-metrics
uv run thunder-forge usage collect-node-metrics --continuous --interval-seconds 60
uv run thunder-forge edge serve --metrics-interval-seconds 60
uv run thunder-forge usage report --period 2026-06-02
uv run thunder-forge usage report --period 2026-06-02 --json
uv run thunder-forge usage trim-logs

# Make wrappers
make usage 2026-06-02
make usage-json 2026-06-02
```

## Data Model

### Request Event JSONL

Source: TF edge access log.

Expected keys:

- `timestamp`
- `request_id`
- `client_id` (tf user identity)
- `path`
- `model`
- `status_code`
- `latency_ms`
- `olla_endpoint`
- optional token fields: `prompt_tokens`, `completion_tokens`, `total_tokens`

### Node Snapshot JSONL

Source: `usage collect-node-metrics` (one row per inference node per run).

Expected keys:

- `timestamp`
- `node_id`
- `health_ok`
- `models_ok`
- `status_ok`
- `hot_loaded_models` (status entries where `loaded=true`, ordered by latest access)
- `hot_loaded_count`
- `errors`

## Summary Contract

`usage report --json` returns a summary with:

- `requests.total`
- `requests.by_user`
- `requests.by_node`
- `requests.by_node_model`
- `requests.by_node_hour` (0-23 buckets)
- `requests.by_model`
- `requests.by_hour`
- `consumed_ms.total`
- `consumed_ms.by_user`
- `consumed_ms.by_node`
- `consumed_ms.by_model`
- `tokens.by_user` (when available)
- `tokens.by_node` (when available)
- `tokens.by_model` (when available)
- `invalid_lines`

`consumed_ms` is defined as sum of edge-observed `latency_ms`.

## Retention Contract

- Config key: `services.log_retention_days`
- Default: `3`
- Scope: local TF-managed logs
  - `logs/tf-edge-access.jsonl`
  - `logs/tf-node-metrics.jsonl`
  - `logs/olla.log`
  - `logs/*.stdout.log`, `logs/*.stderr.log`, `logs/*.log.gz`
- Behavior:
  - JSONL files are trimmed by record `timestamp`
  - plain/archive logs are pruned by file mtime

## DuckDB CLI Workflow

Run parameterized daily SQL directly on JSONL:

```bash
duckdb -readonly -cmd ".mode table" -cmd ".headers on" \
  -cmd ".param set period 2026-06-02" \
  -f docs/operations/daily-usage-duckdb.sql

# or
make usage-duckdb 2026-06-02
```

The SQL script emits daily tables for:

- by user: requests, consumed time, tokens
- by node: requests, consumed time, tokens
- by node/model: request split
- by node/hour: request split
- by model: requests, consumed time, tokens
- node hot-loaded summary from collected snapshots

## Guardrails

- no PostgreSQL, no queue, no dashboard for MVP
- edge traffic is the request source of truth for now
- token fields are optional and consumed only when present
- hot-loaded model sets come from oMLX model status snapshots

## Evolution

1. keep file-backed JSONL + CLI until operator pain appears
2. enrich token coverage when upstream usage fields are reliable
3. only then evaluate durable metrics backends
