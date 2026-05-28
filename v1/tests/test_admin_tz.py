"""Tests for timezone utility functions."""

from __future__ import annotations

from datetime import UTC, datetime
from zoneinfo import ZoneInfoNotFoundError

import pytest
from thunder_admin.tz import format_dt, get_display_tz


def test_converts_utc_to_named_timezone():
    dt = datetime(2024, 7, 15, 12, 0, 0, tzinfo=UTC)
    user = {"timezone": "Europe/Helsinki"}
    assert format_dt(dt, user) == "2024-07-15 15:00"  # EEST = UTC+3


def test_user_timezone_overrides_env(monkeypatch):
    monkeypatch.setenv("DISPLAY_TZ", "America/New_York")
    user = {"timezone": "Europe/Helsinki"}
    assert get_display_tz(user).key == "Europe/Helsinki"


def test_null_user_timezone_uses_env(monkeypatch):
    monkeypatch.setenv("DISPLAY_TZ", "America/New_York")
    user = {"timezone": None}
    assert get_display_tz(user).key == "America/New_York"


def test_missing_env_falls_back_to_utc(monkeypatch):
    monkeypatch.delenv("DISPLAY_TZ", raising=False)
    user = {"timezone": None}
    assert get_display_tz(user).key == "UTC"


def test_invalid_timezone_raises():
    user = {"timezone": "Not/ATimezone"}
    with pytest.raises(ZoneInfoNotFoundError):
        get_display_tz(user)


def test_custom_format():
    dt = datetime(2024, 7, 15, 14, 30, 0, tzinfo=UTC)
    user = {"timezone": None}
    assert format_dt(dt, user, fmt="%H:%M") == "14:30"
