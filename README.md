# Thunder Forge v2

CLI for managing a self-hosted MLX inference cluster with oMLX serving and Olla routing.

## Architecture

```
Client → Caddy → TF edge → Olla → oMLX nodes (Apple Silicon)
```

- **TF edge** — auth (API key → client identity), session management, proxy to Olla
- **Olla** — model routing, sticky sessions, load balancing, health checks
- **oMLX** — multi-model inference server for Apple Silicon (MLX native)
- **Artifacts** — oMLX-native model dirs under `~/.omlx/models/<owner>/<repo>`, e.g. `~/.omlx/models/mlx-community/gpt-oss-20b-MXFP4-Q8`

## Quickstart

```bash
git clone https://github.com/shared-goals/thunder-forge.git
cd thunder-forge
uv sync

# Generate Olla config from the TF cluster config
uv run thunder-forge generate-olla-config

# Run dev smoke test
uv run thunder-forge olla dev-smoke --binary /path/to/olla --model <model> --alias <alias>

# Generate local TF edge API keys for MVP clients in .env
make edge-keys EDGE_CLIENTS="client-a client-b"

# Run and smoke the TF edge with per-user API keys
uv run thunder-forge edge serve --olla-base-url http://127.0.0.1:40115
uv run thunder-forge edge smoke --base-url http://127.0.0.1:40116 --client-id <client-id> --model memory
uv run thunder-forge edge usage

# Restart a remote node runtime after artifact sync
uv run thunder-forge runtime restart --node msm3 --apply

# One-time durable no-GUI production setup through an admin account
uv run thunder-forge runtime setup-daemon --node msm3 --admin-user <admin> --apply

# Durable production restart after setup
uv run thunder-forge runtime restart --node msm3 --manager daemon --apply
```

## Runtime Management

`runtime restart` supports three managers:

- `process` (default): rootless SSH control. It stops any existing oMLX process on the node port, starts `omlx serve` as the node user with `nohup`, writes `~/.omlx/run/omlx-<port>.pid`, and health-checks the runtime. This works without a GUI session and without sudo, but it is not reboot durable.
- `daemon`: production system launchd control. It stages a plist under `~/.omlx/run`, installs `/Library/LaunchDaemons/com.thunder-forge.omlx-<port>.plist` with `sudo -n install`, and manages `system/com.thunder-forge.omlx-<port>` with `sudo -n launchctl`. The daemon runs as the configured node user via `UserName` and survives logout/reboot.
- `launchd`: user LaunchAgent control. This is useful only when the remote user launchd domain accepts the service; on headless SSH sessions macOS may reject `gui/<uid>` and `user/<uid>` LaunchAgent bootstraps.

For production nodes, prefer `--manager daemon` after node setup grants only the required non-interactive sudo commands. Use the default `process` manager for dev recovery and immediate no-sudo operation.

`runtime setup-daemon` is the one-time setup path. By default it prints the generated node-side admin script and remote commands. With `--apply`, it copies the script to the node and runs it through an admin account:

```bash
uv run thunder-forge runtime setup-daemon --node msm3 --admin-user <admin> --apply
```

If the admin account is not reachable over SSH but can be reached from the node user with `su`, use:

```bash
uv run thunder-forge runtime setup-daemon --node msm3 --admin-user <admin> --via-su --apply
```

The setup script installs the LaunchDaemon, stages a node-user-writable plist copy under `~/.omlx/run`, validates sudoers with `visudo -cf`, and installs a narrow sudoers include like this for one oMLX daemon on port `8018`:

```sudoers
Cmnd_Alias TF_OMLX_8018_INSTALL = /usr/bin/install -o root -g wheel -m 644 /Users/shag/.omlx/run/com.thunder-forge.omlx-8018.plist /Library/LaunchDaemons/com.thunder-forge.omlx-8018.plist
Cmnd_Alias TF_OMLX_8018_LAUNCHD = /bin/launchctl bootout system/com.thunder-forge.omlx-8018, /bin/launchctl bootstrap system /Library/LaunchDaemons/com.thunder-forge.omlx-8018.plist, /bin/launchctl kickstart -k system/com.thunder-forge.omlx-8018, /bin/launchctl print system/com.thunder-forge.omlx-8018
shag ALL=(root) NOPASSWD: TF_OMLX_8018_INSTALL, TF_OMLX_8018_LAUNCHD
```

Future TF daemon restarts use `sudo -n`, so a missing or invalid setup rule fails instead of prompting or hanging.

## Config

Edit `configs/node-assignments.yaml.example`, copy to `configs/node-assignments.yaml`:

```bash
cp configs/node-assignments.yaml.example configs/node-assignments.yaml
```

For TF v2, `models.<id>` is the public alias and Thunder Forge model identity. Each model declares `runtime_model_id`, the id exposed by oMLX. Nodes declare which model ids they can serve with `nodes.<node>.models`, and Olla config generation derives endpoints and aliases from that placement. Temporary comparison aliases such as `memory-bf16` may be used for benchmarks, but they are not canonical role names.

## Topology and Rollout

Current state:

- `msm1`, `msm2`: TF v1 production nodes
- `msm3`: dedicated TF v2 dev node
- `msm4`: direct oMLX node for Hindsight

After `msm3` tests and use cases are stable, migrate nodes into TF v2 in order: `msm1`, then `msm2`, then `msm4`.

## Roles

Canonical role aliases are `memory`, `coder`, and `agent`. Use `memory` for the Hindsight memory LLM; do not introduce a second Hindsight memory alias unless compatibility requires it. Benchmark-only aliases such as `memory-bf16` should stay clearly marked and temporary.

Target production spread on 128 GB nodes:

| Node | Roles | Budget intent |
|------|-------|---------------|
| msm1 | memory + coder | memory around 20 GB runtime RAM; coder around 40-90 GB |
| msm2 | memory + coder | memory around 20 GB runtime RAM; coder around 40-90 GB |
| msm3 | memory + agent | memory around 20 GB runtime RAM; agent around 40-90 GB |
| msm4 | memory + agent | memory around 20 GB runtime RAM; agent around 40-90 GB |

Role placement and routing should preserve no-swap headroom and keep every major role ready. For example, memory traffic should avoid consuming coder-node capacity when healthy memory replicas are available elsewhere.

## Edge Users

For the MVP, TF edge API keys are local secrets stored in one ignored `.env` JSON hash named `TF_USERS`. Generate the local client map with `make edge-keys EDGE_CLIENTS="client-a client-b"`. TF edge logs JSONL accounting records without secrets; `make edge-usage` summarizes request load by `client_id`, model, endpoint, failures, and latency percentiles.

## Model Selection

Use the in-repo skill at `.github/skills/thunder-forge/SKILL.md` when working on Thunder Forge operations, refactors, or model selection. Prefer SOTA HuggingFace MLX candidates, then reject anything that does not fit the 128 GB no-swap budget after weights, KV cache, MLX overhead, OS headroom, and paired-role capacity are considered.

## Testing

```bash
uv run pytest --tb=short -q
uv run ruff check .
```

## V1

Previous architecture is preserved in `v1/` for reference.
