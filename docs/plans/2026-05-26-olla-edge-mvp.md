# Olla Router + Thunder Forge Edge MVP Plan

> **For Hermes:** Use subagent-driven-development skill to implement this plan task-by-task. Keep each task reviewable. Do not touch production `rock` or `msm4`.

**Goal:** Turn the Olla smoke result into a reproducible Thunder Forge MVP: generate Olla config from TF desired state, smoke Olla against `msm3` oMLX, then add the smallest TF edge needed for root OpenAI `/v1/*`, client API-key validation, stable session behavior, and structured request accounting.

**Architecture:** `studio` runs Thunder Forge control plane, generated Olla router config, and optionally the TF edge service. `msm3` runs oMLX on dev port `8018`. External clients go through homelab Caddy to TF edge; internal trusted clients may call TF edge directly for MVP simplicity. Both can later route through Caddy if uniform ingress is useful.

**Tech Stack:** Python 3.12+, Typer, pytest, httpx, existing Thunder Forge SSH/config helpers, Olla release binary/config, optional Caddy config snippet. The custom edge may be implemented as a tiny Python ASGI/httpx streaming proxy or a small Go service; choose only after writing the edge spike acceptance tests.

---

## Constraints and guardrails

- Work only in `/Users/shag/Work/thunder-forge` on `studio`.
- Use `uv run`, never raw `python` or `pytest`, inside this repo.
- Do not modify production `rock`.
- Do not disturb `msm4`; it remains dedicated to direct oMLX/Hindsight stability.
- Use `msm3-wifi.lan` as the stable management/runtime host for this MVP.
- Keep generated Olla config out of source-of-truth role; Thunder Forge desired state owns node/model/router intent.
- Do not commit API keys. Use env vars or ignored local files.
- Keep tracing off by default.
- Prefer metrics/status/logs that already exist before adding a database.
- KISS: no PostgreSQL, queue framework, Admin UI, MCP server, dynamic key management, or quota product in this MVP.
- DRY: do not duplicate node/model state between config generator, smoke tests, and docs.
- YAGNI: no multi-node scheduling before the single-node Olla+edge path is repeatable.

## Current evidence

Olla `v0.0.27` was smoked locally from `/tmp/tf-gateway-close-look/olla-bin/olla` using `/tmp/tf-gateway-close-look/olla-bin/tf-olla-smoke.yaml`.

Green:

- `/internal/health`;
- endpoint health/failover exclusion;
- discovered model `Qwen3-1.7B-4bit` from `msm3` oMLX;
- `/olla/proxy/v1/models` and `/olla/openai-compatible/v1/models`;
- non-streaming `/v1/chat/completions` through Olla;
- alias rewrite;
- sticky sessions;
- endpoint attribution headers.

Gaps to cover in TF edge:

- root `/v1/*` path;
- inbound API-key validation;
- client identity and accounting envelope;
- stable session id injection/defaulting;
- Prometheus-style metrics are not available in Olla v0.0.27 smoke.

---

## Task 1: Align docs with the Olla decision

**Objective:** Make the architecture decision explicit before new code.

**Files:**

- Created: `docs/adr/0002-olla-router-with-tf-edge.md`
- Modify: `docs/prd/2026-05-24-omlx-runtime-mvp.md`
- Modify: `docs/adr/0001-omlx-node-runtime.md`
- Modify: `docs/plans/2026-05-24-omlx-runtime-mvp.md` only if it still names LiteLLM/llm-gateway as the preferred next frontend.

**Steps:**

1. Update PRD language to “Olla + TF edge is the chosen MVP direction”.
2. Add a short reference from ADR 0001 to ADR 0002.
3. Keep historical notes intact; do not rewrite the investigation history.
4. Run:
   ```bash
   git diff -- docs/prd/2026-05-24-omlx-runtime-mvp.md docs/adr/0001-omlx-node-runtime.md docs/adr/0002-olla-router-with-tf-edge.md docs/plans/2026-05-24-omlx-runtime-mvp.md
   ```

**Expected:** Docs clearly say: direct oMLX first, Olla router next, TF edge for auth/root path/accounting, Caddy for external ingress.

## Task 2: Define minimal desired-state schema for router generation

**Objective:** Represent the data needed to generate Olla config without letting Olla YAML become source of truth.

**Files:**

- Modify: `src/thunder_forge/cluster/config.py`
- Modify: `configs/node-assignments.yaml.example`
- Create/modify: `tests/test_config.py` or `tests/test_olla_config.py`

**Schema direction:**

```yaml
router:
  type: olla
  listen: 127.0.0.1:40115
  provider_path: /olla/openai-compatible/v1
  sticky_sessions:
    enabled: true
    session_header: X-Olla-Session-ID
  aliases:
    qwen3-1.7b-omlx-msm3-test: Qwen3-1.7B-4bit

nodes:
  msm3:
    host: msm3-wifi.lan
    runtime:
      type: omlx
      base_url: http://msm3-wifi.lan:8018

runtime_routes:
  - model_name: agent
    runtime: omlx
    node: msm3
    model: Qwen3.6-35B-A3B-4bit

models:
  qwen3-dev:
    runtime_model_id: Qwen3-1.7B-4bit
    public_aliases:
      - qwen3-1.7b-omlx-msm3-test
```

Exact names may change if current config models already have better fields. Preserve the distinction: TF model id, backend runtime model id, public aliases, and node runtime endpoint.

**Tests:**

- parse one-node Olla router config;
- verify default sticky sessions enabled;
- verify no API keys are present in generated/committed config fields;
- verify aliases point to backend runtime model ids.

## Task 3: Implement Olla config generator

**Objective:** Generate Olla YAML from TF desired state.

**Files:**

- Create: `src/thunder_forge/cluster/olla.py`
- Modify: `src/thunder_forge/cli.py`
- Create: `tests/test_olla_config.py`
- Create/modify: `tests/test_cli_gateway.py`

**Command shape:**

```bash
uv run thunder-forge gateway olla config --dry-run
uv run thunder-forge gateway olla config --output /tmp/tf-olla.yaml --apply
```

**Generator requirements:**

- listen address from config, default local-only for MVP;
- OpenAI-compatible backend profile for oMLX nodes;
- endpoint name equals TF node id or explicit runtime endpoint id, e.g. `msm3-omlx`;
- health checks with `check_timeout < check_interval` to avoid the known Olla validation error;
- sticky sessions enabled;
- aliases generated from TF model aliases;
- no plaintext client API keys in Olla config;
- stable deterministic YAML output.

**Expected:** Generated YAML is close to the smoke config but reproducible and source-of-truth-free.

## Task 4: Add reproducible Olla smoke command

**Objective:** Replace the manual `/tmp` smoke with a repeatable CLI command.

**Files:**

- Modify: `src/thunder_forge/cluster/olla.py`
- Modify: `src/thunder_forge/cli.py`
- Create/modify: `tests/test_olla_smoke.py`, `tests/test_cli_gateway.py`

**Command shape:**

```bash
uv run thunder-forge gateway olla smoke \
  --base-url http://127.0.0.1:40115 \
  --model Qwen3-1.7B-4bit \
  --alias qwen3-1.7b-omlx-msm3-test
```

**Smoke checks:**

1. `GET /internal/health` returns healthy.
2. `GET /internal/status/endpoints` includes healthy `msm3` and excludes dead endpoint from routable use.
3. `GET /olla/openai-compatible/v1/models` includes requested backend model id.
4. `POST /olla/openai-compatible/v1/chat/completions` succeeds non-streaming.
5. Alias request succeeds and returns/routs to backend model.
6. Sticky session miss/hit can be observed using a fixed `X-Olla-Session-ID`.
7. Response headers include `X-Olla-Endpoint`.
8. Root `/v1/models` is either absent as expected or later covered by TF edge.

**Output:** JSON plus readable summary. Include failure reasons, not just pass/fail.

## Task 5: Decide TF edge implementation by spike test, not taste

**Objective:** Choose Caddy-only or tiny custom edge with evidence.

**Files:**

- Create: `docs/notes/2026-05-26-edge-options.md`
- Optional create: `experiments/edge-caddy/` ignored or docs-only snippet
- Optional create: `experiments/edge-custom/` ignored or throwaway script

**Comparison criteria:**

| Capability | Caddy-only | Tiny custom edge |
|---|---|---|
| root `/v1/*` rewrite | easy | easy |
| static API-key validation | possible | easy |
| per-client identity mapping | awkward | easy |
| stable session id injection | awkward | easy |
| structured per-request accounting with Olla endpoint header | awkward | easy |
| external TLS/hostnames | excellent | should not own |
| streaming proxy correctness | Caddy proven | must test |

**Default decision unless evidence disagrees:** custom TF edge behind Caddy. Caddy remains external ingress.

## Task 6: Implement minimal TF edge config and smoke tests

**Objective:** Define the edge contract before proxy implementation.

**Files:**

- Create: `src/thunder_forge/cluster/edge.py` or `src/thunder_forge/edge.py`
- Modify: `src/thunder_forge/cli.py`
- Create: `tests/test_edge.py`
- Create: `tests/test_cli_edge.py`

**Command shape:**

```bash
uv run thunder-forge edge smoke \
  --base-url http://127.0.0.1:40116 \
  --api-key-env TF_DEV_EDGE_KEY \
  --model qwen3-1.7b-omlx-msm3-test
```

**Smoke expectations:**

- missing API key -> `401`;
- invalid API key -> `401`;
- valid API key -> `/v1/models` works;
- valid API key -> `/v1/chat/completions` works through Olla;
- edge rewrites `/v1/*` to Olla provider path;
- edge supplies or passes `X-Olla-Session-ID`;
- edge logs a structured JSON access line with:
  - timestamp;
  - request id;
  - client id;
  - path;
  - model if present;
  - status;
  - latency_ms;
  - Olla endpoint from response header when present;
  - no API key value.

## Task 7: Implement the minimal TF edge proxy

**Objective:** Add the smallest production-shaped edge after tests define the contract.

**Implementation constraints:**

- Keep it one small service/module.
- Support non-streaming first, but do not claim streaming support until tested.
- If using Python ASGI, use existing project dependency discipline via `uv`; do not add a framework if stdlib/httpx is enough for CLI smoke. If streaming makes Python awkward, choose tiny Go and document why.
- Static keys come from env/local ignored config and map to client ids.
- Never log secrets.
- Do not implement quotas, dynamic keys, UI, DB, or admin API.

**Expected:** Edge can be run locally on `studio`, front Olla on `127.0.0.1:40115`, and pass Task 6 smoke.

## Task 8: Add optional Caddy ingress snippet

**Objective:** Document how external homelab traffic reaches TF edge without moving TF semantics into Caddy.

**Files:**

- Create: `docs/operations/caddy-tf-edge.md`
- Optional: `configs/caddy/thunder-forge.Caddyfile.example`

**Snippet shape:**

```caddyfile
# Example only; real hostnames/secrets live outside git.
tf.example.internal {
  reverse_proxy 127.0.0.1:40116
}
```

If using Caddy for static perimeter auth in addition to TF edge, document the order clearly. Avoid duplicating API-key truth in both Caddy and TF edge for MVP.

## Task 9: Add accounting summary contract

**Objective:** Make request accounting useful before adding storage.

**Files:**

- Create: `docs/operations/tf-edge-accounting-contract.md`

**Contract:**

Start with JSONL logs emitted by TF edge. Define how a daily summary can be computed from logs:

```json
{
  "period": "YYYY-MM-DD",
  "requests_total": 0,
  "clients": [],
  "models": [],
  "nodes": [],
  "failures": [],
  "latency_ms": {"p50": 0, "p95": 0}
}
```

Do not add a database until JSONL files are insufficient.

## Task 10: Final verification

Run:

```bash
uv run pytest tests/ -q
uv run ruff check src/ tests/
git status --short --branch
git diff
```

Expected:

- tests pass;
- ruff passes;
- diff is reviewable;
- no production `rock` files changed;
- no `msm4`/Hindsight runtime changes;
- no API keys in docs/configs/tests.

## MVP acceptance

The Olla + TF edge MVP is accepted when:

1. Direct oMLX on `msm3` is healthy.
2. Generated Olla config routes to `msm3` and passes smoke.
3. TF edge exposes root `/v1/*`, validates static API keys, injects/preserves sessions, and logs request accounting.
4. Optional Caddy route reaches TF edge for external-style traffic.
5. The whole path is reproducible from CLI commands and documented evidence, not from hand-edited `/tmp` artifacts.
