# admin/thunder_admin/pages/nodes.py
"""Nodes config page — CRUD for cluster nodes."""

from __future__ import annotations

import streamlit as st

from thunder_admin import db
from thunder_admin.config import save_config_or_error


def render(user: dict):
    st.header("Nodes")

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

    nodes = config.get("nodes", {})

    # Nodes table
    if nodes:
        for name, node in nodes.items():
            with st.expander(
                f"**{name}** — {node.get('ip', 'N/A')} ({node.get('role', 'node')})",
                expanded=False,
            ):
                col1, col2, col3, col4 = st.columns(4)
                col1.write(f"**IP:** {node.get('ip', '')}")
                col2.write(f"**RAM:** {node.get('ram_gb', 0)} GB")
                col3.write(f"**Role:** {node.get('role', 'node')}")
                user_display = node.get("user", "") or "(GATEWAY_SSH_USER)"
                col4.write(f"**User:** {user_display}")

                edit_col, del_col = st.columns(2)

                # Edit node
                if edit_col.button("Edit", key=f"edit_node_{name}"):
                    st.session_state[f"editing_node_{name}"] = True
                    st.rerun()

                if st.session_state.get(f"editing_node_{name}"):
                    with st.form(f"edit_node_{name}"):
                        new_ip = st.text_input("IP", value=node.get("ip", ""), key=f"en_ip_{name}")
                        new_ram = st.number_input(
                            "RAM (GB)",
                            value=node.get("ram_gb", 128),
                            step=1,
                            key=f"en_ram_{name}",
                        )
                        new_role = st.selectbox(
                            "Role",
                            ["node", "gateway"],
                            index=["node", "gateway"].index(node.get("role", "node")),
                            key=f"en_role_{name}",
                        )
                        new_user = st.text_input(
                            "SSH user",
                            value=node.get("user", ""),
                            key=f"en_user_{name}",
                        )
                        sc, cc = st.columns(2)
                        if sc.form_submit_button("Save"):
                            node["ip"] = new_ip
                            node["ram_gb"] = new_ram
                            node["role"] = new_role
                            node["user"] = new_user
                            if save_config_or_error(st, config, user, f"Updated node '{name}'"):
                                del st.session_state[f"editing_node_{name}"]
                                st.success(f"Updated '{name}'")
                                st.rerun()
                        if cc.form_submit_button("Cancel"):
                            del st.session_state[f"editing_node_{name}"]
                            st.rerun()

                if del_col.button("Delete", key=f"delete_node_{name}", type="secondary"):
                    if name in config.get("assignments", {}):
                        slots = config["assignments"][name]
                        model_names = [s.get("model", "?") for s in slots]
                        st.warning(f"Node has assignments: {', '.join(model_names)}. Remove assignments and delete?")
                        if st.button(
                            "Confirm delete",
                            key=f"confirm_delete_node_{name}",
                        ):
                            del config["assignments"][name]
                            del config["nodes"][name]
                            if save_config_or_error(
                                st,
                                config,
                                user,
                                f"Deleted node '{name}' and its assignments",
                            ):
                                st.success(f"Deleted '{name}'")
                                st.rerun()
                    else:
                        del config["nodes"][name]
                        if save_config_or_error(st, config, user, f"Deleted node '{name}'"):
                            st.success(f"Deleted '{name}'")
                            st.rerun()
    else:
        st.info("No nodes configured. Add one below.")

    # Add node form
    st.subheader("Add Node")
    with st.form("add_node"):
        name = st.text_input("Node name (e.g. msm1)")
        ip = st.text_input("IP address")
        ram_gb = st.number_input("RAM (GB)", min_value=1, value=128, step=1)
        role = st.selectbox("Role", ["node", "gateway"])
        node_user = st.text_input("SSH user (optional — falls back to GATEWAY_SSH_USER)")

        if st.form_submit_button("Add Node"):
            if not name:
                st.error("Node name is required")
            elif not ip:
                st.error("IP address is required")
            elif name in nodes:
                st.error(f"Node '{name}' already exists")
            else:
                config["nodes"][name] = {
                    "ip": ip,
                    "ram_gb": ram_gb,
                    "role": role,
                    "user": node_user,
                }
                if save_config_or_error(st, config, user, f"Added node '{name}'"):
                    st.success(f"Added '{name}'")
                    st.rerun()
