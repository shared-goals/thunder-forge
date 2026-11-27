# Daily Summary Contract

**Status:** Implemented (MVP) · **Last Updated:** 2026-11-27 · **Owner:** Thunder Forge

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
- `sticky_id` (empty when the client sent no `X-Olla-Session-ID`)
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

- `users.available` (every client identity seen in the period, regardless of the filter),
  `users.selected` (the applied `?user=` filter) and `users.known` (every configured
  `TF_USER_<NAME>` identity, so a client with no traffic today is still selectable). API keys
  are never logged, so only the client identity is ever exposed
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
- `latency_ms.by_hour` (nearest-rank `p50`, `p95`, `max`, and `count` per 0-23 bucket; with
  `period=all` the buckets are hour-of-day across every retained day, not distinct days)
- `cache.ttl_seconds` (the Olla sticky-session idle TTL the cold/warm split used: `600` s, the
  `DEFAULT_STICKY_IDLE_TTL_SECONDS` that the generated Olla config also writes)
- `cache.total`, `cache.by_hour`, `cache.by_client` (`cold`, `warm`, `cold_share` — see below)
- `cache.latency.total` and `cache.latency.hour` (`cold` and `warm` each carry `count`, `p50`, `p95`, so
  the cold tail is read against the warm tail instead of against one merged distribution)
- `concurrency.peak_by_hour` (maximum overlapping request intervals per 0-23 bucket; an
  interval belongs to the hour the request finished in)
- `node_metrics.load_by_hour` (per hour and node: `samples`, `active_max`, `active_avg`,
  `waiting_max`, taken from node snapshots)
- `node_metrics.model_memory` (latest snapshot per node: `{model: {actual_size, idle_seconds}}`)
- `node_metrics.model_memory_sampled_at` (snapshot `timestamp` behind each node's memory numbers)
- `invalid_lines`

`consumed_ms` is defined as sum of edge-observed `latency_ms`.

## Cold and Warm Cache

A request is **cold** when its prefix cache on the node it landed on cannot be reused, and **warm**
otherwise. `latency_ms` is the full request duration, and its tail is mostly cold prefills, so the split
is what makes a slow hour readable.

A request counts as cold when any of these holds:

1. `sticky_id` is empty - without a session id Olla picks a node by least-connections on every turn, so
   the previous prefix sits on an unknown node;
2. it is the first request of that `sticky_id` inside the reported period;
3. the gap from the previous request of the same `sticky_id` exceeds `cache.ttl_seconds` - the sticky
   affinity expired, so the next turn may have gone to another node. The gap runs between request
   starts (`timestamp - latency_ms`), the same sliding window Olla keeps.

The cache is per node and never shared between nodes, so a session that keeps its affinity keeps its
prefix warm; the metric measures affinity, not model behaviour.

Boundaries of the definition:

- the period and the `?user=` filter are applied **before** counting, so a session that started before
  the period produces one cold request at its first turn inside the period;
- a record with no readable `timestamp` counts as cold: without a previous request there is no gap to
  prove affinity (only reachable with `period=all`);
- a client that never sends `X-Olla-Session-ID` reads as 100% cold, because every one of its requests
  falls under rule 1 - check `cache.by_client` before reading the aggregate share;
- the split measures affinity, not observed cache state: a session that fails over inside the TTL (node
  restart, dropped endpoint) keeps its id and therefore stays warm, even though the new node has no
  prefix for it - read the cold share as a lower bound on cache misses;
- records are classified in request-start order, not file order: the edge writes a record when the
  request finishes, so a long cold turn is often logged after the short follow-up that followed it;
- `cold_share` is a share of requests, not of time or tokens; `0.0` for a bucket with no requests;
- a latency bucket with no requests of that kind reports `{count: 0, p50: 0, p95: 0}`, which means "no
  samples", not zero milliseconds.

## HTTP Contract

The edge serves the same aggregates read-only when `TF_EDGE_ADMIN_TOKEN` is set in the
edge environment. Without the token all three paths answer `404 not_found`, so the
endpoints stay invisible. With the token, `GET /usage` is anonymous (the page holds no
data, and browser navigation cannot send an `Authorization` header), while
`/usage/report` and `/usage/events` answer `401 unauthorized` to a wrong or missing token.
Response codes on the data paths: `401 unauthorized` without a valid token, `400 invalid_period` for a
malformed `period`, `400 invalid_source` for an unknown `source`, `400 period_too_broad` for `period=all`
on `/usage/events`, `413 usage_events_too_large` / `413 usage_report_too_large` when the log exceeds 32 MiB
(checked before the file is read), and `503 usage_access_log_unavailable` when the access log is
missing or unreadable for any period, `405 method_not_allowed` for a non-GET request that carries a valid
token, and `503` when a log is missing or unreadable. Authentication is checked before the method on the
data paths, so an unauthenticated request cannot confirm that they exist. `/usage` and `/usage/assets/*`
are anonymous and GET-only.

| Path | Response |
|---|---|
| `GET /usage` | HTML dashboard rendering the report below; Chart.js is served from `/usage/assets/chart.umd.min.js`, not a CDN |
| `GET /usage/report?period=<YYYY-MM-DD\|today\|all>[&user=<client>[,<client>]]` | `usage report --json` payload, optionally narrowed to authenticated client identities |
| `GET /usage/events?period=...&source=access\|node-metrics` | matching JSONL lines (`source` defaults to `access`), `Content-Disposition: attachment`; `period=all` is refused with `400 period_too_broad` |
| `GET /usage/assets/<file>` | vendored dashboard assets (`usage.html`, `chart.umd.min.js`); `404` for anything else |

`?user=` accepts a client identity, repeated or comma-separated; it narrows requests, consumed time,
latency, concurrency and the cold/warm split. Node load and model memory come from node snapshots,
which have no client dimension, so they stay cluster-wide - the cold cache card always says so, and
the header repeats it when a filter is active.

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

- no PostgreSQL, no queue, no dashboard for MVP; the read-only usage page is the exception recorded in
  ADR 0002 (see its MVP guardrails)
- the edge dashboard is a read-only view over the same JSONL files, not a second store
- edge traffic is the request source of truth for now
- token fields are optional and consumed only when present
- hot-loaded model sets come from oMLX model status snapshots

## Evolution

1. keep file-backed JSONL + CLI until operator pain appears
2. enrich token coverage when upstream usage fields are reliable
3. only then evaluate durable metrics backends
