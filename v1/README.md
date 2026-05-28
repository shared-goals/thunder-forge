# Thunder Forge

Typer CLI + Streamlit admin UI for managing a **self-hosted MLX inference cluster** across multiple macOS Apple Silicon machines.

- GitHub org: https://github.com/shared-goals/
- This repo: https://github.com/shared-goals/thunder-forge

## Who is this for

Thunder Forge is for people who own **two or more Apple Silicon Macs** and want to pool them into a private, self-hosted AI inference cluster — without sending data to cloud APIs.

Typical setup:
- **2–8 Mac Studios or Mac minis** as inference nodes, each running one or more LLM services
- **One gateway machine** (Linux or Mac) routing requests, managing deployments, and hosting the web UI
- All machines on the same local network (or connected via Tailscale)

This is useful when you want to:
- Run large models that exceed a single machine's unified memory by distributing across nodes
- Keep sensitive data (medical, financial, personal) entirely on-premise
- Give multiple users OpenAI-compatible API access with individual keys
- Have a chat interface and monitoring without any external dependencies

## Quickstart

> Full setup details: [docs/setup-guide.md](docs/setup-guide.md)

### 1. Clone and configure on the gateway node

```bash
git clone https://github.com/shared-goals/thunder-forge.git ~/thunder-forge
cd ~/thunder-forge
cp .env.example .env
```

Open `.env` and fill in the required values. Generate a secret for each key field:

```bash
openssl rand -hex 32   # run once per secret
```

Minimum required:

```bash
LITELLM_MASTER_KEY=<generated>      # API key for the OpenAI-compatible proxy
POSTGRES_PASSWORD=<generated>       # PostgreSQL password
WEBUI_SECRET_KEY=<generated>        # Open WebUI session key
ADMIN_DB_PASSWORD=<generated>       # Thunder Admin database password
GATEWAY_SSH_USER=<your-username>    # SSH user on this machine
THUNDER_FORGE_DIR=/home/<user>/thunder-forge  # absolute path, no ~
HF_TOKEN=<your-token>               # huggingface.co/settings/tokens (read access)
```

### 2. Bootstrap the gateway

```bash
bash scripts/setup-node.sh gateway
```

Installs Docker and uv if missing, starts the Docker stack, generates an SSH keypair, and automatically adds it to `authorized_keys` so the Admin UI can SSH to localhost. At the end it prints "Next steps" with the `ssh-copy-id` command — use that to authorize gateway access to each compute node.

To see your public key at any time:

```bash
cat ~/.ssh/id_ed25519.pub
```

### 3. Bootstrap each compute Mac

On each macOS inference node:

```bash
git clone https://github.com/shared-goals/thunder-forge.git ~/thunder-forge
zsh scripts/setup-node.sh node
```

Then from the **gateway**, authorize SSH access to that node:

```bash
ssh-copy-id -i ~/.ssh/id_ed25519 <user>@<node-ip>
```

Verify connectivity:

```bash
ssh -i ~/.ssh/id_ed25519 <user>@<node-ip> echo ok
```

### 4. Configure and deploy the cluster

Navigate to `http://<gateway-ip>:8501` (Thunder Admin UI) and complete the initial setup:

1. **Nodes** → add each compute Mac (hostname, SSH user, IP address)
2. **Models** → register models from HuggingFace
3. **Assignments** → assign models to nodes with memory budgets and server args
4. **Deploy** → trigger deployment (downloads model weights, starts launchd services via SSH)
5. **Users** → create additional Thunder Admin UI accounts with per-user timezone preferences

### 5. Create API keys for users

LiteLLM virtual keys — the per-user API keys that clients use to call the OpenAI-compatible proxy — are managed separately via the **LiteLLM admin UI**:

```
http://<gateway-ip>:4000/ui
```

Log in with `UI_USERNAME` / `UI_PASSWORD` from your `.env`. From there:

- **Virtual Keys** → create a key per user or application, set rate limits, spending budgets, and allowed models
- Each key works as a drop-in `Authorization: Bearer <key>` for any OpenAI-compatible client pointed at `http://<gateway-ip>:4000`
- The `LITELLM_MASTER_KEY` from `.env` is the admin key — use it to administer the proxy but distribute virtual keys to end users

## Architecture

```
┌──────────────────────────────────────────────────────────────┐
│  Gateway node (Linux or Mac)                                 │
│                                                              │
│  ┌──────────┐  ┌──────────┐  ┌───────────┐  ┌───────────┐  │
│  │ LiteLLM  │  │Open WebUI│  │  Thunder  │  │ Victoria  │  │
│  │  :4000   │  │  :8080   │  │  Admin UI │  │   Logs    │  │
│  │ (proxy)  │  │ (chat)   │  │   :8501   │  │   :9428   │  │
│  └────┬─────┘  └──────────┘  └─────┬─────┘  └───────────┘  │
│       │     PostgreSQL (shared)     │                        │
└───────┼─────────────────────────────┼────────────────────────┘
        │ OpenAI-compatible HTTP       │ SSH + launchctl
        ▼                             ▼
┌───────────────┐         ┌───────────────┐
│  Mac node 1   │   ...   │  Mac node N   │
│ mlx_lm.server │         │ mlx_lm.server │
│ mlx-openai-   │         │ mlx-openai-   │
│   server      │         │   server      │
└───────────────┘         └───────────────┘
```

**Gateway services** (Docker Compose, `docker/docker-compose.yml`):

| Service | Port | Role |
|---------|------|------|
| LiteLLM | 4000 | OpenAI-compatible proxy; routes requests to nodes, manages API keys |
| Open WebUI | 8080 | Chat interface for end users |
| Thunder Admin | 8501 | Streamlit UI for cluster management |
| PostgreSQL | 5434 | Shared database for LiteLLM and Thunder Admin |
| VictoriaLogs | 9428 | Log aggregation and query UI |

**Compute nodes** (macOS, Apple Silicon):

| Service | Role |
|---------|------|
| `mlx_lm.server` | Chat and text completion — managed as launchd services |
| `mlx-openai-server` | Embeddings |

**Config source of truth:** `configs/node-assignments.yaml` — all cluster state, model assignments, and server arguments. The CLI and Admin UI derive all other configs from this file.

## Privacy & self-hosting principles

Thunder Forge is part of the [Shared Goals](https://github.com/shared-goals/) platform — infrastructure for running AI capabilities on private data without cloud dependency.

Related projects:
- **[text-forge](https://github.com/shared-goals/text-forge)** — transforms a forkable Markdown "Text" (personal goals document) into a website, EPUB, and AI-ready corpus (RAG/MCP input)

Shared Goals concept: joy/happiness → motives → goals → shared action among coauthors. [Details (RU)](https://text.sharedgoals.ru/ru/p2-180-sharedgoals/#use_case)

Self-hosting principles for sensitive workloads:

- Prefer **self-hosted nodes** and **self-hosted agents** for private domains.
- Keep data access **least-privilege** (skills should request only what they need).
- Treat secrets and tokens as production-grade (no plaintext in repos).
- Make agent activity auditable (logs, runs, and permissions).

## Cluster Management

Thunder Forge manages an MLX inference cluster via two interfaces: a **web Admin UI** for day-to-day operation, and a **Typer CLI** for scripting and automation.

For full setup instructions, see [docs/setup-guide.md](docs/setup-guide.md).

### Admin UI

A Streamlit web interface (`admin/thunder_admin/`) deployed as a Docker container on the gateway node. After initial setup, all cluster management flows through here:

| Page                | What it does                                                        |
| ------------------- | ------------------------------------------------------------------- |
| **Dashboard**       | Live cluster health — node status, service reachability             |
| **Nodes**           | Manage compute node inventory and hardware specs                    |
| **Assignments**     | Assign models to nodes, configure memory budgets and server args    |
| **Models**          | Model registry and HuggingFace cache management                     |
| **Deploy**          | Trigger deployments; view launchd plist generation and SSH output   |
| **External Endpoints** | Register external OpenAI-compatible endpoints in LiteLLM        |
| **History**         | Deployment and event log                                            |
| **Users**           | Admin user management with per-user timezone preferences            |

### CLI

```bash
uv sync                                      # Install dependencies
uv run thunder-forge --help                  # See all commands
```

| Command            | Description                                                    |
| ------------------ | -------------------------------------------------------------- |
| `generate-config`  | Generate LiteLLM `proxy_config.yaml` from cluster state        |
| `ensure-models`    | Download/sync models to inference nodes via SSH                |
| `deploy`           | Deploy mlx_lm.server services to inference nodes (launchd)     |
| `health`           | Check SSH reachability and service status across all nodes      |

Use `uv run thunder-forge <command> --help` for per-command details.

### Infrastructure Stack

The gateway node runs these services via Docker Compose (`docker/`):

- **LiteLLM** -- OpenAI-compatible proxy routing requests to inference nodes
- **Open WebUI** -- Chat interface
- **PostgreSQL** -- Shared backend for LiteLLM and Thunder Admin
- **Thunder Admin** -- The Streamlit admin UI

Inference nodes (macOS, Apple Silicon) run `mlx_lm.server` managed as launchd services.

### CI/CD

Pushes to `main` that touch `configs/`, `src/thunder_forge/`, or `docker/` trigger the deploy workflow (`.github/workflows/deploy.yml`) on a self-hosted runner on the gateway node.

## Roadmap

### Monitoring stack

Full observability for the cluster is planned as the next infrastructure milestone:

- **VictoriaMetrics** — time-series metrics: LiteLLM request latency, per-model throughput, node memory pressure, and token/s rates. Links surfaced in the Admin UI via `GRAFANA_URL`.
- **Grafana** — dashboards for cluster health, request rates, and per-model performance stats
- **Vector** — lightweight log shipper from compute nodes → VictoriaLogs

VictoriaLogs is already running in the gateway stack (`docker/docker-compose.yml`). VictoriaMetrics and Grafana come next.

### OMLX backend

[OMLX](https://github.com/jundot/omlx) is a high-performance alternative inference backend for Apple Silicon with notable advantages over the current `mlx_lm.server`:

- **Continuous batching** — handles concurrent requests without serialising them; meaningfully higher throughput under multi-user load
- **SSD caching** — extends the effective KV cache beyond unified memory using NVMe, making very large context windows practical on consumer hardware
- **OpenAI-compatible API** — drop-in replacement for the existing backend

Thunder Forge's deploy pipeline is structured around swappable backends. OMLX integration is under evaluation as an opt-in backend alongside `mlx_lm.server`.

### Other planned work

- Multi-cluster support (manage multiple independent clusters from one Admin UI)
- Automated model benchmarking and per-node performance tracking
- Tailscale-aware node discovery for dynamic cluster membership

## Status

This repository is under active development (see [LICENSE](LICENSE)).
