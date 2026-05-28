# Thunder Forge v2

CLI for managing a self-hosted MLX inference cluster with oMLX serving and Olla routing.

## Architecture

```
Client → Caddy → TF edge → Olla → oMLX nodes (Apple Silicon)
```

- **TF edge** — auth (API key → client identity), session management, proxy to Olla
- **Olla** — model routing, sticky sessions, load balancing, health checks
- **oMLX** — multi-model inference server for Apple Silicon (MLX native)
- **Artifacts** — TF-managed oMLX model dirs preserve HF namespace, e.g. `~/.omlx/models/hf--mlx-community--gpt-oss-20b-MXFP4-Q8`

## Quickstart

```bash
git clone https://github.com/shared-goals/thunder-forge.git
cd thunder-forge
uv sync

# Generate Olla config from the TF cluster config
uv run thunder-forge generate-olla-config

# Run dev smoke test
uv run thunder-forge olla dev-smoke --binary /path/to/olla --model <model> --alias <alias>
```

## Config

Edit `configs/node-assignments.yaml.example`, copy to `configs/node-assignments.yaml`:

```bash
cp configs/node-assignments.yaml.example configs/node-assignments.yaml
```

For TF v2, `runtime_routes` is the operational routing surface. It maps stable public aliases such as `memory`, `coder`, and `agent` to oMLX runtime model ids on nodes. Do not add a second per-node route layer; Olla config generation reads `runtime_routes` directly.

## Topology and Rollout

Current state:

- `msm1`, `msm2`: TF v1 production nodes
- `msm3`: dedicated TF v2 dev node
- `msm4`: direct oMLX node for Hindsight

After `msm3` tests and use cases are stable, migrate nodes into TF v2 in order: `msm1`, then `msm2`, then `msm4`.

## Roles

Canonical role aliases are `memory`, `coder`, and `agent`. Use `memory` for the Hindsight memory LLM; do not introduce a second Hindsight memory alias unless compatibility requires it.

Target production spread on 128 GB nodes:

| Node | Roles | Budget intent |
|------|-------|---------------|
| msm1 | memory + coder | memory around 20 GB runtime RAM; coder around 40-90 GB |
| msm2 | memory + coder | memory around 20 GB runtime RAM; coder around 40-90 GB |
| msm3 | memory + agent | memory around 20 GB runtime RAM; agent around 40-90 GB |
| msm4 | memory + agent | memory around 20 GB runtime RAM; agent around 40-90 GB |

Role placement and routing should preserve no-swap headroom and keep every major role ready. For example, memory traffic should avoid consuming coder-node capacity when healthy memory replicas are available elsewhere.

## Model Selection

Use the in-repo skill at `.github/skills/thunder-forge/SKILL.md` when working on Thunder Forge operations, refactors, or model selection. Prefer SOTA HuggingFace MLX candidates, then reject anything that does not fit the 128 GB no-swap budget after weights, KV cache, MLX overhead, OS headroom, and paired-role capacity are considered.

## Testing

```bash
uv run pytest --tb=short -q
uv run ruff check .
```

## V1

Previous architecture is preserved in `v1/` for reference.
