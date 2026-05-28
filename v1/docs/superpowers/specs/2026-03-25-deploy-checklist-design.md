# Deploy Checklist — Design Spec

**Date:** 2026-03-25
**Feature:** Pre-deploy status checklist on the Deploy page

## Overview

Add a "Check Status" button to the Deploy page that runs a 5-step pipeline check for each assignment slot and displays the results as a compact status row. The goal is to make it immediately visible what's deployable and what's blocked before pressing Deploy.

## UI Layout

On the Deploy page, between "Changes to Deploy" and the Deploy button:

```
[Check Status]

air / fast:8000   ✓ config  ✓ ssh  ✓ model  ✗ service  – port
                                      com.mlx-lm-8000 not found

[Deploy ▶]
```

- Each assignment slot renders as one row with 5 status columns
- Status icons: `✓` green (ok), `✗` red (error), `⚠` yellow (warn), `–` grey (skipped / upstream failed)
- Errors show a caption/detail line below the row
- Checks are invalidated when the config version changes

## Checks Pipeline

Checks run in parallel via `ThreadPoolExecutor`, one thread per slot.

| Step | Type | What it checks |
|------|------|----------------|
| **config** | static (no SSH) | RAM fits on node, port is unique per node, model and node exist in config — delegates to existing `validate_config()` and reports all errors (not scoped per slot — config errors are rare and a global list is sufficient) |
| **ssh** | SSH to compute node | `echo ok` on `node.ip` using `node.user` — confirms connectivity |
| **model** | SSH to compute node | For `huggingface` source type: `ls ~/.cache/huggingface/hub/models--{org}--{name}/` where path is derived by replacing `/` with `--` in `source.repo` (assumes default HF cache — nodes don't set custom `HF_HOME`). For any other source type (`local`, `pip`, `convert`, or future types): returns `("warn", "non-HF source; skipping model check")` |
| **service** | SSH to compute node | macOS: `launchctl list com.mlx-lm-{port}` and grep for `"PID"` key in output — PID present means running, absent means stopped/crashed. Linux: `systemctl is-active thunder-forge-{port}` |
| **port** | HTTP | `GET http://{node_ip}:{port}/v1/models` with 3s timeout |

Steps after a failed dependency return `("skip", "")` (grey `–`) to avoid misleading results:
- `model`, `service`, `port` are skipped if `ssh` fails
- `port` is also skipped if `service` is not running
- If config check fails, remaining steps still run (config errors are non-blocking warnings)

## Data Model

```python
# Check result tuple
CheckResult = tuple[Literal["ok", "warn", "error", "skip"], str]  # (status, detail_message)

# Per-slot results dict
SlotChecks = dict[str, CheckResult]  # keys: "config", "ssh", "model", "service", "port"

# Session state key: (node_name, port) — port is unique per node (enforced by validate_config)
# st.session_state["deploy_checks"]: dict[tuple[str, int], SlotChecks]
# st.session_state["deploy_checks_config_id"]: int — invalidate when db.get_current_config()["id"] changes
```

## Code Structure

### New file: `admin/thunder_admin/checks.py`

Five check functions, all returning `CheckResult`:

- `check_config(config) -> CheckResult` — static only, no I/O; delegates to `validate_config()` and returns all errors joined with `"; "` (capped at 120 chars). Not scoped per slot — config errors are global and rare
- `check_ssh(node) -> CheckResult` — paramiko `echo ok` to `node.ip` as `node.user`; on success, returns the open connection for reuse
- `check_model(ssh_conn, node, slot, config) -> CheckResult` — SSH ls on HF cache path; skip for non-HF sources
- `check_service(ssh_conn, node, slot) -> CheckResult` — SSH launchctl (grep PID) on macOS, systemctl on Linux
- `check_port(node, slot) -> CheckResult` — HTTP GET /v1/models timeout 3s

SSH connection reuse: `check_ssh` opens a paramiko connection to `node.ip`/`node.user` and returns it alongside the `CheckResult`. Subsequent SSH checks (`check_model`, `check_service`) reuse that connection instead of opening new ones. This avoids 3 separate handshakes per slot. All connections are independent of the gateway `ssh_exec` helper (which is gateway-only).

Node user resolution: `run_all_checks` parses the raw config dict via `parse_cluster_config()` to get `Node` dataclass objects. If `node.user` is empty after parsing, fall back to `TF_SSH_USER` environment variable; if still empty, return `("error", "node user not configured")` without attempting a connection.

Entry point:

```python
def run_all_checks(config: dict) -> dict[tuple[str, int], SlotChecks]:
    """Run all checks for all assignment slots in parallel. Returns (node_name, port) → SlotChecks.

    Internally parses raw config dict via parse_cluster_config() to get Node/Assignment dataclasses.
    """
    ...
```

### Modified: `admin/thunder_admin/pages/deploy.py`

- Add "Check Status" `st.button` above the Deploy button; hide if no assignments
- On click: call `run_all_checks(config)`, store results + config id in session state
- After checks: render one row per slot using `st.columns([3, 1, 1, 1, 1, 1])`
- Show `st.info("No assignments to check")` when assignments is empty
- Invalidate cache when `current["id"] != st.session_state.get("deploy_checks_config_id")`

## Error Handling

- SSH timeout (10s per check) → `("error", "SSH timeout")`
- HTTP timeout (3s) → `("error", "timeout")`
- Unexpected exceptions → `("error", str(e)[:120])`
- No assignments in config → show info message instead of checklist

## Non-Goals

- No auto-refresh / polling — checks are on-demand only
- No blocking the Deploy button based on check results — user decides
- No checks for external endpoints (they have their own validation)
- No partial re-check — "Check Status" always re-runs all slots
