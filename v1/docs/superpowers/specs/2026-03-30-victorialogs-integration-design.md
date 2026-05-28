# VictoriaLogs Integration Design

**Date:** 2026-03-30
**Status:** Approved
**Scope:** Log aggregation only (no metrics, no Grafana)

## Goal

Centralized log viewing for the inference cluster via VictoriaLogs UI, replacing SSH-based log fetching as the primary way to view service logs. Deployed automatically via `thunder-forge deploy`.

## Architecture

### Gateway (Docker)

Add a `victorialogs` service to `docker/docker-compose.yml`:

- **Image:** `docker.io/victoriametrics/victoria-logs:latest`
- **Port:** `9428` (LAN-accessible for the built-in UI)
- **Volume:** `victorialogs-data` for log persistence
- **Network:** `infra` (same as other services)
- **Healthcheck:** HTTP GET to `/health`
- **Retention:** 30 days default, configurable via `VICTORIALOGS_RETENTION` env var

### Compute Nodes (via deploy)

Each compute node runs a Vector agent that tails local log files and ships them to VictoriaLogs.

**Installation:** `brew install vector` (idempotent, skipped if already installed)

**Config file:** `/etc/vector/vector.yaml` generated per node from template:

```yaml
sources:
  mlx_lm_stdout:
    type: file
    include:
      - $HOME/logs/mlx-lm-*.log
    read_from: end

  mlx_lm_stderr:
    type: file
    include:
      - $HOME/logs/mlx-lm-*.err
    read_from: end

  openai_server_stdout:
    type: file
    include:
      - $HOME/logs/mlx-openai-server-*.log
    read_from: end

  openai_server_stderr:
    type: file
    include:
      - $HOME/logs/mlx-openai-server-*.err
    read_from: end

transforms:
  enrich:
    type: remap
    inputs: ["mlx_lm_stdout", "mlx_lm_stderr", "openai_server_stdout", "openai_server_stderr"]
    source: |
      .host = "<NODE_NAME>"
      filename = string!(.file)
      if contains(filename, "mlx-lm") {
        .job = "mlx-lm"
        .port = replace(replace(filename, r'/.*mlx-lm-', ""), r'\.(log|err)$', "")
      } else if contains(filename, "mlx-openai-server") {
        .job = "mlx-openai-server"
        .port = replace(replace(filename, r'/.*mlx-openai-server-', ""), r'\.(log|err)$', "")
      } else {
        .job = replace(replace(filename, r'/.*/', ""), r'\.(log|err)$', "")
        .port = "0"
      }
      if contains(filename, ".err") {
        .level = "error"
      } else {
        .level = "info"
      }

sinks:
  victorialogs:
    type: elasticsearch
    inputs: ["enrich"]
    endpoints:
      - "http://<GATEWAY_IP>:9428/insert/elasticsearch/"
    mode: bulk
    api_version: v8
    healthcheck:
      enabled: false
    query:
      _msg_field: "message"
      _time_field: "timestamp"
      _stream_fields: "host,job,level,port"
```

`<NODE_NAME>` and `<GATEWAY_IP>` are substituted at deploy time from the cluster config.

**Launchd plist:** `com.vector.plist` in `~/Library/LaunchAgents/`, managed by the same launchctl bootstrap/bootout pattern as mlx-lm services.

## Deploy Pipeline Changes

### `install_node_tools()` — add Vector install

After mlx-lm and mlx-openai-server installation:
1. `brew install vector` (skip if already present via `which vector`)
2. Generate `vector.yaml` with node name and gateway IP substituted
3. Push config to `/etc/vector/vector.yaml` via `scp_content` (requires sudo)
4. Bootstrap the `com.vector` launchd service

### `deploy_node()` — no change needed

Vector runs independently of model services. It's installed once and tails whatever log files exist.

### `stop_node_services()` / `restart_node_services()`

Vector is NOT stopped/restarted with model services — it should run continuously. It has its own start/stop/restart controls in the admin UI (see below).

## Docker Compose Addition

```yaml
  victorialogs:
    image: docker.io/victoriametrics/victoria-logs:latest
    container_name: victorialogs
    restart: unless-stopped
    ports:
      - "9428:9428"
    volumes:
      - victorialogs-data:/vlogs
    command:
      - "-storageDataPath=/vlogs"
      - "-retentionPeriod=${VICTORIALOGS_RETENTION:-30d}"
    healthcheck:
      test: ["CMD", "wget", "-qO-", "http://localhost:9428/health"]
      interval: 15s
      timeout: 5s
      retries: 3
    networks: [infra]
```

Add `victorialogs-data` to the `volumes:` section.

## Admin UI Changes

### Dashboard — add "Logs" quick link

Add a link to `http://<gateway-ip>:9428/select/vmui/` alongside the existing Open WebUI and LiteLLM links. Configurable via `VICTORIALOGS_URL` env var.

### Services page — keep SSH log viewer

The existing SSH-based log viewer stays as a fallback for when VictoriaLogs is not running or Vector hasn't shipped logs yet.

### Services page — Vector controls per node

Add a "Vector" section per node with:
- **Status indicator** — check if `com.vector` launchd service is running (same pattern as `check_service` in `checks.py`)
- **Start / Stop / Restart buttons** — manage the `com.vector` launchd service via SSH (`launchctl bootstrap`/`bootout`/`kickstart`)
- **View Logs** — fetch Vector's own logs. Vector logs to stdout/stderr by default; configure the plist to write to `~/logs/vector.log` and `~/logs/vector.err`, then use the same `fetch_logs` function with a `vector` log path pattern.

Vector controls are separate from the model service controls (Restart All / Stop All do NOT affect Vector).

## Labels

| Label | Source | Example |
|-------|--------|---------|
| `host` | node name from config | `msm1` |
| `job` | derived from filename | `mlx-lm`, `mlx-openai-server` |
| `port` | derived from filename | `8000`, `8001` |
| `level` | `.err` → error, `.log` → info | `error`, `info` |

## Example LogsQL Queries

```
# All errors on msm3
host:msm3 AND level:error

# coder-fast service on port 8002
port:8002

# All logs from a specific node in last hour
host:msm4 | _time:1h
```

## Security

- VictoriaLogs is unauthenticated, LAN-accessible on port 9428
- Accepted risk: cluster runs on an isolated VLAN
- No sensitive data in inference logs (model weights, prompts are not logged by mlx-lm)

## Out of Scope

- VictoriaMetrics (time-series metrics)
- Grafana dashboards
- macOS system exporter
- Log-based alerting
