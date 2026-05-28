"""Tests for admin database connection lifecycle."""

from __future__ import annotations

import pytest


class FakeCursor:
    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False


class FakeConnection:
    def __init__(self) -> None:
        self.cursor_obj = FakeCursor()
        self.committed = False
        self.rolled_back = False
        self.closed = False

    def cursor(self) -> FakeCursor:
        return self.cursor_obj

    def commit(self) -> None:
        self.committed = True

    def rollback(self) -> None:
        self.rolled_back = True

    def close(self) -> None:
        self.closed = True


def test_get_cursor_closes_connection_on_success(monkeypatch):
    from thunder_admin import db

    conn = FakeConnection()
    monkeypatch.setenv("DATABASE_URL", "postgresql://example")
    monkeypatch.setattr(db.psycopg, "connect", lambda *args, **kwargs: conn)

    with db.get_cursor() as cur:
        assert cur is conn.cursor_obj

    assert conn.committed is True
    assert conn.rolled_back is False
    assert conn.closed is True


def test_get_cursor_rolls_back_and_closes_connection_on_error(monkeypatch):
    from thunder_admin import db

    conn = FakeConnection()
    monkeypatch.setenv("DATABASE_URL", "postgresql://example")
    monkeypatch.setattr(db.psycopg, "connect", lambda *args, **kwargs: conn)

    with pytest.raises(RuntimeError, match="boom"):
        with db.get_cursor():
            raise RuntimeError("boom")

    assert conn.committed is False
    assert conn.rolled_back is True
    assert conn.closed is True
