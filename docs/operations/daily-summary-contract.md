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
- `active_requests` (oMLX status value, `null` when unavailable)
- `waiting_requests` (oMLX status value, `null` when unavailable)
- `loaded_models` (`id`, `actual_size` bytes, `idle_seconds` per loaded model)
- `errors`

## Summary Contract

`usage report --json` returns a summary with:

- `requests.total`
- `requests.by_user`
- `requests.by_node`
- `requests.by_model`
- `requests.by_hour` (0-23 buckets, each bucket carries `requests`, `consumed_ms`,
  `by_model`, and `by_user` so one view answers when/what/who)
- `requests.by_node_hour` (0-23 buckets)
- `consumed_ms.total`
- `consumed_ms.by_user`
- `consumed_ms.by_node`
- `consumed_ms.by_model`
- `tokens.by_user` (when available)
- `tokens.by_node` (when available)
- `tokens.by_model` (when available)
- `latency_ms.by_hour` (nearest-rank `p50`, `p95`, `max`, and `count` per 0-23 bucket)
- `concurrency.peak_by_hour` (maximum overlapping request intervals per 0-23 bucket; an
  interval belongs to the hour the request finished in)
- `node_metrics.load_by_hour` (per hour and node: `samples`, `active_max`, `active_avg`,
  `waiting_max`, taken from node snapshots)
- `node_metrics.model_memory` (latest snapshot per node: `{model: {actual_size, idle_seconds}}`)
- `invalid_lines`

`consumed_ms` is defined as sum of edge-observed `latency_ms`.

## HTTP Contract

The edge serves the same aggregates read-only when `TF_EDGE_ADMIN_TOKEN` is set in the
edge environment. Without the token all three paths answer `404 not_found`, so the
endpoints stay invisible. With the token, `GET /usage` is anonymous (the page holds no
data, and browser navigation cannot send an `Authorization` header), while
`/usage/report` and `/usage/events` answer `401 unauthorized` to a wrong or missing token.

| Path | Response |
|---|---|
| `GET /usage` | HTML dashboard rendering the report below; Chart.js is served from `/usage/assets/chart.umd.min.js`, not a CDN |
| `GET /usage/report?period=<YYYY-MM-DD\|today\|all>` | `usage report --json` payload |
| `GET /usage/events?period=...` | matching `tf-edge-access.jsonl` lines, `Content-Disposition: attachment` |
| `GET /usage/assets/<file>` | vendored dashboard assets (`chart.umd.min.js`); `404` for anything else |

`period` defaults to the edge host's local date. Authentication accepts only
`Authorization: Bearer <token>`: a `?token=` query would be recorded by the Caddy/gost
access logs, browser history, and any pasted link.
Aggregation honours `operations.usage.exclude_models` and
`operations.usage.exclude_unauthenticated`, like the CLI.

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
- by hour: requests with model and user split
- by node/hour: request split
- by model: requests, consumed time, tokens
- node hot-loaded summary from collected snapshots

## Guardrails

- no PostgreSQL, no queue, no separate metrics backend for MVP
- the edge dashboard is a read-only view over the same JSONL files, not a second store
- edge traffic is the request source of truth for now
- token fields are optional and consumed only when present
- hot-loaded model sets come from oMLX model status snapshots

## Evolution

1. keep file-backed JSONL + CLI until operator pain appears
2. enrich token coverage when upstream usage fields are reliable
3. only then evaluate durable metrics backends
