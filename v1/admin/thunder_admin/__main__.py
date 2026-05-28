# admin/thunder_admin/__main__.py
"""CLI commands for admin UI container management."""

from __future__ import annotations

import json
import sys

import yaml


def cmd_reset_password(username: str) -> None:
    """Reset a user's password."""
    from thunder_admin import db
    from thunder_admin.auth import hash_password
    from thunder_admin.bootstrap import generate_password

    user = db.get_user_by_username(username)
    if not user:
        print(f"Error: User '{username}' not found", file=sys.stderr)
        sys.exit(1)

    password = generate_password()
    db.update_user_password(user["id"], hash_password(password))
    print(f"New password for {username}: {password}")


def cmd_create_user(username: str) -> None:
    """Create a new user."""
    from thunder_admin import db
    from thunder_admin.auth import hash_password
    from thunder_admin.bootstrap import generate_password

    if db.get_user_by_username(username):
        print(f"Error: User '{username}' already exists", file=sys.stderr)
        sys.exit(1)

    password = generate_password()
    db.create_user(username, hash_password(password))
    print(f"Created user '{username}'. Password: {password}")


def cmd_import_config(path: str) -> None:
    """Import a node-assignments.yaml file as a config version."""
    from thunder_admin.config import validate_config

    with open(path) as f:
        raw = yaml.safe_load(f)

    # Convert to JSON-compatible dict (YAML None -> JSON null, etc.)
    config_json = json.loads(json.dumps(raw, default=str))

    errors = validate_config(config_json)
    if errors:
        for e in errors:
            print(f"Warning: {e}", file=sys.stderr)

    from psycopg.types.json import Json

    from thunder_admin.db import get_cursor

    with get_cursor() as cur:
        cur.execute(
            "INSERT INTO config_versions (config, author_id, comment) VALUES (%s, NULL, %s) RETURNING id",
            (Json(config_json), f"Imported from {path}"),
        )
        row = cur.fetchone()
        print(f"Imported as config version {row['id']}")


def cmd_list_users() -> None:
    """List all users."""
    from thunder_admin import db

    users = db.list_users()
    for u in users:
        role = "admin" if u["is_admin"] else "user"
        print(f"  {u['id']:3d}  {u['username']:<20s}  {role:<6s}  {u['created_at']}")


def cmd_export_config(version: int | None = None) -> None:
    """Export a config version as YAML to stdout."""
    from thunder_admin import db
    from thunder_admin.config import jsonb_to_yaml

    if version:
        row = db.get_config_version(version)
        if not row:
            print(f"Error: Config version {version} not found", file=sys.stderr)
            sys.exit(1)
    else:
        row = db.get_current_config()
        if not row:
            print("Error: No config versions exist", file=sys.stderr)
            sys.exit(1)

    print(jsonb_to_yaml(row["config"]))


def main() -> None:
    """CLI dispatch."""
    if len(sys.argv) < 2:
        print("Usage: python -m thunder_admin <command> [args]")
        print("Commands: reset-password, create-user, import-config, list-users, export-config")
        sys.exit(1)

    cmd = sys.argv[1]
    if cmd == "reset-password":
        if len(sys.argv) < 3:
            print(
                "Usage: python -m thunder_admin reset-password <username>",
                file=sys.stderr,
            )
            sys.exit(1)
        cmd_reset_password(sys.argv[2])
    elif cmd == "create-user":
        if len(sys.argv) < 3:
            print(
                "Usage: python -m thunder_admin create-user <username>",
                file=sys.stderr,
            )
            sys.exit(1)
        cmd_create_user(sys.argv[2])
    elif cmd == "import-config":
        if len(sys.argv) < 3:
            print(
                "Usage: python -m thunder_admin import-config <path>",
                file=sys.stderr,
            )
            sys.exit(1)
        cmd_import_config(sys.argv[2])
    elif cmd == "list-users":
        cmd_list_users()
    elif cmd == "export-config":
        version = int(sys.argv[2]) if len(sys.argv) > 2 else None
        cmd_export_config(version)
    else:
        print(f"Unknown command: {cmd}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
