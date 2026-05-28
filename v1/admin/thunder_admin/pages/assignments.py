# admin/thunder_admin/pages/assignments.py
"""Assignments config page — model-to-node mapping with memory validation."""

from __future__ import annotations

import streamlit as st

from thunder_admin import db
from thunder_admin.config import save_config_or_error
from thunder_forge.cluster.config import OS_OVERHEAD_GB


def render(user: dict):
    st.header("Assignments")

    current = db.get_current_config()
    if current:
        config = current["config"]
        st.session_state.setdefault("loaded_config_id", current["id"])
    else:
        config = {
            "models": {},
            "nodes": {},
            "assignments": {},
            "external_endpoints": [],
        }
        st.session_state.setdefault("loaded_config_id", None)

    models = config.get("models", {})
    nodes = config.get("nodes", {})
    assignments = config.get("assignments", {})
    compute_nodes = {k: v for k, v in nodes.items() if v.get("role") == "node"}

    # Memory budget per node
    st.subheader("Memory Budget")
    for node_name, node in compute_nodes.items():
        slots = assignments.get(node_name, [])
        total = OS_OVERHEAD_GB
        parts = []
        for slot in slots:
            model = models.get(slot.get("model", ""))
            if model:
                weight = model.get("ram_gb") or model.get("disk_gb", 0)
                kv = model.get("kv_per_32k_gb", 0)
                total += weight + kv
                parts.append(f"{slot['model']}({weight}+{kv}kv)")
        budget = " + ".join(parts) + f" + {OS_OVERHEAD_GB} OS = {total:.1f} / {node.get('ram_gb', 0)} GB"
        if total > node.get("ram_gb", 0):
            st.error(f"**{node_name}:** {budget} — EXCEEDS RAM")
        else:
            st.success(f"**{node_name}:** {budget}")

    # Assignments by node
    st.subheader("Current Assignments")
    for node_name in compute_nodes:
        st.write(f"**{node_name}**")
        slots = assignments.get(node_name, [])
        if slots:
            for i, slot in enumerate(slots):
                col1, col2, col3, col4 = st.columns([3, 1, 1, 1])
                col1.write(slot.get("model", "?"))
                col2.write(f"Port {slot.get('port', 0)}")
                col3.write("Embedding" if slot.get("embedding") else "")
                if col4.button("Remove", key=f"rm_{node_name}_{i}"):
                    assignments[node_name].pop(i)
                    if not assignments[node_name]:
                        del assignments[node_name]
                    config["assignments"] = assignments
                    if save_config_or_error(
                        st,
                        config,
                        user,
                        f"Removed {slot.get('model')} from {node_name}",
                    ):
                        st.rerun()
        else:
            st.caption("No assignments")

    # Add assignment form
    st.subheader("Add Assignment")
    with st.form("add_assignment"):
        node_options = list(compute_nodes.keys()) if compute_nodes else ["(no nodes)"]
        model_options = list(models.keys()) if models else ["(no models)"]
        node_name = st.selectbox("Node", node_options)
        model_name = st.selectbox("Model", model_options)
        port = st.number_input("Port", min_value=1, max_value=65535, value=8000, step=1)
        embedding = st.checkbox("Embedding slot")

        if st.form_submit_button("Add Assignment"):
            if node_name not in nodes:
                st.error("Select a valid node")
            elif model_name not in models:
                st.error("Select a valid model")
            else:
                if node_name not in assignments:
                    assignments[node_name] = []
                assignments[node_name].append(
                    {
                        "model": model_name,
                        "port": port,
                        "embedding": embedding,
                    }
                )
                config["assignments"] = assignments
                if save_config_or_error(
                    st,
                    config,
                    user,
                    f"Assigned {model_name} to {node_name}:{port}",
                ):
                    st.success(f"Assigned {model_name} to {node_name}:{port}")
                    st.rerun()
