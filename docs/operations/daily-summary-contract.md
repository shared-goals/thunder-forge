# Daily Summary Contract

**Status:** Draft  
**Last Updated:** 2026-05-26  
**Owner:** Thunder Forge

## Overview

This document defines the minimal JSON contract for daily utilization and audit summaries that feed Daily Compass and operator review. The design intentionally avoids databases, log ingestion systems, or web dashboards until the direct runtime and Olla+edge stack are proven in production.

## JSON Schema

```json
{
  "period": "2026-05-26",
  "generated_at": "2026-05-27T00:15:23Z",
  "source": "thunder-forge",
  "version": "1.0",

  "requests": {
    "total": 142,
    "by_client": {
      "shag-dev": 89,
      "hermes-cron": 53
    },
    "by_model": {
      "qwen3-1.7b-omlx-msm3-test": 142
    },
    "by_status": {
      "200": 138,
      "401": 3,
      "502": 1
    },
    "by_path": {
      "/v1/chat/completions": 139,
      "/v1/models": 3
    }
  },

  "latency": {
    "p50_ms": 1245,
    "p95_ms": 3890,
    "p99_ms": 8920,
    "max_ms": 12100
  },

  "nodes": [
    {
      "name": "msm3",
      "host": "msm3-wifi.lan",
      "port": 8018,
      "health_ok": true,
      "models_served": ["Qwen3-1.7B-4bit"],
      "requests_routed": 142
    }
  ],

  "workloads": [
    {
      "client_id": "shag-dev",
      "description": "Interactive Olla dev-smoke testing",
      "requests": 89,
      "latency_avg_ms": 1180
    },
    {
      "client_id": "hermes-cron",
      "description": "Daily Compass morning projection",
      "requests": 53,
      "latency_avg_ms": 1310
    }
  ],

  "failures": [
    {
      "timestamp": "2026-05-26T14:23:18Z",
      "client_id": "shag-dev",
      "path": "/v1/chat/completions",
      "model": "qwen3-1.7b-omlx-msm3-test",
      "status_code": 502,
      "error": "upstream_failed: ConnectError: [Errno 61] Connection refused",
      "olla_endpoint": ""
    }
  ],

  "model_freshness": {
    "qwen3-1.7b-omlx-msm3-test": {
      "model_dir": "/Users/shag/.omlx/models/Qwen3-1.7B-4bit",
      "last_modified": "2026-05-20T10:15:00Z",
      "age_days": 6,
      "notes": "No updates available"
    }
  },

  "configuration_changes": [
    {
      "timestamp": "2026-05-26T09:30:00Z",
      "commit": "60f2d5b",
      "message": "feat(runtime): add launchd install command for node-level oMLX daemon",
      "files_changed": 4
    }
  ]
}
```

## Data Sources

### Edge Proxy Access Logs

**Source:** `src/thunder_forge/cluster/edge.py` `EdgeAccessLog`  
**Fields captured per request:**
- `timestamp` (ISO 8601, UTC)
- `request_id` (UUID)
- `client_id` (from API key mapping)
- `path` (/v1/chat/completions, /v1/models)
- `model` (from request body)
- `status_code` (200/401/502)
- `latency_ms` (edge-to-edge)
- `olla_endpoint` (from X-Olla-Endpoint header, if routed)

**Current limitation:** Edge proxy logs to `access_log_sink` callback (stdout by default). For daily summary, logs need to be written to a file or aggregated in-memory.

### oMLX Runtime Health

**Source:** `src/thunder_forge/cluster/omlx.py` `check_omlx_health()`  
**Fields:**
- `base_url`
- `health_ok` (from /health)
- `models_ok` (from /v1/models)
- `status_ok` (from /v1/models/status, optional)
- `models` (list of served model IDs)

### Model Freshness

**Source:** Filesystem metadata on model directories  
**Computation:** Compare `last_modified` timestamp against current date.

### Configuration Changes

**Source:** `git log --oneline --since "2026-05-26 00:00:00"` in Thunder Forge repo  
**Fields:** timestamp, commit hash, message, files changed.

## CLI Command (Future)

```bash
uv run thunder-forge summary --period 2026-05-26 --output summary.json
uv run thunder-forge summary --period 2026-05-26 --feed-daily-compass
```

**Behavior:**
1. Read edge access logs for the period (file or stdin).
2. Aggregate by client, model, status, path.
3. Compute latency percentiles.
4. Query oMLX runtime health for each node.
5. Check model freshness.
6. Parse git log for configuration changes.
7. Emit JSON conforming to the schema above.

## Integration with Daily Compass

Daily Compass (Shared Goals) consumes the summary JSON and extracts:
- **Mind:** Configuration changes, model freshness notes
- **Feelings:** Workload descriptions, failure patterns
- **Work:** Request counts, latency trends, model utilization

The summary is intentionally coarse-grained (daily aggregates) to avoid overwhelming Daily Compass with raw request logs.

## Guardrails

- **No PostgreSQL:** Until we know which data already exists in LiteLLM/oMLX logs.
- **No log ingestion system:** Edge proxy logs to stdout/file; no Elasticsearch/Loki/Grafana until necessary.
- **No web dashboard:** CLI output only until runtime MVP is proven.
- **No token counting:** Edge proxy does not parse response bodies; token stats deferred until oMLX or LiteLLM expose them.

## Open Questions

1. **Log persistence:** Should edge proxy write to a rotating file, or rely on systemd/journald?
2. **Token counts:** How do we extract prompt/completion tokens from oMLX or LiteLLM responses?
3. **Streaming requests:** How do we log streaming chat completions (multiple chunks, partial responses)?
4. **Multi-node aggregation:** When multiple oMLX nodes are active, how do we attribute requests to specific nodes? (Partially solved by X-Olla-Endpoint header.)

## Evolution Path

1. **MVP:** File-based logs, manual `summary` CLI command, JSON output.
2. **Iteration 1:** Auto-feed Daily Compass via cron job.
3. **Iteration 2:** Add token counts from oMLX/LiteLLM.
4. **Iteration 3:** Consider PostgreSQL + Grafana if scale demands it.
