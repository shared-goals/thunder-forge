"""Tests for auth module — password hashing and session timeout."""

from datetime import UTC, datetime, timedelta


def test_hash_and_verify_password():
    from thunder_admin.auth import hash_password, verify_password

    hashed = hash_password("mysecret")
    assert verify_password("mysecret", hashed)
    assert not verify_password("wrong", hashed)


def test_is_session_expired_within_timeout():
    from thunder_admin.auth import is_session_expired

    login_time = datetime.now(UTC)
    assert not is_session_expired(login_time, timeout_hours=24)


def test_is_session_expired_after_timeout():
    from thunder_admin.auth import is_session_expired

    login_time = datetime.now(UTC) - timedelta(hours=25)
    assert is_session_expired(login_time, timeout_hours=24)
