"""Timezone-aware datetime formatting for the admin UI."""

from __future__ import annotations

import os
from datetime import datetime
from zoneinfo import ZoneInfo


def get_display_tz(user: dict) -> ZoneInfo:
    """Return ZoneInfo for the logged-in user. Falls back to DISPLAY_TZ env var, then UTC."""
    tz_str = user.get("timezone") or os.environ.get("DISPLAY_TZ", "UTC")
    return ZoneInfo(tz_str)


def format_dt(dt: datetime, user: dict, fmt: str = "%Y-%m-%d %H:%M") -> str:
    """Format a timezone-aware datetime in the logged-in user's display timezone."""
    return dt.astimezone(get_display_tz(user)).strftime(fmt)
