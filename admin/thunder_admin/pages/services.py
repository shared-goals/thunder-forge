# admin/thunder_admin/pages/services.py
"""Services page — start, stop, and restart inference services."""

from __future__ import annotations

import os
import time

import streamlit as st

from thunder_admin import db
from thunder_admin.checks import fetch_logs
from thunder_admin.deploy import start_service_op
from thunder_admin.tz import format_dt
from thunder_forge.cluster.config import Node, parse_cluster_config


def _render_running_op(running: dict, user: dict) -> None:
    """Render a running service operation with live output polling."""
    st.warning(
        f"**{running['op_type'].title()}** in progress"
        f" (started by {running.get('triggered_by_name', '?')}"
        f" at {format_dt(running['started_at'], user, fmt='%H:%M')})"
    )
    output_placeholder = st.empty()
    op = db.get_service_op(running["id"])
    if op:
        output_placeholder.code(op.get("output", "") or "Waiting for output...")
        if op["status"] == "running":
            time.sleep(2)
            st.rerun()
        elif op["status"] == "success":
            st.success(f"{op['op_type'].title()} completed successfully!")
        else:
            st.error(f"{op['op_type'].title()} {op['status']}")


def _render_op_buttons(user: dict, config: dict) -> None:
    """Render service control buttons."""
    assignments = config.get("assignments", {})
    node_names = sorted(assignments.keys())

    try:
        cluster = parse_cluster_config(config)
    except Exception:
        cluster = None

    st.subheader("Cluster-Wide")
    col1, col2 = st.columns(2)
    with col1:
        if st.button("Restart All Services", type="primary", use_container_width=True):
            op_id, error = start_service_op("restart", user["id"])
            if op_id:
                st.rerun()
            else:
                st.error(error)
    with col2:
        if st.button("Stop All Services", type="secondary", use_container_width=True):
            op_id, error = start_service_op("stop", user["id"])
            if op_id:
                st.rerun()
            else:
                st.error(error)

    if node_names:
        st.subheader("Per Node")
        for node_name in node_names:
            slots = assignments[node_name]
            models = ", ".join(s.get("model", "?") for s in slots)
            st.markdown(f"**{node_name}** — {models}")
            col1, col2 = st.columns(2)
            with col1:
                if st.button("Restart", key=f"restart_{node_name}", use_container_width=True):
                    op_id, error = start_service_op(
                        "restart", user["id"], target_node=node_name, skip_gateway=True,
                    )
                    if op_id:
                        st.rerun()
                    else:
                        st.error(error)
            with col2:
                if st.button("Stop", key=f"stop_{node_name}", use_container_width=True):
                    op_id, error = start_service_op(
                        "stop", user["id"], target_node=node_name, skip_gateway=True,
                    )
                    if op_id:
                        st.rerun()
                    else:
                        st.error(error)

            # Per-slot log viewer
            for slot in slots:
                port = slot.get("port", "?")
                model_name = slot.get("model", "?")
                data_key = f"data_logs_{node_name}_{port}"
                with st.expander(f"Logs — {model_name}:{port}"):
                    lines = st.select_slider(
                        "Lines", options=[100, 500, 1000, 5000], value=500,
                        key=f"lines_{node_name}_{port}",
                    )
                    if st.button("Fetch Logs", key=f"btn_logs_{node_name}_{port}"):
                        node_obj = cluster.nodes.get(node_name) if cluster else None
                        if node_obj:
                            resolved_user = node_obj.user or os.environ.get("GATEWAY_SSH_USER", "")
                            resolved_node = Node(
                                ip=node_obj.ip, ram_gb=node_obj.ram_gb,
                                user=resolved_user, role=node_obj.role,
                            )
                            st.session_state[data_key] = fetch_logs(resolved_node, port, tail_lines=lines)
                        else:
                            st.session_state[data_key] = {
                                "stderr": f"Node {node_name} not found in config", "stdout": "",
                            }
                    if data_key in st.session_state:
                        logs = st.session_state[data_key]
                        tab_err, tab_out = st.tabs(["stderr", "stdout"])
                        with tab_err:
                            st.code(logs["stderr"] or "(empty)", language="log")
                        with tab_out:
                            st.code(logs["stdout"] or "(empty)", language="log")

            st.divider()


def render(user: dict):
    st.header("Services")

    current = db.get_current_config()
    if not current:
        st.warning("No config found. Add models and nodes first.")
        return

    # Show running operation or control buttons
    running = db.get_running_service_op()
    if running:
        _render_running_op(running, user)
    else:
        _render_op_buttons(user, current["config"])

    # Operation history
    st.subheader("Operation History")
    ops = db.list_service_ops(limit=20)
    if ops:
        for op in ops:
            color = {"success": "green", "failed": "red", "running": "orange"}.get(op["status"], "grey")
            duration = ""
            if op.get("finished_at") and op.get("started_at"):
                secs = (op["finished_at"] - op["started_at"]).total_seconds()
                duration = f" ({int(secs)}s)"

            target = f" --node {op['target_node']}" if op.get("target_node") else " (all nodes)"
            with st.expander(
                f":{color}[{op['op_type'].title()} #{op['id']}] — "
                f"{op.get('triggered_by_name', '?')} — "
                f"{format_dt(op['started_at'], user)}{target} — "
                f"{op['status']}{duration}",
                expanded=False,
            ):
                if op.get("output"):
                    st.code(op["output"])
                else:
                    st.caption("No output recorded")
    else:
        st.info("No operations yet.")
