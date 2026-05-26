# oMLX Runtime MVP PRD

## Status

Draft, agreed direction. This document describes the first dev-only Thunder Forge v2 experiment on `studio` and an `msm3` oMLX runtime endpoint. `msm4` is deliberately excluded from TF v2 development experiments while it is dedicated to direct oMLX/Hindsight stability work.

## Context

Thunder Forge currently has a proven production implementation on `rock`, using LiteLLM plus per-model MLX services managed over SSH/launchd. That implementation remains the library of working procedures: configuration generation, SSH orchestration, launchd service management, health checks, model syncing, and LiteLLM integration.

For the next architecture, `shag@studio` is the development operator and `studio` is the development frontend/cache hub. Production `rock` must not be used for development work. Later, the control plane can move back to the Armbian `rock` server, while `studio` can remain the cache hub because it is Thunderbolt-connected to the `msm1`-`msm4` inference nodes.

The final product expectation is broader than this MVP: Thunder Forge should become a controlled compute resource for the Shared Goals platform, `whattodo`, and `text-forge` workloads. It should make model choice, model freshness, node readiness, routing, utilization, and auditability visible to an operator without requiring a web UI for every operation.

oMLX is treated as a node-level runtime daemon: one oMLX server per inference node, serving one or more local models from a model directory. Thunder Forge remains the control plane; LiteLLM remains the frontend/router.

## Current node split

- `studio`: TF v2 development control plane and future model cache hub.
- `msm3-wifi.lan`: TF v2/oMLX development inference node for the MVP.
- `msm4-wifi.lan`: dedicated direct oMLX/Hindsight node; do not use it as the TF v2 dev test bench.
- `rock.lan`: production/current Thunder Forge; do not touch for TF v2 development.

## MVP Goal

Run one MLX artifact on `msm3` through oMLX using oMLX's default model directory without overriding `--model-dir`, prove the node-runtime abstraction from `studio`, then optionally expose it through a dev LiteLLM route:

```text
Hermes/operator -> Thunder Forge dev repo on studio -> oMLX daemon on msm3 -> optional LiteLLM dev route on studio
```

The first proof does not need multi-node scheduling or a custom cache topology. It should prove that Thunder Forge can inspect or prepare a `shag`-owned model under the oMLX default model directory on `msm3`, resolve a usable model directory, start or verify oMLX, smoke-test direct access, and only then generate a LiteLLM route if the direct path is healthy.

## Operator expectations

- A human or agent operator can request a model by task intent, Hugging Face URL, or model id.
- Thunder Forge can select or validate an appropriate compatible model for the requested task.
- Thunder Forge can download the selected model directly into the oMLX default model directory on the cache/frontend node, choose a suitable inference node, sync the same oMLX model directory to the node, verify node readiness, and start or verify oMLX.
- `shag` is the operational dev user for the new Thunder Forge version on `studio` and `msm3`.
- For the TF v2 MVP, product state is only the oMLX default model directory. Hugging Face cache layout is not modeled as a product state. `msm4` continues serving Hindsight through direct oMLX and is not a dev test bench.
- Friends or trusted external users may later access the cluster through scoped API keys; their usage must be visible in utilization and audit summaries.

## Initial Scope

### In scope

- Dev repository under `shag@studio`, not on production `rock`.
- `studio` as development frontend and future caching hub.
- `msm3` as the only TF v2 development inference node.
- oMLX as a node-level runtime daemon.
- Downloading or verifying one MLX artifact directly under the oMLX default model directory (`~/.omlx/models`) on `studio`, syncing that same model directory to `msm3`, then running `omlx serve` without an explicit `--model-dir`. Initial candidate ids for this dev flow include:
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
  - `msm3-wifi.lan` is the stable management/bootstrap path via normal LAN/DNS;
  - future Thunderbolt/fabric paths are point-to-point and must not be assumed to exist under `.lan` DNS; use explicit local aliases such as `msm3-fabric` only after the interface and host mapping are configured.
- LiteLLM route generation for a temporary dev model name after the direct oMLX path is healthy.
- Compatibility evidence for memory/agent-like requests:
  - clean final output, no leaked internal channel tokens;
  - stable JSON-ish short outputs;
  - acceptable direct and LiteLLM-mediated latency;
  - no known MLX stream/thread crashes.

### Out of scope

- Production changes on `rock`.
- Moving production traffic from existing Thunder Forge.
- Disturbing `msm4` while it is dedicated to Hindsight/oMLX.
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
Thunder Forge uses the dev control plane on studio to inspect msm3,
downloads or verifies the selected MLX artifact directly under the oMLX default model directory on `studio`,
syncs the selected oMLX model directory to `msm3`,
starts or verifies the oMLX daemon,
smoke-tests direct oMLX access,
and only then returns either a direct endpoint or a temporary LiteLLM model id for development testing.
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
studio
  Thunder Forge dev control plane
  future model cache hub
  optional LiteLLM dev frontend
    -> http://<msm3-runtime-host>:<dev-port>/v1

msm3
  stable management path: msm3-wifi.lan
  desired future point-to-point fabric path: explicit local alias such as msm3-fabric, not configured yet

  omlx serve --host 0.0.0.0 --port <dev-port>
    # uses oMLX default model directory: /Users/shag/.omlx/models

msm4
  dedicated Hindsight direct oMLX runtime; excluded from this MVP's dev experiments
```

Prefer port `8018` for early `msm3` dev experiments while stale `admin`-owned MLX processes may still exist on old ports.

## Acceptance Criteria

The MVP is accepted when all of the following are true:

1. The dev Thunder Forge repository exists under `shag@studio` and does not depend on production `rock` for development.
2. Thunder Forge documents `studio` as dev frontend/cache hub, `msm3` as the TF v2 development inference node, and `msm4` as the dedicated Hindsight node.
3. The system treats `~/.omlx/models` as the only product artifact location for TF v2/oMLX model readiness.
4. The selected model folder is present under `/Users/shag/.omlx/models/<oMLX-default-model-dir>` and readable by `shag@msm3` without relying on `admin@msm3` ownership.
5. oMLX on `msm3` is started or verified as a node-level daemon using its default model directory, without passing `--model-dir` in the normal path.
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
9. A dev LiteLLM route exposes the model under an explicit test name if direct oMLX is healthy.
10. The same smoke tests pass through LiteLLM if LiteLLM is included in the MVP run.
11. The compatibility matrix records the result and any limitations.

## Future Direction

After the MVP, Thunder Forge should support task-oriented model selection:

```text
task -> choose appropriate compatible model/version -> prepare/cache -> select node -> run via node runtime -> route through LiteLLM
```

The fixed dev-node MVP is intentionally narrow. It is the first proof point for the later model-selection and cluster-management architecture. After the single-node default oMLX model-directory path works, Thunder Forge can generalize to downloading updated or more appropriate models directly into `studio`'s oMLX model directory and syncing those directories to inference nodes over the point-to-point Thunderbolt fabric.

## External design notes considered

A separate design sketch proposed a larger rebuild around FastAPI, PostgreSQL, SQLAlchemy/SQLModel, Alembic, a React/TypeScript/shadcn UI, jobs, and DB-backed LiteLLM reconciliation. This PRD treats that sketch as a useful library of ideas, not as mandatory MVP scope.

Accepted for direction:

- LiteLLM remains the proven gateway/router/load-balancer.
- oMLX should run on each inference node as the node runtime.
- Thunder Forge owns nodes, models, placements, reconcile, and auditability.
- CLI is a first-class interface for Hermes/cron/ops.
- Dry-run before apply and stable JSON output are important.

Deferred by KISS/DRY/YAGNI:

- PostgreSQL/Alembic schema until file-backed desired state becomes insufficient.
- React/shadcn UI until CLI/API summaries are not enough for operations.
- Queue frameworks such as Arq/RQ until simple synchronous or lightweight background operations are insufficient.
- Full LiteLLM DB-backed mutation flow until read-only deployment visibility and dry-run reconciliation are stable.
