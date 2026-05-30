# oMLX Runtime MVP PRD

## Status

Draft, agreed direction. This document describes the first dev-only Thunder Forge v2 experiment on `gateway-cache-01` and an `infer-03` oMLX runtime endpoint. `infer-04` is deliberately excluded from TF v2 development experiments while it is dedicated to direct oMLX/Hindsight stability work.

## Context

Thunder Forge currently has a proven production implementation on `rock`, using per-model MLX services managed over SSH/launchd. That implementation remains a library of operational techniques, but TF v2 should not keep its router/config fallback shape.

For the next architecture, `shag@gateway-cache-01` is the development operator and Thunder Forge has three operational roles: `frontend`, `cache/download`, and `inference node`. In the current dev setup, `gateway-cache-01` is both macOS frontend and macOS cache/download host, while `infer-01`-`infer-04` are macOS inference nodes. Production `rock` must not be used for development work. Later, the frontend role can move back to the Armbian `rock` server, while `gateway-cache-01` remains the cache/download host because it is Thunderbolt-connected to the `infer-01`-`infer-04` inference nodes. Cache/download does not need a daemon; it can be a script/CLI workflow that uses oMLX/Hugging Face tooling to prepare models, then syncs them over Thunderbolt fabric when available or Wi-Fi as fallback.

The final product expectation is broader than this MVP: Thunder Forge should become a controlled compute resource for the Shared Goals platform, `whattodo`, and `text-forge` workloads. It should make model choice, model freshness, node readiness, routing, utilization, and auditability visible to an operator without requiring a web UI for every operation.

oMLX is treated as a node-level runtime daemon: one oMLX server per inference node, serving one or more local models from a model directory. Thunder Forge remains the control plane. The MVP router direction is Olla plus a minimal Thunder Forge edge.

## Current node split

- `gateway-cache-01`: TF v2 development control plane and future model cache hub.
- `infer-03.lan`: TF v2/oMLX development inference node for the MVP.
- `infer-04.lan`: dedicated direct oMLX/Hindsight node; do not use it as the TF v2 dev test bench.
- `rock.lan`: production/current Thunder Forge; do not touch for TF v2 development.

## MVP Goal

Run one MLX artifact on `infer-03` through oMLX using oMLX's default model directory without overriding `--model-dir`, prove the node-runtime abstraction from `gateway-cache-01`, then expose it through Olla and TF edge:

```text
Hermes/operator -> Thunder Forge dev repo on gateway-cache-01 -> oMLX daemon on infer-03 -> Olla -> TF edge
```

The first proof does not need multi-node scheduling or a custom cache topology. It should prove that Thunder Forge can inspect or prepare a `shag`-owned model under the oMLX default model directory on `infer-03`, resolve a usable model directory, start or verify oMLX, smoke-test direct access, and then generate Olla routing from Thunder Forge desired state.

## Operator expectations

- A human or agent operator can request a model by task intent, Hugging Face URL, or model id.
- Thunder Forge can select or validate an appropriate compatible model for the requested task.
- Thunder Forge can download the selected model directly into the oMLX default model directory on the cache/frontend node, choose a suitable inference node, sync the same oMLX model directory to the node, verify node readiness, and start or verify oMLX.
- `shag` is the operational dev user for the new Thunder Forge version on `gateway-cache-01` and `infer-03`.
- For the TF v2 MVP, product state is only the oMLX default model directory. Hugging Face cache layout is not modeled as a product state. `infer-04` continues serving Hindsight through direct oMLX and is not a dev test bench.
- Friends or trusted external users may later access the cluster through scoped API keys; their usage must be visible in utilization and audit summaries.

## Initial Scope

### In scope

- Dev repository under `shag@gateway-cache-01`, not on production `rock`.
- `gateway-cache-01` as development frontend and future caching hub.
- `infer-03` as the only TF v2 development inference node.
- oMLX as a node-level runtime daemon.
- Downloading or verifying one MLX artifact directly under the oMLX default model directory (`~/.omlx/models`) on `gateway-cache-01`, syncing that same model directory to `infer-03`, then running `omlx serve` without an explicit `--model-dir`. Initial candidate ids for this dev flow include:
  - `mlx-community/Qwen3-Embedding-0.6B-4bit-DWQ`
  - `mlx-community/Qwen3-Coder-30B-A3B-Instruct-4bit`
  - `mlx-community/Qwen3-30B-A3B-4bit`
  - `mlx-community/Qwen3.5-35B-A3B-4bit`
  - `mlx-community/Qwen3.6-35B-A3B-4bit`
  - `mlx-community/Qwen3-Coder-Next-4bit`
  - `mlx-community/Qwen3.5-122B-A10B-4bit`
- Node readiness checks:
  - SSH reachable over the stable interface;
  - `uv` installed under `shag`;
  - oMLX installed under `shag`;
  - oMLX daemon running on an agreed dev port;
  - selected model folder visible and readable by `shag`;
  - direct oMLX smoke tests pass.
- Network identity separation:
  - `infer-03.lan` is the stable management/bootstrap path via normal LAN/DNS;
  - future Thunderbolt/fabric paths are point-to-point and must not be assumed to exist under `.lan` DNS; Thunder Forge should dynamically probe link-local fabric addresses when fabric probing is enabled.
- Olla route generation for stable role aliases after the direct oMLX path is healthy.
- Compatibility evidence for memory/agent-like requests:
  - clean final output, no leaked internal channel tokens;
  - stable JSON-ish short outputs;
  - acceptable direct and Olla-mediated latency;
  - no known MLX stream/thread crashes.

### Out of scope

- Production changes on `rock`.
- Moving production traffic from existing Thunder Forge.
- Disturbing `infer-04` while it is dedicated to Hindsight/oMLX.
- Multi-node scheduling.
- Automatic model ranking or model search.
- Multi-node model sync before the single-node default oMLX model-directory flow works.
- Thunderbolt fabric setup as an implicit hidden prerequisite. It must be treated as its own explicit setup/discovery task.
- Full Admin UI redesign.
- PostgreSQL-backed control-plane rewrite.
- New background job framework.
- MCP server implementation.
- SSD KV cache tuning before baseline generation works.
- Hindsight production switch from this TF v2 MVP.
- Kubernetes or other orchestration frameworks.

## First expected flow

```text
An operator asks Hermes or Thunder Forge to make a cached model available for a dev task.
Thunder Forge uses the dev control plane on gateway-cache-01 to inspect infer-03,
downloads or verifies the selected MLX artifact directly under the oMLX default model directory on `gateway-cache-01`,
syncs the selected oMLX model directory to `infer-03`,
starts or verifies the oMLX daemon,
smoke-tests direct oMLX access,
and only then returns either a direct endpoint or an Olla/TF-edge alias for development testing.
```

## Daily operations expectations

Thunder Forge should eventually provide enough structured information for a Daily Compass / operations routine without requiring a dedicated web interface:

- what requests were served by the cluster;
- who sent them, using API-key identity or trusted internal identity;
- which workloads they belonged to, for example Shared Goals platform, `whattodo`, `text-forge`, Hindsight, exploratory/dev, or friend/external usage;
- which model and model version served each workload;
- whether the model was selected intentionally or was only a fallback;
- per-node utilization, latency, failures, and queue/concurrency pressure;
- whether newer or better open-source models should replace current defaults for known workloads;
- what changed in the cluster configuration and through which controlled channel.

The preferred operator interface is a strict, auditable channel first: CLI/API, and later MCP if it becomes useful for Hermes integration. A web UI is optional and should be added only when it clearly improves inspection or safe manual operation.

## Proposed Runtime Shape

```text
gateway-cache-01
  Thunder Forge dev control plane
  future model cache hub
  optional TF edge
    -> Olla router
      -> http://<infer-03-runtime-host>:<dev-port>/v1

infer-03
  stable management path: infer-03.lan
  desired future point-to-point fabric path: dynamic link-local discovery when `fabric_host: true`

  omlx serve --host 0.0.0.0 --port <dev-port>
    # uses oMLX default model directory: /Users/shag/.omlx/models

infer-04
  dedicated Hindsight direct oMLX runtime; excluded from this MVP's dev experiments
```

Prefer port `8018` for early `infer-03` dev experiments while stale `admin`-owned MLX processes may still exist on old ports.

## Acceptance Criteria

The MVP is accepted when all of the following are true:

1. The dev Thunder Forge repository exists under `shag@gateway-cache-01` and does not depend on production `rock` for development.
2. Thunder Forge documents `gateway-cache-01` as dev frontend/cache hub, `infer-03` as the TF v2 development inference node, and `infer-04` as the dedicated Hindsight node.
3. The system treats `~/.omlx/models` as the only product artifact location for TF v2/oMLX model readiness.
4. The selected model folder is present under `/Users/shag/.omlx/models/<oMLX-default-model-dir>` and readable by `shag@infer-03` without relying on `admin@infer-03` ownership.
5. oMLX on `infer-03` is started or verified as a node-level daemon using its default model directory, without passing `--model-dir` in the normal path.
6. Direct oMLX smoke tests pass over the currently available stable interface:
   - `GET /health`;
   - `GET /v1/models`;
   - model status or equivalent model visibility check;
   - `POST /v1/chat/completions`;
   - `POST /v1/responses` if supported by the installed oMLX version.
7. Memory/agent-like smoke tests pass repeatedly:
   - short deterministic prompt;
   - short JSON-like answer;
   - no raw internal channel tokens;
   - no `There is no Stream(gpu, 1) in current thread` or equivalent runtime crash.
8. The Thunderbolt point-to-point fabric requirement is documented with current status, naming/host-mapping options, and setup gap.
9. A generated Olla route exposes the model under an explicit test name if direct oMLX is healthy.
10. The same smoke tests pass through Olla, and then through the minimal TF edge if the edge is included in the MVP run.
11. The compatibility matrix records the result and any limitations.

## Future Direction

After the MVP, Thunder Forge should support task-oriented model selection:

```text
task -> choose appropriate compatible model/version -> prepare/cache -> select node -> run via node runtime -> route through Olla/TF edge
```

The fixed dev-node MVP is intentionally narrow. It is the first proof point for the later model-selection and cluster-management architecture. After the single-node default oMLX model-directory path works, Thunder Forge can generalize to downloading updated or more appropriate models directly into `gateway-cache-01`'s oMLX model directory and syncing those directories to inference nodes over the point-to-point Thunderbolt fabric.

## External design notes considered

A separate design sketch proposed a larger rebuild around FastAPI, PostgreSQL, SQLAlchemy/SQLModel, Alembic, a React/TypeScript/shadcn UI, jobs, and DB-backed LiteLLM reconciliation. This PRD treats that sketch as a useful library of ideas, not as mandatory MVP scope.

Accepted for direction:

- Olla plus a minimal TF edge is the chosen MVP frontend direction after smoke testing; see ADR 0002.
- oMLX should run on each inference node as the node runtime.
- Thunder Forge owns nodes, models, placements, reconcile, and auditability.
- CLI is a first-class interface for Hermes/cron/ops.
- Dry-run before apply and stable JSON output are important.

Deferred by KISS/DRY/YAGNI:

- PostgreSQL/Alembic schema until file-backed desired state becomes insufficient.
- React/shadcn UI until CLI/API summaries are not enough for operations.
- Queue frameworks such as Arq/RQ until simple synchronous or lightweight background operations are insufficient.
- Database-backed router mutation flow until read-only deployment visibility and dry-run reconciliation are stable.
