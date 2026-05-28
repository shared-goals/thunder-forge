# admin/thunder_admin/pages/history.py
"""Config version history — view, diff, and restore."""

from __future__ import annotations

import difflib

import streamlit as st

from thunder_admin import db
from thunder_admin.config import jsonb_to_yaml
from thunder_admin.tz import format_dt


def render(user: dict):
    st.header("Config History")

    versions = db.list_config_versions(limit=50)
    if not versions:
        st.info("No config versions yet.")
        return

    for v in versions:
        deploy_badge = ""
        if v.get("deploy_status"):
            colors = {
                "success": "green",
                "failed": "red",
                "running": "orange",
                "cancelled": "grey",
            }
            color = colors.get(v["deploy_status"], "grey")
            deploy_badge = f" :{color}[{v['deploy_status']}]"

        with st.expander(
            f"**v{v['id']}** — {v.get('author_name', 'system')} — "
            f"{format_dt(v['created_at'], user)} — "
            f"{v.get('comment', '')}{deploy_badge}",
            expanded=False,
        ):
            yaml_str = jsonb_to_yaml(v["config"])
            st.code(yaml_str, language="yaml")

            col1, col2 = st.columns(2)
            if col1.button("Diff vs previous", key=f"diff_{v['id']}"):
                prev = db.get_previous_config_version(v["id"])
                if prev:
                    prev_yaml = jsonb_to_yaml(prev["config"])
                    diff = difflib.unified_diff(
                        prev_yaml.splitlines(keepends=True),
                        yaml_str.splitlines(keepends=True),
                        fromfile=f"v{prev['id']}",
                        tofile=f"v{v['id']}",
                    )
                    diff_str = "".join(diff)
                    if diff_str:
                        st.code(diff_str, language="diff")
                    else:
                        st.info("No changes")
                else:
                    st.info("No previous version to compare")

            if col2.button("Restore this version", key=f"restore_{v['id']}"):
                loaded_id = st.session_state.get("loaded_config_id")
                if loaded_id is None:
                    current = db.get_current_config()
                    loaded_id = current["id"] if current else None

                new_id = db.save_config(
                    v["config"],
                    user["id"],
                    f"Restored from version {v['id']}",
                    loaded_id,
                )
                if new_id:
                    st.session_state["loaded_config_id"] = new_id
                    st.success(f"Restored version {v['id']} as new version {new_id}")
                    st.rerun()
                else:
                    st.error("Config was modified while restoring. Reload and retry.")
