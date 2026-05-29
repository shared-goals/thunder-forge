# Thunder Forge v2

CLI for managing a self-hosted MLX inference cluster with oMLX serving and Olla routing.

Thunder Forge is part of the [Shared Goals](https://github.com/shared-goals/) platform: private, self-hosted AI infrastructure for working with personal and collective goals without sending sensitive data to cloud APIs.

## Architecture

```
Client → Caddy → TF edge → Olla → oMLX nodes (Apple Silicon)
```

- **TF edge** — auth (API key → client identity), session management, proxy to Olla
- **Olla** — model routing, sticky sessions, load balancing, health checks
- **oMLX** — multi-model inference server for Apple Silicon (MLX native)
- **Artifacts** — oMLX-native model dirs under `~/.omlx/models/<owner>/<repo>`, e.g. `~/.omlx/models/mlx-community/gpt-oss-20b-MXFP4-Q8`

## Shared Goals Vision

Shared Goals starts from a simple loop: people clarify motives and goals, turn them into texts and memory, then use AI agents to help convert that context into coordinated action. Thunder Forge is the local inference layer for that loop. It should let a household, studio, lab, or small group run useful AI capacity on machines they control, with private data staying on-premise.

The current v2 direction is agent-managed operation. Instead of assuming a human runs every `ssh`, `launchctl`, artifact sync, and smoke test by hand, the cluster should be manageable by an operator agent with its own Unix account on the frontend, cache/download host, and inference nodes. That account is the execution identity for routine work: prepare artifacts, sync model caches, regenerate Olla config, restart services, run smokes, and record what happened.

Thunder Forge has three operational roles:

- `frontend`: runs Caddy ingress, TF edge, Olla, routing config, auth, accounting, and external API surface.
- `cache/download`: prepares model artifacts under the oMLX-native `~/.omlx/models/<owner>/<repo>` layout and syncs them to inference nodes.
- `inference node`: runs oMLX as the node-level inference daemon and serves the local model set.

In the current dev setup, `studio` is both `frontend` and `cache/download` on macOS, while `msm1`-`msm4` are macOS inference nodes. In the intended production split, `rock` becomes the Armbian `frontend`, `studio` remains the macOS `cache/download` host, and `msm1`-`msm4` remain macOS inference nodes. The `cache/download` role does not need to be a daemon: it can be an operator script/CLI workflow that uses oMLX or Hugging Face tooling to download models, then syncs them over Thunderbolt fabric when available with Wi-Fi as fallback.

That model keeps the Shared Goals self-hosting principles explicit:

- Prefer self-hosted nodes and self-hosted agents for private domains.
- Give the agent least-privilege access to only the hosts, files, services, and model roles it needs.
- Keep secrets in ignored local environment files or OS keychains, never in the repo.
- Make agent activity auditable through command output, JSONL access logs, launchd state, and test/smoke results.
- Expose OpenAI-compatible APIs through TF edge with client identity, not by exposing raw Olla or oMLX broadly.

## Quickstart

```bash
git clone https://github.com/shared-goals/thunder-forge.git
cd thunder-forge
uv sync

# Create local config/env files
cp configs/node-assignments.yaml.example configs/node-assignments.yaml
cp .env.example .env

# Generate Olla config from the TF cluster config
uv run thunder-forge generate-olla-config

# Install or update the pinned Olla binary into .tmp/olla-bin/olla
make olla-install

# Install/update and restart Olla as a local launchd service on the frontend host
uv run thunder-forge service restart --service olla --binary .tmp/olla-bin/olla --config configs/olla-config.yaml --apply

# Run dev smoke test
uv run thunder-forge olla dev-smoke --binary .tmp/olla-bin/olla --model <model> --alias <alias>

# Generate local TF edge API keys for MVP clients in .env
make edge-keys EDGE_CLIENTS="client-a client-b"

# Run and smoke the TF edge with per-user API keys
uv run thunder-forge edge serve
uv run thunder-forge edge smoke --client-id <client-id> --model memory
uv run thunder-forge edge usage

# Restart a remote node runtime after artifact sync
uv run thunder-forge runtime restart --node msm3 --apply

# One-time durable no-GUI production setup through an admin account
uv run thunder-forge runtime setup-daemon --node msm3 --admin-user <admin> --apply

# Durable production restart after setup
uv run thunder-forge service restart --service omlx --node msm3 --manager daemon --apply

# Reinstall/repair reboot-durable frontend and node daemons, then smoke them
make full-daemon-test EDGE_CLIENT=<client-id>
```

`make olla-restart` is a convenience wrapper for installing the pinned Olla binary and applying the local frontend launchd restart.

## Service Management

`service restart` is the unified operator path for managed Thunder Forge daemons:

- `uv run thunder-forge service restart --service olla --apply` installs or updates the local frontend LaunchAgent and restarts Olla as the current user. This is the default path for `studio`, so `shag` can restart Olla without sudo.
- `uv run thunder-forge service restart --service edge --apply` installs or updates the local frontend TF edge LaunchAgent and restarts it as the current user.
- `uv run thunder-forge service restart --service omlx --node <node> --manager daemon --apply` delegates to the existing node LaunchDaemon workflow after one-time setup.
- Use `--dry-run` first to print the generated plist and shell commands without changing the host.

For reboot-durable system daemons, bootstrap once, then use the reinstall path as often as needed. `make daemon-bootstrap DAEMON_NODES="msm3"` installs gateway Olla/Edge and node oMLX LaunchDaemons through the configured admin accounts, validates sudoers with `visudo -cf`, and writes one narrow Thunder Forge sudoers include on each host at `/etc/sudoers.d/thunder-forge`. After that, `make daemon-reinstall DAEMON_NODES="msm3"` regenerates Olla config and reinstalls/restarts frontend Olla, frontend Edge, and node oMLX with `sudo -n`; no password prompt is expected. `make full-daemon-test EDGE_CLIENT=<client-id>` runs that reinstall path first, then verifies node runtime status, Olla routing, and TF edge auth/proxy smoke.

Run system-daemon install targets from a real terminal, not the VS Code guarded terminal, because macOS sudo password prompts can be blocked by the editor guard:

```bash
cd /Users/shag/Work/thunder-forge
make daemon-bootstrap
make daemon-reinstall
make daemon-smoke EDGE_CLIENT=<client-id>
```

Gateway setup with `services.frontend.admin_user` uses one `su - <admin_user>` shell for both frontend services. The unlabeled `Password:` prompt is `su` asking for that admin account's local macOS login password; after that, Thunder Forge prints a labeled sudo prompt before running the root setup script. Node setup uses the configured node admin account over SSH by default; use `runtime setup-daemon --via-su` only when direct admin SSH is not available. Frontend and node reinstalls then use already-installed narrow `sudo -n` rules for the operator user and should not ask for a password. That means an `msm3` or gateway reinstall can succeed without prompting even though `shag` is not a full sudo user.

For an agent-managed cluster, run these commands as the dedicated operator account. The account should exist on the frontend role, the cache/download host, and every node it manages. On the current dev setup, `studio` holds both frontend and cache/download roles; in production, `rock` should hold the frontend role while `studio` keeps cache/download work close to the Mac inference fabric.

Daemon installation intentionally separates the operator user from the admin user. Configure `nodes.<node>.admin_user` for the account that can run sudo on that node (`admin` on `msm1`-`msm4`), while `nodes.<node>.user` remains the operator/runtime user (`shag`). Configure `services.frontend.admin_user` for frontend system daemons (`serpo` on `studio`). The operator agent should not be a full administrator. The setup flow uses the admin account to install system LaunchDaemons and a narrow sudoers rule for the operator account. After that, normal restarts use `sudo -n` for only the specific install and launchctl commands required by those daemons.

## Local Config

Thunder Forge keeps secrets and operational config separate:

- `.env` is ignored and secrets-only. Keep `HF_TOKEN`, `TF_USERS`, and similar credentials there.
- `tfconfig.yaml` is ignored and is the local source of truth for services, model registry, and node placement.
- `tfconfig.example.yaml` is tracked as the schema/example mirror.
- `configs/` is ignored generated output, currently including `configs/olla-config.yaml`.
- Config node roles are `gateway`, `cache`, and `inference`. Use `roles: [gateway, cache]` for multi-role hosts such as `studio`; use `role: inference` for oMLX-serving nodes.

Create a local config with `cp tfconfig.example.yaml tfconfig.yaml`, then edit the local file for this host.

## Service Ports

Thunder Forge service ports live in `tfconfig.yaml` under `services:`:

| Config key | Default | Service |
|------------|---------|---------|
| `services.olla.port` | `40115` | Local Olla router on the frontend host |
| `services.edge.port` | `40116` | Local TF edge OpenAI-compatible proxy |
| `services.omlx.port` | `8018` | Default oMLX node runtime port when a node runtime omits `port` |
| `services.edge.access_log` | `logs/tf-edge-access.jsonl` | TF edge JSONL accounting log |
| `services.frontend.admin_user` | empty | Admin account used for frontend system-daemon sudo operations |

Explicit CLI flags such as `--port` still win over config defaults, and explicit `nodes.<node>.runtime.port` values still win over the shared oMLX default.

## Runtime Management

`runtime restart` remains available for direct oMLX node operations and supports three managers:

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

The setup script installs the LaunchDaemon, stages a node-user-writable plist copy under `~/.omlx/run`, validates sudoers with `visudo -cf`, and installs `/etc/sudoers.d/thunder-forge` with a narrow include like this for one oMLX daemon on port `8018`:

```sudoers
Cmnd_Alias TF_OMLX_8018_INSTALL = /usr/bin/install -o root -g wheel -m 644 /Users/shag/.omlx/run/com.thunder-forge.omlx-8018.plist /Library/LaunchDaemons/com.thunder-forge.omlx-8018.plist
Cmnd_Alias TF_OMLX_8018_LAUNCHD = /bin/launchctl bootout system/com.thunder-forge.omlx-8018, /bin/launchctl bootstrap system /Library/LaunchDaemons/com.thunder-forge.omlx-8018.plist, /bin/launchctl kickstart -k system/com.thunder-forge.omlx-8018, /bin/launchctl print system/com.thunder-forge.omlx-8018
shag ALL=(root) NOPASSWD: TF_OMLX_8018_INSTALL, TF_OMLX_8018_LAUNCHD
```

Future TF daemon restarts use `sudo -n`, so a missing or invalid setup rule fails instead of prompting or hanging.

## Config

Copy the tracked examples, then edit the local files:

```bash
cp tfconfig.example.yaml tfconfig.yaml
cp .env.example .env
```

For TF v2, `tfconfig.yaml` is the local source of truth. `models.<id>` is the public alias and Thunder Forge model identity. Each model declares `runtime_model_id`, the id exposed by oMLX. Nodes declare which model ids they can serve with `nodes.<node>.models`, and Olla config generation derives endpoints and aliases from that placement. Temporary comparison aliases such as `memory-bf16` may be used for benchmarks, but they are not canonical role names.

Run `uv run thunder-forge config lint` before generating runtime/router config. It catches unknown model assignments, duplicate runtime model ids, benchmark-only placements, invalid runtime ports, and oMLX `0.0.0.0` exposure without `trusted_network: true`.

### Parameter Sources

- Studio artifact root: `.env` key `TF_STUDIO_OMLX_MODELS_DIR`, default `~/.omlx/models`. Artifact `status`, `download`, and `sync` use this path on the machine running the CLI.
- Node oMLX process args: `configs/node-assignments.yaml` under `nodes.<node>.runtime`. `type` and `port` are required; optional keys map directly to `omlx serve` flags: `model_dir`, `bind_host`, `base_path`, `log_level`, `max_concurrent_requests`, `paged_ssd_cache_dir`, `paged_ssd_cache_max_size`, `hot_cache_max_size`, `no_cache`, `mcp_config`, and `hf_endpoint`.
- Olla generated endpoints: `generate-olla-config` reads `nodes.<node>.host`, `nodes.<node>.runtime.port`, and node names. Endpoint names are `<node>-omlx-live`.
- Olla model aliases: generated from `models.<alias>.runtime_model_id` and `nodes.<node>.models`.
- Olla router defaults: still owned by `thunder_forge.cluster.config.generate_olla_config` rather than a YAML schema. Use `olla smoke --expected-endpoint <node>-omlx-live` or `olla dev-smoke --expected-endpoint <node>-omlx-live` when you want smoke tests to pin a specific generated endpoint.

With no `.env` and no TF-specific environment variables, Thunder Forge uses these defaults: studio artifacts under `~/.omlx/models`; omitted node users from `GATEWAY_SSH_USER`, then `$USER`, then `unknown`; and no TF edge clients because `TF_USERS` is empty. Commands that need edge auth, such as `edge smoke`, fail until `TF_USERS` contains the requested client.

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

For the MVP, TF edge API keys are local secrets stored in one ignored `.env` JSON hash named `TF_USERS`. The value maps stable `client_id` names to API keys:

```dotenv
TF_USERS='{"hindsight":"replace-with-long-random-key","codex":"replace-with-long-random-key"}'
```

Prefer generating keys instead of editing them by hand:

```bash
make edge-keys EDGE_CLIENTS="hindsight codex"
```

`edge serve` loads all entries from `TF_USERS` and accepts requests with `Authorization: Bearer <api-key>`. The access log records the mapped `client_id`, model, endpoint, status, and latency, but never the API key. `edge smoke --client-id hindsight` reads that client's key from `TF_USERS`; `make edge-usage` summarizes the JSONL access log.

## Model Selection

Use the in-repo skill at `.github/skills/thunder-forge/SKILL.md` when working on Thunder Forge operations, refactors, or model selection. Prefer SOTA HuggingFace MLX candidates, then reject anything that does not fit the 128 GB no-swap budget after weights, KV cache, MLX overhead, OS headroom, and paired-role capacity are considered.

## Testing

```bash
uv run pytest --tb=short -q
uv run ruff check .
```

## V1

Previous architecture is preserved in `v1/` for reference.
