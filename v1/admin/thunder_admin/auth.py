"""Authentication, session management, and password hashing."""

from __future__ import annotations

import os
from datetime import UTC, datetime, timedelta

import bcrypt


def hash_password(password: str) -> str:
    return bcrypt.hashpw(password.encode(), bcrypt.gensalt()).decode()


def verify_password(password: str, password_hash: str) -> bool:
    return bcrypt.checkpw(password.encode(), password_hash.encode())


def is_session_expired(login_time: datetime, timeout_hours: int | None = None) -> bool:
    if timeout_hours is None:
        timeout_hours = int(os.environ.get("SESSION_TIMEOUT_HOURS", "24"))
    return datetime.now(UTC) - login_time > timedelta(hours=timeout_hours)


def require_auth(st) -> dict | None:
    """Check session state for auth. Returns user dict or None."""
    if "user" not in st.session_state or "login_time" not in st.session_state:
        return None
    if is_session_expired(st.session_state["login_time"]):
        st.session_state.clear()
        return None
    return st.session_state["user"]


def login(st, username: str, password: str) -> bool:
    from thunder_admin.db import get_user_by_username

    user = get_user_by_username(username)
    if user and verify_password(password, user["password_hash"]):
        st.session_state["user"] = {
            "id": user["id"],
            "username": user["username"],
            "is_admin": user["is_admin"],
            "timezone": user.get("timezone"),
        }
        st.session_state["login_time"] = datetime.now(UTC)
        return True
    return False


def logout(st) -> None:
    st.session_state.clear()
