"""Database creation, migrations, and first-run admin user setup."""

from __future__ import annotations

import os
import secrets
import string
import sys

import psycopg

MIGRATION_SQL = """\
CREATE TABLE IF NOT EXISTS users (
    id            SERIAL PRIMARY KEY,
    username      TEXT UNIQUE NOT NULL,
    password_hash TEXT NOT NULL,
    is_admin      BOOLEAN DEFAULT FALSE,
    created_at    TIMESTAMPTZ DEFAULT now()
);

CREATE TABLE IF NOT EXISTS config_versions (
    id            SERIAL PRIMARY KEY,
    config        JSONB NOT NULL,
    author_id     INTEGER REFERENCES users(id),
    comment       TEXT,
    created_at    TIMESTAMPTZ DEFAULT now()
);

CREATE TABLE IF NOT EXISTS deploys (
    id            SERIAL PRIMARY KEY,
    config_id     INTEGER REFERENCES config_versions(id),
    triggered_by  INTEGER REFERENCES users(id),
    status        TEXT NOT NULL DEFAULT 'running',
    output        TEXT,
    started_at    TIMESTAMPTZ DEFAULT now(),
    finished_at   TIMESTAMPTZ
);

ALTER TABLE users ADD COLUMN IF NOT EXISTS timezone TEXT;

CREATE TABLE IF NOT EXISTS service_operations (
    id            SERIAL PRIMARY KEY,
    op_type       TEXT NOT NULL,
    target_node   TEXT,
    skip_gateway  BOOLEAN DEFAULT FALSE,
    triggered_by  INTEGER REFERENCES users(id),
    status        TEXT NOT NULL DEFAULT 'running',
    output        TEXT,
    started_at    TIMESTAMPTZ DEFAULT now(),
    finished_at   TIMESTAMPTZ
);
"""


def generate_password(length: int = 20) -> str:
    """Generate a cryptographically secure random password."""
    alphabet = string.ascii_letters + string.digits
    return "".join(secrets.choice(alphabet) for _ in range(length))


def ensure_database() -> None:
    """Create the thunder_admin database and user if they don't exist."""
    pg_password = os.environ.get("POSTGRES_PASSWORD", "litellm-local")
    admin_password = os.environ.get("ADMIN_DB_PASSWORD", "admin-local")
    pg_host = os.environ.get("POSTGRES_HOST", "postgres")
    pg_port = os.environ.get("POSTGRES_PORT", "5432")
    pg_url = f"postgresql://litellm:{pg_password}@{pg_host}:{pg_port}/postgres"

    with psycopg.connect(pg_url, autocommit=True) as conn:
        row = conn.execute("SELECT 1 FROM pg_roles WHERE rolname = 'thunder_admin'").fetchone()
        if not row:
            conn.execute(f"CREATE USER thunder_admin WITH PASSWORD '{admin_password}'")
            print("Created database user: thunder_admin")

        row = conn.execute("SELECT 1 FROM pg_database WHERE datname = 'thunder_admin'").fetchone()
        if not row:
            conn.execute("CREATE DATABASE thunder_admin OWNER thunder_admin")
            print("Created database: thunder_admin")

        conn.execute("GRANT ALL PRIVILEGES ON DATABASE thunder_admin TO thunder_admin")


def run_migrations(database_url: str) -> None:
    """Run schema migrations (idempotent)."""
    with psycopg.connect(database_url) as conn:
        conn.execute(MIGRATION_SQL)
        conn.commit()
    print("Migrations complete.")


def sync_admin_password(database_url: str, password: str) -> None:
    """Create or update the admin user with the given password."""
    import bcrypt

    hashed = bcrypt.hashpw(password.encode(), bcrypt.gensalt()).decode()
    with psycopg.connect(database_url) as conn:
        row = conn.execute("SELECT id FROM users WHERE username = 'admin'").fetchone()
        if row:
            conn.execute("UPDATE users SET password_hash = %s WHERE username = 'admin'", (hashed,))
            print("Admin password updated from ADMIN_PASSWORD.")
        else:
            conn.execute(
                "INSERT INTO users (username, password_hash, is_admin) VALUES (%s, %s, TRUE)",
                ("admin", hashed),
            )
            print("Admin user created from ADMIN_PASSWORD.")
        conn.commit()


def create_initial_admin(database_url: str) -> str | None:
    """Create the initial admin user if no users exist. Returns generated password or None."""
    import bcrypt

    with psycopg.connect(database_url) as conn:
        row = conn.execute("SELECT COUNT(*) FROM users").fetchone()
        if row and row[0] > 0:
            return None

        password = generate_password()
        hashed = bcrypt.hashpw(password.encode(), bcrypt.gensalt()).decode()
        conn.execute(
            "INSERT INTO users (username, password_hash, is_admin) VALUES (%s, %s, TRUE)",
            ("admin", hashed),
        )
        conn.commit()

    return password


def bootstrap() -> None:
    """Full bootstrap sequence: create DB, run migrations, create admin."""
    database_url = os.environ.get("DATABASE_URL", "")
    if not database_url:
        print("ERROR: DATABASE_URL not set", file=sys.stderr)
        sys.exit(1)

    ensure_database()
    run_migrations(database_url)

    admin_password = os.environ.get("ADMIN_PASSWORD", "")
    if admin_password:
        sync_admin_password(database_url, admin_password)
        return

    password = create_initial_admin(database_url)
    if password:
        print("\nFirst run detected. Admin account created:")
        print("  Username: admin")
        print(f"  Password: {password}")
        print("Save this password!\n")
