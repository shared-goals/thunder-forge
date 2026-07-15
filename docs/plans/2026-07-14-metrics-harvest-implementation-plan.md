# Thunder Forge Metrics Harvest Implementation Plan (2026-07-14)

## Goal
Implement endpoint-first, minimal metrics harvesting and reporting that can diagnose cluster under-utilization, sticky/session effectiveness, and routing quality without adding a new metrics backend.

## Scope
- In scope:
  - Extend edge request JSONL schema for session/sticky and TTFT-focused analysis.
  - Extend node snapshot JSONL schema with oMLX queue/idle/cache signals when exposed.
  - Add derived key metrics to usage CLI and DuckDB report.
  - Keep implementation thin and upstream-aligned with Olla and oMLX capabilities.
- Out of scope:
  - New persistent metrics database.
  - Custom Thunder Forge routing logic parallel to Olla.
  - Long-term compatibility shims.

## Decision Order (Mandatory)
1. Reuse upstream capability as-is.
2. Wire existing upstream capability into Thunder Forge with thin integration.
3. Propose upstream enhancement when capability is missing.
4. Add minimal local temporary workaround only if unavoidable.

## Current Baseline (Observed)
- Edge log includes: client_id, path, model, status_code, latency_ms, sticky_id, olla_endpoint.
- Node snapshot collection currently samples oMLX health/models/status and stores hot_loaded_models and hot_loaded_count.
- DuckDB daily report currently provides request volume and consumed time splits, but not TTFT, sticky reuse, or routing-regret style indicators.

## Phase 1: Edge Access Schema Upgrade

### Objective
Capture the minimum per-request fields needed for TTFT and sticky/session quality metrics while keeping logs secret-free.

### Files
- src/thunder_forge/cluster/edge.py
- src/thunder_forge/cluster/usage.py
- tests/test_edge.py
- tests/test_usage.py
- docs/operations/daily-summary-contract.md

### Changes
1. Update edge access log payload fields:
- Include olla_endpoint in serialized payload.
- Include sticky_result from response header X-Olla-Sticky-Session when present.
- Include sticky_key_source from response header X-Olla-Sticky-Key-Source when present.
- Add client_class derived from known edge callers where deterministic (opencode, vscode, hermes-agent, other).

2. Add TTFT field:
- Add time_to_first_token_ms as nullable integer.
- For streaming requests: compute TTFT as elapsed ms from upstream request start to first streamed bytes sent.
- For non-streaming requests: leave null (do not fake TTFT from full latency).

3. Preserve strict privacy:
- No API keys, full request body content, response content, or auth header values in logs.
- Internal-cluster exception: if Hermes session id is present in request JSON, extract only that field and log it as session_key (or sticky_id); do not persist any other request-body fields.

### Acceptance Criteria
- New JSONL records contain olla_endpoint and derive node_id from endpoint in usage/reporting.
- Streaming requests write non-null time_to_first_token_ms for successful upstream streams.
- Non-streaming requests write null/missing TTFT.
- Existing edge behavior and smoke tests continue to pass.

## Phase 2: Node Snapshot Schema Upgrade (Endpoint-First)

### Objective
Collect just enough node-side signals from oMLX status for idle/queue/cache analysis.

### Files
- src/thunder_forge/cli.py
- src/thunder_forge/cluster/omlx.py
- tests/test_cli_usage.py
- docs/operations/daily-summary-contract.md

### Changes
1. Extend per-node snapshot rows written by usage collect-node-metrics:
- Add model-level aggregates from oMLX status when available:
  - models_loading_count
  - models_loaded_count
  - latest_last_access_epoch (max)
  - oldest_last_access_epoch (min)
- Add optional fields only if exposed by oMLX status payload:
  - queue_depth
  - active_requests
  - prompt_cache_hit_count
  - prompt_cache_miss_count

2. Add a lightweight schema policy:
- Missing upstream fields remain absent or null.
- No synthetic fallback values that imply availability.

### Acceptance Criteria
- Snapshot collector writes extended fields without breaking existing consumers.
- Collector remains robust when optional upstream fields are absent.
- Existing hot_loaded_models and hot_loaded_count outputs remain unchanged.

## Phase 3: Usage Computation Extensions

### Objective
Compute key metrics that map directly to routing and placement decisions.

### Files
- src/thunder_forge/cluster/usage.py
- src/thunder_forge/cli.py
- docs/operations/daily-usage-duckdb.sql
- tests/test_usage.py
- tests/test_cli_usage.py

### New Derived Metrics
1. TTFT p95
- Group by model, node_id, client_class.
- Use time_to_first_token_ms where present.

2. Sticky/session reuse rate
- For repeat sticky_id requests, rate of same-node routing as previous request in same sticky_id.
- Exclude empty sticky_id rows.

3. Hot-load hit rate
- Request considered hit if requested model appears in latest node hot-loaded set near request time.
- Use nearest snapshot at or before request timestamp for node.

4. Model spread and pressure spread
- Per model: distribution across capable nodes.
- Per node: request share vs cluster mean ratio.

5. Routing regret (v1 approximation)
- For each request, compare selected node observed TTFT/latency against best eligible node estimate in same time bucket.
- Keep approximation explicit and conservative.

### Acceptance Criteria
- usage report --json includes a new metrics section with above metrics when source fields exist.
- Missing source fields degrade gracefully (metric omitted or marked unavailable).
- DuckDB script prints same metrics for parity with CLI summary.

## Phase 4: Olla Status Integration (Thin, Optional)

### Objective
Use Olla internal endpoints as supplemental diagnostics, not primary source of request metrics.

### Files
- src/thunder_forge/cluster/olla.py
- src/thunder_forge/cli.py
- tests/test_olla_smoke.py

### Changes
1. Add read-only probes callable from usage flows:
- GET /internal/status/endpoints
- GET /internal/status/models
- GET /internal/stats/sticky

2. Wire these into optional diagnostics output:
- Add a usage diagnostics command or optional flag in usage report.
- Keep default path fast and local-file based.

### Acceptance Criteria
- Endpoint probe failures do not fail daily usage reports.
- Diagnostics output clearly labels point-in-time router stats.

## Phase 5: Validation and Regression Gates

### Objective
Prove correctness and operational value before expanding scope.

### Test Commands
- make dev-lint
- make dev-test
- uv run pytest tests/test_edge.py tests/test_usage.py tests/test_cli_usage.py tests/test_olla_smoke.py
- make usage <day>
- make usage-duckdb <day>

### Before/After KPI Checks
- Main KPI: TTFT p95 reduction for opencode and vscode cohorts.
- Balance KPI: lower node request-share imbalance ratio.
- Reuse KPI: higher sticky/session same-node reuse for repeat sessions.
- Warmth KPI: higher hot-load hit rate for memory-heavy models.

## Upstream Contribution Track

### Olla upstream candidates
1. Sticky stats clarity and stable schema at /internal/stats/sticky.
2. Optional per-endpoint queue/load telemetry in internal status endpoints.

### oMLX upstream candidates
1. Stable queue depth and active-request counters in /v1/models/status or companion endpoint.
2. Prompt-cache hit/miss counters exposed in a machine-friendly form.

### Issue Template Checklist
- Reproduction steps.
- Observed behavior vs expected behavior.
- Minimal sample payload/log snippet.
- Testable acceptance criteria.

## Implementation Sequence (Recommended)
1. Phase 1 edge schema changes plus tests.
2. Phase 2 node snapshot extensions plus tests.
3. Phase 3 usage and DuckDB metric computation.
4. Phase 4 optional Olla diagnostics probe.
5. Full validation and docs refresh.

## Risks and Mitigations
- Risk: TTFT unavailable for non-streaming flows.
  - Mitigation: keep TTFT nullable and reported only for valid records.
- Risk: Upstream schema drift in Olla/oMLX endpoints.
  - Mitigation: tolerant parsing and optional field semantics.
- Risk: Overfitting local heuristics.
  - Mitigation: encode approximations explicitly and prefer upstream metrics when exposed.

## Done Definition
- Daily usage outputs can answer:
  - Which models and clients suffer highest TTFT?
  - Are repeat sessions staying on the same node?
  - Is model load concentrated on a subset of nodes?
  - Are hot-loaded models actually serving requests?
- No new TF-specific routing engine introduced.
- Docs and tests reflect final schema and formulas.
