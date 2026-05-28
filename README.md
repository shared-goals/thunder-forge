# Thunder Forge v2

CLI for managing a self-hosted MLX inference cluster with oMLX serving and Olla routing.

## Architecture

```
Client → Caddy → TF edge → Olla → oMLX nodes (Apple Silicon)
```

- **TF edge** — auth (API key → client identity), session management, proxy to Olla
- **Olla** — model routing, sticky sessions, load balancing, health checks
- **oMLX** — multi-model inference server for Apple Silicon (MLX native)

## Quickstart

```bash
git clone https://github.com/shared-goals/thunder-forge.git
cd thunder-forge
uv sync

# Generate Olla config from node-assignments
uv run thunder-forge generate-olla-config --apply

# Run dev smoke test
uv run thunder-forge olla dev-smoke --binary /path/to/olla --model <model> --alias <alias>
```

## Config

Edit `configs/node-assignments.yaml.example`, copy to `configs/node-assignments.yaml`:

```bash
cp configs/node-assignments.yaml.example configs/node-assignments.yaml
```

## Testing

```bash
uv run pytest --tb=short -q
uv run ruff check .
```

## V1

Previous architecture (Streamlit admin, LiteLLM, Docker) is preserved in `v1/` for reference.
