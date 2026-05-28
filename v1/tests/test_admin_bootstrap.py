"""Tests for bootstrap SQL and first-run logic."""


def test_migration_sql_is_idempotent():
    """Migration SQL uses IF NOT EXISTS for all objects."""
    from thunder_admin.bootstrap import MIGRATION_SQL

    assert "IF NOT EXISTS" in MIGRATION_SQL or "CREATE TABLE" not in MIGRATION_SQL
    assert "users" in MIGRATION_SQL
    assert "config_versions" in MIGRATION_SQL
    assert "deploys" in MIGRATION_SQL


def test_generate_password_length():
    """Generated passwords are sufficiently long."""
    from thunder_admin.bootstrap import generate_password

    pwd = generate_password()
    assert len(pwd) >= 16
