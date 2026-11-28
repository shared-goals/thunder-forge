# Daily Summary Contract

**Status:** Implemented (MVP)  
**Last Updated:** 2026-11-27  
**Owner:** Thunder Forge

## Overview

Thunder Forge daily usage reporting is file-backed and CLI-first:

- request events: `logs/tf-edge-access.jsonl`
- node snapshots: `logs/tf-node-metrics.jsonl`
- node snapshots are collected by `thunder-forge edge serve` in-process every 60s by default
- summary command: `thunder-forge usage report`
- shared retention command: `thunder-forge usage trim-logs` (operator-run; `edge serve` never trims)
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
- `stream` (bool, `true` when the request reached the streaming response path)
- `headers_sent` (bool, `true` only when a *streaming* failure came after the status and headers reached the
  client; a successful record stays `false`. A non-streaming request is recorded before its body is written, so
  a client that hangs up mid-body leaves a `200` with no failure field set — the record cannot tell a delivered
  answer from a truncated one)
- `error_kind` (empty when the edge itself raised nothing for the request — including a rejected request and an error status Olla answered on its own, such as `503` with no healthy node; otherwise `edge_timeout`, `edge_connect`, `upstream_error`, `stream_aborted` or `client_aborted`)
- optional token fields: `prompt_tokens`, `completion_tokens`, `total_tokens`

### Node Snapshot JSONL

Source: `usage collect-node-metrics` (one row per inference node per run).

Expected keys:

- `timestamp`
- `node_id`
- `health_ok`
- `models_ok`
- `status_ok`
- `hot_loaded_models` (aliases of the entries where `loaded=true`, ordered by latest access)
- `hot_loaded_count`
- `active_requests` (oMLX status value, `null` when unavailable)
- `waiting_requests` (oMLX status value, `null` when unavailable)
- `loaded_models` (`id` alias, or the raw runtime id when no alias is configured; `actual_size` bytes; `idle_seconds` per loaded model)
- `errors`

`hot_loaded_models` lists only aliases declared for **this node** in the cluster configuration, the
same membership rule `GET /status` uses. A runtime entry with no configured alias is dropped when it
holds no memory — tool adapters such as `MarkItDown` report none — and kept under its raw runtime id
in `loaded_models` when it does, so a hand-loaded model cannot hide its footprint. A kept entry is
named in `errors` as `no configured alias for runtime model: <id>`. That entry cannot tell a
hand-loaded model from a `runtime_model_id` that drifted away from the configuration — both look the
same — so expect one per node per collection run for a node that runs a hand-loaded model; a dropped tool
adapter adds nothing to `errors`. Nothing in `usage report` reads this list: it is visible in the raw
`logs/tf-node-metrics.jsonl` or through `usage events --source node-metrics`.

`hot_loaded_models` lists every configured alias of a loaded runtime model, while `loaded_models`
carries one row per loaded runtime model, using its first alias, so the two counts agree only when
a runtime model has a single alias. `hot_loaded_count` counts aliases, so a kept model that has no
alias appears in `loaded_models` only and leaves that count at `0` beside its memory row. A node that lists no models and has nothing loaded writes `hot_loaded_count: 0` and
`loaded_models: []`, which is indistinguishable from a probe that found nothing loaded.

## Summary Contract

`usage report --json` returns a summary with:

- `users.available` (every client identity seen in the period, regardless of the filter),
  `users.selected` (the applied `?user=` filter) and `users.known` (every configured
  `TF_USER_<NAME>` identity, so a client with no traffic today is still selectable). API keys
  are never logged, so only the client identity is ever exposed
- `requests.total`
- `requests.unauthenticated` (records whose `client_id` is the unauthenticated marker, e.g. a
  rejected key or scanner traffic; counted even when `exclude_unauthenticated` keeps them out of
  every other breakdown, so a scanner run stays visible)
- `requests.by_user`
- `requests.by_node` (`"unknown"` collects records with no `X-Olla-Endpoint` attribution, so the
  bucket sums to `requests.total`)
- `requests.by_model` (`"unknown"` collects records with an empty model, such as `/v1/models`
  catalogue calls; models listed in `operations.usage.exclude_models` stay out of the breakdown)
- `requests.by_status` (`status_code` -> count histogram; `"unknown"` when a record carries no
  integer status)
- `requests.by_hour` (all 24 hour buckets 00-23, each bucket carries `requests`, `consumed_ms`,
  `by_model`, and `by_user` so one view answers when/what/who; hours without traffic are present
  with zeros, so a silent hour is visible)
- `requests.by_node_hour` (same 24 hour buckets per node, keyed by hour of day so `period=all` sums the same hour across days, zero-filled the same way; for `period=all` a
  record with no parsable timestamp lands in an extra `"unknown"` bucket in both hour breakdowns, so
  `by_node_hour` still sums to `requests.total`. A dated period keeps only records whose timestamp it
  can parse, so the sum still matches: an undated record is outside every day)
- `failures_total` (top level: records with `status_code >= 400`, plus any record whose
  `error_kind` is a cluster failure — a stream that died after its headers keeps
  `status_code: 200`; `client_aborted` is the client's own choice and is not counted)
- `consumed_ms.total`
- `consumed_ms.by_user`
- `consumed_ms.by_node`
- `consumed_ms.by_model`
- `tokens.by_user` (when available)
- `tokens.by_node` (when available)
- `tokens.by_model` (when available)
- `latency_ms.by_hour` (nearest-rank `p50`, `p95`, `max`, and `count` per 0-23 bucket; with
  `period=all` the buckets are hour-of-day across every retained day, not distinct days)
- `concurrency.peak_by_hour` (maximum overlapping request intervals per 0-23 bucket; an
  interval belongs to the hour the request finished in)
- `node_metrics.load_by_hour` (per hour and node: `samples`, `active_max`, `active_avg`,
  `waiting_max`, taken from node snapshots)
- `node_metrics.model_memory` (latest snapshot per node: `{model: {actual_size, idle_seconds}}`)
- `node_metrics.model_memory_sampled_at` (snapshot `timestamp` behind each node's memory numbers)
- `coverage.first_record_at` / `coverage.last_record_at` (first and last timestamp among the
  retained records of the period, `null` when the period holds none; they ignore the `?user=`
  filter and describe the log, not the filtered view)
- `partial` (true when the requested period is older than the oldest day in the log — the period
  is missing, not empty — or when it is the oldest day **and** its first record starts after
  00:00:59; minute granularity, so a cut inside the first minute is not flagged) Retention cuts the file at an absolute `now - N days` instant, so the
  oldest day is the only one it can truncate. The flag is a warning, not proof: an idle morning
  looks the same as a truncated morning)
- `invalid_lines`

`consumed_ms` is defined as sum of edge-observed `latency_ms`.

## CLI Exit Codes

`usage report` and `usage report --json` exit `2` and name the resolved path when the access log
file is absent, instead of printing `requests_total: 0` for a mistyped path or a wrong checkout.
An invalid `--period` stays `1`.

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
latency and concurrency. Node load and model memory come from node snapshots, which have no client
dimension, so they stay cluster-wide - the dashboard says so when a filter is active.

`period` defaults to the edge host's local date. Authentication accepts only
`Authorization: Bearer <token>`: a `?token=` query would be recorded by the Caddy/gost
access logs, browser history, and any pasted link.
Aggregation honours `operations.usage.exclude_models` and
`operations.usage.exclude_unauthenticated`, like the CLI.

## Retention Contract

- Config key: `services.log_retention_days`
- Default: `3`
- Entry point: `thunder-forge usage trim-logs` only. Retention never runs inside `edge serve` or `usage collect-node-metrics` (including its `--continuous` mode), and that collector has no `--retention-days` flag any more; trimming a log that a live process appends to loses records.
- Scheduling: operator-run, nothing in the repository schedules it. Run it from cron (`README.md` carries the line); it exits `1` when any file had to be skipped.
- Scope: local TF-managed logs
  - `logs/tf-edge-access.jsonl`
  - `logs/tf-node-metrics.jsonl`
  - `logs/*.stdout.log`, `logs/*.stderr.log`, `logs/*.log.gz`, `logs/.*.jsonl.*.tmp` (leftovers of a killed trim)
- Behavior:
  - JSONL files are trimmed by record `timestamp`; blank lines are not counted as removed records, and a file with no expired record is not rewritten at all (blank lines survive such a run, and are dropped as collateral when the file is rewritten)
  - the trimmed file is written next to the original and swapped with `os.replace`. Bytes a live writer appended after the read are spliced onto the new file from the saved offset, so a growing log still trims; a file that shrank (rotated or truncated under us) is skipped and reported as `skipped_jsonl_files`. The offset read and the swap are not atomic: a record written in that window can still be lost
  - plain/archive logs are pruned whole by file mtime
  - `logs/olla.log` is plain text without record timestamps, so it is not a retention target; this command never rewrites or deletes it

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
