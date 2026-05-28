# Thunder Forge v2

CLI for managing a local MLX inference cluster (oMLX + Olla routing layer).

## Commands

```bash
uv sync                                          # Install dependencies
uv run thunder-forge --help                      # CLI help
uv run thunder-forge artifact download --model <hf-repo> --apply  # Download model to studio
uv run thunder-forge artifact sync --model <hf-repo> --node msm3 --apply  # Sync to node
uv run thunder-forge runtime smoke --node msm3 --model <model>  # Direct oMLX smoke
uv run thunder-forge olla dev-smoke --binary <path> --model <model> --alias <alias>  # Olla smoke
uv run thunder-forge generate-olla-config --apply  # Generate olla-config.yaml from node-assignments
uv run thunder-forge generate-config --apply     # Generate litellm-config.yaml
uv run thunder-forge edge serve                  # TF edge (auth + proxy to Olla)
```

## Architecture

```
Client → Caddy → TF edge (auth/identity/session) → Olla (routing/balancer/sticky) → oMLX nodes
```

- **TF edge**: auth (static API key → client identity), session ID, proxy to Olla, JSONL access log
- **Olla**: model alias routing, sticky sessions, health checks, least-connections balancer
- **oMLX**: multi-model serving with LRU, Apple Silicon native MLX inference
- **HF models**: downloaded to `~/.omlx/models/<model-id>/` on studio, synced to nodes via rsync

## Node Roles

| Node | Role | Notes |
|------|------|-------|
| studio | Dev control plane + cache hub | Downloads, syncs, TF edge |
| msm3 | TF v2 dev node | oMLX + Olla routing |
| msm4 | Dedicated Hindsight | Direct oMLX, separate config |
| rock | Production infra | Do not touch for TF v2 dev |

## Config

- `configs/node-assignments.yaml` — model registry, node definitions, assignments, runtime_routes
- `configs/olla-config.yaml` — auto-generated from node-assignments (Olla router config)
- `configs/*.yaml` gitignored (per-cluster settings); `*.yaml.example` in git

## Testing

```bash
uv run pytest --tb=short -q    # All tests
uv run ruff check .            # Lint
```

## V1 Reference

Old v1 architecture (Streamlit admin, LiteLLM, Docker, vllm-mlx) is in `v1/` for reference.
