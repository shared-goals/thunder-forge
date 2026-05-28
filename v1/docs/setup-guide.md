# Thunder Forge Setup Guide

Get your MLX inference cluster running. This guide ends when you reach the Admin UI — the UI handles cluster configuration, model assignment, and deployment from there.

## Quick Start

### Prerequisites

- **Gateway**: Linux or macOS machine (Docker is installed automatically in Step 2)
- **Compute nodes**: one or more macOS Apple Silicon machines
- All nodes on the same network with SSH access between them
- **Shell**: zsh — used throughout this guide on both macOS and Linux

### Step 1: Clone & Configure

On the gateway node:

```bash
git clone https://github.com/shared-goals/thunder-forge.git ~/thunder-forge
cd ~/thunder-forge
cp .env.example .env
```

Open `.env` and fill in the required fields. Generate a value for each secret:

```zsh
openssl rand -hex 32
```

```bash
LITELLM_MASTER_KEY=<generated>           # API key for LiteLLM proxy
POSTGRES_PASSWORD=<generated>            # PostgreSQL password
WEBUI_SECRET_KEY=<generated>             # Open WebUI session encryption
ADMIN_DB_PASSWORD=<generated>            # Thunder Admin database password
GATEWAY_SSH_USER=<your-username>         # SSH user on this gateway machine
THUNDER_FORGE_DIR=/home/<user>/thunder-forge  # Absolute path — no ~
HF_TOKEN=<your-token>                    # HuggingFace token — required for gated/fast model downloads
```

Get your HuggingFace token at https://huggingface.co/settings/tokens (read access is sufficient). Without it, model downloads may be rate-limited or blocked for gated models.

All other values in `.env` have safe defaults.

### Step 2: Generate SSH Key for Node Access

```zsh
zsh scripts/setup-node.sh gateway
```

This bootstraps the gateway node: installs missing dependencies (Docker, uv), starts the Docker stack, and generates the SSH keypair for compute node access. The public key is printed at the end — copy it for the next step.

The Admin UI SSHes to the gateway itself (localhost) to run deploy commands. Add the generated public key to the gateway's own `authorized_keys`:

```zsh
cat ~/.ssh/id_ed25519.pub >> ~/.ssh/authorized_keys
chmod 600 ~/.ssh/authorized_keys
```

Also make sure `GATEWAY_SSH_KEY` in `.env` uses an **absolute path** (not `~/...`):

```zsh
sed -i "s|GATEWAY_SSH_KEY=.*|GATEWAY_SSH_KEY=$HOME/.ssh/id_ed25519|" .env
```

### Step 3: Bootstrap Compute Nodes

On **each macOS compute node**, run:

```bash
git clone https://github.com/shared-goals/thunder-forge.git ~/thunder-forge
cd ~/thunder-forge
zsh scripts/setup-node.sh node
```

Then add the gateway's public key (from Step 2) to the node's authorized keys:

```bash
echo "<public-key-from-step-2>" >> ~/.ssh/authorized_keys
```

Verify connectivity from the gateway:

```bash
ssh -i ~/.ssh/id_ed25519 <user>@<node-ip> echo ok
```

### Step 4: Start the Docker Stack

On the gateway:

```zsh
cd ~/thunder-forge
docker compose -f docker/docker-compose.yml --env-file .env up -d
docker compose -f docker/docker-compose.yml --env-file .env ps   # all services should show "Up"
```

Four services start: PostgreSQL, LiteLLM proxy, Open WebUI, Admin UI.

### Step 5: Open the Admin UI

Navigate to `http://<gateway-ip>:8501`

First run creates the admin account and prompts for credentials. The Admin UI guides you through adding nodes, models, and deploying the cluster from here.

---

## Reference

### Environment Variables

#### `.env` (repo root) — unified config

Single config file for both the CLI and the Docker stack.

**Core secrets** — all required. Generate each with `openssl rand -hex 32`:

| Variable | Description |
|---|---|
| `LITELLM_MASTER_KEY` | API key for the OpenAI-compatible LiteLLM proxy |
| `POSTGRES_PASSWORD` | PostgreSQL password |
| `WEBUI_SECRET_KEY` | Open WebUI session encryption key |
| `ADMIN_DB_PASSWORD` | Thunder Admin database password |

**Admin UI — SSH access:**

| Variable | Required | Default | Description |
|---|---|---|---|
| `GATEWAY_SSH_USER` | ✅ | — | SSH user on the gateway node |
| `GATEWAY_SSH_KEY` | Optional | `~/.ssh/id_ed25519` | Path to SSH private key on the host (mounted read-only into the container) |
| `GATEWAY_SSH_HOST` | Optional | `localhost` | Gateway hostname or IP. Defaults to localhost since the Admin UI runs on the gateway. |
| `GATEWAY_SSH_PORT` | Optional | `22` | SSH port |

**Admin UI — Thunder Forge integration:**

| Variable | Required | Default | Description |
|---|---|---|---|
| `THUNDER_FORGE_DIR` | ✅ | — | Absolute path to the thunder-forge repo on the gateway |
| `HF_TOKEN` | Optional | — | HuggingFace token for private or gated models |
| `SESSION_TIMEOUT_HOURS` | Optional | `24` | Admin UI session timeout in hours |
| `DISPLAY_TZ` | Optional | `UTC` | Timezone for timestamps in the Admin UI (e.g. `Europe/Moscow`) |

**Open WebUI:**

| Variable | Required | Default | Description |
|---|---|---|---|
| `UI_USERNAME` | Optional | `admin` | Chat interface username |
| `UI_PASSWORD` | Optional | — | Chat interface password |
| `WEBUI_AUTH` | Optional | `true` | Require login |
| `WEBUI_PORT` | Optional | `8080` | Open WebUI port |
| `ENABLE_SIGNUP` | Optional | `false` | Allow new user registration |

**LiteLLM & infrastructure:**

| Variable | Required | Default | Description |
|---|---|---|---|
| `LITELLM_WORKERS` | Optional | `4` | LiteLLM worker processes (increase for high request volume) |
| `PG_PORT` | Optional | `5434` | PostgreSQL port (non-standard to avoid conflicts with a local Postgres instance) |
| `GRAFANA_URL` | Optional | — | Grafana dashboard URL, shown as a link in the Admin UI |

**CLI only** (not used by Docker):

| Variable | Required | Description |
|---|---|---|
| `HF_HOME` | Optional | HuggingFace cache directory on inference nodes |
| `TF_DISABLE_SLEEP` | Optional | Disable macOS sleep on nodes (`true`/`false`) |

---

### Updating a Running Cluster

#### Code update (no config changes)

```zsh
git pull
docker compose -f docker/docker-compose.yml --env-file .env up -d --build
```

Docker volumes (database, config history) are preserved. The Admin UI retains all config versions across updates.

#### Env var changes

Edit `.env`, then restart:

```zsh
docker compose -f docker/docker-compose.yml --env-file .env up -d
```

Only containers whose environment changed will restart. Data is preserved.

**Rotating secrets** (`LITELLM_MASTER_KEY`, `WEBUI_SECRET_KEY`, `ADMIN_DB_PASSWORD`):
1. Generate a new value: `openssl rand -hex 32`
2. Update `.env`
3. `docker compose -f docker/docker-compose.yml --env-file .env up -d`
4. Update any API clients that used the old `LITELLM_MASTER_KEY`

**⚠️ Special case: `POSTGRES_PASSWORD`**

The Postgres password is written into the database volume on first run. Changing `.env` alone after that will break the connection — the value in the database must be updated first:

```zsh
# Update the password in the running database
docker compose -f docker/docker-compose.yml --env-file .env exec postgres psql -U litellm -c "ALTER USER litellm PASSWORD 'new-password';"
docker compose -f docker/docker-compose.yml --env-file .env exec postgres psql -U postgres -c "ALTER USER thunder_admin PASSWORD 'new-password';"

# Then update .env and restart
docker compose -f docker/docker-compose.yml --env-file .env up -d
```

#### Database schema migrations

Migrations run automatically on every container startup. A `git pull` + `docker compose -f docker/docker-compose.yml --env-file .env up -d --build` is always sufficient — no manual migration commands needed.

#### Model cache

Model weights live on compute nodes, not in Docker volumes. Adding new models is always safe via the Admin UI.

To free disk space after removing a model from the config:

```zsh
ssh <user>@<node-ip> "rm -rf ~/.cache/huggingface/hub/<model-repo-name>"
```

---

### Troubleshooting

**Check stack health:**

```zsh
cd ~/thunder-forge
docker compose -f docker/docker-compose.yml --env-file .env ps                        # all services should show "Up"
docker compose -f docker/docker-compose.yml --env-file .env logs --tail=50 <service>  # logs for a specific service
```

**Admin UI not reachable on port 8501:**
- Admin UI runs in host network mode — check firewall rules on the gateway
- Check logs: `docker compose -f docker/docker-compose.yml --env-file .env logs --tail=50 admin-ui`

**LiteLLM not responding on port 4000:**
- PostgreSQL must be healthy first: `docker compose -f docker/docker-compose.yml --env-file .env logs postgres`
- LiteLLM depends on postgres — check `docker compose -f docker/docker-compose.yml --env-file .env ps`

**All containers exit immediately after `docker compose up`:**
Missing required `.env` values — check logs for the specific variable name: `docker compose -f docker/docker-compose.yml --env-file .env logs`

**Admin UI can't reach gateway via SSH:**

```zsh
# Check the SSH key is accessible inside the container
docker compose -f docker/docker-compose.yml --env-file .env exec admin-ui ls -la $GATEWAY_SSH_KEY

# Test SSH from inside the container
docker compose -f docker/docker-compose.yml --env-file .env exec admin-ui ssh -i $GATEWAY_SSH_KEY $GATEWAY_SSH_USER@$GATEWAY_SSH_HOST echo ok
```

Also verify `THUNDER_FORGE_DIR` exists on the gateway.

**Compute node unreachable:**
- Verify the public key is in `~/.ssh/authorized_keys` on the node
- Test from the gateway: `ssh -i ~/.ssh/id_ed25519 <user>@<node-ip> echo ok`

**mlx-lm service not starting on a compute node:**

```zsh
ssh <user>@<node-ip> "tail -50 ~/logs/mlx-lm-<port>.err"
```

**HuggingFace offline error / model not loading:**
Model cache is incomplete. Trigger model sync from the Admin UI deploy page, or run directly:

```zsh
cd ~/thunder-forge && uv run thunder-forge ensure-models
```

**`POSTGRES_PASSWORD` changed after first run:**
Containers fail to connect. Follow the rotation procedure under [Env var changes](#env-var-changes).

**LiteLLM `IsADirectoryError`:**
`configs/litellm-config.yaml` doesn't exist — Docker created a directory instead. Fix:

```zsh
cd ~/thunder-forge
docker compose -f docker/docker-compose.yml --env-file .env down
rm -rf configs/litellm-config.yaml
uv run thunder-forge generate-config
docker compose -f docker/docker-compose.yml --env-file .env up -d
```

**Port conflict on Open WebUI (port 8080 already in use):**

```zsh
echo 'WEBUI_PORT=8081' >> ~/thunder-forge/.env
docker compose -f docker/docker-compose.yml --env-file .env up -d
```

---

## Appendix: Local Test Setup

For a minimal local test on a single machine with a Linux VM, see [`docs/local-test-guide.md`](local-test-guide.md).
