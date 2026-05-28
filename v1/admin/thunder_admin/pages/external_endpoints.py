# admin/thunder_admin/pages/external_endpoints.py
"""External endpoints config page — remote LiteLLM/OpenAI-compatible APIs."""

from __future__ import annotations

import streamlit as st

from thunder_admin import db
from thunder_admin.config import save_config_or_error


def render(user: dict):
    st.header("External Endpoints")

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

    endpoints = config.get("external_endpoints", [])

    if endpoints:
        for i, ep in enumerate(endpoints):
            with st.expander(
                f"**{ep.get('model_name', '?')}** — {ep.get('api_base', 'N/A')}",
                expanded=False,
            ):
                st.write(f"**API Base:** {ep.get('api_base', '')}")
                st.write(f"**API Key Env:** {ep.get('api_key_env', '') or '(none)'}")
                if ep.get("max_input_tokens"):
                    st.write(f"**Max Input Tokens:** {ep['max_input_tokens']:,}")
                if ep.get("max_output_tokens"):
                    st.write(f"**Max Output Tokens:** {ep['max_output_tokens']:,}")

                if st.button("Delete", key=f"delete_ep_{i}", type="secondary"):
                    endpoints.pop(i)
                    config["external_endpoints"] = endpoints
                    if save_config_or_error(
                        st,
                        config,
                        user,
                        f"Deleted endpoint '{ep.get('model_name')}'",
                    ):
                        st.success("Deleted")
                        st.rerun()
    else:
        st.info("No external endpoints configured.")

    # Add endpoint form
    st.subheader("Add External Endpoint")
    with st.form("add_endpoint"):
        model_name = st.text_input("Model name")
        api_base = st.text_input("API base URL (e.g. http://example.com:4000/v1)")
        api_key_env = st.text_input("API key env var name (optional)")
        max_input = st.number_input("Max input tokens (0 = default)", min_value=0, value=0, step=1024)
        max_output = st.number_input("Max output tokens (0 = default)", min_value=0, value=0, step=1024)

        if st.form_submit_button("Add Endpoint"):
            if not model_name or not api_base:
                st.error("Model name and API base are required")
            else:
                new_ep: dict = {
                    "model_name": model_name,
                    "api_base": api_base,
                }
                if api_key_env:
                    new_ep["api_key_env"] = api_key_env
                if max_input > 0:
                    new_ep["max_input_tokens"] = max_input
                if max_output > 0:
                    new_ep["max_output_tokens"] = max_output
                endpoints.append(new_ep)
                config["external_endpoints"] = endpoints
                if save_config_or_error(st, config, user, f"Added endpoint '{model_name}'"):
                    st.success(f"Added '{model_name}'")
                    st.rerun()
