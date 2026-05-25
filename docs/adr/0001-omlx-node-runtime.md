# ADR 0001: oMLX as Node-Level Runtime for Thunder Forge v2

## Status

Accepted for MVP design.

## Context

The current Thunder Forge implementation on `rock` is a working production system and should be treated as a library of proven procedures, not as disposable legacy. It contains useful implementation patterns for:

- LiteLLM configuration generation;
- SSH-based node operations;
- launchd service management;
- model download/sync flows;
- direct and routed health checks;
- config-driven cluster state.

However, the current runtime model is centered around one service per model/port. That shape is awkward for oMLX, because oMLX is designed as a local Apple Silicon inference server that can discover and serve multiple models from a model directory, with model loading/unloading, continuous batching, and cache management handled inside the node runtime.

The next Thunder Forge version starts in a dev environment:

```text
shag@studio:/Users/shag/Work/thunder-forge
```

Production `rock` must not be used for dev work. `studio` is the dev frontend and future cache hub. `msm3` is the first TF v2 development inference node. `msm4` is dedicated to direct oMLX/Hindsight work and should not be disturbed by TF v2 experiments. Later, the control plane may move back to Armbian `rock`; `studio` may remain the model cache hub due to its Thunderbolt connection to `msm1`-`msm4`.

The very first use case is narrower than full model preparation: run oMLX against an already cached model folder on `msm3`. The cache is under `shag@msm3` and contains several `mlx-community/Qwen3*` artifacts. `gpt-oss-20b` is currently the Hindsight model on `msm4`, not the first TF v2 dev target.

The long-term product target is a controlled compute resource for Shared Goals platform, `whattodo`, and `text-forge` tasks. Thunder Forge should expose enough operational facts for agent-driven daily operations: request load, caller/API-key identity, workload identity, model version, node utilization, failures, latency, and model freshness. That does not require a web UI in the MVP; a strict CLI/API path is the smaller and more controllable interface.

The network path has two distinct roles:

- `msm3-wifi.lan` is stable and suitable for management/bootstrap, but slow for large model movement. It belongs to the normal LAN/DNS world.
- Future Thunderbolt/fabric links are point-to-point between `studio` and inference nodes, so they should not assume Keenetic `.lan` DNS. A local alias such as `msm3-fabric`/`studio-fabric-msm3` can be provided via `/etc/hosts`, mDNS, or another macOS-native host mapping after the interface is configured.

## Decision

Thunder Forge v2 will model oMLX as a **node-level runtime daemon**, not as a per-model/per-port serving mode.

The MVP architecture is:

```text
Thunder Forge = control plane and orchestration
LiteLLM       = optional frontend/router after direct runtime health is proven
studio        = dev control plane + future cache hub + optional LiteLLM dev gateway
msm3          = first TF v2 development oMLX inference node
msm4          = dedicated direct oMLX/Hindsight node, excluded from dev experiments
oMLX          = node-local inference runtime daemon
```

For the MVP, the model is not hardcoded. It is chosen from the existing `shag@msm3` Hugging Face cache for a direct oMLX smoke test. The current initial candidates are:

```text
mlx-community/Qwen3-Embedding-0.6B-4bit-DWQ
mlx-community/Qwen3-Coder-30B-A3B-Instruct-4bit
mlx-community/Qwen3-30B-A3B-4bit
mlx-community/Qwen3.5-35B-A3B-4bit
mlx-community/Qwen3.6-35B-A3B-4bit
mlx-community/Qwen3-Coder-Next-4bit
mlx-community/Qwen3.5-122B-A10B-4bit
```

The first implementation milestone is direct oMLX execution from the existing `shag@msm3` model cache. Downloading to `studio` and syncing over Thunderbolt comes after the existing-cache path is understood.

Thunder Forge v2 will prefer strict, auditable operator channels in this order:

1. CLI with stable JSON and explicit dry-run/apply semantics.
2. HTTP API when the CLI/service boundary becomes useful.
3. MCP only if Hermes integration needs a first-class tool surface.
4. Web UI only if it clearly improves safe inspection or manual operations.

## Consequences

### Positive

- Keeps Thunder Forge's cluster/control-plane role clear.
- Avoids forcing oMLX into the old one-model/one-port abstraction.
- Preserves working production `rock` while dev proceeds on `studio`.
- Keeps Hindsight stable by reserving `msm4` for direct oMLX/Hindsight.
- Reduces MVP risk by trying an already cached MLX artifact before building download/sync automation.
- Separates stable LAN management networking from future high-speed point-to-point Thunderbolt/fabric networking.
- Makes later task-oriented model selection natural:

  ```text
  task -> compatible model -> cache -> node -> runtime -> LiteLLM route
  ```

- Allows oMLX to own local inference concerns: batching, model loading, cache behavior, Harmony handling, and supported API surfaces.
- Keeps public documentation framed around operator expectations and system behavior rather than personal user stories.

### Negative / Trade-offs

- Existing deployment code cannot be reused mechanically; it must be abstracted around runtime type.
- Health checks need to distinguish node runtime health from individual model readiness.
- LiteLLM route generation must allow many logical model names to point at one node-level base URL.
- Model cache/sync semantics become more important than port allocation.
- The first milestone still depends on the exact Hugging Face snapshot layout and whether oMLX accepts that cache path directly or needs a resolved snapshot/model directory.
- Thunderbolt fabric setup and host mapping become separate prerequisites for the later cache-hub flow.
- Early experiments should use an isolated dev port, preferably `8018`, until runtime ownership and launchd/service management are represented explicitly in Thunder Forge config.

## Schema Direction

The old model-level serving field can remain for compatibility, but oMLX should be represented at node/runtime level.

Possible future shape:

```yaml
nodes:
  msm3:
    host: msm3-wifi.lan          # stable management/bootstrap path
    fabric_host: msm3-fabric     # desired point-to-point alias, once configured outside LAN DNS
    role: inference
    runtime:
      type: omlx
      base_url: http://msm3-wifi.lan:8018
      fabric_base_url: http://msm3-fabric:8018
      model_dir: /Users/shag/.cache/thunder-forge/models
      api_key_env: TF_NODE_API_KEY

models:
  qwen-dev:
    source:
      type: huggingface
      repo: mlx-community/Qwen3.6-35B-A3B-4bit
    runtime_artifact:
      repo: mlx-community/Qwen3.6-35B-A3B-4bit
      cache_path: /Users/shag/.cache/huggingface/hub/models--mlx-community--Qwen3.6-35B-A3B-4bit
      status: inspect-first
    intended_workloads:
      - tf-v2-dev-smoke
    runtime_compat:
      omlx: unknown

assignments:
  msm3:
    models:
      - qwen-dev
```

The exact field names are not final. The important architectural distinction is that the node has both stable LAN management identity and future point-to-point fabric identity.

## MVP Guardrails

- Do not modify production `rock` for dev work.
- Do not disturb `msm4`; it is the dedicated direct oMLX/Hindsight node.
- Do not switch Hindsight production traffic from this TF v2 MVP.
- Do not assume `/v1/models` alone proves model readiness.
- Do not download another copy of any MVP model until the existing `shag@msm3` cache has been inspected.
- Do not assume the Hugging Face cache root is directly accepted by oMLX; resolve the snapshot/model directory if needed.
- Do not treat `msm3-wifi.lan` as the final data plane. It is stable but slow.
- Do not assume a `.lan` fabric hostname exists. Thunderbolt/fabric interface setup and host mapping are separate explicit tasks.
- Do not enable optional SSD KV cache until baseline generation is stable.
- Use `.lan` hostnames for LAN management and document which hostname is management vs fabric.
- Record compatibility results before promoting any model/runtime path.
- Do not add PostgreSQL, a queue framework, React UI, or MCP before file-backed desired state plus CLI/API stops being sufficient.

## Alternatives Considered

### Keep per-model/per-port services

Rejected for oMLX MVP. It preserves the old shape but fights the runtime. oMLX is more naturally a node daemon.

### Replace Thunder Forge with oMLX directly

Rejected. oMLX is a node inference runtime, not the whole cluster control plane. Thunder Forge still owns cross-node orchestration, caching policy, model placement, and LiteLLM integration.

### Use Kubernetes/LLMKube-style orchestration

Rejected for MVP. LLMKube is useful as an architectural reference, especially the one-oMLX-daemon-per-Mac lesson, but Kubernetes would add unnecessary complexity for the current homelab cluster.

### Move control plane immediately to `rock`

Rejected for MVP. `studio` is the dev environment and future cache hub. Production `rock` remains stable and untouched during design and early implementation.

### Start by downloading from Hugging Face to `studio`

Deferred. It is the right later cache-hub flow, but the first useful proof is simpler: run oMLX with an already cached model on `msm3`, then generalize to studio cache and Thunderbolt sync.

### Adopt a full FastAPI/PostgreSQL/React rebuild immediately

Deferred for MVP. The proposed control-plane shape is directionally useful: nodes, models, placements, reconcile, jobs/audit, LiteLLM sync, and first-class CLI. But adding PostgreSQL, Alembic, a React/shadcn UI, and a job framework before direct oMLX smoke and dry-run reconcile work would violate KISS/YAGNI. These components should be introduced only when the simpler file-backed CLI/API architecture proves insufficient.
