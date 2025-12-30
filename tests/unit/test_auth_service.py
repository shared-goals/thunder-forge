from __future__ import annotations

import hashlib
import hmac
import json
import time
from urllib.parse import urlencode

import pytest
from fastapi.testclient import TestClient

from api.webhook import app
from services.config_service import load_config


def _make_init_data(*, bot_token: str, user_id: int, auth_date: int | None = None) -> str:
    auth_date = auth_date or int(time.time())
    user = {"id": user_id, "username": "admin"}

    data = {
        "auth_date": str(auth_date),
        "query_id": "AAEAAAEAAA==",
        "user": json.dumps(user, separators=(",", ":")),
    }

    data_check_string = "\n".join(f"{k}={data[k]}" for k in sorted(data.keys()))

    secret_key = hmac.new(
        key=b"WebAppData",
        msg=bot_token.encode("utf-8"),
        digestmod=hashlib.sha256,
    ).digest()

    sig = hmac.new(
        key=secret_key,
        msg=data_check_string.encode("utf-8"),
        digestmod=hashlib.sha256,
    ).hexdigest()

    payload = dict(data)
    payload["hash"] = sig
    return urlencode(payload)


@pytest.fixture()
def client():
    return TestClient(app)


def _write_config(tmp_path, *, bot_token: str, admin_ids: list[int]) -> str:
    path = tmp_path / "tf.yml"
    lines: list[str] = [
        "telegram:",
        f"  bot_token: {bot_token}",
        "access:",
        "  admin_telegram_ids:",
        *[f"    - {i}" for i in admin_ids],
        "nodes:",
        "  - name: node1",
        "    ssh_user: u",
        "    wifi_ip: 127.0.0.1",
        "    service_manager: brew",
        "",
    ]
    path.write_text("\n".join(lines), encoding="utf-8")
    return str(path)


def test_me_missing_init_data_is_401(monkeypatch: pytest.MonkeyPatch, tmp_path, client: TestClient):
    monkeypatch.setenv("TF_CONFIG_PATH", _write_config(tmp_path, bot_token="test-token", admin_ids=[123]))
    load_config.cache_clear()

    res = client.post("/api/mini-app/me", json={})
    assert res.status_code == 401


def test_me_invalid_signature_is_401(monkeypatch: pytest.MonkeyPatch, tmp_path, client: TestClient):
    monkeypatch.setenv("TF_CONFIG_PATH", _write_config(tmp_path, bot_token="test-token", admin_ids=[123]))
    load_config.cache_clear()

    init_data = "auth_date=1&user=%7B%22id%22%3A123%7D&hash=deadbeef"
    res = client.post("/api/mini-app/me", headers={"Authorization": "tma " + init_data})
    assert res.status_code == 401


def test_me_not_in_allowlist_is_403(monkeypatch: pytest.MonkeyPatch, tmp_path, client: TestClient):
    monkeypatch.setenv("TF_CONFIG_PATH", _write_config(tmp_path, bot_token="test-token", admin_ids=[999]))
    load_config.cache_clear()

    init_data = _make_init_data(bot_token="test-token", user_id=123)
    res = client.post("/api/mini-app/me", headers={"Authorization": "tma " + init_data})
    assert res.status_code == 403


def test_me_admin_ok(monkeypatch: pytest.MonkeyPatch, tmp_path, client: TestClient):
    monkeypatch.setenv("TF_CONFIG_PATH", _write_config(tmp_path, bot_token="test-token", admin_ids=[123]))
    load_config.cache_clear()

    init_data = _make_init_data(bot_token="test-token", user_id=123)
    res = client.post("/api/mini-app/me", headers={"Authorization": "tma " + init_data})
    assert res.status_code == 200
    body = res.json()
    assert body["user"]["id"] == 123
