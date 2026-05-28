# VictoriaLogs Integration Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Centralized log viewing via VictoriaLogs UI with automatic Vector deployment to compute nodes.

**Architecture:** VictoriaLogs runs as a Docker container on the gateway (port 9428). Each compute node runs a Vector agent that tails mlx-lm log files and ships them to VictoriaLogs via the elasticsearch sink. Vector is installed and managed by `thunder-forge deploy`, with start/stop/restart controls in the admin UI.

**Tech Stack:** VictoriaLogs (Docker), Vector (brew on macOS nodes), Streamlit (admin UI)

---

## File Structure

| File | Action | Responsibility |
|------|--------|---------------|
| `docker/docker-compose.yml` | Modify | Add victorialogs service |
| `.env.example` | Modify | Add VICTORIALOGS_URL and VICTORIALOGS_RETENTION vars |
| `src/thunder_forge/cluster/deploy.py` | Modify | Vector install, config generation, launchd management |
| `src/thunder_forge/cluster/config.py` | Modify | Add public load_config helper |
| `admin/thunder_admin/pages/dashboard.py` | Modify | Add VictoriaLogs quick link |
| `admin/thunder_admin/pages/services.py` | Modify | Add Vector controls per node |
| `admin/thunder_admin/checks.py` | Modify | Add check_vector_status and fetch_vector_logs |

---

### Task 1: Add VictoriaLogs to Docker Compose

**Files:**
- Modify: `docker/docker-compose.yml:122-128` (before networks/volumes sections)
- Modify: `.env.example`

- [ ] **Step 1: Add victorialogs service to docker-compose.yml**

Add the following service before the `networks:` section in `docker/docker-compose.yml`:

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

Add `victorialogs-data:` to the `volumes:` section at the bottom.

- [ ] **Step 2: Add env vars to .env.example**

Add after the `GRAFANA_URL` line:

```
# VictoriaLogs - centralized log viewer (auto-configured by docker-compose)
VICTORIALOGS_URL=http://localhost:9428/select/vmui
VICTORIALOGS_RETENTION=30d
```

- [ ] **Step 3: Add VICTORIALOGS_URL to admin-ui environment in docker-compose.yml**

Add to the admin-ui `environment:` section (after `LITELLM_URL`):

```yaml
      VICTORIALOGS_URL: ${VICTORIALOGS_URL:-http://localhost:9428/select/vmui}
```

- [ ] **Step 4: Commit**

```bash
git add docker/docker-compose.yml .env.example
git commit -m "feat: add VictoriaLogs container to Docker Compose stack"
```

---

### Task 2: Vector Config Generation and Installation in Deploy Pipeline

**Files:**
- Modify: `src/thunder_forge/cluster/deploy.py`

- [ ] **Step 1: Add Vector config template function**

Add after the `NEWSYSLOG_CONF` constant (around line 225) in `deploy.py`:

```python
def _generate_vector_config(node_name: str, gateway_ip: str) -> str:
    """Generate vector.yaml config for a compute node."""
    return f"""\
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
      .host = "{node_name}"
      filename = string!(.file)
      if contains(filename, "mlx-lm") {{
        .job = "mlx-lm"
        .port = replace(replace(filename, r'/.*mlx-lm-', ""), r'\\.(log|err)$', "")
      }} else if contains(filename, "mlx-openai-server") {{
        .job = "mlx-openai-server"
        .port = replace(replace(filename, r'/.*mlx-openai-server-', ""), r'\\.(log|err)$', "")
      }} else {{
        .job = replace(replace(filename, r'/.*/', ""), r'\\.(log|err)$', "")
        .port = "0"
      }}
      if contains(filename, ".err") {{
        .level = "error"
      }} else {{
        .level = "info"
      }}

sinks:
  victorialogs:
    type: elasticsearch
    inputs: ["enrich"]
    endpoints:
      - "http://{gateway_ip}:9428/insert/elasticsearch/"
    mode: bulk
    api_version: v8
    healthcheck:
      enabled: false
    query:
      _msg_field: "message"
      _time_field: "timestamp"
      _stream_fields: "host,job,level,port"
"""
```

- [ ] **Step 2: Add Vector plist generation function**

Add after the vector config function:

```python
def _generate_vector_plist(home: str, homebrew_prefix: str | None = None) -> str:
    """Generate a launchd plist for Vector."""
    prefix = homebrew_prefix or f"{home}/.homebrew"
    vector_bin = f"{prefix}/bin/vector"

    plist = ET.Element("plist", version="1.0")
    top = ET.SubElement(plist, "dict")

    def add(key: str, tag: str, text: str) -> None:
        ET.SubElement(top, "key").text = key
        ET.SubElement(top, tag).text = text

    add("Label", "string", "com.vector")
    ET.SubElement(top, "key").text = "ProgramArguments"
    arr = ET.SubElement(top, "array")
    for arg in [vector_bin, "--config", "/etc/vector/vector.yaml"]:
        ET.SubElement(arr, "string").text = arg
    add("StandardOutPath", "string", f"{home}/logs/vector.log")
    add("StandardErrorPath", "string", f"{home}/logs/vector.err")
    ET.SubElement(top, "key").text = "RunAtLoad"
    ET.SubElement(top, "true")
    ET.SubElement(top, "key").text = "KeepAlive"
    ET.SubElement(top, "true")
    add("ThrottleInterval", "integer", "10")

    raw = ET.tostring(plist, encoding="unicode")
    header = (
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        '<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN"'
        ' "http://www.apple.com/DTDs/PropertyList-1.0.dtd">\n'
    )
    return header + raw + "\n"
```

- [ ] **Step 3: Add helper to kill a process by name**

Add near `_kill_port`:

```python
def _kill_process_by_name(node: Node, process_name: str) -> None:
    """Kill a process by name and wait for it to exit."""
    ssh_run(
        node.user, node.ip,
        f"pkill -9 -f {process_name} 2>/dev/null; sleep 1",
        timeout=10, shell=node.shell,
    )
```

- [ ] **Step 4: Add `install_vector` function**

Add after `install_node_tools`:

```python
def install_vector(node: Node, node_name: str, gateway_ip: str) -> None:
    """Install and configure Vector log shipper on a compute node."""
    result = ssh_run(
        node.user, node.ip,
        "which vector >/dev/null 2>&1 || brew install vector",
        timeout=120, shell=node.shell,
    )
    if result.returncode != 0:
        print(f"  Warning: vector install failed: {(result.stderr or '').strip()} (continuing)")
        return

    config_yaml = _generate_vector_config(node_name, gateway_ip)
    ssh_run(node.user, node.ip, "sudo mkdir -p /etc/vector", timeout=10, shell=node.shell)
    scp_content(node.user, node.ip, config_yaml, "/tmp/vector.yaml", shell=node.shell)
    ssh_run(
        node.user, node.ip, "sudo mv /tmp/vector.yaml /etc/vector/vector.yaml",
        timeout=10, shell=node.shell,
    )

    _require_resolved(node, "vector")
    plist_xml = _generate_vector_plist(node.home_dir, node.homebrew_prefix)
    plist_path = "~/Library/LaunchAgents/com.vector.plist"
    scp_content(node.user, node.ip, plist_xml, plist_path, shell=node.shell)

    result = ssh_run(node.user, node.ip, "id -u", shell=node.shell)
    if result.returncode != 0:
        print("  Warning: could not get UID for vector service start")
        return
    uid = result.stdout.strip()
    domain = f"gui/{uid}"

    ssh_run(node.user, node.ip, f"launchctl bootout {domain}/com.vector 2>/dev/null", shell=node.shell)
    _kill_process_by_name(node, "vector")
    result = ssh_run(
        node.user, node.ip, f"launchctl bootstrap {domain} {plist_path}",
        timeout=30, shell=node.shell,
    )
    if result.returncode != 0:
        print(f"  Warning: vector service start failed: {(result.stderr or '').strip()}")
    else:
        print(f"  vector installed and running on {node_name}")
```

- [ ] **Step 5: Call `install_vector` from `deploy_node`**

In `deploy_node()`, add after the `install_node_tools` call (around line 280):

```python
    # Install and configure Vector log shipper
    install_vector(node, node_name, config.gateway.ip)
```

- [ ] **Step 6: Commit**

```bash
git add src/thunder_forge/cluster/deploy.py
git commit -m "feat: deploy Vector log shipper to compute nodes"
```

---

### Task 3: Vector Start/Stop/Restart Functions

**Files:**
- Modify: `src/thunder_forge/cluster/deploy.py`

- [ ] **Step 1: Add vector service management functions**

Add after `stop_node_services`:

```python
def restart_vector(node_name: str, config: ClusterConfig) -> str | None:
    """Restart Vector on a node. Returns error string or None on success."""
    node = config.nodes[node_name]
    uid_result = ssh_run(node.user, node.ip, "id -u", shell=node.shell)
    if uid_result.returncode != 0:
        return f"{node_name}: failed to get UID"
    uid = uid_result.stdout.strip()
    domain = f"gui/{uid}"
    plist_path = "~/Library/LaunchAgents/com.vector.plist"

    ssh_run(node.user, node.ip, f"launchctl bootout {domain}/com.vector 2>/dev/null", shell=node.shell)
    _kill_process_by_name(node, "vector")
    result = ssh_run(
        node.user, node.ip, f"launchctl bootstrap {domain} {plist_path}",
        timeout=30, shell=node.shell,
    )
    if result.returncode != 0:
        return f"{node_name}: vector restart failed"
    return None


def stop_vector(node_name: str, config: ClusterConfig) -> str | None:
    """Stop Vector on a node. Returns error string or None on success."""
    node = config.nodes[node_name]
    uid_result = ssh_run(node.user, node.ip, "id -u", shell=node.shell)
    if uid_result.returncode != 0:
        return f"{node_name}: failed to get UID"
    uid = uid_result.stdout.strip()
    ssh_run(
        node.user, node.ip, f"launchctl bootout gui/{uid}/com.vector 2>/dev/null",
        shell=node.shell,
    )
    _kill_process_by_name(node, "vector")
    return None


def start_vector(node_name: str, config: ClusterConfig) -> str | None:
    """Start Vector on a node. Returns error string or None on success."""
    node = config.nodes[node_name]
    uid_result = ssh_run(node.user, node.ip, "id -u", shell=node.shell)
    if uid_result.returncode != 0:
        return f"{node_name}: failed to get UID"
    uid = uid_result.stdout.strip()
    plist_path = "~/Library/LaunchAgents/com.vector.plist"
    result = ssh_run(
        node.user, node.ip, f"launchctl bootstrap gui/{uid} {plist_path}",
        timeout=30, shell=node.shell,
    )
    if result.returncode != 0:
        return f"{node_name}: vector start failed"
    return None
```

- [ ] **Step 2: Commit**

```bash
git add src/thunder_forge/cluster/deploy.py
git commit -m "feat: add vector start/stop/restart functions"
```

---

### Task 4: Add `load_config` helper for programmatic access

**Files:**
- Modify: `src/thunder_forge/cluster/config.py`

The Vector start/stop commands in the admin UI run via SSH on the gateway using `python -c` with `load_config()`. The CLI has a private `_load_config()` in `cli.py` but no public equivalent.

- [ ] **Step 1: Add `load_config` to config.py**

Add at the end of `config.py`:

```python
def load_config() -> tuple[ClusterConfig, Path]:
    """Load cluster config from the default node-assignments.yaml. Returns (config, path)."""
    root = find_repo_root()
    config_path = root / "configs" / "node-assignments.yaml"
    raw = yaml.safe_load(config_path.read_text())
    return parse_cluster_config(raw), config_path
```

- [ ] **Step 2: Commit**

```bash
git add src/thunder_forge/cluster/config.py
git commit -m "feat: add public load_config helper for programmatic access"
```

---

### Task 5: Admin UI - VictoriaLogs Quick Link on Dashboard

**Files:**
- Modify: `admin/thunder_admin/pages/dashboard.py`

- [ ] **Step 1: Add VictoriaLogs link to Quick Links section**

In `dashboard.py`, change the quick links section. Replace the 3-column layout with 4 columns and add VictoriaLogs:

```python
    # Quick links
    st.subheader("Quick Links")
    links_col1, links_col2, links_col3, links_col4 = st.columns(4)
    grafana = os.environ.get("GRAFANA_URL", "")
    webui = os.environ.get("OPENWEBUI_URL", "")
    litellm = os.environ.get("LITELLM_URL", "")
    vlogs = os.environ.get("VICTORIALOGS_URL", "")
    if grafana:
        links_col1.link_button("Grafana", grafana)
    if webui:
        links_col2.link_button("Open WebUI", webui)
    if litellm:
        links_col3.link_button("LiteLLM", litellm)
    if vlogs:
        links_col4.link_button("Logs", vlogs)
```

- [ ] **Step 2: Commit**

```bash
git add admin/thunder_admin/pages/dashboard.py
git commit -m "feat: add VictoriaLogs quick link to dashboard"
```

---

### Task 6: Admin UI - Vector Status Check and Log Fetch

**Files:**
- Modify: `admin/thunder_admin/checks.py`

- [ ] **Step 1: Add `check_vector_status` function**

Add after `fetch_logs` in `checks.py`:

```python
def check_vector_status(node: Node) -> tuple[Literal["ok", "error"], str]:
    """Check if Vector is running on a node via launchctl."""
    try:
        client = paramiko.SSHClient()
        client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
        pkey = _resolve_ssh_key()
        client.connect(
            hostname=node.ip,
            username=node.user,
            pkey=pkey,
            timeout=_SSH_TIMEOUT,
            look_for_keys=False,
            allow_agent=False,
        )
        _, stdout, _ = client.exec_command("launchctl list com.vector", timeout=_SSH_TIMEOUT)
        output = stdout.read().decode()
        exit_code = stdout.channel.recv_exit_status()
        client.close()
        if exit_code != 0 or '"PID"' not in output:
            return ("error", "not running")
        return ("ok", "running")
    except Exception as e:
        return ("error", str(e)[:120])


def fetch_vector_logs(node: Node, tail_lines: int = 100) -> dict[str, str]:
    """Fetch Vector's own stderr and stdout logs via SSH."""
    result = {"stderr": "", "stdout": ""}
    try:
        client = paramiko.SSHClient()
        client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
        pkey = _resolve_ssh_key()
        client.connect(
            hostname=node.ip,
            username=node.user,
            pkey=pkey,
            timeout=_SSH_TIMEOUT,
            look_for_keys=False,
            allow_agent=False,
        )
        for key, suffix in [("stderr", "err"), ("stdout", "log")]:
            path = f"~/logs/vector.{suffix}"
            _, stdout, _ = client.exec_command(
                f"tail -n {tail_lines} {path} 2>&1", timeout=_SSH_TIMEOUT,
            )
            result[key] = stdout.read().decode()
        client.close()
    except Exception as e:
        result["stderr"] = f"Failed to fetch logs: {e}"
    return result
```

- [ ] **Step 2: Commit**

```bash
git add admin/thunder_admin/checks.py
git commit -m "feat: add vector status check and log fetch functions"
```

---

### Task 7: Admin UI - Vector Controls on Services Page

**Files:**
- Modify: `admin/thunder_admin/pages/services.py`

- [ ] **Step 1: Update imports**

The final imports block at the top of `services.py` should be:

```python
from __future__ import annotations

import os
import time

import streamlit as st

from thunder_admin import db
from thunder_admin.checks import check_vector_status, fetch_logs, fetch_vector_logs
from thunder_admin.deploy import ssh_exec, start_service_op
from thunder_admin.tz import format_dt
from thunder_forge.cluster.config import Node, parse_cluster_config
```

- [ ] **Step 2: Add Vector section to `_render_op_buttons`**

After the per-slot log viewer for loop (after the closing of the slot loop), add within the `for node_name in node_names:` loop:

```python
            # Vector agent controls
            st.markdown(f"**Vector** on {node_name}")
            node_obj = cluster.nodes.get(node_name) if cluster else None
            if node_obj:
                resolved_user = node_obj.user or os.environ.get("GATEWAY_SSH_USER", "")
                resolved_node = Node(
                    ip=node_obj.ip, ram_gb=node_obj.ram_gb,
                    user=resolved_user, role=node_obj.role,
                )

                # Status check
                status_key = f"vector_status_{node_name}"
                if st.button("Check Vector Status", key=f"btn_vstatus_{node_name}"):
                    st.session_state[status_key] = check_vector_status(resolved_node)
                if status_key in st.session_state:
                    status, detail = st.session_state[status_key]
                    if status == "ok":
                        st.success(f"Vector: {detail}")
                    else:
                        st.error(f"Vector: {detail}")

                # Start / Stop / Restart
                vcol1, vcol2, vcol3 = st.columns(3)
                tf_dir = os.environ.get("THUNDER_FORGE_DIR", "")
                with vcol1:
                    if st.button("Start", key=f"vstart_{node_name}"):
                        cmd = (
                            f"cd {tf_dir} && set -a && [ -f .env ] && . ./.env && set +a && "
                            f"~/.local/bin/uv run python -c \""
                            f"from thunder_forge.cluster.config import load_config; "
                            f"from thunder_forge.cluster.deploy import start_vector; "
                            f"c, _ = load_config(); "
                            f"err = start_vector('{node_name}', c); "
                            f"print(err or 'ok')\""
                        )
                        _, output = ssh_exec(cmd, timeout=30)
                        if "ok" in output:
                            st.success(f"Vector started on {node_name}")
                        else:
                            st.error(output.strip())
                with vcol2:
                    if st.button("Stop", key=f"vstop_{node_name}"):
                        cmd = (
                            f"cd {tf_dir} && set -a && [ -f .env ] && . ./.env && set +a && "
                            f"~/.local/bin/uv run python -c \""
                            f"from thunder_forge.cluster.config import load_config; "
                            f"from thunder_forge.cluster.deploy import stop_vector; "
                            f"c, _ = load_config(); "
                            f"err = stop_vector('{node_name}', c); "
                            f"print(err or 'ok')\""
                        )
                        _, output = ssh_exec(cmd, timeout=30)
                        if "ok" in output:
                            st.success(f"Vector stopped on {node_name}")
                        else:
                            st.error(output.strip())
                with vcol3:
                    if st.button("Restart", key=f"vrestart_{node_name}"):
                        cmd = (
                            f"cd {tf_dir} && set -a && [ -f .env ] && . ./.env && set +a && "
                            f"~/.local/bin/uv run python -c \""
                            f"from thunder_forge.cluster.config import load_config; "
                            f"from thunder_forge.cluster.deploy import restart_vector; "
                            f"c, _ = load_config(); "
                            f"err = restart_vector('{node_name}', c); "
                            f"print(err or 'ok')\""
                        )
                        _, output = ssh_exec(cmd, timeout=30)
                        if "ok" in output:
                            st.success(f"Vector restarted on {node_name}")
                        else:
                            st.error(output.strip())

                # Vector logs
                with st.expander(f"Vector Logs - {node_name}"):
                    vlines = st.select_slider(
                        "Lines", options=[100, 500, 1000, 5000], value=500,
                        key=f"vlines_{node_name}",
                    )
                    vdata_key = f"data_vlogs_{node_name}"
                    if st.button("Fetch Logs", key=f"btn_vlogs_{node_name}"):
                        st.session_state[vdata_key] = fetch_vector_logs(
                            resolved_node, tail_lines=vlines,
                        )
                    if vdata_key in st.session_state:
                        vlogs = st.session_state[vdata_key]
                        vtab_err, vtab_out = st.tabs(["stderr", "stdout"])
                        with vtab_err:
                            st.code(vlogs["stderr"] or "(empty)", language="log")
                        with vtab_out:
                            st.code(vlogs["stdout"] or "(empty)", language="log")

            st.divider()
```

- [ ] **Step 3: Lint and fix**

Run: `uv run ruff check admin/thunder_admin/pages/services.py`
Fix any line-length or import issues.

- [ ] **Step 4: Commit**

```bash
git add admin/thunder_admin/pages/services.py
git commit -m "feat: add Vector controls and log viewer to services page"
```

---

### Task 8: Final Integration Test

- [ ] **Step 1: Lint all changed files**

```bash
uv run ruff check src/thunder_forge/cluster/deploy.py src/thunder_forge/cluster/config.py admin/thunder_admin/pages/services.py admin/thunder_admin/pages/dashboard.py admin/thunder_admin/checks.py
uv run ruff format src/thunder_forge/cluster/deploy.py src/thunder_forge/cluster/config.py admin/thunder_admin/pages/services.py admin/thunder_admin/pages/dashboard.py admin/thunder_admin/checks.py
```

- [ ] **Step 2: Run existing tests**

```bash
uv run pytest tests/ -v
```

- [ ] **Step 3: Push all commits**

```bash
git push origin main && git push upstream main
```

- [ ] **Step 4: Deploy to gateway and verify**

On the gateway:
```bash
git pull && uv sync && make restart
```

Verify:
1. VictoriaLogs UI is accessible at `http://<gateway-ip>:9428/select/vmui/`
2. Admin UI dashboard shows "Logs" quick link
3. Admin UI services page shows Vector controls per node
4. Deploy a node to install Vector: `uv run thunder-forge deploy --node <name>`
5. Check VictoriaLogs UI for incoming logs after Vector starts shipping
