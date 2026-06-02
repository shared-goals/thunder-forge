# ADR 0001: oMLX as Node-Level Runtime for Thunder Forge v2

## Status

Accepted for MVP design.

## Context

The current Thunder Forge implementation on `rock` is a working production system and should be treated as a library of proven procedures, not as disposable legacy. It contains useful implementation patterns for:

- generated router configuration;
- SSH-based node operations;
- launchd service management;
- model download/sync flows;
- direct and routed health checks;
- config-driven cluster state.

However, the current runtime model is centered around one service per model/port. That shape is awkward for oMLX, because oMLX is designed as a local Apple Silicon inference server that can discover and serve multiple models from a model directory, with model loading/unloading, continuous batching, and cache management handled inside the node runtime.

The next Thunder Forge version starts in a dev environment:

```text
shag@gateway-cache-01:/Users/shag/Work/thunder-forge
```

Production `rock` must not be used for dev work. Thunder Forge has three operational roles: `frontend`, `cache/download`, and `inference node`. In the current dev setup, `gateway-cache-01` is both macOS frontend and macOS cache/download host, while `infer-03` is the first TF v2 development inference node and `infer-01`-`infer-04` are the macOS inference-node pool. `infer-04` is dedicated to direct oMLX/Hindsight work and should not be disturbed by TF v2 experiments. Later, the frontend role may move back to Armbian `rock`; `gateway-cache-01` should remain the cache/download host due to its Thunderbolt connection to `infer-01`-`infer-04`.

The very first use case is narrower than full model orchestration: run oMLX against a model that lives under the oMLX default model directory (`~/.omlx/models`) on `infer-03`. Product state must not include Hugging Face cache layout. `gpt-oss-20b` is currently the Hindsight model on `infer-04`, not the first TF v2 dev target.

The long-term product target is a controlled compute resource for Shared Goals platform, `whattodo`, and `text-forge` tasks. Thunder Forge should expose enough operational facts for agent-driven daily operations: request load, caller/API-key identity, workload identity, model version, node utilization, failures, latency, and model freshness. That does not require a web UI in the MVP; a strict CLI/API path is the smaller and more controllable interface.

The network path has two distinct roles:

- `infer-03.lan` is stable and suitable for management/bootstrap, but slow for large model movement. It belongs to the normal LAN/DNS world.
- Future Thunderbolt/fabric links are point-to-point between `gateway-cache-01` and inference nodes, so they should not assume Keenetic `.lan` DNS. Thunder Forge should dynamically probe reachable link-local fabric addresses when fabric probing is enabled.

## Decision

Thunder Forge v2 will model oMLX as a **node-level runtime daemon**, not as a per-model/per-port serving mode.

The MVP architecture is:

```text
Thunder Forge = control plane and orchestration
Frontend      = optional router/balancer after direct runtime health is proven
gateway-cache-01        = dev control plane + future cache hub + optional dev gateway
infer-03          = first TF v2 development oMLX inference node
infer-04          = dedicated direct oMLX/Hindsight node, excluded from dev experiments
oMLX          = node-local inference runtime daemon
```

For the MVP, the model is not hardcoded. It is chosen by model id, downloaded directly into the oMLX default model directory on `gateway-cache-01`, then synced as an oMLX model directory to `infer-03` for a direct smoke test. Current initial candidate ids are:

```text
mlx-community/Qwen3-Embedding-0.6B-4bit-DWQ
mlx-community/Qwen3-Coder-30B-A3B-Instruct-4bit
mlx-community/Qwen3-30B-A3B-4bit
mlx-community/Qwen3.5-35B-A3B-4bit
mlx-community/Qwen3.6-35B-A3B-4bit
mlx-community/Qwen3-Coder-Next-4bit
mlx-community/Qwen3.5-122B-A10B-4bit
```

The first implementation milestone is direct oMLX execution from oMLX's default model directory on `infer-03` (`~/.omlx/models`) without overriding `--model-dir`. The cache host is the primary MVP model source, but the source path is also the oMLX default model directory. If gateway-cache-01 is missing a requested artifact, the next action is to download it directly into gateway-cache-01's `~/.omlx/models`, then sync that model directory from gateway-cache-01 to the node. The initial implementation should expose this as explicit dry-run/apply CLI steps before broader automation.

Thunder Forge v2 will prefer strict, auditable operator channels in this order:

1. CLI with stable JSON and explicit dry-run/apply semantics.
2. HTTP API when the CLI/service boundary becomes useful.
3. MCP only if Hermes integration needs a first-class tool surface.
4. Web UI only if it clearly improves safe inspection or manual operations.

## Consequences

### Positive

- Keeps Thunder Forge's cluster/control-plane role clear.
- Avoids forcing oMLX into the old one-model/one-port abstraction.
- Preserves working production `rock` while dev proceeds on `gateway-cache-01`.
- Keeps Hindsight stable by reserving `infer-04` for direct oMLX/Hindsight.
- Reduces MVP risk by using the same oMLX default model-directory layout on `gateway-cache-01` and nodes before building broader download/sync automation.
- Separates stable LAN management networking from future high-speed point-to-point Thunderbolt/fabric networking.
- Makes later task-oriented model selection natural:

  ```text
  task -> compatible model -> cache -> node -> runtime -> Olla route -> TF edge
  ```

- Allows oMLX to own local inference concerns: batching, model loading, cache behavior, Harmony handling, and supported API surfaces.
- Keeps public documentation framed around operator expectations and system behavior rather than personal user stories.

### Negative / Trade-offs

- Existing deployment code cannot be reused mechanically; it must be abstracted around runtime type.
- Health checks need to distinguish node runtime health from individual model readiness.
- Olla route generation must allow stable role aliases to point at node-level oMLX runtime model ids.
- Model directory sync semantics become more important than port allocation.
- The first milestone depends on downloading models directly into the oMLX default model directory instead of relying on tool-specific cache layout.
- Thunderbolt fabric setup and host mapping become separate prerequisites for the later cache-hub flow.
- Early experiments should use an isolated dev port, preferably `8018`, until runtime ownership and launchd/service management are represented explicitly in Thunder Forge config.

## Schema Direction

Thunder Forge v2 is not bound to the old config schema. Existing production config on `rock` stays untouched operationally, but the new dev architecture may use a fresh schema if that is cleaner. Old fields are useful reference material, not compatibility requirements for the MVP.

Preferred fresh shape:

```yaml
nodes:
  gateway-cache-01:
    host: gateway-cache-01.lan
    roles: [gateway, cache]
    user: shag
    admin_user: serpo

  infer-03:
    host: infer-03.lan          # stable management/bootstrap path
    fabric_host: true            # enable dynamic point-to-point fabric probing
    role: inference
    user: shag
    admin_user: admin
    runtime:
      type: omlx
      port: 8018
    models:
      - agent

models:
  agent:
    source:
      repo: mlx-community/Qwen3.6-35B-A3B-4bit
    runtime_model_id: Qwen3.6-35B-A3B-4bit
    runtime_artifact:
      repo: mlx-community/Qwen3.6-35B-A3B-4bit
      model_dir_name: mlx-community/Qwen3.6-35B-A3B-4bit
      cache_path: /Users/shag/.omlx/models/mlx-community/Qwen3.6-35B-A3B-4bit
      node_path: /Users/shag/.omlx/models/mlx-community/Qwen3.6-35B-A3B-4bit
      status: sync-first
    intended_workloads:
      - tf-v2-dev-smoke
    runtime_compat:
      omlx: unknown
```

The exact field names are not final. The important architectural distinctions are: the node has both stable LAN management identity and future point-to-point fabric identity; oMLX runtime state is node-level; runtime models live under the oMLX default model directory and the normal `omlx serve` command should not pass `--model-dir` unless there is a deliberate reason to override it.

## MVP Guardrails

- Do not modify production `rock` for dev work.
- Do not disturb `infer-04`; it is the dedicated direct oMLX/Hindsight node.
- Do not switch Hindsight production traffic from this TF v2 MVP.
- Do not assume `/v1/models` alone proves model readiness.
- Do not model Hugging Face cache layout as TF v2/oMLX product state.
- Download new MVP models directly into the oMLX default model directory on `gateway-cache-01`, using oMLX's default direct subdirectory format.
- Do not treat `infer-03.lan` as the final data plane. It is stable but slow.
- Do not assume a `.lan` fabric hostname exists. Thunderbolt/fabric interface setup and host mapping are separate explicit tasks.
- Do not enable optional SSD KV cache until baseline generation is stable.
- Use `.lan` hostnames for LAN management and document which hostname is management vs fabric.
- Record compatibility results before promoting any model/runtime path.
- Do not add PostgreSQL, a queue framework, React UI, or MCP before file-backed desired state plus CLI/API stops being sufficient.

## Alternatives Considered

### Keep per-model/per-port services

Rejected for oMLX MVP. It preserves the old shape but fights the runtime. oMLX is more naturally a node daemon.

### Replace Thunder Forge with oMLX directly

Rejected. oMLX is a node inference runtime, not the whole cluster control plane. Thunder Forge still owns cross-node orchestration, caching policy, model placement, and route generation.

### Use Kubernetes/LLMKube-style orchestration

Rejected for MVP. LLMKube is useful as an architectural reference, especially the one-oMLX-daemon-per-Mac lesson, but Kubernetes would add unnecessary complexity for the current homelab cluster.

### Move control plane immediately to `rock`

Rejected for MVP. `gateway-cache-01` is the dev environment and future cache hub. Production `rock` remains stable and untouched during design and early implementation.

### Start by importing/backfilling from a node cache to `gateway-cache-01`

Rejected for the MVP. `gateway-cache-01` is the primary source for artifact movement and the source path is `~/.omlx/models`. Node-local Hugging Face caches are not product state and should not get automated import/backfill logic. If a requested artifact is missing on gateway-cache-01, Thunder Forge should download it directly into gateway-cache-01's oMLX default model directory, then sync that directory to the node.

### Decide the final frontend before measurement

Accepted for MVP direction after measurement, with constraints. The frontend comparison is recorded in `docs/notes/2026-05-26-frontend-balancer-alternatives.md`; the `openziti/llm-gateway` spike validated a tiny OpenAI-compatible proxy with virtual API keys and Prometheus metrics, and the later Olla smoke validated a stronger router/balancer shape: alias rewriting, sticky sessions, failover exclusion, and per-response endpoint attribution.

The selected MVP frontend direction is recorded in `docs/adr/0002-olla-router-with-tf-edge.md`: Olla owns routing/balancing, while a minimal Thunder Forge edge owns root `/v1/*`, static client API-key validation, client identity/accounting envelope, and stable session id behavior. Caddy remains the homelab external ingress and may route both external and internal traffic if uniform ingress becomes useful.

### Adopt a full FastAPI/PostgreSQL/React rebuild immediately

Deferred for MVP. The proposed control-plane shape is directionally useful: nodes, models, placements, reconcile, jobs/audit, route sync, and first-class CLI. But adding PostgreSQL, Alembic, a React/shadcn UI, and a job framework before direct oMLX smoke and dry-run reconcile work would violate KISS/YAGNI. These components should be introduced only when the simpler file-backed CLI/API architecture proves insufficient.
