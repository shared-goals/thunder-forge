# Timezone Display — Admin UI Design

**Date:** 2026-03-26
**Scope:** `admin/thunder_admin/` — new `tz.py`, `bootstrap.py`, `db.py`, `app.py`, and all pages that render timestamps

## Background

All timestamps in the admin UI are stored as `TIMESTAMPTZ` in PostgreSQL (UTC-aware) but displayed raw in UTC because `strftime` is called without timezone conversion. Users want to see timestamps in their local timezone.

## Goal

Configurable timezone display: an installation default via env var, overridable per-user via the UI.

## Architecture

### Env var

`DISPLAY_TZ` in `docker/.env` (and `.env.example`). Standard IANA string (e.g. `Europe/Helsinki`). Defaults to `UTC` if unset.

### DB migration

Add a nullable `timezone TEXT` column to `users`:

```sql
ALTER TABLE users ADD COLUMN IF NOT EXISTS timezone TEXT;
```

Added to `MIGRATION_SQL` in `bootstrap.py`. `NULL` = use installation default.

### `admin/thunder_admin/tz.py` (new file)

Central timezone logic:

```python
import os
from datetime import datetime
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

def get_display_tz(user: dict) -> ZoneInfo:
    tz_str = user.get("timezone") or os.environ.get("DISPLAY_TZ", "UTC")
    return ZoneInfo(tz_str)  # raises ZoneInfoNotFoundError on bad value

def format_dt(dt: datetime, user: dict, fmt: str = "%Y-%m-%d %H:%M") -> str:
    return dt.astimezone(get_display_tz(user)).strftime(fmt)
```

### Affected pages

Replace all bare `strftime` calls with `format_dt(dt, user)`:

| File | Call site |
|---|---|
| `pages/history.py:36` | `v['created_at'].strftime('%Y-%m-%d %H:%M')` |
| `pages/deploy.py:117` | `running['started_at'].strftime('%H:%M')` |
| `pages/deploy.py:190` | `d['started_at'].strftime('%Y-%m-%d %H:%M')` |
| `pages/dashboard.py:41` | `last_deploy["started_at"].strftime("%Y-%m-%d %H:%M")` |
| `pages/users.py:27` | `u["created_at"].strftime("%Y-%m-%d")` |

The `'%H:%M'` call in `deploy.py:117` keeps its short format — pass `fmt="%H:%M"` to `format_dt`.

In all cases, the `user` argument is the **logged-in user** (the viewer), not the entity whose timestamp is being shown.

## UI

### Sidebar timezone indicator

In `app.py`, after the `st.caption(f"Logged in as ...")` line, add:

```python
from thunder_admin.tz import get_display_tz
tz = get_display_tz(user)
st.caption(f"🕐 {tz.key}")
```

Shows the active timezone so users know what they're seeing without navigating anywhere.

### Users page — per-user timezone setting

**Logged-in user's own preferences:** Below the users table, add a "Preferences" expander. Contains a `st.selectbox` of common IANA timezones plus a "Use installation default" option. On save, calls `db.update_user_timezone(user_id, tz_str_or_none)` and refreshes the session.

**Admin editing other users:** The existing per-user expander on the users table gets a timezone field alongside password/delete actions. Admin-only.

**Timezone selectbox options** (curated ~25 common zones):

```python
COMMON_TIMEZONES = [
    "",  # "Use installation default"
    "UTC",
    "Europe/London", "Europe/Paris", "Europe/Berlin", "Europe/Helsinki",
    "Europe/Moscow",
    "America/New_York", "America/Chicago", "America/Denver", "America/Los_Angeles",
    "America/Sao_Paulo",
    "Asia/Dubai", "Asia/Kolkata", "Asia/Bangkok", "Asia/Shanghai",
    "Asia/Tokyo", "Asia/Seoul",
    "Australia/Sydney",
    "Pacific/Auckland",
]
```

Empty string maps to `NULL` in DB (installation default).

### `db.py` additions

```python
def update_user_timezone(user_id: int, timezone: str | None) -> None:
    with get_cursor() as cur:
        cur.execute("UPDATE users SET timezone = %s WHERE id = %s", (timezone or None, user_id))
```

`list_users()` already returns all columns — add `timezone` to the SELECT.

## Error handling

`ZoneInfo(tz_str)` raises `ZoneInfoNotFoundError` for invalid values. This should not be silently swallowed — an invalid env var or DB value is a misconfiguration. Let it propagate (Streamlit will show an error page). The selectbox UI prevents invalid values from being saved through the UI.

## Testing

Unit tests in `tests/test_tz.py`:

- UTC datetime converts correctly to a named timezone
- User `timezone` field takes priority over `DISPLAY_TZ` env var
- `NULL` user timezone falls back to env var
- Unset env var falls back to UTC
- Invalid timezone string raises `ZoneInfoNotFoundError`

## Out of scope

- No per-request timezone detection (browser locale)
- No date-only formatting changes beyond replacing existing `strftime` calls
- No changes to how timestamps are stored (stays UTC in DB)
