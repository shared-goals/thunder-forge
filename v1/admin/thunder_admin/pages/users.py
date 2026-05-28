# admin/thunder_admin/pages/users.py
"""User management page — admin user table + per-user timezone preferences."""

from __future__ import annotations

import streamlit as st

from thunder_admin import db
from thunder_admin.auth import hash_password
from thunder_admin.bootstrap import generate_password
from thunder_admin.tz import format_dt

COMMON_TIMEZONES = [
    "",
    "UTC",
    "Europe/London",
    "Europe/Paris",
    "Europe/Berlin",
    "Europe/Helsinki",
    "Europe/Moscow",
    "America/New_York",
    "America/Chicago",
    "America/Denver",
    "America/Los_Angeles",
    "America/Sao_Paulo",
    "Asia/Dubai",
    "Asia/Kolkata",
    "Asia/Bangkok",
    "Asia/Shanghai",
    "Asia/Tokyo",
    "Asia/Seoul",
    "Australia/Sydney",
    "Pacific/Auckland",
]


def render(user: dict):
    # ── Admin: user management table ─────────────────────────────────────
    if user.get("is_admin"):
        st.header("Users")
        users = db.list_users()

        if users:
            for u in users:
                col1, col2, col3, col4 = st.columns([3, 2, 2, 2])
                col1.write(u["username"])
                col2.write("Admin" if u["is_admin"] else "User")
                col3.write(format_dt(u["created_at"], user, fmt="%Y-%m-%d"))

                with col4:
                    btn1, btn2, btn3 = st.columns(3)
                    if btn1.button("Reset PW", key=f"reset_{u['id']}"):
                        new_pw = generate_password()
                        db.update_user_password(u["id"], hash_password(new_pw))
                        st.info(f"New password for {u['username']}: `{new_pw}`")
                    if btn2.button("TZ", key=f"edit_tz_{u['id']}"):
                        st.session_state[f"editing_tz_{u['id']}"] = True
                    if u["id"] != user["id"]:
                        if btn3.button("Del", key=f"del_{u['id']}", type="secondary"):
                            db.delete_user(u["id"])
                            st.success(f"Deleted {u['username']}")
                            st.rerun()

                if st.session_state.get(f"editing_tz_{u['id']}"):
                    current_tz = u.get("timezone") or ""
                    with st.form(f"tz_form_{u['id']}"):
                        new_tz = st.selectbox(
                            f"Timezone for {u['username']}",
                            COMMON_TIMEZONES,
                            format_func=lambda x: "Use installation default" if x == "" else x,
                            index=COMMON_TIMEZONES.index(current_tz) if current_tz in COMMON_TIMEZONES else 0,
                        )
                        col_save, col_cancel = st.columns(2)
                        if col_save.form_submit_button("Save"):
                            db.update_user_timezone(u["id"], new_tz or None)
                            if u["id"] == user["id"]:
                                st.session_state["user"]["timezone"] = new_tz or None
                            del st.session_state[f"editing_tz_{u['id']}"]
                            st.success(f"Updated timezone for {u['username']}")
                            st.rerun()
                        if col_cancel.form_submit_button("Cancel"):
                            del st.session_state[f"editing_tz_{u['id']}"]
                            st.rerun()

        # Add user form
        st.subheader("Create User")
        with st.form("create_user"):
            username = st.text_input("Username")
            is_admin = st.checkbox("Admin")

            if st.form_submit_button("Create User"):
                if not username:
                    st.error("Username is required")
                elif db.get_user_by_username(username):
                    st.error(f"User '{username}' already exists")
                else:
                    password = generate_password()
                    db.create_user(username, hash_password(password), is_admin=is_admin)
                    st.success(f"Created user '{username}'. Password: `{password}`")
                    st.rerun()

    # ── All users: My Preferences ─────────────────────────────────────────
    st.subheader("My Preferences")
    current_tz = user.get("timezone") or ""
    with st.form("my_preferences"):
        new_tz = st.selectbox(
            "Timezone",
            COMMON_TIMEZONES,
            format_func=lambda x: "Use installation default" if x == "" else x,
            index=COMMON_TIMEZONES.index(current_tz) if current_tz in COMMON_TIMEZONES else 0,
        )
        if st.form_submit_button("Save Preferences"):
            db.update_user_timezone(user["id"], new_tz or None)
            st.session_state["user"]["timezone"] = new_tz or None
            st.success("Preferences saved.")
            st.rerun()
