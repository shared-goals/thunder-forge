# Thunder Forge v2

CLI for managing a self-hosted MLX inference cluster with oMLX serving and Olla routing.

Thunder Forge is part of the [Shared Goals](https://github.com/shared-goals/) platform: private, self-hosted AI infrastructure for working with personal and collective goals without sending sensitive data to cloud APIs.

## Architecture

```
Client → TF edge → Olla → oMLX nodes (Apple Silicon)
```

- **TF edge** — auth (API key → client identity), session management, proxy to Olla
- **Olla** — model routing, sticky sessions, load balancing, health checks
- **oMLX** — multi-model inference server for Apple Silicon (MLX native)
- **Artifacts** — oMLX-native model dirs under `~/.omlx/models/<owner>/<repo>`, e.g. `~/.omlx/models/mlx-community/gpt-oss-20b-MXFP4-Q8`

## Upstream-First Goal

Thunder Forge aims to be the thinnest possible operator layer for a homelab Mac cluster while delegating inference and routing behavior to upstream projects that already solve those concerns well:

- **oMLX as the inference runtime** for Apple Silicon model serving, model lifecycle, and node-level performance behavior.
- **Olla as the router/balancer** for endpoint health checks, sticky sessions, and request distribution.
- **Thunder Forge as integration and operations glue** for auth boundary (TF edge), topology/model placement, bootstrap/restart/smoke workflows, and auditable usage reporting.

Design intent:

- Minimize Thunder Forge-specific control-plane logic over time.
- Reuse existing upstream capabilities before adding new local behavior.
- Prefer upstream issue/PR contributions for general routing or runtime needs.
- Keep local stopgaps small, explicit, and removable.

## Shared Goals Vision

Shared Goals starts from a simple loop: people clarify motives and goals, turn them into texts and memory, then use AI agents to help convert that context into coordinated action. Thunder Forge is the local inference layer for that loop. It should let a household, workshop, lab, or small group run useful AI capacity on machines they control, with private data staying on-premise.

The current v2 direction is agent-managed operation. Instead of assuming a human runs every `ssh`, `launchctl`, artifact sync, and smoke test by hand, the cluster should be manageable by an operator agent with its own Unix account on the frontend, cache/download host, and inference nodes. That account is the execution identity for routine work: prepare artifacts, sync model caches, regenerate Olla config, restart services, run smokes, and record what happened.

Thunder Forge has three operational roles:

- `gateway`: runs TF edge, Olla, routing config, auth, accounting, and external API surface.
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

## Cache Host Prerequisites (Repo Optional)

The cache/download role is designed to run with minimal host requirements. A full Thunder Forge repo checkout on the cache host is optional for the target architecture.

Required on the cache host:

- oMLX CLI binary available in the operator user path (default `~/.local/bin/omlx`).
- Writable cache root (default `~/.omlx/models` or `TF_CACHE_OMLX_MODELS_DIR`).
- Optional Hugging Face token only when downloading gated/private models (`HF_TOKEN` in the cache host environment).
- Network access to Hugging Face and to inference nodes over management LAN and/or Thunderbolt fabric.
- Operator SSH identity allowed from the gateway/control host.

`cluster prepare --apply` now treats cache as a first-class bootstrap role: it ensures oMLX tooling and prepares the cache hub directory on each configured cache host.

## Thunderbolt Fabric Setup (Cache to Inference)

For split topology, treat the cache host as a fabric hub. Example: one cache machine with four Thunderbolt links, one direct link per inference node.

One-time host/network setup (outside Thunder Forge):

1. Physically cable cache Thunderbolt ports to inference nodes one-to-one.
2. Create/enable Thunderbolt network interfaces on both ends (macOS Network Settings).
3. Verify each link has link-local or private IPv4 addresses and is reachable.
4. Ensure SSH host keys are trusted for management hostnames first.

Thunder Forge runtime behavior stays dynamic and no-extra-config:

- Keep node management hostnames in `nodes.<name>.host`.
- Set `nodes.<name>.fabric_host: true` only for nodes that should use fabric probing.
- Use `operations.sync.transport: auto` (default) so sync prefers discovered fabric paths and falls back to management LAN when unresolved.
- `--transport fabric` enforces fabric-only and fails fast when no reachable fabric address is discovered.

Fabric probing is intentionally Darwin-only today and runs from the machine executing the sync command. In split mode, run sync/download from the cache role so discovery and transfer use cache-local Thunderbolt interfaces.

For a step-by-step operations checklist, see `docs/operations/thunderbolt-cache-fabric.md`.

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

# One-time bootstrap: install gateway (Olla + Edge), cache oMLX tooling + hub, and node (oMLX) LaunchDaemons
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

For reboot-durable system daemons, bootstrap once with `make bootstrap`, then use `make restart` for all subsequent updates. Bootstrap ensures the configured Olla binary version, generates Olla config, ensures cache-role oMLX tooling (including upgrade checks), prepares the cache hub directory on each cache host, ensures user-local `uv`/oMLX tooling on inference nodes (including upgrade checks), installs gateway Olla/Edge and node oMLX LaunchDaemons through the configured admin accounts, validates sudoers with `visudo -cf`, and writes one narrow Thunder Forge sudoers include on each host at `/etc/sudoers.d/thunder-forge`. Olla upgrades occur when the configured target changes (for example a new pinned Olla version, or unpinned `latest` resolving to a newer release). After that, `make restart` regenerates Olla config and reinstalls/restarts all services with `sudo -n`; no password prompt is expected.

When `services.olla.version` is omitted inside an explicit `services.olla` block, bootstrap treats Olla as unpinned and resolves the latest release tag at runtime. If latest lookup fails, it falls back to `v0.0.27`.

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
- `config/olla-config.yaml` is generated output managed by Thunder Forge.
- Config node roles are `gateway`, `cache`, and `inference`. Use `roles: [gateway, cache]` for multi-role hosts such as `gateway-cache-01`; use `roles: [inference]` for oMLX-serving nodes such as `infer-01`.

Create a local config with `cp tfconfig.example.yaml tfconfig.yaml`, then edit the local file for this host.

## Service Ports

Thunder Forge service ports live in `tfconfig.yaml` under `services:`:

| Config key | Default | Service |
|------------|---------|---------|
| `services.olla.port` | `40115` | Local Olla router on the frontend host |
| `services.olla.version` | `v0.0.27` | Olla release used by `cluster prepare` |
| `services.olla.bin_dir` | `olla-bin` | Local Olla binary install directory |
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

`cluster prepare` is the unified one-time setup path for the pre-MVP cluster. It prints a plan, then applies phases in this order: gateway tooling, gateway daemons, cache tooling + cache hub, inference daemons. Use the lower-level `runtime setup-daemon` command only when working on one node directly. By default it prints the generated node-side admin script and remote commands. With `--apply`, it copies the script to the node and runs it through an admin account:

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

Copy the tracked config example, then create/edit local files:

```bash
cp tfconfig.example.yaml tfconfig.yaml
touch .env
```

For TF v2, `tfconfig.yaml` is the local source of truth. `models.<id>` is the public alias and Thunder Forge model identity. Each model declares `runtime_model_id`, the id exposed by oMLX. Nodes declare which model ids they can serve with `nodes.<node>.models`, and Olla config generation derives endpoints and aliases from that placement. Temporary comparison aliases such as `memory-bf16` may be used for benchmarks, but they are not canonical role names.

Run `uv run thunder-forge config lint` before generating runtime/router config. It catches unknown model assignments, duplicate runtime model ids, benchmark-only placements, invalid runtime ports, and oMLX `0.0.0.0` exposure without `trusted_network: true`.

### Parameter Sources

- Cache artifact root: `.env` key `TF_CACHE_OMLX_MODELS_DIR`, default `~/.omlx/models`. Artifact `status`, `download`, and `sync` use this path on the cache execution host (local cache role or remotely dispatched cache role).
- Node oMLX process args: `tfconfig.yaml` under `nodes.<node>.runtime`. `type` and `port` are required; optional keys map directly to `omlx serve` flags: `model_dir`, `bind_host`, `base_path`, `log_level`, `max_model_memory`, `max_process_memory`, `max_concurrent_requests`, `paged_ssd_cache_dir`, `paged_ssd_cache_max_size`, `hot_cache_max_size`, `no_cache`, `mcp_config`, and `hf_endpoint`.
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

## Daily Usage Reporting

Thunder Forge now has a file-backed daily usage workflow designed for KISS/DRY/YAGNI operation:

- Request events come from `logs/tf-edge-access.jsonl`.
- Node snapshots (health + hot-loaded models) come from `logs/tf-node-metrics.jsonl`.
- Daily rollups are produced by the TF CLI and can also be queried with DuckDB CLI.
- Shared log retention is controlled by `services.log_retention_days` in `tfconfig.yaml` (default: `3`).

TF edge now collects node metrics in-process every 60 seconds by default, so no separate metrics process is required.

Manual collector commands remain available for diagnostics:

```bash
uv run thunder-forge usage collect-node-metrics

# continuous collector every minute
uv run thunder-forge usage collect-node-metrics --continuous --interval-seconds 60
```

Edge serve flags for this behavior:

```bash
uv run thunder-forge edge serve --metrics-interval-seconds 60
uv run thunder-forge edge serve --no-collect-node-metrics
```

Manually run shared log trimming across edge access logs, node metrics, and local service logs:

```bash
uv run thunder-forge usage trim-logs
# optional override
uv run thunder-forge usage trim-logs --retention-days 7
```

Print daily summary from structured logs:

```bash
uv run thunder-forge usage report --period 2026-06-02
uv run thunder-forge usage report --period 2026-06-02 --json

# or
make usage 2026-06-02
make usage-json 2026-06-02
```

Summary dimensions include:

- by user (`tf_user`/`client_id`): requests, consumed time (`latency_ms` sum), tokens when present
- by node (`msm1`-`msm4`): requests, consumed time, tokens, by-model split, by-hour split
- by model: requests, consumed time, tokens when present
- node hot-loaded model sets from collected node snapshots

### Minimal Metric Strategy

Thunder Forge should collect only the smallest set of base signals needed to answer two questions:

1. How fast does a request reach its first useful answer?
2. How well is the cluster being used across nodes, models, and sessions?

Base signals to collect:

#### `logs/tf-edge-access.jsonl`

| Field | Keep? | Why |
|---|---|---|
| `timestamp` | useful | Required for all time-bucketed metrics |
| `client_id` | useful | Needed to split by user/account/use-case class |
| `model` | useful | Needed to split latency and routing by model |
| `node_name` | useful | Needed to attribute routing outcomes to nodes |
| `time_to_first_token_ms` | useful | Primary latency metric for "shortest wait before answer" |
| `completion_latency_ms` | secondary only | Keep only if total-response analysis is needed; not required for the key cluster metrics |
| `session_key` or sticky header value | useful when available | Needed to measure session reuse and sticky routing effectiveness |

#### `logs/tf-node-metrics.jsonl`

| Field | Keep? | Why |
|---|---|---|
| `timestamp` | useful | Required for joining with edge-access by minute or sample time |
| `node_name` | useful | Required to attribute metrics to a node |
| `health_ok` / `status_ok` | useful | Needed to exclude unhealthy nodes from routing decisions |
| `hot_loaded_models` | useful | Needed to measure hot-load hit rate and warm-node reuse |
| `active_jobs` | new, needed if oMLX exposes it | Needed to measure node pressure directly instead of inferring it indirectly |
| `queue_depth` | new, needed if oMLX exposes it | Needed to measure queue time / overload pressure directly |
| `cache_hit` / `prompt_cache_hit` | new, needed if oMLX exposes it | Needed to measure reuse of prior session/request context |

Key metrics to compute from those base signals:

| Key metric | Formula |
|---|---|
| Time-to-first-token p95 | `percentile(time_to_first_token_ms, 95)` grouped by model, client class, and node |
| Hot-load hit rate | `requests where chosen node had model in hot_loaded_models / total requests` |
| Sticky/session reuse rate | `requests with same session_key routed to the same node as the previous request for that session / total repeat-session requests` |
| Model spread | `count(distinct node_name serving model) / count(distinct eligible nodes for that model)` |
| Routing regret | `chosen_node_ttf_ms - min(ttf_ms over eligible nodes in same time bucket)` |
| Node pressure spread | `max(active_jobs + queue_depth) - min(active_jobs + queue_depth)` across eligible nodes in the same time bucket |
| Cluster imbalance ratio | `minutes where max(node_pressure) > 0 and min(node_pressure) = 0 / total minutes` |

What to avoid:

- Do not keep base metrics that are not used to compute one of the key metrics above.
- Do not add secondary metrics unless they directly improve a routing or placement decision.
- Prefer the smallest set of measurements that can drive the next implementation step.

Implementation rule:

- Collect base metrics first, calculate key metrics in DuckDB, then use the results to refine Olla routing, oMLX observability, or Thunder Forge integration only when needed.

### Request Routing by Use Case

The routing policy should be use-case aware, not global.

| Use case | Desired routing rule | Why |
|---|---|---|
| `memory` / hindsight | Route to the most-idle node that is capable of serving `memory`, then prefer nodes with the model already hot-loaded | Minimize total wait time without wasting a warm node on a busier request |
| `opencode` / `vscode` | Keep sticky session affinity and prefer the same node for the same session | Reuse model load and prompt/session cache to reduce time-to-first-token |
| `hermes-agent` | Investigate upstream/session-header support first; until then use the least-idle capable node with a cold-load penalty | Preserve latency while avoiding a weaker sticky strategy than the editor clients |

Important Hermes sticky finding:

- Injecting a sticky key as `hermes-<account>` is too coarse: it can pin unrelated conversations from one user to one node, which reduces effective KV/prompt-cache reuse and harms cluster balance.
- If sticky is used for Hermes, the key must be conversation/session-scoped (`hermes-<session-id>`), not account-scoped.
- Reuse Olla sticky capability directly; avoid implementing a parallel Thunder Forge sticky router.

### Information Sources To Inspect

The following sources are the first places to look when debugging routing or building better metrics:

| Source | What it tells us | How to query it |
|---|---|---|
| `logs/tf-edge-access.jsonl` | Per-request client, model, node, and latency timing | Read JSONL directly or summarize with `make usage` |
| `logs/tf-node-metrics.jsonl` | Node health plus hot-loaded model sets | Read JSONL directly or collect via `usage collect-node-metrics` |
| `logs/olla-40115.stdout.log` | Olla startup config, discovered endpoints, model filters, sticky-session settings, and routed request decisions | Read the log directly if available; it is a useful operational trace |
| `GET /internal/health` | Olla liveness/ready status | Probe Olla directly |
| `GET /internal/status/endpoints` | Which endpoints are healthy/routable | Probe Olla directly |
| `GET /internal/status/models` | Model catalog by endpoint | Probe Olla directly |
| `GET /internal/stats/sticky` | Sticky-session statistics | Probe Olla directly |
| `GET /health` on oMLX | Node-level runtime health | Probe each oMLX node directly |
| `GET /v1/models` on oMLX | Models visible on the node | Probe each oMLX node directly |
| `GET /v1/models/status` on oMLX | Model load state and runtime status | Probe each oMLX node directly |
| `X-Olla-Endpoint` response header | Which backend actually handled a request | Inspect routed responses |
| `X-Olla-Session-ID` request/response header | Caller-provided session identity for stickiness | Send an explicit sticky-session id |
| `X-Olla-Sticky-Session` response header | Sticky-session outcome (`hit`, `miss`, `repin`, `disabled`) | Inspect routed responses |

Olla log observations from the local stdout trace support the same model: startup loads `config/olla-config.yaml`, registers endpoints, applies model filters, enables sticky-session affinity, and logs each routed request with the selected endpoint and completion latency. That makes `logs/olla-40115.stdout.log` a useful complement to the JSONL summaries when diagnosing routing decisions.

Data-source decision:

- Use Olla/oMLX endpoints and TF JSONL logs as the primary harvest path for metrics.
- Use `logs/olla-40115.stdout.log` as a diagnostic/fallback trace, not as the primary metrics source.

For ad hoc SQL analysis, use DuckDB directly over JSONL:

```bash
duckdb -readonly -cmd ".mode table" -cmd ".headers on" \
	-cmd ".param set period 2026-06-02" \
	-f docs/operations/daily-usage-duckdb.sql

# or
make usage-duckdb 2026-06-02
```

## Similar Repo Landscape (Olla/oMLX/Mac Cluster)

The current landscape has partial matches but few complete implementations of the exact target architecture (Olla + oMLX + small multi-node Mac homelab cluster).

| Repo | Core idea | Match to TF target | Good findings to reuse |
|---|---|---|---|
| [shared-goals/thunder-forge](https://github.com/shared-goals/thunder-forge) | TF edge + Olla routing + oMLX nodes for Apple Silicon | **Direct match** | Existing baseline for auth boundary, model aliases, node metrics, and operator workflows |
| [JoacoEsteban/omlx-ollama-proxy](https://github.com/JoacoEsteban/omlx-ollama-proxy) | Lightweight Ollama-compatible proxy in front of oMLX | **Partial** (bridge pattern, not cluster orchestration) | Clean API translation pattern (`/api/tags` and `/api/chat`), minimal adapter philosophy, simple client compatibility layer |
| [ThiagoLPereira/ollama-swarm-balancer](https://github.com/ThiagoLPereira/ollama-swarm-balancer) | Async application-level balancer for multiple Ollama nodes | **Partial** (balancing patterns, no oMLX/Olla stack) | Round-robin + health-check failover pattern, connection pooling behavior, per-request node attribution + latency annotations |
| [robert-mcdermott/ollama-batch-cluster](https://github.com/robert-mcdermott/ollama-batch-cluster) | Batch processing across many Ollama instances/hosts | **Partial** (distributed execution ideas, not Mac/oMLX) | Host-level parallel dispatch ideas, simple cluster config for multi-host fanout, throughput-oriented benchmarking examples |

Interpretation:

- There is no widely adopted public repo that already ships the full Olla + oMLX + 2-4 Mac node homelab operator workflow.
- Thunder Forge should continue as the integration reference while pushing reusable behaviors upstream to Olla/oMLX.

## Reuse Plan and Roadmap

The roadmap is upstream-first: integrate existing upstream features first, then propose upstream improvements before expanding Thunder Forge-local logic.

### Phase 1: Measure and Attribute (now)

- Harvest the minimal base metrics from TF edge access logs and oMLX node snapshots.
- Encode the key metric calculations in DuckDB and surface them in `make usage`.
- Compare request latency by model, client class, node, and session-friendly client type.

### Phase 2: Thin Integration Enhancements

- Keep balancing and sticky behavior in Olla configuration, not custom TF routing code.
- Keep inference scheduling/runtime behaviors in oMLX.
- Keep TF changes focused on topology config, auth boundary, minimal observability, and operator ergonomics.

### Phase 3: Upstream Contributions

Open upstream issues/PRs when the need is generic:

- **Olla candidates**:
	- richer routing telemetry for endpoint decisions,
	- model-aware routing hints that remain generic,
	- clearer balancing introspection for sticky-session-heavy workloads.
- **oMLX candidates**:
	- explicit time-to-first-token and cache/reuse fields in status for external schedulers/operators,
	- stronger runtime model-state telemetry for hot-loaded/cold-loaded transitions,
	- scheduling observability hooks that reduce local heuristics.

### Phase 4: Deletion-Oriented Cleanup

- Remove Thunder Forge-local stopgaps once upstream support lands.
- Keep a small migration checklist per removed workaround.
- Track net reduction of Thunder Forge-specific control-plane code as a success metric.

Execution rule:

- For each feature request, decide in order: **reuse upstream as-is -> wire existing upstream capability -> contribute upstream -> only then add minimal local temporary workaround**.

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

Generate or refresh the OpenCode config from `tfconfig.yaml` after changing model placement. The `opencode` target prints the generated config and copies the same payload to the terminal clipboard through OSC52. In tmux and screen-like terminals it wraps OSC52 with multiplexer passthrough sequences so remote iTerm2/tmux sessions can forward the copy to the local clipboard:

```bash
make opencode
```

Pass a TF edge client id to inject that client's API key directly into the generated config. If the key is missing, the command asks before creating `TF_USER_<CLIENT>` in `.env`, then prints and copies the config:

```bash
make opencode shag
```

Omit the client id to keep the safer `{env:TF_USER_OPENCODE}` placeholder. The CLI defaults to JSONC so it can include comments. Use the direct command when strict JSON, a custom base URL, or an output file is needed:

```bash
uv run thunder-forge edge client-config opencode shag --inject-api-key --create-missing-key --yes --copy --base-url http://gateway-01.lan:40116/v1 --output $HOME/.config/opencode/opencode.jsonc
uv run thunder-forge edge client-config opencode --format json
```

You can also inspect the live TF edge catalog directly:

```bash
curl -sS -H "Authorization: Bearer $TF_USER_OPENCODE" http://gateway-01.lan:40116/v1/models \
	| jq '.data[] | {alias: .id, model: (.tf_source_repo // .tf_runtime_model_id)}'
```

### Hermes Provider

Hermes Agent uses named custom providers for OpenAI-compatible endpoints. Keep the existing Hermes default provider in its top-level `model:` block unless Thunder Forge should become the default. Add Thunder Forge under `custom_providers` so it is available for explicit model switches.

For the `shag` client, keep the API key in the Hermes env file:

```dotenv
# ~/.hermes/.env
TF_USER_SHAG=replace-with-generated-key
```

Then add or merge this provider entry into `~/.hermes/config.yaml`:

```yaml
custom_providers:
  - name: thunder-forge
    base_url: http://studio.lan:40116/v1
    key_env: TF_USER_SHAG
    api_mode: chat_completions
    models:
      # mlx-community/Qwen3.6-35B-A3B-4bit
      agent: {}
      # mlx-community/Qwen3.6-35B-A3B-mxfp8
      agent-better: {}
      # mlx-community/Qwen3-Coder-Next-4bit
      coder: {}
      # mlx-community/Qwen3-Coder-Next-mxfp8
      coder-better: {}
      # mlx-community/gpt-oss-20b-MXFP4-Q8
      memory: {}
      # mlx-community/gpt-oss-20b-mxfp4-bf16
      memory-bf16: {}
```

Switch to a Thunder Forge alias explicitly from the command line:

```bash
hermes --provider custom:thunder-forge -m agent -z 'Reply exactly: ok'
```

Inside an existing Hermes session, use the named custom provider form:

```text
/model custom:thunder-forge:coder
```

Hermes may discover backing runtime ids from `/v1/models`, while TF aliases such as `agent` and `coder` are still accepted by `/v1/chat/completions`. Keep the generated `models:` map alias-first so the user-facing choices match Thunder Forge roles rather than raw oMLX runtime names.

Generate or refresh the Hermes provider snippet from `tfconfig.yaml` after changing model placement:

```bash
make hermes shag
```

Hermes output always uses `key_env`; it does not embed API keys. `make hermes <client>` asks before creating a missing key. Use the direct command when a custom base URL or output file is needed:

```bash
uv run thunder-forge edge client-config hermes shag --create-missing-key --yes --copy --base-url http://studio.lan:40116/v1 --output $HOME/.hermes/thunder-forge.yaml
```

### VS Code Provider

VS Code's BYOK flow uses `chatLanguageModels.json`. Thunder Forge generates the custom-endpoint provider entry with one model object per assigned TF alias, keeping the alias in both `id` and `name`, pointing every model at the TF edge base URL, and enabling both `toolCalling` and `vision`.

The generated shape follows the VS Code model configuration reference:

```json
[
	{
		"name": "Thunder Forge",
		"vendor": "customendpoint",
		"apiKey": "<API-ID-VALUE>",
		"apiType": "chat-completions",
		"models": [
			{
				"id": "memory",
				"name": "memory",
				"url": "http://gateway-01.lan:40116/v1",
				"toolCalling": true,
				"vision": true,
				"maxInputTokens": 117965,
				"maxOutputTokens": 13107
			}
		]
	}
]
```

`make vscode` prints the generated JSON and copies it to the terminal clipboard. Pass a client id to inject the resolved TF edge key and create it in `.env` when missing:

```bash
make vscode
make vscode shag
uv run thunder-forge edge client-config vscode shag --inject-api-key --create-missing-key --yes --copy
```

### Client Config Generator

OpenCode and Hermes config snippets are generated from the same TF edge source of truth: the gateway base URL, `TF_USER_<CLIENT>` key name, assigned model aliases, backing model comments, and benchmark-only status. The target is explicit:

```bash
uv run thunder-forge edge client-config opencode [client-id] --copy
uv run thunder-forge edge client-config hermes [client-id] --copy
uv run thunder-forge edge client-config vscode [client-id] --copy
```

Behavior:

- Both renderers use the same assigned alias catalog from `tfconfig.yaml`.
- `--create-missing-key <client-id>` asks before creating `TF_USER_<CLIENT>` in `.env` when it is missing.
- `--create-missing-key --yes <client-id>` creates or reads `TF_USER_<CLIENT>` in `.env` without prompting.
- OpenCode can use `--inject-api-key` when the client config must contain the real key.
- Hermes always emits `key_env: TF_USER_<CLIENT>` and never embeds the secret.
- Keep OpenCode output as JSONC/JSON because OpenCode needs static `provider.<id>.models`.
- Keep VS Code output as JSON because `chatLanguageModels.json` expects strict model objects with `maxInputTokens` and `maxOutputTokens` that stay within the model context window.
- Keep Hermes output as a YAML snippet with `custom_providers:`, `base_url`, `key_env`, `api_mode: chat_completions`, and alias `models:`. It does not rewrite the top-level Hermes `model:` block.
- Preserve `--base-url`, `--output`, `--copy`, and OSC52/tmux clipboard behavior across both clients.

The Makefile stays thin aliases over that CLI:

```bash
make opencode shag
make hermes shag
```

Avoid putting client ids, model ids, or ports in the Makefile; keep those in CLI options, `.env`, and `tfconfig.yaml`.

## Model Selection

Use the in-repo skill at `.github/skills/thunder-forge/SKILL.md` when working on Thunder Forge operations, refactors, or model selection. Prefer SOTA HuggingFace MLX candidates, then reject anything that does not fit the 128 GB no-swap budget after weights, KV cache, MLX overhead, OS headroom, and paired-role capacity are considered.

## Testing

```bash
uv run pytest --tb=short -q
uv run ruff check .
```

