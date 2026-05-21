# admin/thunder_admin/pages/dashboard.py
"""Dashboard page — gateway connectivity, health summary, quick links."""

from __future__ import annotations

import os

import streamlit as st

from thunder_admin import db
from thunder_admin.deploy import check_gateway_connectivity, ssh_exec
from thunder_admin.tz import format_dt


def render(user: dict):
    st.header("Dashboard")

    # Gateway connectivity
    ok, msg = check_gateway_connectivity()
    if ok:
        st.success("Gateway: Connected")
    else:
        host = os.environ.get("GATEWAY_SSH_HOST", "unknown")
        st.error(f"Cannot reach gateway ({host}) — {msg}")

    # Summary metrics
    col1, col2, col3 = st.columns(3)

    current = db.get_current_config()
    if current:
        config = current["config"]
        col1.metric("Nodes", len(config.get("nodes", {})))
        col2.metric("Models", len(config.get("models", {})))
    else:
        col1.metric("Nodes", 0)
        col2.metric("Models", 0)

    last_deploy = db.get_last_successful_deploy()
    if last_deploy:
        col3.metric(
            "Last Deploy",
            format_dt(last_deploy["started_at"], user),
        )
    else:
        col3.metric("Last Deploy", "Never")

    # Cluster health (if gateway is reachable)
    if ok:
        st.subheader("Cluster Health")
        if st.button("Check Health"):
            tf_dir = os.environ.get("THUNDER_FORGE_DIR", "")
            exit_code, output = ssh_exec(
                f"cd {tf_dir} && ~/.local/bin/uv run thunder-forge health --skip-preflight",
                timeout=60,
            )
            if exit_code == 0:
                st.code(output)
            else:
                st.error("Health check failed")
                st.code(output)

    # Quick links
    st.subheader("Quick Links")
    links_col1, links_col2, links_col3 = st.columns(3)
    grafana = os.environ.get("GRAFANA_URL", "")
    webui = os.environ.get("OPENWEBUI_URL", "")
    litellm = os.environ.get("LITELLM_URL", "")
    if grafana:
        links_col1.link_button("Grafana", grafana)
    if webui:
        links_col2.link_button("Open WebUI", webui)
    if litellm:
        links_col3.link_button("LiteLLM", litellm)
