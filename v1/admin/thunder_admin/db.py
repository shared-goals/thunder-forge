"""Database connection pool and query helpers."""

from __future__ import annotations

import os
from contextlib import contextmanager
from typing import Any

import psycopg
from psycopg.rows import dict_row


def get_connection() -> psycopg.Connection:
    """Create a database connection.

    Callers that use this directly own closing it. Most code should use
    get_cursor(), which closes the connection after each operation.
    """
    database_url = os.environ["DATABASE_URL"]
    return psycopg.connect(database_url, row_factory=dict_row)


@contextmanager
def get_cursor():
    """Yield a cursor, then commit or rollback and close the connection."""
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            yield cur
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def get_current_config() -> dict | None:
    with get_cursor() as cur:
        cur.execute(
            "SELECT cv.*, u.username AS author_name FROM config_versions cv "
            "LEFT JOIN users u ON cv.author_id = u.id "
            "ORDER BY cv.id DESC LIMIT 1"
        )
        return cur.fetchone()


def get_config_version(version_id: int) -> dict | None:
    with get_cursor() as cur:
        cur.execute(
            "SELECT cv.*, u.username AS author_name FROM config_versions cv "
            "LEFT JOIN users u ON cv.author_id = u.id "
            "WHERE cv.id = %s",
            (version_id,),
        )
        return cur.fetchone()


def get_previous_config_version(version_id: int) -> dict | None:
    """Get the config version immediately before the given ID."""
    with get_cursor() as cur:
        cur.execute(
            "SELECT cv.*, u.username AS author_name FROM config_versions cv "
            "LEFT JOIN users u ON cv.author_id = u.id "
            "WHERE cv.id < %s ORDER BY cv.id DESC LIMIT 1",
            (version_id,),
        )
        return cur.fetchone()


def list_config_versions(limit: int = 50) -> list[dict]:
    with get_cursor() as cur:
        cur.execute(
            "SELECT cv.*, u.username AS author_name, "
            "(SELECT d.status FROM deploys d WHERE d.config_id = cv.id "
            "ORDER BY d.id DESC LIMIT 1) AS deploy_status "
            "FROM config_versions cv "
            "LEFT JOIN users u ON cv.author_id = u.id "
            "ORDER BY cv.id DESC LIMIT %s",
            (limit,),
        )
        return cur.fetchall()


def save_config(config_json: dict, author_id: int, comment: str, loaded_version_id: int | None) -> int | None:
    """Save a new config version with optimistic locking.
    Returns the new version ID, or None if the loaded version is stale.
    """
    with get_cursor() as cur:
        if loaded_version_id is None:
            cur.execute(
                "INSERT INTO config_versions (config, author_id, comment) VALUES (%s, %s, %s) RETURNING id",
                (psycopg.types.json.Json(config_json), author_id, comment),
            )
        else:
            cur.execute(
                "INSERT INTO config_versions (config, author_id, comment) "
                "SELECT %s, %s, %s "
                "WHERE (SELECT MAX(id) FROM config_versions) = %s "
                "RETURNING id",
                (psycopg.types.json.Json(config_json), author_id, comment, loaded_version_id),
            )
        row = cur.fetchone()
        return row["id"] if row else None


def get_running_deploy() -> dict | None:
    with get_cursor() as cur:
        cur.execute(
            "SELECT d.*, u.username AS triggered_by_name FROM deploys d "
            "LEFT JOIN users u ON d.triggered_by = u.id "
            "WHERE d.status = 'running' ORDER BY d.id DESC LIMIT 1"
        )
        return cur.fetchone()


def create_deploy(config_id: int, user_id: int) -> int | None:
    """Create a new deploy record atomically (fails if one is already running)."""
    with get_cursor() as cur:
        cur.execute(
            "INSERT INTO deploys (config_id, triggered_by, status) "
            "SELECT %s, %s, 'running' "
            "WHERE NOT EXISTS (SELECT 1 FROM deploys WHERE status = 'running') "
            "RETURNING id",
            (config_id, user_id),
        )
        row = cur.fetchone()
        return row["id"] if row else None


def update_deploy(deploy_id: int, **kwargs: Any) -> None:
    if not kwargs:
        return
    sets = ", ".join(f"{k} = %s" for k in kwargs)
    vals = list(kwargs.values()) + [deploy_id]
    with get_cursor() as cur:
        cur.execute(f"UPDATE deploys SET {sets} WHERE id = %s", vals)  # noqa: S608


def get_deploy(deploy_id: int) -> dict | None:
    with get_cursor() as cur:
        cur.execute(
            "SELECT d.*, u.username AS triggered_by_name FROM deploys d "
            "LEFT JOIN users u ON d.triggered_by = u.id "
            "WHERE d.id = %s",
            (deploy_id,),
        )
        return cur.fetchone()


def list_deploys(limit: int = 20) -> list[dict]:
    with get_cursor() as cur:
        cur.execute(
            "SELECT d.*, u.username AS triggered_by_name FROM deploys d "
            "LEFT JOIN users u ON d.triggered_by = u.id "
            "ORDER BY d.id DESC LIMIT %s",
            (limit,),
        )
        return cur.fetchall()


def get_last_successful_deploy() -> dict | None:
    with get_cursor() as cur:
        cur.execute(
            "SELECT d.*, u.username AS triggered_by_name FROM deploys d "
            "LEFT JOIN users u ON d.triggered_by = u.id "
            "WHERE d.status = 'success' ORDER BY d.id DESC LIMIT 1"
        )
        return cur.fetchone()


def get_user_by_username(username: str) -> dict | None:
    with get_cursor() as cur:
        cur.execute("SELECT * FROM users WHERE username = %s", (username,))
        return cur.fetchone()


def get_user_by_id(user_id: int) -> dict | None:
    with get_cursor() as cur:
        cur.execute("SELECT * FROM users WHERE id = %s", (user_id,))
        return cur.fetchone()


def list_users() -> list[dict]:
    with get_cursor() as cur:
        cur.execute("SELECT id, username, is_admin, timezone, created_at FROM users ORDER BY id")
        return cur.fetchall()


def create_user(username: str, password_hash: str, is_admin: bool = False) -> int:
    with get_cursor() as cur:
        cur.execute(
            "INSERT INTO users (username, password_hash, is_admin) VALUES (%s, %s, %s) RETURNING id",
            (username, password_hash, is_admin),
        )
        return cur.fetchone()["id"]


def update_user_password(user_id: int, password_hash: str) -> None:
    with get_cursor() as cur:
        cur.execute(
            "UPDATE users SET password_hash = %s WHERE id = %s",
            (password_hash, user_id),
        )


def update_user_timezone(user_id: int, timezone: str | None) -> None:
    with get_cursor() as cur:
        cur.execute("UPDATE users SET timezone = %s WHERE id = %s", (timezone or None, user_id))


def delete_user(user_id: int) -> None:
    with get_cursor() as cur:
        cur.execute("DELETE FROM users WHERE id = %s", (user_id,))


# --- Service Operations ---


def get_running_service_op() -> dict | None:
    with get_cursor() as cur:
        cur.execute(
            "SELECT so.*, u.username AS triggered_by_name FROM service_operations so "
            "LEFT JOIN users u ON so.triggered_by = u.id "
            "WHERE so.status = 'running' ORDER BY so.id DESC LIMIT 1"
        )
        return cur.fetchone()


def create_service_op(op_type: str, user_id: int, target_node: str | None, skip_gateway: bool) -> int | None:
    """Create a service operation atomically (fails if one is already running)."""
    with get_cursor() as cur:
        cur.execute(
            "INSERT INTO service_operations (op_type, target_node, skip_gateway, triggered_by, status) "
            "SELECT %s, %s, %s, %s, 'running' "
            "WHERE NOT EXISTS (SELECT 1 FROM service_operations WHERE status = 'running') "
            "RETURNING id",
            (op_type, target_node, skip_gateway, user_id),
        )
        row = cur.fetchone()
        return row["id"] if row else None


def update_service_op(op_id: int, **kwargs: Any) -> None:
    if not kwargs:
        return
    sets = ", ".join(f"{k} = %s" for k in kwargs)
    vals = list(kwargs.values()) + [op_id]
    with get_cursor() as cur:
        cur.execute(f"UPDATE service_operations SET {sets} WHERE id = %s", vals)  # noqa: S608


def get_service_op(op_id: int) -> dict | None:
    with get_cursor() as cur:
        cur.execute(
            "SELECT so.*, u.username AS triggered_by_name FROM service_operations so "
            "LEFT JOIN users u ON so.triggered_by = u.id "
            "WHERE so.id = %s",
            (op_id,),
        )
        return cur.fetchone()


def list_service_ops(limit: int = 20) -> list[dict]:
    with get_cursor() as cur:
        cur.execute(
            "SELECT so.*, u.username AS triggered_by_name FROM service_operations so "
            "LEFT JOIN users u ON so.triggered_by = u.id "
            "ORDER BY so.id DESC LIMIT %s",
            (limit,),
        )
        return cur.fetchall()
