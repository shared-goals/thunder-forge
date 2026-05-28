# admin/thunder_admin/app.py
"""Streamlit app entry point — page routing and auth guard."""

from __future__ import annotations

import importlib

import streamlit as st

from thunder_admin.auth import login, logout, require_auth
from thunder_admin.tz import get_display_tz

st.set_page_config(page_title="Thunder Forge Admin", page_icon="⚡", layout="wide")

PAGES = {
    "Dashboard": "thunder_admin.pages.dashboard",
    "Models": "thunder_admin.pages.models",
    "Nodes": "thunder_admin.pages.nodes",
    "Assignments": "thunder_admin.pages.assignments",
    "External Endpoints": "thunder_admin.pages.external_endpoints",
    "History": "thunder_admin.pages.history",
    "Deploy": "thunder_admin.pages.deploy",
    "Services": "thunder_admin.pages.services",
    "Users": "thunder_admin.pages.users",
}


def render_login():
    """Render the login form."""
    st.title("Thunder Forge Admin")
    with st.form("login"):
        username = st.text_input("Username")
        password = st.text_input("Password", type="password")
        submitted = st.form_submit_button("Login")
        if submitted:
            if login(st, username, password):
                st.rerun()
            else:
                st.error("Invalid username or password")


def main():
    user = require_auth(st)
    if user is None:
        render_login()
        return

    # Sidebar navigation
    with st.sidebar:
        st.title("Thunder Forge")
        st.caption(f"Logged in as {user['username']}")
        tz = get_display_tz(user)
        st.caption(f"🕐 {tz.key}")

        page_names = list(PAGES.keys())

        selection = st.radio("Navigation", page_names, label_visibility="collapsed")

        st.divider()
        if st.button("Logout"):
            logout(st)
            st.rerun()

    # Render selected page
    module = importlib.import_module(PAGES[selection])
    module.render(user)


if __name__ == "__main__":
    main()
