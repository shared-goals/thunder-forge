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

Shared Goals starts from a simple loop: people clarify motives and goals, turn them into texts and memory, then use AI agents to help convert that context into coordinated action. Thunder Forge is the local inference layer for that loop. It should let a household, workshop, lab, or small group run useful AI capacity on machines they control, with private data staying on-premise.

The current v2 direction is agent-managed operation. Instead of assuming a human runs every `ssh`, `launchctl`, artifact sync, and smoke test by hand, the cluster should be manageable by an operator agent with its own Unix account on the frontend, cache/download host, and inference nodes. That account is the execution identity for routine work: prepare artifacts, sync model caches, regenerate Olla config, restart services, run smokes, and record what happened.

Thunder Forge has three operational roles:

- `frontend`: runs Caddy ingress, TF edge, Olla, routing config, auth, accounting, and external API surface.
- `cache/download`: prepares model artifacts under the oMLX-native `~/.omlx/models/<owner>/<repo>` layout and syncs them to inference nodes.
- `inference node`: runs oMLX as the node-level inference daemon and serves the local model set.

In a compact development setup, one host such as `gateway-cache-01` can hold both the `gateway` and `cache/download` roles while `infer-01`-`infer-04` are inference nodes. In a split production setup, `gateway-01` runs ingress, TF edge, and Olla, while `cache-01` prepares model artifacts close to the inference fabric. The `cache/download` role does not need to be a daemon: it can be an operator script/CLI workflow that uses oMLX or Hugging Face tooling to download models, then syncs them over Thunderbolt fabric when available with Wi-Fi as fallback.

That model keeps the Shared Goals self-hosting principles explicit:

- Prefer self-hosted nodes and self-hosted agents for private domains.
- Give the agent least-privilege access to only the hosts, files, services, and model roles it needs.
- Keep secrets in ignored local environment files or OS keychains, never in the repo.
- Make agent activity auditable through command output, JSONL access logs, launchd state, and test/smoke results.
- Expose OpenAI-compatible APIs through TF edge with client identity, not by exposing raw Olla or oMLX broadly.

## Prerequisites

The operator user (e.g. `shag`) must have **passwordless SSH access** to every node in the cluster before running any Thunder Forge setup or management commands:

```bash
# Verify access to each node
ssh infer-01 true && echo ok
```

If key auth is not yet configured:
```bash
ssh-copy-id infer-01   # or ssh-copy-id user@host
```

Thunder Forge always SSHes as the operator user. Privilege escalation (via `su` or `sudo`) is performed on the remote node — the operator user is never required to SSH as the admin user directly.

## Quickstart

```bash
git clone https://github.com/shared-goals/thunder-forge.git
cd thunder-forge
uv sync

# Create local config/env files
cp tfconfig.example.yaml tfconfig.yaml
# edit tfconfig.yaml for your cluster

# Generate Olla config from the TF cluster config
uv run thunder-forge generate-olla-config

# One-time bootstrap: install gateway (Olla + Edge), cache hub, and node (oMLX) LaunchDaemons
# Operator user must have passwordless SSH to all nodes first (see Prerequisites)
make bootstrap           # gateway + cache + inference nodes
make bootstrap gateway-cache-01    # combined gateway/cache host only
make bootstrap infer-01            # inference node only

# Restart daemons after config changes (passwordless via installed sudoers)
make restart

# Smoke-test the cluster
make smoke

# Check node runtime status
make status
```

## Service Management

The Makefile is a thin dispatcher for cluster-level CLI commands:

- `make bootstrap [node]` -> `uv run thunder-forge cluster prepare [node] --apply`
- `make restart [node]` -> `uv run thunder-forge cluster restart [node] --apply`
- `make smoke [node]` -> `uv run thunder-forge cluster smoke [node] ...`
- `make status [node]` -> `uv run thunder-forge cluster status [node]`
- `make sync [node]` -> `uv run thunder-forge cluster sync [node] --apply`, syncing configured models and following `operations.sync.restart_runtime` for the post-sync oMLX restart.

`service restart` remains the lower-level per-service path for managed Thunder Forge daemons:

- `uv run thunder-forge service restart --service olla --apply` installs or updates the local gateway LaunchAgent and restarts Olla as the current user. This is the default path for a macOS gateway/cache host such as `gateway-cache-01`, so the operator user can restart Olla without sudo.
- `uv run thunder-forge service restart --service edge --apply` installs or updates the local frontend TF edge LaunchAgent and restarts it as the current user.
- `uv run thunder-forge service restart --service omlx --node <node> --manager daemon --apply` delegates to the existing node LaunchDaemon workflow after one-time setup.
- Use `--dry-run` first to print the generated plist and shell commands without changing the host.

For reboot-durable system daemons, bootstrap once with `make bootstrap`, then use `make restart` for all subsequent updates. Bootstrap installs the pinned Olla binary, generates Olla config, prepares the local oMLX model cache hub, installs user-local `uv`/oMLX tooling on inference nodes when missing, installs gateway Olla/Edge and node oMLX LaunchDaemons through the configured admin accounts, validates sudoers with `visudo -cf`, and writes one narrow Thunder Forge sudoers include on each host at `/etc/sudoers.d/thunder-forge`. After that, `make restart` regenerates Olla config and reinstalls/restarts all services with `sudo -n`; no password prompt is expected.

After changing model placement or node topology in `tfconfig.yaml`, run `make restart gateway-cache-01` (or full `make restart`) before `make smoke <node>` so Olla and TF edge reload the generated router config.

Bootstrap verifies minimal service readiness only: Olla `/internal/health`, TF edge auth boundary health, and direct oMLX `/health`. It does not require inference models or chat to be ready; use `make smoke` for model visibility, routing, and chat checks after the cluster has warmed up.

Run system-daemon install targets from a real terminal, not the VS Code guarded terminal, because macOS sudo/su password prompts can be blocked by the editor guard:

```bash
cd /path/to/thunder-forge
make bootstrap                 # first time: prompts for admin passwords
make restart                   # subsequent: passwordless via installed sudoers
make smoke                     # verify cluster health
```

**Bootstrap escalation modes** (operator user always SSHes, escalation runs on the remote):

- **Gateway** (`gateway-cache-01`): the local operator runs `su - <admin_user>`, then admin uses `sudo` to run the setup script.
- **Inference nodes**: Thunder Forge SSHes as `nodes.<node>.user`, then uses `su - nodes.<node>.admin_user` so admin can `sudo` run the setup script. If a node has no admin user configured, setup falls back to direct sudo as the operator user.

Every password notice is printed before macOS asks for input and includes `host`, `method`, `user`, and `reason`, for example:

```text
[infer-01.lan] password prompt: method=su user=admin reason=bootstrap Thunder Forge oMLX daemon com.thunder-forge.omlx-8018
[%h] password: user=admin reason=install Thunder Forge oMLX daemon com.thunder-forge.omlx-8018:
```

After bootstrap, node restarts use already-installed narrow `sudo -n` rules for the operator user and should not ask for a password.

For an agent-managed cluster, run these commands as the dedicated operator account. The account should exist on the gateway role, the cache/download host, and every node it manages. In a compact setup, `gateway-cache-01` can hold both gateway and cache/download roles; in a split setup, `gateway-01` holds ingress/TF edge/Olla while `cache-01` keeps cache/download work close to the inference fabric.

Daemon installation intentionally separates the operator user from the admin user. Configure `nodes.<node>.admin_user` for the account that can run sudo on that node, while `nodes.<node>.user` remains the operator/runtime user. Configure `services.frontend.admin_user` for gateway system daemons. The operator agent should not be a full administrator. The setup flow uses the admin account to install system LaunchDaemons and a narrow sudoers rule for the operator account. After that, normal restarts use `sudo -n` for only the specific install and launchctl commands required by those daemons.

## Local Config

Thunder Forge keeps secrets and operational config separate:

- `.env` is ignored and secrets-only. Keep `HF_TOKEN`, `TF_USER_<CLIENT>`, and similar credentials there.
- `tfconfig.yaml` is ignored and is the local source of truth for services, operator defaults, model registry, and node placement.
- `tfconfig.example.yaml` is tracked as the schema/example mirror.
- `configs/` is ignored generated output, currently including `configs/olla-config.yaml`.
- Config node roles are `gateway`, `cache`, and `inference`. Use `roles: [gateway, cache]` for multi-role hosts such as `gateway-cache-01`; use `role: inference` for oMLX-serving nodes such as `infer-01`.

Create a local config with `cp tfconfig.example.yaml tfconfig.yaml`, then edit the local file for this host.

## Service Ports

Thunder Forge service ports live in `tfconfig.yaml` under `services:`:

| Config key | Default | Service |
|------------|---------|---------|
| `services.olla.port` | `40115` | Local Olla router on the frontend host |
| `services.olla.version` | `v0.0.27` | Olla release used by `cluster prepare` |
| `services.olla.bin_dir` | `.tmp/olla-bin` | Local Olla binary install directory |
| `services.edge.host` | `0.0.0.0` | TF edge bind address; use `0.0.0.0` for LAN clients, keep raw Olla private |
| `services.edge.port` | `40116` | Local TF edge OpenAI-compatible proxy |
| `services.omlx.port` | `8018` | Default oMLX node runtime port when a node runtime omits `port` |
| `services.edge.access_log` | `logs/tf-edge-access.jsonl` | TF edge JSONL accounting log |
| `services.frontend.admin_user` | empty | Admin account used for frontend system-daemon sudo operations |

Explicit CLI flags such as `--port` still win over config defaults, and explicit `nodes.<node>.runtime.port` values still win over the shared oMLX default.

## Operator Defaults

Non-secret Make/CLI defaults live in `tfconfig.yaml` under `operations:`. `operations.smoke.alias` and `operations.smoke.client_id` let `make smoke <node>` run without model IDs in the Makefile; when `operations.smoke.model` is omitted, Thunder Forge resolves the backend runtime model id from the configured alias. `operations.sync.transport`, `operations.sync.timeout`, and `operations.sync.restart_runtime` drive `make sync <node>`.

## Runtime Management

`runtime restart` remains available for direct oMLX node operations and supports three managers:

- `process` (default): rootless SSH control. It stops any existing oMLX process on the node port, starts `omlx serve` as the node user with `nohup`, writes `~/.omlx/run/omlx-<port>.pid`, and health-checks the runtime. This works without a GUI session and without sudo, but it is not reboot durable.
- `daemon`: production system launchd control. It stages a plist under `~/.omlx/run`, installs `/Library/LaunchDaemons/com.thunder-forge.omlx-<port>.plist` with `sudo -n install`, and manages `system/com.thunder-forge.omlx-<port>` with `sudo -n launchctl`. The daemon runs as the configured node user via `UserName` and survives logout/reboot.
- `launchd`: user LaunchAgent control. This is useful only when the remote user launchd domain accepts the service; on headless SSH sessions macOS may reject `gui/<uid>` and `user/<uid>` LaunchAgent bootstraps.

For production nodes, prefer `--manager daemon` after node setup grants only the required non-interactive sudo commands. Use the default `process` manager for dev recovery and immediate no-sudo operation.

`cluster prepare` is the unified one-time setup path for the pre-MVP cluster. It prints a plan, then applies phases in this order: gateway tooling, gateway daemons, cache hub, inference daemons. Use the lower-level `runtime setup-daemon` command only when working on one node directly. By default it prints the generated node-side admin script and remote commands. With `--apply`, it copies the script to the node and runs it through an admin account:

```bash
uv run thunder-forge runtime setup-daemon --node infer-01 --admin-user <admin> --apply
```

If the admin account is not reachable over SSH but can be reached from the node user with `su`, use:

```bash
uv run thunder-forge runtime setup-daemon --node infer-01 --admin-user <admin> --via-su --apply
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

- Cache artifact root: `.env` key `TF_CACHE_OMLX_MODELS_DIR`, default `~/.omlx/models`. Artifact `status`, `download`, and `sync` use this path on the machine running the CLI.
- Node oMLX process args: `configs/node-assignments.yaml` under `nodes.<node>.runtime`. `type` and `port` are required; optional keys map directly to `omlx serve` flags: `model_dir`, `bind_host`, `base_path`, `log_level`, `max_model_memory`, `max_process_memory`, `max_concurrent_requests`, `paged_ssd_cache_dir`, `paged_ssd_cache_max_size`, `hot_cache_max_size`, `no_cache`, `mcp_config`, and `hf_endpoint`.
- Olla generated endpoints: `generate-olla-config` reads `nodes.<node>.host`, `nodes.<node>.runtime.port`, and node names. Endpoint names are `<node>-omlx-live`.
- Olla model aliases: generated from `models.<alias>.runtime_model_id` and `nodes.<node>.models`.
- Olla router defaults: still owned by `thunder_forge.cluster.config.generate_olla_config` rather than a YAML schema. Use `olla smoke --expected-endpoint <node>-omlx-live` or `olla dev-smoke --expected-endpoint <node>-omlx-live` when you want smoke tests to pin a specific generated endpoint.

With no `.env` and no TF-specific environment variables, Thunder Forge uses these defaults: cache artifacts under `~/.omlx/models`; omitted node users from `GATEWAY_SSH_USER`, then `$USER`, then `unknown`; and no TF edge clients because no `TF_USER_<CLIENT>` entries are set. Commands that need edge auth, such as `edge smoke`, fail until the requested client has a matching key such as `TF_USER_HINDSIGHT`.

## Topology and Rollout

Example rollout state:

- `infer-01`, `infer-02`: existing production inference nodes to migrate after the first TF v2 proof
- `infer-03`: dedicated TF v2 development inference node
- `infer-04`: direct oMLX node reserved for an existing workload until TF v2 is ready

After `infer-03` tests and use cases are stable, migrate nodes into TF v2 in order: `infer-01`, then `infer-02`, then `infer-04`.

## Roles

Canonical role aliases are `memory`, `coder`, and `agent`. Use `memory` for the Hindsight memory LLM; do not introduce a second Hindsight memory alias unless compatibility requires it. Benchmark-only aliases such as `memory-bf16` should stay clearly marked and temporary.

Target production spread on 128 GB nodes:

| Node | Roles | Budget intent |
|------|-------|---------------|
| infer-01 | memory + coder | memory around 20 GB runtime RAM; coder around 40-90 GB |
| infer-02 | memory + coder | memory around 20 GB runtime RAM; coder around 40-90 GB |
| infer-03 | memory + agent | memory around 20 GB runtime RAM; agent around 40-90 GB |
| infer-04 | memory + agent | memory around 20 GB runtime RAM; agent around 40-90 GB |

Role placement and routing should preserve no-swap headroom and keep every major role ready. For example, memory traffic should avoid consuming coder-node capacity when healthy memory replicas are available elsewhere.

## Edge Users

TF edge API keys are local secrets stored in ignored `.env` lines named `TF_USER_<CLIENT>`. The suffix maps to a stable `client_id`, so `TF_USER_OPENCODE` authenticates requests as `opencode`:

```dotenv
TF_USER_OPENCODE=replace-with-long-random-key
TF_USER_HINDSIGHT=replace-with-long-random-key
```

Prefer generating keys instead of editing them by hand:

```bash
make edge-keys EDGE_CLIENTS="opencode hindsight"
```

`edge serve` loads all `TF_USER_<CLIENT>` entries and accepts requests with `Authorization: Bearer <api-key>`. The access log records the mapped `client_id`, model, endpoint, status, and latency, but never the API key. `edge smoke --client-id hindsight` reads that client's key from `TF_USER_HINDSIGHT`; `make edge-usage` summarizes the JSONL access log.

### OpenCode Provider

TF edge owns the client-facing model catalog. Authenticated `GET /v1/models` returns Thunder Forge public aliases such as `coder` and `agent-better`, not raw oMLX runtime ids. Each model object includes a `description` containing the underlying model id/repo plus TF metadata fields such as `tf_runtime_model_id` and `tf_source_repo`. Raw Olla still reports backend runtime ids from discovery, so clients that should choose TF aliases should call TF edge, not Olla directly.

OpenCode custom providers require a `provider.<id>.models` map for the `/models` picker. TF edge remains the source of truth for what aliases exist and what real model ids they route to, but the OpenCode config needs a generated snapshot of those aliases. The generated map includes every configured model alias assigned to an inference node, including benchmark aliases such as `memory-bf16`. Model keys and `name` values stay as TF aliases; the generated JSONC comments show the underlying repo/runtime id. Add top-level `model` or `small_model` only when you want OpenCode defaults such as `thunder-forge/coder` or `thunder-forge/memory`.

```jsonc
{
	"$schema": "https://opencode.ai/config.json",
	"provider": {
		"thunder-forge": {
			"npm": "@ai-sdk/openai-compatible",
			"name": "Thunder Forge",
			"options": {
				"baseURL": "http://gateway-01.lan:40116/v1",
				// TF_USER_OPENCODE: check .env
				"apiKey": "{env:TF_USER_OPENCODE}"
			},
			"models": {
				// mlx-community/gpt-oss-20b-MXFP4-Q8
				"memory": {
					"name": "memory"
				},
				// mlx-community/gpt-oss-20b-mxfp4-bf16
				"memory-bf16": {
					"name": "memory-bf16",
					"status": "beta"
				},
				// mlx-community/Qwen3-Coder-Next-4bit
				"coder": {
					"name": "coder"
				},
				// mlx-community/Qwen3-Coder-Next-mxfp8
				"coder-better": {
					"name": "coder-better"
				},
				// mlx-community/Qwen3.6-35B-A3B-4bit
				"agent": {
					"name": "agent"
				},
				// mlx-community/Qwen3.6-35B-A3B-mxfp8
				"agent-better": {
					"name": "agent-better"
				}
			}
		}
	}
}
```

Generate or refresh the OpenCode config from `tfconfig.yaml` after changing model placement. The `client` target prints the generated config and copies the same payload to the terminal clipboard through OSC52. In tmux and screen-like terminals it wraps OSC52 with multiplexer passthrough sequences so remote iTerm2/tmux sessions can forward the copy to the local clipboard:

```bash
make client
```

Pass a TF edge client id to inject that client's API key directly into the generated config. If the key is missing, the command creates `TF_USER_<CLIENT>` in `.env`, then prints and copies the config:

```bash
make client gnezim
```

Omit the client id to keep the safer `{env:TF_USER_OPENCODE}` placeholder. The CLI defaults to JSONC so it can include comments. Use the direct command when strict JSON, a custom base URL, or an output file is needed:

```bash
uv run thunder-forge edge opencode-config gnezim --inject-api-key --create-missing-key --yes --copy --base-url http://gateway-01.lan:40116/v1 --output $HOME/.config/opencode/opencode.jsonc
uv run thunder-forge edge opencode-config --format json
```

You can also inspect the live TF edge catalog directly:

```bash
curl -sS -H "Authorization: Bearer $TF_USER_OPENCODE" http://gateway-01.lan:40116/v1/models \
	| jq '.data[] | {alias: .id, model: (.tf_source_repo // .tf_runtime_model_id)}'
```

## Model Selection

Use the in-repo skill at `.github/skills/thunder-forge/SKILL.md` when working on Thunder Forge operations, refactors, or model selection. Prefer SOTA HuggingFace MLX candidates, then reject anything that does not fit the 128 GB no-swap budget after weights, KV cache, MLX overhead, OS headroom, and paired-role capacity are considered.

## Testing

```bash
uv run pytest --tb=short -q
uv run ruff check .
```

## V1

Previous architecture is preserved in `v1/` for reference.
