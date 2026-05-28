# Design: Setup Guide Redesign

**Date:** 2026-03-27
**Status:** Approved

## Overview

Rewrite `docs/setup-guide.md` as a Quick Start + Reference structure. The goal: anyone can follow 7 steps to reach the Admin UI login screen, then the UI handles the rest. `docs/local-test-guide.md` is trimmed and becomes an appendix at the bottom of the main guide.

**Audience:** All — first-time installers, returning operators, collaborators.

**Scope boundary:** The guide ends when the user opens the Admin UI. Cluster configuration, model assignment, and deployment are Admin UI territory and are not documented here.

---

## Section 1: Document Structure

`docs/setup-guide.md` reorganized as:

```
# Thunder Forge Setup Guide

## Quick Start
  1. Prerequisites
  2. Clone & configure .env
  3. Generate SSH key for node access
  4. Bootstrap compute nodes
  5. Start the Docker stack
  6. Open Admin UI
  7. Configure cluster in UI (UI takes over here)

## Reference

### Environment Variables
### Updating a Running Cluster
### Troubleshooting

## Appendix: Local Test Setup
  (condensed from local-test-guide.md)
```

`docs/local-test-guide.md` — trimmed to a minimal appendix section, kept as a standalone file but clearly marked as supplementary.

---

## Section 2: Quick Start Content

7 steps. Each step is a single focused action with exact commands. No explanation beyond what's needed to execute the step.

**Step 1 — Prerequisites**
- Gateway: Linux or macOS machine with Docker + Docker Compose installed and running
- Compute nodes: at least one macOS Apple Silicon machine
- SSH access from gateway to each compute node
- Verify Docker: `docker info`

**Step 2 — Clone & configure**
```bash
git clone <repo-url> ~/thunder-forge
cp docker/.env.example docker/.env
# Edit docker/.env — fill in the 6 required fields (marked below)
```
Required fields: `LITELLM_MASTER_KEY`, `POSTGRES_PASSWORD`, `WEBUI_SECRET_KEY`, `ADMIN_DB_PASSWORD`, `GATEWAY_SSH_USER`, `THUNDER_FORGE_DIR`. All others have safe defaults.

Quick generation for secret values:
```bash
openssl rand -hex 32
```

**Step 3 — Generate SSH key for node access**
```bash
bash scripts/setup-node.sh gateway
```
This creates the SSH keypair the Admin UI uses to reach compute nodes. Copy the displayed public key to each compute node's `~/.ssh/authorized_keys`.

**Step 4 — Bootstrap compute nodes**
Run on each macOS compute node:
```bash
bash scripts/setup-node.sh node
```
Installs Homebrew, uv, mlx-lm, disables sleep. Run once per node.

**Step 5 — Start the Docker stack**
```bash
cd docker && docker compose up -d
docker compose ps  # all services should show "Up"
```
Services: PostgreSQL + LiteLLM + Open WebUI + Admin UI.

**Step 6 — Open Admin UI**
Navigate to `http://<gateway-ip>:8501`. First run creates the admin account and prompts for credentials.

**Step 7 — Configure cluster in UI**
Add nodes → add models → assign models to nodes → deploy. The Admin UI guides you through the rest. This guide's job is done.

---

## Section 3: Environment Variables Reference

### `docker/.env` — Docker stack (main file)

**Core secrets** — all required, generate with `openssl rand -hex 32`:

| Variable | Required | Default | Description |
|---|---|---|---|
| `LITELLM_MASTER_KEY` | ✅ | — | API key for OpenAI-compatible proxy |
| `POSTGRES_PASSWORD` | ✅ | — | PostgreSQL password |
| `WEBUI_SECRET_KEY` | ✅ | — | Open WebUI session encryption |
| `ADMIN_DB_PASSWORD` | ✅ | — | Thunder Admin database password |

**Admin UI — SSH access:**

| Variable | Required | Default | Description |
|---|---|---|---|
| `GATEWAY_SSH_USER` | ✅ | — | SSH user on the gateway node |
| `GATEWAY_SSH_KEY` | ✅ | `~/.ssh/thunder_forge` | Path to SSH private key (host path, mounted read-only) |
| `GATEWAY_SSH_HOST` | Optional | `localhost` | Gateway hostname/IP (defaults to localhost since admin-ui runs on gateway) |
| `GATEWAY_SSH_PORT` | Optional | `22` | SSH port |

**Admin UI — Thunder Forge integration:**

| Variable | Required | Default | Description |
|---|---|---|---|
| `THUNDER_FORGE_DIR` | ✅ | — | Absolute path to thunder-forge repo on the gateway |
| `HF_TOKEN` | Optional | — | HuggingFace token for private/gated models |
| `SESSION_TIMEOUT_HOURS` | Optional | `24` | Admin UI session timeout |
| `DISPLAY_TZ` | Optional | `UTC` | Timezone for timestamps in Admin UI (e.g. `Europe/Moscow`) |

**Open WebUI:**

| Variable | Required | Default | Description |
|---|---|---|---|
| `UI_USERNAME` | Optional | `admin` | Chat interface username |
| `UI_PASSWORD` | Optional | — | Chat interface password |
| `WEBUI_AUTH` | Optional | `true` | Require login |
| `WEBUI_PORT` | Optional | `8080` | Open WebUI port |
| `ENABLE_SIGNUP` | Optional | `false` | Allow new user registration |

**LiteLLM & monitoring:**

| Variable | Required | Default | Description |
|---|---|---|---|
| `LITELLM_WORKERS` | Optional | `4` | LiteLLM worker processes (increase for high-throughput) |
| `PG_PORT` | Optional | `5434` | PostgreSQL port (non-standard to avoid conflicts) |
| `GRAFANA_URL` | Optional | — | Grafana dashboard URL (shown as link in Admin UI) |

### `.env` (repo root) — CLI only

Only needed if running `thunder-forge` CLI commands directly (not through the Admin UI).

| Variable | Required | Description |
|---|---|---|
| `TF_SSH_USER` | Yes (CLI) | Default SSH user for compute nodes |
| `TF_SSH_KEY` | Yes (CLI) | Path to SSH key for node access |
| `HF_HOME` | Optional | HuggingFace cache directory |
| `HF_TOKEN` | Optional | Private model access |
| `TF_DIR` | Optional | Path to thunder-forge repo |
| `TF_DISABLE_SLEEP` | Optional | Disable macOS sleep on nodes (`true`/`false`) |

> **Note:** If you're using the Admin UI exclusively, you only need `docker/.env`. The root `.env` is for direct CLI usage.

---

## Section 4: Updating a Running Cluster

### 4.1 Code update (no config changes)

```bash
git pull
cd docker && docker compose up -d --build
```

Safe: Docker volumes (database, config history) are untouched. Admin UI preserves all config versions across updates.

### 4.2 Env var changes

**Adding or changing a value:**
1. Edit `docker/.env`
2. `docker compose up -d` — restarts only affected containers

**Rotating secrets (`LITELLM_MASTER_KEY`, `WEBUI_SECRET_KEY`, `ADMIN_DB_PASSWORD`):**
1. Generate new value: `openssl rand -hex 32`
2. Update `docker/.env`
3. `docker compose up -d`
4. Update any API clients using the old `LITELLM_MASTER_KEY`

**⚠️ Special case: `POSTGRES_PASSWORD`**

The Postgres password is written to the database volume on first run. Changing only `.env` after that will break the connection. To rotate:
```bash
# Connect to the running database and change password there first
docker compose exec postgres psql -U litellm -c "ALTER USER litellm PASSWORD 'new-password';"
# Also change the admin database user if applicable
docker compose exec postgres psql -U postgres -c "ALTER USER thunder_admin PASSWORD 'new-password';"
# Then update .env and restart
docker compose up -d
```

### 4.3 Database schema migrations

Thunder Admin runs migrations automatically on every startup (`bootstrap.py` applies all schema changes idempotently using `CREATE TABLE IF NOT EXISTS` and `ALTER TABLE ... ADD COLUMN IF NOT EXISTS`). No manual migration command is ever needed.

```bash
git pull
cd docker && docker compose up -d --build
```

That's it — the container handles schema updates on its own.

### 4.4 Model cache

Model weights live on compute nodes (not in Docker volumes). Safe to add new models at any time via the Admin UI.

To remove a model:
1. Remove from config in Admin UI
2. Redeploy
3. Weights remain on disk — clean up manually via SSH if disk space is needed:
   ```bash
   ssh <user>@<node-ip> "rm -rf ~/.cache/huggingface/hub/<model-repo>"
   ```

---

## Section 5: Troubleshooting

### Stack health

```bash
docker compose ps                        # all services should show "Up"
docker compose logs <service>            # per-service logs
docker compose logs --tail=50 admin-ui   # Admin UI startup errors
```

Common issues:
- **Admin UI not reachable on 8501** → check host network mode is active (`network_mode: host` in compose), check firewall
- **LiteLLM 4000 not responding** → postgres must be healthy first; check `docker compose logs postgres`
- **All containers exit immediately** → missing required `.env` fields; check logs for the specific variable

### Admin UI can't reach gateway via SSH

```bash
# Verify SSH key is mounted
docker compose exec admin-ui ls -la /run/secrets/ 2>/dev/null || \
  docker compose exec admin-ui ls -la $GATEWAY_SSH_KEY

# Test SSH from inside the container
docker compose exec admin-ui ssh -i $GATEWAY_SSH_KEY $GATEWAY_SSH_USER@$GATEWAY_SSH_HOST echo ok
```

Also verify:
- `THUNDER_FORGE_DIR` path exists on the gateway
- `GATEWAY_SSH_KEY` points to the key generated by `setup-node.sh gateway`

### Compute node issues

- **Node unreachable** → verify public key is in `~/.ssh/authorized_keys` on node
- **mlx-lm service not starting** → check `~/logs/mlx-lm-<port>.log` on the node
- **HuggingFace offline error** → model cache incomplete; run `thunder-forge ensure-models` (or trigger from Admin UI Deploy page)

### Common first-run mistakes

- **Forgot required `.env` fields** → containers exit immediately; check `docker compose logs`
- **`POSTGRES_PASSWORD` changed after first run** → DB auth fails; follow Section 4.2 rotation procedure
- **Admin UI shows empty config** → expected on first run; use UI to add nodes, models, and assignments

---

## Appendix: Local Test Setup

*For a minimal local test on a single machine with a Linux VM — no Apple Silicon required.*

See `docs/local-test-guide.md` for the full walkthrough. Summary:
1. Start a Linux VM (e.g., UTM or VirtualBox) — acts as gateway
2. Use your Mac as the compute node
3. Follow Steps 1–7 above, using `localhost` / VM IP as appropriate
4. Use a small model (e.g., `mlx-community/Qwen2.5-0.5B-Instruct-4bit`) to verify end-to-end
