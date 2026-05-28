# admin/thunder_admin/pages/deploy.py
"""Deploy page — trigger deploys, view streaming output, deploy history."""

from __future__ import annotations

import difflib
import time
from datetime import UTC, datetime

import streamlit as st

from thunder_admin import db
from thunder_admin.checks import run_all_checks
from thunder_admin.config import jsonb_to_yaml
from thunder_admin.deploy import (
    check_gateway_lock_alive,
    clear_gateway_lock,
    kill_gateway_deploy,
    read_gateway_lock,
    start_deploy,
)
from thunder_admin.tz import format_dt

_STATUS_ICONS = {
    "ok": ":green[✓]",
    "error": ":red[✗]",
    "warn": ":orange[⚠]",
    "skip": ":grey[–]",
}
_CHECK_LABELS = ["config", "ssh", "model", "service", "port"]


def _render_check_results(results: dict, config: dict) -> None:
    """Render one compact status row per assignment slot."""
    assignments = config.get("assignments", {})
    for node_name, slots in assignments.items():
        for slot_dict in slots:
            port = slot_dict.get("port")
            if port is None:
                continue
            model = slot_dict.get("model", "?")
            key = (node_name, port)
            slot_checks = results.get(key)
            if slot_checks is None:
                continue

            cols = st.columns([3, 1, 1, 1, 1, 1])
            cols[0].markdown(f"**{node_name} / {model}:{port}**")
            for i, check_name in enumerate(_CHECK_LABELS):
                status, _ = slot_checks.get(check_name, ("skip", ""))
                cols[i + 1].markdown(f"{_STATUS_ICONS.get(status, '?')} {check_name}")

            # Error/warn detail lines
            for check_name in _CHECK_LABELS:
                status, detail = slot_checks.get(check_name, ("skip", ""))
                if detail and status in ("error", "warn"):
                    st.caption(f"{check_name}: {detail}")


def render(user: dict):
    st.header("Deploy")

    current = db.get_current_config()
    if not current:
        st.warning("No config to deploy. Add models and nodes first.")
        return

    # Diff: current config vs last deployed
    st.subheader("Changes to Deploy")
    current_yaml = jsonb_to_yaml(current["config"])
    last_deploy = db.get_last_successful_deploy()

    if last_deploy:
        last_config = db.get_config_version(last_deploy["config_id"])
        if last_config:
            last_yaml = jsonb_to_yaml(last_config["config"])
            diff = difflib.unified_diff(
                last_yaml.splitlines(keepends=True),
                current_yaml.splitlines(keepends=True),
                fromfile=f"v{last_config['id']} (deployed)",
                tofile=f"v{current['id']} (current)",
            )
            diff_str = "".join(diff)
            if diff_str:
                st.code(diff_str, language="diff")
            else:
                st.info("No changes since last deploy")
        else:
            st.code(current_yaml, language="yaml")
    else:
        st.caption("First deploy — full config:")
        st.code(current_yaml, language="yaml")

    # Check Status section
    assignments = current["config"].get("assignments", {})
    if not assignments:
        st.info("No assignments to check")
    else:
        # Invalidate cached checks when config version changes
        if st.session_state.get("deploy_checks_config_id") != current["id"]:
            st.session_state.pop("deploy_checks", None)
            st.session_state.pop("deploy_checks_config_id", None)

        if st.button("Check Status"):
            with st.spinner("Running checks..."):
                st.session_state["deploy_checks"] = run_all_checks(current["config"])
                st.session_state["deploy_checks_config_id"] = current["id"]

        check_results: dict = st.session_state.get("deploy_checks", {})
        if check_results:
            _render_check_results(check_results, current["config"])

    # Deploy button or running status
    running = db.get_running_deploy()
    if running:
        st.warning(
            f"Deploy in progress (started by "
            f"{running.get('triggered_by_name', '?')} at "
            f"{format_dt(running['started_at'], user, fmt='%H:%M')})"
        )

        # Show streaming output
        output_placeholder = st.empty()
        deploy = db.get_deploy(running["id"])
        if deploy:
            output_placeholder.code(deploy.get("output", "") or "Waiting for output...")
            if deploy["status"] == "running":
                # Poll for updates
                time.sleep(2)
                st.rerun()
            elif deploy["status"] == "success":
                st.success("Deploy completed successfully!")
            else:
                st.error(f"Deploy {deploy['status']}")

        # Cancel button
        lock = read_gateway_lock()
        lock_status = check_gateway_lock_alive(lock) if lock else "dead"
        pid = lock.get("PID") if lock else None

        if lock_status == "alive" and pid:
            if st.button("Cancel Deploy", type="secondary"):
                if kill_gateway_deploy(pid):
                    db.update_deploy(
                        running["id"],
                        status="cancelled",
                        finished_at=datetime.now(UTC),
                    )
                    st.warning("Deploy cancelled")
                    st.rerun()
                else:
                    st.error("Failed to cancel deploy")
        elif lock_status == "stale" and pid:
            st.warning(f"Deploy process (PID {pid}) appears stuck. Force cancel?")
            if st.button("Force Cancel"):
                if kill_gateway_deploy(str(pid)):
                    db.update_deploy(
                        running["id"],
                        status="cancelled",
                        finished_at=datetime.now(UTC),
                    )
                    st.rerun()
        else:
            st.warning("Deploy process is no longer running.")
            if st.button("Clear Stuck Deploy", type="secondary"):
                clear_gateway_lock()
                db.update_deploy(running["id"], status="failed", finished_at=datetime.now(UTC))
                st.rerun()
    else:
        if st.button("Deploy", type="primary"):
            deploy_id, error = start_deploy(current["id"], user["id"], current_yaml)
            if deploy_id:
                st.success(f"Deploy started (ID: {deploy_id})")
                st.rerun()
            else:
                st.error(error)

    # Deploy history
    st.subheader("Deploy History")
    deploys = db.list_deploys(limit=20)
    if deploys:
        for d in deploys:
            status_icons = {
                "success": "green",
                "failed": "red",
                "running": "orange",
                "cancelled": "grey",
            }
            color = status_icons.get(d["status"], "grey")
            duration = ""
            if d.get("finished_at") and d.get("started_at"):
                secs = (d["finished_at"] - d["started_at"]).total_seconds()
                duration = f" ({int(secs)}s)"

            with st.expander(
                f":{color}[Deploy #{d['id']}] — "
                f"{d.get('triggered_by_name', '?')} — "
                f"{format_dt(d['started_at'], user)} — "
                f"config v{d['config_id']} — {d['status']}{duration}",
                expanded=False,
            ):
                if d.get("output"):
                    st.code(d["output"])
                else:
                    st.caption("No output recorded")
    else:
        st.info("No deploys yet.")
