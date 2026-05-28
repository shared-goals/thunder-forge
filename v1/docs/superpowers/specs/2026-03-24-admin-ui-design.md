# Design: Thunder Forge Admin UI

**Date:** 2026-03-24
**Status:** Approved
**Sub-project:** 1 of 3 (Admin UI: config + deploy)

## Context

Thunder Forge manages an MLX inference cluster via a CLI tool (`thunder-forge`). The cluster config (`node-assignments.yaml`) is gitignored because the repo is public and all config data is private (IPs, users, model assignments). Currently config is edited manually on the gateway host.

This design adds a web-based admin UI for managing config, triggering deploys, and viewing cluster health. It replaces manual YAML editing and the GitHub Actions deploy pipeline.

**Related sub-projects (separate specs):**
- Sub-project 2: Monitoring integration in admin UI (depends on this)
- Sub-project 3: Repository sanitization (independent)

## Architecture

```
+--------------------------------------------------+
|  Gateway                                          |
|                                                   |
|  Docker Compose                                   |
|  +----------+  +----------+  +----------+        |
|  | Postgres |<-| LiteLLM  |  | OpenWebUI|        |
|  |  :5432   |  |  :4000   |  |  :8080   |        |
|  +----+-----+  +----------+  +----------+        |
|       |                                           |
|  +----+-----+                                     |
|  | Admin UI |--SSH--> gateway host                |
|  | Streamlit|         +-> thunder-forge deploy    |
|  |  :8501   |         +-> thunder-forge health    |
|  +----------+                                     |
|                                                   |
|  Gateway host (native)                            |
|  +-> thunder-forge CLI                            |
|  +-> SSH keys -> nodes                            |
+--------------------------------------------------+

         SSH                SSH
          |                  |
     +----+----+        +----+----+
     |  Node   |  ...   |  Node   |
     | mlx-lm  |        | mlx-lm  |
     |  :8000  |        |  :8000  |
     +---------+        +---------+
```

**Key decisions:**
- Admin UI runs in Docker, calls thunder-forge on gateway host via SSH
- Config stored in Postgres (separate database from LiteLLM), not on disk or in git
- Deploy flow: read config from DB -> generate YAML -> SSH copy to gateway -> SSH run thunder-forge
- Streamlit for rapid development, minimal frontend code
- JSONB → YAML serialization is a **new code path** in the admin UI (`config.py`), separate from the CLI's `load_cluster_config`. It writes JSONB directly to YAML without going through the Python dataclasses, preserving all fields (including `extra_args: null`, `notes`, `serving`, etc.) on roundtrip. The CLI's parser is lossy (drops unknown fields); the admin serializer must not be. YAML serialization uses a fixed key order matching the `node-assignments.yaml` convention (`models`, `nodes`, `assignments`, `external_endpoints`; within models: `source`, `disk_gb`, `ram_gb`, etc.) to ensure consistent output for diffing.
- **Validation uses the CLI as source of truth.** The CLI exposes a `parse_cluster_config(raw: dict) -> ClusterConfig` function that parses a raw YAML-like dict into dataclasses without file I/O, repo root discovery, dotenv loading, or user resolution from env vars. The `user` field is stored as-is (empty string if unset; resolution happens at deploy time on the gateway). The existing `load_cluster_config(path)` becomes a thin wrapper: env setup → `parse_cluster_config(yaml.safe_load(f))`. The admin container installs `thunder-forge` as a Python package and imports `parse_cluster_config` and `validate_memory` directly. This is a non-breaking refactor of the CLI.
- **Roundtrip integration test:** A shared test roundtrips a known config through both paths (admin JSONB → YAML → `parse_cluster_config` → `validate_memory`, and CLI `load_cluster_config` → validate) and asserts the resulting `ClusterConfig` objects are equivalent. This catches drift between the admin serializer and the CLI parser at test time.

## Data Model

Separate database `thunder_admin` in the existing Postgres instance. Created by the admin-ui container on startup (see Bootstrap).

```sql
-- Operators
CREATE TABLE users (
    id            SERIAL PRIMARY KEY,
    username      TEXT UNIQUE NOT NULL,
    password_hash TEXT NOT NULL,
    is_admin      BOOLEAN DEFAULT FALSE,
    created_at    TIMESTAMPTZ DEFAULT now()
);

-- Full snapshot of node-assignments config as JSONB (append-only)
CREATE TABLE config_versions (
    id            SERIAL PRIMARY KEY,
    config        JSONB NOT NULL,
    author_id     INTEGER REFERENCES users(id),
    comment       TEXT,
    created_at    TIMESTAMPTZ DEFAULT now()
);

-- Deploy log
CREATE TABLE deploys (
    id            SERIAL PRIMARY KEY,
    config_id     INTEGER REFERENCES config_versions(id),
    triggered_by  INTEGER REFERENCES users(id),
    status        TEXT NOT NULL DEFAULT 'running',  -- running, success, failed, cancelled
    output        TEXT,
    started_at    TIMESTAMPTZ DEFAULT now(),
    finished_at   TIMESTAMPTZ
);
```

**JSONB schema:** The `config` column stores the exact structure of `node-assignments.yaml` parsed to JSON. This includes all top-level keys: `models` (with full `source` sub-object: `type`, `repo`, `revision`, `quantize`, `path`, `package`, `weight_repo`), `nodes`, `assignments`, and `external_endpoints`. All fields are preserved on roundtrip — the UI exposes common fields for editing, but the JSONB stores the complete structure including `extra_args`, `serving`, `notes`, etc.

Example JSONB:
```json
{
  "models": {
    "qwen3-30b": {
      "source": {"type": "huggingface", "repo": "mlx-community/Qwen3-30B-A3B-4bit", "revision": "main"},
      "disk_gb": 18, "ram_gb": 15, "kv_per_32k_gb": 0,
      "max_context": 131072,
      "extra_args": null, "serving": "", "notes": "..."
    }
  },
  "nodes": {
    "msm1": {"ip": "192.168.1.101", "ram_gb": 128, "role": "node", "user": "admin"}
  },
  "assignments": {
    "msm1": [{"model": "qwen3-30b", "port": 8000, "embedding": false}]
  },
  "external_endpoints": [
    {"model_name": "qwen3-30b", "api_base": "http://...", "api_key_env": "FINN_LITELLM_KEY"}
  ]
}
```

**Node user field:** Stored explicitly in JSONB. If empty/null, thunder-forge falls back to `TF_SSH_USER` on the gateway host at deploy time (existing behavior). The admin UI cannot preview the resolved user — it only knows the value stored in config. The UI shows the field as optional with placeholder text: "Falls back to TF_SSH_USER on gateway".

**Logic:**
- Each config save = new row in `config_versions` (immutable, append-only)
- "Current config" = `SELECT * FROM config_versions ORDER BY id DESC LIMIT 1`
- Rollback = create new version copying JSONB from an older version
- Each deploy references a specific config version
- "Last deployed config" for diff = `config_id` from most recent `deploys` row where `status = 'success'`
- `users.is_admin` controls access to user management page

**Optimistic locking:** Each config page load records the current `config_versions.id`. On save, the INSERT is atomic — it only succeeds if the loaded version is still the latest:
```sql
INSERT INTO config_versions (config, author_id, comment)
SELECT $config, $author_id, $comment
WHERE (SELECT MAX(id) FROM config_versions) = $loaded_version_id;
```
If 0 rows are affected, the version changed since page load. The UI shows: "Config was modified by {user} while you were editing. Reload and retry." No race window because the check and insert are a single atomic statement.

**Deploy locking (two layers):**

1. **DB-level:** The deploy INSERT is atomic — it only succeeds if no deploy is currently running:
   ```sql
   INSERT INTO deploys (config_id, triggered_by, status)
   SELECT $config_id, $user_id, 'running'
   WHERE NOT EXISTS (SELECT 1 FROM deploys WHERE status = 'running');
   ```
   If 0 rows affected, a deploy is already running. Show "Deploy already in progress (started by {user} at {time})".

   **Stale record recovery:** If a `status = 'running'` row exists but `started_at` is older than a reasonable threshold (e.g., 2 hours — longer than any expected deploy), the UI checks the gateway lock file via SSH before blocking. If the gateway lock is dead/missing, the DB record is stale (container crashed, thread died). The UI marks it as `failed` with `finished_at = now()` and appends "[Marked as failed — process no longer running on gateway]" to output, then allows the new deploy. If the gateway lock is alive, the deploy is genuinely still running.

2. **Gateway-level lock file:** The deploy SSH command checks for `/tmp/thunder-forge-deploy.lock` on the gateway host before starting. The lock file contains:
   ```
   PID:12345
   HEARTBEAT:1711324800
   ```
   The `thunder-forge deploy` command acquires this lock on start, updates the heartbeat timestamp every 30 seconds via a background thread, and removes the lock on exit. Before acquiring, the deploy checks:
   - **PID dead** → stale lock, safe to take
   - **PID alive + heartbeat fresh** (< 5 minutes old) → genuinely running, block
   - **PID alive + heartbeat stale** (> 5 minutes old) → process appears stuck. The admin UI reports: "Deploy process (PID X) appears stuck — last activity {N} minutes ago. Force cancel?" The user can choose to kill the process, clear the lock, and redeploy.

   This protects against the scenario where a DB "cancel" doesn't stop the actual gateway process — the gateway lock is the real source of truth for whether a deploy is running.

**Config validation at save time:** Every config save (from any page) validates cross-entity consistency before inserting a new version. Validation uses the CLI's `parse_cluster_config` and `validate_memory` functions directly (imported as a Python package):
- All models referenced in `assignments` must exist in `models`
- All nodes referenced in `assignments` must exist in `nodes`
- No duplicate ports on the same node
- Memory budget validation (via CLI's `validate_memory`)

Invalid configs are blocked with a clear error message. This prevents saving a config that would fail at deploy time.

**Cascade deletes:** Deleting a model or node that is referenced by assignments triggers a confirmation: "This model is assigned to nodes: msm1:8000, msm2:8001. Remove these assignments and delete the model?" Confirming removes both the entity and its assignments in a single config version save. This avoids a multi-page dance where the user must manually clean up assignments before deleting.

## UI Pages

### 1. Dashboard

- **Gateway connectivity indicator:** green/red badge showing whether the admin UI can SSH to the gateway host. Checked on page load via a trivial paramiko connect + `echo ok`. If unreachable, show: "Cannot reach gateway ({host}) — check SSH config."
- Cluster health status (calls `thunder-forge health --skip-preflight`)
- Summary: node count, model count, last deploy timestamp + status
- Quick links: Grafana, Open WebUI, LiteLLM UI (configurable URLs)

### 2. Config: Models

- Table of models from current config (name, repo, disk_gb, max_context)
- Add model: enter HF repo name, auto-fill from HuggingFace API (see Model Add Flow below)
- Edit / delete model
- Save = new config version in Postgres

### 3. Config: Nodes

- Table of nodes (name, ip, ram_gb, role, user)
- Add / edit / delete node
- Save = new config version

### 4. Config: Assignments

- Which model on which node, port
- Memory budget validation in real-time (same formula as `thunder-forge generate-config`)
- Visual indicator: green (fits), red (exceeds RAM)
- Save = new config version

### 4b. Config: External Endpoints

- Table of external LiteLLM/OpenAI-compatible endpoints (model_name, api_base, api_key_env)
- Add / edit / delete
- Save = new config version

### 5. Config: History

- Table of all config versions: ID, author, comment, created_at, and whether it was deployed (with deploy status)
- **View:** Expander showing the full config as formatted YAML
- **Diff:** Side-by-side or unified diff against the previous version (or a user-selected version)
- **Restore:** Creates a new config version with the old JSONB and comment "Restored from version {N}". After restore, the user is on the latest config and can review/deploy it.
- Links to the deploy page for the "deploy this config" action

### 6. Deploy

- Shows diff: current config vs last deployed config version, rendered as a unified YAML text diff (`difflib.unified_diff`). Added lines in green, removed in red, using Streamlit's `st.code` with diff syntax highlighting. YAML serialization uses a fixed key order (see Architecture) to prevent false diffs from key reordering. If no prior successful deploy exists (first deploy), show the full config as "all additions" with a "First deploy" label.
- "Deploy" button triggers the deploy flow. Disabled with message if a deploy is already running.
- Streaming stdout/stderr output in real-time
- Deploy history table (from `deploys` table). Each row shows: timestamp, user, config version, status, duration. Clicking a row shows the full output log.
- Status badges: running (spinner), success (green), failed (red), cancelled (grey)
- **Cancel deploy:** If the gateway lock shows the process is alive and heartbeat is fresh, the UI offers "Cancel deploy? This will kill the running process." Confirming sends `kill <PID>` via SSH to the gateway, clears the lock file, and updates `deploys.status` to `cancelled`. If the process doesn't die within 10 seconds, escalate to `kill -9` and report. If the process is already dead (container crashed), cancellation just updates the DB status.

### 7. Users (admin only)

- List users, create new user, delete user
- Reset password (generates new random password, displays once)

## Model Add Flow

When adding a model, the admin UI fetches metadata from HuggingFace API to auto-fill fields.

**Auto-filled from HF API (`GET https://huggingface.co/api/models/{repo}`):**
- `disk_gb` — sum of safetensors file sizes from repo siblings
- `max_context` — from config.json (`max_position_embeddings` or model-specific field)
- `kv_per_32k_gb` — computed from config.json: `num_kv_heads * head_dim * num_layers * 2 * 2 * 32768 / 1e9`
- `revision` — current main commit hash

**Manual / optional fields:**
- `ram_gb` — override if runtime RAM differs from disk_gb
- `notes` — free text
- `serving` — embedding, cli, mlx-openai-server, or blank (default: served by mlx_lm.server)

**Validation before save:**
- HF repo exists and is accessible
- Contains safetensors files (not GGUF, not empty)
- Contains `tokenizer_config.json` (required by mlx_lm.server)
- disk_gb fits on at least one node in the cluster

**HF API authentication:** The admin UI supports an optional `HF_TOKEN` env var for accessing gated/private repos. Passed as `Authorization: Bearer {token}` header. If a request returns 403 (gated repo) and no token is configured, show: "This repo requires authentication. Set HF_TOKEN in docker-compose environment."

**Fallback:** If the HF API is unreachable (network error, timeout, rate limited), all fields become manual input. The UI shows a warning banner explaining why auto-fill is unavailable and lets the user proceed with manual entry.

## Deploy Flow (detailed)

1. User clicks "Deploy" in UI
2. Admin UI reads current config from Postgres
3. Generates `node-assignments.yaml` from JSONB
4. SSH copies YAML to gateway host: `configs/node-assignments.yaml`
5. SSH runs on gateway host:
   ```bash
   cd /path/to/thunder-forge && \
   uv run thunder-forge generate-config && \
   uv run thunder-forge ensure-models && \
   uv run thunder-forge deploy
   ```
6. **Note:** `generate-config` writes `configs/litellm-config.yaml` on the gateway, which is tracked in git. With the admin UI as source of truth, this file should be in `.gitignore` (see Migration Path step 1).
7. Deploy runs in a background thread. SSH channel reads stdout/stderr in chunks, appends to `deploys.output` column incrementally
8. UI polls `deploys.output` using `st.empty()` + `time.sleep(1)` loop with `st.rerun()`, showing growing log
9. On completion: thread updates `deploys.status` and `deploys.finished_at`
10. UI detects status change, shows final result with link to deploy details

## SSH Configuration

Admin UI container connects to gateway host via SSH. The gateway SSH credentials are separate from the CLI's node SSH credentials (`TF_SSH_USER`/`TF_SSH_KEY`), since the gateway user/key may differ from the node user/key.

```yaml
# docker-compose.yml
admin-ui:
  environment:
    GATEWAY_SSH_HOST: host.docker.internal
    GATEWAY_SSH_USER: ${GATEWAY_SSH_USER}
    GATEWAY_SSH_KEY: /ssh/id_ed25519
    THUNDER_FORGE_DIR: /path/to/thunder-forge
  volumes:
    - ${GATEWAY_SSH_KEY:-~/.ssh/id_ed25519}:/ssh/id_ed25519:ro
  extra_hosts:
    - "host.docker.internal:host-gateway"
```

**Environment variables (in `.env`):**
- `GATEWAY_SSH_USER` — user for admin-ui container → gateway host SSH (required)
- `GATEWAY_SSH_KEY` — host-side path to the SSH key, mounted read-only into the container (default: `~/.ssh/id_ed25519`)

These are distinct from `TF_SSH_USER`/`TF_SSH_KEY` (which the CLI uses for gateway → node SSH). Per-node SSH key overrides are a future consideration.

**SSH requirements:**
- Gateway host must be running `sshd`
- SSH key mounted read-only. Container entrypoint must `cp /ssh/id_ed25519 /tmp/ssh_key && chmod 400 /tmp/ssh_key` (bind-mounted files may have wrong permissions)
- SSH connections use `StrictHostKeyChecking=no` and `UserKnownHostsFile=/dev/null` (internal network, no host key verification needed)
- `sshd` must listen on the Docker bridge interface (resolved by `host.docker.internal`, typically `172.17.0.1`). If `sshd` is bound to specific interfaces, add `ListenAddress 0.0.0.0` or the bridge IP to `sshd_config`.
- Gateway host user must have: access to thunder-forge repo directory, `uv` installed, SSH keys to all nodes

**Gateway connectivity check:** On container startup (after bootstrap), the admin UI tests the SSH connection via paramiko (connect + `echo ok`). If it fails, a warning is logged to stdout but the container still starts — the dashboard will show the connectivity error.

**THUNDER_FORGE_DIR:** Required env var, no default. Container startup validates it is set and fails loudly if missing.

## Bootstrap and First Run

On every container start, the admin-ui entrypoint runs a bootstrap sequence:

1. **Create database (if needed):** Connect to Postgres using the `litellm` superuser credentials (`postgresql://litellm:${POSTGRES_PASSWORD}@postgres:5432/postgres`) and run `CREATE DATABASE thunder_admin` / `CREATE USER thunder_admin WITH PASSWORD '${ADMIN_DB_PASSWORD}'` if they don't exist. The `litellm` user is a Postgres superuser (created via `POSTGRES_USER` in the Postgres container) and has full `CREATEDB`/`CREATEROLE` privileges. This avoids relying on `docker-entrypoint-initdb.d` scripts, which only run on first Postgres data volume init — existing clusters would never trigger them.
2. **Connect to `thunder_admin` database** using `DATABASE_URL` and run migrations (create tables if not exist).
3. **Create admin account** if `users` table is empty:
   ```
   First run detected. Admin account created:
     Username: admin
     Password: <random-generated>
   Save this password!
   ```
4. Password printed to container stdout (visible in `docker logs admin-ui`).

Steps 1-2 are idempotent — safe to run on every restart.

## CLI Commands

Run inside the admin-ui container:

```bash
# Reset a user's password (prints new password to stdout)
docker exec admin-ui python -m thunder_admin reset-password <username>

# Create a new user
docker exec admin-ui python -m thunder_admin create-user <username>

# Import existing node-assignments.yaml as first config version
# Creates config_version with author_id=NULL, comment="Imported from <filename>"
docker exec admin-ui python -m thunder_admin import-config /path/to/node-assignments.yaml

# List users
docker exec admin-ui python -m thunder_admin list-users

# Export current (or specific) config version as YAML to stdout
docker exec admin-ui python -m thunder_admin export-config [--version N]
```

## Docker Compose Addition

```yaml
admin-ui:
  build:
    context: ..              # repo root — access to admin/ and src/
    dockerfile: admin/Dockerfile
  container_name: admin-ui
  restart: unless-stopped
  ports:
    - "${ADMIN_PORT:-8501}:8501"
  environment:
    DATABASE_URL: postgresql://thunder_admin:${ADMIN_DB_PASSWORD:-admin-local}@postgres:5432/thunder_admin
    POSTGRES_PASSWORD: ${POSTGRES_PASSWORD:-litellm-local}  # for bootstrap CREATE DATABASE/USER
    ADMIN_DB_PASSWORD: ${ADMIN_DB_PASSWORD:-admin-local}    # password for thunder_admin DB user
    GATEWAY_SSH_HOST: host.docker.internal
    GATEWAY_SSH_USER: ${GATEWAY_SSH_USER:?GATEWAY_SSH_USER must be set}
    GATEWAY_SSH_KEY: /ssh/id_ed25519
    THUNDER_FORGE_DIR: ${THUNDER_FORGE_DIR:?THUNDER_FORGE_DIR must be set}
    HF_TOKEN: ${HF_TOKEN:-}
    SESSION_TIMEOUT_HOURS: ${SESSION_TIMEOUT_HOURS:-24}
  volumes:
    - ${GATEWAY_SSH_KEY:-~/.ssh/id_ed25519}:/ssh/id_ed25519:ro
  extra_hosts:
    - "host.docker.internal:host-gateway"
  depends_on:
    postgres: { condition: service_healthy }
  healthcheck:
    test: ["CMD", "curl", "-sf", "http://localhost:8501/_stcore/health"]
    interval: 15s
    timeout: 5s
    retries: 3
  networks: [infra]
```

The docker-compose build context is the repo root (not `admin/`), so the Dockerfile can access both `admin/` and `src/` for installing thunder-forge as a package. A `.dockerignore` at the repo root excludes `.git/`, `tests/`, `docs/`, and other unnecessary directories to keep the build context small. The admin image must be rebuilt (`docker compose build admin-ui`) when CLI validation logic changes.

**No Postgres init script needed.** The admin-ui container handles database creation on startup (see Bootstrap). This works for both fresh installs and existing clusters where the Postgres data volume already exists.

**`host.docker.internal` on Linux:** Requires Docker 20.10+ with the `extra_hosts: host-gateway` mapping (already in the compose file). The Radxa ROCK gateway runs Docker Engine, not Docker Desktop — verify Docker version >= 20.10 during setup.

## Authentication

- Per-user credentials stored in `users` table (bcrypt-hashed passwords)
- Streamlit session state for login (no external auth provider)
- First user auto-created at bootstrap
- Password reset via CLI only (no "forgot password" in UI)
- `is_admin` flag controls access to Users page
- **Session timeout:** Sessions expire after `SESSION_TIMEOUT_HOURS` (default: 24). Login timestamp stored in `st.session_state`; checked on each page load. Expired sessions redirect to login. Container restarts also clear all sessions (Streamlit session state is in-memory).
- **Known session limitations:** Streamlit session state is per-browser-tab — opening a new tab requires logging in again. Sessions can also be lost on WebSocket reconnect (network hiccups). Acceptable for an internal admin tool.

## What This Does NOT Include

- **Monitoring dashboards** — Grafana already handles this. Admin UI links to Grafana.
- **Log viewing** — VictoriaLogs + Grafana handles this.
- **Model serving configuration** (batching params, etc.) — thunder-forge defaults are sufficient.
- **Per-node SSH keys** — currently all nodes use the same `TF_SSH_KEY`. Per-node key overrides in `node-assignments.yaml` are a future consideration.
- **Multi-cluster support** — one admin UI per gateway. Future consideration.
- **HTTPS/TLS** — assumed internal network. Add reverse proxy if needed.
- **GitHub Actions pipeline** — replaced by admin UI deploy. deploy.yml can be removed.
- **Deploy output retention** — deploy logs (`deploys.output`) are kept indefinitely. For clusters with frequent deploys, consider periodic cleanup of old deploy rows (e.g., keep last 100). Not implemented in v1.
- **Efficient deploy log streaming** — v1 polls the full `deploys.output` column on each refresh. For very long deploys, this may become slow. Chunked output storage is a future optimization.

## Migration Path

For existing clusters:

1. Add `configs/litellm-config.yaml` to `.gitignore` — the admin UI is now the source of truth for config, and `generate-config` during deploys will modify this file on the gateway.
2. Verify Docker Engine >= 20.10 on gateway (`docker --version`)
3. Verify `sshd` listens on the Docker bridge interface (`ss -tlnp | grep 22` — should show `0.0.0.0:22` or the bridge IP)
4. Set required env vars in `.env`: `THUNDER_FORGE_DIR`, `GATEWAY_SSH_USER`, and optionally `GATEWAY_SSH_KEY` (defaults to `~/.ssh/id_ed25519`), `ADMIN_DB_PASSWORD` (defaults to `admin-local`)
5. Add admin-ui service to docker-compose.yml
6. `docker compose up -d` (starts admin-ui, bootstrap creates database + admin user)
7. Note admin password from `docker logs admin-ui`
8. Import existing config: `docker cp configs/node-assignments.yaml admin-ui:/tmp/ && docker exec admin-ui python -m thunder_admin import-config /tmp/node-assignments.yaml`
9. Verify config in UI, test deploy
10. Remove GitHub Actions workflow (deploy.yml) if no longer needed

## File Structure

```
admin/                        # at repo root, sibling to docker/ and src/
  Dockerfile
  requirements.txt            # streamlit, psycopg[binary], paramiko, bcrypt, pyyaml, httpx
  thunder_admin/
    __init__.py
    __main__.py               # CLI entry point (reset-password, create-user, import-config)
    app.py                    # Streamlit app entry point
    bootstrap.py              # Database creation, migrations, first-run admin user
    db.py                     # Database connection pool, queries
    auth.py                   # Login, session management, password hashing, session timeout
    config.py                 # Config CRUD, JSONB<->YAML serialization, cross-entity validation
    deploy.py                 # SSH execution, deploy orchestration, deploy locking
    hf.py                     # HuggingFace API integration (model metadata fetch)
    pages/
      dashboard.py
      models.py
      nodes.py
      assignments.py
      external_endpoints.py
      history.py
      deploy.py
      users.py
```

**SSH from container:** Uses `paramiko` (Python SSH library) rather than shelling out to `ssh` binary. This avoids needing `openssh-client` in the container image and gives direct control over the SSH channel for streaming deploy output.
